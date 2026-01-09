"""
Main arbitrage trading bot for Backpack and Lighter exchanges.

Strategy:
- Monitor price spread between Backpack and Lighter
- When spread exceeds threshold, execute market orders on both exchanges simultaneously
- Buy on the cheaper exchange, sell on the more expensive exchange
- Maintain hedged position (net position close to zero)
"""

import asyncio
import signal
import logging
import os
import sys
import time
import traceback
from decimal import Decimal
from typing import Optional

from exchanges.backpack import BackpackClient
from exchanges.lighter import LighterClient
from .order_book_manager import OrderBookManager
from .position_tracker import PositionTracker
from .data_logger import DataLogger


class BackpackLighterArb:
    """Arbitrage trading bot: market orders on both Backpack and Lighter."""

    def __init__(self, ticker: str, order_quantity: Decimal,
                 long_threshold: Decimal = Decimal('5'),
                 short_threshold: Decimal = Decimal('5'),
                 max_position: Decimal = Decimal('0'),
                 check_interval: float = 0.1):
        """
        Initialize the arbitrage trading bot.
        
        Args:
            ticker: Trading pair symbol (e.g., 'BTC', 'ETH')
            order_quantity: Size of each arbitrage trade
            long_threshold: Minimum spread for long Backpack trade (Lighter bid - Backpack ask)
            short_threshold: Minimum spread for short Backpack trade (Backpack bid - Lighter ask)
            max_position: Maximum absolute position on each exchange (0 = unlimited)
            check_interval: Seconds between spread checks
        """
        self.ticker = ticker
        self.order_quantity = order_quantity
        self.long_threshold = long_threshold
        self.short_threshold = short_threshold
        self.max_position = max_position
        self.check_interval = check_interval

        self.stop_flag = False
        self._cleanup_done = False

        # Setup logger
        self._setup_logger()

        # Initialize components (will be set in run)
        self.backpack_client: Optional[BackpackClient] = None
        self.lighter_client: Optional[LighterClient] = None
        self.order_book_manager: Optional[OrderBookManager] = None
        self.position_tracker: Optional[PositionTracker] = None
        self.data_logger: Optional[DataLogger] = None

        # Trading stats
        self.total_trades = 0
        self.successful_trades = 0
        self.failed_trades = 0

    def _setup_logger(self):
        """Setup logging configuration."""
        os.makedirs("logs", exist_ok=True)
        self.log_filename = f"logs/backpack_lighter_{self.ticker}.log"

        self.logger = logging.getLogger(f"arb_bot_{self.ticker}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        # Disable verbose logging from external libraries
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('requests').setLevel(logging.WARNING)
        logging.getLogger('websockets').setLevel(logging.WARNING)
        logging.getLogger('lighter').setLevel(logging.WARNING)

        # File handler
        file_handler = logging.FileHandler(self.log_filename)
        file_handler.setLevel(logging.INFO)
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_formatter)

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter('%(levelname)s: %(message)s')
        console_handler.setFormatter(console_formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.logger.propagate = False

    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def shutdown(self, signum=None, frame=None):
        """Graceful shutdown handler."""
        if self.stop_flag:
            return

        self.stop_flag = True
        self.logger.info("🛑 Shutting down...")

    async def _async_cleanup(self):
        """Async cleanup for exchange connections."""
        if self._cleanup_done:
            return
        self._cleanup_done = True

        try:
            if self.backpack_client:
                await self.backpack_client.disconnect()
                self.logger.info("🔌 Backpack disconnected")
        except Exception as e:
            self.logger.error(f"Error disconnecting Backpack: {e}")

        try:
            if self.lighter_client:
                await self.lighter_client.disconnect()
                self.logger.info("🔌 Lighter disconnected")
        except Exception as e:
            self.logger.error(f"Error disconnecting Lighter: {e}")

        if self.data_logger:
            self.data_logger.close()

    async def _initialize_clients(self):
        """Initialize exchange clients."""
        self.logger.info("🚀 Initializing exchange clients...")

        # Initialize Backpack client
        self.backpack_client = BackpackClient(ticker=self.ticker)
        self.backpack_client.set_logger(self.logger)
        await self.backpack_client.connect()

        # Initialize Lighter client
        self.lighter_client = LighterClient(ticker=self.ticker)
        self.lighter_client.set_logger(self.logger)
        await self.lighter_client.connect()

        # Initialize components
        self.order_book_manager = OrderBookManager(self.logger)
        self.position_tracker = PositionTracker(
            self.backpack_client, self.lighter_client, self.logger
        )
        self.data_logger = DataLogger(self.ticker, self.logger)

        self.logger.info("✅ All clients initialized")

    async def _wait_for_order_books(self, timeout: int = 30):
        """Wait for order books to be ready."""
        self.logger.info("⏳ Waiting for order book data...")

        start_time = time.time()
        while time.time() - start_time < timeout and not self.stop_flag:
            # Check Backpack WebSocket
            bp_ready = (self.backpack_client.ws_manager and
                        self.backpack_client.ws_manager.order_book_ready)

            # Check Lighter WebSocket
            lt_ready = (self.lighter_client.ws_manager and
                        self.lighter_client.ws_manager.order_book_ready)

            if bp_ready and lt_ready:
                self.logger.info("✅ Both order books ready")
                return True

            await asyncio.sleep(0.5)

        self.logger.warning(f"⚠️ Timeout waiting for order books (BP: {bp_ready}, LT: {lt_ready})")
        return False

    async def _update_order_books(self):
        """Update order book manager with latest data from WebSockets."""
        # Update Backpack
        if self.backpack_client.ws_manager:
            bp_bid = self.backpack_client.ws_manager.best_bid
            bp_ask = self.backpack_client.ws_manager.best_ask
            if bp_bid and bp_ask:
                self.order_book_manager.update_backpack_bbo(bp_bid, bp_ask)

        # Update Lighter
        if self.lighter_client.ws_manager:
            lt_bid = self.lighter_client.ws_manager.best_bid
            lt_ask = self.lighter_client.ws_manager.best_ask
            if lt_bid and lt_ask:
                self.order_book_manager.update_lighter_bbo(lt_bid, lt_ask)

    async def _execute_arbitrage(self, direction: str):
        """
        Execute arbitrage trade.
        
        Args:
            direction: 'LONG_BACKPACK' (buy BP, sell LT) or 'SHORT_BACKPACK' (sell BP, buy LT)
        """
        self.total_trades += 1
        self.logger.info(f"🎯 Executing {direction} arbitrage...")

        try:
            if direction == 'LONG_BACKPACK':
                # Buy on Backpack, Sell on Lighter
                bp_side = 'buy'
                lt_side = 'sell'
            else:
                # Sell on Backpack, Buy on Lighter
                bp_side = 'sell'
                lt_side = 'buy'

            # Execute orders concurrently
            bp_result, lt_result = await asyncio.gather(
                self.backpack_client.place_market_order(self.order_quantity, bp_side),
                self.lighter_client.place_market_order(self.order_quantity, lt_side),
                return_exceptions=True
            )

            # Check results
            bp_success = not isinstance(bp_result, Exception) and bp_result.success
            lt_success = not isinstance(lt_result, Exception) and lt_result.success

            if bp_success and lt_success:
                self.successful_trades += 1
                self.logger.info(f"✅ Arbitrage executed successfully!")

                # Update positions
                if bp_side == 'buy':
                    self.position_tracker.update_backpack_position(self.order_quantity)
                    self.position_tracker.update_lighter_position(-self.order_quantity)
                else:
                    self.position_tracker.update_backpack_position(-self.order_quantity)
                    self.position_tracker.update_lighter_position(self.order_quantity)

                # Log trade
                self.data_logger.log_trade(
                    direction=direction,
                    quantity=self.order_quantity,
                    backpack_side=bp_side,
                    backpack_price=bp_result.avg_price or bp_result.price,
                    lighter_side=lt_side,
                    lighter_price=lt_result.avg_price or lt_result.price
                )

                # Log positions
                self.position_tracker.log_positions()

            else:
                self.failed_trades += 1
                self.logger.error(f"❌ Arbitrage failed!")
                if isinstance(bp_result, Exception):
                    self.logger.error(f"   Backpack error: {bp_result}")
                elif not bp_result.success:
                    self.logger.error(f"   Backpack error: {bp_result.error_message}")

                if isinstance(lt_result, Exception):
                    self.logger.error(f"   Lighter error: {lt_result}")
                elif not lt_result.success:
                    self.logger.error(f"   Lighter error: {lt_result.error_message}")

        except Exception as e:
            self.failed_trades += 1
            self.logger.error(f"❌ Error executing arbitrage: {e}")
            self.logger.error(traceback.format_exc())

    def _can_trade(self, direction: str) -> bool:
        """Check if we can execute a trade based on position limits."""
        if self.max_position == Decimal('0'):
            return True  # No limit

        bp_pos = self.position_tracker.get_backpack_position()
        lt_pos = self.position_tracker.get_lighter_position()

        if direction == 'LONG_BACKPACK':
            # Will buy on Backpack, sell on Lighter
            return bp_pos + self.order_quantity <= self.max_position
        else:
            # Will sell on Backpack, buy on Lighter
            return abs(bp_pos - self.order_quantity) <= self.max_position

    async def trading_loop(self):
        """Main trading loop implementing the arbitrage strategy."""
        self.logger.info(f"🚀 Starting Backpack-Lighter arbitrage bot for {self.ticker}")
        self.logger.info(f"   Order quantity: {self.order_quantity}")
        self.logger.info(f"   Long threshold: {self.long_threshold}")
        self.logger.info(f"   Short threshold: {self.short_threshold}")
        self.logger.info(f"   Max position: {self.max_position}")

        try:
            # Initialize clients
            await self._initialize_clients()

            # Wait for order books
            if not await self._wait_for_order_books():
                self.logger.error("❌ Failed to get order book data")
                return

            # Get initial positions
            await self.position_tracker.refresh_positions()
            self.position_tracker.log_positions()

        except Exception as e:
            self.logger.error(f"❌ Initialization failed: {e}")
            self.logger.error(traceback.format_exc())
            return

        # Main loop
        self.logger.info("🔄 Starting main trading loop...")
        last_log_time = time.time()

        while not self.stop_flag:
            try:
                # Update order books from WebSockets
                await self._update_order_books()

                if not self.order_book_manager.is_ready():
                    await asyncio.sleep(self.check_interval)
                    continue

                # Calculate spreads
                long_spread, short_spread = self.order_book_manager.get_spread()

                # Get BBO for logging
                bp_bid, bp_ask = self.order_book_manager.get_backpack_bbo()
                lt_bid, lt_ask = self.order_book_manager.get_lighter_bbo()

                # Determine signal
                signal = 'NONE'
                if long_spread and long_spread > self.long_threshold:
                    signal = 'LONG_BACKPACK'
                elif short_spread and short_spread > self.short_threshold:
                    signal = 'SHORT_BACKPACK'

                # Log BBO data periodically
                current_time = time.time()
                if current_time - last_log_time >= 5:  # Log every 5 seconds
                    self.data_logger.log_bbo(
                        bp_bid, bp_ask, lt_bid, lt_ask,
                        long_spread, short_spread, signal
                    )
                    last_log_time = current_time

                # Check for arbitrage opportunity
                if signal == 'LONG_BACKPACK' and self._can_trade('LONG_BACKPACK'):
                    self.logger.info(
                        f"📈 Long opportunity! Spread: {long_spread} "
                        f"(BP ask: {bp_ask}, LT bid: {lt_bid})"
                    )
                    await self._execute_arbitrage('LONG_BACKPACK')

                elif signal == 'SHORT_BACKPACK' and self._can_trade('SHORT_BACKPACK'):
                    self.logger.info(
                        f"📉 Short opportunity! Spread: {short_spread} "
                        f"(BP bid: {bp_bid}, LT ask: {lt_ask})"
                    )
                    await self._execute_arbitrage('SHORT_BACKPACK')

                await asyncio.sleep(self.check_interval)

            except Exception as e:
                self.logger.error(f"⚠️ Error in trading loop: {e}")
                self.logger.error(traceback.format_exc())
                await asyncio.sleep(1)

    async def run(self):
        """Run the arbitrage bot."""
        self.setup_signal_handlers()

        try:
            await self.trading_loop()
        except KeyboardInterrupt:
            self.logger.info("\n🛑 Interrupted by user...")
        except asyncio.CancelledError:
            self.logger.info("\n🛑 Task cancelled...")
        finally:
            self.logger.info("🔄 Cleaning up...")
            self.shutdown()

            try:
                await asyncio.wait_for(self._async_cleanup(), timeout=10.0)
            except asyncio.TimeoutError:
                self.logger.warning("⚠️ Cleanup timeout")

            # Print summary
            self.logger.info("=" * 50)
            self.logger.info("📊 Trading Summary:")
            self.logger.info(f"   Total trades: {self.total_trades}")
            self.logger.info(f"   Successful: {self.successful_trades}")
            self.logger.info(f"   Failed: {self.failed_trades}")
            self.logger.info("=" * 50)

