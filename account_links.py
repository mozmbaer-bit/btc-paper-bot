import json
from pathlib import Path
from urllib.parse import parse_qsl, urlparse


ACCOUNT_LINKS_FILE = Path(__file__).with_name("account_links.json")


class AccountLinksError(ValueError):
    """Raised when saved account links cannot be read or validated."""


def validate_account_link(name: str, url: str) -> dict[str, str]:
    name = name.strip()
    url = url.strip()
    if not name:
        raise AccountLinksError("أدخل اسمًا للرابط.")

    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise AccountLinksError(
            "أدخل رابط HTTPS صالحًا من دون اسم مستخدم أو كلمة مرور داخل الرابط."
        )

    sensitive_query_keys = {"apikey", "password", "passwd", "secret", "token"}
    if any(
        any(part in key.casefold().replace("-", "_") for part in sensitive_query_keys)
        for key, _ in parse_qsl(parsed.query)
    ):
        raise AccountLinksError(
            "لا تحفظ روابط تحتوي على كلمة مرور أو رمز دخول أو مفتاح API."
        )

    return {"name": name, "url": url}


def load_account_links(path: Path = ACCOUNT_LINKS_FILE) -> list[dict[str, str]]:
    try:
        raw_links = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise AccountLinksError(f"تعذّرت قراءة ملف الروابط: {exc}") from exc

    if not isinstance(raw_links, list):
        raise AccountLinksError("تنسيق ملف الروابط غير صالح.")

    links = []
    for item in raw_links:
        if not isinstance(item, dict):
            raise AccountLinksError("يوجد رابط محفوظ بتنسيق غير صالح.")
        name, url = item.get("name"), item.get("url")
        if not isinstance(name, str) or not isinstance(url, str):
            raise AccountLinksError("يوجد رابط محفوظ ينقصه الاسم أو العنوان.")
        links.append(validate_account_link(name, url))
    return links


def save_account_links(
    links: list[dict[str, str]], path: Path = ACCOUNT_LINKS_FILE
) -> None:
    validated = []
    for item in links:
        name, url = item.get("name"), item.get("url")
        if not isinstance(name, str) or not isinstance(url, str):
            raise AccountLinksError("كل رابط يحتاج إلى اسم وعنوان صالحين.")
        validated.append(validate_account_link(name, url))

    try:
        path.write_text(
            json.dumps(validated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise AccountLinksError(f"تعذّر حفظ ملف الروابط: {exc}") from exc
