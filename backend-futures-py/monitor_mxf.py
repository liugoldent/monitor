import os
import csv
import math
import io
import json
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests
import time
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo
from pymongo import MongoClient

DISCORD_MXF_ALERT_WEBHOOK_ENV = "DISCORD_MXF_ALERT_WEBHOOK_URL"
DISCORD_MXF_VALUE_WEBHOOK_ENV = "DISCORD_MXF_VALUE_WEBHOOK_URL"
LAST_ALERT_STATE: str | None = None
LAST_ALIVE_SENT_SLOT: tuple[str, int] | None = None
H_TRADE_CSV_PATH = Path(__file__).resolve().parent / "tv_doc" / "h_trade.csv"
WEBHOOK_DATA_1MIN_PATH = Path(__file__).resolve().parent / "tv_doc" / "webhook_data_1min.csv"
MTX_BVAV_AVG_WINDOW = 23
MXF_CSV_HEADER = ["time", "tx_bvav", "mtx_bvav", "mtx_tbta", "mtx_bvav_avg", "signal", "trend"]

def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
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


load_env_file()

WEBHOOK_URL = os.getenv(DISCORD_MXF_ALERT_WEBHOOK_ENV, "").strip()
MXF_VALUE_WEBHOOK_URL = os.getenv(DISCORD_MXF_VALUE_WEBHOOK_ENV, "").strip()
MONGO_URI = require_env("MONGO_URI")
API_URL = "https://market-data-api.futures-ai.com/chip960_tradeinfo/"
GOODWAY_HISTORY_URL = "https://tvapi.futures-op.com/history"
GOODWAY_SYMBOLS = ("MTX-1", "MTX-2")
GOODWAY_POSITIVE_THRESHOLD = 1500
GOODWAY_GUERRILLA_THRESHOLDS = (1000, 1500)
GOODWAY_GUERRILLA_SYMBOLS = ("TX-1#goodway_chip960", "TX-2#goodway_chip960")
GOODWAY_GROUPS = (
    ("掛單能量", "trade_info", ("TX-1", "TX-2", "MTX-1", "MTX-2"),
     ("大戶掛單", "市場掛單", "散戶成交", "原始close", "原始volume")),
    ("主散方向", "bs_indicator_acc", ("TX-1", "TX-2", "MTX-1", "MTX-2"),
     ("總和", "大戶", "散戶", "外盤量", "內盤量")),
    ("台指籌碼（近次月前綴可能回傳同一合計）", "goodway_chip960", ("TX-1", "TX-2"),
     ("坦克", "游擊隊", "炮灰", "原始close", "原始volume")),
    ("股威力道來源（欄位對應依數值比對）", "goodway_sfChip960", ("TX-1", "TX-2"),
     ("大台近月大戶掛單", "大台近月散戶成交", "大台次月大戶掛單", "大台次月散戶成交", "原始volume")),
)
GOODWAY_HISTORY_SYMBOLS = tuple(
    f"{symbol}#{suffix}"
    for _, suffix, symbols, _ in GOODWAY_GROUPS
    for symbol in symbols
)
GOODWAY_FIELDS = ("open", "high", "low", "close", "volume")
DB_NAME = "mxf_futures"
TZ = ZoneInfo("Asia/Taipei")
CSV_PATH = Path(__file__).resolve().parent / "tv_doc" / "mxf_value.csv"
GOODWAY_OPENING_STATE_PATH = CSV_PATH.parent / "goodway_opening_alert_state.json"


def fetch_tradeinfo() -> object:
    response = requests.get(API_URL, timeout=20)
    response.raise_for_status()
    return response.json()


def fetch_goodway_history(symbol: str, now: datetime) -> dict[int, dict]:
    # Fetch a short window so nearby requests can be aligned if their latest
    # bars arrive at different times. API bar timestamps are milliseconds.
    response = requests.get(
        GOODWAY_HISTORY_URL,
        params={
            "symbol": symbol,
            "resolution": "1",
            "from": int(now.timestamp()) - 15 * 60,
            "to": int(now.timestamp()),
            "ws": "true",
            "metaData": "true",
            "agent": "daddy960",
            "product": "web_goodway",
        },
        timeout=(3, 8),
    )
    response.raise_for_status()
    payload = response.json()
    bars = payload.get("bars") if isinstance(payload, dict) else None
    if not isinstance(bars, list):
        raise ValueError(f"{symbol}: history 回應沒有 bars")
    return {
        bar["time"]: bar
        for bar in bars
        if isinstance(bar, dict)
        and isinstance(bar.get("time"), (int, float))
        and math.isfinite(bar["time"])
        and 0 <= bar["time"] <= now.timestamp() * 1000
    }


