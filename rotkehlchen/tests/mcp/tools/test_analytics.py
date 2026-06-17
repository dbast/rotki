import asyncio
from inspect import signature
from typing import Any

from rotkehlchen.mcp.backend import DEFAULT_BACKEND_TIMEOUT, DEFAULT_BACKEND_URL, configure_backend
from rotkehlchen.mcp.tools import analytics
from rotkehlchen.mcp.tools._analytics_session import get_analytics_session


def _serialized_event(
        identifier: int,
        amount: str,
        address: str,
        location_label: str,
        notes: str,
) -> dict[str, Any]:
    return {
        'entry': {
            'identifier': identifier,
            'entry_type': 'evm event',
            'asset': 'ETH',
            'amount': amount,
            'address': address,
            'counterparty': 'gas',
            'event_subtype': 'fee',
            'event_type': 'spend',
            'group_identifier': '0xabc',
            'location': 'ethereum',
            'location_label': location_label,
            'sequence_index': identifier,
            'timestamp': 1642802807000 + identifier,
            'tx_ref': '0xabc',
            'user_notes': notes,
        },
        'event_accounting_rule_status': 'not processed',
    }


def _mock_history_payload() -> dict[str, Any]:
    return {
        'result': {
            'entries': [
                _serialized_event(
                    identifier=1,
                    amount='1.5',
                    address='0x2222222222222222222222222222222222222222',
                    location_label='Main wallet',
                    notes='private note one',
                ),
                _serialized_event(
                    identifier=2,
                    amount='2.5',
                    address='0x3333333333333333333333333333333333333333',
                    location_label='Main wallet',
                    notes='private note two',
                ),
            ],
            'entries_found': 2,
            'entries_limit': 100000,
            'entries_total': 2,
        },
        'message': '',
    }


def setup_function() -> None:
    get_analytics_session().clear()
    configure_backend(
        base_url=DEFAULT_BACKEND_URL,
        timeout=DEFAULT_BACKEND_TIMEOUT,
        privacy_mode='balanced',
    )


def test_analytics_tools_should_not_expose_privacy_mode_argument() -> None:
    assert 'privacy_mode' not in signature(analytics.refresh_analytics_data).parameters
    assert 'privacy_mode' not in signature(analytics.query_sql).parameters


def test_query_sql_should_cache_and_query_sanitized_events(monkeypatch) -> None:
    calls = []

    def mock_query_history_events(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _mock_history_payload()

    monkeypatch.setattr(
        'rotkehlchen.mcp.tools._analytics_session.query_history_events',
        mock_query_history_events,
    )

    first_result = asyncio.run(analytics.query_sql(
        sql=(
            'select entry_asset, sum(entry_amount_float) as total_amount '
            'from history_events group by entry_asset'
        ),
        tables=['history_events'],
        from_timestamp=1,
        to_timestamp=2,
    ))
    second_result = asyncio.run(analytics.query_sql(
        sql='select count(*) as event_count from history_events',
        tables=['history_events'],
        from_timestamp=1,
        to_timestamp=2,
    ))

    assert len(calls) == 1
    assert calls[0]['exclude_ignored_assets'] is True
    assert first_result['rows'] == [{'entry_asset': 'ETH', 'total_amount': 4.0}]
    assert second_result['rows'] == [{'event_count': 2}]
    assert first_result['source']['tables']['history_events']['source']['cache_hit'] is False
    assert second_result['source']['tables']['history_events']['source']['cache_hit'] is False


def test_query_sql_should_use_balanced_privacy_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        'rotkehlchen.mcp.tools._analytics_session.query_history_events',
        lambda **kwargs: _mock_history_payload(),
    )

    result = asyncio.run(analytics.query_sql(
        sql=(
            'select entry_address_hash, entry_location_label, entry_location_label_hash, '
            'entry_user_notes '
            'from history_events limit 1'
        ),
        tables=['history_events'],
        from_timestamp=1,
        to_timestamp=2,
    ))

    assert result['rows'][0]['entry_address_hash'].startswith('anon_')
    assert result['rows'][0]['entry_location_label'] == 'Main wallet'
    assert result['rows'][0]['entry_location_label_hash'].startswith('anon_')
    assert result['rows'][0]['entry_user_notes'] == '[redacted]'


