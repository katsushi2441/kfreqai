import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import advisory_state
from sample_strategy import SampleStrategy


class KurageAdvisoryStrategy(SampleStrategy):
    """SampleStrategy's entry/exit logic, gated by two LLM advisory signals.

    - regime: gemma4 via Ollama, refreshed hourly -- direction filter
      (bullish/bearish/neutral)
    - directive: Claude CLI (OAuth session), refreshed 3x/day -- blanket
      risk on/off switch (risk_on/risk_off/neutral)

    Both signals are produced by kurage-advisory/hourly_regime.py and
    daily_directive.py running as host-side systemd timers, and shared
    via user_data/advisory_state.json. If that file is missing or stale,
    advisory_state.effective_*() fails open to "neutral" -- a broken
    advisory pipeline can only make trading more conservative, never
    block it outright and never crash the strategy.
    """

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        directive = advisory_state.effective_directive()
        if directive == "risk_off":
            return False

        regime = advisory_state.effective_regime()
        if side == "long" and regime == "bearish":
            return False
        if side == "short" and regime == "bullish":
            return False

        return super().confirm_trade_entry(
            pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
        )
