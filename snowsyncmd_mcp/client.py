"""
client.py
=========
SnowSyncMDClient — thin wrapper around the Native App's stored procedures.

This is the ONLY class that talks to Snowflake.
The MCP server (server.py) uses this class exclusively.

Usage:
    client = SnowSyncMDClient.from_env()          # reads from environment
    client = SnowSyncMDClient(                     # explicit
        account="myaccount",
        user="myuser",
        password="mypassword",
        app_name="snowsyncmd",
    )

    doc  = client.get_schema("MY_DB", "SALES", "ORDERS")
    hits = client.search("customer")
    objs = client.list_objects(database="MY_DB")
    st   = client.get_status()
    res  = client.sync(database="MY_DB")  # optional write
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SchemaObject:
    database:    str
    schema:      str
    object_name: str
    object_type: str = ""
    size_bytes:  int = 0

    @property
    def full_name(self) -> str:
        return f"{self.database}.{self.schema}.{self.object_name}"


@dataclass
class SyncStatus:
    task_state:           str
    total_objects:        int
    md_files_present:     int
    dirty_count:          int
    last_scan_at:         Optional[str]
    databases:            list = field(default_factory=list)


class SnowSyncMDClient:
    """
    Wraps all calls to the SnowSyncMD Native App stored procedures.

    Responsibilities:
    - Manage the Snowflake connection lifecycle
    - Call api.* procedures and parse VARIANT responses
    - Read MD files directly from @core.md_stage
    - Provide typed return values (no raw SQL in server.py)
    """

    def __init__(
        self,
        account:   str,
        user:      str,
        password:  str,
        app_name:  str  = "snowsyncmd",
        role:      str  = "ACCOUNTADMIN",
        warehouse: str  = "",
    ):
        self._conn_params = dict(
            account=account, user=user, password=password,
            role=role, warehouse=warehouse,
        )
        self.app = app_name

    # ── constructor helpers ───────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "SnowSyncMDClient":
        """Build a client from standard environment variables."""
        return cls(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            password=os.environ["SNOWFLAKE_PASSWORD"],
            app_name=os.environ.get("SNOWSYNCMD_APP", "snowsyncmd"),
            role=os.environ.get("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", ""),
        )

    def _connect(self):
        import snowflake.connector
        return snowflake.connector.connect(**self._conn_params)

    # ── internal helpers ──────────────────────────────────────────────────────

    def _call(self, proc: str, *args) -> dict | list:
        """Call a stored procedure and return the parsed JSON response."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            escaped = ", ".join(
                "NULL" if a is None else f"'{str(a).replace(chr(39), chr(39)*2)}'"
                for a in args
            )
            cur.execute(f"CALL {self.app}.api.{proc}({escaped})")
            row = cur.fetchone()
            if not row:
                return {}
            val = row[0]
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except Exception:
                    return {"raw": val}
            return val or {}
        finally:
            conn.close()

    def _call_varchar(self, proc: str, *args) -> str | None:
        """Call a stored procedure that returns VARCHAR (not VARIANT)."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            escaped = ", ".join(
                "NULL" if a is None else f"'{str(a).replace(chr(39), chr(39)*2)}'"
                for a in args
            )
            cur.execute(f"CALL {self.app}.api.{proc}({escaped})")
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _list_stage(self, database: str | None = None) -> list[SchemaObject]:
        """LIST the stage and return typed SchemaObject entries."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            prefix = f"@{self.app}.core.md_stage/"
            if database:
                prefix += f"{database.upper()}/"
            cur.execute(f"LIST {prefix}")
            rows = cur.fetchall()
            objects = []
            for r in rows:
                parts = r[0].split("/")   # md_stage/DB/SCHEMA/OBJECT.md
                if len(parts) >= 4:
                    objects.append(SchemaObject(
                        database=parts[1],
                        schema=parts[2],
                        object_name=parts[3].replace(".md", ""),
                        size_bytes=int(r[1]) if r[1] else 0,
                    ))
            return objects
        finally:
            conn.close()

    # ── public API ────────────────────────────────────────────────────────────

    def get_schema(self, database: str, schema: str, object_name: str) -> str | None:
        """
        Return the full Markdown documentation for one Snowflake object,
        or None if not found / error.
        """
        result = self._call_varchar(
            "get_schema_doc",
            database.upper(), schema.upper(), object_name.upper(),
        )
        if result and not result.startswith("Error reading") and not result.startswith("No documentation"):
            return result
        return None

    def search(self, query: str, database: str | None = None) -> list[SchemaObject]:
        """
        Keyword search across all tracked objects.
        Matches on object name, schema name, or database name.
        """
        q = query.lower()
        objects = self._list_stage(database)
        return [
            obj for obj in objects
            if q in obj.object_name.lower()
            or q in obj.schema.lower()
            or q in obj.database.lower()
        ]

    def list_objects(
        self,
        database: str | None = None,
        object_type: str | None = None,
    ) -> list[SchemaObject]:
        """
        List all tracked objects, optionally filtered by database or type.
        Type filter requires the list_md_files API (has type info).
        """
        result = self._call("list_md_files", database or "")
        files  = result.get("files", []) if isinstance(result, dict) else []

        objects = []
        for f in files:
            obj = SchemaObject(
                database=f.get("database_name", ""),
                schema=f.get("schema_name", ""),
                object_name=f.get("object_name", ""),
                object_type=f.get("object_type", ""),
                size_bytes=f.get("file_size", 0),
            )
            objects.append(obj)

        if object_type:
            objects = [o for o in objects if o.object_type.upper() == object_type.upper()]

        return objects

    def get_status(self) -> SyncStatus:
        """Return current sync health as a typed SyncStatus."""
        raw = self._call("get_status")
        return SyncStatus(
            task_state=raw.get("task_state", "UNKNOWN"),
            total_objects=raw.get("total_objects_tracked", 0),
            md_files_present=raw.get("md_files_present", 0),
            dirty_count=raw.get("dirty_count", 0),
            last_scan_at=raw.get("last_scan_at"),
            databases=raw.get("databases", []),
        )

    def sync(self, database: str | None = None) -> dict:
        """
        Trigger an immediate sync.
        Returns the raw sync result dict from the Native App.
        """
        if database:
            return self._call("sync_database", database.upper())
        return self._call("sync_now")

    def search_objects(
        self,
        query: str | None = None,
        database: str | None = None,
        object_type: str | None = None,
    ) -> tuple[list[SchemaObject], list[dict]]:
        """
        Structured search against core.snowflake_objects_ref.
        Returns (typed_list, raw_dicts) — raw_dicts include enrichment fields
        (owner, business_context, pii_flag) that SchemaObject doesn't carry.
        """
        result = self._call(
            "search_objects",
            query or None,
            database.upper() if database else None,
            object_type.upper() if object_type else None,
        )
        objects = result.get("objects", []) if isinstance(result, dict) else []
        return [
            SchemaObject(
                database=o.get("database_name", ""),
                schema=o.get("schema_name", ""),
                object_name=o.get("object_name", ""),
                object_type=o.get("object_type", ""),
            )
            for o in objects
        ], objects  # return both typed list and raw dicts for enrichment fields

    def get_columns(self, database: str, schema: str, object_name: str) -> list[dict]:
        """
        Return structured column list for a table or view from core.snowflake_columns_ref.
        Each dict has: column_name, ordinal_position, data_type, is_nullable,
        column_default, column_comment.
        """
        result = self._call(
            "list_columns",
            database.upper(), schema.upper(), object_name.upper(),
        )
        return result.get("columns", []) if isinstance(result, dict) else []

    def _log_query(self, sql: str, first_kw: str, status: str,
                   error_msg: str | None = None) -> None:
        try:
            self._call("log_safe_query", sql[:2000], first_kw, status,
                       None, None, error_msg)
        except Exception:
            pass  # audit failure must never block the caller

    def run_query(self, sql: str) -> dict:
        """
        Validate a SQL statement via the Native App and return APPROVED or BLOCKED.

        The MCP server never executes user SQL — it only checks the statement
        against core.query_permissions (per-user overrides) and baseline rules
        inside api.classify_query (EXECUTE AS OWNER, reads text only).

        If APPROVED: returns the validated SQL so Claude can show it to the user
        for manual execution in Snowflake.
        If BLOCKED:  returns the reason; Claude explains why and suggests alternatives.

        All checks are audited in core.query_audit_log via api.log_safe_query.
        """
        if not sql or not sql.strip():
            return {"status": "ERROR", "error": "Empty query."}

        username = self._conn_params.get("user", "")
        verdict = self._call("classify_query", sql, username)
        if not isinstance(verdict, dict):
            verdict = {}

        first_kw = verdict.get("keyword", "UNKNOWN")

        if not verdict.get("allowed", False):
            reason = verdict.get("reason", "Statement not allowed.")
            self._log_query(sql, first_kw, "BLOCKED", error_msg=reason)
            return {
                "status":       "BLOCKED",
                "keyword":      first_kw,
                "reason":       reason,
                "submitted_sql": sql[:300],
            }

        self._log_query(sql, first_kw, "APPROVED")
        result = {
            "status":       "APPROVED",
            "keyword":      first_kw,
            "approved_sql": sql.strip(),
        }
        for field in ("run_as_role", "masking_notice", "pii_tables"):
            if verdict.get(field):
                result[field] = verdict[field]
        return result

    def set_query_permission(
        self,
        username: str,
        keyword: str,
        permission: str,
        note: str | None = None,
    ) -> dict:
        """
        Set a per-user SQL keyword permission override.
        username:   Snowflake username or '*' for all users.
        keyword:    SQL first keyword (SELECT, INSERT, SHOW, etc.)
        permission: ALLOW | DENY | REMOVE
                    ALLOW  — grant access to a normally-blocked keyword
                    DENY   — block a normally-allowed keyword
                    REMOVE — delete the rule (restore baseline behaviour)
        """
        return self._call("set_query_permission", username, keyword, permission, note)

    def list_query_permissions(self) -> list[dict]:
        """Return all per-user query permission rules from core.query_permissions."""
        result = self._call("list_query_permissions")
        return result.get("permissions", []) if isinstance(result, dict) else []

    def update_enrichment(
        self,
        database: str,
        schema: str,
        object_name: str,
        field: str,
        value: str,
    ) -> dict:
        """
        Write a team annotation to core.snowflake_objects_ref.
        field must be one of: owner, business_context, sla_description, pii_flag, tags.
        Changes are immediately visible to all users — no sync required.
        """
        return self._call(
            "update_enrichment",
            database.upper(), schema.upper(), object_name.upper(),
            field.lower(), value,
        )
