import unittest

import btc_mobile_auth as mobile_auth


def valid_secrets():
    return {
        "auth": {
            "redirect_uri": "https://trade.example.com/oauth2callback",
            "cookie_secret": "a" * 48,
            "google": {
                "client_id": "client.apps.googleusercontent.com",
                "client_secret": "client-secret",
                "server_metadata_url": mobile_auth.GOOGLE_METADATA_URL,
            },
        },
        "mobile_access": {"allowed_email": "owner@example.com"},
    }


class MobileAuthTests(unittest.TestCase):
    def test_only_loopback_clients_bypass_google_login(self):
        self.assertTrue(mobile_auth.is_loopback_client("127.0.0.1"))
        self.assertTrue(mobile_auth.is_loopback_client("::1"))
        self.assertTrue(mobile_auth.is_loopback_client("::ffff:127.0.0.1"))
        self.assertFalse(mobile_auth.is_loopback_client("192.168.1.25"))
        self.assertFalse(mobile_auth.is_loopback_client(None))
        self.assertFalse(mobile_auth.is_loopback_client("invalid"))

    def test_local_only_server_keeps_local_access_but_not_lan_access(self):
        self.assertTrue(mobile_auth.is_local_access("192.168.1.25", "127.0.0.1"))
        self.assertTrue(mobile_auth.is_local_access("::1", "10.0.0.20"))
        self.assertFalse(
            mobile_auth.is_local_access("192.168.1.25", "10.0.0.20")
        )

    def test_google_settings_require_https_and_allowlist_email(self):
        self.assertEqual(
            mobile_auth.validate_google_auth_settings(valid_secrets()),
            "owner@example.com",
        )

        secrets = valid_secrets()
        secrets["auth"]["redirect_uri"] = "http://192.168.1.25:8501/oauth2callback"
        with self.assertRaisesRegex(
            mobile_auth.MobileAuthConfigurationError, "HTTPS"
        ):
            mobile_auth.validate_google_auth_settings(secrets)

    def test_mobile_request_must_use_the_registered_https_host(self):
        mobile_auth.validate_https_request_url(
            "https://trade.example.com:8501/",
            "https://trade.example.com:8501/oauth2callback",
        )
        with self.assertRaisesRegex(
            mobile_auth.MobileAuthConfigurationError, "HTTPS"
        ):
            mobile_auth.validate_https_request_url(
                "http://trade.example.com:8501/",
                "https://trade.example.com:8501/oauth2callback",
            )
        with self.assertRaisesRegex(
            mobile_auth.MobileAuthConfigurationError, "يطابق"
        ):
            mobile_auth.validate_https_request_url(
                "https://other.example.com:8501/",
                "https://trade.example.com:8501/oauth2callback",
            )

    def test_google_user_must_match_be_verified_and_unexpired(self):
        self.assertTrue(
            mobile_auth.is_authorized_google_user(
                "OWNER@example.com", True, "owner@example.com", 200, now=100
            )
        )
        self.assertFalse(
            mobile_auth.is_authorized_google_user(
                "other@example.com", True, "owner@example.com", 200, now=100
            )
        )
        self.assertFalse(
            mobile_auth.is_authorized_google_user(
                "owner@example.com", False, "owner@example.com", 200, now=100
            )
        )
        self.assertFalse(
            mobile_auth.is_authorized_google_user(
                "owner@example.com", True, "owner@example.com", 100, now=100
            )
        )


if __name__ == "__main__":
    unittest.main()
