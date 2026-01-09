"""Order book management for Backpack and Lighter exchanges."""

import logging
from decimal import Decimal
from typing import Tuple, Optional


class OrderBookManager:
    """Manages order book state for both exchanges."""

    def __init__(self, logger: logging.Logger):
        """Initialize order book manager."""
        self.logger = logger

        # Backpack order book state
        self.backpack_best_bid: Optional[Decimal] = None
        self.backpack_best_ask: Optional[Decimal] = None
        self.backpack_ready = False

        # Lighter order book state
        self.lighter_best_bid: Optional[Decimal] = None
        self.lighter_best_ask: Optional[Decimal] = None
        self.lighter_ready = False

    def update_backpack_bbo(self, best_bid: Decimal, best_ask: Decimal):
        """Update Backpack best bid/ask."""
        self.backpack_best_bid = best_bid
        self.backpack_best_ask = best_ask

        if not self.backpack_ready:
            self.backpack_ready = True
            self.logger.info(f"📊 Backpack order book ready - Bid: {best_bid}, Ask: {best_ask}")

    def update_lighter_bbo(self, best_bid: Decimal, best_ask: Decimal):
        """Update Lighter best bid/ask."""
        self.lighter_best_bid = best_bid
        self.lighter_best_ask = best_ask

        if not self.lighter_ready:
            self.lighter_ready = True
            self.logger.info(f"📊 Lighter order book ready - Bid: {best_bid}, Ask: {best_ask}")

    def get_backpack_bbo(self) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """Get Backpack best bid/ask prices."""
        return self.backpack_best_bid, self.backpack_best_ask

    def get_lighter_bbo(self) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """Get Lighter best bid/ask prices."""
        return self.lighter_best_bid, self.lighter_best_ask

    def is_ready(self) -> bool:
        """Check if both order books are ready."""
        return self.backpack_ready and self.lighter_ready

    def get_spread(self) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """
        Calculate spread between exchanges.
        
        Returns:
            Tuple of:
            - long_backpack_spread: Lighter bid - Backpack ask (profit if > 0: buy Backpack, sell Lighter)
            - short_backpack_spread: Backpack bid - Lighter ask (profit if > 0: buy Lighter, sell Backpack)
        """
        if not self.is_ready():
            return None, None

        if (self.lighter_best_bid and self.backpack_best_ask and
                self.backpack_best_bid and self.lighter_best_ask):
            # Long Backpack: Buy on Backpack (at ask), Sell on Lighter (at bid)
            long_backpack_spread = self.lighter_best_bid - self.backpack_best_ask

            # Short Backpack: Buy on Lighter (at ask), Sell on Backpack (at bid)
            short_backpack_spread = self.backpack_best_bid - self.lighter_best_ask

            return long_backpack_spread, short_backpack_spread

        return None, None

    def get_mid_prices(self) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """Get mid prices for both exchanges."""
        backpack_mid = None
        lighter_mid = None

        if self.backpack_best_bid and self.backpack_best_ask:
            backpack_mid = (self.backpack_best_bid + self.backpack_best_ask) / 2

        if self.lighter_best_bid and self.lighter_best_ask:
            lighter_mid = (self.lighter_best_bid + self.lighter_best_ask) / 2

        return backpack_mid, lighter_mid

