# SnowSyncMD MCP Server

> **This directory is the source for the [`snowsyncmd-mcp`](https://pypi.org/project/snowsyncmd-mcp/) PyPI package.**
> It will move to its own repository. Consumers should install from PyPI — do not import from here directly.

```bash
pip install snowsyncmd-mcp
snowsyncmd-mcp   # starts the MCP server
```

Connects Claude Code, Cursor, and any MCP-compatible AI assistant directly to your
SnowSyncMD Native App so Claude can read Snowflake schema documentation automatically —
no copy-pasting, no manual downloads, no live INFORMATION_SCHEMA queries.

## How it works

```
You ask Claude: "Write a query joining ORDERS to CUSTOMERS"
       ↓
Claude calls: snowflake_search_objects("orders")   → finds ORDERS, FACT_ORDERS, …
Claude calls: snowflake_get_schema("MY_DB","SALES","ORDERS")
Claude calls: snowflake_get_schema("MY_DB","SALES","CUSTOMERS")
       ↓
Claude gets the column list from pre-built Markdown docs (no warehouse spin-up)
       ↓
Claude writes the correct query with real column names and types
```

---

## Installation

```bash
pip install snowsyncmd-mcp
```

---

## Configuration

Add to your Claude Code settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "snowsyncmd": {
      "command": "snowsyncmd-mcp",
      "env": {
        "SNOWFLAKE_ACCOUNT":   "your-account-identifier",
        "SNOWFLAKE_USER":      "your_username",
        "SNOWFLAKE_PASSWORD":  "your_password",
        "SNOWFLAKE_ROLE":      "ACCOUNTADMIN",
        "SNOWFLAKE_WAREHOUSE": "COMPUTE_WH",
        "SNOWSYNCMD_APP":      "snowsyncmd"
      }
    }
  }
}
```

> **Tip:** Put credentials in a `.env` file at the project root instead of
> hardcoding them in settings.json.

---

## Available tools

Claude sees these 9 tools and calls them automatically — no prompting required.

### `snowflake_get_schema`
Returns the full Markdown schema documentation for one Snowflake object.
Covers tables, views, functions, procedures, stages, pipes, tasks, masking policies,
dynamic tables, and more. Each doc includes column names, data types, nullability,
defaults, business descriptions, and a generation timestamp.

**When Claude uses it:** Before writing SQL, before explaining table relationships,
before reviewing any query that touches a specific object.

```
Claude: [calls snowflake_get_schema("SALES_DB","CORE","FACT_ORDERS")]
→ Returns 408-token Markdown doc with all 14 columns, types, and comments
```

---

### `snowflake_search_schema`
Keyword search across all tracked object names using the stage file index.
Returns a list of matching fully-qualified names.

**When Claude uses it:** When the user mentions a table by a partial name or concept
and Claude needs to find the exact DB.SCHEMA.OBJECT path before calling get_schema.

```
Claude: [calls snowflake_search_schema("revenue")]
→ Returns: SALES_DB.CORE.FACT_ORDERS, SALES_DB.REPORTING.V_DAILY_REVENUE
```

---

### `snowflake_search_objects` *(v1.1.0)*
Structured search against the `snowflake_objects_ref` reference table.
Supports keyword match on object name, description, and business context;
filter by database; filter by object type. Returns enriched results including
owner, PII flag, business context, SLA description, and tags alongside the name.

**When Claude uses it:** Exploring unfamiliar databases, finding all tables in a
domain, discovering who owns an object, checking PII status before generating SQL.

```
Claude: [calls snowflake_search_objects(query="customer", object_type="BASE TABLE")]
→ Returns: CUSTOMERS [owner=data_team, pii_flag=true, business_context="..."]
```

---

### `snowflake_get_columns` *(v1.1.0)*
Returns the structured column list for a specific table or view directly from
`snowflake_columns_ref` — without loading the full Markdown schema doc.
Each column entry includes name, data type, nullability, default, and business description.

**When Claude uses it:** Quick column inspection, checking whether a specific column
exists, counting columns, finding nullable columns — when the full MD doc isn't needed.

```
Claude: [calls snowflake_get_columns("SALES_DB","CORE","FACT_ORDERS")]
→ Returns: 14 columns with types and nullability in a tabular format
```

---

### `snowflake_query` *(v1.2.0)*
Execute a read-only SQL query against Snowflake directly from a Claude conversation.
The SQL classifier — running in the MCP Python client under the caller's own credentials — blocks
every mutating statement before it reaches Snowflake. Allowed statement types: `SELECT`, `SHOW`,
`DESCRIBE`, and `EXPLAIN`. Multi-statement injection (semicolon-separated) is also blocked.
Results are capped at 1000 rows. Every call (blocked or allowed) is written to
`core.query_audit_log` in the Native App.

**Security design:**
- Classification runs in Python (not in Snowflake), so queries execute under the caller's own role
- Masking policies are applied correctly — PII stays masked for restricted roles
- Blocked keywords include: `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `DROP`, `ALTER`,
  `TRUNCATE`, `GRANT`, `REVOKE`, `COPY`, `PUT`, `GET`, `EXECUTE`, `CALL`, `SET`, and more

