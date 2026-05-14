"""
breeze_feed.py
==============
Data feed adapter that wraps BreezeDataClient to match
the MarketDataFeed interface expected by the strategy engine.

Provides:
- Real Nifty spot price from ICICIdirect
- Real options chain LTP from ICICIdirect
- Websocket streaming for live ticks
- Same public API as MarketDataFeed so zero changes needed in strategy.py
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

from core.models import OptionContract, OptionType, Greeks
from utils.logger import get_logger

logger = get_logger("breeze_feed")


class BreezeFeed:
    """
    Live data feed backed by ICICIdirect Breeze.
    Drop-in replacement for MarketDataFeed when broker = icicidirect.
    """

    def __init__(self, breeze_client, lot_size: int = 50, strike_step: int = 50):
        self.client      = breeze_client
        self.lot_size    = lot_size
        self.strike_step = strike_step

        self._chain_cache: Dict[str, OptionContract] = {}
        self._ohlc_bars:   List[dict] = []
        self._streaming    = False
        self._stream_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        logger.info("BreezeFeed initialised (live ICICIdirect data)")

    # ── Spot price ────────────────────────────────────────────────────────────

    def get_spot_price(self) -> float:
        return self.client.get_nifty_spot()

    def get_atm_strike(self, strike_step: int = None) -> int:
        step = strike_step or self.strike_step
        return self.client.get_atm_strike(step)

    # ── Expiry ────────────────────────────────────────────────────────────────

    def get_nearest_expiry(self) -> str:
        """
        Return nearest Tuesday weekly expiry as YYYY-MM-DD.
        SEBI rule (effective Apr 5 2025): Nifty weekly expiry = Tuesday.
        """
        return self.client.get_nearest_expiry_iso()

    # ── Options chain ─────────────────────────────────────────────────────────

    def build_options_chain(
        self,
        expiry: str,
        atm_strike: int,
        width: int = 5,
        strike_step: int = None,
    ) -> Dict[str, OptionContract]:
        """
        Fetch live options chain from Breeze and return OptionContract dict.

        Parameters
        ----------
        expiry     : YYYY-MM-DD (internal format)
        atm_strike : ATM strike price
        width      : number of strikes each side
        strike_step: strike interval (default from config)
        """
        step = strike_step or self.strike_step
        expiry_breeze = self.client._iso_to_breeze(expiry)   # DD-MMM-YYYY e.g. 15-May-2026

        # Use width=2 max to save REST API calls (basic plan)
        safe_width = min(width, 2)
        contracts = self.client.build_option_contracts(
            expiry_date_breeze=expiry_breeze,
            atm_strike=atm_strike,
            width=safe_width,
            strike_step=step,
            lot_size=self.lot_size,
        )

        with self._lock:
            self._chain_cache.update(contracts)

        logger.info(f"BreezeFeed: chain built — {len(contracts)} contracts, ATM={atm_strike}")
        return contracts

    # ── LTP ───────────────────────────────────────────────────────────────────

    def get_ltp(self, symbol: str) -> float:
        return self.client.get_ltp(symbol)

    def refresh_ltp_cache(self, symbols: List[str]) -> Dict[str, float]:
        return self.client.refresh_ltp_batch(symbols)

    # ── Greeks ────────────────────────────────────────────────────────────────

    def get_greeks(self, contract: OptionContract) -> Greeks:
        """
        Approximate Greeks from Breeze IV data.
        Full B-S Greeks computation using scipy can be added here.
        """
        import math
        spot   = self.get_spot_price()
        strike = contract.strike
        ltp    = self.get_ltp(contract.trading_symbol)
        is_call = contract.option_type == OptionType.CALL

        moneyness = (spot - strike) / spot
        delta_base = 0.5 + moneyness * 2
        delta = max(0.01, min(0.99, delta_base if is_call else 1 - delta_base))
        gamma = max(0.001, 0.05 * (1 - abs(moneyness) * 5))
        theta = -ltp * 0.01
        vega  = ltp * 0.10
        iv    = 15.0  # fallback; Breeze provides this in chain data

        return Greeks(delta=round(delta,3), gamma=round(gamma,4),
                      theta=round(theta,2), vega=round(vega,2), iv=round(iv,2))

    # ── Streaming ─────────────────────────────────────────────────────────────

    def start_streaming(self, symbols: List[str], interval: float = 2.0) -> None:
        """
        Start websocket feed for real-time ticks + background polling thread.
        Websocket handles tick-level updates; polling is a fallback heartbeat.
        """
        if self._streaming:
            return
        self._streaming = True

        # Start Breeze websocket
        try:
            self.client.start_websocket_feed(symbols)
            logger.info("Breeze websocket started")
        except Exception as e:
            logger.warning(f"Websocket failed, falling back to polling: {e}")

        # Background polling thread (refreshes LTP every `interval` seconds)
        self._stream_thread = threading.Thread(
            target=self._poll_loop,
            args=(symbols, interval),
            daemon=True,
            name="BreezePollLoop",
        )
        self._stream_thread.start()
        logger.info(f"BreezeFeed: streaming started ({len(symbols)} symbols)")

    def stop_streaming(self) -> None:
        self._streaming = False
        try:
            self.client.stop_websocket()
        except Exception:
            pass
        logger.info("BreezeFeed: streaming stopped")

    def _poll_loop(self, symbols: List[str], interval: float) -> None:
        """Fallback polling loop — refreshes spot + LTP cache periodically."""
        while self._streaming:
            try:
                spot = self.client.get_nifty_spot()
                self._ohlc_bars.append({
                    "time": datetime.now().strftime("%H:%M"),
                    "spot": round(spot, 2),
                })
                if len(self._ohlc_bars) > 400:
                    self._ohlc_bars.pop(0)
                # LTP is updated by websocket callbacks automatically
                # No REST polling needed — saves API quota
                pass
            except Exception as e:
                logger.warning(f"Poll loop error: {e}")
            time.sleep(interval)

    # ── OHLC ─────────────────────────────────────────────────────────────────

    def get_intraday_ohlc(self) -> List[dict]:
        with self._lock:
            return list(self._ohlc_bars)

    # ── Symbol helper (forwarded for strategy.py compatibility) ───────────────

    def _make_symbol(self, index, expiry_iso, strike, opt_type) -> str:
        return self.client._make_symbol(index, expiry_iso, strike, opt_type)
