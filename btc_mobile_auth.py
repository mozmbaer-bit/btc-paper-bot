from collections.abc import Mapping
from ipaddress import IPv6Address, ip_address
import time
from urllib.parse import urlparse


GOOGLE_METADATA_URL = (
    "https://accounts.google.com/.well-known/openid-configuration"
)


class MobileAuthConfigurationError(ValueError):
    """Raised when Google sign-in settings are missing or unsafe."""


def is_loopback_client(client_ip: str | None) -> bool:
    if not client_ip:
        return False
    try:
        address = ip_address(client_ip)
    except ValueError:
        return False
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def is_local_access(client_ip: str | None, server_address: str | None) -> bool:
    if server_address in {"127.0.0.1", "::1", "localhost"}:
        return True
    return is_loopback_client(client_ip)


def _section(parent: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = parent.get(key)
    if not isinstance(value, Mapping):
        raise MobileAuthConfigurationError(
            f"قسم [{key}] مفقود أو غير صالح في ملف الإعدادات."
        )
    return value


def _setting(section: Mapping[str, object], key: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MobileAuthConfigurationError(
            f"أكمل إعداد {key} في ملف الإعدادات المحلي."
        )
    return value.strip()


def validate_google_auth_settings(
    secrets: Mapping[str, object],
) -> str:
    auth = _section(secrets, "auth")
    google = _section(auth, "google")
    mobile_access = _section(secrets, "mobile_access")

    redirect_uri = _setting(auth, "redirect_uri")
    cookie_secret = _setting(auth, "cookie_secret")
    client_id = _setting(google, "client_id")
    client_secret = _setting(google, "client_secret")
    metadata_url = _setting(google, "server_metadata_url")
    allowed_email = _setting(mobile_access, "allowed_email").casefold()

    parsed_redirect = urlparse(redirect_uri)
    if (
        parsed_redirect.scheme != "https"
        or not parsed_redirect.hostname
        or parsed_redirect.path != "/oauth2callback"
        or parsed_redirect.username is not None
        or parsed_redirect.password is not None
        or parsed_redirect.query
        or parsed_redirect.fragment
    ):
        raise MobileAuthConfigurationError(
            "عنوان التحويل يجب أن يكون HTTPS وينتهي بـ/oauth2callback."
        )
    if len(cookie_secret) < 32 or cookie_secret.casefold().startswith("replace"):
        raise MobileAuthConfigurationError(
            "أنشئ قيمة عشوائية قوية لـcookie_secret بطول 32 حرفًا على الأقل."
        )
    if client_id.casefold().startswith("your_") or client_secret.casefold().startswith(
        "your_"
    ):
        raise MobileAuthConfigurationError(
            "أكمل Client ID وClient Secret في ملف الإعدادات المحلي."
        )
    if metadata_url != GOOGLE_METADATA_URL:
        raise MobileAuthConfigurationError(
            "استخدم عنوان OpenID الرسمي الخاص بـGoogle."
        )
    if "@" not in allowed_email or allowed_email.startswith("@"):
        raise MobileAuthConfigurationError(
            "أدخل بريد Google المسموح له بالدخول في الإعدادات المحلية."
        )

    return allowed_email


def validate_https_request_url(request_url: str, redirect_uri: str) -> None:
    request = urlparse(request_url)
    redirect = urlparse(redirect_uri)
    if request.scheme != "https" or not request.hostname:
        raise MobileAuthConfigurationError(
            "افتح اللوحة عبر عنوان HTTPS موثوق من الهاتف."
        )

    try:
        request_port = request.port or 443
        redirect_port = redirect.port or 443
    except ValueError as exc:
        raise MobileAuthConfigurationError(
            "عنوان HTTPS أو منفذ اللوحة غير صالح."
        ) from exc

    if (
        redirect.scheme != "https"
        or request.hostname.casefold() != (redirect.hostname or "").casefold()
        or request_port != redirect_port
    ):
        raise MobileAuthConfigurationError(
            "عنوان اللوحة يجب أن يطابق عنوان HTTPS المسجل لدى Google."
        )


def is_authorized_google_user(
    email: object,
    email_verified: object,
    allowed_email: str,
    expires_at: object,
    now: float | None = None,
) -> bool:
    if not isinstance(email, str) or email_verified is not True:
        return False
    if not isinstance(expires_at, (int, float)):
        return False
    current_time = time.time() if now is None else now
    return (
        email.casefold() == allowed_email.casefold()
        and expires_at > current_time
    )
