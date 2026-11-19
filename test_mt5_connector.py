import unittest
from types import SimpleNamespace

from mt5_connector import MT5Client, MT5Error


class FakeMT5:
    ACCOUNT_TRADE_MODE_REAL = 2
    SYMBOL_TRADE_MODE_DISABLED = 0
    SYMBOL_TRADE_MODE_LONGONLY = 1
    SYMBOL_TRADE_MODE_FULL = 4
    POSITION_TYPE_BUY = 0
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010

    def __init__(self, positions=()):
        self.positions = tuple(positions)
        self.sent_requests = []
        self.checked_requests = []
        self.terminal = SimpleNamespace(connected=True, trade_allowed=True)
        self.account = SimpleNamespace(
            login=12345678,
            server="Broker-Live",
            currency="USD",
            balance=1000.0,
            equity=1000.0,
            trade_mode=self.ACCOUNT_TRADE_MODE_REAL,
            trade_allowed=True,
        )
        self.symbol = SimpleNamespace(
            trade_contract_size=1.0,
            volume_min=0.01,
            volume_max=10.0,
            volume_step=0.01,
            digits=2,
            point=0.01,
            trade_tick_size=0.01,
            trade_stops_level=0,
            trade_mode=self.SYMBOL_TRADE_MODE_FULL,
        )
        self.tick = SimpleNamespace(bid=63000.0, ask=63001.0)

    def terminal_info(self):
        return self.terminal

    def account_info(self):
        return self.account

    def symbol_select(self, symbol, selected):
        return symbol == "BTCUSD" and selected

    def symbol_info(self, symbol):
        return self.symbol if symbol == "BTCUSD" else None

    def symbol_info_tick(self, symbol):
        return self.tick if symbol == "BTCUSD" else None

    def positions_get(self, symbol=None):
        return self.positions if symbol in (None, "BTCUSD") else ()

    def order_check(self, request):
        self.checked_requests.append(request.copy())
        return SimpleNamespace(retcode=0, comment="checked")

    def order_send(self, request):
        self.sent_requests.append(request.copy())
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            order=101,
            deal=202,
            volume=request["volume"],
            price=request["price"],
            comment="done",
        )

    def last_error(self):
        return (0, "no error")

    def initialize(self):
        return True


class MT5ClientTests(unittest.TestCase):
    def test_open_requires_explicit_confirmation(self):
        api = FakeMT5()

        with self.assertRaises(MT5Error):
            MT5Client(api).open_long("BTCUSD", 0.01, confirmed=False)

        self.assertEqual(api.sent_requests, [])

    def test_open_enforces_volume_cap_before_sending(self):
        api = FakeMT5()

        with self.assertRaisesRegex(MT5Error, "الحد الأقصى"):
            MT5Client(api).open_long("BTCUSD", 0.02, confirmed=True)

        self.assertEqual(api.sent_requests, [])

    def test_confirmed_buy_is_checked_and_includes_stop_and_target(self):
        api = FakeMT5()

        result = MT5Client(api).open_long("BTCUSD", 0.01, confirmed=True)

        self.assertEqual(result.order, 101)
        self.assertEqual(len(api.checked_requests), 1)
        self.assertEqual(len(api.sent_requests), 1)
        request = api.sent_requests[0]
        self.assertEqual(request["volume"], 0.01)
        self.assertLess(request["sl"], request["price"])
        self.assertGreater(request["tp"], request["price"])

    def test_confirmed_close_targets_only_the_selected_position(self):
        position = SimpleNamespace(
            ticket=42,
            type=FakeMT5.POSITION_TYPE_BUY,
            volume=0.02,
        )
        api = FakeMT5(positions=(position,))

        result = MT5Client(api).close_position(
            "BTCUSD", 42, 0.01, confirmed=True
        )

        self.assertEqual(result.order, 101)
        self.assertEqual(api.sent_requests[0]["position"], 42)
        self.assertEqual(api.sent_requests[0]["type"], FakeMT5.ORDER_TYPE_SELL)
        self.assertEqual(api.sent_requests[0]["volume"], 0.01)


if __name__ == "__main__":
    unittest.main()
