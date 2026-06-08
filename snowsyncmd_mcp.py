#!/usr/bin/env python3
"""
SnowSyncMD MCP Server
=====================
Exposes the SnowSyncMD Native App as MCP tools so Claude Code can
automatically read Snowflake schema documentation without live queries.

Claude sees these tools:
  snowflake_get_schema    – fetch the MD doc for one object
  snowflake_search_schema – full-text search across all schema docs
  snowflake_list_objects  – list every tracked object (filter by DB / type)
  snowflake_get_status    – show sync health and registered databases
  snowflake_sync          – trigger an immediate sync (optional)

Setup (consumer side):
  pip install mcp snowflake-connector-python python-dotenv
  python mcp/snowsyncmd_mcp.py

Then add to Claude Code settings (~/.claude/settings.json):
  {
    "mcpServers": {
      "snowsyncmd": {
        "command": "python3",
        "args": ["/path/to/snowsyncmd_mcp.py"],
        "env": {
          "SNOWFLAKE_ACCOUNT":  "...",
          "SNOWFLAKE_USER":     "...",
          "SNOWFLAKE_PASSWORD": "...",
          "SNOWFLAKE_ROLE":     "ACCOUNTADMIN",
          "SNOWSYNCMD_APP":     "snowsyncmd"
        }
      }
    }
  }

Claude then uses these tools automatically whenever you ask:
  "What columns does ORDERS have?"
  "Write a query joining CUSTOMERS to ORDERS"
  "Which tables track payments?"
"""

import asyncio
import json
import os
import sys
from typing import Any

# ── MCP SDK ──────────────────────────────────────────────────────────────────
try:
    import mcp.server.stdio
    import mcp.types as types
    from mcp.server import Server
except ImportError:
    print("Install the MCP SDK:  pip install mcp", file=sys.stderr)
    sys.exit(1)

# ── Snowflake connector ───────────────────────────────────────────────────────
try:
    import snowflake.connector
