import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from rotkehlchen.assets.asset import AssetWithOracles
from rotkehlchen.assets.converters import asset_from_mexc
from rotkehlchen.errors.asset import UnknownAsset, UnsupportedAsset
from rotkehlchen.history.events.structures.base import HistoryBaseEntry
from rotkehlchen.types import Timestamp, TimestampMS

if TYPE_CHECKING:
    from rotkehlchen.db.dbhandler import DBHandler
    from rotkehlchen.user_messages import MessagesAggregator

from rotkehlchen.accounting.structures.balance import Balance
from rotkehlchen.errors.misc import RemoteError
from rotkehlchen.errors.serialization import DeserializationError
from rotkehlchen.exchanges.exchange import ExchangeInterface
from rotkehlchen.externalapis.mexc import MEXCAPI
from rotkehlchen.fval import FVal
from rotkehlchen.history.events.structures.swap import SwapEvent, create_swap_events
from rotkehlchen.types import ApiKey, ApiSecret, AssetAmount, Location

log = logging.getLogger(__name__)


class MEXC(ExchangeInterface):
    """
    MEXC exchange implementation for rotki.

    Provides methods for querying balances, trade history, deposit/withdrawal history,
    and account information, with robust logging and error handling.
    """

    def __init__(
            self,
            api_key: ApiKey,
            api_secret: ApiSecret,
            name: str,
            location: Location,
            database: 'DBHandler',
            msg_aggregator: 'MessagesAggregator',
    ) -> None:
        """
        Initialize the MEXC exchange interface.

        Args:
            api_key (ApiKey): API key for MEXC.
            api_secret (ApiSecret): API secret for MEXC.
            name (str): Exchange name.
            location (Location): Exchange location.
            database: Database handler.
            msg_aggregator: Message aggregator for user-facing messages.
        """
        super().__init__(
            name=name,
            location=location,
            api_key=api_key,
            secret=api_secret,
            database=database,
            msg_aggregator=msg_aggregator,
        )
        self.api = MEXCAPI(api_key, str(api_secret))
        self.first_connection_made = False

    def first_connection(self) -> None:
        """
        Perform any first-connection initialization logic.

        Currently a placeholder for future enhancements (e.g., caching symbols).
        """
        if self.first_connection_made:
            return
        self.first_connection_made = True

    def validate_credentials(self) -> tuple[bool, str]:
        """
        Validate API credentials by attempting to fetch account info.

        Returns:
            tuple[bool, str]: (True, "") if valid, (False, error_message) otherwise.
        """
        try:
            self.api.get_account_info()
        except RemoteError as e:
            msg = f'Failed to validate MEXC credentials: {e!s}'
            log.error(msg)
            self.msg_aggregator.add_error(msg)
            return False, msg
        except Exception as e:
            msg = f'Unexpected error during MEXC credential validation: {e!s}'
            log.error(msg)
            self.msg_aggregator.add_error(msg)
            return False, msg
        else:
            return True, ''

    def query_balances(
            self, **kwargs: Any,
    ) -> tuple[dict[AssetWithOracles, Balance] | None, str]:
        """
        Fetch balances from MEXC.

        Returns:
            tuple[dict[AssetWithOracles, Balance] | None, str]:
                (balances_dict, "") on success,
                (None, error_message) on failure.
        """
        try:
            self.first_connection()
            account_info = self.api.get_account_info()
            balances = account_info.get('balances', [])
            result: dict[AssetWithOracles, Balance] = {}
            for entry in balances:
                try:
                    asset_symbol = entry['asset']
                    free = float(entry['free'])
                    locked = float(entry['locked'])
                    try:
                        asset = asset_from_mexc(asset_symbol)
                    except (UnsupportedAsset, UnknownAsset, DeserializationError) as e:
                        msg = f'Error processing MEXC asset {asset_symbol}: {e!s}'
                        log.error(msg)
                        self.msg_aggregator.add_error(msg)
                        continue

                    result[asset] = Balance(
                        amount=FVal(free + locked),
                        usd_value=FVal(0),
                    )
                except KeyError as e:
                    msg = f'Missing key in MEXC balance entry: {e!s}'
                    log.error(msg)
                    self.msg_aggregator.add_error(msg)
                    continue
                except Exception as e:
                    msg = f'Error processing MEXC balance entry: {e!s}'
                    log.error(msg)
                    self.msg_aggregator.add_error(msg)
                    continue
            log.debug(f'MEXC balance query result: {result}')
        except RemoteError as e:
            msg = f'Failed to fetch balances from MEXC: {e!s}'
            log.error(msg)
            self.msg_aggregator.add_error(msg)
            return None, msg
        except Exception as e:
            msg = f'Unexpected error fetching balances from MEXC: {e!s}'
            log.error(msg)
            self.msg_aggregator.add_error(msg)
            return None, msg
        else:
            return result, ''

    def query_trade_history(self, symbol: str) -> tuple[list[SwapEvent] | None, str]:
        """
        Fetch trade history for a specific symbol and convert to SwapEvents.

        Args:
            symbol (str): Trading symbol (e.g., "BTCUSDT").

        Returns:
            tuple[list[SwapEvent] | None, str]:
                (list of SwapEvents, "") on success,
                (None, error_message) on failure.
        """
        try:
            self.first_connection()
            trades = self.api.get_trade_history(symbol)
            events = []
            for trade in trades:
                try:
                    events.extend(trade_from_mexc(trade, symbol, self.location))
                except DeserializationError as e:
                    msg = f'Failed to deserialize MEXC trade: {e!s}'
                    log.error(msg)
                    self.msg_aggregator.add_error(msg)
                    continue
        except RemoteError as e:
            msg = f'Failed to fetch trade history from MEXC: {e!s}'
            log.error(msg)
            self.msg_aggregator.add_error(msg)
            return [], msg
        except Exception as e:
            msg = f'Unexpected error fetching trade history from MEXC: {e!s}'
            log.error(msg)
            self.msg_aggregator.add_error(msg)
            return [], msg
        else:
            return events, ''

    def query_deposit_withdrawals(self) -> tuple[dict[str, Any] | None, str]:
        """
        Fetch deposit and withdrawal history.

        Returns:
            tuple[dict[str, Any] | None, str]:
                ({"deposits": [...], "withdrawals": [...]}, "") on success,
                (None, error_message) on failure.
        """
        try:
            self.first_connection()
            deposits = self.api.get_deposit_history()
            withdrawals = self.api.get_withdraw_history()
            result = {
                'deposits': deposits,
                'withdrawals': withdrawals,
            }
            log.debug(f'MEXC deposit/withdrawal query result: {result}')
        except RemoteError as e:
            msg = f'Failed to fetch deposit/withdrawal history from MEXC: {e!s}'
            log.error(msg)
            self.msg_aggregator.add_error(msg)
            return None, msg
        except Exception as e:
            msg = f'Unexpected error fetching deposit/withdrawal history from MEXC: {e!s}'
            log.error(msg)
            self.msg_aggregator.add_error(msg)
            return None, msg
        else:
            return result, ''

    def query_online_history_events(
            self,
            start_ts: Timestamp,
            end_ts: Timestamp,
    ) -> tuple[Sequence[HistoryBaseEntry[Any]], Timestamp]:
        """
        Aggregate trade and deposit/withdrawal history for a given time range.

        Args:
            start_ts (Timestamp): Start timestamp (seconds).
            end_ts (Timestamp): End timestamp (seconds).

        Returns:
            tuple[Sequence[HistoryBaseEntry[Any]], Timestamp]: (list of events, end_ts)
        """
        events: list = []
        # Trades
        try:
            symbols = self.api.get_symbols() if hasattr(self.api, 'get_symbols') else []
            for symbol in symbols:
                try:
                    trades, _ = self.query_trade_history(symbol)
                    events.extend(
                        event
                        for event in trades or []
                        if hasattr(event, 'timestamp') and start_ts <= event.timestamp <= end_ts
                    )
                except Exception as e:
                    msg = f'Error fetching trade history for symbol {symbol}: {e!s}'
                    log.error(msg)
                    self.msg_aggregator.add_error(msg)
        except Exception as e:
            msg = f'Error fetching symbols for trade history: {e!s}'
            log.error(msg)
            self.msg_aggregator.add_error(msg)
        # Deposits/Withdrawals
        try:
            asset_movements, _ = self.query_deposit_withdrawals()
            if asset_movements is not None:
                for movement_type in ('deposits', 'withdrawals'):
                    for entry in asset_movements.get(movement_type, []):
                        # Assume each entry has a 'timestamp' field in seconds
                        ts = entry.get('timestamp') or entry.get('time') or entry.get('insertTime')
                        if ts is not None and start_ts <= int(ts) <= end_ts:
                            events.append(entry)
        except Exception as e:
            msg = f'Error fetching deposit/withdrawal history: {e!s}'
            log.error(msg)
            self.msg_aggregator.add_error(msg)
        return events, end_ts

    def query_account_info(self) -> tuple[dict | None, str]:
        """
        Fetch account information (permissions, status, etc.) from MEXC.

        Returns:
            tuple[dict | None, str]:
                (account_info_dict, "") on success,
                (None, error_message) on failure.
        """
        try:
            self.first_connection()
            info = self.api.get_account_info()
            log.debug(f'MEXC account info query result: {info}')
        except RemoteError as e:
            msg = f'Failed to fetch account info from MEXC: {e!s}'
            log.error(msg)
            self.msg_aggregator.add_error(msg)
            return None, msg
        except Exception as e:
            msg = f'Unexpected error fetching account info from MEXC: {e!s}'
            log.error(msg)
            self.msg_aggregator.add_error(msg)
            return None, msg
        else:
            return info, ''

    # TODO: Implement additional features based on MEXC API docs:
    # - Withdraw/deposit address management
    # - Transfers (internal, universal, dust, etc.)
    # - Fee/commission queries
    # - KYC status, sub-account management, rebates, etc.
    # - Websocket support for live updates
    # - More granular error handling for specific MEXC error codes


