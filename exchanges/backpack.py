"""
Backpack exchange client implementation for arbitrage.
"""

import os
import asyncio
import json
import time
import base64
from decimal import Decimal
from typing import Dict, Any, Tuple, Optional, Callable
from cryptography.hazmat.primitives.asymmetric import ed25519
import websockets
from bpx.public import Public
from bpx.constants.enums import OrderTypeEnum, TimeInForceEnum

from .base import BaseExchangeClient, OrderResult, query_retry


class BackpackWebSocketManager:
    """WebSocket manager for Backpack order book and order updates."""

    def __init__(self, public_key: str, secret_key: str, symbol: str,
                 orderbook_callback: Optional[Callable] = None,
                 order_callback: Optional[Callable] = None):
        self.public_key = public_key
        self.secret_key = secret_key
        self.symbol = symbol
        self.orderbook_callback = orderbook_callback
        self.order_callback = order_callback
        self.websocket = None
        self.running = False
        self.ws_url = "wss://ws.backpack.exchange"
        self.logger = None

        # Order book state
        self.best_bid: Optional[Decimal] = None
        self.best_ask: Optional[Decimal] = None
        self.order_book_ready = False

        # Initialize ED25519 private key from base64 decoded secret
        self.private_key = ed25519.Ed25519PrivateKey.from_private_bytes(
            base64.b64decode(secret_key)
        )

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

    def _log(self, message: str, level: str = "INFO"):
        """Log message using the logger if available."""
        if self.logger:
            self.logger.info(f"[Backpack WS] {message}")
        else:
            print(f"[Backpack WS] [{level}] {message}")

    def _generate_signature(self, instruction: str, timestamp: int, window: int = 5000) -> str:
        """Generate ED25519 signature for WebSocket authentication."""
        message = f"instruction={instruction}&timestamp={timestamp}&window={window}"
        signature_bytes = self.private_key.sign(message.encode())
        return base64.b64encode(signature_bytes).decode()

    async def connect(self):
        """Connect to Backpack WebSocket."""
        reconnect_delay = 1
        max_reconnect_delay = 30

        while True:
            try:
                self._log("Connecting to Backpack WebSocket...")
                self.websocket = await websockets.connect(self.ws_url)
                self.running = True

                # Subscribe to order book depth
                await self._subscribe_orderbook()

                # Subscribe to order updates (authenticated)
                await self._subscribe_orders()

                reconnect_delay = 1  # Reset on successful connection
                self._log("WebSocket connected and subscribed")

                # Start listening for messages
                await self._listen()

            except Exception as e:
                self._log(f"WebSocket connection error: {e}", "ERROR")

            if self.running:
                self._log(f"Waiting {reconnect_delay} seconds before reconnecting...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    async def _subscribe_orderbook(self):
        """Subscribe to order book updates."""
        subscribe_message = {
            "method": "SUBSCRIBE",
            "params": [f"depth.{self.symbol}"]
        }
        await self.websocket.send(json.dumps(subscribe_message))
        self._log(f"Subscribed to order book for {self.symbol}")

    async def _subscribe_orders(self):
        """Subscribe to order updates (authenticated)."""
        timestamp = int(time.time() * 1000)
        signature = self._generate_signature("subscribe", timestamp)

        subscribe_message = {
            "method": "SUBSCRIBE",
            "params": [f"account.orderUpdate.{self.symbol}"],
            "signature": [
                self.public_key,
                signature,
                str(timestamp),
                "5000"
            ]
        }
        await self.websocket.send(json.dumps(subscribe_message))
        self._log(f"Subscribed to order updates for {self.symbol}")

    async def _listen(self):
        """Listen for WebSocket messages."""
        try:
            async for message in self.websocket:
                if not self.running:
                    break

                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError as e:
                    self._log(f"Failed to parse message: {e}", "ERROR")
                except Exception as e:
                    self._log(f"Error handling message: {e}", "ERROR")

        except websockets.exceptions.ConnectionClosed:
            self._log("WebSocket connection closed", "WARNING")
        except Exception as e:
            self._log(f"WebSocket listen error: {e}", "ERROR")

    async def _handle_message(self, data: Dict[str, Any]):
        """Handle incoming WebSocket messages."""
        stream = data.get('stream', '')

        if 'depth' in stream:
            await self._handle_orderbook_update(data.get('data', {}))
        elif 'orderUpdate' in stream:
            await self._handle_order_update(data.get('data', {}))

    async def _handle_orderbook_update(self, orderbook_data: Dict[str, Any]):
        """Handle order book updates."""
        try:
            bids = orderbook_data.get('b', [])  # [[price, size], ...]
            asks = orderbook_data.get('a', [])  # [[price, size], ...]

            if bids:
                # Best bid is highest price
                sorted_bids = sorted(bids, key=lambda x: Decimal(x[0]), reverse=True)
                self.best_bid = Decimal(sorted_bids[0][0])

            if asks:
                # Best ask is lowest price
                sorted_asks = sorted(asks, key=lambda x: Decimal(x[0]))
                self.best_ask = Decimal(sorted_asks[0][0])

            if not self.order_book_ready and self.best_bid and self.best_ask:
                self.order_book_ready = True
                self._log(f"Order book ready - Bid: {self.best_bid}, Ask: {self.best_ask}")

            if self.orderbook_callback:
                await self.orderbook_callback({
                    'best_bid': self.best_bid,
                    'best_ask': self.best_ask
                })

        except Exception as e:
            self._log(f"Error handling orderbook update: {e}", "ERROR")

    async def _handle_order_update(self, order_data: Dict[str, Any]):
        """Handle order update messages."""
        try:
            if self.order_callback:
                await self.order_callback(order_data)
        except Exception as e:
            self._log(f"Error handling order update: {e}", "ERROR")

    async def disconnect(self):
        """Disconnect from WebSocket."""
        self.running = False
        if self.websocket:
            await self.websocket.close()
            self._log("WebSocket disconnected")


class BackpackAccount:
    """Backpack account client for private API calls."""

    def __init__(self, public_key: str, secret_key: str):
        from bpx import Account
        self.client = Account(
            public_key=public_key,
            secret_key=secret_key
        )

    def execute_order(self, **kwargs):
        return self.client.execute_order(**kwargs)

    def get_open_positions(self):
        return self.client.get_open_positions()

    def cancel_order(self, **kwargs):
        return self.client.cancel_order(**kwargs)


class BackpackClient(BaseExchangeClient):
    """Backpack exchange client implementation for arbitrage."""

    def __init__(self, ticker: str):
        """Initialize Backpack client."""
        super().__init__(ticker)

        # Backpack credentials from environment
        self.public_key = os.getenv('BACKPACK_PUBLIC_KEY')
        self.secret_key = os.getenv('BACKPACK_SECRET_KEY')

        if not self.public_key or not self.secret_key:
            raise ValueError("BACKPACK_PUBLIC_KEY and BACKPACK_SECRET_KEY must be set")

        # Initialize clients
        self.public_client = Public()
        self.account_client = BackpackAccount(
            public_key=self.public_key,
            secret_key=self.secret_key
        )

        # WebSocket manager
        self.ws_manager: Optional[BackpackWebSocketManager] = None

    async def connect(self) -> None:
        """Connect to Backpack."""
        # Get contract info first
        await self.get_contract_info()

        # Initialize WebSocket manager
        self.ws_manager = BackpackWebSocketManager(
            public_key=self.public_key,
            secret_key=self.secret_key,
            symbol=self.contract_id,
            orderbook_callback=self._on_orderbook_update,
            order_callback=self._on_order_update
        )
        self.ws_manager.set_logger(self.logger)

        # Start WebSocket in background
        asyncio.create_task(self.ws_manager.connect())

        # Wait for connection and initial data
        await asyncio.sleep(2)
        self._log("Connected to Backpack")

    async def disconnect(self) -> None:
        """Disconnect from Backpack."""
        if self.ws_manager:
            await self.ws_manager.disconnect()

    async def _on_orderbook_update(self, data: Dict[str, Any]):
        """Handle orderbook updates from WebSocket."""
        pass  # Data is stored in ws_manager

    async def _on_order_update(self, data: Dict[str, Any]):
        """Handle order updates from WebSocket."""
        pass  # Can be extended for order tracking

    @query_retry(default_return=(Decimal('0'), Decimal('0')))
    async def fetch_bbo_prices(self) -> Tuple[Decimal, Decimal]:
        """Fetch best bid/ask prices."""
        # Try WebSocket data first
        if (self.ws_manager and self.ws_manager.order_book_ready and
                self.ws_manager.best_bid and self.ws_manager.best_ask):
            return self.ws_manager.best_bid, self.ws_manager.best_ask

        # Fallback to REST API
        order_book = self.public_client.get_depth(self.contract_id)

        bids = order_book.get('bids', [])
        asks = order_book.get('asks', [])

        bids = sorted(bids, key=lambda x: Decimal(x[0]), reverse=True)
        asks = sorted(asks, key=lambda x: Decimal(x[0]))

        best_bid = Decimal(bids[0][0]) if bids else Decimal('0')
        best_ask = Decimal(asks[0][0]) if asks else Decimal('0')

        return best_bid, best_ask

    async def place_market_order(self, quantity: Decimal, side: str) -> OrderResult:
        """Place a market order on Backpack."""
        try:
            if side.lower() == 'buy':
                order_side = 'Bid'
            elif side.lower() == 'sell':
                order_side = 'Ask'
            else:
                return OrderResult(success=False, error_message=f"Invalid side: {side}")

            result = self.account_client.execute_order(
                symbol=self.contract_id,
                side=order_side,
                order_type=OrderTypeEnum.MARKET,
                quantity=str(quantity)
            )

            if not result:
                return OrderResult(success=False, error_message="No response from API")

            if 'code' in result:
                return OrderResult(success=False, error_message=result.get('message', 'Unknown error'))

            order_id = result.get('id')
            order_status = result.get('status', '').upper()

            if order_status == 'FILLED':
                executed_qty = Decimal(result.get('executedQuantity', '0'))
                executed_quote = Decimal(result.get('executedQuoteQuantity', '0'))
                avg_price = executed_quote / executed_qty if executed_qty > 0 else Decimal('0')

                return OrderResult(
                    success=True,
                    order_id=order_id,
                    side=side.lower(),
                    size=quantity,
                    price=avg_price,
                    avg_price=avg_price,
                    status='FILLED',
                    filled_size=executed_qty
                )
            else:
                return OrderResult(
                    success=False,
                    order_id=order_id,
                    error_message=f"Order not filled, status: {order_status}"
                )

        except Exception as e:
            return OrderResult(success=False, error_message=str(e))

    @query_retry(default_return=Decimal('0'))
    async def get_position(self) -> Decimal:
        """Get current position on Backpack."""
        positions_data = self.account_client.get_open_positions()
        for position in positions_data:
            if position.get('symbol', '') == self.contract_id:
                return Decimal(position.get('netQuantity', '0'))
        return Decimal('0')

    async def get_contract_info(self) -> Tuple[str, Decimal, Decimal]:
        """Get contract info for the ticker."""
        markets = self.public_client.get_markets()

        for market in markets:
            if (market.get('marketType', '') == 'PERP' and
                    market.get('baseSymbol', '') == self.ticker and
                    market.get('quoteSymbol', '') == 'USDC'):

                self.contract_id = market.get('symbol', '')
                self.min_quantity = Decimal(market.get('filters', {}).get('quantity', {}).get('minQuantity', '0'))
                self.tick_size = Decimal(market.get('filters', {}).get('price', {}).get('tickSize', '0'))

                self._log(f"Contract: {self.contract_id}, Tick: {self.tick_size}, Min Qty: {self.min_quantity}")
                return self.contract_id, self.tick_size, self.min_quantity

        raise ValueError(f"Failed to find contract for ticker: {self.ticker}")

    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        return "Backpack"

