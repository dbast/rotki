from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final

import requests

if TYPE_CHECKING:
    from rotkehlchen.mcp.constants import PrivacyMode

DEFAULT_BACKEND_HOST: Final = '127.0.0.1'
DEFAULT_BACKEND_PORT: Final = 4242
DEFAULT_BACKEND_TIMEOUT: Final = 15
DEFAULT_BACKEND_URL: Final = f'http://{DEFAULT_BACKEND_HOST}:{DEFAULT_BACKEND_PORT}/api/1'


@dataclass(frozen=True)
class BackendConfig:
    base_url: str
    timeout: int
    privacy_mode: PrivacyMode


_backend_config = BackendConfig(
    base_url=DEFAULT_BACKEND_URL,
    timeout=DEFAULT_BACKEND_TIMEOUT,
    privacy_mode='balanced',
)


class BackendQueryError(Exception):
    """Raised when the local rotki backend cannot be queried."""


def configure_backend(base_url: str, timeout: int, privacy_mode: PrivacyMode = 'balanced') -> None:
    global _backend_config  # noqa: PLW0603  -- MCP server startup config for registered tools
    _backend_config = BackendConfig(base_url=base_url, timeout=timeout, privacy_mode=privacy_mode)


def get_backend_config() -> BackendConfig:
    return _backend_config


def _api_url(base_url: str, endpoint: str) -> str:
    return f'{base_url.rstrip("/")}/{endpoint.lstrip("/")}'


def request_api(
        base_url: str,
        endpoint: str,
        timeout: int,
        method: str = 'GET',
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = _api_url(base_url=base_url, endpoint=endpoint)
    try:
        if method == 'POST':
            response = requests.post(url=url, json=json_data, params=params, timeout=timeout)
        else:
            response = requests.get(url=url, params=params, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise BackendQueryError(f'Could not connect to rotki backend at {url}: {e!s}') from e

    if response.status_code != HTTPStatus.OK:
        raise BackendQueryError(
            f'rotki backend returned HTTP {response.status_code} for {url}: {response.text}',
        )

    try:
        payload = response.json()
    except ValueError as e:
        raise BackendQueryError(f'rotki backend returned invalid JSON for {url}: {e!s}') from e

    if not isinstance(payload, dict) or 'result' not in payload:
        raise BackendQueryError(f'rotki backend returned an unexpected response for {url}')

    return payload


def query_history_events(
        from_timestamp: int,
        to_timestamp: int,
        limit: int,
        offset: int,
        exclude_ignored_assets: bool,
) -> dict[str, Any]:
    return request_api(
        base_url=_backend_config.base_url,
        endpoint='history/events',
        timeout=_backend_config.timeout,
        method='POST',
        json_data={
            'from_timestamp': from_timestamp,
            'to_timestamp': to_timestamp,
            'limit': limit,
            'offset': offset,
            'exclude_ignored_assets': exclude_ignored_assets,
            'aggregate_by_group_ids': False,
            'order_by_attributes': ['timestamp'],
            'ascending': [True],
        },
    )


def query_all_balances(ignore_cache: bool) -> dict[str, Any]:
    return request_api(
        base_url=_backend_config.base_url,
        endpoint='balances',
        timeout=_backend_config.timeout,
        params={
            'save_data': False,
            'ignore_errors': True,
            'async_query': False,
            'ignore_cache': ignore_cache,
        },
    )
