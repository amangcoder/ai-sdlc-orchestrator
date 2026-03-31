"""Integration tests for the centralized DB persistence layer.

Uses SQLite via aiosqlite — no external services required.
Run with: pytest tests/test_db_persistence.py -v
"""

from __future__ import annotations

import asyncio
import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def db_config(tmp_path):
    """Return a DatabaseConfig pointing at a temporary SQLite file."""
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("aiosqlite")

    from orchestrator.models import DatabaseConfig
    db_file = tmp_path / "test.db"
    return DatabaseConfig(
        url=f"sqlite+aiosqlite:///{db_file}",
        migrate_on_start=True,
        event_log_sidecar=False,
        run_state_sidecar=False,
    )


@pytest_asyncio.fixture
async def initialized_db(db_config):
    """Initialize the DB and yield the config. Reset engine singleton between tests."""
    import orchestrator.db as db_module
    # Reset singleton so each test gets a fresh engine
    db_module._engine = None
    from orchestrator.db.session import configure_session_factory
    # Reset session factory
    import orchestrator.db.session as session_module
    session_module._session_factory = None

    await db_module.init_db(db_config)
    yield db_config

    # Cleanup
    engine = db_module.get_engine()
    if engine:
        await engine.dispose()
    db_module._engine = None
    session_module._session_factory = None


@pytest_asyncio.fixture
async def session(initialized_db):
    """Yield a single AsyncSession for use in tests."""
    from orchestrator.db.session import get_session
    async with get_session() as s:
        yield s


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def make_run_state(run_id: str = "abc123def456", feature_request: str = "Build test app"):
    from orchestrator.models import RunState
    return RunState(
        run_id=run_id,
        feature_request=feature_request,
        workspace_dir=f"/tmp/workspace/runs/{run_id}",
    )


# ---------------------------------------------------------------------------
# RunRepository tests
# ---------------------------------------------------------------------------

class TestRunRepository:

    @pytest.mark.asyncio
    async def test_upsert_and_get(self, session):
        from orchestrator.db.repositories.runs import RunRepository
        repo = RunRepository(session)
        state = make_run_state()

        await repo.upsert(state, project_name="myproject")
        result = await repo.get(state.run_id)

        assert result is not None
        assert result["run_id"] == state.run_id
        assert result["feature_request"] == state.feature_request
        assert result["project_name"] == "myproject"
        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_upsert_idempotent(self, session):
        from orchestrator.db.repositories.runs import RunRepository
        repo = RunRepository(session)
        state = make_run_state(run_id="idem000000ab")

        await repo.upsert(state)
        await repo.upsert(state)  # second upsert should not raise

        result = await repo.get(state.run_id)
        assert result is not None

    @pytest.mark.asyncio
    async def test_list_returns_all(self, session):
        from orchestrator.db.repositories.runs import RunRepository
        repo = RunRepository(session)

        for i in range(3):
            state = make_run_state(run_id=f"listtest{i:04d}", feature_request=f"feature {i}")
            await repo.upsert(state)

        runs = await repo.list(limit=200)
        run_ids = {r["run_id"] for r in runs}
        assert "listtest0000" in run_ids
        assert "listtest0002" in run_ids

    @pytest.mark.asyncio
    async def test_update_status(self, session):
        from orchestrator.db.repositories.runs import RunRepository
        repo = RunRepository(session)
        state = make_run_state(run_id="statustest001")
        await repo.upsert(state)

        await repo.update_status("statustest001", "completed")
        result = await repo.get("statustest001")
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_exists(self, session):
        from orchestrator.db.repositories.runs import RunRepository
        repo = RunRepository(session)

        assert not await repo.exists("doesnotexist_")
        state = make_run_state(run_id="exists000001")
        await repo.upsert(state)
        assert await repo.exists("exists000001")

    @pytest.mark.asyncio
    async def test_state_json_roundtrip(self, session):
        """Full RunState serialized to state_json and back."""
        from orchestrator.db.repositories.runs import RunRepository
        from orchestrator.models import RunState, PhaseState, PhaseStatus
        repo = RunRepository(session)

        state = make_run_state(run_id="jsonroundtrip")
        state.phases["pm"] = PhaseState(status=PhaseStatus.COMPLETED, cost_usd=0.05)
        state.total_cost_usd = 0.05

        await repo.upsert(state)
        result = await repo.get_state_json("jsonroundtrip")

        assert result is not None
        assert result["total_cost_usd"] == pytest.approx(0.05)
        assert "pm" in result["phases"]


