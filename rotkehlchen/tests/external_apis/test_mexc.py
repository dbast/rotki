import hashlib
import hmac
import unittest
from unittest.mock import Mock, patch

from rotkehlchen.externalapis.mexc import MEXCAPI


class TestMEXCAPI(unittest.TestCase):
    def setUp(self):
        self.api_key = 'test_api_key'
        self.secret_key = 'test_secret_key'
        self.api = MEXCAPI(self.api_key, self.secret_key)

    def fake_response(self, data=None):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = (
            data
            if data is not None
            else {
                'balances': [{'asset': 'BTC', 'free': '0.1', 'locked': '0.2'}],
                'canTrade': True,
                'canWithdraw': True,
                'canDeposit': True,
                'accountType': 'SPOT',
                'permissions': ['SPOT'],
            }
        )
        mock_resp.raise_for_status.return_value = None
        return mock_resp

    def test_sign(self):
        params = {'b': '2', 'a': '1'}
        query_string = 'a=1&b=2'
        expected_signature = hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        signature = self.api._sign(params)
        assert signature == expected_signature

    @patch('rotkehlchen.externalapis.mexc.requests.Session.request')
    def test_get_account_info(self, mock_request):
        mock_request.return_value = self.fake_response()
        result = self.api.get_account_info()
        assert isinstance(result, dict)
        assert 'balances' in result
        assert isinstance(result['balances'], list)
        assert result['canTrade'] is True
        assert result['accountType'] == 'SPOT'

    @patch('rotkehlchen.externalapis.mexc.requests.Session.request')
    def test_get_trade_history(self, mock_request):
        mock_request.return_value = self.fake_response(data=[{'id': 1, 'symbol': 'BTCUSDT'}])
        symbol = 'BTCUSDT'
        result = self.api.get_trade_history(symbol, limit=50)
        assert isinstance(result, list)
        assert result and result[0]['symbol'] == 'BTCUSDT'

    @patch('rotkehlchen.externalapis.mexc.requests.Session.request')
    def test_get_deposit_history(self, mock_request):
        mock_request.return_value = self.fake_response(data=[{'amount': '1.0', 'coin': 'BTC'}])
        result = self.api.get_deposit_history()
        assert isinstance(result, list)
        assert result and result[0]['coin'] == 'BTC'

    @patch('rotkehlchen.externalapis.mexc.requests.Session.request')
    def test_get_withdraw_history(self, mock_request):
        mock_request.return_value = self.fake_response(data=[{'amount': '2.0', 'coin': 'ETH'}])
        result = self.api.get_withdraw_history()
        assert isinstance(result, list)
        assert result and result[0]['coin'] == 'ETH'
