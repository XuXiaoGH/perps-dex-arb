"""
Base exchange client interface.
All exchange implementations should inherit from this class.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple, Type, Union
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from tenacity import RetryCallState, retry, retry_if_exception_type, stop_after_attempt, wait_exponential


def query_retry(
    default_return: Any = None,
    exception_type: Union[Type[Exception], Tuple[Type[Exception], ...]] = (Exception,),
    max_attempts: int = 5,
    min_wait: float = 1,
    max_wait: float = 10,
    reraise: bool = False
):
    """Decorator for retrying queries with exponential backoff."""
    def retry_error_callback(retry_state: RetryCallState):
        print(f"Operation: [{retry_state.fn.__name__}] failed after {retry_state.attempt_number} retries, "
              f"exception: {str(retry_state.outcome.exception())}")
        return default_return

    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception_type(exception_type),
        retry_error_callback=retry_error_callback,
        reraise=reraise
    )


@dataclass
class OrderResult:
    """Standardized order result structure."""
    success: bool
    order_id: Optional[str] = None
    side: Optional[str] = None
    size: Optional[Decimal] = None
    price: Optional[Decimal] = None
    status: Optional[str] = None
    error_message: Optional[str] = None
    filled_size: Optional[Decimal] = None
    avg_price: Optional[Decimal] = None


@dataclass
class OrderInfo:
    """Standardized order information structure."""
    order_id: str
    side: str
    size: Decimal
    price: Decimal
    status: str
    filled_size: Decimal = Decimal('0')
    remaining_size: Decimal = Decimal('0')
    cancel_reason: str = ''


class BaseExchangeClient(ABC):
    """Base class for all exchange clients."""

    def __init__(self, ticker: str):
        """Initialize the exchange client with configuration."""
        self.ticker = ticker
        self.contract_id = None
        self.tick_size = None
        self.min_quantity = None
        self.logger = None

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

    def _log(self, message: str, level: str = "INFO"):
        """Log message using the logger if available."""
        if self.logger:
            self.logger.info(f"[{self.get_exchange_name()}] {message}")
        else:
            print(f"[{self.get_exchange_name()}] [{level}] {message}")

    def round_to_tick(self, price: Decimal) -> Decimal:
        """Round price to tick size."""
        if self.tick_size is None:
            return price
        price = Decimal(str(price))
        return price.quantize(self.tick_size, rounding=ROUND_HALF_UP)

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the exchange (WebSocket, etc.)."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the exchange."""
        pass

    @abstractmethod
    async def fetch_bbo_prices(self) -> Tuple[Decimal, Decimal]:
        """Fetch best bid/ask prices."""
        pass

    @abstractmethod
    async def place_market_order(self, quantity: Decimal, side: str) -> OrderResult:
        """Place a market order."""
        pass

    @abstractmethod
    async def get_position(self) -> Decimal:
        """Get current position."""
        pass

    @abstractmethod
    async def get_contract_info(self) -> Tuple[str, Decimal, Decimal]:
        """Get contract info (contract_id, tick_size, min_quantity)."""
        pass

    @abstractmethod
    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        pass

