"""
Lighter exchange client implementation for arbitrage.
"""

import os
import asyncio
import json
import time
import logging
import requests
from decimal import Decimal
from typing import Dict, Any, Tuple, Optional, Callable, List
import websockets

from .base import BaseExchangeClient, OrderResult, query_retry

# Import official Lighter SDK
import lighter
from lighter import SignerClient, ApiClient, Configuration

# Suppress Lighter SDK debug logs
logging.getLogger('lighter').setLevel(logging.WARNING)


class LighterWebSocketManager:
    """WebSocket manager for Lighter order book and order updates."""

    def __init__(self, market_index: int, account_index: int,
                 lighter_client: SignerClient,
                 orderbook_callback: Optional[Callable] = None,
                 order_callback: Optional[Callable] = None):
        self.market_index = market_index
        self.account_index = account_index
        self.lighter_client = lighter_client
        self.orderbook_callback = orderbook_callback
        self.order_callback = order_callback
        self.logger = None
        self.running = False
        self.ws = None

        # WebSocket URL
        self.ws_url = "wss://mainnet.zklighter.elliot.ai/stream"

        # Order book state
        self.order_book = {"bids": {}, "asks": {}}
        self.best_bid: Optional[Decimal] = None
        self.best_ask: Optional[Decimal] = None
        self.order_book_ready = False
        self.snapshot_loaded = False
        self.order_book_offset = None
        self.order_book_lock = asyncio.Lock()

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

    def _log(self, message: str, level: str = "INFO"):
        """Log message using the logger if available."""
        if self.logger:
            self.logger.info(f"[Lighter WS] {message}")
        else:
            print(f"[Lighter WS] [{level}] {message}")

    def _update_order_book(self, side: str, updates: List[Dict[str, Any]]):
        """Update the order book with new price/size information."""
        ob = self.order_book[side]

        for update in updates:
            try:
                price = float(update["price"])
                size = float(update["size"])

                if size == 0:
                    ob.pop(price, None)
                else:
                    ob[price] = size
            except (KeyError, ValueError, TypeError) as e:
                self._log(f"Error processing order book update: {e}", "ERROR")

    def _get_best_levels(self) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """Get best bid and ask from order book."""
        best_bid = None
        best_ask = None

        if self.order_book["bids"]:
            # Best bid is highest price with sufficient liquidity
            bid_levels = [(p, s) for p, s in self.order_book["bids"].items() if s * p >= 10000]
            if bid_levels:
                best_bid = Decimal(str(max(bid_levels, key=lambda x: x[0])[0]))

        if self.order_book["asks"]:
            # Best ask is lowest price with sufficient liquidity
            ask_levels = [(p, s) for p, s in self.order_book["asks"].items() if s * p >= 10000]
            if ask_levels:
                best_ask = Decimal(str(min(ask_levels, key=lambda x: x[0])[0]))

        return best_bid, best_ask

    async def connect(self):
        """Connect to Lighter WebSocket."""
        reconnect_delay = 1
        max_reconnect_delay = 30

        while True:
            try:
                # Reset order book state
                async with self.order_book_lock:
                    self.order_book["bids"].clear()
                    self.order_book["asks"].clear()
                    self.snapshot_loaded = False
                    self.order_book_offset = None

                async with websockets.connect(self.ws_url) as self.ws:
                    self.running = True

                    # Subscribe to order book
                    await self.ws.send(json.dumps({
                        "type": "subscribe",
                        "channel": f"order_book/{self.market_index}"
                    }))

                    # Subscribe to account orders (authenticated)
                    try:
                        ten_minutes_deadline = int(time.time() + 10 * 60)
                        auth_token, err = self.lighter_client.create_auth_token_with_expiry(ten_minutes_deadline)
                        if err is None:
                            await self.ws.send(json.dumps({
                                "type": "subscribe",
                                "channel": f"account_orders/{self.market_index}/{self.account_index}",
                                "auth": auth_token
                            }))
                            self._log("Subscribed to account orders")
                    except Exception as e:
                        self._log(f"Error subscribing to account orders: {e}", "WARNING")

                    reconnect_delay = 1
                    self._log("WebSocket connected")

                    # Message loop
                    while self.running:
                        try:
                            msg = await asyncio.wait_for(self.ws.recv(), timeout=1)
                            data = json.loads(msg)

                            async with self.order_book_lock:
                                await self._handle_message(data)

                        except asyncio.TimeoutError:
                            continue
                        except websockets.exceptions.ConnectionClosed:
                            self._log("Connection closed", "WARNING")
                            break
                        except Exception as e:
                            self._log(f"Error processing message: {e}", "ERROR")
                            break

            except Exception as e:
                self._log(f"WebSocket connection error: {e}", "ERROR")

            if self.running:
                self._log(f"Reconnecting in {reconnect_delay}s...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    async def _handle_message(self, data: Dict[str, Any]):
        """Handle incoming WebSocket messages."""
        msg_type = data.get("type", "")

        if msg_type == "subscribed/order_book":
            # Initial snapshot
            self.order_book["bids"].clear()
            self.order_book["asks"].clear()

            order_book = data.get("order_book", {})
            if "offset" in order_book:
                self.order_book_offset = order_book["offset"]

            self._update_order_book("bids", order_book.get("bids", []))
            self._update_order_book("asks", order_book.get("asks", []))
            self.snapshot_loaded = True

            self._log(f"Order book snapshot loaded: {len(self.order_book['bids'])} bids, "
                      f"{len(self.order_book['asks'])} asks")

        elif msg_type == "update/order_book" and self.snapshot_loaded:
            order_book = data.get("order_book", {})
            self._update_order_book("bids", order_book.get("bids", []))
            self._update_order_book("asks", order_book.get("asks", []))

            # Update best bid/ask
            best_bid, best_ask = self._get_best_levels()
            if best_bid:
                self.best_bid = best_bid
            if best_ask:
                self.best_ask = best_ask

            if not self.order_book_ready and self.best_bid and self.best_ask:
                self.order_book_ready = True
                self._log(f"Order book ready - Bid: {self.best_bid}, Ask: {self.best_ask}")

            if self.orderbook_callback:
                await self.orderbook_callback({
                    'best_bid': self.best_bid,
                    'best_ask': self.best_ask
                })

        elif msg_type == "update/account_orders":
            orders = data.get("orders", {}).get(str(self.market_index), [])
            if orders and self.order_callback:
                await self.order_callback(orders)

        elif msg_type == "ping":
            await self.ws.send(json.dumps({"type": "pong"}))

    async def disconnect(self):
        """Disconnect from WebSocket."""
        self.running = False
        if self.ws:
            await self.ws.close()
        self._log("WebSocket disconnected")


class LighterClient(BaseExchangeClient):
    """Lighter exchange client implementation for arbitrage."""

    def __init__(self, ticker: str):
        """Initialize Lighter client."""
        super().__init__(ticker)

        # Lighter credentials from environment
        self.api_key_private_key = os.getenv('API_KEY_PRIVATE_KEY')
        self.account_index = int(os.getenv('LIGHTER_ACCOUNT_INDEX', '0'))
        self.api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX', '0'))
        self.base_url = "https://mainnet.zklighter.elliot.ai"

        if not self.api_key_private_key:
            raise ValueError("API_KEY_PRIVATE_KEY must be set")

        # Initialize clients
        self.lighter_client: Optional[SignerClient] = None
        self.api_client: Optional[ApiClient] = None

        # Market configuration
        self.base_amount_multiplier = None
        self.price_multiplier = None

        # WebSocket manager
        self.ws_manager: Optional[LighterWebSocketManager] = None

    async def _initialize_lighter_client(self):
        """Initialize the Lighter signer client."""
        if self.lighter_client is None:
            self.lighter_client = SignerClient(
                url=self.base_url,
                private_key=self.api_key_private_key,
                account_index=self.account_index,
                api_key_index=self.api_key_index,
            )

            err = self.lighter_client.check_client()
            if err is not None:
                raise Exception(f"Lighter client check failed: {err}")

            self._log("Lighter signer client initialized")

    async def connect(self) -> None:
        """Connect to Lighter."""
        # Initialize API client
        self.api_client = ApiClient(configuration=Configuration(host=self.base_url))

        # Initialize signer client
        await self._initialize_lighter_client()

        # Get contract info
        await self.get_contract_info()

        # Initialize WebSocket manager
        self.ws_manager = LighterWebSocketManager(
            market_index=self.contract_id,
            account_index=self.account_index,
            lighter_client=self.lighter_client,
            orderbook_callback=self._on_orderbook_update,
            order_callback=self._on_order_update
        )
        self.ws_manager.set_logger(self.logger)

        # Start WebSocket in background
        asyncio.create_task(self.ws_manager.connect())

        # Wait for connection and initial data
        await asyncio.sleep(2)
        self._log("Connected to Lighter")

    async def disconnect(self) -> None:
        """Disconnect from Lighter."""
        if self.ws_manager:
            await self.ws_manager.disconnect()
        if self.api_client:
            await self.api_client.close()

    async def _on_orderbook_update(self, data: Dict[str, Any]):
        """Handle orderbook updates from WebSocket."""
        pass  # Data is stored in ws_manager

    async def _on_order_update(self, data: List[Dict[str, Any]]):
        """Handle order updates from WebSocket."""
        pass  # Can be extended for order tracking

    @query_retry(default_return=(Decimal('0'), Decimal('0')))
    async def fetch_bbo_prices(self) -> Tuple[Decimal, Decimal]:
        """Fetch best bid/ask prices."""
        # Use WebSocket data
        if (self.ws_manager and self.ws_manager.order_book_ready and
                self.ws_manager.best_bid and self.ws_manager.best_ask):
            return self.ws_manager.best_bid, self.ws_manager.best_ask

        raise ValueError("WebSocket not ready")

    async def place_market_order(self, quantity: Decimal, side: str) -> OrderResult:
        """Place a market order on Lighter (implemented as aggressive limit order)."""
        try:
            if self.lighter_client is None:
                await self._initialize_lighter_client()

            # Get current BBO for pricing
            best_bid, best_ask = await self.fetch_bbo_prices()

            if side.lower() == 'buy':
                is_ask = False
                # Buy at slightly above ask to ensure fill
                price = best_ask * Decimal('1.002')
            elif side.lower() == 'sell':
                is_ask = True
                # Sell at slightly below bid to ensure fill
                price = best_bid * Decimal('0.998')
            else:
                return OrderResult(success=False, error_message=f"Invalid side: {side}")

            client_order_index = int(time.time() * 1000) % 1000000

            # Create order
            create_order, tx_hash, error = await self.lighter_client.create_order(
                market_index=self.contract_id,
                client_order_index=client_order_index,
                base_amount=int(quantity * self.base_amount_multiplier),
                price=int(price * self.price_multiplier),
                is_ask=is_ask,
                order_type=self.lighter_client.ORDER_TYPE_LIMIT,
                time_in_force=self.lighter_client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
                reduce_only=False,
                trigger_price=0,
            )

            if error is not None:
                return OrderResult(success=False, error_message=f"Order creation error: {error}")

            # Wait for fill
            await asyncio.sleep(2)

            return OrderResult(
                success=True,
                order_id=str(client_order_index),
                side=side.lower(),
                size=quantity,
                price=price,
                avg_price=price,
                status='FILLED',
                filled_size=quantity
            )

        except Exception as e:
            return OrderResult(success=False, error_message=str(e))

    @query_retry(default_return=Decimal('0'))
    async def get_position(self) -> Decimal:
        """Get current position on Lighter."""
        url = f"{self.base_url}/api/v1/account"
        headers = {"accept": "application/json"}
        parameters = {"by": "index", "value": self.account_index}

        response = requests.get(url, headers=headers, params=parameters, timeout=10)
        response.raise_for_status()

        data = response.json()
        if 'accounts' not in data or not data['accounts']:
            return Decimal('0')

        positions = data['accounts'][0].get('positions', [])
        for position in positions:
            if position.get('symbol') == self.ticker:
                return Decimal(str(position['position'])) * position['sign']

        return Decimal('0')

    async def get_contract_info(self) -> Tuple[str, Decimal, Decimal]:
        """Get contract info for the ticker."""
        order_api = lighter.OrderApi(self.api_client)
        order_books = await order_api.order_books()

        for market in order_books.order_books:
            if market.symbol == self.ticker:
                self.contract_id = market.market_id
                self.base_amount_multiplier = pow(10, market.supported_size_decimals)
                self.price_multiplier = pow(10, market.supported_price_decimals)

                # Get tick size
                market_summary = await order_api.order_book_details(market_id=market.market_id)
                order_book_details = market_summary.order_book_details[0]
                self.tick_size = Decimal("1") / (Decimal("10") ** order_book_details.price_decimals)

                self._log(f"Contract: {self.contract_id}, Tick: {self.tick_size}")
                return str(self.contract_id), self.tick_size, Decimal('0')

        raise ValueError(f"Failed to find market for ticker: {self.ticker}")

    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        return "Lighter"

