import os
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

from rotkehlchen.errors.misc import RemoteError
from rotkehlchen.exchanges.mexc import MEXC
from rotkehlchen.fval import FVal
from rotkehlchen.history.events.structures.types import HistoryEventSubType
from rotkehlchen.types import Location


@pytest.fixture(name='dummy_db')
def fixture_dummy_db():
    class DummyDB:
        pass

    return DummyDB()


@pytest.fixture(name='dummy_msg_aggregator')
def fixture_dummy_msg_aggregator():
    class DummyMsgAggregator:
        def __init__(self):
            self.errors = []
            self.warnings = []

        def add_error(self, msg):
            self.errors.append(msg)

        def add_warning(self, msg):
            self.warnings.append(msg)

    return DummyMsgAggregator()


@pytest.fixture(name='mexc_instance')
def fixture_mexc_instance(dummy_db, dummy_msg_aggregator):
    return MEXC(
        api_key='dummy_key',
        api_secret=b'dummy_secret',
        name='mexc',
        location=Location.MEXC,
        database=dummy_db,
        msg_aggregator=dummy_msg_aggregator,
    )


def test_validate_credentials_success(mexc_instance):
    mexc_instance.api.get_account_info = MagicMock(return_value={})
    valid, msg = mexc_instance.validate_credentials()
    assert valid is True
    assert msg == ''


def test_validate_credentials_failure(mexc_instance):
    mexc_instance.api.get_account_info = MagicMock(side_effect=RemoteError('Invalid credentials'))
    valid, msg = mexc_instance.validate_credentials()
    assert valid is False
    assert 'Invalid credentials' in msg


def test_validate_credentials_unexpected_error(mexc_instance):
    mexc_instance.api.get_account_info = MagicMock(side_effect=Exception('Boom'))
    valid, msg = mexc_instance.validate_credentials()
    assert valid is False
    assert 'Boom' in msg


def assert_balances_detail(balances, expected):
    assert isinstance(balances, dict)
    for asset, (amount, usd_value) in expected.items():
        assert asset in balances
        assert balances[asset].amount == FVal(amount)
        if usd_value is not None:
            assert balances[asset].usd_value == usd_value


def test_query_balances_success(mexc_instance):
    mexc_instance.api.get_account_info = MagicMock(
        return_value={
            'balances': [
                {'asset': 'BTC', 'free': '0.5', 'locked': '0.1'},
                {'asset': 'ETH', 'free': '1.0', 'locked': '0.0'},
            ],
        },
    )
    balances, msg = mexc_instance.query_balances()
    assert msg == ''
    assert_balances_detail(balances, {'BTC': (0.6, None), 'ETH': (1.0, None)})
    assert not mexc_instance.msg_aggregator.errors
    assert not mexc_instance.msg_aggregator.warnings


@pytest.mark.parametrize(
    ('bad_balances', 'expected_error'),
    [
        ([{'asset': 'BTC', 'locked': '0.1'}], 'Missing key in MEXC balance entry'),
        (
            [{'asset': 'BTC', 'free': 'not_a_number', 'locked': '0.1'}],
            'Error processing MEXC balance entry',
        ),
        ([{}], 'Missing key in MEXC balance entry'),
    ],
)
def test_query_balances_edge_cases(mexc_instance, bad_balances, expected_error):
    mexc_instance.api.get_account_info = MagicMock(return_value={'balances': bad_balances})
    balances, _msg = mexc_instance.query_balances()
    assert isinstance(balances, dict)
    assert len(balances) == 0
    assert expected_error in '\n'.join(mexc_instance.msg_aggregator.errors)


def test_query_balances_remote_error(mexc_instance):
    mexc_instance.api.get_account_info = MagicMock(side_effect=RemoteError('API error'))
    balances, msg = mexc_instance.query_balances()
    assert balances is None
    assert 'API error' in msg
    assert 'API error' in '\n'.join(mexc_instance.msg_aggregator.errors)


def test_query_balances_unexpected_error(mexc_instance):
    mexc_instance.api.get_account_info = MagicMock(side_effect=Exception('Boom'))
    balances, msg = mexc_instance.query_balances()
    assert balances is None
    assert 'Boom' in msg
    assert 'Boom' in '\n'.join(mexc_instance.msg_aggregator.errors)


def assert_events_detail(events, expected_types):
    assert isinstance(events, list)
    event_types = {e.event_subtype for e in events}
    for t in expected_types:
        assert t in event_types