def trade_from_mexc(mexc_trade: dict, symbol: str, location: Location) -> list[SwapEvent]:
    """
    Convert a trade returned from the MEXC API into SwapEvents.
    This mimics the trade_from_binance pattern.
    """
    # Example fields: 'isBuyer', 'qty', 'price', 'commission', 'commissionAsset', 'time'
    try:
        is_buy = mexc_trade['isBuyer']
        # Handle symbols like 'BTCUSDT' (base: BTC, quote: USDT)
        if '_' in symbol:
            base_asset = symbol.split('_')[0]
            quote_asset = symbol.split('_')[1]
        elif '/' in symbol:
            base_asset = symbol.split('/')[0]
            quote_asset = symbol.split('/')[1]
        else:
            # fallback for symbols like BTCUSDT
            base_asset = symbol[:3]
            quote_asset = symbol[3:]
        spend_asset = asset_from_mexc(quote_asset if is_buy else base_asset)
        receive_asset = asset_from_mexc(base_asset if is_buy else quote_asset)
        spend_amount = (
            FVal(mexc_trade['qty']) * FVal(mexc_trade['price'])
            if is_buy
            else FVal(mexc_trade['qty'])
        )
        receive_amount = (
            FVal(mexc_trade['qty'])
            if is_buy
            else FVal(mexc_trade['qty']) * FVal(mexc_trade['price'])
        )
        spend = AssetAmount(asset=spend_asset, amount=spend_amount)
        receive = AssetAmount(asset=receive_asset, amount=receive_amount)
        fee = (
            AssetAmount(
                asset=asset_from_mexc(mexc_trade['commissionAsset']),
                amount=FVal(mexc_trade['commission']),
            )
            if float(mexc_trade['commission']) > 0
            else None
        )
        timestamp = TimestampMS(int(mexc_trade['time']) // 1000)  # Assuming ms to s
        unique_id = str(mexc_trade.get('id', f"{mexc_trade['time']}_{symbol}"))
        return create_swap_events(
            timestamp=timestamp,
            location=location,
            spend=spend,
            receive=receive,
            fee=fee,
            unique_id=unique_id,
        )
    except Exception as e:
        raise DeserializationError(f'Failed to convert MEXC trade to SwapEvent: {e}') from e