# ---------------------------------------------------------------------------
# ArtifactRepository tests
# ---------------------------------------------------------------------------

class TestArtifactRepository:

    @pytest.mark.asyncio
    async def test_save_and_load(self, session):
        from orchestrator.db.repositories.artifacts import ArtifactRepository
        repo = ArtifactRepository(session)

        data = {"title": "Test PRD", "features": ["auth", "api"]}
        v = await repo.save("run001", "prd", data, agent="pm")
        assert v == 1

        loaded = await repo.load("run001", "prd")
        assert loaded == data

    @pytest.mark.asyncio
    async def test_versioning(self, session):
        from orchestrator.db.repositories.artifacts import ArtifactRepository
        repo = ArtifactRepository(session)

        v1 = await repo.save("run002", "arch", {"v": 1})
        v2 = await repo.save("run002", "arch", {"v": 2})
        assert v1 == 1
        assert v2 == 2

        # Latest version
        latest = await repo.load("run002", "arch")
        assert latest == {"v": 2}

        # Specific version
        old = await repo.load("run002", "arch", version=1)
        assert old == {"v": 1}

    @pytest.mark.asyncio
    async def test_list_for_run(self, session):
        from orchestrator.db.repositories.artifacts import ArtifactRepository
        repo = ArtifactRepository(session)

        await repo.save("run003", "prd", {"x": 1})
        await repo.save("run003", "arch", {"y": 2})

        listing = await repo.list_for_run("run003")
        names = {a["name"] for a in listing}
        assert names == {"prd", "arch"}

    @pytest.mark.asyncio
    async def test_get_history(self, session):
        from orchestrator.db.repositories.artifacts import ArtifactRepository
        repo = ArtifactRepository(session)

        await repo.save("run004", "prd", {"v": 1})
        await repo.save("run004", "prd", {"v": 2})
        await repo.save("run004", "prd", {"v": 3})

        history = await repo.get_history("run004", "prd")
        assert len(history) == 3
        assert [h["version"] for h in history] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_search_by_agent(self, session):
        from orchestrator.db.repositories.artifacts import ArtifactRepository
        repo = ArtifactRepository(session)

        await repo.save("run005", "prd", {"x": 1}, agent="pm")
        await repo.save("run005", "arch", {"y": 2}, agent="architect")

        results = await repo.search(run_id="run005", agent="pm")
        assert len(results) == 1
        assert results[0]["name"] == "prd"

    @pytest.mark.asyncio
    async def test_search_by_content(self, session):
        from orchestrator.db.repositories.artifacts import ArtifactRepository
        repo = ArtifactRepository(session)

        await repo.save("run006", "prd", {"title": "authentication system"})
        await repo.save("run006", "arch", {"description": "payment gateway"})

        results = await repo.search(run_id="run006", query="authentication")
        assert len(results) == 1
        assert results[0]["name"] == "prd"

    @pytest.mark.asyncio
    async def test_mark_run_status(self, session):
        from orchestrator.db.repositories.artifacts import ArtifactRepository
        repo = ArtifactRepository(session)

        await repo.save("run007", "prd", {"x": 1})
        await repo.mark_run_status("run007", "completed")

        listing = await repo.list_for_run("run007")
        assert all(a["run_status"] == "completed" for a in listing)

    @pytest.mark.asyncio
    async def test_load_missing_returns_none(self, session):
        from orchestrator.db.repositories.artifacts import ArtifactRepository
        repo = ArtifactRepository(session)
        result = await repo.load("noexist", "prd")
        assert result is None