def test_query_sql_should_hide_address_like_labels_in_balanced_mode(monkeypatch) -> None:
    def mock_query_history_events(**kwargs: Any) -> dict[str, Any]:
        return {
            'result': {
                'entries': [_serialized_event(
                    identifier=1,
                    amount='1',
                    address='0x2222222222222222222222222222222222222222',
                    location_label='bc1qp7mhptkhn4vfwr7fjs80cmdqlkn93tl9umw39c',
                    notes='private note',
                )],
                'entries_found': 1,
                'entries_limit': 100000,
                'entries_total': 1,
            },
            'message': '',
        }

    monkeypatch.setattr(
        'rotkehlchen.mcp.tools._analytics_session.query_history_events',
        mock_query_history_events,
    )

    result = asyncio.run(analytics.query_sql(
        sql='select * from history_events limit 1',
        tables=['history_events'],
        from_timestamp=1,
        to_timestamp=2,
    ))

    assert 'entry_location_label' not in result['columns']
    assert result['rows'][0]['entry_location_label_hash'].startswith('anon_')


def test_query_sql_should_use_strict_privacy_mode(monkeypatch) -> None:
    configure_backend(
        base_url=DEFAULT_BACKEND_URL,
        timeout=DEFAULT_BACKEND_TIMEOUT,
        privacy_mode='strict',
    )
    monkeypatch.setattr(
        'rotkehlchen.mcp.tools._analytics_session.query_history_events',
        lambda **kwargs: _mock_history_payload(),
    )

    result = asyncio.run(analytics.query_sql(
        sql='select * from history_events limit 1',
        tables=['history_events'],
        from_timestamp=1,
        to_timestamp=2,
    ))

    assert 'entry_address' not in result['columns']
    assert 'entry_location_label' not in result['columns']
    assert 'entry_address_hash' in result['columns']
    assert 'entry_location_label_hash' in result['columns']


def test_query_sql_should_use_raw_privacy_mode(monkeypatch) -> None:
    configure_backend(
        base_url=DEFAULT_BACKEND_URL,
        timeout=DEFAULT_BACKEND_TIMEOUT,
        privacy_mode='raw',
    )
    monkeypatch.setattr(
        'rotkehlchen.mcp.tools._analytics_session.query_history_events',
        lambda **kwargs: _mock_history_payload(),
    )

    result = asyncio.run(analytics.query_sql(
        sql='select entry_address, entry_user_notes from history_events limit 1',
        tables=['history_events'],
        from_timestamp=1,
        to_timestamp=2,
    ))

    assert result['rows'][0]['entry_address'] == '0x2222222222222222222222222222222222222222'
    assert result['rows'][0]['entry_user_notes'] == 'private note one'


def test_query_sql_should_not_mask_public_asset_or_numeric_amount(monkeypatch) -> None:
    def mock_query_history_events(**kwargs: Any) -> dict[str, Any]:
        serialized_event = _serialized_event(
            identifier=1,
            amount='0.176286161691057986868124292038438173006575116',
            address='0x2222222222222222222222222222222222222222',
            location_label='Main wallet',
            notes='private note',
        )
        serialized_event['entry']['asset'] = (
            'eip155:1/erc20:0x3333333333333333333333333333333333333333'
        )
        return {
            'result': {
                'entries': [serialized_event],
                'entries_found': 1,
                'entries_limit': 100000,
                'entries_total': 1,
            },
            'message': '',
        }

    monkeypatch.setattr(
        'rotkehlchen.mcp.tools._analytics_session.query_history_events',
        mock_query_history_events,
    )

    result = asyncio.run(analytics.query_sql(
        sql='select * from history_events limit 1',
        tables=['history_events'],
        from_timestamp=1,
        to_timestamp=2,
    ))

    assert result['rows'][0]['entry_asset'] == (
        'eip155:1/erc20:0x3333333333333333333333333333333333333333'
    )
    assert result['rows'][0]['entry_amount'] == (
        '0.176286161691057986868124292038438173006575116'
    )
    assert 'entry_asset_hash' not in result['columns']
    assert 'entry_amount_hash' not in result['columns']


