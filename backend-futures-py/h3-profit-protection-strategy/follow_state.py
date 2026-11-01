"""Account-two quantity ledger; no guerrilla add flag or two-lot ceiling."""
from dataclasses import replace

import protection_bootstrap
from strategy import State as BaseState


class FollowState(BaseState):
    def validate(self):
        if type(self.expected) is not int or self.added is not False:
            raise ValueError('永豐2部位須為整數口數，禁止加碼旗標')
        # Preserve all shared price/source validation with a one-lot surrogate.
        direction = (1 if self.expected > 0 else -1) if self.expected else 0
        BaseState.validate(replace(self, expected=direction))
        return self
