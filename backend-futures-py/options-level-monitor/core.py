from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from html.parser import HTMLParser
from statistics import median
from typing import Iterable, Optional


@dataclass(frozen=True)
class OptionQuote:
    strike: float
    call_bid: Optional[float]
    call_ask: Optional[float]
    call_last: Optional[float]
    call_change: Optional[float]
    call_oi: int
    call_volume: int
    call_time: str
    put_bid: Optional[float]
    put_ask: Optional[float]
    put_last: Optional[float]
    put_change: Optional[float]
    put_oi: int
    put_volume: int
    put_time: str

    @property
    def call_mid(self) -> Optional[float]:
        return quote_mid(self.call_bid, self.call_ask)

    @property
    def put_mid(self) -> Optional[float]:
        return quote_mid(self.put_bid, self.put_ask)


@dataclass(frozen=True)
class FuturesQuote:
    bid: float
    ask: float
    last: float
    change: Optional[float]
    change_percent: Optional[float]
    volume: int
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    basis: Optional[float]
    reference: Optional[float]
    open_interest: int
    quote_time: str
    trade_date: str


@dataclass(frozen=True)
class MarketSnapshot:
    captured_at: str
    futures: FuturesQuote
    options: tuple[OptionQuote, ...]
    option_trade_date: str
    option_symbol: str


@dataclass(frozen=True)
class LevelCandidate:
    strike: float
    futures_level: float
    score: float
    open_interest: int
    volume_change: int
    distance: float
    kind: str


@dataclass(frozen=True)
class AnalysisResult:
    option_center: Optional[float]
    atm_strike: Optional[float]
    expected_move: Optional[float]
    expected_low: Optional[float]
    expected_high: Optional[float]
    direction_score: Optional[float]
    direction_label: str
    supports: tuple[LevelCandidate, ...]
    resistances: tuple[LevelCandidate, ...]