# ---------------------------------------------------------------------------
# EventRepository tests
# ---------------------------------------------------------------------------

class TestEventRepository:

    @pytest_asyncio.fixture
    async def event_repo(self, initialized_db):
        from orchestrator.db.session import get_session
        repo_cls = None
        from orchestrator.db.repositories.events import EventRepository
        return EventRepository(get_session, run_id="evtrun0001")

    @pytest.mark.asyncio
    async def test_append_and_get_slice(self, event_repo):
        await event_repo.append("agent_invoke", {"agent": "pm", "model": "sonnet"})
        await event_repo.append("agent_result", {"agent": "pm", "success": True, "cost_usd": 0.02})

        events = await event_repo.get_slice(offset=0, limit=10)
        assert len(events) == 2
        assert events[0]["event_type"] == "agent_invoke"
        assert events[1]["event_type"] == "agent_result"

    @pytest.mark.asyncio
    async def test_offset_pagination(self, event_repo):
        for i in range(5):
            await event_repo.append("tick", {"i": i})

        page1 = await event_repo.get_slice(offset=0, limit=3)
        page2 = await event_repo.get_slice(offset=3, limit=3)
        assert len(page1) == 3
        assert len(page2) == 2

    @pytest.mark.asyncio
    async def test_tail(self, event_repo):
        for i in range(4):
            await event_repo.append(f"evt{i}", {"i": i})

        tail = await event_repo.tail(after_seq=2)
        assert all(e["seq"] > 2 for e in tail)

    @pytest.mark.asyncio
    async def test_seq_monotonic(self, event_repo):
        for _ in range(5):
            await event_repo.append("tick", {})

        events = await event_repo.get_slice()
        seqs = [e["seq"] for e in events]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)

    @pytest.mark.asyncio
    async def test_push_sync_and_batch_flush(self, initialized_db):
        """Push events synchronously, then flush the queue to DB."""
        from orchestrator.db.session import get_session
        from orchestrator.db.repositories.events import EventRepository

        repo = EventRepository(get_session, run_id="syncrun0001")
        repo.push_sync("sync_event_1", {"key": "val1"})
        repo.push_sync("sync_event_2", {"key": "val2"})

        # Manually flush the queue
        await repo._flush_queue()

        events = await repo.get_slice()
        assert len(events) == 2


# ---------------------------------------------------------------------------
# AlertRepository tests
# ---------------------------------------------------------------------------

class TestAlertRepository:

    @pytest.mark.asyncio
    async def test_append_and_list(self, initialized_db):
        from orchestrator.db.session import get_session
        from orchestrator.db.repositories.alerts import AlertRepository

        repo = AlertRepository(get_session)
        await repo.append("budget_warning", "80% budget used", severity="warning", run_id=None)
        await repo.append("run_failed", "pm phase failed", severity="error", run_id=None)

        alerts = await repo.list(limit=10)
        assert len(alerts) >= 2
        types = {a["alert_type"] for a in alerts}
        assert "budget_warning" in types
        assert "run_failed" in types

    @pytest.mark.asyncio
    async def test_list_newest_first(self, initialized_db):
        from orchestrator.db.session import get_session
        from orchestrator.db.repositories.alerts import AlertRepository

        repo = AlertRepository(get_session)
        for i in range(3):
            await repo.append(f"alert_{i}", f"message {i}")

        alerts = await repo.list(limit=10)
        # Newest first — ids should be descending
        ids = [a["id"] for a in alerts]
        assert ids == sorted(ids, reverse=True)


# ---------------------------------------------------------------------------
# TimelineRepository tests
# ---------------------------------------------------------------------------

