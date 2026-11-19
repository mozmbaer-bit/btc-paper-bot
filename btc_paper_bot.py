#!/usr/bin/env python3
"""
بوت محاكاة (Paper Trading) يقرأ سعر BTC-USD من مصادر عامة.

- لا يحتاج أي مفتاح API ولا يتصل بحساب أو محفظة.
- لا يرسل أي أمر شراء أو بيع حقيقي، كل الصفقات وهمية وتُسجَّل في ملف CSV.
- الاستراتيجية: تقاطع متوسطين متحركين، مع وقف خسارة وهدف لكل صفقة.

التشغيل:  python3 btc_paper_bot.py
الإيقاف:  Ctrl+C
"""

import csv
import json
import os
import sys
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone

# ----------------------------- الإعدادات -----------------------------
YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
    "BTC-USD?range=5d&interval=15m"
)
TICKER_URLS = (
    (
        "Yahoo Finance",
        YAHOO_CHART_URL,
    ),
    ("CoinPaprika", "https://api.coinpaprika.com/v1/tickers/btc-bitcoin"),
    (
        "Coinbase Exchange",
        "https://api.exchange.coinbase.com/products/BTC-USD/ticker",
    ),
    ("Coinbase", "https://api.coinbase.com/v2/prices/BTC-USD/spot"),
    (
        "CoinGecko",
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin&vs_currencies=usd",
    ),
)
USER_AGENT = "btc-paper-bot-education/1.0"

POLL_SECONDS = 10          # كم ثانية بين كل قراءة للسعر
FAST_N = 6                 # متوسط سريع (عدد العينات)
SLOW_N = 18                # متوسط بطيء (عدد العينات)

START_CASH = 10_000.0      # رصيد وهمي
RISK_PCT = 1.0             # أقصى مخاطرة لكل صفقة (% من قيمة المحفظة)
STOP_PCT = 1.0             # وقف الخسارة (% من سعر الدخول)
TARGET_PCT = 2.0           # الهدف (% من سعر الدخول)

LOG_FILE = "paper_trades.csv"
# ----------------------------------------------------------------------


def _safe_print(message: str) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    printable = message.encode(encoding, errors="backslashreplace").decode(encoding)
    print(printable)


