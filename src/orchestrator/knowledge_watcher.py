"""File-change watcher that triggers incremental .knowledge rebuilds.

Runs as an async background task during implementation steps so that
parallel agents in later waves see code written by earlier waves.

Uses ``watchfiles`` (Rust-backed, fast) to detect changes and debounces
rebuilds so rapid-fire file writes don't cause a rebuild storm.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from orchestrator.knowledge import build_knowledge, synthesize_brief

logger = logging.getLogger(__name__)

# Directories that should NOT trigger a knowledge rebuild
_IGNORE_DIRS = frozenset({
    ".knowledge",
    ".git",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "workspace",
    ".claude",
})

# File extensions that are interesting for knowledge indexing
_SOURCE_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
    ".rb", ".php", ".swift", ".kt", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".vue", ".svelte", ".html", ".css", ".scss",
    ".sql", ".graphql", ".proto", ".yaml", ".yml", ".json",
    ".toml", ".md", ".rst", ".txt",
})


def _should_trigger(change_path: str, project_root: Path) -> bool:
    """Return True if this file change should trigger a knowledge rebuild."""
    try:
        rel = Path(change_path).relative_to(project_root)
    except ValueError:
        return False

    # Skip changes in ignored directories
    parts = rel.parts
    if any(part in _IGNORE_DIRS for part in parts):
        return False

    # Only rebuild for source-like files
    suffix = Path(change_path).suffix.lower()
    return suffix in _SOURCE_EXTENSIONS


class KnowledgeWatcher:
    """Watches the project root for file changes and triggers knowledge rebuilds.

    Usage::

        watcher = KnowledgeWatcher(project_root, config)
        await watcher.start()
        # ... implementation step runs ...
        await watcher.stop()
    """

    def __init__(
        self,
        project_root: Path,
        aicoder_path: str = "",
        build_timeout_seconds: int = 60,
        debounce_seconds: float = 5.0,
        brief_max_files: int = 30,
        brief_max_symbols: int = 15,
        inject_brief: bool = True,
        knowledge_context: Any | None = None,
        richness: str = "rich",
    ) -> None:
        self.project_root = project_root
        self.aicoder_path = aicoder_path
        self.build_timeout_seconds = build_timeout_seconds
        self.debounce_seconds = debounce_seconds
        self.brief_max_files = brief_max_files
        self.brief_max_symbols = brief_max_symbols
        self.inject_brief = inject_brief
        self.knowledge_context = knowledge_context
        self.richness = richness

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._rebuild_count = 0
        self._last_rebuild_time: float = 0.0

    @property
    def rebuild_count(self) -> int:
        return self._rebuild_count

    async def start(self) -> None:
        """Start watching for file changes in the background."""
        if self._task is not None:
            logger.warning("KnowledgeWatcher already running")
            return

        self._stop_event.clear()
        self._task = asyncio.create_task(self._watch_loop())
        logger.info(
            f"Knowledge watcher started (debounce={self.debounce_seconds}s, "
            f"project={self.project_root})"
        )

    async def stop(self) -> None:
        """Stop the watcher and wait for cleanup."""
        if self._task is None:
            return

        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info(
            f"Knowledge watcher stopped ({self._rebuild_count} rebuild(s) performed)"
        )

    async def _watch_loop(self) -> None:
        """Core watch loop: detect changes, debounce, rebuild."""
        try:
            from watchfiles import awatch, Change
        except ImportError:
            logger.warning(
                "watchfiles not installed — knowledge watcher disabled. "
                "Install with: pip install watchfiles"
            )
            return

        # Build the watch filter to exclude irrelevant directories
        watch_path = str(self.project_root)

        try:
            async for changes in awatch(
                watch_path,
                stop_event=self._stop_event,
                debounce=int(self.debounce_seconds * 1000),
                recursive=True,
                step=200,  # polling interval in ms
            ):
                if self._stop_event.is_set():
                    break

                # Filter to only source file changes
                relevant = [
                    (change_type, path)
                    for change_type, path in changes
                    if _should_trigger(path, self.project_root)
                ]

                if not relevant:
                    continue

                # Log what triggered the rebuild
                sample = relevant[:3]
                sample_desc = ", ".join(
                    f"{Path(p).relative_to(self.project_root)}"
                    for _, p in sample
                )
                if len(relevant) > 3:
                    sample_desc += f" (+{len(relevant) - 3} more)"

                logger.info(
                    f"Knowledge watcher: {len(relevant)} file(s) changed — "
                    f"rebuilding ({sample_desc})"
                )

                await self._rebuild()

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Knowledge watcher error: {e}")

    async def _rebuild(self) -> None:
        """Trigger a knowledge rebuild and update the brief."""
        start = time.monotonic()

        result = await build_knowledge(
            project_root=self.project_root,
            aicoder_path=self.aicoder_path,
            timeout_seconds=self.build_timeout_seconds,
            skip_if_fresh_minutes=0,  # Always rebuild on file change
            richness=self.richness,
        )

        elapsed_ms = (time.monotonic() - start) * 1000

        if result.success:
            self._rebuild_count += 1
            self._last_rebuild_time = time.monotonic()

            # Update the shared knowledge context so agents get fresh briefs
            if self.inject_brief and self.knowledge_context and result.knowledge_root:
                brief = synthesize_brief(
                    result.knowledge_root,
                    max_files=self.brief_max_files,
                    max_symbols=self.brief_max_symbols,
                )
                self.knowledge_context.brief = brief
                self.knowledge_context.file_count = result.file_count

            logger.info(
                f"Knowledge rebuilt: {result.file_count} files in {elapsed_ms:.0f}ms "
                f"(rebuild #{self._rebuild_count})"
            )
        else:
            logger.warning(f"Knowledge rebuild failed: {result.error}")