def build_goodway_components(histories: dict[str, dict[int, dict]]) -> dict:
    """Use the newest timestamp present in both contracts; never mix bars."""
    common_times = set(histories[GOODWAY_SYMBOLS[0]])
    for symbol in GOODWAY_SYMBOLS[1:]:
        common_times.intersection_update(histories[symbol])
    if not common_times:
        raise ValueError("小台近月與次月沒有共同分鐘資料，無法計算游擊隊合計")
    bar_time = max(common_times)

    def value(symbol: str, field: str) -> float:
        number = _to_float(histories[symbol][bar_time].get(field))
        if number is None or not math.isfinite(number):
            raise ValueError(f"{symbol}.{field}: 無有效數值")
        return number

    components = {}
    for label, near_symbol, next_symbol, field in (
        ("游擊隊", "MTX-1", "MTX-2", "open"),
    ):
        near = value(near_symbol, field)
        next_month = value(next_symbol, field)
        components[label] = {"near": near, "next": next_month, "total": near + next_month}
    return {"time": bar_time, "components": components}


def fetch_goodway_indicators(now: datetime) -> tuple[dict, dict]:
    histories = {}
    errors = {}
    # Limit concurrent requests and keep the other rows if one API fails.
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(fetch_goodway_history, symbol, now): symbol
            for symbol in GOODWAY_HISTORY_SYMBOLS
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                histories[symbol] = future.result()
            except Exception as exc:
                errors[symbol] = str(exc)
    return histories, errors


def _format_goodway_value(value: object) -> str:
    number = _to_float(value)
    if number is None or not math.isfinite(number):
        return "-"
    marker = " [>1500]" if number > GOODWAY_POSITIVE_THRESHOLD else ""
    return f"{number:g}{marker}"


def print_goodway_indicators(histories: dict, errors: dict, now: datetime) -> None:
    lines = [
        f"{now:%Y-%m-%d %H:%M:%S} 股威指標完整對照｜TX=大台、MTX=小台、-1=近月、-2=次月",
        "  每列採該 API 最新資料時間；[>1500] 表示正數大於 1500。",
    ]
    for group, suffix, symbols, labels in GOODWAY_GROUPS:
        lines.append(f"  【{group} #{suffix}】")
        for symbol in symbols:
            ticker = f"{symbol}#{suffix}"
            history = histories.get(ticker, {})
            if not history:
                lines.append(f"    {ticker}：無資料｜{errors.get(ticker, 'history 沒有有效 bars')}")
                continue
            bar = history[max(history)]
            bar_time = datetime.fromtimestamp(bar["time"] / 1000, TZ)
            age = max(0, int((now - bar_time).total_seconds()))
            freshness = f"｜資料落後 {age} 秒" if age > 120 else ""
            fields = []
            for field, label in zip(GOODWAY_FIELDS, labels):
                fields.append(f"{field}({label})={_format_goodway_value(bar.get(field))}")
            lines.append(
                f"    {ticker}｜資料時間 {bar_time:%Y-%m-%d %H:%M:%S}{freshness}｜"
                + "｜".join(fields)
            )

    try:
        snapshot = build_goodway_components({
            symbol: histories.get(f"{symbol}#trade_info", {}) for symbol in GOODWAY_SYMBOLS
        })
        values = snapshot["components"]["游擊隊"]
        bar_time = datetime.fromtimestamp(snapshot["time"] / 1000, TZ)
        lines.append(
            f"  游擊隊組成比對｜共同資料時間 {bar_time:%Y-%m-%d %H:%M:%S}"
            f"｜近月 {_format_goodway_value(values['near'])}"
            f" ＋ 次月 {_format_goodway_value(values['next'])}"
            f" ＝ 合計 {_format_goodway_value(values['total'])}"
        )
    except ValueError as exc:
        lines.append(f"  游擊隊組成比對：{exc}")

    print("\n".join(lines), flush=True)