def fetch_price() -> float:
    """يقرأ آخر سعر من مصدر عام متاح، مع استخدام البدائل عند التعطل."""
    errors = []
    for source_name, ticker_url in TICKER_URLS:
        try:
            req = urllib.request.Request(
                ticker_url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if "price" in data:
                price = float(data["price"])
            elif "data" in data and "amount" in data["data"]:
                price = float(data["data"]["amount"])
            elif "bitcoin" in data and "usd" in data["bitcoin"]:
                price = float(data["bitcoin"]["usd"])
            elif "quotes" in data and "USD" in data["quotes"]:
                price = float(data["quotes"]["USD"]["price"])
            elif (
                "chart" in data
                and data["chart"].get("result")
                and "regularMarketPrice"
                in data["chart"]["result"][0].get("meta", {})
            ):
                price = float(
                    data["chart"]["result"][0]["meta"]["regularMarketPrice"]
                )
            else:
                raise ValueError("استجابة واجهة الأسعار لا تحتوي على السعر")

            if price <= 0:
                raise ValueError("السعر المستلم غير صالح")
            return price
        except (
            AttributeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            OSError,
        ) as e:
            errors.append(f"{source_name}: {e}")

    raise RuntimeError(
        "تعذّر قراءة سعر BTC-USD من مصادر الأسعار المتاحة. "
        + " | ".join(errors)
    )


def fetch_price_history() -> list[dict[str, object]]:
    """Fetch up to five days of 15-minute BTC/USD candles from Yahoo Finance."""
    req = urllib.request.Request(
        YAHOO_CHART_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        history = [
            {
                "time": datetime.fromtimestamp(timestamp, timezone.utc),
                "price": float(close),
            }
            for timestamp, close in zip(timestamps, closes)
            if timestamp is not None and close is not None and float(close) > 0
        ]
        if not history:
            raise ValueError("استجابة الرسم لا تحتوي على أسعار")
        return history
    except (
        IndexError,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as e:
        raise RuntimeError(f"تعذّر تحميل بيانات الرسم من Yahoo Finance: {e}") from e


def sma(values, n: int) -> float:
    recent = list(values)[-n:]
    return sum(recent) / len(recent)


class PaperBroker:
    """وسيط وهمي: يحتفظ بالرصيد والصفقة المفتوحة فقط، ولا يتصل بأي منصة."""

    def __init__(self):
        self.cash = START_CASH
        self.qty = 0.0
        self.entry = None
        self.stop = None
        self.target = None
        self.realized = 0.0
        self._init_log()

    def _init_log(self):
        if not os.path.exists(LOG_FILE):
            with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(
                    ["time_utc", "side", "price", "qty", "reason", "pnl", "cash", "equity"]
                )

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    def buy(self, price: float, reason: str = "signal"):
        if self.qty > 0:
            return
        stop_dist = price * STOP_PCT / 100
        qty = (self.equity(price) * RISK_PCT / 100) / stop_dist
        qty = min(qty, self.cash / price)  # لا نشتري أكثر من الرصيد
        if qty <= 0:
            return
        self.cash -= qty * price
        self.qty = qty
        self.entry = price
        self.stop = price - stop_dist
        self.target = price * (1 + TARGET_PCT / 100)
        self._log("BUY", price, reason, 0.0)

    def sell(self, price: float, reason: str):
        if self.qty <= 0:
            return
        pnl = (price - self.entry) * self.qty
        self.cash += self.qty * price
        self.realized += pnl
        self._log("SELL", price, reason, pnl)
        self.qty, self.entry, self.stop, self.target = 0.0, None, None, None

    def check_levels(self, price: float):
        if self.qty <= 0:
            return
        if price <= self.stop:
            self.sell(price, "stop-loss")
        elif price >= self.target:
            self.sell(price, "target")

    def _log(self, side, price, reason, pnl):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                now, side, f"{price:.2f}", f"{self.qty:.6f}", reason,
                f"{pnl:.2f}", f"{self.cash:.2f}", f"{self.equity(price):.2f}",
            ])
        _safe_print(f"[{now}] {side:4} @ {price:,.2f}  ({reason})"
                    + (f"  ربح/خسارة: {pnl:+,.2f}" if side == "SELL" else ""))


def main():
    broker = PaperBroker()
    prices = deque(maxlen=SLOW_N)
    prev_fast = prev_slow = None

    _safe_print("بدء المحاكاة. الأموال وهمية والأسعار من مصادر عامة.")
    _safe_print(f"الرصيد الوهمي: ${START_CASH:,.2f} | المتوسطات: {FAST_N}/{SLOW_N} | كل {POLL_SECONDS} ثانية\n")

    try:
        while True:
            try:
                price = fetch_price()
            except Exception as e:  # شبكة متقطعة أو خطأ من الواجهة
                _safe_print(f"تعذّر قراءة السعر، إعادة المحاولة لاحقًا: {e}")
                time.sleep(POLL_SECONDS)
                continue

            prices.append(price)
            broker.check_levels(price)

            if len(prices) == SLOW_N:
                fast, slow = sma(prices, FAST_N), sma(prices, SLOW_N)
                if prev_fast is not None:
                    crossed_up = prev_fast <= prev_slow and fast > slow
                    crossed_down = prev_fast >= prev_slow and fast < slow
                    if crossed_up:
                        broker.buy(price, "تقاطع صعودي")
                    elif crossed_down:
                        broker.sell(price, "تقاطع هبوطي")
                prev_fast, prev_slow = fast, slow

            equity = broker.equity(price)
            _safe_print(f"  السعر {price:,.2f} | المحفظة ${equity:,.2f} | "
                        f"المفتوحة {'نعم' if broker.qty > 0 else 'لا'}")
            time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        final = broker.equity(prices[-1]) if prices else broker.cash
        _safe_print("\nإيقاف المحاكاة.")
        _safe_print(f"قيمة المحفظة الوهمية: ${final:,.2f} | الربح المحقق: ${broker.realized:+,.2f}")
        _safe_print(f"سجل الصفقات محفوظ في {LOG_FILE}")


if __name__ == "__main__":
    main()