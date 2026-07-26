"""Console-script entrypoint for `pmt-mcp` (spec: fase-5-mcp.md, Server & Entrypoint).

Runs the assembled server over stdio — `FastMCP.run()`'s default transport
(design decision: "Transport"). Any startup failure (e.g. a missing model
artifact — see `server.build_server()`) surfaces before the stdio loop
ever starts.
"""

from __future__ import annotations

from predictive_monitoring_tool.mcp_server.server import build_server


def main() -> None:
    """Build the server and serve it over stdio until the client disconnects."""
    build_server().run()


if __name__ == "__main__":
    main()
