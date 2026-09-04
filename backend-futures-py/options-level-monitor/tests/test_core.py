from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import (  # noqa: E402
    FuturesQuote,
    MarketSnapshot,
    OptionQuote,
    analyze_snapshot,
    parse_futures_html,
    parse_options_html,
)


def option_row(strike: int, call: list[str], put: list[str], symbol: str = "WTXUU6") -> str:
    def side(values: list[str], right: str) -> str:
        cells = []
        for index, value in enumerate(values):
            href = f'/future/{symbol};{strike}{right}' if index in (0, 1, 2, 3, 5) else ""
            link = f'<a href="{href}"><span>{value}</span></a>' if href else f"<span>{value}</span>"
            cells.append(f"<li>{link}</li>")
        return "<div><ul>" + "".join(cells) + "</ul></div>"

    return (
        '<li><div class="table-row other">'
        + side(call, "C")
        + f"<div><span>{strike:,}</span></div>"
        + side(put, "P")
        + "</div></li>"
    )


class ParserTests(unittest.TestCase):
    def test_parse_options_rows(self) -> None:
        rows = "".join(
            option_row(
                strike,
                ["100", "102", "101", "5", "1,200", "3,400", "12:00:00"],
                ["90", "92", "91", "-3", "1,500", "4,500", "12:00:01"],
            )
            for strike in (46000, 46050, 46100, 46150, 46200)
        )
        html = (
            '<div id="main-1-OptionsPriceTable-Proxy"><time datatime="2026/09/04"></time>'
            + rows
            + "</div>"
        )
        quotes, trade_date, symbol = parse_options_html(html)
        self.assertEqual(len(quotes), 5)
        self.assertEqual(trade_date, "2026/09/04")
        self.assertEqual(symbol, "WTXUU6")
        self.assertEqual(quotes[0].strike, 46000)
        self.assertEqual(quotes[0].call_oi, 1200)
        self.assertEqual(quotes[0].put_volume, 4500)

    def test_parse_front_month_futures(self) -> None:
        values = [
            "46,422.00",
            "46,425.00",
            "46,422.00",
            "603.00",
            "1.32%",
            "70,959",
            "45,902.00",
            "46,650.00",
            "45,726.00",
            "79.60",
            "45,819.00",
            "103,513",
            "12:10:08",
        ]
        cells = "".join(f"<div><span>{value}</span></div>" for value in values)
        html = (
            '<div id="main-1-FuturePriceTable-Proxy"><time datatime="2026/09/04"></time>'
            '<div class="table-row"><div><a href="https://tw.stock.yahoo.com/future/WTX&amp;"></a>'
            "<div>台指期近一</div><span>WTX&amp;</span></div>"
            + cells
            + "</div></div>"
        )
        quote = parse_futures_html(html)
        self.assertEqual(quote.last, 46422)
        self.assertEqual(quote.high, 46650)
        self.assertEqual(quote.quote_time, "12:10:08")


class AnalysisTests(unittest.TestCase):
    def _snapshot(self, last: float, call_shift: float = 0, put_shift: float = 0) -> MarketSnapshot:
        strikes = range(45800, 46901, 50)
        options = []
        for strike in strikes:
            intrinsic_call = max(last - strike, 0)
            intrinsic_put = max(strike - last, 0)
            time_value = max(12.0, 180.0 - abs(strike - last) * 0.25)
            call_mid = intrinsic_call + time_value + call_shift
            put_mid = intrinsic_put + time_value + put_shift
            call_oi = 1700 if strike == 46500 else 300
            put_oi = 1900 if strike == 46000 else 250
            options.append(
                OptionQuote(
                    strike=float(strike),
                    call_bid=call_mid - 1,
                    call_ask=call_mid + 1,
                    call_last=call_mid,
                    call_change=0,
                    call_oi=call_oi,
                    call_volume=500,
                    call_time="12:00:00",
                    put_bid=put_mid - 1,
                    put_ask=put_mid + 1,
                    put_last=put_mid,
                    put_change=0,
                    put_oi=put_oi,
                    put_volume=500,
                    put_time="12:00:00",
                )
            )
        future = FuturesQuote(
            bid=last - 1,
            ask=last + 1,
            last=last,
            change=100,
            change_percent=0.2,
            volume=10_000,
            open=46100,
            high=46600,
            low=45900,
            basis=0,
            reference=46100,
            open_interest=100_000,
            quote_time="12:00:00",
            trade_date="2026/09/04",
        )
        return MarketSnapshot(
            captured_at=datetime.now(timezone.utc).isoformat(),
            futures=future,
            options=tuple(options),
            option_trade_date="2026/09/04",
            option_symbol="WTXUU6",
        )

    def test_levels_prefer_nearby_oi_walls(self) -> None:
        result = analyze_snapshot(self._snapshot(46280))
        self.assertIn(46000, [level.strike for level in result.supports])
        self.assertIn(46500, [level.strike for level in result.resistances])
        self.assertTrue(all(level.futures_level > 0 for level in result.supports + result.resistances))
        self.assertIsNotNone(result.option_center)
        self.assertIsNotNone(result.expected_move)

    def test_direction_uses_futures_and_option_premium_shift(self) -> None:
        previous = self._snapshot(46250)
        current = self._snapshot(46280, call_shift=8, put_shift=-8)
        result = analyze_snapshot(current, previous)
        self.assertIsNotNone(result.direction_score)
        self.assertGreater(result.direction_score or 0, 15)
        self.assertIn("偏多", result.direction_label)

    def test_option_strikes_are_translated_to_futures_levels(self) -> None:
        snapshot = self._snapshot(46280, call_shift=-40, put_shift=40)
        result = analyze_snapshot(snapshot)
        self.assertAlmostEqual(result.option_center or 0, 46200, delta=1)
        resistance = next(level for level in result.resistances if level.strike == 46500)
        self.assertAlmostEqual(resistance.futures_level, 46580, delta=1)


if __name__ == "__main__":
    unittest.main()
