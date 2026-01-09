#!/usr/bin/env python3
"""
Backpack-Lighter Cross-Exchange Arbitrage Bot

This bot monitors price spreads between Backpack and Lighter exchanges,
executing market orders simultaneously when profitable arbitrage opportunities arise.

Usage:
    python arbitrage.py --ticker BTC --size 0.001 --long-threshold 5 --short-threshold 5

Environment Variables Required:
    BACKPACK_PUBLIC_KEY     - Backpack API public key
    BACKPACK_SECRET_KEY     - Backpack API secret key (base64 encoded)
    API_KEY_PRIVATE_KEY     - Lighter API private key
    LIGHTER_ACCOUNT_INDEX   - Lighter account index
    LIGHTER_API_KEY_INDEX   - Lighter API key index
"""

import asyncio
import sys
import argparse
from decimal import Decimal

import dotenv

from strategy.backpack_lighter_arb import BackpackLighterArb


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Backpack-Lighter Cross-Exchange Arbitrage Bot',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Basic usage
    python arbitrage.py --ticker BTC --size 0.001

    # With custom thresholds
    python arbitrage.py --ticker ETH --size 0.01 --long-threshold 3 --short-threshold 3

    # With position limit
    python arbitrage.py --ticker BTC --size 0.001 --max-position 0.1
        """
    )

    parser.add_argument('--ticker', type=str, default='BTC',
                        help='Trading pair symbol (default: BTC)')
    parser.add_argument('--size', type=str, required=True,
                        help='Number of tokens to buy/sell per order')
    parser.add_argument('--long-threshold', type=str, default='5',
                        help='Minimum spread for long Backpack trade (default: 5)')
    parser.add_argument('--short-threshold', type=str, default='5',
                        help='Minimum spread for short Backpack trade (default: 5)')
    parser.add_argument('--max-position', type=str, default='0',
                        help='Maximum position per exchange, 0=unlimited (default: 0)')
    parser.add_argument('--check-interval', type=float, default=0.1,
                        help='Seconds between spread checks (default: 0.1)')

    return parser.parse_args()


def validate_args(args):
    """Validate command line arguments."""
    try:
        size = Decimal(args.size)
        if size <= 0:
            print(f"Error: Size must be positive, got {size}")
            sys.exit(1)
    except Exception as e:
        print(f"Error: Invalid size value: {args.size}")
        sys.exit(1)

    try:
        long_threshold = Decimal(args.long_threshold)
        short_threshold = Decimal(args.short_threshold)
        if long_threshold < 0 or short_threshold < 0:
            print("Error: Thresholds must be non-negative")
            sys.exit(1)
    except Exception as e:
        print(f"Error: Invalid threshold value")
        sys.exit(1)


async def main():
    """Main entry point."""
    args = parse_arguments()

    # Load environment variables
    dotenv.load_dotenv()

    # Validate arguments
    validate_args(args)

    try:
        # Create and run the bot
        bot = BackpackLighterArb(
            ticker=args.ticker.upper(),
            order_quantity=Decimal(args.size),
            long_threshold=Decimal(args.long_threshold),
            short_threshold=Decimal(args.short_threshold),
            max_position=Decimal(args.max_position),
            check_interval=args.check_interval
        )

        await bot.run()

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        print(traceback.format_exc())
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

