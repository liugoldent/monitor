"""Increase entry size after four or six net losing H3 rounds."""
from dataclasses import dataclass, replace

from protection_policy import number

ROUNDTRIP_COST = '4.8'  # estimated points per contract, same research convention


def loss_tier(streak):
    return int(streak >= 4) + int(streak >= 6)


@dataclass(frozen=True)
class RecoverySizing:
    version: int = 1
    equity_points: str = '0'
    peak_points: str = '0'
    loss_streak: int = 0
    extra_contracts: int = 0
    closed_trades: int = 0
    roll_cost_points: str = '0'
    cost_points_roundtrip: str = ROUNDTRIP_COST

    def validate(self):
        equity, peak = number(self.equity_points), number(self.peak_points)
        if (type(self.version) is not int or self.version != 1
                or type(self.loss_streak) is not int or self.loss_streak < 0
                or type(self.closed_trades) is not int or self.closed_trades < self.loss_streak
                or peak < max(0, equity)
                or type(self.extra_contracts) is not int or not loss_tier(self.loss_streak) <= self.extra_contracts <= 2
                or (self.extra_contracts and peak == equity)
                or number(self.roll_cost_points) < 0
                or number(self.cost_points_roundtrip) != number(ROUNDTRIP_COST)):
            raise ValueError('連輸加口帳本無效')
        return self

    @classmethod
    def from_payload(cls, payload):
        if 'boosted' in payload and 'extra_contracts' not in payload:
            payload = dict(payload)
            boosted = payload.pop('boosted')
            streak = payload['loss_streak']
            if type(boosted) is not bool or type(streak) is not int or (streak >= 6 and not boosted):
                raise ValueError('舊版連輸加口帳本無效')
            # An active legacy boost proves six losses occurred since the last recovery.
            payload['extra_contracts'] = 2 if boosted else loss_tier(streak)
        return cls(**payload).validate()

    def quantity(self, unit):
        self.validate()
        if type(unit) is not int or unit < 1:
            raise ValueError('基準口數須為正整數')
        return unit + self.extra_contracts

    def close(self, gross_points_per_contract, quantity):
        """Called once per fully closed H3 round, never once per fill/month."""
        self.validate()
        if type(quantity) is not int or quantity < 1:
            raise ValueError('平倉口數無效')
        net = (number(gross_points_per_contract) - number(self.cost_points_roundtrip)
               - number(self.roll_cost_points)) * quantity
        equity = number(self.equity_points) + net
        peak = max(number(self.peak_points), equity)
        streak = self.loss_streak + 1 if net < 0 else 0
        extra = 0 if equity == peak else max(self.extra_contracts, loss_tier(streak))
        return replace(self, equity_points=str(equity), peak_points=str(peak),
                       loss_streak=streak, extra_contracts=extra,
                       closed_trades=self.closed_trades+1, roll_cost_points='0').validate()

    def rolled(self):
        return replace(self, roll_cost_points=str(number(self.roll_cost_points)
                       + number(self.cost_points_roundtrip))).validate()
