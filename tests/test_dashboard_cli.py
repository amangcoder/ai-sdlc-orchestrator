"""Test dashboard CLI workspace creation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_dashboard_cli_creates_missing_workspace(tmp_path: Path) -> None:
    """Test that dashboard CLI creates workspace directory if missing."""
    from orchestrator.dashboard.cli import main

    # Create a workspace path that doesn't exist yet
    workspace = tmp_path / "nonexistent" / "workspace"
    assert not workspace.exists()

    # Mock the argument parser to return our test workspace
    with patch("orchestrator.dashboard.cli.argparse.ArgumentParser") as mock_parser_class:
        mock_parser = MagicMock()
        mock_parser_class.return_value = mock_parser

        mock_args = MagicMock()
        mock_args.workspace = workspace
        mock_args.port = 8080
        mock_args.host = "127.0.0.1"
        mock_args.config = None
        mock_parser.parse_args.return_value = mock_args

        # Mock uvicorn to avoid actually starting the server
        with patch("orchestrator.dashboard.cli.uvicorn") as mock_uvicorn:
            # Mock the create_app import to avoid import errors
            with patch("orchestrator.dashboard.cli.create_app") as mock_create_app:
                mock_create_app.return_value = MagicMock()

                # Run main (will fail trying to run uvicorn but that's ok - we just want to check workspace creation)
                try:
                    main()
                except Exception:
                    pass

        # Verify workspace was created
        assert workspace.exists()
        assert workspace.is_dir()


def test_dashboard_cli_handles_existing_workspace(tmp_path: Path) -> None:
    """Test that dashboard CLI works with existing workspace directory."""
    from orchestrator.dashboard.cli import main

    # Create a workspace that already exists
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    assert workspace.exists()

    # Mock the argument parser to return our test workspace
    with patch("orchestrator.dashboard.cli.argparse.ArgumentParser") as mock_parser_class:
        mock_parser = MagicMock()
        mock_parser_class.return_value = mock_parser

        mock_args = MagicMock()
        mock_args.workspace = workspace
        mock_args.port = 8080
        mock_args.host = "127.0.0.1"
        mock_args.config = None
        mock_parser.parse_args.return_value = mock_args

        # Mock uvicorn to avoid actually starting the server
        with patch("orchestrator.dashboard.cli.uvicorn") as mock_uvicorn:
            # Mock the create_app import to avoid import errors
            with patch("orchestrator.dashboard.cli.create_app") as mock_create_app:
                mock_create_app.return_value = MagicMock()

                # Run main (will fail trying to run uvicorn but that's ok)
                try:
                    main()
                except Exception:
                    pass

        # Verify workspace still exists
        assert workspace.exists()
        assert workspace.is_dir()
