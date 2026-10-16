"""Read-only H3 entry observations; never imports or calls an account executor.

An immutable export checkpoint plus the trusted receipt journal reconstructs
the one-contract CLOSED curve on every notice, including after a restart.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import csv
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from h_signal import parse_h_event_direction


ENTRY = Decimal("0.025")
RECOVERY = Decimal("0.0125")
RULE_VERSION = 2
COST = Decimal("2.4")  # points per side, independent of an account's point value
MAX_PRICE_AGE = timedelta(seconds=180)
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class WatchError(ValueError):
    pass


def number(value, *, positive=False):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or (positive and result <= 0):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise WatchError("價格或損益資料無效") from None


class Curve:
    def __init__(self, state):
        self.equity = number(state["equity_points"])
        self.peak = number(state["peak_points"])
        self.price = number(state["entry_price"], positive=True)
        self.side = state["side"]
        self.armed = state["armed"]
        self.phase = state["phase"]
        self.closed = state["closed_trades"]
        self.high_count = state["high_count"]
        self.recovered = state["recovered"]
        self.entry_ready = state["entry_ready"]
        self.qualifies = state["qualifies"]
        if (self.peak < max(Decimal(0), self.equity)
                or type(self.side) is not int or self.side not in (-1, 1)
                or type(self.armed) is not bool
                or type(self.closed) is not int or self.closed < 0
                or self.phase not in ("waiting_drawdown", "waiting_recovery", "entry_window")
                or type(self.high_count) is not int or self.high_count < 0
                or any(type(v) is not bool for v in (self.recovered, self.entry_ready, self.qualifies))
                or (self.recovered and not self.armed)):
            raise WatchError("歷史基準狀態不完整")

    def checkpoint(self):
        return dict(equity_points=str(self.equity), peak_points=str(self.peak),
                    entry_price=str(self.price), side=self.side, armed=self.armed,
                    phase=self.phase, closed_trades=self.closed, high_count=self.high_count,
                    recovered=self.recovered, entry_ready=self.entry_ready, qualifies=self.qualifies)

    def check(self, index):
        ratio = (self.peak - self.equity) / number(index, positive=True)
        self.qualifies = False
        if ratio == 0:
            self.qualifies = self.armed and not self.recovered
            self.entry_ready = self.entry_ready or self.qualifies
            self.high_count += 1
            self.armed = self.recovered = False
        else:
            if ratio >= ENTRY:
                self.armed = True
            if self.armed and ratio <= RECOVERY:
                self.qualifies = not self.recovered
                self.recovered = True
            # A missed alert may be used only while the current DD is still low.
            self.entry_ready = (self.entry_ready or self.recovered) and ratio <= RECOVERY
        if self.entry_ready:
            self.phase = "entry_window"
        elif self.armed:
            self.phase = "waiting_recovery"
        else:
            self.phase = "waiting_drawdown"

    def close(self, side, price):
        if self.side != side:
            raise WatchError("歷史交易方向接續不符")
        self.equity += (number(price, positive=True) - self.price) * side - COST * 2
        self.peak = max(self.peak, self.equity)
        self.closed += 1
        self.side = 0

    def enter(self, side, price):
        if self.side or side not in (-1, 1):
            raise WatchError("歷史交易重疊或方向無效")
        self.side, self.price = side, number(price, positive=True)
        self.check(self.price)


def seed_from_export(source: Path):
    """Recompute matched fills; Entry rows' future P&L is NEVER consumed."""
    data = source.read_bytes()
    curve = Curve(dict(equity_points="0", peak_points="0", entry_price="1", side=1,
                       armed=False, phase="waiting_drawdown", closed_trades=0,
                       high_count=0, recovered=False, entry_ready=False, qualifies=False))
    curve.side = 0
    first = last = None
    pending = None
    seen = set()
    reported = Decimal(0)
    for row in csv.DictReader(data.decode("utf-8-sig").splitlines()):
        kind = row.get("類型", "")
        if not kind:
            continue  # exporter footer (1900/01/00), not a trade
        if kind not in ("Entry Long", "Entry Short", "Exit Long", "Exit Short"):
            raise WatchError("歷史匯出含未知交易類型")
        stamp = datetime.strptime(row["日期"] + " " + row["時間"], "%Y/%m/%d %H:%M:%S")
        if last is not None and stamp < last:
            raise WatchError("歷史交易時間順序錯誤")
        if number(row["數量"]) != 1:
            raise WatchError("歷史基準必須是固定一口")
        price = number(row["價格"], positive=True)
        side = 1 if kind.endswith("Long") else -1
        identity = (row["No"], kind.startswith("Entry"))
        if identity in seen:
            raise WatchError("歷史交易重複")
        seen.add(identity)
        first, last = first or stamp, stamp
        if identity[1]:
            curve.enter(side, price)
            pending = row["No"]
        else:
            if pending != row["No"]:
                raise WatchError("歷史進出場無法配對")
            gross = (price - curve.price) * side
            if gross * 10 != number(row["獲利($)"]):
                raise WatchError("歷史匯出點值或獲利不符")
            reported += gross
            curve.close(side, price)
            pending = None
    if not last or not curve.side:
        raise WatchError("歷史基準缺少最後一筆持倉")
    if curve.equity != reported - COST * 2 * curve.closed:
        raise WatchError("歷史已平倉損益對帳失敗")
    return dict(version=RULE_VERSION, entry_threshold=str(ENTRY), recovery_threshold=str(RECOVERY), source_name=source.name,
                source_sha256=hashlib.sha256(data).hexdigest(),
                first_at=first.strftime(TIME_FORMAT), last_at=last.strftime(TIME_FORMAT),
                cost_points_one_way=str(COST), curve=curve.checkpoint())


