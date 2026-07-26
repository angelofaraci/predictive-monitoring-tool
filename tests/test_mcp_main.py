"""Unit test for the `pmt-mcp` console-script entrypoint (spec: fase-5-mcp.md).

`main()` is a one-line entrypoint; it's tested with a mocked `build_server()`
rather than a real subprocess so it runs fast and without a trained model,
while still proving the shipped entrypoint calls the right functions.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from predictive_monitoring_tool.mcp_server.__main__ import main


def test_main_builds_server_and_runs_it_over_stdio():
    fake_server = MagicMock()

    with patch(
        "predictive_monitoring_tool.mcp_server.__main__.build_server",
        return_value=fake_server,
    ) as mock_build_server:
        main()

    mock_build_server.assert_called_once_with()
    fake_server.run.assert_called_once_with()