def test_describe_table_should_return_loaded_schema(monkeypatch) -> None:
    monkeypatch.setattr(
        'rotkehlchen.mcp.tools._analytics_session.query_history_events',
        lambda **kwargs: _mock_history_payload(),
    )

    asyncio.run(analytics.refresh_analytics_data(
        tables=['history_events'],
        from_timestamp=1,
        to_timestamp=2,
    ))
    result = asyncio.run(analytics.describe_table(table='history_events'))

    assert result['table'] == 'history_events'
    assert {'name': 'entry_asset', 'dtype': 'String'} in result['columns']
    assert result['rows'] == 2
    assert result['source']['kind'] == 'source_table'
    assert result['source']['is_derived'] is False


def test_query_sql_should_return_sql_execution_error(monkeypatch) -> None:
    monkeypatch.setattr(
        'rotkehlchen.mcp.tools._analytics_session.query_history_events',
        lambda **kwargs: _mock_history_payload(),
    )

    result = asyncio.run(analytics.query_sql(
        sql='select unknown_column from history_events',
        tables=['history_events'],
        from_timestamp=1,
        to_timestamp=2,
    ))

    assert result['error']['type'] == 'sql_execution_error'
    assert result['error']['sql'] == 'select unknown_column from history_events'
    assert 'unknown_column' in result['error']['message']
    assert 'entry_asset' in result['error']['available_columns']['history_events']


def test_query_sql_should_reject_non_select_sql() -> None:
    result = asyncio.run(analytics.query_sql(
        sql='delete from history_events',
        tables=['history_events'],
        from_timestamp=1,
        to_timestamp=2,
    ))

    assert result['error']['type'] == 'validation_error'
    assert result['error']['message'] == (
        'Only read-only SELECT queries over analytics tables are allowed'
    )


def test_refresh_balances_should_not_force_backend_cache_refresh(monkeypatch) -> None:
    calls = []

    def mock_query_all_balances(ignore_cache: bool) -> dict[str, Any]:
        calls.append(ignore_cache)
        return {
            'result': {
                'assets': {
                    'ETH': {
                        'amount': '2',
                        'value': '4000',
                    },
                },
            },
            'message': '',
        }

    monkeypatch.setattr(
        'rotkehlchen.mcp.tools._analytics_session.query_all_balances',
        mock_query_all_balances,
    )

    result = asyncio.run(analytics.refresh_analytics_data(
        tables=['balances'],
        refresh_cache=True,
    ))

    assert calls == [False]
    assert result['tables']['balances']['rows'] == 1
    assert result['tables']['balances']['source']['is_derived'] is False


def test_query_balances_should_preserve_source_shape(monkeypatch) -> None:
    def mock_query_all_balances(ignore_cache: bool) -> dict[str, Any]:
        return {
            'result': {
                'assets': {
                    'ETH': {
                        'amount': '2',
                        'value': '4000',
                    },
                },
            },
            'message': '',
        }

    monkeypatch.setattr(
        'rotkehlchen.mcp.tools._analytics_session.query_all_balances',
        mock_query_all_balances,
    )

    result = asyncio.run(analytics.query_sql(
        sql='select source_path, source_path_1, amount, value, value_float from balances',
        tables=['balances'],
        refresh_cache=True,
    ))

    assert result['rows'] == [{
        'source_path': 'assets/ETH',
        'source_path_1': 'ETH',
        'amount': '2',
        'value': '4000',
        'value_float': 4000.0,
    }]


def test_query_balances_should_mask_address_like_path_values(monkeypatch) -> None:
    def mock_query_all_balances(ignore_cache: bool) -> dict[str, Any]:
        return {
            'result': {
                'blockchains': {
                    'ethereum': {
                        '0x2222222222222222222222222222222222222222': {
                            'assets': {
                                'ETH': {'amount': '2'},
                            },
                        },
                    },
                },
            },
            'message': '',
        }

    monkeypatch.setattr(
        'rotkehlchen.mcp.tools._analytics_session.query_all_balances',
        mock_query_all_balances,
    )

    result = asyncio.run(analytics.query_sql(
        sql='select source_path, source_path_2_hash from balances',
        tables=['balances'],
        refresh_cache=True,
    ))

    assert '0x2222222222222222222222222222222222222222' not in result['rows'][0]['source_path']
    assert result['rows'][0]['source_path_2_hash'].startswith('anon_')
