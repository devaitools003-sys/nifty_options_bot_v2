"""
margin_calculator.py
====================
Margin requirement calculator for Nifty index options.

Capital structure
-----------------
Total Capital    : ₹1,50,000
Max Margin Limit : ₹1,00,000  (never exceed this)
Safety Buffer    : ₹50,000    (always kept free)

Margin rules (approximate SPAN + Exposure for MIS)
---------------------------------------------------
Short Option (naked CE/PE) : ~18-22% of notional (spot × lot_size)
Straddle (2 short legs)    : ~20-25% of notional (hedge benefit ~20%)
Strangle (2 short legs)    : ~22-27% of notional
Iron Condor (defined risk) : ~10-14% of notional (long hedges reduce SPAN)

These are conservative estimates. Actual SPAN margin varies with IV.
The calculator uses configurable percentages so you can tune them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
from utils.logger import get_logger

logger = get_logger("margin")


# ── Margin rate table (% of notional per strategy) ────────────
# notional = spot_price × lot_size
# These are MIS (intraday) rates — conservative estimates

# Fixed per-lot margin in Rs (MIS intraday SPAN estimates for Nifty)
# These are flat amounts, NOT % of notional
# Actual SPAN varies with IV — these are conservative estimates
MARGIN_FIXED: Dict[str, float] = {
    "naked_call":   45_000,   # ~Rs45,000 per lot MIS
    "naked_put":    45_000,
    "atm_straddle": 50_000,   # 2 legs with hedge benefit ~Rs50,000 total
    "atm_strangle": 55_000,   # slightly wider = slightly more margin
    "iron_condor":  30_000,   # defined risk — lowest margin
}

# Keep rate table for notional-based fallback only
MARGIN_RATES: Dict[str, float] = {
    "naked_call":   0.20,
    "naked_put":    0.20,
    "atm_straddle": 0.22,
    "atm_strangle": 0.24,
    "iron_condor":  0.12,
}

# Strategy risk ranking — safest first (lowest margin usage)
STRATEGY_SAFETY_RANK = [
    "iron_condor",
    "atm_straddle",
    "atm_strangle",
    "naked_put",
    "naked_call",
]


@dataclass
class MarginEstimate:
    """Result of a margin calculation for a potential trade."""
    strategy: str
    lots: int
    lot_size: int
    spot_price: float
    notional: float
    margin_required: float
    margin_available: float
    margin_after_trade: float
    fits_within_limit: bool
    utilisation_pct: float


class MarginCalculator:
    """
    Calculates margin requirements and determines optimal lot size
    for each strategy given the available capital.

    Parameters
    ----------
    total_capital     : Total account capital (₹1,50,000)
    max_margin_limit  : Hard ceiling on margin in use (₹1,00,000)
    lot_size          : Nifty lot size (50)
    """

    def __init__(
        self,
        total_capital: float = 150_000,
        max_margin_limit: float = 100_000,
        lot_size: int = 50,
    ):
        self.total_capital    = total_capital
        self.max_margin_limit = max_margin_limit
        self.lot_size         = lot_size
        self.safety_buffer    = total_capital - max_margin_limit  # ₹50,000

        logger.info(
            f"MarginCalculator | Capital=₹{total_capital:,.0f} "
            f"| MaxMargin=₹{max_margin_limit:,.0f} "
            f"| Buffer=₹{self.safety_buffer:,.0f}"
        )

    # ── Core margin calculation ───────────────────────────────

    def margin_per_lot(self, strategy: str, spot_price: float) -> float:
        """
        Return estimated margin for 1 lot of a strategy.
        Uses fixed SPAN MIS estimates (more realistic than % of notional).
        """
        return MARGIN_FIXED.get(strategy, 50_000)

    def max_lots_for_strategy(
        self,
        strategy: str,
        spot_price: float,
        current_margin_used: float = 0.0,
    ) -> int:
        """
        Return the maximum number of lots that can be traded
        for a given strategy without breaching the margin limit.

        Parameters
        ----------
        strategy             : strategy name string
        spot_price           : current Nifty spot price
        current_margin_used  : margin already consumed by open positions
        """
        available = self.max_margin_limit - current_margin_used
        if available <= 0:
            return 0

        margin_per = self.margin_per_lot(strategy, spot_price)
        if margin_per <= 0:
            return 0

        max_lots = int(available / margin_per)
        return max(0, max_lots)

    def estimate(
        self,
        strategy: str,
        lots: int,
        spot_price: float,
        current_margin_used: float = 0.0,
    ) -> MarginEstimate:
        """
        Full margin estimate using fixed SPAN MIS amounts per lot.
        """
        margin_per      = MARGIN_FIXED.get(strategy, 50_000)
        margin_required = margin_per * lots
        notional        = spot_price * self.lot_size * lots
        margin_available= self.max_margin_limit - current_margin_used
        margin_after    = current_margin_used + margin_required
        fits            = margin_after <= self.max_margin_limit
        util_pct        = (margin_after / self.max_margin_limit * 100) if self.max_margin_limit else 0

        return MarginEstimate(
            strategy           = strategy,
            lots               = lots,
            lot_size           = self.lot_size,
            spot_price         = spot_price,
            notional           = round(notional, 2),
            margin_required    = round(margin_required, 2),
            margin_available   = round(margin_available, 2),
            margin_after_trade = round(margin_after, 2),
            fits_within_limit  = fits,
            utilisation_pct    = round(util_pct, 1),
        )


    def pick_best_strategy(
        self,
        spot_price: float,
        current_margin_used: float = 0.0,
        preferred: str = "atm_straddle",
    ) -> tuple:
        """
        Given current margin usage, pick the safest strategy that
        fits within the margin limit and return (strategy_name, lots).

        Logic
        -----
        1. Try preferred strategy first with max possible lots
        2. If it doesn't fit even with 1 lot, try next safest strategy
        3. If nothing fits, return ("none", 0)

        Returns
        -------
        (strategy_name, lots) — lots=0 means no trade possible
        """
        # Guard: use fallback spot if 0
        if not spot_price or spot_price < 1000:
            spot_price = 23700.0
            logger.warning(f"MarginSelector: spot=0, using fallback Rs{spot_price:,.0f}")

        # Try preferred first
        candidates = [preferred] + [s for s in STRATEGY_SAFETY_RANK if s != preferred]

        for strategy in candidates:
            lots = self.max_lots_for_strategy(strategy, spot_price, current_margin_used)
            if lots >= 1:
                logger.info(
                    f"MarginSelector: {strategy} | {lots} lot(s) | "
                    f"Margin=₹{self.margin_per_lot(strategy, spot_price) * lots:,.0f} | "
                    f"Used={current_margin_used:,.0f}/{self.max_margin_limit:,.0f}"
                )
                return strategy, lots

        logger.warning(
            f"MarginSelector: No strategy fits within margin limit "
            f"(used=₹{current_margin_used:,.0f}, limit=₹{self.max_margin_limit:,.0f})"
        )
        return "none", 0

    # ── Current margin usage ──────────────────────────────────

    def compute_current_margin_used(
        self,
        positions: list,
        spot_price: float,
    ) -> float:
        """
        Estimate total margin currently consumed by open positions.
        Uses the same rate table applied to each open position's strategy.

        In a real broker, you'd call the margin API instead.
        """
        from core.models import PositionStatus
        total = 0.0
        for pos in positions:
            if pos.status != PositionStatus.OPEN:
                continue
            # Detect strategy type from position (naked = 1 leg)
            # For simplicity, use naked rate per leg
            rate     = 0.20  # default per short leg
            notional = spot_price * pos.quantity
            total   += notional * rate
        return round(total, 2)

    # ── Margin breach handler ─────────────────────────────────

    def find_most_losing_position(self, positions: list):
        """
        Return the open position with the worst (most negative) unrealised PnL.
        Used when margin breach requires squaring off the weakest leg first.
        """
        from core.models import PositionStatus
        open_pos = [p for p in positions if p.status == PositionStatus.OPEN]
        if not open_pos:
            return None
        # Sort by unrealised PnL ascending (most negative first)
        return min(open_pos, key=lambda p: p.unrealised_pnl)

    def is_margin_breached(self, current_margin_used: float) -> bool:
        """Return True if current margin usage exceeds the hard limit."""
        return current_margin_used > self.max_margin_limit

    def margin_utilisation_pct(self, current_margin_used: float) -> float:
        """Return margin used as % of the limit."""
        return round(current_margin_used / self.max_margin_limit * 100, 1)
