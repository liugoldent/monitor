from datetime import datetime, time as dt_time
import time


START_WORK = dt_time(8, 0)
END_WORK = dt_time(10, 0)
START_OFF = dt_time(18, 0)
END_OFF = dt_time(20, 0)


def _format_timestamp(now: datetime) -> str:
    return f"{now.month}/{now.day}。{now:%H:%M}"


def _in_range(now_time: dt_time, start: dt_time, end: dt_time) -> bool:
    return start <= now_time <= end


def _sleep_until_next_minute() -> None:
    now = datetime.now()
    sleep_seconds = 60 - now.second
    if sleep_seconds <= 0:
        sleep_seconds = 60
    time.sleep(sleep_seconds)


def main() -> None:
    last_minute_key = ""
    while True:
        now = datetime.now()
        minute_key = now.strftime("%Y-%m-%d %H:%M")
        if minute_key != last_minute_key:
            last_minute_key = minute_key
            current_time = now.time()
            timestamp = _format_timestamp(now)
            if _in_range(current_time, START_WORK, END_WORK):
                print(f"HQT - Keedem {timestamp} 上班")
            elif _in_range(current_time, START_OFF, END_OFF):
                print(f"HQT - Keedem {timestamp} 下班")
        _sleep_until_next_minute()


if __name__ == "__main__":
    main()
