import csv
from collections import deque
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import altair as alt
import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

import btc_paper_bot as bot
import mt5_connector as mt5
import account_links
import btc_mobile_auth as mobile_auth


NEWS_FEEDS = (
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
)

st.set_page_config(
    page_title="محاكاة تداول BTC",
    page_icon=":material/currency_bitcoin:",
    layout="wide",
)


def authenticate_remote_client() -> str | None:
    if mobile_auth.is_local_access(
        st.context.ip_address,
        st.get_option("server.address"),
    ):
        return None

    secrets_path = Path(__file__).parent / ".streamlit" / "secrets.toml"
    if not secrets_path.is_file():
        st.title("تفعيل الدخول الآمن للهاتف")
        st.error(
            "لم يتم إعداد تسجيل Google بعد؛ لم تُعرض بيانات الحساب أو أدوات التداول.",
            title="الوصول عبر الشبكة مغلق",
        )
        st.caption(
            "أكمل إعداد .streamlit/secrets.toml.example محليًا، ثم أعد تشغيل "
            "اللوحة. لا ترسل Client Secret أو كلمة المرور هنا."
        )
        st.stop()

    try:
        allowed_email = mobile_auth.validate_google_auth_settings(st.secrets)
        mobile_auth.validate_https_request_url(
            st.context.url,
            st.secrets["auth"]["redirect_uri"],
        )
    except (
        mobile_auth.MobileAuthConfigurationError,
        StreamlitSecretNotFoundError,
    ) as exc:
        st.title("تفعيل الدخول الآمن للهاتف")
        st.error(str(exc), title="إعداد تسجيل Google غير مكتمل")
        st.stop()

    if importlib.util.find_spec("authlib") is None:
        st.title("تفعيل الدخول الآمن للهاتف")
        st.error(
            "مكتبة تسجيل الدخول غير مثبتة. ثبّت اعتماديات المشروع قبل المتابعة.",
            title="Authlib غير متاحة",
        )
        st.code(
            r".venv\Scripts\python.exe -m pip install -r requirements.txt",
            language="powershell",
        )
        st.stop()

    if not st.user.is_logged_in:
        st.title("تسجيل الدخول إلى لوحة التداول")
        st.caption("الوصول من الشبكة يتطلب حساب Google المصرح به.")
        if st.button(
            "تسجيل الدخول باستخدام Google",
            type="primary",
            icon=":material/login:",
        ):
            st.login("google")
        st.stop()

    email = st.user.get("email")
    email_verified = st.user.get("email_verified")
    expires_at = st.user.get("exp")
    if not mobile_auth.is_authorized_google_user(
        email, email_verified, allowed_email, expires_at
    ):
        st.error(
            "هذا الحساب غير مصرح له أو انتهت جلسة الدخول.",
            title="تم رفض الوصول",
        )
        if st.button("تسجيل الخروج", key="rejected_google_logout"):
            st.logout()
        st.stop()

    return email


mobile_user_email = authenticate_remote_client()

st.title("محاكاة تداول BTC", icon=":material/currency_bitcoin:")
st.caption("أسعار مباشرة من مصادر عامة، مع بدائل تلقائية عند تعذر أحدها.")
st.warning(
    "المحاكاة لا ترسل أوامر حقيقية. أوامر MT5 منفصلة، يدوية فقط، "
    "وتتطلب تأكيدًا لكل أمر.",
    title="تنبيه قبل التداول",
)

with st.sidebar:
    if mobile_user_email is not None:
        st.caption("اتصال الهاتف محمي بتسجيل Google.")
        if st.button("تسجيل الخروج", key="mobile_google_logout"):
            st.logout()
        st.divider()
    st.subheader("التحكم", icon=":material/tune:")
    st.toggle("تشغيل المحاكاة الورقية", key="paper_running", value=True)
    st.caption(f"تحديث السعر كل {bot.POLL_SECONDS} ثوانٍ")
    st.caption(f"رأس المال الافتراضي: ${bot.START_CASH:,.2f}")

if "paper_broker" not in st.session_state:
    st.session_state.paper_broker = bot.PaperBroker()
    st.session_state.paper_prices = deque(maxlen=bot.SLOW_N)
    st.session_state.price_history = deque(maxlen=1000)
    st.session_state.previous_fast = None
    st.session_state.previous_slow = None
    st.session_state.current_fast = None
    st.session_state.current_slow = None
    st.session_state.last_price = None
    st.session_state.last_update = None
    st.session_state.last_error = None
    st.session_state.last_signal = "بانتظار إشارة"

