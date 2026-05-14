"""
models.py
=========
Core data models used across the trading bot.
Using dataclasses for lightweight, typed value objects.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ── Enumerations ─────────────────────────────────────────────

class OptionType(str, Enum):
    CALL = "CE"
    PUT = "PE"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL_M = "SL-M"
    SL = "SL"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ProductType(str, Enum):
    MIS = "MIS"       # Intraday
    NRML = "NRML"     # Carry-forward / overnight


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


# ── Data Models ──────────────────────────────────────────────

@dataclass
class OptionContract:
    """Represents a single Nifty option contract."""
    index: str                        # e.g. NIFTY
    expiry: str                       # e.g. 2025-01-30
    strike: int                       # e.g. 24000
    option_type: OptionType           # CE | PE
    trading_symbol: str               # broker-specific symbol string
    instrument_token: str = ""        # broker-specific token
    lot_size: int = 50


@dataclass
class Greeks:
    """Option Greeks snapshot."""
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    iv: float = 0.0                   # implied volatility %


@dataclass
class Order:
    """Represents a single order submitted to the broker."""
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    product_type: ProductType = ProductType.MIS
    quantity: int = 0                 # total quantity (lots × lot_size)
    price: float = 0.0               # limit/SL price; 0 for MARKET
    trigger_price: float = 0.0       # for SL / SL-M orders
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float = 0.0
    filled_quantity: int = 0
    broker_order_id: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    message: str = ""                 # rejection reason or notes


@dataclass
class Position:
    """Tracks an open or closed options position."""
    position_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    contract: Optional[OptionContract] = None
    side: OrderSide = OrderSide.SELL  # SELL = short premium
    lots: int = 1
    entry_price: float = 0.0
    current_price: float = 0.0
    exit_price: float = 0.0
    entry_order_id: str = ""
    exit_order_id: str = ""
    status: PositionStatus = PositionStatus.OPEN
    entry_time: datetime = field(default_factory=datetime.now)
    exit_time: Optional[datetime] = None
    greeks: Greeks = field(default_factory=Greeks)

    @property
    def quantity(self) -> int:
        return self.lots * (self.contract.lot_size if self.contract else 50)

    @property
    def unrealised_pnl(self) -> float:
        """P&L for open positions (positive = profit for short)."""
        if self.status != PositionStatus.OPEN:
            return 0.0
        multiplier = -1 if self.side == OrderSide.BUY else 1
        return multiplier * (self.current_price - self.entry_price) * self.quantity

    @property
    def realised_pnl(self) -> float:
        """P&L for closed positions."""
        if self.status != PositionStatus.CLOSED:
            return 0.0
        multiplier = -1 if self.side == OrderSide.BUY else 1
        return multiplier * (self.exit_price - self.entry_price) * self.quantity


@dataclass
class TradeLog:
    """Immutable record of a completed order event."""
    timestamp: datetime
    symbol: str
    side: str
    order_type: str
    quantity: int
    price: float
    status: str
    order_id: str
    notes: str = ""


@dataclass
class RiskSnapshot:
    """Current risk metrics for the session."""
    session_date: str = ""
    total_unrealised_pnl: float = 0.0
    total_realised_pnl: float = 0.0
    peak_pnl: float = 0.0
    drawdown: float = 0.0
    trades_today: int = 0
    open_positions: int = 0
    gross_exposure: float = 0.0       # sum of all premium paid/received
    max_loss_limit: float = 0.0
    target_profit: float = 0.0
    is_halted: bool = False
    halt_reason: str = ""

    @property
    def total_pnl(self) -> float:
        return self.total_unrealised_pnl + self.total_realised_pnl