**When Claude uses it:** Row counts, data samples, DISTINCT value checks, NULL checks,
ad-hoc exploration — whenever the user asks to inspect actual data rather than schema.

```
You:    "How many orders were placed last month?"
Claude: [calls snowflake_query("SELECT COUNT(*) FROM FACT_ORDERS WHERE ORDER_DATE >= DATEADD('month',-1,CURRENT_DATE())")]
→ Returns: 1 row — COUNT(*): 14,832

You:    "Show me a sample of the CUSTOMERS table"
Claude: [calls snowflake_query("SELECT * FROM SALES.CORE.CUSTOMERS LIMIT 5")]
→ Returns: 5 rows with masked PII (masking policies applied for caller's role)

You:    "DELETE FROM customers WHERE id=1"
Claude: [calls snowflake_query("DELETE FROM customers WHERE id=1")]
→ Query blocked — Statement type "DELETE" is not allowed. Block logged to audit trail.
```

---

### `snowflake_annotate` *(v1.1.0)*
Writes a team annotation to any tracked Snowflake object. Changes are stored in
`snowflake_objects_ref` and are **immediately visible to all users** — no sync cycle
needed. Supports five enrichment fields:

| Field | Type | Example |
|---|---|---|
| `owner` | text | `"data_team"` |
| `business_context` | text | `"Core revenue table, source of truth for P&L"` |
| `sla_description` | text | `"Refreshed hourly, 99.9% availability SLA"` |
| `pii_flag` | boolean | `"true"` or `"false"` |
| `tags` | JSON | `'{"domain":"finance","cost_centre":"data"}'` |

**When Claude uses it:** When a user says "mark this table as PII", "set the owner of
FACT_ORDERS to data_team", or "add a description to this view".

```
You:    "Mark CUSTOMERS as PII and set the owner to data_team"
Claude: [calls snowflake_annotate("SALES_DB","CORE","CUSTOMERS","pii_flag","true")]
Claude: [calls snowflake_annotate("SALES_DB","CORE","CUSTOMERS","owner","data_team")]
→ Saved. Visible to all users instantly.
```

---

### `snowflake_list_objects`
Lists every tracked object across all registered databases with database, schema,
type, and name. Optionally filtered by database or object type.

**When Claude uses it:** Getting an overview of what's available before starting a
task, counting objects by type, confirming that a database has been registered.

---

### `snowflake_get_status`
Returns SnowSyncMD sync health: task state, registered databases with priorities
and object counts, total objects tracked, MD files present, and last sync timestamp.

**When Claude uses it:** When the user asks if the schema docs are up to date, or
after a sync is triggered to confirm it completed.

