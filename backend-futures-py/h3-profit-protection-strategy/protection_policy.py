"""Legacy profit ledger compatibility only; not used for H3 price exits."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

ACTIVATION = Decimal('500')


def number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('獲利保護數值無效') from None


@dataclass(frozen=True)
class Protection:
    h3_direction: int = 0
    peak: str = '0'
    armed: bool = False
    locked: bool = False
    # Realized points from prior contracts in this SAME H3 round.
    carry_points: str = '0'
    entered_at: str | None = None
    sample_bar: str | None = None
    sample_received: str | None = None
    sample_price: str | None = None

    def validate(self):
        if (type(self.h3_direction) is not int or self.h3_direction not in (-1, 0, 1)
                or type(self.armed) is not bool or type(self.locked) is not bool
                or number(self.peak) < 0
                or self.armed != (number(self.peak) >= ACTIVATION)
                or (self.locked and (not self.armed or not self.h3_direction))):
            raise ValueError('獲利保護帳本無效')
        number(self.carry_points)
        if self.h3_direction == 0 and (self.armed or self.locked or number(self.peak)
                                      or number(self.carry_points)):
            raise ValueError('空手輪次保護帳本無效')
        if self.sample_price is not None and number(self.sample_price) <= 0:
            raise ValueError('保護報價無效')
        for stamp in (self.entered_at, self.sample_bar, self.sample_received):
            if stamp is not None:
                from datetime import datetime
                if datetime.fromisoformat(stamp).tzinfo is not None:
                    raise ValueError('保護帳本時間須為台北本地時間')
        return self
