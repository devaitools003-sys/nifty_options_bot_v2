"""
broker_client.py
================
Broker integration layer.

Architecture
------------
- BaseBrokerClient  : abstract interface every broker must implement
- UpstoxClient      : Upstox v2 API implementation
- PaperBrokerClient : simulated broker for paper trading / backtesting

All order placement calls return an Order dataclass with the broker's
assigned order ID populated.  Error handling and retry logic are baked in.
"""

from __future__ import annotations

import time
import uuid
import random
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from core.models import Order, OrderStatus, OrderType, OrderSide, Position, PositionStatus
from utils.logger import get_logger

logger = get_logger("broker")


# ── Base Interface ────────────────────────────────────────────

class BaseBrokerClient(ABC):
    """Abstract base class that every broker adapter must implement."""

    def __init__(self, api_key: str, api_secret: str, **kwargs):
        self.api_key = api_key
        self.api_secret = api_secret
        self._connected: bool = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @abstractmethod
    def login(self) -> bool:
        """Authenticate and obtain access token. Returns True on success."""

    @abstractmethod
    def place_order(self, order: Order) -> Order:
        """Submit an order to the broker. Returns updated Order with broker_order_id."""

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order. Returns True on success."""

    @abstractmethod
    def modify_order(self, broker_order_id: str, price: float, quantity: int) -> bool:
        """Modify an open limit/SL order."""

    @abstractmethod
    def get_order_status(self, broker_order_id: str) -> Order:
        """Fetch current status of an order."""

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """Return all open positions for the session."""

    @abstractmethod
    def get_ltp(self, exchange: str, symbol: str) -> float:
        """Get last traded price for a given symbol."""

    def _retry(self, func, retries: int = 3, delay: float = 2.0, *args, **kwargs):
        """Generic retry wrapper for broker API calls."""
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt}/{retries} failed: {e}")
                if attempt < retries:
                    time.sleep(delay)
        raise RuntimeError(f"All {retries} attempts failed. Last error: {last_error}")


# ── Upstox Client ─────────────────────────────────────────────

class UpstoxClient(BaseBrokerClient):
    """
    Upstox v2 REST API client.

    Reference: https://upstox.com/developer/api-documentation/

    NOTE: Requires `upstox-python-sdk` or direct REST calls.
    Install: pip install upstox-python-sdk
    """

    BASE_URL = "https://api.upstox.com/v2"

    def __init__(self, api_key: str, api_secret: str, redirect_uri: str, access_token: str = ""):
        super().__init__(api_key, api_secret)
        self.redirect_uri = redirect_uri
        self._access_token = access_token
        self._session_headers: Dict[str, str] = {}

    def login(self) -> bool:
        """
        Upstox OAuth2 login flow.
        If access_token is pre-set in config (from prior session), uses it directly.
        Otherwise prints auth URL for the user to complete in browser.
        """
        if self._access_token:
            self._session_headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            self._connected = True
            logger.info("Upstox: using pre-set access token")
            return True

        # Build OAuth2 authorization URL
        auth_url = (
            f"https://api.upstox.com/v2/login/authorization/dialog"
            f"?response_type=code&client_id={self.api_key}&redirect_uri={self.redirect_uri}"
        )
        logger.info(f"Visit this URL to authorize: {auth_url}")
        code = input("Enter the authorization code from the redirect URL: ").strip()

        # Exchange code for token
        import requests
        resp = requests.post(
            f"{self.BASE_URL}/login/authorization/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "code": code,
                "client_id": self.api_key,
                "client_secret": self.api_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            token_data = resp.json()
            self._access_token = token_data.get("access_token", "")
            self._session_headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            self._connected = True
            logger.info("Upstox: login successful")
            return True
        else:
            logger.error(f"Upstox login failed: {resp.text}")
            return False

    def place_order(self, order: Order) -> Order:
        """Place an order via Upstox Order API."""
        import requests
        payload = {
            "quantity": order.quantity,
            "product": order.product_type.value,
            "validity": "DAY",
            "price": order.price,
            "tag": order.order_id[:20],
            "instrument_token": order.symbol,
            "order_type": order.order_type.value,
            "transaction_type": order.side.value,
            "disclosed_quantity": 0,
            "trigger_price": order.trigger_price,
            "is_amo": False,
        }
        try:
            resp = requests.post(
                f"{self.BASE_URL}/order/place",
                json=payload,
                headers=self._session_headers,
                timeout=10,
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("status") == "success":
                order.broker_order_id = data["data"]["order_id"]
                order.status = OrderStatus.OPEN
                logger.info(f"Order placed: {order.broker_order_id} — {order.symbol}")
            else:
                order.status = OrderStatus.REJECTED
                order.message = data.get("message", "Unknown error")
                logger.error(f"Order rejected: {order.message}")
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.message = str(e)
            logger.error(f"Order placement exception: {e}")
        return order

    def cancel_order(self, broker_order_id: str) -> bool:
        import requests
        resp = requests.delete(
            f"{self.BASE_URL}/order/cancel",
            params={"order_id": broker_order_id},
            headers=self._session_headers,
            timeout=10,
        )
        return resp.status_code == 200

    def modify_order(self, broker_order_id: str, price: float, quantity: int) -> bool:
        import requests
        resp = requests.put(
            f"{self.BASE_URL}/order/modify",
            json={"order_id": broker_order_id, "price": price, "quantity": quantity},
            headers=self._session_headers,
            timeout=10,
        )
        return resp.status_code == 200

    def get_order_status(self, broker_order_id: str) -> Order:
        import requests
        resp = requests.get(
            f"{self.BASE_URL}/order/details",
            params={"order_id": broker_order_id},
            headers=self._session_headers,
            timeout=10,
        )
        data = resp.json().get("data", {})
        order = Order()
        order.broker_order_id = broker_order_id
        order.status = OrderStatus(data.get("status", "PENDING").upper())
        order.filled_price = data.get("average_price", 0.0)
        order.filled_quantity = data.get("filled_quantity", 0)
        return order

    def get_positions(self) -> List[Position]:
        import requests
        resp = requests.get(
            f"{self.BASE_URL}/portfolio/short-term-positions",
            headers=self._session_headers,
            timeout=10,
        )
        positions = []
        for item in resp.json().get("data", []):
            pos = Position()
            pos.status = PositionStatus.OPEN if item.get("quantity", 0) != 0 else PositionStatus.CLOSED
            positions.append(pos)
        return positions

    def get_ltp(self, exchange: str, symbol: str) -> float:
        import requests
        key = f"{exchange}_EQ|{symbol}"
        resp = requests.get(
            f"{self.BASE_URL}/market-quote/ltp",
            params={"symbol": key},
            headers=self._session_headers,
            timeout=5,
        )
        data = resp.json().get("data", {})
        return data.get(key, {}).get("last_price", 0.0)

    def get_ltp_option(self, trading_symbol: str) -> float:
        """Fetch LTP for an option contract by its trading symbol."""
        return self.get_ltp("NFO", trading_symbol)


# ── Paper Broker Client ────────────────────────────────────────

class PaperBrokerClient(BaseBrokerClient):
    """
    Simulated broker for paper trading and testing.

    Fills MARKET orders at current LTP with configurable slippage.
    Maintains an internal order book and position ledger.
    """

    def __init__(self, api_key: str = "", api_secret: str = "", slippage_pct: float = 0.1):
        super().__init__(api_key, api_secret)
        self.slippage_pct = slippage_pct
        self._order_book: Dict[str, Order] = {}       # broker_id → Order
        self._positions: Dict[str, Position] = {}     # symbol → Position
        self._ltp_store: Dict[str, float] = {}        # symbol → price (set by data feed)
        self._connected = True

    def login(self) -> bool:
        self._connected = True
        logger.info("PaperBroker: login successful (simulation mode)")
        return True

    def set_ltp(self, symbol: str, price: float) -> None:
        """Called by the data feed to keep the paper broker's prices current."""
        self._ltp_store[symbol] = price

    def place_order(self, order: Order) -> Order:
        broker_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"
        order.broker_order_id = broker_id

        ltp = self._ltp_store.get(order.symbol, 100.0)

        if order.order_type == OrderType.MARKET:
            # Apply slippage
            slippage = ltp * (self.slippage_pct / 100)
            fill_price = (ltp + slippage) if order.side == OrderSide.BUY else (ltp - slippage)
            order.filled_price = round(fill_price, 2)
            order.filled_quantity = order.quantity
            order.status = OrderStatus.COMPLETE
            logger.info(
                f"PaperBroker: FILLED {order.side.value} {order.quantity} "
                f"{order.symbol} @ ₹{order.filled_price}"
            )
        else:
            order.status = OrderStatus.OPEN  # LIMIT orders stay open until manually filled

        self._order_book[broker_id] = order
        return order

    def cancel_order(self, broker_order_id: str) -> bool:
        if broker_order_id in self._order_book:
            self._order_book[broker_order_id].status = OrderStatus.CANCELLED
            return True
        return False

    def modify_order(self, broker_order_id: str, price: float, quantity: int) -> bool:
        if broker_order_id in self._order_book:
            self._order_book[broker_order_id].price = price
            self._order_book[broker_order_id].quantity = quantity
            return True
        return False

    def get_order_status(self, broker_order_id: str) -> Order:
        return self._order_book.get(broker_order_id, Order())

    def get_positions(self) -> List[Position]:
        return list(self._positions.values())

    def get_ltp(self, exchange: str, symbol: str) -> float:
        return self._ltp_store.get(symbol, 0.0)


# ── Factory ───────────────────────────────────────────────────

def create_broker_client(config: dict) -> BaseBrokerClient:
    """
    Factory function: returns the appropriate broker client based on config.

    Parameters
    ----------
    config : dict — the 'broker' section of config.yml
    """
    name = config.get("name", "paper").lower()
    paper = config.get("paper_trading", True)

    if paper or name == "paper":
        logger.info("Broker: Paper trading mode selected")
        return PaperBrokerClient()
    elif name == "upstox":
        return UpstoxClient(
            api_key=config["api_key"],
            api_secret=config["api_secret"],
            redirect_uri=config.get("redirect_uri", ""),
            access_token=config.get("access_token", ""),
        )
    else:
        raise ValueError(f"Unsupported broker: {name}. Add its client class to broker_client.py")