---

### `snowflake_sync`
Triggers an immediate sync cycle for all registered databases or one specific database.
Returns objects scanned, changed, and MD files written.

**When Claude uses it:** After the user creates new tables or alters schema and asks
Claude to "refresh" or "update" the documentation.

---

## What's new in 1.3.0

| Tool | 1.2.x | 1.3.0 |
|---|---|---|
| All existing tools | Unchanged | Unchanged |
| `snowflake_query` | Classified in Python client | **Classification moved to Native App** via `api.classify_query` — per-user overrides now consulted before baseline rules |
| `snowflake_set_query_permission` | — | **New** — set ALLOW / DENY / REMOVE for any username+keyword pair; use `*` for all users |
| `snowflake_list_query_permissions` | — | **New** — list all active permission overrides with username, keyword, permission, and note |

### Backend changes in 1.3.0 (requires SnowSyncMD Native App re-deploy)
- `core.query_permissions` — new table: per-user keyword overrides (username, keyword, ALLOW/DENY, note, set_by, set_at)
- `api.classify_query(sql_text, username)` — new stored procedure: strips comments, blocks multi-statement injection, checks per-user overrides from `core.query_permissions`, then applies baseline rules; EXECUTE AS OWNER (never executes user SQL)
- `api.set_query_permission(username, keyword, permission, note)` — new stored procedure: MERGE into `core.query_permissions`; REMOVE deletes the row
- `api.list_query_permissions()` — new stored procedure: returns all active rules

### How per-user permissions work

```
Query comes in: "SHOW DATABASES"   (user: ANALYST1)
                      │
                      ▼
         api.classify_query(sql, 'ANALYST1')
                      │
                      ├─ personal rule for ANALYST1 + SHOW?  → DENY → BLOCKED
                      ├─ wildcard rule for * + SHOW?          → DENY → BLOCKED
                      └─ no override → baseline: SHOW=ALLOWED → OK
```

Priority: **user-specific rule > wildcard (*) rule > baseline**

Examples:

| username | keyword | permission | Effect |
|---|---|---|---|
| `*` | `SHOW` | `DENY` | Nobody can run SHOW |
| `ANALYST1` | `SHOW` | `ALLOW` | ANALYST1 can SHOW even if `*` denies it |
| `ENGINEER1` | `INSERT` | `ALLOW` | ENGINEER1 can INSERT (normally blocked) |
| `ANALYST2` | `SELECT` | `DENY` | ANALYST2 cannot run any SELECT |

---

## What's new in 1.2.0

| Tool | 1.1.x | 1.2.0 |
|---|---|---|
| `snowflake_get_schema` | Returns full MD doc for one object | Unchanged |
| `snowflake_search_schema` | Keyword search on object names (stage-based) | Unchanged |
| `snowflake_list_objects` | List all objects with DB/schema/type | Unchanged |
| `snowflake_get_status` | Sync health, registered databases, last sync | Unchanged |
| `snowflake_sync` | Trigger immediate sync for all or one DB | Unchanged |
| `snowflake_search_objects` | Structured search with enrichment fields | Unchanged |
| `snowflake_get_columns` | Structured column list from ref table | Unchanged |
| `snowflake_annotate` | Real-time object annotations | Unchanged |
| `snowflake_query` | — | **New** — read-only SQL query runner with client-side SQL classifier; blocks all mutating statements; applies caller's masking policies; row cap 1,000; full audit log |

### Backend changes in 1.2.0 (requires SnowSyncMD Native App re-deploy)
- `core.query_audit_log` — new table: every query attempt logged (blocked or allowed) with keyword, status, row count, duration, and error
- `api.log_safe_query` — new stored procedure: audit sink called by the MCP client after each query; uses EXECUTE AS OWNER (Native App framework requirement)
- SQL classification runs client-side in Python so queries execute under the caller's own role — masking policies apply correctly

