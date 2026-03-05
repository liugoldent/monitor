import time
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib import request, error


URL = "https://monitor-9dtg.onrender.com/"
INTERVAL_SECONDS = 15 * 60
TIMEOUT_SECONDS = 20
TZ = ZoneInfo("Asia/Taipei")


def _now() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def ping_once() -> None:
    req = request.Request(URL, method="GET")
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            status_code = resp.getcode()
            print(f"[{_now()}] GET {URL} -> {status_code}", flush=True)
    except error.HTTPError as exc:
        print(f"[{_now()}] GET {URL} -> HTTPError {exc.code}", flush=True)
    except Exception as exc:
        print(f"[{_now()}] GET {URL} failed: {exc}", flush=True)


def main() -> None:
    while True:
        ping_once()
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
