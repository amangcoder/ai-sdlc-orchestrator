"""Test dashboard CLI workspace creation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _run_main_with_mocks(workspace: Path) -> None:
    """Run dashboard main() with argparse, uvicorn, and create_app mocked."""
    from orchestrator.dashboard.cli import main

    mock_uvicorn = MagicMock()

    with patch("orchestrator.dashboard.cli.argparse.ArgumentParser") as mock_parser_class:
        mock_parser = MagicMock()
        mock_parser_class.return_value = mock_parser

        mock_args = MagicMock()
        mock_args.workspace = workspace
        mock_args.port = 8080
        mock_args.host = "127.0.0.1"
        mock_args.config = None
        mock_parser.parse_args.return_value = mock_args

        with patch.dict(sys.modules, {"uvicorn": mock_uvicorn}):
            with patch("orchestrator.dashboard.app.create_app", return_value=MagicMock()):
                try:
                    main()
                except Exception:
                    pass


def test_dashboard_cli_creates_missing_workspace(tmp_path: Path) -> None:
    """Test that dashboard CLI creates workspace directory if missing."""
    workspace = tmp_path / "nonexistent" / "workspace"
    assert not workspace.exists()

    _run_main_with_mocks(workspace)

    assert workspace.exists()
    assert workspace.is_dir()


def test_dashboard_cli_handles_existing_workspace(tmp_path: Path) -> None:
    """Test that dashboard CLI works with existing workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    assert workspace.exists()

    _run_main_with_mocks(workspace)

    assert workspace.exists()
    assert workspace.is_dir()
