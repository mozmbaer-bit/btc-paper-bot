import tempfile
import unittest
from pathlib import Path

import account_links


class AccountLinkTests(unittest.TestCase):
    def test_saves_and_loads_account_links(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "links.json"
            link = account_links.validate_account_link(
                "وسيط التداول", "https://broker.example/login"
            )

            account_links.save_account_links([link], path)

            self.assertEqual(account_links.load_account_links(path), [link])

    def test_rejects_non_https_and_embedded_credentials(self):
        invalid_urls = (
            "http://broker.example/login",
            "https://user:password@broker.example/login",
        )
        for url in invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(account_links.AccountLinksError):
                    account_links.validate_account_link("وسيط", url)

    def test_rejects_urls_with_credentials_in_query(self):
        with self.assertRaisesRegex(account_links.AccountLinksError, "مفتاح API"):
            account_links.validate_account_link(
                "منصة", "https://broker.example/login?access_token=secret"
            )

    def test_missing_link_file_starts_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "missing.json"

            self.assertEqual(account_links.load_account_links(path), [])


if __name__ == "__main__":
    unittest.main()
