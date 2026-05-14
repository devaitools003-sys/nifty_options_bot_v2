"""
data_feed.py
============
Market data layer for Nifty options.
Handles options chain fetching, LTP polling, and OHLC history.

In paper-trading mode, simulates realistic price movements.
In live mode, delegates to the broker client.
"""

from __future__ import annotations

import random
import time
import threading
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple

from core.models import OptionContract, OptionType, Greeks
from utils.logger import get_logger

logger = get_logger("data_feed")


class MarketDataFeed:
    """
    Provides real-time and historical market data for Nifty options.

    Responsibilities
    ----------------
    - Fetch current Nifty spot price
    - Build the options chain (CE + PE across strikes)
    - Provide LTP (last traded price) for any contract
    - Compute or fetch Greeks (delta, gamma, theta, vega, IV)
    - Stream OHLC bars for charting
    """

    def __init__(self, broker_client=None, paper_trading: bool = True, lot_size: int = 50):
        self.broker_client = broker_client
        self.paper_trading = paper_trading
        self.lot_size = lot_size

        # ── Internal price cache ─────────────────────────────
        self._spot_price: float = 24_500.0        # seed value for paper mode
        self._ltp_cache: Dict[str, float] = {}    # symbol → LTP
        self._chain_cache: Dict[str, OptionContract] = {}

        # ── Streaming state ──────────────────────────────────
        self._streaming = False
        self._stream_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # ── OHLC history (intraday 1-min bars) ───────────────
        self._ohlc_bars: List[dict] = []

        logger.info(f"MarketDataFeed initialised (paper={paper_trading})")

    # ── Spot Price ────────────────────────────────────────────

    def get_spot_price(self) -> float:
        """Return current Nifty spot price."""
        if self.paper_trading:
            return self._spot_price
        try:
            return self.broker_client.get_ltp("NSE", "NIFTY 50")
        except Exception as e:
            logger.error(f"Spot price fetch failed: {e}")
            return self._spot_price

    def get_atm_strike(self, strike_step: int = 50) -> int:
        """Round spot price to nearest Nifty strike."""
        spot = self.get_spot_price()
        return int(round(spot / strike_step) * strike_step)

    # ── Options Chain ─────────────────────────────────────────

    def get_nearest_expiry(self) -> str:
        """
        Return the nearest weekly Nifty expiry date as YYYY-MM-DD.

        SEBI rule (effective April 5, 2025):
        - Weekly expiry  : Every TUESDAY (weekday=1)
        - Monthly expiry : Last MONDAY of the month
        """
        today    = date.today()
        now_hour = datetime.now().hour
        now_min  = datetime.now().minute
        # Tuesday = weekday 1
        days_ahead = (1 - today.weekday()) % 7
        # If today is Tuesday and market closed, roll to next Tuesday
        if days_ahead == 0 and (now_hour > 15 or (now_hour == 15 and now_min >= 30)):
            days_ahead = 7
        expiry = today + timedelta(days=days_ahead)
        return expiry.strftime("%Y-%m-%d")

    def build_options_chain(
        self,
        expiry: str,
        atm_strike: int,
        width: int = 5,
        strike_step: int = 50,
    ) -> Dict[str, OptionContract]:
        """
        Build a dictionary of OptionContract objects for ±width strikes
        around ATM for both CE and PE.

        Parameters
        ----------
        expiry      : expiry date string YYYY-MM-DD
        atm_strike  : ATM strike price
        width       : number of strikes on each side of ATM
        strike_step : distance between strikes (₹50 for Nifty)
        """
        chain: Dict[str, OptionContract] = {}

        for offset in range(-width, width + 1):
            strike = atm_strike + offset * strike_step
            for opt_type in (OptionType.CALL, OptionType.PUT):
                symbol = self._make_symbol("NIFTY", expiry, strike, opt_type)
                contract = OptionContract(
                    index="NIFTY",
                    expiry=expiry,
                    strike=strike,
                    option_type=opt_type,
                    trading_symbol=symbol,
                    instrument_token=f"TOKEN_{strike}_{opt_type.value}",
                    lot_size=self.lot_size,
                )
                chain[symbol] = contract

        with self._lock:
            self._chain_cache.update(chain)

        logger.debug(f"Options chain built: {len(chain)} contracts around {atm_strike}")
        return chain

    def _make_symbol(self, index: str, expiry: str, strike: int, opt_type: OptionType) -> str:
        """
        Construct the broker trading symbol.
        Format: NIFTY25JAN24000CE (Upstox-style)
        """
        dt = datetime.strptime(expiry, "%Y-%m-%d")
        date_str = dt.strftime("%d%b%y").upper()
        return f"{index}{date_str}{strike}{opt_type.value}"

    # ── LTP & Quotes ─────────────────────────────────────────

    def get_ltp(self, symbol: str) -> float:
        """Return last traded price for a symbol."""
        if self.paper_trading:
            return self._simulate_ltp(symbol)
        try:
            price = self.broker_client.get_ltp_option(symbol)
            with self._lock:
                self._ltp_cache[symbol] = price
            return price
        except Exception as e:
            logger.warning(f"LTP fetch failed for {symbol}: {e}")
            return self._ltp_cache.get(symbol, 0.0)

    def _simulate_ltp(self, symbol: str) -> float:
        """
        Generate a realistic simulated option price based on symbol metadata.
        Uses a simplified Black-Scholes-like seed based on moneyness.
        """
        # Extract strike from symbol (last 5 digits before CE/PE)
        opt_suffix = symbol[-2:]
        strike_str = symbol[-7:-2]
        try:
            strike = int(strike_str)
        except ValueError:
            strike = int(self._spot_price)

        spot = self._spot_price
        moneyness = abs(spot - strike) / spot

        # Base premium: higher for ATM, lower for OTM
        base = max(5.0, 300.0 * (1 - moneyness * 8))

        # Add random walk noise
        with self._lock:
            prev = self._ltp_cache.get(symbol, base)
        noise = prev * random.gauss(0, 0.005)   # 0.5% std dev per tick
        price = max(0.5, prev + noise)

        with self._lock:
            self._ltp_cache[symbol] = round(price, 2)
        return round(price, 2)

    def refresh_ltp_cache(self, symbols: List[str]) -> Dict[str, float]:
        """Batch-refresh LTP for a list of symbols. Returns dict symbol→ltp."""
        result = {}
        for sym in symbols:
            result[sym] = self.get_ltp(sym)
        return result

    # ── Greeks ───────────────────────────────────────────────

    def get_greeks(self, contract: OptionContract) -> Greeks:
        """
        Fetch or compute Greeks for a contract.
        Paper mode: returns approximate values.
        Live mode: fetches from broker if available, else computes locally.
        """
        spot = self.get_spot_price()
        strike = contract.strike
        ltp = self.get_ltp(contract.trading_symbol)

        moneyness = (spot - strike) / spot
        is_call = contract.option_type == OptionType.CALL

        # Simplified approximation (not exact B-S)
        delta_base = 0.5 + moneyness * 2
        delta = max(0.01, min(0.99, delta_base if is_call else 1 - delta_base))
        gamma = max(0.001, 0.05 * (1 - abs(moneyness) * 5))
        theta = -ltp * 0.01   # 1% decay per day (rough)
        vega = ltp * 0.1
        iv = random.uniform(12.0, 22.0)  # paper: random IV band

        return Greeks(delta=round(delta, 3), gamma=round(gamma, 4),
                      theta=round(theta, 2), vega=round(vega, 2), iv=round(iv, 2))

    # ── Streaming ─────────────────────────────────────────────

    def start_streaming(self, symbols: List[str], interval: float = 1.0) -> None:
        """Start background thread to continuously refresh LTP and spot."""
        if self._streaming:
            return
        self._streaming = True
        self._stream_thread = threading.Thread(
            target=self._stream_loop,
            args=(symbols, interval),
            daemon=True,
            name="DataFeedStream",
        )
        self._stream_thread.start()
        logger.info(f"Data stream started for {len(symbols)} symbols")

    def stop_streaming(self) -> None:
        """Signal the streaming thread to stop."""
        self._streaming = False
        logger.info("Data stream stopped")

    def _stream_loop(self, symbols: List[str], interval: float) -> None:
        """Background streaming loop — updates LTP cache and spot price."""
        while self._streaming:
            try:
                # Simulate spot price random walk in paper mode
                if self.paper_trading:
                    with self._lock:
                        change = self._spot_price * random.gauss(0, 0.0003)
                        self._spot_price = max(10_000, self._spot_price + change)

                self.refresh_ltp_cache(symbols)

                # Record 1-min OHLC bar
                now = datetime.now()
                self._ohlc_bars.append({
                    "time": now.strftime("%H:%M"),
                    "spot": round(self._spot_price, 2),
                })

            except Exception as e:
                logger.error(f"Stream loop error: {e}")
            time.sleep(interval)

    # ── OHLC History ─────────────────────────────────────────

    def get_intraday_ohlc(self) -> List[dict]:
        """Return list of intraday spot price snapshots."""
        with self._lock:
            return list(self._ohlc_bars)
