#!/usr/bin/env bash
# Konformitaetstest des MCP-Endpunkts mit dem offiziellen MCP-Python-SDK (NPT-14, Auflage 10).
# Das SDK wird nur in einem Wegwerf-Container installiert (nicht im App-Image, SBOM unveraendert).
#   scripts/run-mcp-conformance.sh http://127.0.0.1:8480 <Projekt-Token>
set -euo pipefail
cd "$(dirname "$0")/.."
BASE="${1:?Basis-URL fehlt}"
TOKEN="${2:?Projekt-Token fehlt}"
MCP_SDK_VERSION="${MCP_SDK_VERSION:-1.30.0}"  # 1.x-API (streamablehttp_client), mit pydantic 2.14 getestet
docker run --rm --network host -v "$PWD/tests/e2e:/t:ro" -e TOKEN="$TOKEN" python:3.12-slim \
  sh -c "pip install -q --disable-pip-version-check 'mcp==$MCP_SDK_VERSION' >/dev/null && \
         python /t/mcp_conformance.py --base '$BASE' --token \"\$TOKEN\""