class _YahooTableParser(HTMLParser):
    """Extract Yahoo's server-rendered rows without browser automation."""

    def __init__(self, target_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.target_id = target_id
        self.target_depth: Optional[int] = None
        self.row_depth: Optional[int] = None
        self.row_text: list[str] = []
        self.row_hrefs: list[str] = []
        self.rows: list[tuple[list[str], list[str]]] = []
        self.trade_date = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr = dict(attrs)
        if tag == "div":
            if self.target_depth is None and attr.get("id") == self.target_id:
                self.target_depth = 1
            elif self.target_depth is not None:
                self.target_depth += 1

            classes = set((attr.get("class") or "").split())
            if self.target_depth is not None and self.row_depth is None and "table-row" in classes:
                self.row_depth = self.target_depth
                self.row_text = []
                self.row_hrefs = []

        if self.target_depth is not None and tag == "time" and not self.trade_date:
            self.trade_date = attr.get("datatime") or ""
        if self.row_depth is not None and tag == "a" and attr.get("href"):
            self.row_hrefs.append(attr["href"] or "")

    def handle_data(self, data: str) -> None:
        if self.row_depth is None:
            return
        text = " ".join(data.split())
        if text:
            self.row_text.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag != "div" or self.target_depth is None:
            return
        if self.row_depth == self.target_depth:
            self.rows.append((self.row_text, self.row_hrefs))
            self.row_depth = None
            self.row_text = []
            self.row_hrefs = []
        self.target_depth -= 1
        if self.target_depth == 0:
            self.target_depth = None


def _number(value: str) -> Optional[float]:
    cleaned = value.replace(",", "").replace("%", "").strip()
    if not cleaned or cleaned == "-":
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else None


def _integer(value: str) -> int:
    parsed = _number(value)
    return int(parsed) if parsed is not None else 0


def quote_mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    midpoint = (bid + ask) / 2.0
    spread = ask - bid
    if spread > max(30.0, midpoint * 0.50):
        return None
    return midpoint


def parse_options_html(html: str) -> tuple[tuple[OptionQuote, ...], str, str]:
    parser = _YahooTableParser("main-1-OptionsPriceTable-Proxy")
    parser.feed(html)
    quotes: list[OptionQuote] = []
    option_symbol = ""

    for texts, hrefs in parser.rows:
        call_href = next((href for href in hrefs if re.search(r";\d+C(?:$|[?#])", href)), "")
        put_href = next((href for href in hrefs if re.search(r";\d+P(?:$|[?#])", href)), "")
        if not call_href or not put_href or len(texts) != 15:
            continue
        call_match = re.search(r"/future/([^;/]+);(\d+)C", call_href)
        put_match = re.search(r"/future/([^;/]+);(\d+)P", put_href)
        if not call_match or not put_match or call_match.group(2) != put_match.group(2):
            continue
        strike = float(call_match.group(2))
        displayed_strike = _number(texts[7])
        if displayed_strike is not None and abs(displayed_strike - strike) > 0.01:
            continue
        option_symbol = option_symbol or call_match.group(1)
        quotes.append(
            OptionQuote(
                strike=strike,
                call_bid=_number(texts[0]),
                call_ask=_number(texts[1]),
                call_last=_number(texts[2]),
                call_change=_number(texts[3]),
                call_oi=_integer(texts[4]),
                call_volume=_integer(texts[5]),
                call_time=texts[6],
                put_bid=_number(texts[8]),
                put_ask=_number(texts[9]),
                put_last=_number(texts[10]),
                put_change=_number(texts[11]),
                put_oi=_integer(texts[12]),
                put_volume=_integer(texts[13]),
                put_time=texts[14],
            )
        )

    if len(quotes) < 5:
        raise ValueError(f"Yahoo option table parse failed: only {len(quotes)} rows")
    return tuple(sorted(quotes, key=lambda quote: quote.strike)), parser.trade_date, option_symbol


def parse_futures_html(html: str) -> FuturesQuote:
    parser = _YahooTableParser("main-1-FuturePriceTable-Proxy")
    parser.feed(html)
    for texts, hrefs in parser.rows:
        is_front_tx = any("/future/WTX&" in href for href in hrefs)
        if not is_front_tx or len(texts) < 15 or texts[0] != "台指期近一":
            continue
        values = texts[2:]
        bid, ask, last = (_number(values[index]) for index in range(3))
        if bid is None or ask is None or last is None:
            break
        return FuturesQuote(
            bid=bid,
            ask=ask,
            last=last,
            change=_number(values[3]),
            change_percent=_number(values[4]),
            volume=_integer(values[5]),
            open=_number(values[6]),
            high=_number(values[7]),
            low=_number(values[8]),
            basis=_number(values[9]),
            reference=_number(values[10]),
            open_interest=_integer(values[11]),
            quote_time=values[12],
            trade_date=parser.trade_date,
        )
    raise ValueError("Yahoo front-month TX futures row was not found")


def _weighted_median(values: Iterable[tuple[float, float]]) -> Optional[float]:
    ordered = sorted((value, weight) for value, weight in values if weight > 0)
    if not ordered:
        return None
    half = sum(weight for _, weight in ordered) / 2.0
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= half:
            return value
    return ordered[-1][0]


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _direction_label(score: Optional[float]) -> str:
    if score is None:
        return "資料累積中"
    if score >= 0.45:
        return "強勢偏多"
    if score >= 0.15:
        return "溫和偏多"
    if score <= -0.45:
        return "強勢偏空"
    if score <= -0.15:
        return "溫和偏空"
    return "震盪"


def _level_candidates(
    snapshot: MarketSnapshot,
    previous: Optional[MarketSnapshot],
    kind: str,
    max_distance: float,
    decay_points: float,
    option_center: Optional[float],
) -> tuple[LevelCandidate, ...]:
    current = snapshot.futures.last
    anchor = option_center or current
    basis_adjustment = current - anchor
    if kind == "support":
        quotes = [quote for quote in snapshot.options if 0 < anchor - quote.strike <= max_distance]
        oi_values = [quote.put_oi for quote in quotes]
        volume_attr = "put_volume"
        oi_attr = "put_oi"
    else:
        quotes = [quote for quote in snapshot.options if 0 < quote.strike - anchor <= max_distance]
        oi_values = [quote.call_oi for quote in quotes]
        volume_attr = "call_volume"
        oi_attr = "call_oi"
    if not quotes:
        return ()

    previous_by_strike = {quote.strike: quote for quote in previous.options} if previous else {}
    volume_changes: list[int] = []
    for quote in quotes:
        old = previous_by_strike.get(quote.strike)
        current_volume = int(getattr(quote, volume_attr))
        old_volume = int(getattr(old, volume_attr)) if old else 0
        volume_changes.append(max(0, current_volume - old_volume) if previous else current_volume)

    max_oi = max(max(oi_values), 1)
    max_volume = max(max(volume_changes), 1)
    ranked: list[LevelCandidate] = []
    for quote, volume_change in zip(quotes, volume_changes):
        oi = int(getattr(quote, oi_attr))
        distance = abs(quote.strike - anchor)
        distance_weight = math.exp(-distance / decay_points)
        oi_score = math.sqrt(oi / max_oi) if oi else 0.0
        volume_score = math.sqrt(volume_change / max_volume) if volume_change else 0.0
        round_bonus = 1.0 if quote.strike % 100 == 0 else 0.0
        score = 100.0 * distance_weight * (0.65 * oi_score + 0.25 * volume_score + 0.10 * round_bonus)
        ranked.append(
            LevelCandidate(
                strike=quote.strike,
                futures_level=quote.strike + basis_adjustment,
                score=score,
                open_interest=oi,
                volume_change=volume_change,
                distance=distance,
                kind=kind,
            )
        )

    selected: list[LevelCandidate] = []
    for candidate in sorted(ranked, key=lambda level: level.score, reverse=True):
        if all(abs(candidate.strike - existing.strike) >= 100 for existing in selected):
            selected.append(candidate)
        if len(selected) == 3:
            break
    return tuple(sorted(selected, key=lambda level: level.distance))


def analyze_snapshot(
    snapshot: MarketSnapshot,
    previous: Optional[MarketSnapshot] = None,
    max_level_distance: float = 1_000.0,
    decay_points: float = 350.0,
) -> AnalysisResult:
    current = snapshot.futures.last
    centers: list[tuple[float, float]] = []
    for quote in snapshot.options:
        if abs(quote.strike - current) > 350:
            continue
        call_mid = quote.call_mid
        put_mid = quote.put_mid
        if call_mid is None or put_mid is None:
            continue
        spread = (quote.call_ask - quote.call_bid) + (quote.put_ask - quote.put_bid)  # type: ignore[operator]
        distance_weight = math.exp(-abs(quote.strike - current) / 250.0)
        centers.append((quote.strike + call_mid - put_mid, distance_weight / (1.0 + spread)))
    option_center = _weighted_median(centers)

    valid_quotes = [quote for quote in snapshot.options if quote.call_mid is not None and quote.put_mid is not None]
    atm = min(valid_quotes, key=lambda quote: abs(quote.strike - (option_center or current))) if valid_quotes else None
    expected_move = (atm.call_mid + atm.put_mid) if atm else None  # type: ignore[operator]
    basis_adjustment = current - option_center if option_center is not None else 0.0
    range_center = atm.strike + basis_adjustment if atm else None

    direction_score: Optional[float] = None
    if previous and expected_move:
        normalization = max(expected_move * 0.05, 10.0)
        futures_component = _clamp((snapshot.futures.last - previous.futures.last) / normalization)
        previous_by_strike = {quote.strike: quote for quote in previous.options}
        option_signals: list[float] = []
        for quote in snapshot.options:
            if abs(quote.strike - current) > 150:
                continue
            old = previous_by_strike.get(quote.strike)
            if not old or None in (quote.call_mid, quote.put_mid, old.call_mid, old.put_mid):
                continue
            premium_shift = (quote.call_mid - old.call_mid) - (quote.put_mid - old.put_mid)  # type: ignore[operator]
            option_signals.append(_clamp(premium_shift / normalization))
        option_component = median(option_signals) if option_signals else futures_component
        direction_score = 100.0 * (0.55 * futures_component + 0.45 * option_component)

    supports = _level_candidates(
        snapshot, previous, "support", max_level_distance, decay_points, option_center
    )
    resistances = _level_candidates(
        snapshot, previous, "resistance", max_level_distance, decay_points, option_center
    )
    return AnalysisResult(
        option_center=option_center,
        atm_strike=atm.strike if atm else None,
        expected_move=expected_move,
        expected_low=(range_center - expected_move) if range_center is not None and expected_move else None,
        expected_high=(range_center + expected_move) if range_center is not None and expected_move else None,
        direction_score=direction_score,
        direction_label=_direction_label(direction_score / 100.0 if direction_score is not None else None),
        supports=supports,
        resistances=resistances,
    )


def snapshot_to_dict(snapshot: MarketSnapshot, distance: float = 1_500.0) -> dict:
    payload = asdict(snapshot)
    current = snapshot.futures.last
    payload["options"] = [
        asdict(quote) for quote in snapshot.options if abs(quote.strike - current) <= distance
    ]
    return payload


def make_snapshot(futures_html: str, options_html: str, captured_at: Optional[datetime] = None) -> MarketSnapshot:
    futures = parse_futures_html(futures_html)
    options, option_trade_date, option_symbol = parse_options_html(options_html)
    moment = captured_at or datetime.now().astimezone()
    return MarketSnapshot(
        captured_at=moment.isoformat(timespec="seconds"),
        futures=futures,
        options=options,
        option_trade_date=option_trade_date,
        option_symbol=option_symbol,
    )
