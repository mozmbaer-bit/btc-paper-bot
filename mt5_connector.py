from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from importlib import import_module
import math
from typing import Any

import btc_paper_bot as bot


MAX_LIVE_VOLUME = 0.01
MAX_DEVIATION_POINTS = 20


class MT5Error(RuntimeError):
    """A connection or trade request failed in the MetaTrader 5 terminal."""


@dataclass(frozen=True)
class MT5Account:
    login: str
    server: str
    currency: str
    balance: float
    equity: float
    is_real: bool
    trading_enabled: bool


@dataclass(frozen=True)
class MT5Symbol:
    bid: float
    ask: float
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    digits: int
    buy_enabled: bool


@dataclass(frozen=True)
class MT5Position:
    ticket: int
    side: str
    volume: float
    open_price: float
    current_price: float
    stop_loss: float
    take_profit: float
    profit: float


@dataclass(frozen=True)
class MT5Snapshot:
    account: MT5Account
    symbol: MT5Symbol
    positions: list[MT5Position]


@dataclass(frozen=True)
class MT5OrderResult:
    order: int
    deal: int
    volume: float
    price: float
    comment: str


class MT5Client:
    def __init__(self, api: Any | None = None) -> None:
        self._api_module = api

    def _api(self) -> Any:
        if self._api_module is None:
            try:
                self._api_module = import_module("MetaTrader5")
            except ImportError as exc:
                raise MT5Error(
                    "تعذّر تحميل مكتبة MetaTrader5. شغّل المشروع ببيئة "
                    f"Python 3.14 وثبّت requirements.txt. التفاصيل: {exc}"
                ) from exc
        return self._api_module

    def _connect(self) -> tuple[Any, Any, Any]:
        api = self._api()
        terminal = api.terminal_info()
        if terminal is None and not api.initialize():
            error = api.last_error()
            if error and error[0] == -10003:
                raise MT5Error(
                    "لم يُعثر على طرفية MetaTrader 5 ذات 64 بت. "
                    "افتح تطبيق MT5 المكتبي وسجّل الدخول، ثم أعد المحاولة."
                )
            raise MT5Error(f"تعذّر الاتصال بمنصة MT5: {error}")

        terminal = api.terminal_info()
        account = api.account_info()
        if terminal is None or account is None:
            raise MT5Error(f"تعذّر قراءة الطرفية أو الحساب: {api.last_error()}")
        if not getattr(terminal, "connected", False):
            raise MT5Error("منصة MT5 غير متصلة بالخادم.")
        return api, terminal, account

    def _symbol(self, api: Any, symbol: str) -> tuple[Any, Any]:
        if not symbol.strip():
            raise MT5Error("رمز التداول فارغ.")
        if not api.symbol_select(symbol, True):
            raise MT5Error(
                f"تعذّر تفعيل الرمز {symbol} في Market Watch: {api.last_error()}"
            )
        symbol_info = api.symbol_info(symbol)
        tick = api.symbol_info_tick(symbol)
        if symbol_info is None or tick is None:
            raise MT5Error(f"لا توجد بيانات سعر متاحة للرمز {symbol}.")
        if float(getattr(tick, "bid", 0.0)) <= 0 or float(
            getattr(tick, "ask", 0.0)
        ) <= 0:
            raise MT5Error(f"السعر الحالي للرمز {symbol} غير صالح.")
        return symbol_info, tick

    def snapshot(self, symbol: str) -> MT5Snapshot:
        api, terminal, account = self._connect()
        symbol_info, tick = self._symbol(api, symbol)
        positions = api.positions_get(symbol=symbol)
        if positions is None:
            raise MT5Error(
                f"تعذّر قراءة المراكز المفتوحة للرمز {symbol}: {api.last_error()}"
            )

        login = str(getattr(account, "login", ""))
        masked_login = f"••••{login[-4:]}" if len(login) > 4 else login
        account_data = MT5Account(
            login=masked_login or "غير متاح",
            server=str(getattr(account, "server", "غير معروف")),
            currency=str(getattr(account, "currency", "غير معروف")),
            balance=float(account.balance),
            equity=float(account.equity),
            is_real=account.trade_mode == api.ACCOUNT_TRADE_MODE_REAL,
            trading_enabled=bool(
                getattr(terminal, "trade_allowed", False)
                and getattr(account, "trade_allowed", False)
            ),
        )
        volume_min = float(symbol_info.volume_min)
        volume_max = float(symbol_info.volume_max)
        volume_step = float(symbol_info.volume_step)
        contract_size = float(symbol_info.trade_contract_size)
        if (
            not all(
                math.isfinite(value)
                for value in (volume_min, volume_max, volume_step, contract_size)
            )
            or volume_min <= 0
            or volume_max < volume_min
            or volume_step <= 0
            or contract_size <= 0
        ):
            raise MT5Error(f"أعاد الوسيط بيانات عقد غير صالحة للرمز {symbol}.")
        symbol_data = MT5Symbol(
            bid=float(tick.bid),
            ask=float(tick.ask),
            contract_size=contract_size,
            volume_min=volume_min,
            volume_max=volume_max,
            volume_step=volume_step,
            digits=int(symbol_info.digits),
            buy_enabled=(
                symbol_info.trade_mode
                in (
                    api.SYMBOL_TRADE_MODE_LONGONLY,
                    api.SYMBOL_TRADE_MODE_FULL,
                )
            ),
        )
        position_data = [
            MT5Position(
                ticket=int(position.ticket),
                side=(
                    "شراء"
                    if position.type == api.POSITION_TYPE_BUY
                    else "بيع"
                ),
                volume=float(position.volume),
                open_price=float(position.price_open),
                current_price=float(position.price_current),
                stop_loss=float(position.sl),
                take_profit=float(position.tp),
                profit=float(position.profit),
            )
            for position in positions
        ]
        return MT5Snapshot(account_data, symbol_data, position_data)

    def open_long(
        self, symbol: str, volume: float, *, confirmed: bool
    ) -> MT5OrderResult:
        if not confirmed:
            raise MT5Error("يجب تأكيد إرسال الأمر يدويًا.")
        self._validate_volume(volume)

        api, terminal, account = self._connect()
        self._require_trading_enabled(terminal, account)
        symbol_info, tick = self._symbol(api, symbol)
        if symbol_info.trade_mode not in (
            api.SYMBOL_TRADE_MODE_LONGONLY,
            api.SYMBOL_TRADE_MODE_FULL,
        ):
            raise MT5Error(f"الوسيط لا يسمح بفتح شراء على {symbol}.")

        positions = api.positions_get(symbol=symbol)
        if positions is None:
            raise MT5Error(f"تعذّر التحقق من مراكز {symbol}: {api.last_error()}")
        if positions:
            raise MT5Error(
                f"يوجد مركز مفتوح على {symbol}. أغلقه أو خفّضه قبل فتح شراء جديد."
            )

        self._validate_symbol_volume(symbol_info, volume)
        price = float(tick.ask)
        digits = int(symbol_info.digits)
        raw_stop_loss = price * (1 - bot.STOP_PCT / 100)
        raw_take_profit = price * (1 + bot.TARGET_PCT / 100)
        tick_size = float(
            getattr(
                symbol_info,
                "trade_tick_size",
                getattr(symbol_info, "point", 0.0),
            )
        )
        if not math.isfinite(tick_size) or tick_size <= 0:
            raise MT5Error(f"حجم حركة السعر غير صالح للرمز {symbol}.")
        tick_decimal = Decimal(str(tick_size))
        stop_units = (Decimal(str(raw_stop_loss)) / tick_decimal).to_integral_value(
            rounding=ROUND_FLOOR
        )
        target_units = (
            Decimal(str(raw_take_profit)) / tick_decimal
        ).to_integral_value(rounding=ROUND_CEILING)
        stop_loss = round(float(stop_units * tick_decimal), digits)
        take_profit = round(float(target_units * tick_decimal), digits)
        minimum_distance = (
            float(getattr(symbol_info, "trade_stops_level", 0))
            * float(getattr(symbol_info, "point", 0.0))
        )
        if price - stop_loss < minimum_distance or take_profit - price < minimum_distance:
            raise MT5Error(
                "وقف الخسارة أو الهدف أقرب من الحد الذي يفرضه الوسيط؛ "
                "لم يُرسل الأمر."
            )

        request = {
            "action": api.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": api.ORDER_TYPE_BUY,
            "price": price,
            "sl": stop_loss,
            "tp": take_profit,
            "deviation": MAX_DEVIATION_POINTS,
            "magic": 601018,
            "comment": "BTC manual dashboard",
            "type_time": api.ORDER_TIME_GTC,
        }
        return self._send_order(api, request)

    def close_position(
        self,
        symbol: str,
        ticket: int,
        volume: float,
        *,
        confirmed: bool,
    ) -> MT5OrderResult:
        if not confirmed:
            raise MT5Error("يجب تأكيد إغلاق المركز يدويًا.")
        self._validate_volume(volume)

        api, terminal, account = self._connect()
        self._require_trading_enabled(terminal, account)
        symbol_info, tick = self._symbol(api, symbol)
        positions = api.positions_get(symbol=symbol)
        if positions is None:
            raise MT5Error(f"تعذّر قراءة مراكز {symbol}: {api.last_error()}")
        position = next(
            (item for item in positions if int(item.ticket) == ticket), None
        )
        if position is None:
            raise MT5Error(f"المركز #{ticket} لم يعد مفتوحًا على {symbol}.")
        if volume > float(position.volume):
            raise MT5Error("حجم الإغلاق أكبر من حجم المركز المفتوح.")
        self._validate_symbol_volume(symbol_info, volume)

        is_buy = position.type == api.POSITION_TYPE_BUY
        request = {
            "action": api.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": api.ORDER_TYPE_SELL if is_buy else api.ORDER_TYPE_BUY,
            "position": ticket,
            "price": float(tick.bid if is_buy else tick.ask),
            "deviation": MAX_DEVIATION_POINTS,
            "magic": 601018,
            "comment": "BTC manual close",
            "type_time": api.ORDER_TIME_GTC,
        }
        return self._send_order(api, request)

    @staticmethod
    def _require_trading_enabled(terminal: Any, account: Any) -> None:
        if not getattr(terminal, "trade_allowed", False):
            raise MT5Error("التداول معطّل في إعدادات منصة MT5.")
        if not getattr(account, "trade_allowed", False):
            raise MT5Error("الحساب الحالي لا يسمح بإرسال أوامر تداول.")

    @staticmethod
    def _validate_volume(volume: float) -> None:
        if not math.isfinite(volume) or volume <= 0:
            raise MT5Error("حجم الصفقة يجب أن يكون رقمًا موجبًا.")
        if volume > MAX_LIVE_VOLUME:
            raise MT5Error(
                f"حجم الأمر يتجاوز الحد الأقصى المحدد ({MAX_LIVE_VOLUME:.3f} لوت)."
            )

    @staticmethod
    def _validate_symbol_volume(symbol_info: Any, volume: float) -> None:
        minimum = float(symbol_info.volume_min)
        maximum = float(symbol_info.volume_max)
        step = float(symbol_info.volume_step)
        if not all(math.isfinite(value) for value in (minimum, maximum, step)):
            raise MT5Error("أعاد الوسيط قيود حجم غير صالحة.")
        if volume < minimum or volume > maximum:
            raise MT5Error(
                f"حجم {volume:g} خارج نطاق الوسيط لهذا الرمز "
                f"({minimum:g}–{maximum:g} لوت)."
            )
        if step <= 0:
            raise MT5Error("أعاد الوسيط خطوة حجم غير صالحة.")
        steps = (Decimal(str(volume)) - Decimal(str(minimum))) / Decimal(str(step))
        if steps != steps.to_integral_value():
            raise MT5Error(
                f"حجم الأمر لا يطابق خطوة الوسيط ({step:g} لوت)."
            )

    @staticmethod
    def _send_order(api: Any, request: dict[str, Any]) -> MT5OrderResult:
        check_errors = []
        for filling in (
            api.ORDER_FILLING_FOK,
            api.ORDER_FILLING_IOC,
            api.ORDER_FILLING_RETURN,
        ):
            request["type_filling"] = filling
            check = api.order_check(request)
            if check is not None and getattr(check, "retcode", None) == 0:
                break
            comment = getattr(check, "comment", "تعذّر فحص الأمر")
            check_errors.append(str(comment))
        else:
            raise MT5Error(
                "رفض فحص الأمر قبل الإرسال: " + " | ".join(check_errors)
            )

        result = api.order_send(request)
        if result is None:
            raise MT5Error(f"لم تُرجع MT5 نتيجة للأمر: {api.last_error()}")
        successful_codes = {
            api.TRADE_RETCODE_DONE,
            api.TRADE_RETCODE_DONE_PARTIAL,
        }
        if result.retcode not in successful_codes:
            raise MT5Error(
                f"رفض الوسيط الأمر ({result.retcode}): {result.comment}"
            )
        return MT5OrderResult(
            order=int(getattr(result, "order", 0)),
            deal=int(getattr(result, "deal", 0)),
            volume=float(getattr(result, "volume", request["volume"])),
            price=float(getattr(result, "price", request["price"])),
            comment=str(getattr(result, "comment", "")),
        )
