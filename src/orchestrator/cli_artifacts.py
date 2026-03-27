"""CLI entry point for the AI SDLC Orchestrator artifact management system.

Provides subcommands to list, compare, search, and garbage-collect versioned
artifacts stored under the orchestrator workspace.

Usage::

    orchestrate-artifacts list --run-id <run_id>
    orchestrate-artifacts compare --run-a <id_a> --run-b <id_b> --artifact <name>
    orchestrate-artifacts search [--type prd] [--agent pm] [--query text]
    orchestrate-artifacts gc [--max-age 90] [--max-runs 200] [--dry-run]

Entry point registered in pyproject.toml::

    [project.scripts]
    orchestrate-artifacts = "orchestrator.cli_artifacts:main"

Security
--------
All subprocess calls use ``shell=False`` (the default for subprocess.run).
The WORKSPACE_ROOT environment variable is validated to reject paths that
contain shell metacharacters before any filesystem operations are performed.

Dependencies
------------
This CLI delegates all I/O to :class:`orchestrator.artifact_manager.ArtifactManager`.
If the module is not available (feature not yet installed), the CLI prints a
clear installation hint and exits with code 1.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


# Shell metacharacters that must not appear in WORKSPACE_ROOT.
# Covers injection vectors including process substitution, globbing, quoting, etc.
_SHELL_METACHAR_RE = re.compile(r"[;|&$`<>()\[\]{}'\"\\!?*\n\r\t]")


# ---------------------------------------------------------------------------
# WORKSPACE_ROOT resolution and validation
# ---------------------------------------------------------------------------


def _validate_workspace_root(path: str) -> str:
    """Validate that *path* contains no shell metacharacters.

    Args:
        path: Candidate WORKSPACE_ROOT path string.

    Returns:
        The validated path (unchanged).

    Raises:
        SystemExit(1): If the path contains shell metacharacters.
    """
    if _SHELL_METACHAR_RE.search(path):
        print(
            f"Error: WORKSPACE_ROOT contains shell metacharacters: {path!r}\n"
            "WORKSPACE_ROOT must be a plain filesystem path with no shell special characters.",
            file=sys.stderr,
        )
        sys.exit(1)
    return path


def _resolve_artifacts_dir() -> Path:
    """Resolve the artifacts directory from WORKSPACE_ROOT (or a safe default).

    Priority:
      1. ``$WORKSPACE_ROOT`` environment variable (validated for metacharacters)
      2. ``<cwd>/workspace`` as the default workspace root

    The artifacts directory is ``<workspace_root>/artifacts``.

    Returns:
        Resolved Path to the artifacts directory.
    """
    workspace_root = os.environ.get("WORKSPACE_ROOT", "").strip()
    if not workspace_root:
        workspace_root = str(Path.cwd() / "workspace")

    _validate_workspace_root(workspace_root)

    return Path(workspace_root) / "artifacts"


# ---------------------------------------------------------------------------
# ArtifactManager factory
# ---------------------------------------------------------------------------


def _get_artifact_manager():
    """Import ArtifactManager and return an initialised instance.

    Resolves the artifacts directory from WORKSPACE_ROOT, validates it, and
    constructs an ArtifactManager pointing at that directory.

    Exits with code 1 if the module cannot be imported.
    """
    try:
        from orchestrator.artifact_manager import ArtifactManager  # noqa: PLC0415
    except ImportError as exc:
        print(
            f"Error: artifact management module is not available ({exc}).\n"
            "The ArtifactManager feature requires src/orchestrator/artifact_manager.py.\n"
            "Ensure the full feature set is installed.",
            file=sys.stderr,
        )
        sys.exit(1)

    artifacts_dir = _resolve_artifacts_dir()
    return ArtifactManager(artifacts_dir)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    """List all artifacts for a run with metadata."""
    am = _get_artifact_manager()
    artifacts = am.list_artifacts(args.run_id)
    if not artifacts:
        print(f"No artifacts found for run {args.run_id!r}")
        return 0
    print(f"Artifacts for run {args.run_id!r}:")
    for meta in artifacts:
        print(
            f"  {meta.name:30s}  "
            f"v{meta.current_version}  "
            f"{meta.size_bytes:>8,d} bytes  "
            f"{meta.updated_at}"
        )
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare the same artifact across two different runs."""
    am = _get_artifact_manager()
    try:
        diff = am.compare_artifacts(args.run_a, args.run_b, args.artifact)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Diff for artifact '{diff.name}'")
    print(f"  Run A (v{diff.version_a}): {diff.run_a}")
    print(f"  Run B (v{diff.version_b}): {diff.run_b}")
    if diff.added:
        print(f"  Added   ({len(diff.added)}): {', '.join(diff.added)}")
    if diff.removed:
        print(f"  Removed ({len(diff.removed)}): {', '.join(diff.removed)}")
    if diff.changed:
        print(f"  Changed ({len(diff.changed)}): {', '.join(diff.changed)}")
    if not (diff.added or diff.removed or diff.changed):
        print("  (no structural differences)")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Search artifacts by type, agent, or free-text query."""
    am = _get_artifact_manager()
    results = am.search_artifacts(
        query=args.query or "",
        artifact_type=args.type or None,
        agent=args.agent or None,
    )
    if not results:
        print("No matching artifacts found.")
        return 0
    print(f"Found {len(results)} matching artifact(s):")
    for meta in results:
        agent_str = meta.agent or "?"
        run_str = meta.run_id or "?"
        print(f"  {meta.name:30s}  run={run_str:36s}  agent={agent_str}")
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    """Garbage-collect old artifact versions according to retention policy."""
    am = _get_artifact_manager()
    result = am.apply_retention_policy(
        max_age_days=args.max_age,
        max_runs=args.max_runs,
        keep_failed=not args.delete_failed,
        dry_run=args.dry_run,
    )
    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}Retention policy applied:")
    print(f"  Versions deleted:  {result.deleted_versions}")
    print(f"  Versions retained: {result.retained_versions}")
    if result.deleted_paths:
        print(f"  Paths removed ({len(result.deleted_paths)}):")
        for p in result.deleted_paths:
            print(f"    {p}")
    if args.dry_run:
        print("  (no files were deleted — re-run without --dry-run to apply)")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """Show version history of a specific artifact within a run."""
    am = _get_artifact_manager()
    history = am.get_artifact_history(args.run_id, args.name)
    if not history:
        print(f"No version history found for artifact {args.name!r} in run {args.run_id!r}")
        return 0
    print(f"Version history for '{args.name}' in run '{args.run_id}':")
    for v in history:
        agent_str = v.agent or "?"
        print(
            f"  v{v.version:3d}  {v.created_at}  {v.size_bytes:>8,d} bytes  "
            f"agent={agent_str}  status={v.run_status}"
        )
    return 0


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``orchestrate-artifacts`` CLI command."""
    parser = argparse.ArgumentParser(
        prog="orchestrate-artifacts",
        description="Manage versioned artifacts from AI SDLC Orchestrator pipeline runs.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # ── list ────────────────────────────────────────────────────────────────
    p_list = sub.add_parser("list", help="List all artifacts for a run with metadata")
    p_list.add_argument(
        "--run-id",
        required=True,
        metavar="RUN_ID",
        help="Run ID to list artifacts for",
    )

    # ── compare ─────────────────────────────────────────────────────────────
    p_compare = sub.add_parser("compare", help="Compare the same artifact across two runs")
    p_compare.add_argument("--run-a", required=True, metavar="RUN_ID_A")
    p_compare.add_argument("--run-b", required=True, metavar="RUN_ID_B")
    p_compare.add_argument(
        "--artifact",
        required=True,
        metavar="ARTIFACT",
        help="Artifact name to compare (e.g. 'prd')",
    )

    # ── search ──────────────────────────────────────────────────────────────
    p_search = sub.add_parser("search", help="Search artifacts by type, agent, or text")
    p_search.add_argument(
        "--type",
        metavar="TYPE",
        help="Filter by artifact type / schema name (e.g. 'prd', 'architecture')",
    )
    p_search.add_argument("--agent", metavar="AGENT", help="Filter by agent name")
    p_search.add_argument(
        "--query", "-q",
        metavar="TEXT",
        default="",
        help="Free-text search query (case-insensitive substring match)",
    )

    # ── gc (garbage collection) ──────────────────────────────────────────────
    p_gc = sub.add_parser("gc", help="Garbage-collect old runs according to retention policy")
    p_gc.add_argument(
        "--max-age",
        type=int,
        default=90,
        metavar="DAYS",
        help="Delete versions older than N days (default: 90)",
    )
    p_gc.add_argument(
        "--max-runs",
        type=int,
        default=200,
        metavar="N",
        help="Keep at most N distinct run IDs (default: 200)",
    )
    p_gc.add_argument(
        "--delete-failed",
        action="store_true",
        help="Also delete versions from failed runs (by default kept)",
    )
    p_gc.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )

    # ── history ─────────────────────────────────────────────────────────────
    p_history = sub.add_parser("history", help="Show version history of an artifact within a run")
    p_history.add_argument("--run-id", required=True, metavar="RUN_ID")
    p_history.add_argument(
        "--name",
        required=True,
        metavar="ARTIFACT",
        help="Artifact name (e.g. 'prd')",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "list":    cmd_list,
        "compare": cmd_compare,
        "search":  cmd_search,
        "gc":      cmd_gc,
        "history": cmd_history,
    }

    rc = dispatch[args.command](args)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
