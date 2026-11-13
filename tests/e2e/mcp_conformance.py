"""Konformitaet des MCP-Endpunkts (NPT-14) mit dem offiziellen MCP-Python-SDK als Client.

Nur Test-Werkzeug, nie im Image: laeuft in einem Wegwerf-Container mit ``pip install mcp``
(Version siehe ``scripts/run-mcp-conformance.sh``), damit die SBOM unveraendert bleibt.

    python tests/e2e/mcp_conformance.py --base http://127.0.0.1:8480 --token <Projekt-Token>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def run(url: str, headers: dict[str, str] | None) -> dict[str, object]:
    async with (
        streamablehttp_client(url, headers=headers) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        tools = await session.list_tools()
        await session.send_ping()
        info = await session.call_tool("get_project", {})
        listing = await session.call_tool("list_systems", {"limit": 3})
        bad = await session.call_tool("get_system", {"system_id": "GIBT-ES-NICHT"})
        assert not info.isError, info
        assert not listing.isError, listing
        assert bad.isError, bad
        assert listing.structuredContent is not None
        return {
            "protocol": init.protocolVersion,
            "server": init.serverInfo.name,
            "tools": sorted(t.name for t in tools.tools),
            "systems_total": listing.structuredContent["total"],
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--token", required=True)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    results = {
        "pfad": asyncio.run(run(f"{base}/p/{args.token}/mcp", None)),
        "bearer": asyncio.run(run(f"{base}/mcp", {"Authorization": f"Bearer {args.token}"})),
    }
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