def test_query_trade_history_success(mexc_instance):
    # Patch trade_from_mexc to return dummy events
    with patch('rotkehlchen.exchanges.mexc.trade_from_mexc') as trade_from_mexc:
        trade_from_mexc.return_value = [
            MagicMock(event_subtype=HistoryEventSubType.SPEND, amount=5000.0),
            MagicMock(event_subtype=HistoryEventSubType.RECEIVE, amount=0.1),
            MagicMock(event_subtype=HistoryEventSubType.FEE, amount=0.0001),
        ]
        mexc_instance.api.get_trade_history = MagicMock(
            return_value=[
                {
                    'time': 1640995200000,
                    'isBuyer': True,
                    'qty': '0.1',
                    'price': '50000',
                    'commission': '0.0001',
                    'commissionAsset': 'BTC',
                },
            ],
        )
        events, msg = mexc_instance.query_trade_history('BTCUSDT')
        assert msg == ''
        assert_events_detail(
            events,
            [HistoryEventSubType.SPEND, HistoryEventSubType.RECEIVE, HistoryEventSubType.FEE],
        )
        assert not mexc_instance.msg_aggregator.errors
        assert not mexc_instance.msg_aggregator.warnings


def test_query_trade_history_deserialization_error(mexc_instance):
    # Patch trade_from_mexc to raise DeserializationError
    with patch('rotkehlchen.exchanges.mexc.trade_from_mexc', side_effect=Exception('bad data')):
        mexc_instance.api.get_trade_history = MagicMock(
            return_value=[
                {
                    'time': 1640995200000,
                    'isBuyer': True,
                    'qty': '0.1',
                    'price': '50000',
                    'commission': '0.0001',
                    'commissionAsset': 'BTC',
                },
            ],
        )
        events, msg = mexc_instance.query_trade_history('BTCUSDT')
        assert events == []
        assert 'bad data' in msg or msg == ''
        assert 'bad data' in '\n'.join(mexc_instance.msg_aggregator.errors)


def test_query_trade_history_remote_error(mexc_instance):
    mexc_instance.api.get_trade_history = MagicMock(side_effect=RemoteError('API error'))
    events, msg = mexc_instance.query_trade_history('BTCUSDT')
    assert events == []
    assert 'API error' in msg
    assert 'API error' in '\n'.join(mexc_instance.msg_aggregator.errors)


def test_query_trade_history_unexpected_error(mexc_instance):
    mexc_instance.api.get_trade_history = MagicMock(side_effect=Exception('Boom'))
    events, msg = mexc_instance.query_trade_history('BTCUSDT')
    assert events == []
    assert 'Boom' in msg
    assert 'Boom' in '\n'.join(mexc_instance.msg_aggregator.errors)


def test_query_deposit_withdrawals_success(mexc_instance):
    mexc_instance.api.get_deposit_history = MagicMock(
        return_value=[{'asset': 'BTC', 'amount': '0.5', 'timestamp': 1000}],
    )
    mexc_instance.api.get_withdraw_history = MagicMock(
        return_value=[{'asset': 'ETH', 'amount': '1.0', 'timestamp': 2000}],
    )
    result, msg = mexc_instance.query_deposit_withdrawals()
    assert msg == ''
    assert isinstance(result, dict)
    assert len(result['deposits']) == 1
    assert len(result['withdrawals']) == 1
    assert result['deposits'][0]['asset'] == 'BTC'
    assert result['withdrawals'][0]['asset'] == 'ETH'
    assert not mexc_instance.msg_aggregator.errors
    assert not mexc_instance.msg_aggregator.warnings


def test_query_deposit_withdrawals_remote_error(mexc_instance):
    mexc_instance.api.get_deposit_history = MagicMock(side_effect=RemoteError('API error'))
    mexc_instance.api.get_withdraw_history = MagicMock(return_value=[])
    result, msg = mexc_instance.query_deposit_withdrawals()
    assert result is None
    assert 'API error' in msg
    assert 'API error' in '\n'.join(mexc_instance.msg_aggregator.errors)


def test_query_deposit_withdrawals_unexpected_error(mexc_instance):
    mexc_instance.api.get_deposit_history = MagicMock(side_effect=Exception('Boom'))
    mexc_instance.api.get_withdraw_history = MagicMock(return_value=[])
    result, msg = mexc_instance.query_deposit_withdrawals()
    assert result is None
    assert 'Boom' in msg
    assert 'Boom' in '\n'.join(mexc_instance.msg_aggregator.errors)


def test_query_account_info_success(mexc_instance):
    mexc_instance.api.get_account_info = MagicMock(
        return_value={'canTrade': True, 'accountType': 'SPOT'},
    )
    info, msg = mexc_instance.query_account_info()
    assert msg == ''
    assert info['canTrade'] is True
    assert info['accountType'] == 'SPOT'
    assert not mexc_instance.msg_aggregator.errors
    assert not mexc_instance.msg_aggregator.warnings


def test_query_account_info_remote_error(mexc_instance):
    mexc_instance.api.get_account_info = MagicMock(side_effect=RemoteError('API error'))
    info, msg = mexc_instance.query_account_info()
    assert info is None
    assert 'API error' in msg
    assert 'API error' in '\n'.join(mexc_instance.msg_aggregator.errors)


def test_query_account_info_unexpected_error(mexc_instance):
    mexc_instance.api.get_account_info = MagicMock(side_effect=Exception('Boom'))
    info, msg = mexc_instance.query_account_info()
    assert info is None
    assert 'Boom' in msg
    assert 'Boom' in '\n'.join(mexc_instance.msg_aggregator.errors)