class TestTimelineRepository:

    @pytest.mark.asyncio
    async def test_upsert_and_get_all(self, initialized_db):
        from orchestrator.db.session import get_session
        from orchestrator.db.repositories.timeline import TimelineRepository

        repo = TimelineRepository(get_session)
        entry = {
            "task_id": "task-pm-001",
            "agent": "pm",
            "step": "pm",
            "status": "completed",
            "cost_usd": 0.05,
            "model_tier": "sonnet",
            "dependencies": [],
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": 12.5,
        }
        await repo.upsert_entry("tlrun0001", entry)

        entries = await repo.get_all("tlrun0001")
        assert len(entries) == 1
        assert entries[0]["task_id"] == "task-pm-001"
        assert entries[0]["cost_usd"] == pytest.approx(0.05)

    @pytest.mark.asyncio
    async def test_upsert_idempotent(self, initialized_db):
        from orchestrator.db.session import get_session
        from orchestrator.db.repositories.timeline import TimelineRepository

        repo = TimelineRepository(get_session)
        entry = {
            "task_id": "task-arch-001",
            "agent": "architect",
            "step": "architect",
            "status": "running",
            "start_time": datetime.now(timezone.utc).isoformat(),
        }
        await repo.upsert_entry("tlrun0002", entry)

        # Update status
        entry["status"] = "completed"
        await repo.upsert_entry("tlrun0002", entry)

        entries = await repo.get_all("tlrun0002")
        assert len(entries) == 1
        assert entries[0]["status"] == "completed"


# ---------------------------------------------------------------------------
# End-to-end: init_db + repositories with SQLite
# ---------------------------------------------------------------------------

class TestEndToEnd:

    @pytest.mark.asyncio
    async def test_full_run_lifecycle(self, initialized_db):
        """Simulate a full run: register → save artifact → log events → complete."""
        from orchestrator.db.session import get_session
        from orchestrator.db.repositories.runs import RunRepository
        from orchestrator.db.repositories.artifacts import ArtifactRepository
        from orchestrator.db.repositories.events import EventRepository
        from orchestrator.models import RunState, PhaseState, PhaseStatus

        run_id = "e2etest00001"

        # 1. Register run
        async with get_session() as session:
            run_repo = RunRepository(session)
            state = make_run_state(run_id=run_id, feature_request="E2E test feature")
            await run_repo.upsert(state)

        # 2. Save artifacts
        async with get_session() as session:
            art_repo = ArtifactRepository(session)
            await art_repo.save(run_id, "prd", {"title": "E2E PRD", "features": ["x"]}, agent="pm")
            await art_repo.save(run_id, "architecture", {"services": ["api"]}, agent="architect")

        # 3. Log events
        event_repo = EventRepository(get_session, run_id=run_id)
        await event_repo.append("run_start", {"feature_request": "E2E test feature"})
        await event_repo.append("agent_result", {"agent": "pm", "success": True, "cost_usd": 0.03})

        # 4. Complete run
        async with get_session() as session:
            run_repo = RunRepository(session)
            state.phases["pm"] = PhaseState(status=PhaseStatus.COMPLETED, cost_usd=0.03)
            state.total_cost_usd = 0.03
            await run_repo.upsert(state)
            await run_repo.update_status(run_id, "completed")

        # Verify
        async with get_session() as session:
            run_repo = RunRepository(session)
            result = await run_repo.get(run_id)
            assert result["status"] == "completed"
            assert result["total_cost_usd"] == pytest.approx(0.03)

        artifacts = await art_repo.list_for_run(run_id)
        assert {a["name"] for a in artifacts} == {"prd", "architecture"}

        events = await event_repo.get_slice()
        assert len(events) == 2
        assert events[0]["event_type"] == "run_start"

    @pytest.mark.asyncio
    async def test_file_only_mode_unchanged(self, tmp_path):
        """When database.url is empty, no DB imports happen and behavior is unchanged."""
        from orchestrator.models import DatabaseConfig
        config = DatabaseConfig(url="")
        assert not config.enabled

        # init_db with empty config is a no-op
        import orchestrator.db as db_module
        db_module._engine = None
        await db_module.init_db(config)
        assert db_module._engine is None
