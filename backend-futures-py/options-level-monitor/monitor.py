from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from core import AnalysisResult, MarketSnapshot, analyze_snapshot, make_snapshot, snapshot_to_dict


OPTIONS_URL = "https://tw.stock.yahoo.com/future/options.html"
FUTURES_URL = "https://tw.stock.yahoo.com/future/futures.html"
RUNTIME_DIR = Path(__file__).resolve().parent / "runtime"
SNAPSHOT_PATH = RUNTIME_DIR / "options_level_snapshots.jsonl"


class YahooMarketClient:
    def __init__(self, timeout: float = 20.0, attempts: int = 3) -> None:
        self.timeout = timeout
        self.attempts = attempts
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36"
                ),
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
                "Cache-Control": "no-cache",
            }
        )

    def _get(self, url: str, params: dict[str, object]) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                response = self.session.get(
                    url,
                    params={**params, "_ts": int(time.time() * 1000)},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                if len(response.text) < 10_000:
                    raise ValueError(f"response is unexpectedly short: {len(response.text)} bytes")
                return response.text
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < self.attempts:
                    time.sleep(float(attempt))
        raise RuntimeError(f"Yahoo request failed after {self.attempts} attempts: {last_error}")

    def fetch_snapshot(self) -> MarketSnapshot:
        futures_html = self._get(FUTURES_URL, {"fumr": "futurefull"})
        options_html = self._get(
            OPTIONS_URL,
            {"opmr": "optionfull", "opcm": "WTXO", "opym": 1},
        )
        return make_snapshot(futures_html, options_html)


def _format_price(value: float | None) -> str:
    return "-" if value is None else f"{value:,.1f}"


def _format_level(level, prefix: str, has_history: bool) -> str:
    flow_label = "本分鐘量" if has_history else "累積量"
    return (
        f"  {prefix} 台指期約 {level.futures_level:,.0f}  "
        f"（履約價 {level.strike:,.0f}）  分數 {level.score:5.1f}  "
        f"OI {level.open_interest:,}  {flow_label} {level.volume_change:,}  "
        f"距現價 {level.distance:,.0f}點"
    )


def print_analysis(snapshot: MarketSnapshot, result: AnalysisResult, has_history: bool) -> None:
    future = snapshot.futures
    print("=" * 88)
    print(
        f"[{snapshot.captured_at}] Yahoo交易日 {future.trade_date or '-'}  "
        f"台指期近一 {future.last:,.0f}  買/賣 {future.bid:,.0f}/{future.ask:,.0f}  "
        f"報價時間 {future.quote_time}"
    )
    score = "-" if result.direction_score is None else f"{result.direction_score:+.0f}"
    print(
        f"方向：{result.direction_label}（分數 {score}）  "
        f"選擇權隱含中心：{_format_price(result.option_center)}  "
        f"ATM：{_format_price(result.atm_strike)}"
    )
    if result.option_center is not None:
        print(f"期貨換算基差：{future.last - result.option_center:+,.1f}點（候選履約價已換算成台指期價位）")
    if result.expected_move is not None:
        print(
            f"近到期跨式：約 ±{result.expected_move:,.0f}點  "
            f"粗略到期區間 {_format_price(result.expected_low)} ～ {_format_price(result.expected_high)}"
        )
    if future.high is not None or future.low is not None:
        print(f"日內高/低：{_format_price(future.high)} / {_format_price(future.low)}")

    print("\n潛在支撐（Put OI＋成交量＋距離權重）：")
    if result.supports:
        for index, level in enumerate(result.supports, 1):
            print(_format_level(level, f"S{index}", has_history))
    else:
        print("  暫無足夠有效報價")

    print("潛在壓力（Call OI＋成交量＋距離權重）：")
    if result.resistances:
        for index, level in enumerate(result.resistances, 1):
            print(_format_level(level, f"R{index}", has_history))
    else:
        print("  暫無足夠有效報價")

    if not has_history:
        print("\n提示：第一輪只有靜態價位；第二輪起才會加入每分鐘權利金與成交量變化。")
    print("注意：這是候選反應區，不是保證反轉；OI 是整體市場部位，不能識別即時法人方向。")
    sys.stdout.flush()


def save_snapshot(snapshot: MarketSnapshot) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with SNAPSHOT_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(snapshot_to_dict(snapshot), ensure_ascii=False, separators=(",", ":")))
        stream.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="每分鐘估算台指期選擇權潛在支撐與壓力")
    parser.add_argument("--interval", type=float, default=60.0, help="抓取間隔秒數，預設60")
    parser.add_argument("--once", action="store_true", help="只抓取並輸出一次")
    parser.add_argument("--no-save", action="store_true", help="不保存 runtime JSONL 快照")
    parser.add_argument("--timeout", type=float, default=20.0, help="單次HTTP逾時秒數")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.interval < 5:
        raise SystemExit("--interval 不可小於5秒")
    client = YahooMarketClient(timeout=args.timeout)
    previous: MarketSnapshot | None = None
    print("台指期選擇權支撐壓力監控已啟動；按 Ctrl+C 停止。")
    print(f"抓取間隔：{args.interval:g}秒；來源：Yahoo 股市公開期權頁面")
    sys.stdout.flush()

    while True:
        started = time.monotonic()
        try:
            snapshot = client.fetch_snapshot()
            result = analyze_snapshot(snapshot, previous)
            print_analysis(snapshot, result, previous is not None)
            if not args.no_save:
                save_snapshot(snapshot)
            previous = snapshot
        except Exception as exc:  # Keep a long-running monitor alive across transient site failures.
            print(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] 抓取失敗：{exc}", file=sys.stderr)
            sys.stderr.flush()
            if args.once:
                return 1

        if args.once:
            return 0
        elapsed = time.monotonic() - started
        try:
            time.sleep(max(1.0, args.interval - elapsed))
        except KeyboardInterrupt:
            print("\n監控已停止。")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
