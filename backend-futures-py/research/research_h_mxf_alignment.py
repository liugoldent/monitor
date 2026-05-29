from __future__ import annotations

import bisect
import csv
from datetime import datetime
from pathlib import Path
from statistics import mean, median


BASE_DIR = Path(__file__).resolve().parents[1]
TV_DOC_DIR = BASE_DIR / "tv_doc"
H_TRADE_PATH = TV_DOC_DIR / "h_trade.csv"
MXF_VALUE_PATH = TV_DOC_DIR / "mxf_value.csv"


def parse_dt(value: object) -> datetime:
    return datetime.strptime(str(value).strip(), "%Y-%m-%d %H:%M:%S")


def parse_float(value: object) -> float | None:
    try:
        text = str(value).replace(",", "").strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def support_value(side: str, value: float | None) -> float | None:
    if value is None:
        return None
    return value if side == "bull" else -value


def session_name(value: datetime) -> str:
    minute = value.hour * 60 + value.minute
    if 8 * 60 + 45 <= minute <= 13 * 60 + 45:
        return "morning"
    if minute >= 15 * 60 or minute < 5 * 60:
        return "night"
    return "other"


def load_mxf() -> tuple[list[datetime], list[dict]]:
    rows = []
    with MXF_VALUE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                row_time = parse_dt(row["time"])
            except Exception:
                continue
            rows.append(
                {
                    "time": row_time,
                    "tx": parse_float(row.get("tx_bvav")),
                    "mtx": parse_float(row.get("mtx_bvav")),
                    "tbta": parse_float(row.get("mtx_tbta")),
                    "avg": parse_float(row.get("mtx_bvav_avg")),
                    "signal": str(row.get("signal") or "").strip().lower(),
                    "trend": str(row.get("trend") or "").strip().lower(),
                }
            )
    rows.sort(key=lambda item: item["time"])
    return [row["time"] for row in rows], rows


def load_h_intervals(mxf_times: list[datetime], mxf_rows: list[dict]) -> list[dict]:
    def mxf_at(value: datetime) -> dict | None:
        index = bisect.bisect_right(mxf_times, value) - 1
        return mxf_rows[index] if index >= 0 else None

    intervals = []
    current = None
    with H_TRADE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                timestamp = parse_dt(row["timestamp"])
            except Exception:
                continue
            action = str(row.get("action") or "").strip()
            side = str(row.get("side") or "").strip().lower()
            price = parse_float(row.get("price"))
            if action == "enter" and side in {"bull", "bear"} and price is not None:
                current = {"entry_time": timestamp, "side": side, "entry_price": price}
                continue

            if action != "exiting" or current is None:
                continue

            pnl = parse_float(row.get("pnl"))
            entry_mxf = mxf_at(current["entry_time"])
            if pnl is None or entry_mxf is None:
                current = None
                continue

            interval = {
                **current,
                "exit_time": timestamp,
                "points": pnl / 10,
                "session": session_name(current["entry_time"]),
                "mxf": entry_mxf,
            }
            for key in ("tx", "mtx", "tbta", "avg"):
                interval[f"{key}_support"] = support_value(side, entry_mxf.get(key))
            interval["signal_support"] = (
                (side == "bull" and entry_mxf["signal"] == "bull")
                or (side == "bear" and entry_mxf["signal"] == "bear")
            )
            interval["trend_support"] = (
                (side == "bull" and entry_mxf["trend"] == "gold")
                or (side == "bear" and entry_mxf["trend"] == "death")
            )
            intervals.append(interval)
            current = None
    return intervals


def summarize(rows: list[dict]) -> dict[str, float | int]:
    points = [float(row["points"]) for row in rows]
    wins = [point for point in points if point > 0]
    losses = [point for point in points if point <= 0]
    return {
        "trades": len(points),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(points) * 100, 1) if points else 0.0,
        "points": round(sum(points), 1),
        "avg": round(mean(points), 1) if points else 0.0,
        "median": round(median(points), 1) if points else 0.0,
        "avg_win": round(mean(wins), 1) if wins else 0.0,
        "avg_loss": round(mean(losses), 1) if losses else 0.0,
        "worst": round(min(points), 1) if points else 0.0,
        "best": round(max(points), 1) if points else 0.0,
    }


def print_summary(name: str, rows: list[dict]) -> None:
    if not rows:
        return
    item = summarize(rows)
    print(
        f"{name:45s} n={item['trades']:3d} win={item['win_rate']:5.1f}% "
        f"pts={item['points']:8.1f} avg={item['avg']:7.1f} med={item['median']:7.1f} "
        f"avgW={item['avg_win']:7.1f} avgL={item['avg_loss']:7.1f} worst={item['worst']:8.1f}"
    )