class GoodwayGuerrillaAlerts:
    """Track high only, including revisions to the current minute's value."""

    def __init__(self) -> None:
        self.previous: dict[str, tuple[int, float]] = {}
        self.pending: deque[str] = deque()

    def observe(self, histories: dict) -> None:
        crossings = {}
        for ticker in GOODWAY_GUERRILLA_SYMBOLS:
            history = histories.get(ticker, {})
            samples = []
            for stamp in sorted(history):
                value = _to_float(history[stamp].get("high"))
                if value is not None and math.isfinite(value):
                    samples.append((stamp, value))
            if not samples:
                continue
            if ticker not in self.previous:
                # A first observation establishes a baseline, not a crossing.
                self.previous[ticker] = samples[-1]
                continue

            for stamp, value in samples:
                prev_stamp, prev_value = self.previous[ticker]
                if stamp < prev_stamp or (stamp == prev_stamp and value == prev_value):
                    continue
                for threshold in GOODWAY_GUERRILLA_THRESHOLDS:
                    direction = None
                    if prev_value <= threshold < value:
                        direction = f"向上突破 +{threshold}"
                    elif prev_value >= -threshold > value:
                        direction = f"向下跌破 -{threshold}"
                    if direction:
                        # The two prefixes can return one identical aggregate.
                        key = (stamp, direction, prev_value, value)
                        crossings.setdefault(key, []).append(ticker)
                self.previous[ticker] = (stamp, value)

        for (stamp, direction, prev_value, value), tickers in sorted(crossings.items()):
            bar_time = datetime.fromtimestamp(stamp / 1000, TZ)
            contracts = "、".join(
                f"{'近月' if ticker.startswith('TX-1#') else '次月'} {ticker}"
                for ticker in tickers
            )
            self.pending.append(
                f"📣 台指籌碼游擊隊 {direction}\n"
                f"{contracts}\n"
                f"資料時間 {bar_time:%Y-%m-%d %H:%M:%S}\n"
                f"游擊隊（high）：{prev_value:g} → {value:g}"
            )

    def flush(self) -> None:
        while self.pending:
            message = self.pending[0]
            try:
                send_discord_message(message, WEBHOOK_URL)
            except Exception as exc:
                # Request exceptions can contain the secret webhook URL.
                print(
                    f"❌ 游擊隊跨越通知失敗，下輪重試（{type(exc).__name__}）",
                    flush=True,
                )
                return
            self.pending.popleft()
            print(f"📣 已送出游擊隊跨越通知: {message}", flush=True)


def read_opening_price_bar(bar_time: datetime) -> tuple[float, float] | None:
    """Read the matching MXF 1m close and MA, never a different minute's row."""
    if not WEBHOOK_DATA_1MIN_PATH.exists():
        return None
    # Only recent rows are needed; do not load the growing price archive.
    with WEBHOOK_DATA_1MIN_PATH.open("rb") as handle:
        header = next(csv.reader([handle.readline().decode("utf-8-sig")]))
        data_start = handle.tell()
        handle.seek(0, os.SEEK_END)
        tail_start = max(data_start, handle.tell() - 65536)
        handle.seek(tail_start)
        if tail_start > data_start:
            handle.readline()  # Discard a partial first row.
        tail = handle.read().decode("utf-8")
    if tail and not tail.endswith("\n"):
        tail = tail.rpartition("\n")[0]  # Ignore a row still being appended.
    matched = None
    timestamp = bar_time.strftime("%Y-%m-%d %H:%M:%S")
    for row in csv.DictReader(io.StringIO(tail), fieldnames=header):
        if (
            row.get("TradingView Time") == timestamp
            and row.get("Symbol") == "MXF1!"
            and row.get("Timeframe") == "1"
        ):
            matched = row
    if matched is None:
        return None
    close, ma = _to_float(matched.get("Close")), _to_float(matched.get("MA_960"))
    if close is None or ma is None or not math.isfinite(close) or not math.isfinite(ma):
        return None
    return close, ma


