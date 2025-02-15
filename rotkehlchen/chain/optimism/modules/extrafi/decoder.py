import logging
from typing import TYPE_CHECKING, Any

from rotkehlchen.assets.asset import EvmToken
from rotkehlchen.chain.ethereum.utils import (
    asset_normalized_value,
    token_normalized_value,
    token_normalized_value_decimals,
)
from rotkehlchen.chain.evm.decoding.constants import CPT_GAS
from rotkehlchen.chain.evm.decoding.interfaces import DecoderInterface
from rotkehlchen.chain.evm.decoding.structures import (
    DEFAULT_DECODING_OUTPUT,
    DecoderContext,
    DecodingOutput,
)
from rotkehlchen.chain.evm.decoding.types import CounterpartyDetails
from rotkehlchen.chain.evm.decoding.utils import maybe_reshuffle_events
from rotkehlchen.chain.optimism.modules.extrafi.constants import (
    CLAIM,
    CLOSE_POSITION_PARTIALLY,
    CPT_EXTRAFI,
    DEPOSITED,
    EXACT_REPAY,
    EXTRAFI_DISTRIBUTOR,
    EXTRAFI_FARMING_CONTRACT,
    EXTRAFI_POOL_CONTRACT,
    EXTRAFI_STAKING_CONTRACT,
    INVEST_TO_VAULT_POSITION,
    REDEEM,
    REWARD_PAID,
    USER_CHECKPOINT,
    VOTE_ESCROW,
)
from rotkehlchen.constants.resolver import evm_address_to_identifier
from rotkehlchen.history.events.structures.evm_event import EvmEvent, EvmProduct
from rotkehlchen.history.events.structures.types import (
    EventDirection,
    HistoryEventSubType,
    HistoryEventType,
)
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.serialization.deserialize import deserialize_timestamp
from rotkehlchen.types import EvmTokenKind
from rotkehlchen.utils.misc import hex_or_bytes_to_address, hex_or_bytes_to_int, timestamp_to_date

if TYPE_CHECKING:
    from rotkehlchen.chain.evm.decoding.base import BaseDecoderTools
    from rotkehlchen.chain.optimism.node_inquirer import OptimismInquirer
    from rotkehlchen.types import ChecksumEvmAddress
    from rotkehlchen.user_messages import MessagesAggregator

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


