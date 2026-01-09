"""Data logging for arbitrage trading."""

import os
import csv
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional


class DataLogger:
    """Logger for BBO data and trade execution."""

    def __init__(self, ticker: str, logger: logging.Logger, log_dir: str = "logs"):
        """Initialize data logger."""
        self.ticker = ticker
        self.logger = logger
        self.log_dir = log_dir

        # Create log directory
        os.makedirs(log_dir, exist_ok=True)

        # Initialize CSV files
        date_str = datetime.now().strftime("%Y%m%d")
        self.bbo_filename = f"{log_dir}/bbo_{ticker}_{date_str}.csv"
        self.trade_filename = f"{log_dir}/trades_{ticker}_{date_str}.csv"

        # Initialize BBO CSV
        if not os.path.exists(self.bbo_filename):
            with open(self.bbo_filename, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'backpack_bid', 'backpack_ask',
                    'lighter_bid', 'lighter_ask',
                    'long_spread', 'short_spread',
                    'signal'
                ])

        # Initialize trades CSV
        if not os.path.exists(self.trade_filename):
            with open(self.trade_filename, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'direction', 'quantity',
                    'backpack_side', 'backpack_price',
                    'lighter_side', 'lighter_price',
                    'spread_captured', 'pnl_estimate'
                ])

    def log_bbo(self, backpack_bid: Optional[Decimal], backpack_ask: Optional[Decimal],
                lighter_bid: Optional[Decimal], lighter_ask: Optional[Decimal],
                long_spread: Optional[Decimal], short_spread: Optional[Decimal],
                signal: str = 'NONE'):
        """Log BBO data to CSV."""
        try:
            timestamp = datetime.now().isoformat()

            with open(self.bbo_filename, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    timestamp,
                    str(backpack_bid) if backpack_bid else '',
                    str(backpack_ask) if backpack_ask else '',
                    str(lighter_bid) if lighter_bid else '',
                    str(lighter_ask) if lighter_ask else '',
                    str(long_spread) if long_spread else '',
                    str(short_spread) if short_spread else '',
                    signal
                ])
        except Exception as e:
            self.logger.error(f"Error logging BBO: {e}")

    def log_trade(self, direction: str, quantity: Decimal,
                  backpack_side: str, backpack_price: Decimal,
                  lighter_side: str, lighter_price: Decimal):
        """Log trade execution to CSV."""
        try:
            timestamp = datetime.now().isoformat()

            # Calculate spread captured
            if direction == 'LONG_BACKPACK':
                # Bought on Backpack, sold on Lighter
                spread_captured = lighter_price - backpack_price
            else:
                # Bought on Lighter, sold on Backpack
                spread_captured = backpack_price - lighter_price

            pnl_estimate = spread_captured * quantity

            with open(self.trade_filename, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    timestamp, direction, str(quantity),
                    backpack_side, str(backpack_price),
                    lighter_side, str(lighter_price),
                    str(spread_captured), str(pnl_estimate)
                ])

            self.logger.info(
                f"📝 Trade logged: {direction} {quantity} - "
                f"Spread: {spread_captured}, PnL: {pnl_estimate}"
            )

        except Exception as e:
            self.logger.error(f"Error logging trade: {e}")

    def close(self):
        """Close the data logger."""
        pass  # CSV files are closed after each write