class GoodwayOpeningAlerts:
    """Evaluate the completed opening bar once per date/session/prefix."""

    def __init__(self, state_path: Path | None = None) -> None:
        self.state_path = state_path
        self.processed: set[str] = set()
        self.pending: deque[str] = deque()
        self.dirty = False
        if state_path is not None and state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if (
                not isinstance(state, dict) or state.get("version") != 1
                or not isinstance(state.get("processed"), list)
                or not isinstance(state.get("pending"), list)
                or not all(isinstance(key, str) for key in state["processed"])
                or not all(isinstance(message, str) for message in state["pending"])
            ):
                raise ValueError("Invalid opening alert state")
            self.processed = set(state["processed"])
            self.pending = deque(state["pending"])

    def _save(self) -> None:
        if not self.dirty or self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.state_path.with_suffix(".json.tmp")
            temp_path.write_text(json.dumps({
                "version": 1,
                "processed": sorted(self.processed),
                "pending": list(self.pending),
            }, ensure_ascii=False), encoding="utf-8")
            temp_path.replace(self.state_path)
            self.dirty = False
        except OSError as exc:
            print(f"❌ 開盤通知狀態儲存失敗，下輪重試（{type(exc).__name__}）", flush=True)

    def observe(self, histories: dict, now: datetime) -> None:
        local_now = now.astimezone(TZ)
        if local_now.weekday() > 4:
            return
        for hour, minute, session in ((8, 45, "日盤"), (15, 0, "夜盤")):
            bar_time = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            age = (local_now - bar_time).total_seconds()
            # Query time must be after bar close, even if fetching spans the boundary.
            # Delayed data can be retried within the existing 15-minute history window.
            if not 60 <= age <= 900:
                continue
            stamp = int(bar_time.timestamp() * 1000)
            candidates = []
            for ticker in GOODWAY_GUERRILLA_SYMBOLS:
                key = f"{bar_time:%Y-%m-%d %H:%M}|{ticker}"
                if key in self.processed:
                    continue
                bar = histories.get(ticker, {}).get(stamp, {})
                value = _to_float(bar.get("high"))
                if value is not None and math.isfinite(value):
                    candidates.append((ticker, key, value))
            if not candidates:
                continue
            price = read_opening_price_bar(bar_time)
            if price is None:
                continue
            close, ma = price
            signals = {}
            for ticker, key, value in candidates:
                self.processed.add(key)
                self.dirty = True
                if close < ma and value > 0:
                    label = "960下游擊正｜開盤轉多"
                elif close > ma and value < 0:
                    label = "960上游擊負數｜開盤轉空"
                else:
                    continue
                signals.setdefault((label, value), []).append(ticker)
            for (label, value), tickers in signals.items():
                contracts = "、".join(
                    f"{'近月' if ticker.startswith('TX-1#') else '次月'} {ticker}"
                    for ticker in tickers
                )
                closed_at = bar_time + timedelta(minutes=1)
                self.pending.append(
                    f"📣 {label}\n"
                    f"{session}｜開盤 K 棒 {bar_time:%Y-%m-%d %H:%M}"
                    f"（{closed_at:%H:%M} 收棒）\n"
                    f"{contracts}\n"
                    f"收盤價 {close:g}｜MA_960 {ma:g}\n"
                    f"游擊隊（high）：{value:g}"
                )
        self._save()

    def flush(self) -> None:
        while self.pending:
            message = self.pending[0]
            try:
                send_discord_message(message, WEBHOOK_URL)
            except Exception as exc:
                print(f"❌ 游擊隊開盤通知失敗，下輪重試（{type(exc).__name__}）", flush=True)
                break
            self.pending.popleft()
            self.dirty = True
            self._save()
            print(f"📣 已送出游擊隊開盤通知: {message}", flush=True)
        self._save()


GOODWAY_GUERRILLA_ALERTS = GoodwayGuerrillaAlerts()
GOODWAY_OPENING_ALERTS = GoodwayOpeningAlerts(GOODWAY_OPENING_STATE_PATH)


