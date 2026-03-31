#!/usr/bin/env python3
"""One-shot idempotent migration: filesystem runs → centralized DB.

Usage
-----
    # Dry run (shows what would be migrated, no writes):
    python scripts/migrate_files_to_db.py --workspace workspace --db-url "postgresql+asyncpg://..." --dry-run

    # Real migration:
    python scripts/migrate_files_to_db.py --workspace workspace --db-url "postgresql+asyncpg://user:pass@host/db"

    # SQLite (dev/test):
    python scripts/migrate_files_to_db.py --workspace workspace --db-url "sqlite+aiosqlite:///local.db"

What it migrates
----------------
For each run directory found in <workspace>/runs/<run_id>/:
  1. state.json      → runs table
  2. artifacts/*.json → artifacts table (one row per file, version = 1)
  3. logs/run-*.jsonl → run_events table
  4. timeline.json   → timeline_entries table

Also scans ~/.orchestrator/runs/*.json (the global registry) for any runs
not covered by the workspace scan.

Idempotency
-----------
Skips runs that already have a row in the ``runs`` table (checked by run_id).
Safe to run multiple times.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("migrate")

# Artifact names that should be imported (matches ARTIFACT_MODELS allowlist)
_ARTIFACT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
_SKIP_PREFIXES = (".", "_")


async def migrate(
    workspace: Path,
    db_url: str,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Run the full migration."""
    from orchestrator.models import DatabaseConfig
    from orchestrator.db import init_db, get_session
    from orchestrator.db.repositories.runs import RunRepository
    from orchestrator.db.repositories.artifacts import ArtifactRepository
    from orchestrator.db.repositories.events import EventRepository
    from orchestrator.db.repositories.timeline import TimelineRepository

    config = DatabaseConfig(url=db_url, migrate_on_start=True)
    if not dry_run:
        await init_db(config)
        log.info("DB initialized and migrations applied.")
    else:
        log.info("DRY RUN — no writes will be performed.")

    runs_root = workspace / "runs"
    registry_dir = Path.home() / ".orchestrator" / "runs"

    # Collect all run directories
    run_dirs: list[Path] = []
    if runs_root.exists():
        run_dirs = [d for d in sorted(runs_root.iterdir()) if d.is_dir()]
    log.info("Found %d run directories in %s", len(run_dirs), runs_root)

    stats = {"migrated": 0, "skipped": 0, "failed": 0, "artifacts": 0, "events": 0, "timeline": 0}

    for run_dir in run_dirs:
        run_id = run_dir.name
        state_file = run_dir / "state.json"
        if not state_file.exists():
            log.debug("Skipping %s — no state.json", run_id)
            stats["skipped"] += 1
            continue

        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to read state.json for %s: %s", run_id, exc)
            stats["failed"] += 1
            continue

        actual_run_id = state_data.get("run_id", run_id)

        if not dry_run:
            async with get_session() as session:
                run_repo = RunRepository(session)
                if await run_repo.exists(actual_run_id):
                    if verbose:
                        log.info("Run %s already in DB — skipping", actual_run_id)
                    stats["skipped"] += 1
                    continue

                # Insert run
                try:
                    from orchestrator.models import RunState
                    run_state = RunState.model_validate(state_data)
                    await run_repo.upsert(run_state, project_name=workspace.parent.name)
                    log.info("Migrated run %s", actual_run_id)
                    stats["migrated"] += 1
                except Exception as exc:
                    log.warning("Failed to migrate run %s: %s", actual_run_id, exc)
                    stats["failed"] += 1
                    continue

            # Migrate artifacts
            artifacts_dir = run_dir / "artifacts"
            if artifacts_dir.exists():
                async with get_session() as session:
                    art_repo = ArtifactRepository(session)
                    for art_file in sorted(artifacts_dir.glob("*.json")):
                        name = art_file.stem
                        if name.startswith(_SKIP_PREFIXES):
                            continue
                        if not _ARTIFACT_NAME_RE.match(name):
                            continue
                        try:
                            data = json.loads(art_file.read_text(encoding="utf-8"))
                            await art_repo.save(
                                run_id=actual_run_id,
                                name=name,
                                data=data,
                            )
                            stats["artifacts"] += 1
                            if verbose:
                                log.info("  Artifact: %s/%s", actual_run_id, name)
                        except Exception as exc:
                            log.debug("  Artifact %s/%s failed: %s", actual_run_id, name, exc)

            # Migrate events from JSONL
            logs_dir = run_dir / "logs"
            if logs_dir.exists():
                for jsonl_file in logs_dir.glob("*.jsonl"):
                    try:
                        lines = jsonl_file.read_text(encoding="utf-8").strip().splitlines()
                        batch = []
                        seq = 0
                        for line in lines:
                            if not line.strip():
                                continue
                            try:
                                record = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            seq += 1
                            ts_raw = record.get("ts")
                            try:
                                ts = datetime.fromisoformat(ts_raw) if ts_raw else datetime.now(timezone.utc)
                            except ValueError:
                                ts = datetime.now(timezone.utc)
                            batch.append((
                                seq,
                                record.get("event", "unknown"),
                                "INFO",
                                {k: v for k, v in record.items() if k not in ("ts", "run_id", "event")},
                                ts,
                            ))

                        if batch:
                            from orchestrator.db.session import get_session as _gs
                            event_repo = EventRepository(_gs, actual_run_id)
                            # Override seq counter
                            event_repo._seq = seq
                            await event_repo.append_batch(batch)
                            stats["events"] += len(batch)
                            if verbose:
                                log.info("  Events: %d from %s", len(batch), jsonl_file.name)
                    except Exception as exc:
                        log.debug("  Events from %s failed: %s", jsonl_file.name, exc)

            # Migrate timeline
            timeline_file = run_dir / "timeline.json"
            if timeline_file.exists():
                try:
                    timeline_data = json.loads(timeline_file.read_text(encoding="utf-8"))
                    entries = timeline_data.get("entries", [])
                    from orchestrator.db.session import get_session as _gs
                    tl_repo = TimelineRepository(_gs)
                    for entry in entries:
                        await tl_repo.upsert_entry(actual_run_id, entry)
                    stats["timeline"] += len(entries)
                    if verbose:
                        log.info("  Timeline: %d entries", len(entries))
                except Exception as exc:
                    log.debug("  Timeline for %s failed: %s", actual_run_id, exc)

        else:
            # Dry run — just count
            log.info("[DRY RUN] Would migrate run %s", actual_run_id)
            stats["migrated"] += 1
            artifacts_dir = run_dir / "artifacts"
            stats["artifacts"] += len([
                f for f in (artifacts_dir.glob("*.json") if artifacts_dir.exists() else [])
                if not f.stem.startswith(_SKIP_PREFIXES) and _ARTIFACT_NAME_RE.match(f.stem)
            ])

    log.info(
        "\nMigration complete:\n"
        "  Migrated runs:  %d\n"
        "  Skipped runs:   %d\n"
        "  Failed runs:    %d\n"
        "  Artifacts:      %d\n"
        "  Events:         %d\n"
        "  Timeline rows:  %d",
        stats["migrated"], stats["skipped"], stats["failed"],
        stats["artifacts"], stats["events"], stats["timeline"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate filesystem runs to centralized DB")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
        help="Workspace directory (default: ./workspace)",
    )
    parser.add_argument(
        "--db-url",
        required=True,
        help="SQLAlchemy async DB URL (e.g. postgresql+asyncpg://... or sqlite+aiosqlite:///...)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without writing to DB",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show per-artifact and per-event details",
    )
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    if not workspace.exists():
        log.error("Workspace directory does not exist: %s", workspace)
        sys.exit(1)

    # Add src/ to Python path so orchestrator package is importable
    src_dir = Path(__file__).resolve().parents[1] / "src"
    if src_dir.exists() and str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    asyncio.run(migrate(workspace, args.db_url, dry_run=args.dry_run, verbose=args.verbose))


if __name__ == "__main__":
    main()