except ImportError:
    print("Install Snowflake connector:  pip install snowflake-connector-python",
          file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Snowflake helpers
# ─────────────────────────────────────────────────────────────────────────────

APP = os.environ.get("SNOWSYNCMD_APP", "snowsyncmd")


def _connect():
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", ""),
        role=os.environ.get("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
    )


def _call_proc(proc: str, *args) -> Any:
    """Call a SnowSyncMD stored procedure and return parsed JSON."""
    conn = _connect()
    try:
        cur = conn.cursor()
        placeholders = ", ".join(["'%s'" % str(a).replace("'", "''") for a in args])
        sql = f"CALL {APP}.api.{proc}({placeholders})"
        cur.execute(sql)
        row = cur.fetchone()
        if row:
            val = row[0]
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except Exception:
                    return {"raw": val}
            return val
        return {}
    finally:
        conn.close()


def _get_md_file(database: str, schema: str, object_name: str) -> str | None:
    """Read one MD file directly from the stage."""
    conn = _connect()
    try:
        cur = conn.cursor()
        stage_path = f"@{APP}.core.md_stage/{database}/{schema}/{object_name}.md"
        cur.execute(
            f"SELECT $1 FROM {stage_path} "
            f"(FILE_FORMAT => (TYPE='CSV', FIELD_DELIMITER='NONE', RECORD_DELIMITER='\\n'))"
        )
        lines = [r[0] for r in cur.fetchall() if r[0] is not None]
        return "\n".join(lines) if lines else None
    except Exception:
        return None
    finally:
        conn.close()


def _list_stage_files(database: str | None = None) -> list[dict]:
    """List MD files in the stage."""
    conn = _connect()
    try:
        cur = conn.cursor()
        prefix = f"@{APP}.core.md_stage/"
        if database:
            prefix += f"{database.upper()}/"
        cur.execute(f"LIST {prefix}")
        rows = cur.fetchall()
        result = []
        for r in rows:
            name = r[0]   # md_stage/DB/SCHEMA/OBJECT.md
            parts = name.split("/")
            if len(parts) >= 4:
                result.append({
                    "database":    parts[1],
                    "schema":      parts[2],
                    "object_name": parts[3].replace(".md", ""),
                    "stage_path":  name,
                    "size_bytes":  r[1],
                })
        return result
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# MCP Server
# ─────────────────────────────────────────────────────────────────────────────

server = Server("snowsyncmd")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="snowflake_get_schema",
            description=(
                "Get the full Markdown schema documentation for a specific Snowflake "
                "object (table, view, function, procedure, etc.). "
                "Use this whenever you need column names, data types, or metadata "
                "about a specific object before writing a SQL query."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "database":    {"type": "string", "description": "Database name (uppercase)"},
                    "schema":      {"type": "string", "description": "Schema name (uppercase)"},
                    "object_name": {"type": "string", "description": "Object name (uppercase)"},
                },
                "required": ["database", "schema", "object_name"],
            },
        ),
        types.Tool(
            name="snowflake_search_schema",
            description=(
                "Search across all SnowSyncMD schema documentation. "
                "Returns a list of objects whose names or descriptions match the query. "
                "Use this to discover tables/views when you don't know the exact name."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query":    {"type": "string", "description": "Search term (e.g. 'customer', 'order', 'payment')"},
                    "database": {"type": "string", "description": "Limit to this database (optional)"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="snowflake_list_objects",
            description=(
                "List all Snowflake objects tracked by SnowSyncMD. "
                "Returns database, schema, object name, and object type. "
                "Use this to explore what's available before asking for specific schemas."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "database":    {"type": "string", "description": "Filter by database (optional)"},
                    "object_type": {"type": "string", "description": "Filter by type: TABLE, VIEW, FUNCTION, PROCEDURE, STAGE, PIPE, SEQUENCE, FILE_FORMAT, TASK, STREAM (optional)"},
                },
            },
        ),
        types.Tool(
            name="snowflake_get_status",
            description=(
                "Get the SnowSyncMD sync status: registered databases, object counts, "
                "last sync time, and task state. Use this to check if documentation "
                "is up to date before answering schema questions."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="snowflake_sync",
            description=(
                "Trigger an immediate schema sync for one or all databases. "
                "Use this when the user wants fresh documentation after a DDL change."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database to sync (optional — omit to sync all)"},
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:

    # ── snowflake_get_schema ──────────────────────────────────────────────────
    if name == "snowflake_get_schema":
        db  = arguments["database"].upper()
        sc  = arguments["schema"].upper()
        obj = arguments["object_name"].upper()
        md  = _get_md_file(db, sc, obj)
        if md:
            return [types.TextContent(type="text", text=md)]
        return [types.TextContent(
            type="text",
            text=f"No schema documentation found for {db}.{sc}.{obj}. "
                 "Run snowflake_sync to regenerate, or check the object name."
        )]

    # ── snowflake_search_schema ───────────────────────────────────────────────
    elif name == "snowflake_search_schema":
        query    = arguments["query"].lower()
        database = arguments.get("database")
        files    = _list_stage_files(database)

        # Simple keyword match on object name
        matches = [
            f for f in files
            if query in f["object_name"].lower()
            or query in f["schema"].lower()
            or query in f["database"].lower()
        ]

        if not matches:
            return [types.TextContent(
                type="text",
                text=f"No objects matching '{query}' found in schema documentation."
            )]

        lines = [f"Found {len(matches)} object(s) matching '{query}':\n"]
        for m in matches[:20]:  # cap at 20
            lines.append(
                f"  • {m['database']}.{m['schema']}.{m['object_name']}"
            )
        if len(matches) > 20:
            lines.append(f"  … and {len(matches) - 20} more")
        lines.append(
            "\nUse snowflake_get_schema to read the full documentation for any of these."
        )
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── snowflake_list_objects ────────────────────────────────────────────────
    elif name == "snowflake_list_objects":
        database    = arguments.get("database")
        object_type = arguments.get("object_type", "").upper()

        result = _call_proc("list_md_files", database or "")
        files  = result.get("files", []) if isinstance(result, dict) else []

        if object_type:
            # Filter by checking the object snapshot via status
            pass  # Simplified: show all, type filtering would need snapshot query

        if not files:
            return [types.TextContent(type="text", text="No schema documentation available. Run snowflake_sync first.")]

        by_db: dict = {}
        for f in files:
            key = f"{f.get('database_name','?')}.{f.get('schema_name','?')}"
            by_db.setdefault(key, []).append(f.get("object_name", "?"))

        lines = [f"Tracked objects ({len(files)} total):\n"]
        for group, objs in sorted(by_db.items()):
            lines.append(f"\n📁 {group} ({len(objs)} objects)")
            for o in sorted(objs):
                lines.append(f"   • {o}")
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── snowflake_get_status ──────────────────────────────────────────────────
    elif name == "snowflake_get_status":
        status = _call_proc("get_status")
        lines = ["SnowSyncMD Status\n"]
        lines.append(f"Task state:      {status.get('task_state', '?')}")
        lines.append(f"Objects tracked: {status.get('total_objects_tracked', 0)}")
        lines.append(f"MD files:        {status.get('md_files_present', 0)}")
        lines.append(f"Last scan:       {status.get('last_scan_at', 'Never')}")
        lines.append(f"Pending regen:   {status.get('dirty_count', 0)}")
        lines.append("\nDatabases:")
        for db in status.get("databases", []):
            enabled = "✅" if db.get("is_enabled") else "⛔"
            lines.append(
                f"  {enabled} {db['database_name']}  "
                f"priority={db['priority']}  "
                f"objects={db['object_count']}"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── snowflake_sync ────────────────────────────────────────────────────────
    elif name == "snowflake_sync":
        database = arguments.get("database")
        if database:
            result = _call_proc("sync_database", database.upper())
            msg = f"Sync complete for {database.upper()}."
        else:
            result = _call_proc("sync_now")
            msg = "Sync complete for all databases."

        if isinstance(result, dict):
            scan_r = result.get("scan") or {}
            gen_r  = result.get("generate") or {}
            msg += (
                f"\n  Scanned:  {scan_r.get('objects_scanned', 0)}"
                f"\n  Changed:  {scan_r.get('objects_changed', 0)}"
                f"\n  MD files: {gen_r.get('md_files_written', 0)}"
                f"\n  Duration: {result.get('total_duration_seconds', '?')}s"
            )
        return [types.TextContent(type="text", text=msg)]

    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    async with mcp.server.stdio.stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