def poll_goodway_indicators(now: datetime) -> None:
    try:
        histories, errors = fetch_goodway_indicators(now)
        print_goodway_indicators(histories, errors, datetime.now(TZ))
        GOODWAY_GUERRILLA_ALERTS.observe(histories)
        GOODWAY_OPENING_ALERTS.observe(histories, now)
    except Exception as exc:
        print(f"{now:%Y-%m-%d %H:%M:%S} ⚠️ 股威指標查詢失敗: {exc}", flush=True)
    GOODWAY_OPENING_ALERTS.flush()
    GOODWAY_GUERRILLA_ALERTS.flush()


def normalize_documents(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return [payload]

    return [{"value": payload}]


def insert_tradeinfo(payload: object, collection_name: str, now: datetime) -> None:
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    collection = db[collection_name]

    docs = normalize_documents(payload)
    if not docs:
        print("⚠️ 沒有資料可插入。")
        return
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    for doc in docs:
        doc.setdefault("time", timestamp)

    if len(docs) == 1:
        collection.insert_one(docs[0])
        fetch_time = now.strftime("%y-%m-%d %H-%M")
        doc = docs[0]
        print(
            f"{fetch_time} ✅ 成功插入 1 筆資料到集合 {collection_name}"
            f"｜游擊 {_format_display_int(doc.get('mtx_bvav'))}"
            f"｜坦克 {_format_display_int(doc.get('tx_bvav'))}"
            f"｜炮灰 {_format_display_int(doc.get('mtx_tbta'))}"
        )
    else:
        collection.insert_many(docs)
        print(f"✅ 成功插入 {len(docs)} 筆資料到集合 {collection_name}")


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _format_int(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(round(value)))


def _format_display_int(value: object) -> str:
    number = _to_float(value)
    if number is None:
        return "-"
    return _format_int(number)


def _get_signal(tx_bvav: float | None, mtx_bvav: float | None) -> str:
    if tx_bvav is None or mtx_bvav is None:
        return "none"
    if tx_bvav > 0 and mtx_bvav > 0:
        return "bull"
    if tx_bvav < 0 and mtx_bvav < 0:
        return "bear"
    return "none"


def _get_trend(mtx_bvav: float | None, mtx_bvav_avg: float | None) -> str:
    if mtx_bvav is None or mtx_bvav_avg is None:
        return "none"
    if mtx_bvav > mtx_bvav_avg:
        return "gold"
    if mtx_bvav < mtx_bvav_avg:
        return "death"
    return "none"


def _format_trend_label(trend: str) -> str:
    if trend == "gold":
        return "金叉"
    if trend == "death":
        return "死叉"
    return "無"


def _format_recommendation(signal: str) -> str:
    if signal == "bull":
        return "做多"
    if signal == "bear":
        return "做空"
    return "觀望"


def _read_mtx_bvav_history() -> list[float]:
    if not CSV_PATH.exists():
        return []

    values: list[float] = []
    with CSV_PATH.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            value = _to_float(row.get("mtx_bvav"))
            if value is not None:
                values.append(value)
    return values


def _calculate_mtx_bvav_avg(current_value: float | None) -> float | None:
    if current_value is None:
        return None

    history = _read_mtx_bvav_history()
    window = history[-(MTX_BVAV_AVG_WINDOW - 1):]
    window.append(current_value)
    if len(window) < MTX_BVAV_AVG_WINDOW:
        return None
    return sum(window) / len(window)


def _ensure_mxf_csv_header() -> None:
    if not CSV_PATH.exists():
        with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(MXF_CSV_HEADER)
        return

    try:
        with CSV_PATH.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            rows = list(reader)
    except Exception:
        return

    if not rows:
        with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(MXF_CSV_HEADER)
        return

    current_header = rows[0]
    if current_header == MXF_CSV_HEADER:
        return

    data_rows = rows[1:]
    normalized_rows: list[list[str]] = []
    for row in data_rows:
        record = {
            name: row[index] if index < len(row) else ""
            for index, name in enumerate(current_header)
        }
        tx_num = _to_float(record.get("tx_bvav"))
        mtx_num = _to_float(record.get("mtx_bvav"))
        avg_num = _to_float(record.get("mtx_bvav_avg"))
        signal = record.get("signal") or _get_signal(tx_num, mtx_num)
        trend = record.get("trend") or _get_trend(mtx_num, avg_num)
        normalized_rows.append([
            record.get("time", ""),
            record.get("tx_bvav", ""),
            record.get("mtx_bvav", ""),
            record.get("mtx_tbta", ""),
            record.get("mtx_bvav_avg", ""),
            signal,
            trend,
        ])

    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(MXF_CSV_HEADER)
        writer.writerows(normalized_rows)


def append_tradeinfo_csv(payload: object, now: datetime) -> dict[str, object] | None:
    docs = normalize_documents(payload)
    if not docs:
        return None

    doc = docs[0]
    tx_bvav = _to_float(doc.get("tx_bvav"))
    mtx_tbta = _to_float(doc.get("mtx_tbta"))
    mtx_bvav = _to_float(doc.get("mtx_bvav"))
    mtx_bvav_avg = _calculate_mtx_bvav_avg(mtx_bvav)
    signal = _get_signal(tx_bvav, mtx_bvav)
    trend = _get_trend(mtx_bvav, mtx_bvav_avg)

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ensure_mxf_csv_header()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    with CSV_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            timestamp,
            _format_int(tx_bvav),
            _format_int(mtx_bvav),
            _format_int(mtx_tbta),
            _format_int(mtx_bvav_avg),
            signal,
            trend,
        ])

    return {
        "time": timestamp,
        "tx_bvav": tx_bvav,
        "mtx_tbta": mtx_tbta,
        "mtx_bvav": mtx_bvav,
        "mtx_bvav_avg": mtx_bvav_avg,
        "signal": signal,
        "trend": trend,
    }


