"""snowsyncmd-mcp — MCP server for the SnowSyncMD Native App."""

from .client import SnowSyncMDClient, SchemaObject, SyncStatus
from .server import create_server, run
import asyncio


def main():
    """Entry point for the `snowsyncmd-mcp` CLI command."""
    asyncio.run(run())


__all__ = ["SnowSyncMDClient", "SchemaObject", "SyncStatus", "create_server", "main"]
