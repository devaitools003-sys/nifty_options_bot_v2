"""
breeze_client.py
================
ICICIdirect Breeze API client.

Two modes
---------
1. LIVE DATA   : Real Nifty spot + options chain LTP from Breeze websocket/REST
2. PAPER ORDERS: All orders simulated locally — zero real money at risk

Install SDK:  pip install breeze-connect

Getting your credentials
------------------------
1. Log in to https://api.icicidirect.com/
2. Go to My Apps → Create App
3. Note your API Key
4. Each morning run get_session_token() once to get today's session token
   (Breeze session tokens expire daily at market close)

Usage
-----
    from broker.breeze_client import BreezeDataClient
    client = BreezeDataClient(api_key="...", api_secret="...", session_token="...")
    client.login()
    spot = client.get_nifty_spot()
    chain = client.get_options_chain(expiry_date="31-01-2025")
"""

from __future__ import annotations

import time
import uuid
import threading
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple

from core.models import (
    Order, OrderSide, OrderStatus, OrderType,
    Position, PositionStatus, OptionContract, OptionType, ProductType
)
from utils.logger import get_logger

logger = get_logger("breeze")


# ─── Breeze Data + Paper Order Client ────────────────────────────────────────

class BreezeDataClient:
    """
    Fetches REAL market data from ICICIdirect Breeze API.
    Executes orders in PAPER mode only (no real trades).

    Parameters
    ----------
    api_key      : Breeze API key from developer portal
    api_secret   : Breeze API secret
    session_token: Daily session token (refresh each morning)
    """

    EXCHANGE_NFO = "NFO"
    EXCHANGE_NSE = "NSE"
    INDEX_TOKEN  = "NIFTY"

    # Class-level REST call budget (basic plan ~100/day)
    _api_calls_today: int = 0
    _MAX_REST_CALLS:  int = 80
    _FALLBACK_SPOT:   float = 23850.0

    def __init__(self, api_key: str, api_secret: str, session_token: str,
                 slippage_pct: float = 0.1):
        self.api_key       = api_key
        self.api_secret    = api_secret
        self.session_token = session_token
        self.slippage_pct  = slippage_pct

        self._breeze       = None          # breeze-connect BreezeConnect instance
        self._connected    = False
        self._lock         = threading.Lock()

        # Paper order book
        self._paper_orders:    Dict[str, Order]    = {}
        self._paper_positions: Dict[str, Position] = {}

        # LTP cache (filled by websocket callbacks)
        self._ltp_cache:   Dict[str, float] = {}
        self._spot_price:  float = 0.0

        logger.info("BreezeDataClient initialised (LIVE DATA / PAPER ORDERS)")

    # ─── Login ────────────────────────────────────────────────────────────────

    def login(self) -> bool:
        """
        Connect to Breeze API using today's session token.
        Call this once at bot startup each morning.
        """
        try:
            from breeze_connect import BreezeConnect
            self._breeze = BreezeConnect(api_key=self.api_key)
            self._breeze.generate_session(
                api_secret=self.api_secret,
                session_token=self.session_token,
            )
            self._connected = True
            logger.info("Breeze API: connected successfully")

            # Seed spot price immediately
            self._spot_price = self._fetch_spot_from_api()
            logger.info(f"Breeze API: Nifty spot = ₹{self._spot_price:,.2f}")
            return True

        except ImportError:
            logger.error("breeze-connect not installed. Run: pip install breeze-connect")
            return False
        except Exception as e:
            logger.error(f"Breeze login failed: {e}")
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ─── Spot Price ───────────────────────────────────────────────────────────

    # Fallback spot when market is closed (last known Nifty level)
    _FALLBACK_SPOT: float = 23850.0

    def get_nifty_spot(self) -> float:
        """Return current Nifty 50 index spot price."""
        try:
            price = self._fetch_spot_from_api()
            if price and price > 1000:   # valid market price
                with self._lock:
                    self._spot_price = price
                    BreezeDataClient._FALLBACK_SPOT = price  # update fallback
                return price
            # Market closed — return last known price
            cached = self._spot_price or BreezeDataClient._FALLBACK_SPOT
            logger.debug(f"Market closed / spot=0 — using cached price Rs{cached:,.2f}")
            return cached
        except Exception as e:
            logger.warning(f"Spot fetch failed, using cache: {e}")
            return self._spot_price or BreezeDataClient._FALLBACK_SPOT

    def _fetch_spot_from_api(self) -> float:
        """Call Breeze REST to get Nifty 50 LTP."""
        resp = self._breeze.get_quotes(
            stock_code="NIFTY",
            exchange_code="NSE",
            expiry_date="",
            product_type="cash",
            right="",
            strike_price="",
        )
        data = resp.get("Success", [{}])
        if data and data[0]:
            ltp = float(data[0].get("ltp", 0))
            return ltp
        return 0.0

    def get_atm_strike(self, strike_step: int = 50) -> int:
        """Return nearest Nifty ATM strike using fallback when market is closed."""
        spot = self.get_nifty_spot()
        if spot < 1000:
            spot = BreezeDataClient._FALLBACK_SPOT
        return int(round(spot / strike_step) * strike_step)

    # ─── Expiry Helpers ───────────────────────────────────────────────────────

    def get_nearest_expiry(self) -> str:
        """
        Return the nearest Nifty weekly expiry as DD-MMM-YYYY (Breeze format).

        SEBI Rule (effective April 5, 2025):
        - Weekly expiry  : Every TUESDAY  (weekday=1)
        - Monthly expiry : Last MONDAY of the month (weekday=0)

        This method returns the nearest TUESDAY expiry.
        If today IS Tuesday and market is still open (before 15:30),
        return today. If past 15:30 on Tuesday, roll to next Tuesday.
        """
        today     = date.today()
        now_hour  = datetime.now().hour
        now_min   = datetime.now().minute

        # Tuesday = weekday 1
        days_ahead = (1 - today.weekday()) % 7

        # If today is Tuesday but market has closed, go to next Tuesday
        if days_ahead == 0 and (now_hour > 15 or (now_hour == 15 and now_min >= 30)):
            days_ahead = 7

        expiry = today + timedelta(days=days_ahead)
        return expiry.strftime("%d-%b-%Y")   # e.g. 13-May-2026

    def get_nearest_expiry_iso(self) -> str:
        """Return nearest Tuesday expiry as YYYY-MM-DD (internal format)."""
        today    = date.today()
        now_hour = datetime.now().hour
        now_min  = datetime.now().minute

        days_ahead = (1 - today.weekday()) % 7
        if days_ahead == 0 and (now_hour > 15 or (now_hour == 15 and now_min >= 30)):
            days_ahead = 7

        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    def get_monthly_expiry(self) -> str:
        """
        Return the last Monday of the current month as DD-MMM-YYYY.
        Used for monthly/quarterly/half-yearly contracts.
        """
        import calendar
        today = date.today()
        # Find last Monday of current month
        last_day = calendar.monthrange(today.year, today.month)[1]
        last_date = date(today.year, today.month, last_day)
        # Walk back to Monday (weekday=0)
        while last_date.weekday() != 0:
            last_date -= timedelta(days=1)
        # If already past, go to next month
        if last_date < today:
            if today.month == 12:
                last_date = date(today.year + 1, 1, 1)
            else:
                last_date = date(today.year, today.month + 1, 1)
            last_day = calendar.monthrange(last_date.year, last_date.month)[1]
            last_date = date(last_date.year, last_date.month, last_day)
            while last_date.weekday() != 0:
                last_date -= timedelta(days=1)
        return last_date.strftime("%d-%b-%Y")

    # ─── Options Chain ────────────────────────────────────────────────────────

    def get_options_chain(
        self,
        expiry_date: str,          # DD-MMM-YYYY (Breeze format e.g. 15-May-2026)
        atm_strike: int,
        width: int = 5,
        strike_step: int = 50,
    ) -> Dict[str, dict]:
        """
        Fetch full options chain from Breeze for strikes ATM ± width.

        Returns dict: trading_symbol → {ltp, oi, volume, iv, ...}
        """
        # Guard: check REST call budget
        calls_needed = (width * 2 + 1) * 2
        if BreezeDataClient._api_calls_today + calls_needed > BreezeDataClient._MAX_REST_CALLS:
            logger.warning(
                f"REST call budget nearly exhausted "
                f"({BreezeDataClient._api_calls_today}/{BreezeDataClient._MAX_REST_CALLS}). "
                f"Returning cached chain only."
            )
            # Return contracts built from existing cache
            return self._chain_from_cache(atm_strike, width, strike_step,
                                          expiry_date, self._breeze_to_iso(expiry_date))

        chain_data = {}
        expiry_iso  = self._breeze_to_iso(expiry_date)

        # Try Breeze option_chain API first (1 call for all strikes)
        try:
            resp = self._breeze.get_option_chain_quotes(
                stock_code="NIFTY",
                exchange_code=self.EXCHANGE_NFO,
                product_type="options",
                expiry_date=expiry_date,
                right="Call",
                strike_price=str(atm_strike),
            )
            BreezeDataClient._api_calls_today += 1
            data = resp.get("Success", []) or []
            if data:
                for rec in data:
                    try:
                        strike   = int(float(rec.get("strike_price", 0)))
                        if abs(strike - atm_strike) > width * strike_step:
                            continue
                        for right_key, opt_type in [("call", OptionType.CALL), ("put", OptionType.PUT)]:
                            ltp = float(rec.get("ltp", 0))
                            sym = self._make_symbol("NIFTY", expiry_iso, strike, opt_type)
                            chain_data[sym] = {
                                "ltp": ltp, "oi": rec.get("open_interest", 0),
                                "volume": 0, "iv": rec.get("implied_volatility", 0),
                                "bid": ltp*0.99, "ask": ltp*1.01,
                                "strike": strike, "right": right_key,
                            }
                            with self._lock:
                                self._ltp_cache[sym] = ltp
                    except Exception:
                        continue
                logger.info(f"Chain via get_option_chain_quotes: {len(chain_data)} contracts")
                logger.info(f"REST calls today: {BreezeDataClient._api_calls_today}/{BreezeDataClient._MAX_REST_CALLS}")
                return chain_data
        except Exception as e:
            logger.debug(f"get_option_chain_quotes not available: {e}")

        # Fallback: individual get_quotes per strike (uses more API calls)
        for offset in range(-width, width + 1):
            strike = atm_strike + offset * strike_step
            for right in ("call", "put"):
                try:
                    resp = self._breeze.get_quotes(
                        stock_code="NIFTY",
                        exchange_code=self.EXCHANGE_NFO,
                        expiry_date=expiry_date,
                        product_type="options",
                        right=right,
                        strike_price=str(float(strike)),
                    )
                    BreezeDataClient._api_calls_today += 1
                    data = resp.get("Success", []) or []
                    if data and data[0]:
                        rec      = data[0]
                        opt_type = OptionType.CALL if right == "call" else OptionType.PUT
                        sym      = self._make_symbol("NIFTY", expiry_iso, strike, opt_type)
                        ltp      = float(rec.get("ltp", 0))
                        chain_data[sym] = {
                            "ltp": ltp, "oi": rec.get("open_interest", 0),
                            "volume": rec.get("total_quantity_traded", 0),
                            "iv": rec.get("implied_volatility", 0),
                            "bid": float(rec.get("best_bid_price", ltp*0.99)),
                            "ask": float(rec.get("best_offer_price", ltp*1.01)),
                            "strike": strike, "right": right,
                        }
                        with self._lock:
                            self._ltp_cache[sym] = ltp
                except Exception as e:
                    logger.warning(f"Chain fetch failed {strike}{right}: {e}")

        logger.info(
            f"Options chain fetched: {len(chain_data)} contracts "
            f"| REST calls today: {BreezeDataClient._api_calls_today}/{BreezeDataClient._MAX_REST_CALLS}"
        )
        return chain_data

    def _chain_from_cache(self, atm_strike, width, strike_step,
                          expiry_breeze, expiry_iso) -> dict:
        """Build chain dict from websocket cache without REST calls."""
        chain_data = {}
        for offset in range(-width, width + 1):
            strike = atm_strike + offset * strike_step
            for right in ("call", "put"):
                opt_type = OptionType.CALL if right == "call" else OptionType.PUT
                sym  = self._make_symbol("NIFTY", expiry_iso, strike, opt_type)
                ltp  = self._ltp_cache.get(sym, 0.0)
                chain_data[sym] = {
                    "ltp": ltp, "oi": 0, "volume": 0, "iv": 0,
                    "bid": ltp * 0.99, "ask": ltp * 1.01,
                    "strike": strike, "right": right,
                }
        return chain_data

    def build_option_contracts(
        self,
        expiry_date_breeze: str,   # DD-MM-YYYY
        atm_strike: int,
        width: int = 5,
        strike_step: int = 50,
        lot_size: int = 50,
    ) -> Dict[str, OptionContract]:
        """
        Build OptionContract objects for all strikes in chain.
        Populates LTP cache as a side effect.
        """
        chain_raw = self.get_options_chain(
            expiry_date_breeze, atm_strike, width, strike_step
        )
        expiry_iso = self._breeze_to_iso(expiry_date_breeze)
        contracts: Dict[str, OptionContract] = {}

        for sym, data in chain_raw.items():
            opt_type = (OptionType.CALL if data["right"] == "call"
                        else OptionType.PUT)
            contract = OptionContract(
                index="NIFTY",
                expiry=expiry_iso,
                strike=data["strike"],
                option_type=opt_type,
                trading_symbol=sym,
                instrument_token=sym,
                lot_size=lot_size,
            )
            contracts[sym] = contract

        return contracts

    # ─── LTP ─────────────────────────────────────────────────────────────────

    def get_ltp(self, symbol: str) -> float:
        """
        Return LTP from websocket cache only.
        Never hits REST API to protect daily call limit.
        """
        with self._lock:
            return self._ltp_cache.get(symbol, 0.0)

    def _fetch_ltp_api(self, symbol: str) -> float:
        """Parse symbol back to Breeze params and fetch LTP."""
        try:
            strike, right, expiry_breeze = self._parse_symbol(symbol)
            resp = self._breeze.get_quotes(
                stock_code="NIFTY",
                exchange_code=self.EXCHANGE_NFO,
                expiry_date=expiry_breeze,
                product_type="options",
                right=right,
                strike_price=str(strike),
            )
            data = resp.get("Success", [{}])
            ltp = float(data[0].get("ltp", 0)) if data else 0.0
            with self._lock:
                self._ltp_cache[symbol] = ltp
            return ltp
        except Exception as e:
            logger.warning(f"LTP fetch failed for {symbol}: {e}")
            return self._ltp_cache.get(symbol, 0.0)

    def refresh_ltp_batch(self, symbols: List[str]) -> Dict[str, float]:
        """
        Return LTP from websocket cache ONLY — zero REST calls.
        Websocket keeps cache fresh in real time.
        """
        with self._lock:
            return {sym: self._ltp_cache.get(sym, 0.0) for sym in symbols}

    # ─── Websocket Streaming ──────────────────────────────────────────────────

    def start_websocket_feed(self, symbols: List[str]) -> None:
        """
        Subscribe to Breeze websocket for real-time LTP ticks.
        Automatically updates _ltp_cache and _spot_price.
        """
        if not self._connected:
            logger.error("Cannot start websocket — not connected")
            return

        try:
            # Set tick callback
            self._breeze.on_ticks = self._on_tick

            # Try all known Breeze websocket connect methods
            connected = False
            for method_name in ["ws_connect", "connect", "websocket_connect"]:
                if hasattr(self._breeze, method_name):
                    try:
                        getattr(self._breeze, method_name)()
                        connected = True
                        logger.info(f"Websocket connected via {method_name}()")
                        break
                    except Exception as e:
                        logger.warning(f"{method_name}() failed: {e}")

            if not connected:
                logger.warning("Websocket unavailable — will use REST polling as fallback")
                return

            # Subscribe to Nifty spot index
            try:
                self._breeze.subscribe_feeds(
                    exchange_code="NSE",
                    stock_code="NIFTY",
                    product_type="cash",
                    expiry_date="",
                    strike_price="",
                    right="",
                    get_exchange_quotes=True,
                    get_market_depth=False,
                )
            except Exception as e:
                logger.warning(f"Nifty index subscribe failed: {e}")

            # Subscribe to each option contract
            subscribed = 0
            for sym in symbols:
                try:
                    strike, right, expiry_breeze = self._parse_symbol(sym)
                    self._breeze.subscribe_feeds(
                        exchange_code=self.EXCHANGE_NFO,
                        stock_code="NIFTY",
                        product_type="options",
                        expiry_date=expiry_breeze,
                        strike_price=str(strike),
                        right=right,
                        get_exchange_quotes=True,
                        get_market_depth=False,
                    )
                    subscribed += 1
                except Exception as e:
                    logger.warning(f"Subscribe failed for {sym}: {e}")

            logger.info(f"Websocket subscribed: {subscribed} contracts + Nifty index")

        except Exception as e:
            logger.error(f"Websocket start failed: {e}")

    def _on_tick(self, ticks: dict) -> None:
        """Callback fired by Breeze on every price tick."""
        try:
            stock  = ticks.get("stock_code", "")
            ltp    = float(ticks.get("last", 0))
            right  = ticks.get("right", "")
            strike = ticks.get("strike_price", "")
            expiry = ticks.get("expiry_date", "")

            if stock == "NIFTY" and not right:
                # Index tick
                with self._lock:
                    self._spot_price = ltp
            elif stock == "NIFTY" and right:
                # Option tick — rebuild our symbol key
                opt_type = OptionType.CALL if right.lower() in ("call","c") else OptionType.PUT
                expiry_iso = self._breeze_to_iso(expiry)
                sym = self._make_symbol("NIFTY", expiry_iso, int(float(strike)), opt_type)
                with self._lock:
                    self._ltp_cache[sym] = ltp
        except Exception as e:
            logger.debug(f"Tick parse error: {e}")

    def stop_websocket(self) -> None:
        """Disconnect Breeze websocket."""
        try:
            if self._breeze:
                self._breeze.disconnect()
            logger.info("Websocket disconnected")
        except Exception as e:
            logger.warning(f"Websocket disconnect error: {e}")

    # ─── Paper Order Execution ────────────────────────────────────────────────

    def place_order(self, order: Order) -> Order:
        """
        PAPER execution — fills at current LTP ± slippage.
        No real order is sent to Breeze.
        """
        broker_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"
        order.broker_order_id = broker_id

        ltp = self.get_ltp(order.symbol) or 100.0
        slip = ltp * (self.slippage_pct / 100)

        if order.order_type == OrderType.MARKET:
            fill = (ltp + slip) if order.side == OrderSide.BUY else (ltp - slip)
            order.filled_price    = round(max(0.05, fill), 2)
            order.filled_quantity = order.quantity
            order.status          = OrderStatus.COMPLETE
            logger.info(
                f"PAPER FILL: {order.side.value} {order.quantity} "
                f"{order.symbol} @ ₹{order.filled_price}"
            )
        else:
            order.status = OrderStatus.OPEN

        with self._lock:
            self._paper_orders[broker_id] = order
        return order

    def cancel_order(self, broker_order_id: str) -> bool:
        with self._lock:
            if broker_order_id in self._paper_orders:
                self._paper_orders[broker_order_id].status = OrderStatus.CANCELLED
                return True
        return False

    def modify_order(self, broker_order_id: str, price: float, quantity: int) -> bool:
        with self._lock:
            if broker_order_id in self._paper_orders:
                o = self._paper_orders[broker_order_id]
                o.price    = price
                o.quantity = quantity
                return True
        return False

    def get_order_status(self, broker_order_id: str) -> Order:
        with self._lock:
            return self._paper_orders.get(broker_order_id, Order())

    def get_positions(self) -> List[Position]:
        with self._lock:
            return list(self._paper_positions.values())

    # ─── Symbol Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _make_symbol(index: str, expiry_iso: str, strike: int,
                     opt_type: OptionType) -> str:
        """Build internal trading symbol: NIFTY25JAN24500CE"""
        dt = datetime.strptime(expiry_iso, "%Y-%m-%d")
        return f"{index}{dt.strftime('%d%b%y').upper()}{strike}{opt_type.value}"

    @staticmethod
    def _breeze_to_iso(breeze_date: str) -> str:
        """Convert Breeze date (DD-MMM-YYYY or DD-MM-YYYY) to YYYY-MM-DD."""
        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(breeze_date, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return breeze_date

    @staticmethod
    def _iso_to_breeze(iso_date: str) -> str:
        """Convert YYYY-MM-DD to Breeze format DD-MMM-YYYY (e.g. 15-May-2026)."""
        try:
            return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d-%b-%Y")
        except Exception:
            return iso_date

    def _parse_symbol(self, symbol: str) -> Tuple[int, str, str]:
        """
        Reverse-parse a trading symbol back to (strike, right, expiry_breeze).
        E.g. NIFTY30JAN2524500CE → (24500, 'call', '30-01-2025')
        """
        opt_type_str = symbol[-2:]   # CE or PE
        right = "call" if opt_type_str == "CE" else "put"
        strike = int(symbol[-7:-2])
        date_str = symbol[5:-7]      # e.g. 30JAN25
        try:
            dt = datetime.strptime(date_str, "%d%b%y")
            expiry_breeze = dt.strftime("%d-%m-%Y")
        except Exception:
            expiry_breeze = "01-01-2025"
        return strike, right, expiry_breeze
