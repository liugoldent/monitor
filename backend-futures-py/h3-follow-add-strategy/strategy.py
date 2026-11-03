"""H3 configured-unit / profit-500 guerrilla crossing policy. No broker imports."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation

PROFIT_POINTS = Decimal('500')
LONG_LEVEL = Decimal('-1250')
SHORT_LEVEL = Decimal('1250')


def number(value, *, positive=False):
    try:
        result = Decimal(str(value).replace(',', ''))
        if not result.is_finite() or (positive and result <= 0):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('價格／游擊數值無效') from None


def flat_direction(text):
    """Explicit H3 flat notices only; never infer an exit from parse failure."""
    from h_signal import parse_h_flat_direction
    return parse_h_flat_direction(text)


@dataclass(frozen=True)
class State:
    expected: int = 0
    first_price: str | None = None
    added: bool = False
    round_key: str | None = None
    previous_guerrilla: dict[str, str] = field(default_factory=dict)
    sample_at: dict[str, str] = field(default_factory=dict)
    sample_value: dict[str, str] = field(default_factory=dict)

    def validate(self):
        if (type(self.expected) is not int
                or type(self.added) is not bool
                or (self.added and abs(self.expected) < 2)
                or (self.expected == 0 and self.first_price is not None)):
            raise ValueError('策略部位帳本無效')
        if self.first_price is not None:
            number(self.first_price, positive=True)
        for values in (self.previous_guerrilla, self.sample_at, self.sample_value):
            if not isinstance(values, dict):
                raise ValueError('游擊來源帳本無效')
        for value in self.previous_guerrilla.values():
            number(value)
        return self


def observe(state: State, ticker: str, guerrilla, sample_at: str, price):
    """Consume a new observation once; profit must qualify AT the crossing."""
    state.validate()
    value = number(guerrilla)
    current_price = number(price, positive=True) if price is not None else None
    previous_at = state.sample_at.get(ticker)
    if previous_at is not None and (sample_at < previous_at or
            (sample_at == previous_at and str(value) == state.sample_value.get(ticker))):
        return state, False
    previous = (number(state.previous_guerrilla[ticker])
                if ticker in state.previous_guerrilla else None)
    direction = (1 if state.expected > 0 else -1) if state.expected else 0
    level = LONG_LEVEL if direction > 0 else SHORT_LEVEL
    crossed = (previous is not None and direction != 0
               and ((previous < level < value) if direction > 0
                    else (previous > level > value)))
    profit = ((current_price - number(state.first_price)) * direction
              if current_price is not None and state.first_price is not None else None)
    add = bool(crossed and not state.added and state.expected != 0
               and profit is not None and profit >= PROFIT_POINTS)
    # Touching the boundary is neutral: -1300 -> -1250 -> -1200 crosses once.
    previous_values = dict(state.previous_guerrilla)
    if value != level:
        previous_values[ticker] = str(value)
    return replace(state, previous_guerrilla=previous_values,
                   sample_at=dict(state.sample_at, **{ticker: sample_at}),
                   sample_value=dict(state.sample_value, **{ticker: str(value)})), add
