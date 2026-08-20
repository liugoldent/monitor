import os
from pathlib import Path

from telethon import TelegramClient


BACKEND_DIR = Path("/app/backend-futures-py")
ENV_PATH = BACKEND_DIR / ".env"
SESSION_PATH = BACKEND_DIR / "session_monitor_six_strategy"


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


def main() -> None:
    load_env_file()
    client = TelegramClient(
        str(SESSION_PATH),
        int(require_env("API_ID")),
        require_env("API_HASH"),
    )
    client.start()
    me = client.get_me()
    identity = getattr(me, "username", None) or getattr(me, "id", "unknown")
    print(f"Telegram login saved for: {identity}")
    client.disconnect()


if __name__ == "__main__":
    main()
