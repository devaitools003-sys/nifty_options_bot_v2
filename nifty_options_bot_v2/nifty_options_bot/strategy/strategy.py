"""
strategy.py
===========
Strategy Engine for the Nifty options trading bot.

Supported Strategies
--------------------
- ATM Straddle      : Short ATM CE + Short ATM PE
- ATM Strangle      : Short OTM CE + Short OTM PE (configurable width)
- Iron Condor       : Short inner strikes + Long outer strikes (risk-defined)
- Naked Call        : Short OTM CE only
- Naked Put         : Short OTM PE only

The StrategyEngine runs on a background thread, checks entry/exit
conditions based on IST time, and delegates execution to the OrderManager.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, time as dtime
from typing import Dict, List, Optional, Tuple

from core.models import (
    OptionContract, OptionType, OrderSide, Position, PositionStatus
)
from data.data_feed import MarketDataFeed
from risk.risk_engine import RiskEngine
from strategy.orders import OrderManager
from utils.logger import get_logger

logger = get_logger("strategy")


# ── Base Strategy ─────────────────────────────────────────────

class BaseStrategy(ABC):
    """Abstract base class for all option strategies."""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.lots: int = config.get("lots", 1)
        self.atm_offset: int = config.get("atm_offset", 0)
        self.strike_step: int = 50  # set by strategy engine

    @abstractmethod
    def get_legs(
        self,
        atm_strike: int,
        expiry: str,
        data_feed: MarketDataFeed,
    ) -> List[Tuple[OptionContract, OrderSide]]:
        """Return list of (contract, side) tuples representing strategy legs."""

    def __repr__(self):
        return f"{self.name}(lots={self.lots})"


class ATMStraddle(BaseStrategy):
    """Short ATM CE + Short ATM PE."""

    def get_legs(self, atm_strike, expiry, data_feed):
        chain = data_feed.build_options_chain(expiry, atm_strike, width=2)
        legs = []
        for opt_type in (OptionType.CALL, OptionType.PUT):
            sym = data_feed._make_symbol("NIFTY", expiry, atm_strike, opt_type)
            contract = chain.get(sym)
            if contract:
                legs.append((contract, OrderSide.SELL))
        logger.info(f"ATMStraddle legs: {[c.trading_symbol for c, _ in legs]}")
        return legs


class ATMStrangle(BaseStrategy):
    """Short OTM CE + Short OTM PE at configurable width."""

    def get_legs(self, atm_strike, expiry, data_feed):
        width = self.config.get("strangle_width", 2)
        chain = data_feed.build_options_chain(expiry, atm_strike, width=width + 1)
        call_strike = atm_strike + width * self.strike_step
        put_strike = atm_strike - width * self.strike_step
        legs = []
        for strike, opt_type in ((call_strike, OptionType.CALL), (put_strike, OptionType.PUT)):
            sym = data_feed._make_symbol("NIFTY", expiry, strike, opt_type)
            contract = chain.get(sym)
            if contract:
                legs.append((contract, OrderSide.SELL))
        logger.info(f"ATMStrangle legs: {[c.trading_symbol for c, _ in legs]}")
        return legs


class IronCondor(BaseStrategy):
    """
    4-leg iron condor:
    Short inner OTM CE/PE + Long outer OTM CE/PE (wing hedge).
    """

    def get_legs(self, atm_strike, expiry, data_feed):
        ic_cfg = self.config.get("iron_condor", {})
        short_off = ic_cfg.get("short_offset", 1)
        long_off = ic_cfg.get("long_offset", 3)

        chain = data_feed.build_options_chain(expiry, atm_strike, width=long_off + 1)
        legs = []

        for opt_type, direction in ((OptionType.CALL, 1), (OptionType.PUT, -1)):
            for offset, side in ((short_off, OrderSide.SELL), (long_off, OrderSide.BUY)):
                strike = atm_strike + direction * offset * self.strike_step
                sym = data_feed._make_symbol("NIFTY", expiry, strike, opt_type)
                contract = chain.get(sym)
                if contract:
                    legs.append((contract, side))

        logger.info(f"IronCondor legs: {[c.trading_symbol for c, _ in legs]}")
        return legs


class NakedCall(BaseStrategy):
    """Short OTM CE."""

    def get_legs(self, atm_strike, expiry, data_feed):
        offset = self.atm_offset
        call_strike = atm_strike + offset * self.strike_step
        chain = data_feed.build_options_chain(expiry, atm_strike, width=offset + 1)
        sym = data_feed._make_symbol("NIFTY", expiry, call_strike, OptionType.CALL)
        contract = chain.get(sym)
        return [(contract, OrderSide.SELL)] if contract else []


class NakedPut(BaseStrategy):
    """Short OTM PE."""

    def get_legs(self, atm_strike, expiry, data_feed):
        offset = self.atm_offset
        put_strike = atm_strike - offset * self.strike_step
        chain = data_feed.build_options_chain(expiry, atm_strike, width=offset + 1)
        sym = data_feed._make_symbol("NIFTY", expiry, put_strike, OptionType.PUT)
        contract = chain.get(sym)
        return [(contract, OrderSide.SELL)] if contract else []


# ── Strategy Factory ──────────────────────────────────────────

STRATEGY_REGISTRY: Dict[str, type] = {
    "atm_straddle": ATMStraddle,
    "atm_strangle": ATMStrangle,
    "iron_condor": IronCondor,
    "naked_call": NakedCall,
    "naked_put": NakedPut,
}


def create_strategy(config: dict) -> BaseStrategy:
    name = config.get("name", "atm_straddle").lower()
    cls = STRATEGY_REGISTRY.get(name)
    if not cls:
        raise ValueError(f"Unknown strategy '{name}'. Available: {list(STRATEGY_REGISTRY)}")
    return cls(name=name, config=config)


# ── Strategy Engine (Orchestrator) ────────────────────────────

class StrategyEngine:
    """
    Top-level orchestrator that runs the selected strategy on a schedule.

    Lifecycle
    ---------
    1. start()  → launches background loop
    2. Loop checks IST time against entry_time and exit_time
    3. At entry_time: calls strategy.get_legs() and places orders
    4. Continuously: updates prices, evaluates risk, applies position SL/target
    5. At exit_time:  squares off all open positions
    6. stop()   → graceful shutdown
    """

    STATUS_IDLE = "IDLE"
    STATUS_WAITING = "WAITING FOR ENTRY"
    STATUS_ACTIVE = "ACTIVE"
    STATUS_HALTED = "HALTED"
    STATUS_CLOSED = "CLOSED"

    def __init__(
        self,
        strategy: BaseStrategy,
        order_manager: OrderManager,
        risk_engine: RiskEngine,
        data_feed: MarketDataFeed,
        config: dict,
    ):
        self.strategy = strategy
        self.om = order_manager
        self.risk = risk_engine
        self.data_feed = data_feed

        # ── Config ────────────────────────────────────────────
        self._entry_time: dtime = self._parse_time(config.get("entry_time", "09:20"))
        self._exit_time: dtime = self._parse_time(config.get("exit_time", "15:15"))
        self._instrument_config = {}  # set externally by main
        self._capital_config    = {}  # set externally by main

        # ── State ─────────────────────────────────────────────
        self._running = False
        self._entered_today = False
        self._status = self.STATUS_IDLE
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # ── Wire risk callbacks ───────────────────────────────
        self.risk.on_halt = self._on_halt
        self.risk.on_square_off_all = self._square_off_all
        self.risk.on_exit_position = self._exit_single_position

        logger.info(
            f"StrategyEngine initialised | Strategy={strategy.name} "
            f"| Entry={config.get('entry_time')} | Exit={config.get('exit_time')}"
        )

    # ── Public API ────────────────────────────────────────────

    def start(self) -> None:
        """Start the strategy loop on a background daemon thread."""
        self._running = True
        self._status = self.STATUS_WAITING
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="StrategyEngine"
        )
        self._thread.start()
        logger.info("StrategyEngine started")

    def stop(self) -> None:
        """Gracefully stop the strategy engine."""
        self._running = False
        self._status = self.STATUS_IDLE
        logger.info("StrategyEngine stopped")

    @property
    def status(self) -> str:
        return self._status

    # ── Main Loop ─────────────────────────────────────────────

    def _is_market_open(self) -> bool:
        """Return True only on weekdays between 9:15 and 15:30 IST."""
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        t = now.time()
        return dtime(9, 15) <= t <= dtime(15, 30)

    def _loop(self) -> None:
        """Main strategy loop — runs every 5 seconds."""
        while self._running:
            try:
                now = datetime.now().time()

                # Market hours guard
                if not self._is_market_open():
                    self._status = self.STATUS_WAITING
                    time.sleep(30)
                    continue

                # Entry check
                if not self._entered_today and now >= self._entry_time and now < self._exit_time:
                    self._enter_strategy()

                # ── Position price refresh ───────────────────
                if self._entered_today:
                    self._refresh_positions()

                # ── Exit time square-off ─────────────────────
                if now >= self._exit_time and self._entered_today:
                    logger.info("Exit time reached — squaring off all positions")
                    self._square_off_all("End-of-day exit time reached")
                    self._status = self.STATUS_CLOSED
                    break

                # ── Risk evaluation ──────────────────────────
                positions = self.om.get_all_positions()
                self.risk.evaluate(positions)

            except Exception as e:
                logger.error(f"StrategyEngine loop error: {e}", exc_info=True)

            time.sleep(5)

    def _enter_strategy(self) -> None:
        """Execute margin-aware strategy entry."""
        open_positions = self.om.get_open_positions()
        allowed, reason = self.risk.can_enter_trade(open_positions)
        if not allowed:
            logger.warning(f"Entry blocked: {reason}")
            return

        # Gather market data
        strike_step = self._instrument_config.get("strike_step", 50)
        atm_strike  = self.data_feed.get_atm_strike(strike_step=strike_step)
        expiry      = self.data_feed.get_nearest_expiry()
        spot        = self.data_feed.get_spot_price()
        # Use fallback if spot is 0 (market data not yet available)
        if not spot or spot < 1000:
            spot = getattr(self.risk, "_last_known_spot", 23700.0)
            logger.warning(f"Spot=0 from feed, using fallback Rs{spot:,.0f}")
        self.strategy.strike_step = strike_step

        # Store spot for margin engine
        self.risk._last_known_spot = spot

        # Margin-aware lot and strategy selection
        mc = self.risk.margin
        current_margin = mc.compute_current_margin_used(open_positions, spot)

        # Always use margin-aware lot sizing
        preferred  = self._capital_config.get("preferred_strategy", self.strategy.name)
        auto_select= self._capital_config.get("auto_select_strategy", True)

        if auto_select and spot > 1000:
            best_strat, lots = mc.pick_best_strategy(spot, current_margin, preferred)
            if best_strat == "none" or lots == 0:
                # Fallback: just use 1 lot of preferred strategy
                logger.warning("MarginSelector returned none — forcing 1 lot fallback")
                lots = 1
                best_strat = self.strategy.name
            if best_strat != self.strategy.name:
                logger.info(f"Strategy switched: {self.strategy.name} -> {best_strat}")
                from strategy.strategy import create_strategy
                new_cfg = dict(self.strategy.config)
                new_cfg["name"] = best_strat
                self.strategy = create_strategy(new_cfg)
                self.strategy.strike_step = strike_step
        else:
            lots = self._instrument_config.get("lots", 1)

        # Check estimate
        est = mc.estimate(self.strategy.name, lots, spot, current_margin)
        logger.info(
            f"Margin estimate: strategy={self.strategy.name} lots={lots} "
            f"margin_required=Rs{est.margin_required:,.0f} "
            f"utilisation={est.utilisation_pct:.1f}% "
            f"fits={est.fits_within_limit}"
        )

        if not est.fits_within_limit:
            logger.warning("Entry blocked: margin estimate exceeds limit")
            return

        logger.info(f"Entering {self.strategy.name} | ATM={atm_strike} | Expiry={expiry} | Lots={lots}")

        legs = self.strategy.get_legs(atm_strike, expiry, self.data_feed)
        if not legs:
            logger.error("No legs returned by strategy — aborting entry")
            return

        # Place orders for each leg
        placed = 0
        for contract, side in legs:
            ltp   = self.data_feed.get_ltp(contract.trading_symbol)
            order = self.om.market_order(contract, side, lots, ltp)
            if order:
                placed += 1
                logger.info(f"Leg placed: {side.value} {contract.trading_symbol} @ Rs{ltp:.2f}")

        if placed > 0:
            with self._lock:
                self._entered_today = True
                self._status = self.STATUS_ACTIVE
            self.risk.record_trade_entry()
            logger.info(f"Strategy entered: {placed} legs | margin used: Rs{est.margin_after_trade:,.0f}")
        else:
            logger.error("No legs were successfully placed")

    def _refresh_positions(self) -> None:
        """Pull latest prices and update all open positions."""
        open_positions = self.om.get_open_positions()
        symbols = [p.contract.trading_symbol for p in open_positions if p.contract]
        if symbols:
            price_map = self.data_feed.refresh_ltp_cache(symbols)
            self.om.update_position_prices(price_map)

    def _square_off_all(self, reason: str = "") -> None:
        """Exit all open positions (called by risk engine or at exit time)."""
        open_positions = self.om.get_open_positions()
        if not open_positions:
            return
        logger.info(f"Squaring off {len(open_positions)} positions. Reason: {reason}")
        for pos in open_positions:
            ltp = self.data_feed.get_ltp(pos.contract.trading_symbol) if pos.contract else 0.0
            self.om.exit_position(pos, ltp)
        with self._lock:
            self._status = self.STATUS_HALTED if reason else self.STATUS_CLOSED

    def _exit_single_position(self, position: Position) -> None:
        """Close a single position when its individual SL/target is hit."""
        if position.contract:
            ltp = self.data_feed.get_ltp(position.contract.trading_symbol)
            self.om.exit_position(position, ltp)

    def _on_halt(self, reason: str) -> None:
        """Handle bot halt signal from risk engine."""
        with self._lock:
            self._status = self.STATUS_HALTED
        logger.critical(f"Bot HALTED: {reason}")

    @staticmethod
    def _parse_time(time_str: str) -> dtime:
        """Parse 'HH:MM' string to datetime.time object."""
        parts = time_str.split(":")
        return dtime(int(parts[0]), int(parts[1]))
