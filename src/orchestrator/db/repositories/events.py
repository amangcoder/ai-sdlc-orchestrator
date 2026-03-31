"""EventRepository — append-only event log for the ``run_events`` table.

The ``seq`` counter is maintained in-memory per ``RunLogger`` instance
(there is exactly one logger per run, so no cross-process seq collision).
This repository provides both async append and a write-behind queue for
use from synchronous ``RunLogger.log_event()`` callers.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.models import RunEvent

log = logging.getLogger(__name__)


class EventRepository:
    """Manages the append-only ``run_events`` event log.

    Write-behind queue
    ------------------
    The ``RunLogger`` is called synchronously from inside the engine.
    To avoid blocking the event loop on every log call, ``push_sync()``
    pushes events onto a thread-safe deque.  Call ``start_drain_task()``
    once from an async context to start the background drain loop that
    batches inserts every 0.5 seconds.
    """

    def __init__(self, session_factory, run_id: str) -> None:
        """
        Args:
            session_factory: Callable returning an ``AsyncSession`` context manager
                             (i.e. ``get_session`` from ``orchestrator.db.session``).
            run_id: The run this repository instance belongs to.
        """
        self._session_factory = session_factory
        self._run_id = run_id
        self._seq = 0
        self._seq_lock = threading.Lock()
        # Write-behind queue: deque of (seq, event_type, level, data, ts) tuples
        self._queue: deque[tuple[int, str, str, dict[str, Any], datetime]] = deque()
        self._queue_lock = threading.Lock()
        self._drain_task: asyncio.Task | None = None
        self._stopped = False

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    # ------------------------------------------------------------------
    # Synchronous push (called from RunLogger.log_event — hot path)
    # ------------------------------------------------------------------

    def push_sync(
        self,
        event_type: str,
        data: dict[str, Any],
        level: str = "INFO",
    ) -> None:
        """Push an event onto the write-behind queue (non-blocking, thread-safe)."""
        seq = self._next_seq()
        ts = datetime.now(timezone.utc)
        with self._queue_lock:
            self._queue.append((seq, event_type, level, data, ts))

    # ------------------------------------------------------------------
    # Async drain (background task)
    # ------------------------------------------------------------------

    def start_drain_task(self) -> None:
        """Start the background drain coroutine as an asyncio Task."""
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain_loop())

    async def stop(self) -> None:
        """Flush remaining events and stop the drain task."""
        self._stopped = True
        await self._flush_queue()
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass

    async def _drain_loop(self) -> None:
        while not self._stopped:
            await asyncio.sleep(0.5)
            await self._flush_queue()

    async def _flush_queue(self) -> None:
        with self._queue_lock:
            if not self._queue:
                return
            batch = list(self._queue)
            self._queue.clear()

        try:
            await self.append_batch(batch)
        except Exception as exc:
            log.warning("EventRepository flush failed, re-queuing %d events: %s", len(batch), exc)
            with self._queue_lock:
                # Re-add to the front for retry
                for item in reversed(batch):
                    self._queue.appendleft(item)

    # ------------------------------------------------------------------
    # Async writes
    # ------------------------------------------------------------------

    async def append(
        self,
        event_type: str,
        data: dict[str, Any],
        level: str = "INFO",
    ) -> None:
        """Append a single event directly (async callers)."""
        seq = self._next_seq()
        ts = datetime.now(timezone.utc)
        await self.append_batch([(seq, event_type, level, data, ts)])

    async def append_batch(
        self,
        batch: list[tuple[int, str, str, dict[str, Any], datetime]],
    ) -> None:
        """Bulk-insert a list of (seq, event_type, level, data, ts) tuples."""
        if not batch:
            return
        async with self._session_factory() as session:
            rows = [
                RunEvent(
                    run_id=self._run_id,
                    seq=seq,
                    event_type=event_type,
                    level=level,
                    data=data,
                    ts=ts,
                )
                for seq, event_type, level, data, ts in batch
            ]
            session.add_all(rows)
            await session.commit()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_slice(
        self,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return events for this run with pagination (seq-based)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(RunEvent)
                .where(RunEvent.run_id == self._run_id)
                .order_by(RunEvent.seq)
                .offset(offset)
                .limit(limit)
            )
            return [self._to_dict(r) for r in result.scalars().all()]

    async def tail(self, after_seq: int, limit: int = 200) -> list[dict[str, Any]]:
        """Return events with seq > after_seq (for live tailing)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(RunEvent)
                .where(RunEvent.run_id == self._run_id, RunEvent.seq > after_seq)
                .order_by(RunEvent.seq)
                .limit(limit)
            )
            return [self._to_dict(r) for r in result.scalars().all()]

    async def count(self) -> int:
        from sqlalchemy import func
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count()).where(RunEvent.run_id == self._run_id)
            )
            return result.scalar_one()

    @staticmethod
    def _to_dict(row: RunEvent) -> dict[str, Any]:
        return {
            "id": row.id,
            "run_id": row.run_id,
            "seq": row.seq,
            "event_type": row.event_type,
            "level": row.level,
            "data": row.data,
            "ts": row.ts.isoformat() if row.ts else None,
        }
