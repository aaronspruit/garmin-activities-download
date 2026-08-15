"""Tests for the rate limiter, the retries, and the policy loader.

A fake clock drives every test. Sleeping moves that clock forward, so the tests
see the exact waits without waiting for them.
"""

import pytest

from src.ratelimit import (
    BudgetExhaustedError,
    RateLimiter,
    RateLimitError,
    RateLimitPolicy,
    TransientError,
    Window,
    load_policy,
)


class FakeClock:
    """A clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _limiter(clock, **policy_fields) -> RateLimiter:
    fields = {"windows": (), "min_interval": 0.0, "max_retries": 0}
    fields.update(policy_fields)
    return RateLimiter(RateLimitPolicy(**fields), name="test", sleep=clock.sleep, clock=clock)


class TestPacing:
    def test_the_first_request_never_waits(self):
        clock = FakeClock()
        limiter = _limiter(clock, min_interval=5.0)

        limiter.acquire()

        assert clock.slept == []

    def test_it_keeps_the_minimum_interval(self):
        clock = FakeClock()
        limiter = _limiter(clock, min_interval=2.0)

        limiter.acquire()
        limiter.acquire()

        assert clock.slept == [2.0]

    def test_a_window_stops_a_burst(self):
        clock = FakeClock()
        limiter = _limiter(clock, windows=(Window(3, 60),))

        for _ in range(4):
            limiter.acquire()

        # Three requests pass at once. The fourth waits for the first to leave
        # the 60 second window.
        assert clock.slept == [60.0]

    def test_a_window_frees_a_slot_when_time_passes(self):
        clock = FakeClock()
        limiter = _limiter(clock, windows=(Window(2, 60),))

        limiter.acquire()
        limiter.acquire()
        clock.now += 61

        limiter.acquire()

        assert clock.slept == []

    def test_the_shortest_window_binds_first(self):
        clock = FakeClock()
        limiter = _limiter(clock, windows=(Window(2, 10), Window(10, 3600)))

        for _ in range(3):
            limiter.acquire()

        assert clock.slept == [10.0]

    def test_it_counts_every_request(self):
        clock = FakeClock()
        limiter = _limiter(clock)

        for _ in range(5):
            limiter.acquire()

        assert limiter.requests == 5


class TestBudget:
    def test_a_wait_longer_than_max_wait_stops_the_run(self):
        clock = FakeClock()
        limiter = _limiter(clock, windows=(Window(1, 86400),), max_wait=300.0)

        limiter.acquire()

        with pytest.raises(BudgetExhaustedError, match="more than the 300s"):
            limiter.acquire()

    def test_it_does_not_sleep_when_it_gives_up(self):
        clock = FakeClock()
        limiter = _limiter(clock, windows=(Window(1, 86400),), max_wait=10.0)
        limiter.acquire()

        with pytest.raises(BudgetExhaustedError):
            limiter.acquire()

        assert clock.slept == []

    def test_a_wait_inside_max_wait_is_taken(self):
        clock = FakeClock()
        limiter = _limiter(clock, windows=(Window(1, 60),), max_wait=300.0)

        limiter.acquire()
        limiter.acquire()

        assert clock.slept == [60.0]

    def test_penalize_blocks_the_next_request(self):
        clock = FakeClock()
        limiter = _limiter(clock, max_wait=300.0)

        limiter.penalize(45.0)
        limiter.acquire()

        assert clock.slept == [45.0]

    def test_a_long_penalty_stops_the_run(self):
        clock = FakeClock()
        limiter = _limiter(clock, max_wait=300.0)

        limiter.penalize(4000.0)

        with pytest.raises(BudgetExhaustedError):
            limiter.acquire()


class TestRetries:
    def test_a_call_that_works_runs_once(self):
        clock = FakeClock()
        limiter = _limiter(clock, max_retries=3)
        calls = []

        result = limiter.call(lambda: calls.append(1) or "done")

        assert result == "done"
        assert len(calls) == 1

    def test_it_retries_a_rate_limit_error(self):
        clock = FakeClock()
        limiter = _limiter(clock, max_retries=3, backoff_initial=4.0, max_wait=300.0)
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise RateLimitError("slow down")
            return "done"

        assert limiter.call(flaky) == "done"
        assert len(attempts) == 3

    def test_it_retries_a_transient_error(self):
        clock = FakeClock()
        limiter = _limiter(clock, max_retries=2, backoff_initial=1.0, max_wait=300.0)
        attempts = []

        def flaky():
            attempts.append(1)
            raise TransientError("bad gateway")

        with pytest.raises(TransientError):
            limiter.call(flaky)

        assert len(attempts) == 3

    def test_it_reports_the_error_after_the_last_retry(self):
        clock = FakeClock()
        limiter = _limiter(clock, max_retries=1, backoff_initial=1.0, max_wait=300.0)

        def always_fails():
            raise RateLimitError("still limited")

        with pytest.raises(RateLimitError, match="still limited"):
            limiter.call(always_fails)

    def test_the_backoff_grows(self):
        clock = FakeClock()
        limiter = _limiter(clock, max_retries=3, backoff_initial=10.0, backoff_max=1000.0, max_wait=3000.0)

        def always_fails():
            raise TransientError("bad gateway")

        with pytest.raises(TransientError):
            limiter.call(always_fails)

        # Jitter keeps each delay between half of the base and the base, and the
        # base doubles each time: 10, 20, 40.
        assert len(clock.slept) == 3
        for delay, base in zip(clock.slept, [10.0, 20.0, 40.0], strict=True):
            assert base / 2 <= delay <= base

    def test_the_backoff_stops_at_its_maximum(self):
        clock = FakeClock()
        limiter = _limiter(clock, max_retries=4, backoff_initial=100.0, backoff_max=200.0, max_wait=3000.0)

        def always_fails():
            raise TransientError("bad gateway")

        with pytest.raises(TransientError):
            limiter.call(always_fails)

        assert max(clock.slept) <= 200.0

    def test_it_waits_as_long_as_the_tracker_asked(self):
        clock = FakeClock()
        limiter = _limiter(clock, max_retries=1, backoff_initial=1.0, max_wait=300.0)

        def always_fails():
            raise RateLimitError("slow down", retry_after=120.0)

        with pytest.raises(RateLimitError):
            limiter.call(always_fails)

        assert clock.slept == [120.0]

    def test_a_wait_the_run_cannot_take_stops_it(self):
        """A daily limit gives a reset of hours, which must not hold the run open."""
        clock = FakeClock()
        limiter = _limiter(clock, max_retries=3, backoff_initial=1.0, max_wait=300.0)

        def always_fails():
            raise RateLimitError("daily limit", retry_after=41220.0)

        with pytest.raises(BudgetExhaustedError, match="41220s"):
            limiter.call(always_fails)

        assert clock.slept == []

    def test_no_retries_reports_at_once(self):
        clock = FakeClock()
        limiter = _limiter(clock, max_retries=0)

        def always_fails():
            raise RateLimitError("slow down")

        with pytest.raises(RateLimitError):
            limiter.call(always_fails)

        assert clock.slept == []


class TestCountedAndUncounted:
    def test_a_counted_call_spends_the_budget(self):
        clock = FakeClock()
        limiter = _limiter(clock)

        limiter.call(lambda: "done")

        assert limiter.requests == 1

    def test_an_uncounted_call_spends_nothing(self):
        """Wahoo exempts the file downloads from its limits."""
        clock = FakeClock()
        limiter = _limiter(clock, windows=(Window(1, 3600),))

        for _ in range(5):
            limiter.retry(lambda: "done")

        assert limiter.requests == 0
        assert clock.slept == []

    def test_an_uncounted_call_still_retries(self):
        clock = FakeClock()
        limiter = _limiter(clock, max_retries=2, backoff_initial=1.0, max_wait=300.0)
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 2:
                raise TransientError("cdn hiccup")
            return "done"

        assert limiter.retry(flaky) == "done"
        assert len(attempts) == 2


class TestLoadPolicy:
    def test_it_returns_the_default_when_nothing_is_set(self):
        default = RateLimitPolicy(windows=(Window(5, 60),), min_interval=3.0)

        assert load_policy("garmin", default) is default

    def test_a_tracker_variable_replaces_the_windows(self, monkeypatch):
        monkeypatch.setenv("GARMIN_RATE_LIMIT", "50/60, 900/3600")
        default = RateLimitPolicy(windows=(Window(5, 60),))

        policy = load_policy("garmin", default)

        assert policy.windows == (Window(50, 60.0), Window(900, 3600.0))

    def test_it_sorts_the_windows_by_length(self, monkeypatch):
        monkeypatch.setenv("GARMIN_RATE_LIMIT", "2000/86400, 20/60, 300/3600")

        policy = load_policy("garmin", RateLimitPolicy())

        assert [window.seconds for window in policy.windows] == [60.0, 3600.0, 86400.0]

    def test_none_removes_every_window(self, monkeypatch):
        monkeypatch.setenv("WAHOO_RATE_LIMIT", "none")

        policy = load_policy("wahoo", RateLimitPolicy(windows=(Window(5, 60),)))

        assert policy.windows == ()

    def test_one_tracker_does_not_change_another(self, monkeypatch):
        monkeypatch.setenv("GARMIN_RATE_LIMIT", "1/60")
        default = RateLimitPolicy(windows=(Window(25, 300),))

        assert load_policy("wahoo", default).windows == (Window(25, 300),)

    def test_it_reads_the_other_fields(self, monkeypatch):
        monkeypatch.setenv("GARMIN_MIN_INTERVAL", "4.5")
        monkeypatch.setenv("GARMIN_MAX_RETRIES", "7")
        monkeypatch.setenv("GARMIN_BACKOFF_INITIAL", "15")
        monkeypatch.setenv("GARMIN_BACKOFF_MAX", "600")
        monkeypatch.setenv("GARMIN_MAX_WAIT", "120")
        monkeypatch.setenv("GARMIN_MAX_DOWNLOADS_PER_RUN", "250")

        policy = load_policy("garmin", RateLimitPolicy())

        assert policy.min_interval == 4.5
        assert policy.max_retries == 7
        assert policy.backoff_initial == 15.0
        assert policy.backoff_max == 600.0
        assert policy.max_wait == 120.0
        assert policy.max_downloads == 250

    def test_a_shared_variable_reaches_every_tracker(self, monkeypatch):
        monkeypatch.setenv("MAX_DOWNLOADS_PER_RUN", "100")

        assert load_policy("garmin", RateLimitPolicy()).max_downloads == 100
        assert load_policy("wahoo", RateLimitPolicy()).max_downloads == 100

    def test_a_tracker_variable_beats_the_shared_one(self, monkeypatch):
        monkeypatch.setenv("MAX_DOWNLOADS_PER_RUN", "100")
        monkeypatch.setenv("WAHOO_MAX_DOWNLOADS_PER_RUN", "10")

        assert load_policy("wahoo", RateLimitPolicy()).max_downloads == 10
        assert load_policy("garmin", RateLimitPolicy()).max_downloads == 100

    def test_the_shared_wait_uses_its_own_name(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_MAX_WAIT", "45")

        assert load_policy("garmin", RateLimitPolicy()).max_wait == 45.0

    @pytest.mark.parametrize(
        "value",
        ["20", "20/", "/60", "20/abc", "abc/60", "0/60", "20/0", "-5/60"],
    )
    def test_a_bad_window_is_reported(self, monkeypatch, value):
        monkeypatch.setenv("GARMIN_RATE_LIMIT", value)

        with pytest.raises(ValueError, match="GARMIN_RATE_LIMIT"):
            load_policy("garmin", RateLimitPolicy())

    def test_an_empty_list_of_windows_is_reported(self, monkeypatch):
        monkeypatch.setenv("GARMIN_RATE_LIMIT", ",,")

        with pytest.raises(ValueError, match="must not be empty"):
            load_policy("garmin", RateLimitPolicy())

    def test_a_bad_number_is_reported(self, monkeypatch):
        monkeypatch.setenv("GARMIN_MIN_INTERVAL", "soon")

        with pytest.raises(ValueError, match="GARMIN_MIN_INTERVAL"):
            load_policy("garmin", RateLimitPolicy())

    def test_a_negative_number_is_reported(self, monkeypatch):
        monkeypatch.setenv("GARMIN_MAX_RETRIES", "-1")

        with pytest.raises(ValueError, match="0 or more"):
            load_policy("garmin", RateLimitPolicy())

    def test_a_blank_variable_keeps_the_default(self, monkeypatch):
        monkeypatch.setenv("GARMIN_MIN_INTERVAL", "   ")
        default = RateLimitPolicy(min_interval=2.0)

        assert load_policy("garmin", default).min_interval == 2.0


class TestDescribe:
    def test_it_names_every_window(self):
        policy = RateLimitPolicy(windows=(Window(20, 60), Window(300, 3600)))

        assert "20/60s, 300/3600s" in policy.describe()

    def test_it_reports_no_cap_in_words(self):
        assert "max_downloads=unlimited" in RateLimitPolicy(max_downloads=0).describe()

    def test_it_reports_no_windows_in_words(self):
        assert "windows=none" in RateLimitPolicy(windows=()).describe()