def build_mxf_value_discord_message(snapshot: dict[str, object], now: datetime) -> str:
    timestamp = str(snapshot.get("time") or now.strftime("%Y-%m-%d %H:%M:%S"))
    return " / ".join([
        f"日期: {timestamp}",
        f"外資: {_format_display_int(snapshot.get('tx_bvav'))}",
        f"散戶: {_format_display_int(snapshot.get('mtx_tbta'))}",
        f"游擊: {_format_display_int(snapshot.get('mtx_bvav'))}",
    ])


def send_discord_message(message: str, webhook_url: str | None = None) -> None:
    target_url = WEBHOOK_URL if webhook_url is None else webhook_url.strip()
    if not target_url:
        raise RuntimeError(
            f"Missing Discord webhook URL: {DISCORD_MXF_ALERT_WEBHOOK_ENV}"
            f" or {DISCORD_MXF_VALUE_WEBHOOK_ENV}"
        )

    response = requests.post(target_url, json={"content": message}, timeout=20)
    response.raise_for_status()


def read_latest_trade_side() -> str | None:
    if not H_TRADE_CSV_PATH.exists():
        return None

    latest_side: str | None = None
    with H_TRADE_CSV_PATH.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            side = (row.get("side") or "").strip().lower()
            if side in {"bear", "bull"}:
                latest_side = side
    return latest_side


def _read_latest_webhook_rows(limit: int = 2) -> list[dict]:
    if not WEBHOOK_DATA_1MIN_PATH.exists():
        return []

    try:
        with WEBHOOK_DATA_1MIN_PATH.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return []

    return rows[-limit:] if len(rows) >= limit else rows


