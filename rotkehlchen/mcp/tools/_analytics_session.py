from __future__ import annotations

import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Final

import polars as pl

from rotkehlchen.mcp.backend import (
    BackendQueryError,
    get_backend_config,
    query_all_balances,
    query_history_events,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from rotkehlchen.mcp.constants import PrivacyMode

PAGE_SIZE: Final = 1000
MAX_CACHED_EVENTS: Final = 50000
DEFAULT_MAX_RESULT_ROWS: Final = 500
MAX_RESULT_ROWS: Final = 5000
REDACTED_TEXT: Final = '[redacted]'
DEFAULT_TABLES: Final = ('history_events',)
AVAILABLE_TABLES: Final = ('history_events', 'balances')
TEXT_COLUMN_NAMES: Final = frozenset({'notes', 'user_notes'})
PUBLIC_VALUE_COLUMN_NAMES: Final = frozenset({
    'amount',
    'asset',
    'price',
    'usd_value',
    'value',
})
PII_COLUMN_NAMES: Final = frozenset({
    'account',
    'account_address',
    'address',
    'actual_group_identifier',
    'group_identifier',
    'location_label',
    'tx_ref',
})
STRICT_IDENTIFIER_COLUMN_NAMES: Final = PII_COLUMN_NAMES | frozenset({'label', 'name'})
ALLOWED_SQL_PREFIXES: Final = ('select ', 'with ')
DENIED_SQL_TOKENS: Final = frozenset({
    'alter',
    'attach',
    'copy',
    'create',
    'delete',
    'drop',
    'insert',
    'replace',
    'truncate',
    'update',
})
SQL_ERROR_HINT: Final = (
    'Use list_tables() and describe_table(table) to inspect the analytics session schema. '
    'For a quick sample, run `select * from <table> limit 1`.'
)
SENSITIVE_IDENTIFIER_RE: Final = re.compile(
    r'(?:'
    r'0x[a-fA-F0-9]{40}|'
    r'0x[a-fA-F0-9]{64}|'
    r'(?:bc1|tb1)[ac-hj-np-z02-9]{20,90}|'
    r'[13][a-km-zA-HJ-NP-Z1-9]{25,34}|'
    r'[1-9A-HJ-NP-Za-km-z]{32,44}'
    r')',
)

_session_seed = secrets.token_bytes(32)


@dataclass(frozen=True)
class TableData:
    frame: pl.DataFrame
    source: dict[str, Any]


@dataclass(frozen=True)
class AnalyticsScope:
    from_timestamp: int
    to_timestamp: int
    include_ignored_assets: bool
    privacy_mode: PrivacyMode


class AnalyticsSession:
    def __init__(self) -> None:
        self._tables: dict[str, TableData] = {}
        self._cache: dict[tuple[str, int, int, bool, PrivacyMode], TableData] = {}
        self._last_scope: AnalyticsScope | None = None

    def refresh(
            self,
            tables: list[str] | None,
            from_timestamp: int,
            to_timestamp: int,
            include_ignored_assets: bool,
            refresh_cache: bool,
    ) -> dict[str, Any]:
        table_names = _normalize_tables(tables)
        scope = AnalyticsScope(
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
            include_ignored_assets=include_ignored_assets,
            privacy_mode=get_backend_config().privacy_mode,
        )
        loaded: dict[str, Any] = {}
        errors: dict[str, Any] = {}

        for table in table_names:
            try:
                table_data = self._load_table(
                    table=table,
                    scope=scope,
                    refresh_cache=refresh_cache,
                )
                self._tables[table] = table_data
                loaded[table] = _table_summary(table=table, table_data=table_data)
            except BackendQueryError as e:
                errors[table] = str(e)
            except (KeyError, TypeError, pl.exceptions.PolarsError) as e:
                errors[table] = str(e)

        self._last_scope = scope
        return {
            'tables': loaded,
            'errors': errors,
            'privacy_mode': scope.privacy_mode,
            'from_timestamp': from_timestamp,
            'to_timestamp': to_timestamp,
        }

    def list_tables(self) -> dict[str, Any]:
        return {
            'tables': {
                table: _table_summary(table=table, table_data=table_data)
                for table, table_data in sorted(self._tables.items())
            },
            'default_tables': list(DEFAULT_TABLES),
            'available_tables': list(AVAILABLE_TABLES),
            'last_scope': None if self._last_scope is None else {
                'from_timestamp': self._last_scope.from_timestamp,
                'to_timestamp': self._last_scope.to_timestamp,
                'include_ignored_assets': self._last_scope.include_ignored_assets,
                'privacy_mode': self._last_scope.privacy_mode,
            },
        }

    def describe_table(self, table: str) -> dict[str, Any]:
        if (table_data := self._tables.get(table)) is None:
            return _error_response(
                error_type='unknown_table',
                message=f'Table {table!r} is not loaded',
                sql='',
                details={'available_tables': list(AVAILABLE_TABLES)},
            )

        return {
            'table': table,
            'columns': [
                {'name': name, 'dtype': str(dtype)}
                for name, dtype in table_data.frame.schema.items()
            ],
            'rows': table_data.frame.height,
            'source': table_data.source,
        }

    def query_sql(
            self,
            sql: str,
            max_rows: int,
            tables: list[str] | None,
            from_timestamp: int,
            to_timestamp: int,
            include_ignored_assets: bool,
            refresh_cache: bool,
    ) -> dict[str, Any]:
        if error := _validate_sql(sql):
            return _error_response(error_type='validation_error', message=error, sql=sql)

        scope = AnalyticsScope(
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
            include_ignored_assets=include_ignored_assets,
            privacy_mode=get_backend_config().privacy_mode,
        )
        if self._should_refresh(tables=tables, scope=scope, refresh_cache=refresh_cache):
            refresh_result = self.refresh(
                tables=tables,
                from_timestamp=from_timestamp,
                to_timestamp=to_timestamp,
                include_ignored_assets=include_ignored_assets,
                refresh_cache=refresh_cache,
            )
            if refresh_result['errors']:
                return _error_response(
                    error_type='refresh_error',
                    message='Failed to load one or more analytics tables',
                    sql=sql,
                    details=refresh_result,
                )

        context = pl.SQLContext()
        for table, table_data in self._tables.items():
            context.register(table, table_data.frame)

        try:
            result_frame = context.execute(sql, eager=True)
        except pl.exceptions.PolarsError as e:
            return _error_response(
                error_type='sql_execution_error',
                message=str(e),
                sql=sql,
                available_columns=_available_columns(self._tables),
                details={
                    'tables': {
                        table: _table_summary(table=table, table_data=table_data)
                        for table, table_data in self._tables.items()
                    },
                },
            )

        bounded_max_rows = min(max(max_rows, 1), MAX_RESULT_ROWS)
        result_rows = result_frame.head(bounded_max_rows).to_dicts()
        return {
            'columns': result_frame.columns,
            'rows': result_rows,
            'row_count': result_frame.height,
            'returned_rows': len(result_rows),
            'result_truncated': result_frame.height > bounded_max_rows,
            'source': {
                'tables': {
                    table: _table_summary(table=table, table_data=table_data)
                    for table, table_data in self._tables.items()
                },
                'privacy_mode': scope.privacy_mode,
            },
        }

    def clear(self) -> None:
        self._tables.clear()
        self._cache.clear()
        self._last_scope = None

    def _should_refresh(
            self,
            tables: list[str] | None,
            scope: AnalyticsScope,
            refresh_cache: bool,
    ) -> bool:
        if refresh_cache or self._last_scope != scope:
            return True
        return any(table not in self._tables for table in _normalize_tables(tables))

    def _load_table(self, table: str, scope: AnalyticsScope, refresh_cache: bool) -> TableData:
        cache_key = (
            table,
            scope.from_timestamp,
            scope.to_timestamp,
            scope.include_ignored_assets,
            scope.privacy_mode,
        )
        if refresh_cache is False and (cached := self._cache.get(cache_key)) is not None:
            return TableData(frame=cached.frame, source=cached.source | {'cache_hit': True})

        if (loader := TABLE_LOADERS.get(table)) is None:
            raise KeyError(f'Unknown analytics table: {table}')
        table_data = loader(scope)

        self._cache[cache_key] = table_data
        return TableData(frame=table_data.frame, source=table_data.source | {'cache_hit': False})


_analytics_session = AnalyticsSession()


def get_analytics_session() -> AnalyticsSession:
    return _analytics_session


def _normalize_tables(tables: list[str] | None) -> list[str]:
    if not tables:
        return list(DEFAULT_TABLES)
    return sorted(set(tables))


def _hash_identifier(value: Any) -> str | None:
    if value is None:
        return None
    digest = hmac.new(_session_seed, str(value).encode(), sha256).hexdigest()[:16]
    return f'anon_{digest}'


def _is_sensitive_identifier(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return SENSITIVE_IDENTIFIER_RE.fullmatch(value) is not None


def _mask_sensitive_identifiers(value: str) -> str:
    return SENSITIVE_IDENTIFIER_RE.sub(
        lambda match: _hash_identifier(match.group(0)) or REDACTED_TEXT,
        value,
    )


def _to_float(value: Any) -> float | None:
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _column_matches(column: str, column_names: set[str] | frozenset[str]) -> bool:
    return any(
        column == identifier_column or column.endswith(f'_{identifier_column}')
        for identifier_column in column_names
    )


def _base_column_name(column: str) -> str:
    for identifier_column in sorted(STRICT_IDENTIFIER_COLUMN_NAMES, key=len, reverse=True):
        if column == identifier_column or column.endswith(f'_{identifier_column}'):
            return identifier_column
    for value_column in sorted(PUBLIC_VALUE_COLUMN_NAMES, key=len, reverse=True):
        if column == value_column or column.endswith(f'_{value_column}'):
            return value_column
    return column


def _json_scalar(value: Any) -> Any:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return json.dumps(value, default=str, sort_keys=True)


def _flatten_mapping(value: dict[str, Any], prefix: str = '') -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, nested_value in value.items():
        column = f'{prefix}_{key}' if prefix else str(key)
        if isinstance(nested_value, dict):
            row.update(_flatten_mapping(nested_value, prefix=column))
        else:
            scalar_value = _json_scalar(nested_value)
            row[column] = scalar_value
            if (
                isinstance(scalar_value, str) and
                (float_value := _to_float(scalar_value)) is not None
            ):
                row[f'{column}_float'] = float_value

    return row


def _sanitize_text_column(
        sanitized: dict[str, Any],
        column: str,
        value: Any,
        privacy_mode: PrivacyMode,
) -> None:
    has_value = value not in (None, '')
    sanitized[f'has_{column}'] = has_value
    sanitized[column] = value if privacy_mode == 'raw' and has_value else (
        REDACTED_TEXT if has_value else None
    )


def _sanitize_identifier_column(
        sanitized: dict[str, Any],
        column: str,
        value: Any,
        privacy_mode: PrivacyMode,
) -> None:
    sanitized[f'{column}_hash'] = _hash_identifier(value)
    if privacy_mode == 'raw' or (
        privacy_mode == 'balanced' and
        _base_column_name(column) == 'location_label' and
        not _is_sensitive_identifier(value)
    ):
        sanitized[column] = value


def _sanitize_row(row: dict[str, Any], privacy_mode: PrivacyMode) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    identifier_columns = (
        STRICT_IDENTIFIER_COLUMN_NAMES if privacy_mode == 'strict' else PII_COLUMN_NAMES
    )
    for column, value in row.items():
        if _column_matches(column=column, column_names=TEXT_COLUMN_NAMES):
            _sanitize_text_column(
                sanitized=sanitized,
                column=column,
                value=value,
                privacy_mode=privacy_mode,
            )
        elif _column_matches(column=column, column_names=identifier_columns):
            _sanitize_identifier_column(
                sanitized=sanitized,
                column=column,
                value=value,
                privacy_mode=privacy_mode,
            )
        elif (
            isinstance(value, str) and
            _base_column_name(column) not in PUBLIC_VALUE_COLUMN_NAMES and
            _to_float(value) is None and
            SENSITIVE_IDENTIFIER_RE.search(value) is not None
        ):
            sanitized[f'{column}_hash'] = _hash_identifier(value)
            if privacy_mode == 'raw':
                sanitized[column] = value
            elif not _is_sensitive_identifier(value):
                sanitized[column] = _mask_sensitive_identifiers(value)
        else:
            sanitized[column] = value

    return sanitized


def _load_history_events(scope: AnalyticsScope) -> TableData:
    rows: list[dict[str, Any]] = []
    offset = 0
    entries_found = None
    entries_limit = None
    entries_total = None

    while len(rows) < MAX_CACHED_EVENTS:
        payload = query_history_events(
            from_timestamp=scope.from_timestamp,
            to_timestamp=int(time.time()) if scope.to_timestamp == 0 else scope.to_timestamp,
            limit=PAGE_SIZE,
            offset=offset,
            exclude_ignored_assets=scope.include_ignored_assets is False,
        )
        result = payload['result']
        if not isinstance(result, dict):
            break

        entries_found = result.get('entries_found')
        entries_limit = result.get('entries_limit')
        entries_total = result.get('entries_total')
        entries = result.get('entries')
        if not isinstance(entries, list) or len(entries) == 0:
            break

        stack = list(reversed(entries))
        while stack:
            if isinstance(entry := stack.pop(), dict):
                rows.append(
                    _sanitize_row(
                        _flatten_mapping(entry),
                        scope.privacy_mode,
                    ),
                )
            elif isinstance(entry, list):
                stack.extend(reversed(entry))
        offset += PAGE_SIZE

        if isinstance(entries_found, int) and offset >= entries_found:
            break

    frame = pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()
    return TableData(
        frame=frame,
        source={
            'kind': 'source_table',
            'is_derived': False,
            'range_scoped': True,
            'endpoint': 'history/events',
            'flattening_strategy': 'recursive_key_prefix',
            'cached_rows': len(rows),
            'cache_truncated': len(rows) >= MAX_CACHED_EVENTS,
            'entries_found': entries_found,
            'entries_limit': entries_limit,
            'entries_total': entries_total,
            'privacy': _privacy_description(scope.privacy_mode),
        },
    )


def _flatten_source_records(value: Any, path: list[str] | None = None) -> list[dict[str, Any]]:
    path = [] if path is None else path
    if isinstance(value, list):
        rows = []
        for index, item in enumerate(value):
            rows.extend(_flatten_source_records(item, path=[*path, str(index)]))
        return rows

    if not isinstance(value, dict):
        return []

    rows = []
    scalar_values = {
        str(key): nested_value
        for key, nested_value in value.items()
        if not isinstance(nested_value, dict | list)
    }
    if scalar_values:
        row = {
            'source_path': '/'.join(path),
            **{f'source_path_{idx}': segment for idx, segment in enumerate(path)},
            **_flatten_mapping(scalar_values),
        }
        rows.append(row)

    for key, nested_value in value.items():
        if isinstance(nested_value, dict | list):
            rows.extend(_flatten_source_records(nested_value, path=[*path, str(key)]))

    return rows


def _load_balances(scope: AnalyticsScope) -> TableData:
    # MCP cache refresh should not force rotki to recalculate balances; that can be slow and
    # should remain an explicit frontend/user action.
    payload = query_all_balances(ignore_cache=False)
    rows = [
        _sanitize_row(row, privacy_mode=scope.privacy_mode)
        for row in _flatten_source_records(payload.get('result'))
    ]
    return TableData(
        frame=pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame(),
        source={
            'kind': 'source_table',
            'is_derived': False,
            'range_scoped': False,
            'endpoint': 'balances',
            'flattening_strategy': 'recursive_source_path_records',
            'privacy': _privacy_description(scope.privacy_mode),
        },
    )


TABLE_LOADERS: Final[dict[str, Callable[[AnalyticsScope], TableData]]] = {
    'history_events': _load_history_events,
    'balances': _load_balances,
}


def _validate_sql(sql: str) -> str | None:
    normalized = ' '.join(sql.strip().lower().split())
    if not normalized.startswith(ALLOWED_SQL_PREFIXES):
        return 'Only read-only SELECT queries over analytics tables are allowed'
    if ';' in normalized.rstrip(';'):
        return 'Only a single SQL statement is allowed'
    tokens = set(normalized.replace(',', ' ').replace('(', ' ').replace(')', ' ').split())
    if disallowed := tokens & DENIED_SQL_TOKENS:
        return f'Disallowed SQL token(s): {", ".join(sorted(disallowed))}'
    return None


def _error_response(
        error_type: str,
        message: str,
        sql: str,
        available_columns: dict[str, list[str]] | None = None,
        details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        'error': {
            'type': error_type,
            'message': message,
            'sql': sql,
            'hint': SQL_ERROR_HINT,
            'available_columns': available_columns or {},
            'details': details or {},
        },
    }


def _available_columns(tables: dict[str, TableData]) -> dict[str, list[str]]:
    return {table: table_data.frame.columns for table, table_data in tables.items()}


def _table_summary(table: str, table_data: TableData) -> dict[str, Any]:
    return {
        'table': table,
        'rows': table_data.frame.height,
        'columns': table_data.frame.columns,
        'source': table_data.source,
    }


def _privacy_description(privacy_mode: PrivacyMode) -> str:
    if privacy_mode == 'raw':
        return 'raw sensitive identifiers and text fields are exposed'
    if privacy_mode == 'strict':
        return 'sensitive identifiers and labels are HMAC-SHA256 anonymized; free text is redacted'
    return (
        'wallet addresses are HMAC-SHA256 anonymized; labels are preserved; '
        'free text is redacted'
    )
