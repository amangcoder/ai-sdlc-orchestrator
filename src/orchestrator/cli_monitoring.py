"""Alias module — delegates to orchestrator.monitoring.cli.

The canonical monitoring CLI implementation lives at
``src/orchestrator/monitoring/cli.py``.  This shim exists for any code
that imports directly from this path.
"""

from orchestrator.monitoring.cli import main  # noqa: F401

__all__ = ["main"]
