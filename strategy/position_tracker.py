"""Position tracking for Backpack and Lighter exchanges."""

import asyncio
import logging
from decimal import Decimal

from exchanges.backpack import BackpackClient
from exchanges.lighter import LighterClient


class PositionTracker:
    """Tracks positions on both exchanges."""

    def __init__(self, backpack_client: BackpackClient, lighter_client: LighterClient,
                 logger: logging.Logger):
        """Initialize position tracker."""
        self.backpack_client = backpack_client
        self.lighter_client = lighter_client
        self.logger = logger

        # Cached positions
        self.backpack_position = Decimal('0')
        self.lighter_position = Decimal('0')

    async def refresh_positions(self) -> bool:
        """Refresh positions from both exchanges."""
        try:
            # Fetch positions in parallel
            bp_pos, lt_pos = await asyncio.gather(
                self.backpack_client.get_position(),
                self.lighter_client.get_position(),
                return_exceptions=True
            )

            if isinstance(bp_pos, Exception):
                self.logger.warning(f"⚠️ Error fetching Backpack position: {bp_pos}")
            else:
                self.backpack_position = bp_pos

            if isinstance(lt_pos, Exception):
                self.logger.warning(f"⚠️ Error fetching Lighter position: {lt_pos}")
            else:
                self.lighter_position = lt_pos

            return True

        except Exception as e:
            self.logger.error(f"❌ Error refreshing positions: {e}")
            return False

    def update_backpack_position(self, delta: Decimal):
        """Update Backpack position by delta."""
        self.backpack_position += delta
        self.logger.info(f"📊 Backpack position updated: {self.backpack_position}")

    def update_lighter_position(self, delta: Decimal):
        """Update Lighter position by delta."""
        self.lighter_position += delta
        self.logger.info(f"📊 Lighter position updated: {self.lighter_position}")

    def get_backpack_position(self) -> Decimal:
        """Get current Backpack position (cached)."""
        return self.backpack_position

    def get_lighter_position(self) -> Decimal:
        """Get current Lighter position (cached)."""
        return self.lighter_position

    def get_net_position(self) -> Decimal:
        """Get net position across both exchanges."""
        return self.backpack_position + self.lighter_position

    def get_total_absolute_position(self) -> Decimal:
        """Get total absolute position across both exchanges."""
        return abs(self.backpack_position) + abs(self.lighter_position)

    def is_position_balanced(self, tolerance: Decimal = Decimal('0.001')) -> bool:
        """Check if positions are balanced (hedged)."""
        return abs(self.get_net_position()) <= tolerance

    def log_positions(self):
        """Log current positions."""
        self.logger.info(
            f"📊 Positions - Backpack: {self.backpack_position}, "
            f"Lighter: {self.lighter_position}, Net: {self.get_net_position()}"
        )