if "mt5_client" not in st.session_state:
    st.session_state.mt5_client = mt5.MT5Client()


@st.fragment(run_every=bot.POLL_SECONDS)
def live_dashboard():
    history, history_error = load_market_history()
    st.session_state.market_history = history
    st.session_state.market_history_error = history_error

    broker = st.session_state.paper_broker
    price = st.session_state.last_price

    if st.session_state.paper_running:
        try:
            price = bot.fetch_price()
        except RuntimeError as exc:
            st.session_state.last_error = str(exc)
        else:
            st.session_state.last_error = None
            st.session_state.last_price = price
            st.session_state.last_update = datetime.now().astimezone()
            st.session_state.paper_prices.append(price)
            st.session_state.price_history.append(
                {"time": st.session_state.last_update, "price": price}
            )

            broker.check_levels(price)
            if len(st.session_state.paper_prices) == bot.SLOW_N:
                fast = bot.sma(st.session_state.paper_prices, bot.FAST_N)
                slow = bot.sma(st.session_state.paper_prices, bot.SLOW_N)
                st.session_state.current_fast = fast
                st.session_state.current_slow = slow
                previous_fast = st.session_state.previous_fast
                previous_slow = st.session_state.previous_slow

                if previous_fast is not None and previous_slow is not None:
                    crossed_up = previous_fast <= previous_slow and fast > slow
                    crossed_down = previous_fast >= previous_slow and fast < slow
                    if crossed_up:
                        broker.buy(price, "تقاطع صعودي")
                        if broker.qty > 0:
                            st.session_state.last_signal = "شراء افتراضي — تقاطع صعودي"
                    elif crossed_down:
                        was_open = broker.qty > 0
                        broker.sell(price, "تقاطع هبوطي")
                        if was_open:
                            st.session_state.last_signal = "بيع افتراضي — تقاطع هبوطي"

                st.session_state.previous_fast = fast
                st.session_state.previous_slow = slow

    if st.session_state.last_error:
        st.warning(
            f"تعذّر تحديث السعر؛ ستُعرض آخر قيمة ناجحة إن توفرت. "
            f"{st.session_state.last_error}",
            title="مشكلة في الاتصال",
        )
    elif not st.session_state.paper_running:
        st.info("المحاكاة متوقفة مؤقتًا.")

    if price is None:
        st.info("جارٍ جلب أول سعر...")
        equity = broker.cash
    else:
        equity = broker.equity(price)
    st.session_state.market_change_24h = calculate_24h_change(history, price)

    unrealized_pnl = (
        (price - broker.entry) * broker.qty
        if price is not None and broker.qty > 0
        else 0.0
    )
    total_pnl = equity - bot.START_CASH

    fast = st.session_state.current_fast
    slow = st.session_state.current_slow
    if fast is None or slow is None:
        trend_label = f"{len(st.session_state.paper_prices)}/{bot.SLOW_N}"
        trend_delta = None
    else:
        trend_delta = (fast / slow - 1) * 100 if slow else 0.0
        if fast > slow:
            trend_label = "صاعد"
        elif fast < slow:
            trend_label = "هابط"
        else:
            trend_label = "محايد"

    change_24h = st.session_state.market_change_24h
    if change_24h is None:
        trend_24h_label = "غير متاح"
    elif change_24h > 0:
        trend_24h_label = "صاعد"
    elif change_24h < 0:
        trend_24h_label = "هابط"
    else:
        trend_24h_label = "محايد"

    with st.grid(3, row_height=128, gap="small"):
        st.metric(
            "سعر BTC/USD",
            f"${price:,.2f}" if price is not None else "—",
            border=True,
        )
        st.metric(
            "اتجاه السوق خلال 24 ساعة",
            trend_24h_label,
            delta=f"{change_24h:+.2f}%" if change_24h is not None else None,
            delta_color="normal",
            border=True,
        )
        st.metric(
            "اتجاه الاستراتيجية 6/18",
            trend_label,
            delta=(
                f"{trend_delta:+.2f}% فرق المتوسطات"
                if trend_delta is not None
                else None
            ),
            delta_color="normal",
            border=True,
        )
        st.metric(
            "حالة الصفقة الافتراضية",
            "مفتوحة" if broker.qty > 0 else "لا توجد",
            border=True,
        )
        st.metric(
            "قيمة المحفظة الافتراضية",
            f"${equity:,.2f}",
            delta=f"${total_pnl:+,.2f}",
            border=True,
        )
        st.metric(
            "ربح/خسارة الصفقة المفتوحة",
            f"${unrealized_pnl:+,.2f}",
            border=True,
        )

    last_update = st.session_state.last_update
    if last_update is not None:
        st.caption(f"آخر تحديث: {last_update:%Y-%m-%d %H:%M:%S %Z}")
    if fast is None or slow is None:
        st.caption(
            f"تكوين اتجاه الاستراتيجية: "
            f"{len(st.session_state.paper_prices)}/{bot.SLOW_N} قراءة"
        )
    st.caption(f"آخر إشارة: {st.session_state.last_signal}")