class ExtrafiDecoder(DecoderInterface):

    def __init__(
            self,
            optimism_inquirer: 'OptimismInquirer',
            base_tools: 'BaseDecoderTools',
            msg_aggregator: 'MessagesAggregator',
    ) -> None:
        super().__init__(
            evm_inquirer=optimism_inquirer,
            base_tools=base_tools,
            msg_aggregator=msg_aggregator,
        )
        self.extra_token_id = 'eip155:10/erc20:0x2dAD3a13ef0C6366220f989157009e501e7938F8'

    def _decode_deposit_event(self, context: DecoderContext) -> DecodingOutput:
        on_behalf_of = hex_or_bytes_to_address(context.tx_log.topics[2])
        user = hex_or_bytes_to_address(context.tx_log.data[0:32])
        token_amount = hex_or_bytes_to_int(context.tx_log.data[32:64])

        for event in context.decoded_events:
            if (
                event.event_type == HistoryEventType.SPEND and
                event.event_subtype == HistoryEventSubType.NONE and
                event.location_label == user
            ):
                token = event.asset.resolve_to_evm_token()
                if token_normalized_value(
                    token_amount=token_amount,
                    token=token,
                ) != event.balance.amount:
                    continue

                event.event_type = HistoryEventType.DEPOSIT
                event.event_subtype = HistoryEventSubType.DEPOSIT_ASSET
                event.counterparty = CPT_EXTRAFI
                event.extra_data = {'reserve_index': hex_or_bytes_to_int(context.tx_log.topics[1])}  # index of the extrafi position. Used to query for balances later  # noqa: E501
                event.notes = f'Deposit {event.balance.amount} {token.symbol} into Extrafi'
                if on_behalf_of != user:
                    event.notes += f' on behalf of {on_behalf_of}'

                break

        return DEFAULT_DECODING_OUTPUT

    def _decode_withdrawal_event(self, context: DecoderContext) -> DecodingOutput:
        on_behalf_of = hex_or_bytes_to_address(context.tx_log.topics[3])
        user = hex_or_bytes_to_address(context.tx_log.topics[2])
        for event in context.decoded_events:
            if (
                event.event_type == HistoryEventType.RECEIVE and
                event.event_subtype == HistoryEventSubType.NONE and
                event.location_label == user
            ):
                event.event_type = HistoryEventType.WITHDRAWAL
                event.event_subtype = HistoryEventSubType.REMOVE_ASSET
                event.counterparty = CPT_EXTRAFI
                event.notes = f'Withdraw {event.balance.amount} {event.asset.symbol_or_name()} from Extrafi'  # noqa: E501
                if on_behalf_of != user:
                    event.notes += f' on behalf of {on_behalf_of}'

                break

        return DEFAULT_DECODING_OUTPUT

    def _handle_pool_events(self, context: DecoderContext) -> DecodingOutput:
        if context.tx_log.topics[0] == DEPOSITED:
            return self._decode_deposit_event(context)
        if context.tx_log.topics[0] == REDEEM:
            return self._decode_withdrawal_event(context)

        return DEFAULT_DECODING_OUTPUT

    def _handle_claim(self, context: DecoderContext) -> DecodingOutput:
        """Handle claim events for the EXTRA token from the distributor"""
        if context.tx_log.topics[0] != CLAIM:
            return DEFAULT_DECODING_OUTPUT

        recipient = hex_or_bytes_to_address(context.tx_log.topics[1])
        raw_amount = hex_or_bytes_to_int(context.tx_log.data[32:64])
        amount = token_normalized_value_decimals(raw_amount, 18)  # extrafi has 18 decimals

        for event in context.decoded_events:
            if (
                event.event_type == HistoryEventType.RECEIVE and
                event.balance.amount == amount and
                event.asset == self.extra_token_id and
                event.location_label == recipient
            ):
                event.event_subtype = HistoryEventSubType.REWARD
                event.counterparty = CPT_EXTRAFI
                event.notes = f'Claim {amount} EXTRA from Extrafi'
                break
        else:
            log.error(f'Could not match claim event of EXTRA in {context.transaction}')

        return DEFAULT_DECODING_OUTPUT

    def _handle_pool_rewards(self, context: DecoderContext) -> DecodingOutput:
        """Handle rewards claimed for depositing in extrafi pools"""
        if context.tx_log.topics[0] != REWARD_PAID:
            return DEFAULT_DECODING_OUTPUT

        user = hex_or_bytes_to_address(context.tx_log.topics[1])
        token_identifier = evm_address_to_identifier(
            address=hex_or_bytes_to_address(context.tx_log.topics[2]),
            chain_id=self.evm_inquirer.chain_id,
            token_type=EvmTokenKind.ERC20,
        )
        amount = hex_or_bytes_to_int(context.tx_log.data[0:32])
        for event in context.decoded_events:
            if (
                event.location_label == user and
                event.asset == token_identifier and
                event.balance.amount == token_normalized_value(token_amount=amount, token=(token := EvmToken(token_identifier))) and  # noqa: E501
                event.maybe_get_direction() == EventDirection.IN
            ):
                event.event_type = HistoryEventType.RECEIVE
                event.event_subtype = HistoryEventSubType.REWARD
                event.counterparty = CPT_EXTRAFI
                event.notes = f'Claim {event.balance.amount} {token.symbol_or_name()} from Extrafi'
                break
        else:
            log.error(f'Could not find extrafi reward transfer at {context.transaction}')

        return DEFAULT_DECODING_OUTPUT

    def _vote_escrow_contract(self, context: DecoderContext) -> DecodingOutput:
        """Handle lock of extra tokens in the protocol"""
        if context.tx_log.topics[0] != USER_CHECKPOINT:
            return DEFAULT_DECODING_OUTPUT

        amount = token_normalized_value_decimals(
            token_amount=hex_or_bytes_to_int(context.tx_log.data[0:32]),
            token_decimals=18,
        )

        for event in context.decoded_events:
            if (
                event.asset == self.extra_token_id and
                event.balance.amount == amount and
                event.event_type == HistoryEventType.SPEND and
                event.event_subtype == HistoryEventSubType.NONE and
                event.location_label == hex_or_bytes_to_address(context.tx_log.topics[2])
            ):
                locktime = deserialize_timestamp(hex_or_bytes_to_int(context.tx_log.topics[3]))
                event.event_type = HistoryEventType.DEPOSIT
                event.event_subtype = HistoryEventSubType.DEPOSIT_ASSET
                event.counterparty = CPT_EXTRAFI
                event.notes = f'Lock {amount} EXTRA until {timestamp_to_date(locktime, formatstr="%d/%m/%Y %H:%M:%S")}'  # noqa: E501
                event.product = EvmProduct.STAKING
                break
        else:
            log.error(f'Failed to find lock of EXTRA in {context.transaction}')

        return DEFAULT_DECODING_OUTPUT

    def _partially_close_farm_position(self, context: DecoderContext) -> DecodingOutput:
        """Close a position in a farm partially"""
        manager = hex_or_bytes_to_address(context.tx_log.topics[3])
        amount_0_received = hex_or_bytes_to_int(context.tx_log.data[32:64])
        amount_1_received = hex_or_bytes_to_int(context.tx_log.data[64:96])

        for event in context.decoded_events:
            if event.location_label != manager:
                continue

            if (
                event.maybe_get_direction() == EventDirection.IN and
                (
                    asset_normalized_value(amount_0_received, event.asset) == event.balance.amount or  # type: ignore  # noqa: E501
                    asset_normalized_value(amount_1_received, event.asset) == event.balance.amount  # type: ignore
                )
            ):
                event.counterparty = CPT_EXTRAFI
                event.event_type = HistoryEventType.WITHDRAWAL
                event.event_subtype = HistoryEventSubType.REMOVE_ASSET
                event.notes = f"Withdraw {event.balance.amount} {event.asset.symbol_or_name()} from Extrafi's farm"  # noqa: E501
                break
        else:
            log.error(f'Could not find withdrawal event for extrafi at {context.transaction}')

        return DEFAULT_DECODING_OUTPUT

    def _invest_into_farm_position(self, context: DecoderContext) -> DecodingOutput:
        manager = hex_or_bytes_to_address(context.tx_log.topics[3])
        amount_0_invested = hex_or_bytes_to_int(context.tx_log.data[0:32])
        amount_1_invested = hex_or_bytes_to_int(context.tx_log.data[32:64])

        for event in context.decoded_events:
            if (
                event.maybe_get_direction() == EventDirection.OUT and
                event.location_label == manager and
                (
                    event.balance.amount == asset_normalized_value(amount_0_invested, event.asset) or  # type: ignore  # noqa: E501
                    event.balance.amount == asset_normalized_value(amount_1_invested, event.asset)  # type: ignore
                )
            ):
                event.counterparty = CPT_EXTRAFI
                event.event_type = HistoryEventType.DEPOSIT
                event.event_subtype = HistoryEventSubType.DEPOSIT_ASSET
                event.notes = f'Deposit {event.balance.amount} {event.asset.symbol_or_name()} in Extrafi'  # noqa: E501
                event.extra_data = {
                    'vault_id': hex_or_bytes_to_int(context.tx_log.topics[1]),
                    'vault_position': hex_or_bytes_to_int(context.tx_log.topics[2]),
                }
                if amount_0_invested == 0 or amount_1_invested == 0:
                    # It means we have decoded the only existing event and we can exit the logic
                    break
        else:
            log.error(f'Could not find the invest event for extrafi in {context.transaction}')

        return DEFAULT_DECODING_OUTPUT

    def _handle_farm_repayment(self, context: DecoderContext) -> DecodingOutput:
        amount_0_repaid = hex_or_bytes_to_int(context.tx_log.data[32:64])
        amount_1_repaid = hex_or_bytes_to_int(context.tx_log.data[64:96])
        refund_event: EvmEvent | None = None

        # the refund event appears before the send event in the case of an ETH transfer
        # or after the send event in the case of tokens
        for event in context.decoded_events[1:]:
            if event.maybe_get_direction() == EventDirection.IN:
                refund_event = event
                break

        for event in context.decoded_events[1:]:
            if event.maybe_get_direction() != EventDirection.OUT or event.counterparty == CPT_GAS:
                continue

            comparing_amount = event.balance.amount
            if (
                refund_event is not None and
                event.asset == refund_event.asset and
                event.address == refund_event.address
            ):
                # When there is a refund in the transaction we need to check that amount
                # matches taking into account that we sent a little bit more
                comparing_amount -= refund_event.balance.amount

            if not (
                comparing_amount == asset_normalized_value(amount_0_repaid, event.asset) or  # type: ignore
                comparing_amount == asset_normalized_value(amount_1_repaid, event.asset)  # type: ignore
            ):
                continue

            event.counterparty = CPT_EXTRAFI
            event.event_type = HistoryEventType.SPEND
            event.event_subtype = HistoryEventSubType.PAYBACK_DEBT
            event.notes = f'Repay {event.balance.amount} {event.asset.symbol_or_name()} in Extrafi'

            if refund_event is not None:
                refund_event.counterparty = CPT_EXTRAFI
                refund_event.event_type = HistoryEventType.WITHDRAWAL
                refund_event.event_subtype = HistoryEventSubType.REFUND
                refund_event.notes = f'Receive {refund_event.balance.amount} {refund_event.asset.symbol_or_name()} as refund from Extrafi'  # noqa: E501

                maybe_reshuffle_events(
                    ordered_events=[event, refund_event],
                    events_list=context.decoded_events,
                )

            break
        else:
            log.error(f'Could not find repayment event for extrafi in {context.transaction}')

        return DEFAULT_DECODING_OUTPUT

    def _handle_farm_events(self, context: DecoderContext) -> DecodingOutput:
        """Handle creating and closing farm positions"""
        if context.tx_log.topics[0] == CLOSE_POSITION_PARTIALLY:
            return self._partially_close_farm_position(context)
        if context.tx_log.topics[0] == INVEST_TO_VAULT_POSITION:
            return self._invest_into_farm_position(context)
        if context.tx_log.topics[0] == EXACT_REPAY:
            return self._handle_farm_repayment(context)
        return DEFAULT_DECODING_OUTPUT

    def addresses_to_decoders(self) -> dict['ChecksumEvmAddress', tuple[Any, ...]]:
        return {
            EXTRAFI_POOL_CONTRACT: (self._handle_pool_events,),
            EXTRAFI_DISTRIBUTOR: (self._handle_claim,),
            EXTRAFI_STAKING_CONTRACT: (self._handle_pool_rewards,),
            VOTE_ESCROW: (self._vote_escrow_contract,),
            EXTRAFI_FARMING_CONTRACT: (self._handle_farm_events,),
        }

    @staticmethod
    def counterparties() -> tuple[CounterpartyDetails, ...]:
        return (CounterpartyDetails(
            identifier=CPT_EXTRAFI,
            label='Extrafi',
            image='extrafi.svg',
        ),)