def check_mtx_bvav_alert() -> None:
    global LAST_ALERT_STATE

    latest_side = read_latest_trade_side()
    latest_rows = _read_latest_webhook_rows(2)
    if len(latest_rows) < 2:
        LAST_ALERT_STATE = None
        return

    prev_row, curr_row = latest_rows[-2], latest_rows[-1]
    prev_close = _to_float(prev_row.get("Close"))
    curr_close = _to_float(curr_row.get("Close"))
    prev_ma_p200 = _to_float(prev_row.get("MA_P200"))
    curr_ma_p200 = _to_float(curr_row.get("MA_P200"))
    prev_ma_n200 = _to_float(prev_row.get("MA_N200"))
    curr_ma_n200 = _to_float(curr_row.get("MA_N200"))

    if latest_side == "bear":
        if (
            prev_close is not None
            and curr_close is not None
            and prev_ma_p200 is not None
            and curr_ma_p200 is not None
            and prev_close > prev_ma_p200
            and curr_close < curr_ma_p200
        ):
            alert_state = "short"
            message = (
                "shortCycle 空單加碼訊號："
                f"close 由 {prev_close} 跌破 MA_P200 {curr_ma_p200}"
            )
        else:
            LAST_ALERT_STATE = None
            return
    elif latest_side == "bull":
        if (
            prev_close is not None
            and curr_close is not None
            and prev_ma_n200 is not None
            and curr_ma_n200 is not None
            and prev_close < prev_ma_n200
            and curr_close > curr_ma_n200
        ):
            alert_state = "long"
            message = (
                "shortCycle 多單加碼訊號："
                f"close 由 {prev_close} 穿越 MA_N200 {curr_ma_n200}"
            )
        else:
            LAST_ALERT_STATE = None
            return
    else:
        LAST_ALERT_STATE = None
        return

    if LAST_ALERT_STATE == alert_state:
        return

    try:
        send_discord_message(message)
        LAST_ALERT_STATE = alert_state
        print(f"📣 已送出 Discord 通知: {message}")
    except Exception as exc:
        print(f"❌ 發送 Discord 通知失敗: {exc}")


def check_service_alive_alert(now: datetime) -> None:
    global LAST_ALIVE_SENT_SLOT

    if now.minute not in {0, 30}:
        return

    half_hour = 0 if now.minute == 0 else 1
    slot = (now.strftime("%Y-%m-%d"), now.hour, half_hour)
    if LAST_ALIVE_SENT_SLOT == slot:
        return

    minute_label = "00" if half_hour == 0 else "30"
    message = f"服務還啟動著 - {now.strftime('%Y-%m-%d %H')}:{minute_label}"
    try:
        send_discord_message(message)
        LAST_ALIVE_SENT_SLOT = slot
        print(f"📣 已送出服務存活通知: {message}")
    except Exception as exc:
        print(f"❌ 發送服務存活通知失敗: {exc}")


def get_collection_name(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def is_market_open(now: datetime) -> bool:
    weekday = now.weekday()  # Mon=0 ... Sun=6
    current_time = now.time()

    # 日盤：週一到週五 08:45~13:45
    day_session = weekday <= 4 and dt_time(8, 45) <= current_time <= dt_time(13, 45)

    # 夜盤：週一到週五 15:00~23:59:59
    night_session = weekday <= 4 and dt_time(15, 0) <= current_time <= dt_time(23, 59, 59)

    # 凌晨盤：週一到週六 00:00~05:00
    early_session = weekday <= 5 and dt_time(0, 0) <= current_time <= dt_time(5, 0)

    return day_session or night_session or early_session


def sleep_until_next_minute() -> None:
    now = datetime.now(TZ)
    sleep_seconds = 60 - now.second
    if sleep_seconds <= 0:
        sleep_seconds = 60
    time.sleep(sleep_seconds)


def main() -> None:
    while True:
        now = datetime.now(TZ)
        try:
            # check_mtx_bvav_alert()
            check_service_alive_alert(now)
        except Exception as exc:
            print(f"❌ 檢查通知狀態失敗: {exc}")

        if is_market_open(now):
            try:
                payload = fetch_tradeinfo()
                collection_name = get_collection_name(now)
                insert_tradeinfo(payload, collection_name, now)
                append_tradeinfo_csv(payload, now)
                check_service_alive_alert(now)
            except Exception as exc:
                print(f"❌ 打 API 或寫入失敗: {exc}")
            poll_goodway_indicators(datetime.now(TZ))
        sleep_until_next_minute()


if __name__ == "__main__":
    print('=== 坦克、炮灰、游擊手資料監控 ===')
    print(
        "游擊隊跨越通知｜TX-1/TX-2#goodway_chip960.high"
        "｜突破 +1000/+1500 或跌破 -1000/-1500"
        f"｜Discord {'已設定' if WEBHOOK_URL else '未設定'}｜沿用每分鐘輪詢",
        flush=True,
    )
    print(
        "游擊隊開盤通知｜08:45/15:00 一分鐘棒收完｜收盤價 < MA_960 且游擊 > 0 轉多"
        "｜收盤價 > MA_960 且游擊 < 0 轉空｜相同 Discord",
        flush=True,
    )
    main()
