"""
server.py
=========
MCP server — translates Claude's tool calls into SnowSyncMDClient calls.

This module knows nothing about Snowflake directly.
All Snowflake logic lives in client.py.
"""

import asyncio
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

from .client import SnowSyncMDClient


def create_server(client: SnowSyncMDClient) -> Server:
    server = Server("snowsyncmd")

    # ── tool definitions ──────────────────────────────────────────────────────

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="snowflake_get_schema",
                description=(
                    "Get the full Markdown schema doc for a specific Snowflake object "
                    "(table, view, function, procedure, stage, etc.). "
                    "Call this before writing SQL to get accurate column names and types."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database":    {"type": "string"},
                        "schema":      {"type": "string"},
                        "object_name": {"type": "string"},
                    },
                    "required": ["database", "schema", "object_name"],
                },
            ),
            types.Tool(
                name="snowflake_search_schema",
                description=(
                    "Search all schema docs by keyword. Use when you don't know "
                    "the exact table/view name. Returns matching object names."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query":    {"type": "string", "description": "e.g. 'customer', 'order', 'payment'"},
                        "database": {"type": "string", "description": "Limit to one database (optional)"},
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="snowflake_list_objects",
                description="List all tracked Snowflake objects with their database, schema, and type.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database":    {"type": "string"},
                        "object_type": {"type": "string", "description": "TABLE, VIEW, FUNCTION, PROCEDURE, STAGE, etc."},
                    },
                },
            ),
            types.Tool(
                name="snowflake_get_status",
                description="Check SnowSyncMD sync health: registered databases, object counts, last sync time.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="snowflake_sync",
                description="Trigger an immediate schema sync. Use after DDL changes.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Sync one DB only (optional)"},
                    },
                },
            ),
            types.Tool(
                name="snowflake_search_objects",
                description=(
                    "Search all tracked Snowflake objects using the structured reference table. "
                    "Returns object type, owner, business context, and PII flag alongside names. "
                    "Faster and richer than snowflake_search_schema — use this when exploring unfamiliar databases."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query":       {"type": "string",  "description": "Keyword to match on object name or description"},
                        "database":    {"type": "string",  "description": "Filter to one database (optional)"},
                        "object_type": {"type": "string",  "description": "TABLE, VIEW, FUNCTION, PROCEDURE, STAGE, etc. (optional)"},
                    },
                },
            ),
            types.Tool(
                name="snowflake_get_columns",
                description=(
                    "Get the structured column list for a specific Snowflake table or view. "
                    "Returns column names, data types, nullability, defaults, and business descriptions. "
                    "Use when you need to inspect columns without loading the full schema doc."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database":    {"type": "string"},
                        "schema":      {"type": "string"},
                        "object_name": {"type": "string"},
                    },
                    "required": ["database", "schema", "object_name"],
                },
            ),
            types.Tool(
                name="snowflake_query",
                description=(
                    "Execute a read-only SQL query against Snowflake. "
                    "Only SELECT, SHOW, DESCRIBE, and EXPLAIN are allowed — "
                    "INSERT, UPDATE, DELETE, MERGE, CREATE, DROP, and all other "
                    "mutating statements are automatically blocked and logged. "
                    "Runs under the caller's own role and masking policies (PII stays masked). "
                    "Results are capped at 1000 rows. "
                    "Use for: row counts, data samples, DISTINCT value checks, NULL checks, "
                    "ad-hoc exploration of any table or view."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "The SQL query to run. Must be SELECT, SHOW, DESCRIBE, or EXPLAIN.",
                        },
                    },
                    "required": ["sql"],
                },
            ),
            types.Tool(
                name="snowflake_set_query_permission",
                description=(
                    "Set or remove a per-user SQL keyword permission override in core.query_permissions. "
                    "Use to grant a normally-blocked keyword to a specific user (ALLOW), "
                    "block a normally-allowed keyword for a specific user (DENY), "
                    "or restore baseline behaviour (REMOVE). "
                    "Use username='*' to apply to all users. "
                    "Baseline: SELECT, SHOW, DESCRIBE, EXPLAIN are allowed; "
                    "INSERT, UPDATE, DELETE, MERGE, DROP, CREATE, ALTER, GRANT, etc. are blocked."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "username":   {"type": "string", "description": "Snowflake username or '*' for all users"},
                        "keyword":    {"type": "string", "description": "SQL first keyword: SELECT, INSERT, SHOW, DROP, etc."},
                        "permission": {"type": "string", "enum": ["ALLOW", "DENY", "REMOVE"],
                                       "description": "ALLOW=grant, DENY=block, REMOVE=restore baseline"},
                        "note":       {"type": "string", "description": "Optional reason for the rule"},
                    },
                    "required": ["username", "keyword", "permission"],
                },
            ),
            types.Tool(
                name="snowflake_list_query_permissions",
                description=(
                    "List all per-user SQL keyword permission overrides from core.query_permissions. "
                    "Shows who can or cannot run which statement types, and why. "
                    "Use before setting a new permission to see what's already in place."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="snowflake_annotate",
                description=(
                    "Write a team annotation to a Snowflake object. Changes are immediately "
                    "visible to all users — no sync needed. "
                    "Use to record owner, business context, SLA, PII flag, or freeform tags. "
                    "field must be one of: owner, business_context, sla_description, pii_flag, tags."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "database":    {"type": "string"},
                        "schema":      {"type": "string"},
                        "object_name": {"type": "string"},
                        "field":       {"type": "string", "enum": ["owner", "business_context", "sla_description", "pii_flag", "tags"]},
                        "value":       {"type": "string", "description": "For pii_flag use 'true'/'false'. For tags use JSON e.g. '{\"domain\":\"finance\"}'"},
                    },
                    "required": ["database", "schema", "object_name", "field", "value"],
                },
            ),
        ]

    # ── tool handlers ─────────────────────────────────────────────────────────

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:

        if name == "snowflake_get_schema":
            doc = client.get_schema(
                arguments["database"],
                arguments["schema"],
                arguments["object_name"],
            )
            text = doc if doc else (
                f"No documentation found for "
                f"{arguments['database']}.{arguments['schema']}.{arguments['object_name']}. "
                "Run snowflake_sync to regenerate, or verify the object exists."
            )
            return [types.TextContent(type="text", text=text)]

        if name == "snowflake_search_schema":
            hits = client.search(arguments["query"], arguments.get("database"))
            if not hits:
                return [types.TextContent(
                    type="text",
                    text=f"No objects matching '{arguments['query']}'.",
                )]
            lines = [f"Found {len(hits)} match(es) for '{arguments['query']}':\n"]
            for h in hits[:20]:
                lines.append(f"  • {h.full_name}")
            if len(hits) > 20:
                lines.append(f"  … {len(hits) - 20} more")
            lines.append("\nUse snowflake_get_schema to read the full doc for any object.")
            return [types.TextContent(type="text", text="\n".join(lines))]

        if name == "snowflake_list_objects":
            objs = client.list_objects(
                arguments.get("database"),
                arguments.get("object_type"),
            )
            if not objs:
                return [types.TextContent(
                    type="text", text="No objects tracked yet. Run snowflake_sync first."
                )]
            by_db: dict = {}
            for o in objs:
                by_db.setdefault(f"{o.database}.{o.schema}", []).append(o.object_name)
            lines = [f"Tracked objects ({len(objs)} total):\n"]
            for group, names in sorted(by_db.items()):
                lines.append(f"\n📁 {group} ({len(names)} objects)")
                for n in sorted(names):
                    lines.append(f"   • {n}")
            return [types.TextContent(type="text", text="\n".join(lines))]

        if name == "snowflake_get_status":
            s = client.get_status()
            lines = [
                "SnowSyncMD Status",
                f"  Task:     {s.task_state}",
                f"  Objects:  {s.total_objects}",
                f"  MD files: {s.md_files_present}",
                f"  Pending:  {s.dirty_count}",
                f"  Last sync: {s.last_scan_at or 'Never'}",
                "\nDatabases:",
            ]
            for db in s.databases:
                icon = "✅" if db.get("is_enabled") else "⛔"
                lines.append(
                    f"  {icon} {db['database_name']} "
                    f"[{db['priority']}]  {db['object_count']} objects"
                )
            return [types.TextContent(type="text", text="\n".join(lines))]

        if name == "snowflake_sync":
            result = client.sync(arguments.get("database"))
            db_label = arguments.get("database", "all databases").upper()
            scan_r = result.get("scan") or {}
            gen_r  = result.get("generate") or {}
            text = (
                f"Sync complete for {db_label}.\n"
                f"  Scanned:   {scan_r.get('objects_scanned', 0)}\n"
                f"  Changed:   {scan_r.get('objects_changed', 0)}\n"
                f"  MD files:  {gen_r.get('md_files_written', 0)}\n"
                f"  Duration:  {result.get('total_duration_seconds', '?')}s"
            )
            return [types.TextContent(type="text", text=text)]

        if name == "snowflake_search_objects":
            _, raw_objects = client.search_objects(
                query=arguments.get("query"),
                database=arguments.get("database"),
                object_type=arguments.get("object_type"),
            )
            if not raw_objects:
                return [types.TextContent(type="text", text="No matching objects found.")]
            lines = [f"Found {len(raw_objects)} object(s):\n"]
            for o in raw_objects[:50]:
                pii = " [PII]" if o.get("pii_flag") else ""
                owner = f"  owner={o['owner']}" if o.get("owner") else ""
                ctx   = f"  — {o['business_context'][:80]}" if o.get("business_context") else ""
                lines.append(
                    f"  • {o['database_name']}.{o['schema_name']}.{o['object_name']}"
                    f"  [{o['object_type']}]{pii}{owner}{ctx}"
                )
            if len(raw_objects) > 50:
                lines.append(f"  … {len(raw_objects) - 50} more (refine with query/database/object_type)")
            lines.append("\nUse snowflake_get_schema to load the full doc for any object.")
            return [types.TextContent(type="text", text="\n".join(lines))]

        if name == "snowflake_get_columns":
            cols = client.get_columns(
                arguments["database"],
                arguments["schema"],
                arguments["object_name"],
            )
            if not cols:
                return [types.TextContent(
                    type="text",
                    text=f"No columns found for {arguments['database']}.{arguments['schema']}.{arguments['object_name']}. "
                         "Run snowflake_sync first, or use snowflake_get_schema for the full doc.",
                )]
            lines = [
                f"Columns for {arguments['database']}.{arguments['schema']}.{arguments['object_name']} "
                f"({len(cols)} columns):\n",
                f"{'#':<4} {'Column':<35} {'Type':<25} {'Null':<5} {'Default':<20} Description",
                "-" * 110,
            ]
            for c in cols:
                null = "YES" if c.get("is_nullable") else "NO "
                dflt = (c.get("column_default") or "")[:18]
                desc = (c.get("column_comment") or "")[:40]
                lines.append(
                    f"{str(c.get('ordinal_position','')):<4} "
                    f"{(c.get('column_name') or ''):<35} "
                    f"{(c.get('data_type') or ''):<25} "
                    f"{null:<5} {dflt:<20} {desc}"
                )
            return [types.TextContent(type="text", text="\n".join(lines))]

        if name == "snowflake_query":
            sql = arguments["sql"]
            result = client.run_query(sql)
            status = result.get("status")

            if status == "BLOCKED":
                return [types.TextContent(
                    type="text",
                    text=(
                        f"Query blocked — this statement is not permitted.\n\n"
                        f"Reason: {result.get('reason')}\n\n"
                        f"Submitted SQL:\n```sql\n{result.get('submitted_sql', sql)[:300]}\n```\n\n"
                        f"This block has been recorded in the audit log.\n"
                        f"To grant access, an admin can run: "
                        f"snowflake_set_query_permission(username, \"{result.get('keyword')}\", \"ALLOW\")"
                    ),
                )]

            if status == "ERROR":
                return [types.TextContent(
                    type="text",
                    text=f"Validation error: {result.get('error', 'Unknown error')}",
                )]

            # APPROVED — show validated SQL, recommended role, and masking notice
            approved_sql    = result.get("approved_sql", sql)
            run_as_role     = result.get("run_as_role")
            masking_notice  = result.get("masking_notice")
            pii_tables      = result.get("pii_tables", [])

            lines = ["Query approved.\n"]

            if run_as_role:
                lines.append(f"Run this in Snowflake as role **{run_as_role}**:\n")
            else:
                lines.append("Run this in Snowflake:\n")

            lines.append(f"```sql\n{approved_sql}\n```\n")

            if masking_notice:
                lines.append(f"⚠️ Masking notice: {masking_notice}\n")
                lines.append(
                    "Snowflake applies masking policies inside the database before data is returned. "
                    f"When connected as **{run_as_role}**, PII values in "
                    f"{', '.join(pii_tables)} will be masked automatically — "
                    "raw data never reaches Claude or Anthropic servers."
                )
            elif run_as_role:
                lines.append(
                    f"Connect as **{run_as_role}** to ensure any masking policies "
                    "on queried tables are applied before data is returned."
                )

            lines.append("\n\nThis query has been validated and logged to the audit trail.")
            return [types.TextContent(type="text", text="\n".join(lines))]

        if name == "snowflake_set_query_permission":
            result = client.set_query_permission(
                arguments["username"],
                arguments["keyword"],
                arguments["permission"],
                arguments.get("note"),
            )
            if isinstance(result, dict) and result.get("status") == "ERROR":
                return [types.TextContent(type="text", text=f"Error: {result.get('error')}")]
            status = result.get("status", "")
            u   = arguments["username"].upper()
            kw  = arguments["keyword"].upper()
            if status == "REMOVED":
                msg = f"Permission rule removed: {u} / {kw} — baseline behaviour restored."
            else:
                emoji = "✅" if status == "ALLOW" else "🚫"
                note  = f"\n  Note: {arguments['note']}" if arguments.get("note") else ""
                msg = (
                    f"{emoji} Permission set: {u} → {kw} = {status}{note}\n\n"
                    f"Effective immediately. Use snowflake_list_query_permissions to review all rules."
                )
            return [types.TextContent(type="text", text=msg)]

        if name == "snowflake_list_query_permissions":
            perms = client.list_query_permissions()
            if not perms:
                return [types.TextContent(
                    type="text",
                    text=(
                        "No per-user permission overrides set.\n\n"
                        "Baseline applies to everyone:\n"
                        "  ALLOWED: SELECT, SHOW, DESCRIBE, DESC, EXPLAIN, WITH\n"
                        "  BLOCKED: INSERT, UPDATE, DELETE, MERGE, CREATE, DROP, ALTER, "
                        "TRUNCATE, GRANT, REVOKE, COPY, PUT, GET, EXECUTE, CALL, SET, UNSET, ..."
                    ),
                )]
            lines = [f"Query permission overrides ({len(perms)} rule(s)):\n"]
            lines.append(f"{'User':<20} {'Keyword':<18} {'Permission':<8} {'Note'}")
            lines.append("-" * 75)
            for p in perms:
                user = p.get("username", "")
                kw   = p.get("keyword", "")
                perm = p.get("permission", "")
                note = (p.get("note") or "")[:35]
                icon = "✅" if perm == "ALLOW" else "🚫"
                lines.append(f"{user:<20} {kw:<18} {icon} {perm:<6} {note}")
            lines.append(
                "\nBaseline (no override): SELECT/SHOW/DESCRIBE/EXPLAIN=ALLOWED; "
                "INSERT/DROP/MERGE/CREATE/ALTER/GRANT/...=BLOCKED"
            )
            return [types.TextContent(type="text", text="\n".join(lines))]

        if name == "snowflake_annotate":
            result = client.update_enrichment(
                arguments["database"],
                arguments["schema"],
                arguments["object_name"],
                arguments["field"],
                arguments["value"],
            )
            if isinstance(result, dict) and result.get("status") == "ERROR":
                return [types.TextContent(type="text", text=f"Error: {result.get('error')}")]
            obj = f"{arguments['database']}.{arguments['schema']}.{arguments['object_name']}".upper()
            return [types.TextContent(
                type="text",
                text=f"Annotation saved for {obj}.\n"
                     f"  {arguments['field']} = {arguments['value']}\n"
                     "Change is immediately visible to all users.",
            )]

        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


async def run():
    client = SnowSyncMDClient.from_env()
    server = create_server(client)
    async with mcp.server.stdio.stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