@st.cache_data(ttl="5m", show_spinner=False)
def load_market_history():
    try:
        return bot.fetch_price_history(), None
    except RuntimeError as exc:
        return [], str(exc)


def calculate_24h_change(history, current_price=None):
    history_data = pd.DataFrame(history)
    if history_data.empty:
        return None

    history_data["time"] = pd.to_datetime(history_data["time"], utc=True)
    history_data = history_data.sort_values("time")
    latest = history_data.iloc[-1]
    if current_price is None:
        current_price = latest["price"]
        current_time = latest["time"]
    else:
        current_time = pd.Timestamp.now(tz="UTC")
    target_time = current_time - pd.Timedelta(hours=24)
    reference = history_data[history_data["time"] <= target_time]
    if reference.empty or reference.iloc[-1]["price"] <= 0:
        return None

    return (current_price / reference.iloc[-1]["price"] - 1) * 100


@st.cache_data(ttl="10m", show_spinner=False)
def load_market_news():
    articles = []
    errors = []
    for source, feed_url in NEWS_FEEDS:
        try:
            request = urllib.request.Request(
                feed_url,
                headers={"User-Agent": bot.USER_AGENT, "Accept": "application/rss+xml"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                root = ET.fromstring(response.read())

            items = root.findall(".//item")
            source_count = 0
            for item in items:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                parsed_link = urlparse(link)
                if (
                    title
                    and parsed_link.scheme in {"http", "https"}
                    and parsed_link.netloc
                ):
                    articles.append(
                        {
                            "source": source,
                            "title": title,
                            "link": link,
                            "published": (item.findtext("pubDate") or "").strip(),
                        }
                    )
                    source_count += 1
                    if source_count >= 4:
                        break
        except (ET.ParseError, OSError, ValueError) as exc:
            errors.append(f"{source}: {exc}")

    return articles, errors


@st.fragment(run_every="10m")
def market_news():
    with st.container(border=True):
        st.subheader("أخبار السوق", icon=":material/newspaper:")
        st.caption("عناوين حديثة من CoinDesk وCointelegraph.")
        articles, errors = load_market_news()

        if articles:
            for article in articles:
                with st.container(border=True):
                    st.badge(article["source"], color="blue")
                    st.link_button(
                        article["title"][:120],
                        article["link"],
                        help=article["title"],
                        icon=":material/open_in_new:",
                        width="stretch",
                    )
                    if article["published"]:
                        st.caption(article["published"])
        else:
            st.warning(
                "تعذّر تحميل الأخبار من المصادر العامة.",
                title="الأخبار غير متاحة مؤقتًا",
            )

        if errors:
            with st.expander("تفاصيل مصادر الأخبار المتعذرة"):
                st.text("\n".join(errors))


@st.fragment(run_every=bot.POLL_SECONDS)
def recent_trades():
    with st.container(border=True):
        st.subheader("أحدث الصفقات الافتراضية")
        with open(bot.LOG_FILE, newline="", encoding="utf-8") as trades_file:
            trades = list(csv.DictReader(trades_file))
        if not trades:
            st.caption("لم تُسجّل أي صفقات بعد.")
            return

        recent = pd.DataFrame(trades[-15:]).rename(
            columns={
                "time_utc": "الوقت UTC",
                "side": "النوع",
                "price": "السعر",
                "qty": "الكمية BTC",
                "reason": "السبب",
                "pnl": "الربح/الخسارة",
                "cash": "النقد",
                "equity": "قيمة المحفظة",
            }
        )
        recent["النوع"] = recent["النوع"].map(
            {"BUY": "شراء", "SELL": "بيع"}
        ).fillna(recent["النوع"])
        recent["السبب"] = recent["السبب"].replace(
            {
                "stop-loss": "وقف الخسارة",
                "target": "الهدف",
                "signal": "إشارة",
            }
        )
        st.dataframe(
            recent.iloc[::-1],
            hide_index=True,
            alt="أحدث الصفقات الافتراضية المسجلة",
        )


@st.fragment(run_every=bot.POLL_SECONDS)
def mt5_panel():
    symbol = "BTCUSD"
    client = st.session_state.mt5_client

    with st.container(border=True):
        st.subheader("MetaTrader 5", icon=":material/candlestick_chart:")
        st.caption(
            f"اتصال بالطرفية المفتوحة، الرمز {symbol}. "
            "لا تُنفّذ إشارات الاستراتيجية صفقات حقيقية."
        )
        try:
            snapshot = client.snapshot(symbol)
        except mt5.MT5Error as exc:
            st.error(str(exc), title="تعذّر الاتصال بـMetaTrader 5")
            return

        account = snapshot.account
        market = snapshot.symbol
        if account.is_real:
            st.warning(
                "متصل بحساب حقيقي. أي أمر مؤكد سيستخدم أموالًا فعلية.",
                title="حساب حقيقي",
            )
        else:
            st.info("الحساب المتصل ليس حسابًا حقيقيًا.")

        st.caption(
            f"الحساب {account.login} · {account.server} · {account.currency}"
        )
        with st.grid(3, row_height=105, gap="small"):
            st.metric("رصيد MT5", f"{account.balance:,.2f} {account.currency}")
            st.metric("حقوق الحساب", f"{account.equity:,.2f} {account.currency}")
            st.metric(
                f"سعر {symbol}",
                f"{market.ask:,.2f}",
                delta=f"فارق السعر {market.ask - market.bid:,.2f}",
                delta_color="off",
            )

        if not market.buy_enabled:
            st.warning(f"الوسيط لا يسمح بفتح شراء جديد على الرمز {symbol}.")
        elif not account.trading_enabled:
            st.warning("الحساب أو الطرفية لا يسمحان بالتداول.")

        if snapshot.positions:
            positions = pd.DataFrame(
                [
                    {
                        "رقم المركز": item.ticket,
                        "النوع": item.side,
                        "الحجم (لوت)": item.volume,
                        "سعر الفتح": item.open_price,
                        "السعر الحالي": item.current_price,
                        "وقف الخسارة": item.stop_loss,
                        "الهدف": item.take_profit,
                        "الربح/الخسارة": item.profit,
                    }
                    for item in snapshot.positions
                ]
            )
            st.dataframe(
                positions,
                hide_index=True,
                alt=f"مراكز {symbol} المفتوحة في MetaTrader 5",
            )
        else:
            st.caption(f"لا توجد مراكز مفتوحة على {symbol}.")

        enabled = st.toggle(
            "إظهار أدوات الأوامر الحقيقية اليدوية",
            key="mt5_manual_orders_enabled",
            value=False,
        )
        if not enabled:
            st.caption("أدوات إرسال الأوامر مخفية حتى تفعيلها يدويًا.")
            return

        st.caption(
            f"الحد الأقصى لكل أمر: {mt5.MAX_LIVE_VOLUME:.3f} لوت. "
            f"القيمة الاسمية التقريبية عند الحد الأقصى: "
            f"${market.ask * market.contract_size * mt5.MAX_LIVE_VOLUME:,.2f}. "
            f"الشراء يرفق وقفًا {bot.STOP_PCT:g}% وهدفًا {bot.TARGET_PCT:g}%."
        )
        if market.volume_min > mt5.MAX_LIVE_VOLUME:
            st.warning(
                f"الحد الأدنى لدى الوسيط ({market.volume_min:g} لوت) "
                f"أكبر من حدك ({mt5.MAX_LIVE_VOLUME:.3f})؛ لن يُرسل أمر شراء."
            )
        elif market.buy_enabled and account.trading_enabled:
            with st.form("mt5_open_long_form", border=True):
                volume = st.number_input(
                    "حجم الشراء (لوت)",
                    min_value=market.volume_min,
                    max_value=mt5.MAX_LIVE_VOLUME,
                    value=min(0.01, mt5.MAX_LIVE_VOLUME),
                    step=market.volume_step,
                    format="%.3f",
                )
                confirmed = st.checkbox(
                    "أؤكد إرسال أمر شراء حقيقي بهذا الحجم"
                )
                open_submitted = st.form_submit_button(
                    "تأكيد شراء BTC",
                    type="primary",
                    icon=":material/trending_up:",
                    width="stretch",
                )
            if open_submitted:
                try:
                    result = client.open_long(
                        symbol, volume, confirmed=confirmed
                    )
                except mt5.MT5Error as exc:
                    st.error(str(exc), title="لم يُرسل أمر الشراء")
                else:
                    st.success(
                        f"قُبل الأمر #{result.order} بحجم {result.volume:g} "
                        f"لوت عند {result.price:,.2f}."
                    )

        for position in snapshot.positions:
            if market.volume_min > mt5.MAX_LIVE_VOLUME:
                st.caption(
                    f"لا يمكن إغلاق المركز #{position.ticket} من هنا لأن "
                    "الحد الأدنى للوسيط أعلى من حد الأمر."
                )
                continue
            close_volume = min(position.volume, mt5.MAX_LIVE_VOLUME)
            with st.form(f"mt5_close_form_{position.ticket}", border=True):
                st.caption(
                    f"إغلاق جزئي للمركز #{position.ticket}: "
                    f"حتى {mt5.MAX_LIVE_VOLUME:.3f} لوت."
                )
                volume = st.number_input(
                    f"حجم الإغلاق للمركز #{position.ticket} (لوت)",
                    min_value=market.volume_min,
                    max_value=mt5.MAX_LIVE_VOLUME,
                    value=close_volume,
                    step=market.volume_step,
                    format="%.3f",
                    key=f"mt5_close_volume_{position.ticket}",
                )
                confirmed = st.checkbox(
                    f"أؤكد إغلاق هذا الحجم من المركز #{position.ticket}",
                    key=f"mt5_close_confirm_{position.ticket}",
                )
                close_submitted = st.form_submit_button(
                    f"تأكيد الإغلاق الجزئي #{position.ticket}",
                    icon=":material/close:",
                    width="stretch",
                )
            if close_submitted:
                try:
                    result = client.close_position(
                        symbol,
                        position.ticket,
                        volume,
                        confirmed=confirmed,
                    )
                except mt5.MT5Error as exc:
                    st.error(str(exc), title="لم يُغلق المركز")
                else:
                    st.success(
                        f"قُبل أمر الإغلاق الجزئي #{result.order} "
                        f"بحجم {result.volume:g} لوت."
                    )


def account_links_panel():
    with st.container(border=True):
        st.subheader("روابط حسابات التداول", icon=":material/link:")
        st.caption(
            "أضف اختصارات آمنة لفتح مواقع الوسيط أو منصة التداول من الهاتف."
        )
        st.info(
            "هذه الروابط تفتح الموقع فقط؛ لا تربط MT5 على الهاتف ولا تنفّذ "
            "صفقات. تُحفظ محليًا على الجهاز الذي يشغّل اللوحة. لا تضف كلمات "
            "مرور أو مفاتيح API."
        )
        st.subheader("منصات سريعة")
        st.link_button(
            "MetaTrader 5 WebTerminal",
            "https://web.metatrader.app/terminal?mode=demo&lang=en",
            icon=":material/open_in_new:",
            help="المنصة الرسمية؛ قد تحتاج إلى اختيار خادم الوسيط عند تسجيل الدخول.",
            width="stretch",
        )
        st.link_button(
            "TradingView",
            "https://www.tradingview.com/",
            icon=":material/open_in_new:",
            width="stretch",
        )
        st.caption(
            "للوصول إلى حسابك الحقيقي، أضف رابط وسيطك أدناه وسجّل الدخول "
            "داخل موقع الوسيط فقط."
        )

        try:
            links = account_links.load_account_links()
        except account_links.AccountLinksError as exc:
            st.error(str(exc), title="تعذّر تحميل روابط الحسابات")
            return

        with st.form("add_account_link_form", border=True):
            link_name = st.text_input(
                "اسم المنصة أو الحساب",
                placeholder="مثال: وسيطي أو منصة التداول",
                max_chars=80,
            )
            link_url = st.text_input(
                "رابط صفحة الدخول",
                placeholder="https://example.com/login",
            )
            submitted = st.form_submit_button(
                "إضافة رابط",
                icon=":material/add_link:",
                type="primary",
                width="stretch",
            )

        if submitted:
            try:
                new_link = account_links.validate_account_link(
                    link_name, link_url
                )
                links.append(new_link)
                account_links.save_account_links(links)
            except account_links.AccountLinksError as exc:
                st.error(str(exc), title="لم يُضف الرابط")
            else:
                st.success(f"تمت إضافة رابط {new_link['name']}.")

        if not links:
            st.caption("لم تُضف روابط بعد.")
            return

        for index, link in enumerate(links):
            with st.container(border=True):
                st.markdown(f"**{link['name']}**")
                st.link_button(
                    "فتح صفحة الحساب",
                    link["url"],
                    icon=":material/open_in_new:",
                    width="stretch",
                )
                if st.button(
                    "حذف الرابط",
                    key=f"delete_account_link_{index}",
                    icon=":material/delete:",
                    width="stretch",
                ):
                    links.pop(index)
                    try:
                        account_links.save_account_links(links)
                    except account_links.AccountLinksError as exc:
                        st.error(str(exc), title="تعذّر حذف الرابط")
                    else:
                        st.rerun()


@st.fragment(run_every=bot.POLL_SECONDS)
def market_chart():
    with st.container(border=True):
        st.subheader("الرسم السعري", icon=":material/show_chart:")
        st.caption("بيانات تاريخية بفواصل 15 دقيقة مع السعر المباشر.")
        chart_period = st.segmented_control(
            "الفترة",
            ["1 ساعة", "6 ساعات", "24 ساعة", "5 أيام"],
            default="24 ساعة",
            key="market_chart_period",
        )
        period_hours = {
            "1 ساعة": 1,
            "6 ساعات": 6,
            "24 ساعة": 24,
            "5 أيام": 120,
        }

        chart_error = st.session_state.market_history_error
        chart_rows = st.session_state.market_history + list(
            st.session_state.price_history
        )
        chart_data = pd.DataFrame(chart_rows)
        if not chart_data.empty:
            chart_data["time"] = pd.to_datetime(chart_data["time"], utc=True)
            chart_data = (
                chart_data.drop_duplicates(subset="time", keep="last")
                .sort_values("time")
            )
            hours = period_hours.get(chart_period or "24 ساعة", 24)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            chart_data = chart_data[chart_data["time"] >= cutoff]

        if chart_error:
            st.warning(chart_error, title="بيانات الرسم التاريخي غير متاحة")
        if len(chart_data) < 2:
            st.caption("لا توجد نقاط كافية لرسم الفترة المحددة بعد.")
        else:
            base_chart = alt.Chart(chart_data).encode(
                x=alt.X("time:T", title="الوقت"),
                y=alt.Y(
                    "price:Q",
                    title="السعر بالدولار",
                    scale=alt.Scale(zero=False),
                ),
                tooltip=[
                    alt.Tooltip("time:T", title="الوقت"),
                    alt.Tooltip("price:Q", title="السعر", format=",.2f"),
                ],
            )
            price_chart = base_chart.mark_line(strokeWidth=2).properties(height=340)
            st.altair_chart(
                price_chart,
                alt="السعر التاريخي لبيتكوين خلال الفترة المحددة",
            )
        st.caption(
            "الاتجاه القصير يقارن متوسط 6 قراءات بمتوسط 18 قراءة؛ ليس توقعًا مؤكدًا."
        )


st.subheader("ملخص السوق", icon=":material/query_stats:")
live_dashboard()

st.header("السوق والأخبار", icon=":material/monitoring:")
chart_column, news_column = st.columns([1.8, 1], gap="medium")
with chart_column:
    market_chart()

with news_column:
    market_news()

st.header("الحساب والتداول", icon=":material/account_balance_wallet:")
mt5_panel()
account_links_panel()
recent_trades()
