import os
import re
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import PhoneNumberInvalidError


BACKEND_DIR = Path("/app/backend-futures-py")
ENV_PATH = BACKEND_DIR / ".env"
SESSION_PATH = Path(
    os.getenv(
        "TELEGRAM_SESSION_PATH",
        str(BACKEND_DIR / "session_monitor_six_strategy"),
    )
)
SESSION_MARKER_PATH = (
    Path(os.environ["TELEGRAM_SESSION_MARKER"])
    if os.getenv("TELEGRAM_SESSION_MARKER")
    else None
)


def load_env_file() -> None:
    if not ENV_PATH.exists():
        raise RuntimeError(f"Required environment file is missing: {ENV_PATH}")

    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def normalize_phone(raw_phone: str) -> str:
    """Normalize common Taiwan input while preserving international numbers."""
    phone = re.sub(r"[\s()-]", "", raw_phone.strip())
    if phone.startswith("+8860"):
        return "+886" + phone[5:]
    if phone.startswith("886") and phone.isdigit():
        return "+" + phone
    if phone.startswith("09") and phone.isdigit():
        return "+886" + phone[1:]
    return phone


def start_interactive(client: TelegramClient) -> None:
    while True:
        raw_phone = input(
            "Telegram phone (Taiwan 09xxxxxxxx or +8869xxxxxxxx): "
        )
        phone = normalize_phone(raw_phone)
        if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
            print(
                "Invalid phone format. Enter a Taiwan mobile number as "
                "09xxxxxxxx or +8869xxxxxxxx."
            )
            continue
        try:
            client.start(phone=phone)
            return
        except PhoneNumberInvalidError:
            print(
                "Telegram rejected that phone number. Check the country code and "
                "try again; do not keep the leading 0 after +886."
            )


def main() -> None:
    load_env_file()
    client = TelegramClient(
        str(SESSION_PATH),
        int(require_env("API_ID")),
        require_env("API_HASH"),
    )
    start_interactive(client)
    me = client.get_me()
    identity = getattr(me, "username", None) or getattr(me, "id", "unknown")
    if SESSION_MARKER_PATH is not None:
        SESSION_MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        SESSION_MARKER_PATH.write_text("authorized\n", encoding="utf-8")
    print(f"Telegram login saved for: {identity}")
    client.disconnect()


if __name__ == "__main__":
    main()