def _quotes(path, cutoff, since):
    result = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("Symbol") != "MXF1!" or row.get("Timeframe") != "1":
                continue
            if not since <= row.get("Record Time", "") <= cutoff.strftime(TIME_FORMAT):
                continue
            try:
                recorded = datetime.strptime(row["Record Time"], TIME_FORMAT)
                bar = datetime.strptime(row["TradingView Time"], TIME_FORMAT)
                price = number(row["Close"], positive=True)
            except (KeyError, ValueError):
                continue
            if bar <= recorded <= cutoff:
                result.append((recorded, bar, price))
    result.sort(key=lambda value: value[0])
    return result


def _quote_at(quotes, times, stamp):
    index = bisect_right(times, stamp) - 1
    if index < 0:
        raise WatchError("缺少訊號當時可用行情")
    recorded, bar, price = quotes[index]
    if stamp - recorded > MAX_PRICE_AGE or stamp - bar > MAX_PRICE_AGE:
        raise WatchError("訊號當時行情已過期，暫不判定")
    return price, bar


def build_notice(seed_path: Path, event_path: Path, price_path: Path,
                 received_at: datetime, event_key: str, target: int,
                 *, new_transition: bool = True, snapshot_only: bool = False):
    """Rebuild from durable inputs. Errors never turn into a positive entry hint."""
    try:
        if received_at.tzinfo is None:
            raise WatchError("訊號缺少接收時區")
        cutoff = received_at.astimezone(ZoneInfo("Asia/Taipei")).replace(tzinfo=None)
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        if (seed["version"] != RULE_VERSION or number(seed["cost_points_one_way"]) != COST
                or number(seed["entry_threshold"]) != ENTRY
                or number(seed["recovery_threshold"]) != RECOVERY):
            raise WatchError("歷史基準版本或成本不符")
        last = datetime.strptime(seed["last_at"], TIME_FORMAT)
        if last > cutoff:
            raise WatchError("歷史基準晚於本次訊號")
        curve = Curve(seed["curve"])
        quotes = _quotes(price_path, cutoff, (last - MAX_PRICE_AGE).strftime(TIME_FORMAT))
        times = [q[0] for q in quotes]
        current_price, bar = _quote_at(quotes, times, cutoff)
        seen = set()
        last_transition_key = None
        last_transition_at = seed["last_at"]
        with event_path.open(encoding="utf-8-sig") as handle:
            for line in handle:
                row = json.loads(line)
                direction = parse_h_event_direction(row)
                if direction is None:
                    continue
                stamp = datetime.fromisoformat(row["received_at"])
                if stamp.tzinfo is None:
                    raise WatchError("訊號缺少接收時區")
                stamp = stamp.astimezone(ZoneInfo("Asia/Taipei")).replace(tzinfo=None)
                if stamp <= datetime.strptime(seed["last_at"], TIME_FORMAT) or stamp > cutoff:
                    continue
                if row.get("chat_id") is None or row.get("message_id") is None:
                    raise WatchError("訊號紀錄缺少識別碼")
                key = f"{row['chat_id']}:{row['message_id']}"
                if key in seen:
                    continue
                seen.add(key)
                if stamp < last:
                    raise WatchError("訊號時間順序錯誤")
                last = stamp
                if curve.side == direction:
                    continue  # seed overlap or repeated receipt, never a new trade
                price, _ = _quote_at(quotes, times, stamp)
                curve.close(curve.side, price)
                curve.enter(direction, price)
                last_transition_key = key
                last_transition_at = stamp.strftime(TIME_FORMAT)
        if target != curve.side:
            raise WatchError("本次 H3 訊號未完整入帳")
        is_new = new_transition and last_transition_key == event_key
        dd = curve.peak - curve.equity
        ratio = dd / current_price
        if snapshot_only:
            return dict(rule_version=RULE_VERSION, event_key=event_key, is_new=bool(is_new),
                        side=curve.side, dd_points=str(dd), ratio=str(ratio), index=str(current_price),
                        high_count=curve.high_count, qualifies=bool(is_new and curve.qualifies),
                        eligible=bool(is_new and curve.entry_ready and ratio <= RECOVERY),
                        equity_points=str(curve.equity), peak_points=str(curve.peak),
                        source_sha256=seed["source_sha256"], last_transition_at=last_transition_at)
        phase = {
            "waiting_drawdown": "等待回撤達 2.5%",
            "waiting_recovery": "已達過 2.5%，等待修復至 1.25% 以下",
            "entry_window": "第2口加碼觀察條件仍成立；第1口持續跟隨新 H3 訊號",
        }[curve.phase]
        if is_new and curve.qualifies:
            phase = "🔔 回撤曾達 2.5%，本次已修復至 ≤1.25%；第2口加碼觀察條件成立"
        if not is_new:
            if curve.phase == "entry_window":
                phase = "先前已達修復窗口，等待新訊號重新判定"
            phase += "（同向或重複訊號，未產生新的進場提示）"
        return (
            "【H3 單口回撤觀察】\n"
            f"目前回撤：{dd:,.2f} 點 ÷ {current_price:,.0f} = {ratio:.2%}\n"
            f"狀態：{phase}\n"
            f"單口已平倉：{curve.equity:,.2f} 點｜前高：{curve.peak:,.2f} 點\n"
            f"行情：{bar:%m/%d %H:%M}；最後換向：{last_transition_at}\n"
            "規則：第1口從新H3持續跑、不等回撤；先回前高，後續新一輪2.5%→≤1.25%才2口；歸零回1口。\n"
            "影子估算／單邊成本2.4點／不含浮盈虧；非安全進場保證。"
        )
    except (OSError, ValueError, KeyError, TypeError, csv.Error):
        if snapshot_only:
            raise WatchError("歷史基準或訊號行情不完整，禁止以缺失資料判定進場") from None
        return "【H3 單口回撤觀察】資料待核對：歷史或訊號行情不完整，暫不判定進場條件。"


def build_snapshot(seed_path, event_path, price_path, received_at, event_key, target):
    return build_notice(seed_path, event_path, price_path, received_at, event_key, target,
                        snapshot_only=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("基準已存在；請先核對，避免覆蓋既有基準。")
    seed = seed_from_export(args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(seed, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
