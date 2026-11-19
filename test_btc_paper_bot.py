import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import btc_paper_bot


class Cp1252Output:
    encoding = "cp1252"

    def __init__(self):
        self.content = ""

    def write(self, text):
        text.encode(self.encoding)
        self.content += text
        return len(text)

    def flush(self):
        pass


class PaperBrokerLoggingTests(unittest.TestCase):
    def test_arabic_sell_log_does_not_break_on_legacy_console_encoding(self):
        output = Cp1252Output()
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "trades.csv"
            with patch.object(btc_paper_bot, "LOG_FILE", str(log_file)):
                with patch.object(btc_paper_bot.sys, "stdout", output):
                    broker = btc_paper_bot.PaperBroker()
                    broker.buy(100.0, "تقاطع صعودي")
                    broker.sell(101.0, "تقاطع هبوطي")

            self.assertIn(r"\u062a", output.content)
            self.assertNotIn("تقاطع هبوطي", output.content)
            self.assertIn("تقاطع هبوطي", log_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