### What's new in 1.1.0

| Tool | 1.0.0 | 1.1.0 |
|---|---|---|
| `snowflake_get_schema` | Returns full MD doc for one object | Unchanged |
| `snowflake_search_schema` | Keyword search on object names (stage-based) | Unchanged |
| `snowflake_list_objects` | List all objects with DB/schema/type | Unchanged |
| `snowflake_get_status` | Sync health, registered databases, last sync | Unchanged |
| `snowflake_sync` | Trigger immediate sync for all or one DB | Unchanged |
| `snowflake_search_objects` | — | **New** — structured search across `snowflake_objects_ref`; filters by keyword, database, and object type; returns owner, PII flag, business context, and tags alongside names |
| `snowflake_get_columns` | — | **New** — structured column list from `snowflake_columns_ref`; returns column name, type, nullability, default, and description without loading the full MD doc |
| `snowflake_annotate` | — | **New** — write owner, business context, SLA, PII flag, or JSON tags to any object; changes are real-time (no sync required) and immediately visible to all users |

### Backend changes in 1.1.0 (requires SnowSyncMD Native App re-deploy)
- `core.snowflake_objects_ref` — new table: one row per tracked object with full metadata + enrichment fields
- `core.snowflake_columns_ref` — new table: one row per column across all tables and views (1,500+ rows for typical accounts)
- `api.search_objects` — new stored procedure backing `snowflake_search_objects`
- `api.list_columns` — new stored procedure backing `snowflake_get_columns`
- `api.update_enrichment` — new stored procedure backing `snowflake_annotate`
- `generate_md` — updated to MERGE into both ref tables after every MD file write

---

## Example conversations

```
You:    "What columns does FACT_ORDERS have?"
Claude: [calls snowflake_get_schema("SALES","CORE","FACT_ORDERS")]
Claude: "FACT_ORDERS has 14 columns: ORDER_SK (NUMBER, NOT NULL), ORDER_DATE (DATE), ..."

You:    "Find all tables related to customers and tell me which ones have PII"
Claude: [calls snowflake_search_objects("customer", object_type="BASE TABLE")]
Claude: "Found 4 customer tables. CUSTOMERS and CUSTOMER_PII_LOG are flagged as PII."

You:    "Show me the columns of the ORDERS table quickly"
Claude: [calls snowflake_get_columns("SALES","CORE","ORDERS")]
Claude: "ORDERS has 9 columns: ORDER_ID (NUMBER, NOT NULL), CUSTOMER_ID (NUMBER), ..."

You:    "Mark FACT_ORDERS as owned by the data team"
Claude: [calls snowflake_annotate("SALES","CORE","FACT_ORDERS","owner","data_team")]
Claude: "Done. FACT_ORDERS is now annotated with owner=data_team."

You:    "Write a query to show monthly revenue by channel"
Claude: [calls snowflake_search_objects("revenue")]
Claude: [calls snowflake_get_schema for top result]
Claude: "Here's a query using V_DAILY_REVENUE which already aggregates by channel: ..."

You:    "Is the schema documentation up to date?"
Claude: [calls snowflake_get_status]
Claude: "Last synced 3 minutes ago. 191 objects tracked across 2 databases."

You:    "I just added a new table — refresh the docs"
Claude: [calls snowflake_sync]
Claude: "Sync complete. 1 new object found and documented."
```

---

## Upgrading from 1.0.0

```bash
pip install --upgrade snowsyncmd-mcp
```

Then re-deploy the SnowSyncMD Native App to get the new ref tables and procedures:

```bash
snow app run --connection snowsyncmd_deploy
```

---

## Requirements

- SnowSyncMD Native App v1.1+ installed in your Snowflake account
- `ACCOUNTADMIN` or `app_admin` role on the SnowSyncMD app
- Python 3.11+
- Dependencies installed automatically: `mcp`, `snowflake-connector-python`, `python-dotenv`
