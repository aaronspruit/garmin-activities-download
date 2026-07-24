"""Tests for authentication flow."""

from unittest.mock import MagicMock, patch

import pytest
from garminconnect import GarminConnectAuthenticationError

from src.auth import AuthenticationError, authenticate


class TestAuthenticate:
    def test_succeeds_with_saved_tokens(self):
        with patch("src.auth.Garmin") as mock_garmin_cls:
            mock_instance = MagicMock()
            mock_garmin_cls.return_value = mock_instance

            result = authenticate(tokenstore="/tmp/tokens")

            mock_instance.login.assert_called_once_with("/tmp/tokens")
            assert result is mock_instance

    def test_falls_back_to_credentials(self):
        with patch("src.auth.Garmin") as mock_garmin_cls:
            token_instance = MagicMock()
            token_instance.login.side_effect = FileNotFoundError("No tokens")
            cred_instance = MagicMock()
            mock_garmin_cls.side_effect = [token_instance, cred_instance]

            result = authenticate(
                email="test@example.com",
                password="pass",
                tokenstore="/tmp/tokens",
            )

            assert result is cred_instance
            cred_instance.login.assert_called_once_with("/tmp/tokens")

    def test_raises_when_no_tokens_and_no_credentials(self):
        with patch("src.auth.Garmin") as mock_garmin_cls:
            mock_instance = MagicMock()
            mock_instance.login.side_effect = FileNotFoundError("No tokens")
            mock_garmin_cls.return_value = mock_instance

            with pytest.raises(AuthenticationError, match="No valid tokens"):
                authenticate(tokenstore="/tmp/tokens")

    def test_raises_when_credential_login_fails(self):
        with patch("src.auth.Garmin") as mock_garmin_cls:
            token_instance = MagicMock()
            token_instance.login.side_effect = FileNotFoundError
            cred_instance = MagicMock()
            cred_instance.login.side_effect = GarminConnectAuthenticationError("401")
            mock_garmin_cls.side_effect = [token_instance, cred_instance]

            with pytest.raises(GarminConnectAuthenticationError):
                authenticate(
                    email="test@example.com",
                    password="wrong",
                    tokenstore="/tmp/tokens",
                )
