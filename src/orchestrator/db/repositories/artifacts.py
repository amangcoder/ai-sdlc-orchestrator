"""ArtifactRepository — versioned artifact CRUD for the ``artifacts`` table."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.models import Artifact

log = logging.getLogger(__name__)


class ArtifactRepository:
    """Manages versioned artifact storage in the ``artifacts`` table.

    Replaces:
    - ``ArtifactManager.save_artifact()``
    - ``ArtifactManager.load_artifact()``
    - ``ArtifactManager.list_artifacts()``
    - ``ArtifactManager.get_artifact_history()``
    - ``ArtifactManager.search_artifacts()``
    - ``ArtifactManager.mark_run_status()``
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def save(
        self,
        run_id: str,
        name: str,
        data: dict[str, Any],
        agent: str | None = None,
        schema_name: str | None = None,
    ) -> int:
        """Save an artifact, auto-incrementing the version for this run+name.

        Returns the new version number.

        Uses ``SELECT MAX(version) + 1 FOR UPDATE`` inside the transaction to
        prevent concurrent version collisions.
        """
        dialect = self._session.bind.dialect.name if self._session.bind else "sqlite"

        # Determine next version — lock the max row so concurrent savers don't race.
        if dialect == "postgresql":
            result = await self._session.execute(
                select(func.max(Artifact.version))
                .where(Artifact.run_id == run_id, Artifact.name == name)
                .with_for_update()
            )
        else:
            result = await self._session.execute(
                select(func.max(Artifact.version))
                .where(Artifact.run_id == run_id, Artifact.name == name)
            )
        max_ver = result.scalar_one_or_none()
        version = (max_ver or 0) + 1

        content_str = json.dumps(data, default=str)
        artifact = Artifact(
            run_id=run_id,
            name=name,
            version=version,
            agent=agent,
            schema_name=schema_name,
            content=data,
            size_bytes=len(content_str.encode()),
            run_status="running",
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(artifact)
        return version

    async def mark_run_status(self, run_id: str, status: str) -> None:
        """Update ``run_status`` on all artifact rows for a run (retention policy)."""
        from sqlalchemy import update
        await self._session.execute(
            update(Artifact)
            .where(Artifact.run_id == run_id)
            .values(run_status=status)
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def load(
        self,
        run_id: str,
        name: str,
        version: int | None = None,
    ) -> dict[str, Any] | None:
        """Load an artifact's content dict.

        If ``version`` is None, returns the latest version.
        """
        if version is None:
            result = await self._session.execute(
                select(Artifact)
                .where(Artifact.run_id == run_id, Artifact.name == name)
                .order_by(desc(Artifact.version))
                .limit(1)
            )
        else:
            result = await self._session.execute(
                select(Artifact)
                .where(
                    Artifact.run_id == run_id,
                    Artifact.name == name,
                    Artifact.version == version,
                )
            )
        row = result.scalar_one_or_none()
        return row.content if row else None

    async def list_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Return metadata for all artifacts in a run (latest version per name)."""
        # Subquery: max version per run+name
        subq = (
            select(Artifact.name, func.max(Artifact.version).label("max_ver"))
            .where(Artifact.run_id == run_id)
            .group_by(Artifact.name)
            .subquery()
        )
        result = await self._session.execute(
            select(Artifact)
            .join(subq, (Artifact.name == subq.c.name) & (Artifact.version == subq.c.max_ver))
            .where(Artifact.run_id == run_id)
            .order_by(Artifact.name)
        )
        return [self._to_metadata(r) for r in result.scalars().all()]

    async def get_history(self, run_id: str, name: str) -> list[dict[str, Any]]:
        """Return all versions of an artifact for a run."""
        result = await self._session.execute(
            select(Artifact)
            .where(Artifact.run_id == run_id, Artifact.name == name)
            .order_by(Artifact.version)
        )
        return [self._to_version(r) for r in result.scalars().all()]

    async def search(
        self,
        run_id: str | None = None,
        query: str | None = None,
        artifact_type: str | None = None,
        agent: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search artifacts by name, agent, or content substring.

        On PostgreSQL, uses JSONB ``::text ILIKE`` for content search.
        On SQLite, casts JSON to text for LIKE search.
        """
        stmt = select(Artifact).order_by(desc(Artifact.created_at)).limit(limit)

        if run_id:
            stmt = stmt.where(Artifact.run_id == run_id)
        if artifact_type:
            stmt = stmt.where(Artifact.name == artifact_type)
        if agent:
            stmt = stmt.where(Artifact.agent == agent)
        if query:
            from sqlalchemy import cast, Text
            stmt = stmt.where(
                cast(Artifact.content, Text).ilike(f"%{query}%")
            )

        # Only latest version per run+name in search results
        subq = (
            select(Artifact.run_id, Artifact.name, func.max(Artifact.version).label("max_ver"))
            .group_by(Artifact.run_id, Artifact.name)
            .subquery()
        )
        stmt = stmt.join(
            subq,
            (Artifact.run_id == subq.c.run_id)
            & (Artifact.name == subq.c.name)
            & (Artifact.version == subq.c.max_ver),
        )

        result = await self._session.execute(stmt)
        return [self._to_metadata(r) for r in result.scalars().all()]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_metadata(row: Artifact) -> dict[str, Any]:
        # Artifacts are immutable per (run_id, name, version).  The "last write"
        # time is therefore the creation time of the latest version row, which is
        # what callers receive after the latest-version join in list_for_run /
        # search.  We surface it as both ``created_at`` (legacy) and
        # ``updated_at`` / ``artifact_name`` / ``schema`` (mobile API contract).
        ts = row.created_at.isoformat() if row.created_at else None
        return {
            # Legacy keys (dashboard, internal consumers)
            "name": row.name,
            "current_version": row.version,
            "run_id": row.run_id,
            "agent": row.agent,
            "schema_name": row.schema_name,
            "created_at": ts,
            "size_bytes": row.size_bytes,
            "run_status": row.run_status,
            # Mobile API / artifact-search contract keys (AC-003, REQ-006)
            "artifact_name": row.name,
            "schema": row.schema_name,
            "version": row.version,
            "updated_at": ts,
        }

    @staticmethod
    def _to_version(row: Artifact) -> dict[str, Any]:
        return {
            "version": row.version,
            "run_id": row.run_id,
            "agent": row.agent,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "size_bytes": row.size_bytes,
            "run_status": row.run_status,
        }