def test_query_online_history_events(mexc_instance):
    # Patch get_symbols and trade/deposit/withdrawal methods
    mexc_instance.api.get_symbols = MagicMock(return_value=['BTCUSDT', 'ETHUSDT'])
    mexc_instance.query_trade_history = MagicMock(
        return_value=([MagicMock(timestamp=1000), MagicMock(timestamp=2000)], ''),
    )
    mexc_instance.query_deposit_withdrawals = MagicMock(
        return_value=(
            {'deposits': [{'timestamp': 1500}], 'withdrawals': [{'timestamp': 2500}]},
            '',
        ),
    )
    events, end_ts = mexc_instance.query_online_history_events(900, 2100)
    assert end_ts == 2100
    # Should include events with timestamp 1000, 1500, 2000 (not 2500)
    assert any(getattr(e, 'timestamp', None) == 1000 for e in events)
    assert any(getattr(e, 'timestamp', None) == 2000 for e in events)
    assert any(isinstance(e, dict) and e.get('timestamp') == 1500 for e in events)
    assert not any(isinstance(e, dict) and e.get('timestamp') == 2500 for e in events)
    assert not mexc_instance.msg_aggregator.errors
    assert not mexc_instance.msg_aggregator.warnings


def test_query_online_history_events_handles_errors(mexc_instance):
    mexc_instance.api.get_symbols = MagicMock(side_effect=Exception('fail symbols'))
    mexc_instance.query_deposit_withdrawals = MagicMock(
        return_value=({'deposits': [], 'withdrawals': []}, ''),
    )
    events, end_ts = mexc_instance.query_online_history_events(0, 100)
    assert isinstance(events, list)
    assert end_ts == 100
    assert 'fail symbols' in '\n'.join(mexc_instance.msg_aggregator.errors)


@pytest.fixture(name='real_mexc_instance')
def fixture_real_mexc_instance(dummy_db, dummy_msg_aggregator):
    """
    Create a real MEXC instance using API keys from environment variables.
    """
    api_key = os.environ.get('ROTKI_MEXC_TEST_API_KEY')
    api_secret = os.environ.get('ROTKI_MEXC_TEST_API_SECRET')

    if not api_key or not api_secret:
        pytest.skip('No MEXC API credentials found in environment variables')

    return MEXC(
        api_key=api_key,
        api_secret=api_secret.encode(),
        name='mexc',
        location=Location.MEXC,
        database=dummy_db,
        msg_aggregator=dummy_msg_aggregator,
    )


@freeze_time('2023-01-01')
def test_mexc_integration(real_mexc_instance):
    """
    Comprehensive integration test for MEXC exchange.

    This test performs real API calls to MEXC and tests the entire backend logic:
    - Raw API access
    - Data querying
    - Asset conversion
    - Error handling

    To run this test, set the following environment variables:
    - ROTKI_MEXC_TEST_API_KEY: Your MEXC API key
    - ROTKI_MEXC_TEST_API_SECRET: Your MEXC API secret

    WARNING: This test will access your real MEXC account data,
    but will not perform any trades or withdrawals.
    """
    # Step 1: Validate credentials
    valid, msg = real_mexc_instance.validate_credentials()
    assert valid is True, f'Credentials validation failed: {msg}'
    assert msg == ''

    # Step 2: Query account info
    account_info, msg = real_mexc_instance.query_account_info()
    assert msg == ''
    assert account_info is not None
    assert 'accountType' in account_info

    # Step 3: Query balances
    balances, msg = real_mexc_instance.query_balances()
    assert msg == ''
    assert isinstance(balances, dict)
    # We don't assert specific balances as they will vary by account

    # Step 4: Test asset conversion
    # Choose a common trading pair for testing
    common_symbols = ['BTCUSDT', 'ETHUSDT']
    for symbol in common_symbols:
        # Only test if the API actually returns trades for this symbol
        trades, msg = real_mexc_instance.query_trade_history(symbol)
        # We don't assert trades content as the account might not have any
        assert isinstance(trades, list)
        assert msg == '' or 'No trade history found' in msg

    # Step 5: Test deposit/withdrawal history
    deposits_withdrawals, msg = real_mexc_instance.query_deposit_withdrawals()
    assert msg == ''
    assert isinstance(deposits_withdrawals, dict)
    assert 'deposits' in deposits_withdrawals
    assert 'withdrawals' in deposits_withdrawals

    # Step 6: Test online history events (combined history)
    # Use a reasonable time range (6 months)
    import time
    six_months_ago = int(time.time()) - (180 * 24 * 60 * 60)
    now = int(time.time())

    events, end_ts = real_mexc_instance.query_online_history_events(
        start_ts=six_months_ago,
        end_ts=now,
    )
    assert isinstance(events, list)
    assert end_ts == now

    # Verify no errors were raised during the entire test
    assert not real_mexc_instance.msg_aggregator.errors, \
        f'Errors occurred: {real_mexc_instance.msg_aggregator.errors}'
