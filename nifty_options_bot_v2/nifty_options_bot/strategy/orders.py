"""
orders.py
=========
Order Management System (OMS) for the Nifty options trading bot.

Responsibilities
----------------
- Translate strategy signals into broker-ready Order objects
- Submit, track, and manage the lifecycle of all orders
- Maintain an in-memory order book and trade log
- Support order types: MARKET, LIMIT, SL-M
- Publish order events for the risk engine and dashboard
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

from broker.broker_client import BaseBrokerClient
from core.models import (
    Order, OrderSide, OrderStatus, OrderType, Position,
    PositionStatus, OptionContract, TradeLog, ProductType
)
from utils.logger import get_logger

logger = get_logger("orders")


class OrderManager:
    """
    Central OMS component.

    Usage
    -----
    om = OrderManager(broker_client, config['orders'])
    order = om.market_order(contract, side=OrderSide.SELL, lots=1, lot_size=50)
    """

    def __init__(self, broker_client: BaseBrokerClient, config: dict):
        self.broker = broker_client
        self._order_type = OrderType(config.get("order_type", "MARKET"))
        self._product_type = ProductType(config.get("product_type", "MIS"))
        self._retry_attempts = config.get("retry_attempts", 3)
        self._retry_delay = config.get("retry_delay_seconds", 2)
        self._slippage_buffer = config.get("slippage_buffer", 0.5)

        # ── State ────────────────────────────────────────────
        self._orders: Dict[str, Order] = {}         # order_id → Order
        self._positions: Dict[str, Position] = {}   # position_id → Position
        self._trade_log: List[TradeLog] = []
        self._lock = threading.Lock()

        # ── Callbacks ────────────────────────────────────────
        # Register with: om.on_order_update = my_callback
        self.on_order_update: Optional[Callable[[Order], None]] = None
        self.on_position_update: Optional[Callable[[Position], None]] = None

        logger.info(f"OrderManager initialised (type={self._order_type.value}, "
                    f"product={self._product_type.value})")

    # ── Public Order Factories ────────────────────────────────

    def market_order(
        self,
        contract: OptionContract,
        side: OrderSide,
        lots: int,
        current_ltp: float = 0.0,
    ) -> Optional[Order]:
        """
        Place a MARKET order for an option contract.

        Returns the completed Order object or None on failure.
        """
        order = Order(
            symbol=contract.trading_symbol,
            side=side,
            order_type=OrderType.MARKET,
            product_type=self._product_type,
            quantity=lots * contract.lot_size,
            price=0.0,
        )
        return self._execute_order(order, contract, lots, current_ltp)

    def limit_order(
        self,
        contract: OptionContract,
        side: OrderSide,
        lots: int,
        limit_price: float,
    ) -> Optional[Order]:
        """Place a LIMIT order at a specified price."""
        order = Order(
            symbol=contract.trading_symbol,
            side=side,
            order_type=OrderType.LIMIT,
            product_type=self._product_type,
            quantity=lots * contract.lot_size,
            price=limit_price,
        )
        return self._execute_order(order, contract, lots, limit_price)

    def sl_market_order(
        self,
        contract: OptionContract,
        side: OrderSide,
        lots: int,
        trigger_price: float,
    ) -> Optional[Order]:
        """Place a Stop-Loss Market (SL-M) order."""
        order = Order(
            symbol=contract.trading_symbol,
            side=side,
            order_type=OrderType.SL_M,
            product_type=self._product_type,
            quantity=lots * contract.lot_size,
            trigger_price=trigger_price,
        )
        return self._execute_order(order, contract, lots, trigger_price)

    def exit_position(self, position: Position, current_ltp: float = 0.0) -> Optional[Order]:
        """
        Square off an open position with an opposing MARKET order.
        Marks the position as CLOSED on successful fill.
        """
        if position.status != PositionStatus.OPEN:
            logger.warning(f"Position {position.position_id[:8]} is not open — cannot exit")
            return None

        exit_side = OrderSide.BUY if position.side == OrderSide.SELL else OrderSide.SELL
        order = self.market_order(position.contract, exit_side, position.lots, current_ltp)

        if order and order.status == OrderStatus.COMPLETE:
            with self._lock:
                position.exit_price = order.filled_price
                position.exit_order_id = order.order_id
                position.exit_time = datetime.now()
                position.status = PositionStatus.CLOSED
            logger.info(
                f"Position closed: {position.contract.trading_symbol} "
                f"@ ₹{position.exit_price:.2f} | PnL: ₹{position.realised_pnl:.2f}"
            )
            if self.on_position_update:
                self.on_position_update(position)

        return order

    # ── Internal Execution ────────────────────────────────────

    def _execute_order(
        self,
        order: Order,
        contract: OptionContract,
        lots: int,
        entry_price: float,
    ) -> Optional[Order]:
        """
        Submit an order to the broker with retry logic.
        Creates a Position record on successful fill.
        """
        for attempt in range(1, self._retry_attempts + 1):
            try:
                filled_order = self.broker.place_order(order)
                with self._lock:
                    self._orders[filled_order.order_id] = filled_order

                self._log_trade(filled_order)

                if filled_order.status == OrderStatus.COMPLETE:
                    position = self._create_position(filled_order, contract, lots)
                    if self.on_order_update:
                        self.on_order_update(filled_order)
                    if self.on_position_update:
                        self.on_position_update(position)
                    return filled_order

                elif filled_order.status == OrderStatus.REJECTED:
                    logger.error(f"Order rejected: {filled_order.message}")
                    return filled_order

                # OPEN order — poll until filled or give up
                logger.info(f"Order {filled_order.broker_order_id} is OPEN — waiting...")
                time.sleep(self._retry_delay)

            except Exception as e:
                logger.error(f"Attempt {attempt}/{self._retry_attempts} error: {e}")
                if attempt < self._retry_attempts:
                    time.sleep(self._retry_delay)

        logger.error(f"Order execution failed after {self._retry_attempts} attempts")
        return None

    def _create_position(self, order: Order, contract: OptionContract, lots: int) -> Position:
        """Create a Position record from a filled Order."""
        position = Position(
            contract=contract,
            side=order.side,
            lots=lots,
            entry_price=order.filled_price,
            current_price=order.filled_price,
            entry_order_id=order.order_id,
        )
        with self._lock:
            self._positions[position.position_id] = position
        logger.info(
            f"Position opened: {order.side.value} {lots}L "
            f"{contract.trading_symbol} @ ₹{order.filled_price:.2f}"
        )
        return position

    def _log_trade(self, order: Order) -> None:
        """Append a TradeLog entry for a completed order action."""
        entry = TradeLog(
            timestamp=datetime.now(),
            symbol=order.symbol,
            side=order.side.value,
            order_type=order.order_type.value,
            quantity=order.quantity,
            price=order.filled_price or order.price,
            status=order.status.value,
            order_id=order.broker_order_id or order.order_id,
            notes=order.message,
        )
        with self._lock:
            self._trade_log.append(entry)

    # ── Price Updates ─────────────────────────────────────────

    def update_position_prices(self, price_map: Dict[str, float]) -> None:
        """Update current_price for all open positions from a price dict."""
        with self._lock:
            for pos in self._positions.values():
                if pos.status == PositionStatus.OPEN and pos.contract:
                    sym = pos.contract.trading_symbol
                    if sym in price_map:
                        pos.current_price = price_map[sym]

    # ── Read Accessors ────────────────────────────────────────

    def get_open_positions(self) -> List[Position]:
        with self._lock:
            return [p for p in self._positions.values() if p.status == PositionStatus.OPEN]

    def get_all_positions(self) -> List[Position]:
        with self._lock:
            return list(self._positions.values())

    def get_trade_log(self) -> List[TradeLog]:
        with self._lock:
            return list(self._trade_log)

    def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    def cancel_all_open_orders(self) -> int:
        """Cancel all orders with OPEN/PENDING status. Returns count cancelled."""
        cancelled = 0
        with self._lock:
            for order in self._orders.values():
                if order.status in (OrderStatus.OPEN, OrderStatus.PENDING):
                    if self.broker.cancel_order(order.broker_order_id):
                        order.status = OrderStatus.CANCELLED
                        cancelled += 1
        logger.info(f"Cancelled {cancelled} open orders")
        return cancelled
