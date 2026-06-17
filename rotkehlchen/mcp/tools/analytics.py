from __future__ import annotations

import asyncio
from typing import Any

from rotkehlchen.mcp.registry import register_tool
from rotkehlchen.mcp.tools._analytics_session import (
    DEFAULT_MAX_RESULT_ROWS,
    get_analytics_session,
)


@register_tool(name='refresh_analytics_data')
async def refresh_analytics_data(
        tables: list[str] | None = None,
        from_timestamp: int = 0,
        to_timestamp: int = 0,
        include_ignored_assets: bool = False,
        refresh_cache: bool = True,
) -> dict[str, Any]:
    """Load privacy-filtered rotki data into the local MCP analytics session.

    If `tables` is omitted, the default analytics session loads source-backed history events with
    mechanical flattening and privacy filtering. Large tables such as `history_events` are scoped
    by `from_timestamp` and `to_timestamp`; current endpoint tables such as `balances` are opt-in.
    """
    return await asyncio.to_thread(
        get_analytics_session().refresh,
        tables=tables,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
        include_ignored_assets=include_ignored_assets,
        refresh_cache=refresh_cache,
    )


@register_tool(name='list_tables')
async def list_tables() -> dict[str, Any]:
    """List tables currently loaded in the MCP analytics session."""
    return await asyncio.to_thread(get_analytics_session().list_tables)


@register_tool(name='describe_table')
async def describe_table(table: str) -> dict[str, Any]:
    """Describe a loaded analytics table, including columns, row count, and source metadata."""
    return await asyncio.to_thread(get_analytics_session().describe_table, table=table)


@register_tool(name='query_sql')
async def query_sql(
        sql: str,
        max_rows: int = DEFAULT_MAX_RESULT_ROWS,
        tables: list[str] | None = None,
        from_timestamp: int = 0,
        to_timestamp: int = 0,
        include_ignored_assets: bool = False,
        refresh_cache: bool = False,
) -> dict[str, Any]:
    """Run read-only SQL over privacy-filtered rotki analytics tables.

    This generic analytics tool is intentionally table/data agnostic. Use `list_tables()` and
    `describe_table(table)` to inspect the loaded schema, then query anything from simple
    previews (`select * from history_events limit 1`) to cross-table aggregations.

    The default table is `history_events`; `balances` is available as an opt-in table. Raw
    sensitive identifiers are only present when the MCP server starts with `--privacy-mode raw`.
    """
    return await asyncio.to_thread(
        get_analytics_session().query_sql,
        sql=sql,
        max_rows=max_rows,
        tables=tables,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
        include_ignored_assets=include_ignored_assets,
        refresh_cache=refresh_cache,
    )