def main() -> None:
    mxf_times, mxf_rows = load_mxf()
    intervals = load_h_intervals(mxf_times, mxf_rows)
    print(
        f"completed_h_with_mxf={len(intervals)} "
        f"from={intervals[0]['entry_time'] if intervals else ''} "
        f"to={intervals[-1]['exit_time'] if intervals else ''}"
    )
    print_summary("baseline H", intervals)

    print("\nEntry support basics")
    for key in ("mtx", "avg", "tx", "tbta"):
        print_summary(
            f"{key} supports H",
            [row for row in intervals if row[f"{key}_support"] is not None and row[f"{key}_support"] > 0],
        )
        print_summary(
            f"{key} against H",
            [row for row in intervals if row[f"{key}_support"] is not None and row[f"{key}_support"] < 0],
        )

    print_summary("signal supports H", [row for row in intervals if row["signal_support"]])
    print_summary("trend supports H", [row for row in intervals if row["trend_support"]])
    print_summary(
        "signal+trend supports H",
        [row for row in intervals if row["signal_support"] and row["trend_support"]],
    )

    print("\nDirection/session splits")
    for side in ("bull", "bear"):
        print_summary(side, [row for row in intervals if row["side"] == side])
        print_summary(
            f"{side} avg supports",
            [row for row in intervals if row["side"] == side and row["avg_support"] is not None and row["avg_support"] > 0],
        )
        print_summary(
            f"{side} avg+mtx supports",
            [
                row
                for row in intervals
                if row["side"] == side
                and row["avg_support"] is not None
                and row["mtx_support"] is not None
                and row["avg_support"] > 0
                and row["mtx_support"] > 0
            ],
        )
    for session in ("morning", "night", "other"):
        print_summary(f"{session} avg supports", [row for row in intervals if row["session"] == session and row["avg_support"] is not None and row["avg_support"] > 0])
        print_summary(f"{session} avg against", [row for row in intervals if row["session"] == session and row["avg_support"] is not None and row["avg_support"] < 0])

    print("\nTop filters min n>=6 sorted by win rate then points")
    candidates = []
    for side_option in (None, "bull", "bear"):
        for session_option in (None, "morning", "night", "other"):
            for avg_threshold in (0, 100, 200, 300, 500, 800, 1000):
                for mtx_threshold in (None, 0, 200, 500, 800):
                    for require_signal in (False, True):
                        rows = []
                        for row in intervals:
                            if side_option and row["side"] != side_option:
                                continue
                            if session_option and row["session"] != session_option:
                                continue
                            if row["avg_support"] is None or row["avg_support"] < avg_threshold:
                                continue
                            if mtx_threshold is not None and (
                                row["mtx_support"] is None or row["mtx_support"] < mtx_threshold
                            ):
                                continue
                            if require_signal and not row["signal_support"]:
                                continue
                            rows.append(row)
                        if len(rows) < 6:
                            continue
                        item = summarize(rows)
                        candidates.append(
                            {
                                **item,
                                "side": side_option or "*",
                                "session": session_option or "*",
                                "avg_threshold": avg_threshold,
                                "mtx_threshold": mtx_threshold,
                                "require_signal": require_signal,
                            }
                        )

    candidates.sort(key=lambda item: (-float(item["win_rate"]), -float(item["points"])))
    for item in candidates[:25]:
        print(
            f"side={item['side']:5s} session={item['session']:7s} "
            f"avgSup>={item['avg_threshold']:4} mtxSup>={str(item['mtx_threshold']):4s} "
            f"signal={str(item['require_signal']):5s} n={item['trades']:2d} "
            f"win={item['win_rate']:5.1f}% pts={item['points']:8.1f} "
            f"avg={item['avg']:7.1f} worst={item['worst']:8.1f}"
        )

    print("\nWorst H losses with entry mxf")
    for row in sorted(intervals, key=lambda item: float(item["points"]))[:10]:
        print(
            f"{row['entry_time']} {row['side']:4s} points={row['points']:8.1f} "
            f"session={row['session']:7s} mtxSup={row['mtx_support']} "
            f"avgSup={row['avg_support']} txSup={row['tx_support']} "
            f"signal={row['mxf']['signal']} trend={row['mxf']['trend']}"
        )


if __name__ == "__main__":
    main()
