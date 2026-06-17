# rotki MCP

The rotki MCP server exposes local rotki data to MCP-compatible agents through a small,
privacy-aware tool surface.

Start it with:

```bash
python -m rotkehlchen.mcp
```

Useful options:

```bash
python -m rotkehlchen.mcp --backend-url http://127.0.0.1:4242/api/1 --privacy-mode balanced
```

## Tools

### `info`

Returns the MCP package version and checks whether the local rotki backend is reachable and
unlocked.

### Analytics Tools

The analytics tools are intentionally generic. Instead of adding one MCP tool per question,
rotki data is loaded into a local, privacy-filtered analytics session and queried with SQL.

Available tools:

- `refresh_analytics_data`: loads selected rotki tables into the analytics session.
- `list_tables`: lists currently loaded tables and row/column metadata.
- `describe_table`: describes one loaded table.
- `query_sql`: runs read-only SQL over the loaded privacy-filtered tables.

The goal is to support everything from simple inspection:

```sql
select * from history_events limit 1
```

to cross-table analytics:

```sql
select
  h.entry_asset,
  sum(h.entry_amount_float) as total_amount,
  b.value_float as current_value
from history_events h
left join balances b on b.source_path_1 = h.entry_asset
group by h.entry_asset, b.value_float
order by total_amount desc
```

## Analytics Session Design

rotki remains the source of truth. The MCP server fetches data from the existing rotki REST API,
mechanically flattens nested payloads with Polars-friendly column names, applies privacy filtering,
and registers the result as in-memory analytics tables.

The source-backed tables avoid semantic inference:

- `history_events` is loaded by default. It contains paginated `history/events` rows flattened with
  recursive key prefixes such as `entry_asset`, `entry_amount`, and `entry_amount_float`.
- `balances` is available as an opt-in table. It queries cached `/balances` data and flattens it
  into source-path records such as `source_path`, `source_path_0`, `source_path_1`, `amount`, and
  `value`.

Derived views should be explicitly named and marked as derived if they are added later. The source
tables should not infer accounts from labels, prices from balances, or assets from arbitrary paths.

SQL never runs against the raw rotki database. It runs only against the intermediate analytics
tables that have already been scoped and privacy-filtered.

## Privacy Modes

The MCP server supports three privacy modes:

- `balanced` (default): wallet addresses and raw identifiers are hashed, free text is redacted,
  non-address-like location labels are preserved for user-friendly manual lookup in rotki.
- `strict`: raw identifiers and labels are hashed, free text is redacted.
- `raw`: raw identifiers and text fields are exposed.

Privacy mode is configured only at MCP server startup with `--privacy-mode`. Individual MCP tool
calls cannot override it.

The default `balanced` mode lets agents correlate activity and refer to user labels such as
"Main wallet" without exposing the underlying wallet address. If the user needs the raw address,
they can look it up locally in the rotki UI.

Privacy filtering is generic but conservative: known sensitive column names are hashed, free-text
columns are redacted, and address-like values discovered by regex are masked in generic paths or
unexpected identifier columns. Public values such as assets and numeric amounts are preserved.
