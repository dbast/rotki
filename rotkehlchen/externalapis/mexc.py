import hashlib
import hmac
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class MEXCAPI:
    """
    MEXC API client

    See also:
        * Official API documentation: https://mexcdevelop.github.io/apidocs/spot_v3_en/
        * Official SDK (wrapped JavaScript): https://github.com/mexcdevelop/mexc-api-sdk
        * Third Party client (pure Python): https://github.com/ccxt/mexc-python
    """

    BASE_URL = 'https://api.mexc.com'

    def __init__(self, api_key: str, secret_key: str):
        """
        Initialize the MEXCAPI client.

        Args:
            api_key (str): Your MEXC API key.
            secret_key (str): Your MEXC API secret.

        Sets up a requests session with retry logic for robustness.
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=['HEAD', 'GET', 'OPTIONS', 'POST'],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount('https://', adapter)

    def _sign(self, params: dict[str, Any]) -> str:
        """
        Generate HMAC SHA256 signature for signed endpoints.

        Args:
            params (dict[str, Any]): The parameters to be signed.

        Returns:
            str: The resulting signature as a hexadecimal string.

        Reference:
            https://mexcdevelop.github.io/apidocs/spot_v3_en/#signed
        """
        query_string = '&'.join(f'{key}={value}' for key, value in sorted(params.items()))
        return hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()

    def _request(
            self,
            method: str,
            endpoint: str,
            params: dict[str, Any] | None = None,
            signed: bool = False,
    ) -> Any:
        """
        Make a request to the MEXC API.

        Args:
            method (str): HTTP method, e.g., 'GET', 'POST'.
            endpoint (str): API endpoint path, e.g., '/api/v3/account'.
            params (dict[str, Any] | None): Query parameters or body parameters.
            signed (bool): Whether the endpoint requires signing.

        Returns:
            Any: The parsed JSON response from the API.

        Raises:
            requests.HTTPError: If the HTTP request fails.

        Reference:
            https://mexcdevelop.github.io/apidocs/spot_v3_en/
        """
        url = f'{self.BASE_URL}{endpoint}'
        headers = {'X-MEXC-APIKEY': self.api_key}
        if signed:
            params = params or {}
            params['timestamp'] = int(time.time() * 1000)
            params['signature'] = self._sign(params)
        response = self.session.request(method, url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json()

    def get_account_info(self) -> dict[str, Any]:
        """
        Fetch account information for the current API key.

        Returns:
            dict[str, Any]: Account information including balances and permissions.

        Reference:
            https://mexcdevelop.github.io/apidocs/spot_v3_en/#account-information-user_data
        """
        return self._request('GET', '/api/v3/account', signed=True)

    def get_trade_history(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        """
        Fetch trade history for a specific symbol.

        Args:
            symbol (str): The trading pair symbol, e.g., 'BTCUSDT'.
            limit (int): Maximum number of trades to fetch (default: 100).

        Returns:
            list[dict[str, Any]]: List of trade records.

        Reference:
            https://mexcdevelop.github.io/apidocs/spot_v3_en/#account-trade-list-user_data
        """
        params = {'symbol': symbol, 'limit': limit}
        return self._request('GET', '/api/v3/myTrades', params=params, signed=True)

    def get_deposit_history(self) -> list[dict[str, Any]]:
        """
        Fetch deposit history for the account.

        Returns:
            list[dict[str, Any]]: List of deposit records.

        Reference:
            https://mexcdevelop.github.io/apidocs/spot_v3_en/#deposit-history-supporting-network-user_data
        """
        return self._request('GET', '/api/v3/capital/deposit/hisrec', signed=True)

    def get_withdraw_history(self) -> list[dict[str, Any]]:
        """
        Fetch withdrawal history for the account.

        Returns:
            list[dict[str, Any]]: List of withdrawal records.

        Reference:
            https://mexcdevelop.github.io/apidocs/spot_v3_en/#withdraw-history-supporting-network-user_data
        """
        return self._request('GET', '/api/v3/capital/withdraw/history', signed=True)
