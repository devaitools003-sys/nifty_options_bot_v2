"""
risk_engine.py
==============
Risk Management Engine for the Nifty options trading bot.

Enforces
---------
- Maximum daily loss limit (MTM-based halt)
- Daily profit target (auto square-off)
- Per-position stop-loss and target
- Maximum number of trades per session
- Maximum concurrent open positions
- Trailing stop-loss logic
- Gross exposure caps
"""

from __future__ import annotations

import threading
from datetime import datetime, date
from typing import Callable, List, Optional

from core.models import Position, PositionStatus, RiskSnapshot, OrderSide
from risk.margin_calculator import MarginCalculator
from utils.logger import get_logger

logger = get_logger("risk_engine")


class RiskEngine:
    """
    Evaluates risk conditions on every price tick and signals the strategy
    to halt or square off when limits are breached.

    Callbacks
    ---------
    on_halt(reason)          : called when bot should stop all activity
    on_square_off_all(reason): called when all positions should be closed
    on_exit_position(pos)    : called when a single position should be closed
    """

    def __init__(self, config: dict, capital_config: dict = None):
        r = config  # shorthand
        c = capital_config or {}

        # ── Limits from config ────────────────────────────────
        self.max_loss_per_day: float = r.get("max_loss_per_day", 3000)
        self.target_profit_per_day: float = r.get("target_profit_per_day", 5000)
        self.position_stop_loss: float = r.get("position_stop_loss", 2000)
        self.position_target: float = r.get("position_target", 1500)
        self.max_trades_per_day: int = r.get("max_trades_per_day", 1)
        self.max_open_positions: int = r.get("max_open_positions", 4)
        self.trailing_sl_enabled: bool = r.get("trailing_sl", False)
        self.trailing_sl_trigger: float = r.get("trailing_sl_trigger", 1500)
        self.trailing_sl_distance: float = r.get("trailing_sl_distance", 800)
        self.margin_warning_pct: float = r.get("margin_warning_pct", 80)
        self.margin_critical_pct: float = r.get("margin_critical_pct", 95)

        # ── Margin calculator ─────────────────────────────────
        self.margin = MarginCalculator(
            total_capital   = c.get("total_capital",   150_000),
            max_margin_limit= c.get("max_margin_limit", 100_000),
            lot_size        = 50,
        )

        # ── Session state ─────────────────────────────────────
        self._session_date: str = date.today().isoformat()
        self._trades_today: int = 0
        self._is_halted: bool = False
        self._halt_reason: str = ""
        self._peak_pnl: float = 0.0
        self._trailing_active: bool = False
        self._current_margin_used: float = 0.0
        self._lock = threading.Lock()

        # ── Callbacks ─────────────────────────────────────────
        self.on_halt: Optional[Callable[[str], None]] = None
        self.on_square_off_all: Optional[Callable[[str], None]] = None
        self.on_exit_position: Optional[Callable[[Position], None]] = None

        logger.info(
            f"RiskEngine | MaxLoss=Rs{self.max_loss_per_day} "
            f"| Target=Rs{self.target_profit_per_day} "
            f"| Capital=Rs{self.margin.total_capital:,.0f} "
            f"| MarginLimit=Rs{self.margin.max_margin_limit:,.0f}"
        )

    # ── Main Evaluation ───────────────────────────────────────

    def evaluate(self, positions: List[Position]) -> RiskSnapshot:
        """
        Called on every price tick. Evaluates all risk conditions and
        fires callbacks if limits are breached.

        Returns a RiskSnapshot for the dashboard.
        """
        if self._is_halted:
            return self._build_snapshot(positions)

        total_pnl = self._compute_total_pnl(positions)

        # ── Update peak PnL for trailing SL ─────────────────
        with self._lock:
            if total_pnl > self._peak_pnl:
                self._peak_pnl = total_pnl
            if total_pnl >= self.trailing_sl_trigger:
                self._trailing_active = True

        # ── Daily max loss breach ─────────────────────────────
        if total_pnl <= -self.max_loss_per_day:
            self._trigger_halt(f"Max daily loss hit: ₹{total_pnl:.2f}")
            self._trigger_square_off_all("Max loss limit reached")
            return self._build_snapshot(positions)

        # ── Daily profit target hit ───────────────────────────
        if total_pnl >= self.target_profit_per_day:
            self._trigger_halt(f"Daily profit target hit: ₹{total_pnl:.2f}")
            self._trigger_square_off_all("Profit target achieved")
            return self._build_snapshot(positions)

        # ── Trailing SL (if enabled and active) ─────────────
        if self.trailing_sl_enabled and self._trailing_active:
            trail_breach_level = self._peak_pnl - self.trailing_sl_distance
            if total_pnl <= trail_breach_level:
                self._trigger_halt(
                    f"Trailing SL triggered: peak=₹{self._peak_pnl:.2f}, "
                    f"current=₹{total_pnl:.2f}"
                )
                self._trigger_square_off_all("Trailing SL triggered")
                return self._build_snapshot(positions)

        # Margin breach check
        spot = getattr(self, '_last_known_spot', 24000)
        margin_used = self.margin.compute_current_margin_used(positions, spot)
        with self._lock:
            self._current_margin_used = margin_used
        util_pct = self.margin.margin_utilisation_pct(margin_used)

        if util_pct >= self.margin_critical_pct:
            worst = self.margin.find_most_losing_position(positions)
            if worst:
                sym = worst.contract.trading_symbol if worst.contract else 'unknown'
                logger.critical(
                    f'MARGIN BREACH {util_pct:.1f}% >= {self.margin_critical_pct}% '
                    f'Squaring off worst leg: {sym} PnL=Rs{worst.unrealised_pnl:.2f}'
                )
                if self.on_exit_position:
                    self.on_exit_position(worst)
        elif util_pct >= self.margin_warning_pct:
            logger.warning(
                f'Margin warning: {util_pct:.1f}% used '
                f'(Rs{margin_used:,.0f} / Rs{self.margin.max_margin_limit:,.0f})'
            )

        # ── Per-position checks ───────────────────────────────
        for pos in positions:
            if pos.status != PositionStatus.OPEN:
                continue
            pnl = pos.unrealised_pnl
            if pnl <= -self.position_stop_loss:
                logger.warning(
                    f"Position SL hit: {pos.contract.trading_symbol} PnL=₹{pnl:.2f}"
                )
                if self.on_exit_position:
                    self.on_exit_position(pos)
            elif pnl >= self.position_target:
                logger.info(
                    f"Position target hit: {pos.contract.trading_symbol} PnL=₹{pnl:.2f}"
                )
                if self.on_exit_position:
                    self.on_exit_position(pos)

        return self._build_snapshot(positions)

    # ── Trade Guards ──────────────────────────────────────────

    def can_enter_trade(self, open_positions: List[Position]) -> tuple[bool, str]:
        """
        Returns (allowed: bool, reason: str).
        Call before every new strategy entry.
        """
        if self._is_halted:
            return False, f"Bot halted: {self._halt_reason}"

        open_count = len([p for p in open_positions if p.status == PositionStatus.OPEN])
        if open_count >= self.max_open_positions:
            return False, f"Max open positions reached ({self.max_open_positions})"

        if self._trades_today >= self.max_trades_per_day:
            return False, f"Max daily trades reached ({self.max_trades_per_day})"

        return True, "OK"

    def record_trade_entry(self) -> None:
        """Increment trade counter after a new entry is made."""
        with self._lock:
            self._trades_today += 1
        logger.info(f"Trade recorded: {self._trades_today}/{self.max_trades_per_day} today")

    # ── Internal Helpers ──────────────────────────────────────

    def _compute_total_pnl(self, positions: List[Position]) -> float:
        unrealised = sum(p.unrealised_pnl for p in positions if p.status == PositionStatus.OPEN)
        realised = sum(p.realised_pnl for p in positions if p.status == PositionStatus.CLOSED)
        return unrealised + realised

    def _trigger_halt(self, reason: str) -> None:
        with self._lock:
            if not self._is_halted:
                self._is_halted = True
                self._halt_reason = reason
                logger.critical(f"RISK HALT: {reason}")
                if self.on_halt:
                    self.on_halt(reason)

    def _trigger_square_off_all(self, reason: str) -> None:
        logger.warning(f"SQUARE OFF ALL: {reason}")
        if self.on_square_off_all:
            self.on_square_off_all(reason)

    def _build_snapshot(self, positions: List[Position]) -> RiskSnapshot:
        total_pnl = self._compute_total_pnl(positions)
        unrealised = sum(p.unrealised_pnl for p in positions if p.status == PositionStatus.OPEN)
        realised = sum(p.realised_pnl for p in positions if p.status == PositionStatus.CLOSED)
        open_count = len([p for p in positions if p.status == PositionStatus.OPEN])
        exposure = sum(
            p.entry_price * p.quantity
            for p in positions if p.status == PositionStatus.OPEN
        )
        drawdown = self._peak_pnl - total_pnl if total_pnl < self._peak_pnl else 0.0

        return RiskSnapshot(
            session_date=self._session_date,
            total_unrealised_pnl=round(unrealised, 2),
            total_realised_pnl=round(realised, 2),
            peak_pnl=round(self._peak_pnl, 2),
            drawdown=round(drawdown, 2),
            trades_today=self._trades_today,
            open_positions=open_count,
            gross_exposure=round(exposure, 2),
            max_loss_limit=self.max_loss_per_day,
            target_profit=self.target_profit_per_day,
            is_halted=self._is_halted,
            halt_reason=self._halt_reason,
        )

    # ── Reset (new session) ───────────────────────────────────

    def reset_session(self) -> None:
        """Call at start of each trading day to reset daily counters."""
        with self._lock:
            self._session_date = date.today().isoformat()
            self._trades_today = 0
            self._is_halted = False
            self._halt_reason = ""
            self._peak_pnl = 0.0
            self._trailing_active = False
        logger.info("RiskEngine: session reset for new trading day")

    @property
    def is_halted(self) -> bool:
        return self._is_halted

    @property
    def trades_today(self) -> int:
        return self._trades_today
