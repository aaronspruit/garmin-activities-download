"""Tests for configuration loading."""

from src.config import load_config


class TestLoadConfig:
    def test_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("GARMIN_EMAIL", "test@example.com")
        monkeypatch.setenv("GARMIN_PASSWORD", "secret")
        monkeypatch.setenv("DAYS_BACK", "14")
        monkeypatch.setenv("GARMINTOKENS", "/custom/tokens")
        monkeypatch.setenv("OUTPUT_DIR", "/custom/output")

        config = load_config()

        assert config.email == "test@example.com"
        assert config.password == "secret"
        assert config.days_back == 14
        assert config.tokenstore == "/custom/tokens"
        assert config.output_dir == "/custom/output"

    def test_default_values(self, monkeypatch):
        monkeypatch.delenv("GARMIN_EMAIL", raising=False)
        monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
        monkeypatch.delenv("DAYS_BACK", raising=False)
        monkeypatch.delenv("GARMINTOKENS", raising=False)
        monkeypatch.delenv("OUTPUT_DIR", raising=False)

        config = load_config()

        assert config.email is None
        assert config.password is None
        assert config.days_back == 7
        assert config.tokenstore == "/app/tokens"
        assert config.output_dir == "/app/data"

    def test_reads_docker_secret(self, monkeypatch, tmp_path):
        secret_file = tmp_path / "garmin_email"
        secret_file.write_text("secret@example.com\n")
        monkeypatch.delenv("GARMIN_EMAIL", raising=False)

        # Patch the secret path for testing
        monkeypatch.setattr(
            "src.config._read_secret", lambda name: "secret@example.com" if name == "GARMIN_EMAIL" else None
        )

        config = load_config()
        assert config.email == "secret@example.com"
