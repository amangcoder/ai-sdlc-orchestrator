"""Unit tests for src/orchestrator/research_cache.py.

Covers all acceptance criteria defined in TASK-010:
- Lookup: hit/miss, TTL expiry, tag-overlap sorting, project isolation
- save_entry: max_entries eviction, atomic writes (concurrent threads)
- extract_research_from_artifact: architecture.json -> global tier
- format_recommendations: header + severity lines
- get_research_mcp_config: returns None when server binary absent
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from orchestrator.models import Finding, ResearchCache, ResearchEntry
from orchestrator.research_cache import (
    extract_research_from_artifact,
    flag_finding,
    format_recommendations,
    get_research_mcp_config,
    load_cache,
    lookup,
    save_entry,
    validate_cache_paths,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    topic: str = "Test topic",
    content: str = "Test content",
    tags: list[str] | None = None,
    tier: str = "global",
    days_old: int = 0,
    ttl_days: int = 90,
    source_phase: str = "architect",
    run_id: str = "run-test",
) -> ResearchEntry:
    """Construct a ResearchEntry with created_at adjusted by days_old."""
    created_at = (datetime.now(tz=timezone.utc) - timedelta(days=days_old)).isoformat()
    return ResearchEntry(
        topic=topic,
        content=content,
        tags=tags or [],
        tier=tier,  # type: ignore[arg-type]
        created_at=created_at,
        ttl_days=ttl_days,
        source_phase=source_phase,
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# Lookup tests
# ---------------------------------------------------------------------------

class TestLookup:
    def test_lookup_returns_hit_for_matching_tags(self):
        entry = _make_entry(
            topic="Python performance tuning",
            tags=["python", "performance"],
            days_old=30,
            ttl_days=90,
        )
        cache = ResearchCache(global_entries=[entry])
        results = lookup(cache, "Python performance", ["python", "performance"])
        assert len(results) == 1
        assert results[0].topic == "Python performance tuning"

    def test_lookup_returns_miss_for_unknown_topic(self):
        entry = _make_entry(topic="Redis caching", tags=["redis", "cache"])
        cache = ResearchCache(global_entries=[entry])
        results = lookup(cache, "GraphQL schema", ["graphql", "api"])
        assert results == []

    @pytest.mark.parametrize("days_old,should_return", [
        (89, True),
        (90, True),
        (91, False),
    ])
    def test_lookup_ttl_expiry(self, days_old: int, should_return: bool):
        entry = _make_entry(
            topic="React performance",
            tags=["react"],
            days_old=days_old,
            ttl_days=90,
        )
        cache = ResearchCache(global_entries=[entry])
        results = lookup(cache, "React performance", ["react"])
        if should_return:
            assert len(results) == 1, f"Expected hit for {days_old} days old, ttl=90"
        else:
            assert len(results) == 0, f"Expected miss for {days_old} days old, ttl=90"

    def test_lookup_sorting_by_tag_overlap(self):
        entry_a = _make_entry(
            topic="React Native performance on mobile",
            tags=["react-native", "performance", "mobile"],
            days_old=5,
        )
        entry_b = _make_entry(
            topic="Vue performance patterns",
            tags=["vue", "performance"],
            days_old=1,
        )
        cache = ResearchCache(global_entries=[entry_a, entry_b])
        results = lookup(cache, "performance", ["react-native", "performance"])
        assert len(results) == 2
        # entry_a has 2 overlapping tags ("react-native" + "performance"),
        # entry_b has 1 overlapping tag ("performance") — a must come first
        assert results[0].topic == entry_a.topic
        assert results[1].topic == entry_b.topic

    def test_lookup_topic_substring_match_without_tag_overlap(self):
        entry = _make_entry(
            topic="PostgreSQL indexing strategies",
            tags=["postgresql", "database"],
            days_old=1,
        )
        cache = ResearchCache(global_entries=[entry])
        # No tag overlap, but topic contains the query substring
        results = lookup(cache, "PostgreSQL", ["mysql"])
        assert len(results) == 1

    def test_lookup_respects_project_isolation(self, tmp_path: Path):
        """Entries saved to project-A local_dir must not appear when loading project-B."""
        project_a = tmp_path / "project_a" / ".knowledge" / "research"
        project_b = tmp_path / "project_b" / ".knowledge" / "research"

        # Save an entry to project-A
        entry = _make_entry(
            topic="Project-A secret sauce",
            tags=["project-a"],
            tier="project",
        )
        cache_a = ResearchCache()
        save_entry(cache_a, entry, local_dir=project_a, max_entries=500)

        # Load cache from project-B (different directory)
        cache_b = load_cache(
            global_dir=tmp_path / "global",
            local_dir=project_b,
        )
        results = lookup(cache_b, "Project-A secret sauce", ["project-a"])
        assert results == [], "project-A entry should not appear in project-B lookup"

    def test_lookup_ignores_expired_even_with_tag_match(self):
        entry = _make_entry(
            topic="Python async patterns",
            tags=["python", "async"],
            days_old=100,
            ttl_days=90,
        )
        cache = ResearchCache(global_entries=[entry])
        results = lookup(cache, "python async", ["python", "async"])
        assert results == [], "Expired entry must not be returned even with perfect tag match"


# ---------------------------------------------------------------------------
# save_entry tests
# ---------------------------------------------------------------------------

class TestSaveEntry:
    def test_save_entry_persists_to_disk(self, tmp_path: Path):
        global_dir = tmp_path / "global"
        entry = _make_entry(topic="Saved entry", tags=["test"])
        cache = ResearchCache()
        save_entry(cache, entry, global_dir=global_dir, max_entries=500)

        index_path = global_dir / "index.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text())
        assert len(index["entries"]) == 1

    def test_save_entry_updates_in_memory_cache(self, tmp_path: Path):
        global_dir = tmp_path / "global"
        entry = _make_entry(topic="In-memory test", tags=["test"])
        cache = ResearchCache()
        save_entry(cache, entry, global_dir=global_dir, max_entries=500)
        assert len(cache.global_entries) == 1
        assert cache.global_entries[0].topic == "In-memory test"

    def test_save_entry_max_entries_eviction(self, tmp_path: Path):
        """When 5 entries exist at max_entries=5, adding a 6th evicts the oldest."""
        global_dir = tmp_path / "global"
        cache = ResearchCache()
        max_entries = 5

        # Insert 5 entries with distinct created_at timestamps, oldest first
        oldest_topic = None
        for i in range(max_entries):
            e = _make_entry(
                topic=f"Entry {i}",
                tags=[f"tag{i}"],
                days_old=max_entries - i,  # entry 0 is oldest (most days_old)
            )
            if i == 0:
                oldest_topic = e.topic
            save_entry(cache, e, global_dir=global_dir, max_entries=max_entries)

        assert len(cache.global_entries) == max_entries

        # Add 6th entry — should evict the oldest
        new_entry = _make_entry(topic="New entry 6th", tags=["new"])
        save_entry(cache, new_entry, global_dir=global_dir, max_entries=max_entries)

        # Count must stay at max_entries
        index = json.loads((global_dir / "index.json").read_text())
        assert len(index["entries"]) == max_entries

        # Oldest entry must be gone from in-memory list
        topics_in_cache = [e.topic for e in cache.global_entries]
        assert oldest_topic not in topics_in_cache, f"Oldest entry '{oldest_topic}' should have been evicted"
        assert "New entry 6th" in topics_in_cache

    def test_save_entry_atomic_write_concurrent(self, tmp_path: Path):
        """Concurrent saves from 2 threads must not corrupt index.json."""
        global_dir = tmp_path / "global"
        cache = ResearchCache()
        errors: list[Exception] = []

        def save_worker(topic_suffix: str) -> None:
            try:
                entry = _make_entry(topic=f"Concurrent entry {topic_suffix}", tags=[topic_suffix])
                save_entry(cache, entry, global_dir=global_dir, max_entries=500)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=save_worker, args=("thread-1",))
        t2 = threading.Thread(target=save_worker, args=("thread-2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == [], f"Concurrent saves raised exceptions: {errors}"

        # index.json must be valid JSON with both entries
        index_path = global_dir / "index.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text())  # raises if invalid JSON
        assert len(index["entries"]) == 2, (
            f"Expected 2 entries after concurrent saves, got {len(index['entries'])}"
        )

    def test_save_entry_idempotent_for_same_topic_tags(self, tmp_path: Path):
        """Saving the same entry twice must not duplicate it in the index."""
        global_dir = tmp_path / "global"
        entry = _make_entry(topic="Idempotent topic", tags=["idempotent"])
        cache = ResearchCache()
        save_entry(cache, entry, global_dir=global_dir, max_entries=500)
        save_entry(cache, entry, global_dir=global_dir, max_entries=500)

        index = json.loads((global_dir / "index.json").read_text())
        assert len(index["entries"]) == 1


# ---------------------------------------------------------------------------
# extract_research_from_artifact tests
# ---------------------------------------------------------------------------

class TestExtractResearchFromArtifact:
    def test_extract_from_architecture_returns_global_tier(self, tmp_path: Path):
        artifact = tmp_path / "architecture.json"
        artifact.write_text(json.dumps({
            "title": "System Architecture",
            "tech_decisions": [
                {
                    "decision": "Use PostgreSQL",
                    "rationale": "ACID compliance and mature ecosystem",
                    "alternatives_considered": ["MySQL", "SQLite"],
                }
            ],
        }))
        entries = extract_research_from_artifact(artifact, "architect", "run-123")
        assert len(entries) >= 1
        global_entries = [e for e in entries if e.tier == "global"]
        assert len(global_entries) >= 1
        assert global_entries[0].source_phase == "architect"

    def test_extract_from_market_research_returns_project_tier(self, tmp_path: Path):
        artifact = tmp_path / "market_research.json"
        artifact.write_text(json.dumps({
            "trends": [{"trend": "AI-powered mobile apps growing 40% YoY"}],
        }))
        entries = extract_research_from_artifact(artifact, "market_researcher", "run-456")
        project_entries = [e for e in entries if e.tier == "project"]
        assert len(project_entries) >= 1

    def test_extract_returns_empty_for_missing_artifact(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.json"
        entries = extract_research_from_artifact(missing, "architect", "run-001")
        assert entries == []

    def test_extract_returns_empty_for_unrecognized_phase(self, tmp_path: Path):
        artifact = tmp_path / "some_artifact.json"
        artifact.write_text(json.dumps({"foo": "bar"}))
        entries = extract_research_from_artifact(artifact, "unknown_phase", "run-001")
        assert entries == []

    def test_extract_run_id_is_propagated(self, tmp_path: Path):
        artifact = tmp_path / "architecture.json"
        artifact.write_text(json.dumps({
            "tech_decisions": [{"decision": "Use Redis", "rationale": "Speed"}]
        }))
        entries = extract_research_from_artifact(artifact, "architect", "run-xyz-999")
        assert all(e.run_id == "run-xyz-999" for e in entries)


# ---------------------------------------------------------------------------
# format_recommendations tests
# ---------------------------------------------------------------------------

class TestFormatRecommendations:
    def test_format_recommendations_includes_header(self):
        findings = [
            Finding(
                type="performance",
                severity="high",
                finding="N+1 query in orders endpoint",
                recommendation="Add eager loading for order items",
                phase="architect",
            )
        ]
        output = format_recommendations([f.model_dump() for f in findings])
        assert "Recommendations:" in output

    def test_format_recommendations_includes_both_severities(self):
        findings = [
            Finding(
                type="security",
                severity="high",
                finding="SQL injection risk in search",
                recommendation="Use parameterized queries",
                phase="architect",
            ),
            Finding(
                type="quality",
                severity="medium",
                finding="Missing input validation on signup",
                recommendation="Add Pydantic validators",
                phase="pm",
            ),
        ]
        raw = [f.model_dump() for f in findings]
        output = format_recommendations(raw)
        assert "high" in output
        assert "medium" in output
        assert "SQL injection risk in search" in output
        assert "parameterized queries" in output
        assert "Missing input validation on signup" in output
        assert "Pydantic validators" in output

    def test_format_recommendations_returns_empty_string_for_no_findings(self):
        assert format_recommendations([]) == ""

    def test_format_recommendations_uses_arrow_separator(self):
        findings = [
            Finding(
                type="dependency",
                severity="low",
                finding="Outdated npm package",
                recommendation="Upgrade to latest",
                phase="reviewer",
            )
        ]
        output = format_recommendations([f.model_dump() for f in findings])
        assert "→" in output


# ---------------------------------------------------------------------------
# flag_finding tests
# ---------------------------------------------------------------------------

class TestFlagFinding:
    def test_flag_finding_appends_to_list(self):
        findings: list[dict] = []
        f = Finding(
            type="architecture",
            severity="medium",
            finding="Coupling between layers",
            recommendation="Introduce a service layer",
            phase="architect",
        )
        flag_finding(findings, f)
        assert len(findings) == 1
        assert findings[0]["finding"] == "Coupling between layers"

    def test_flag_finding_serializes_as_dict(self):
        findings: list[dict] = []
        f = Finding(
            type="performance",
            severity="high",
            finding="Slow query",
            recommendation="Add index",
            phase="architect",
        )
        flag_finding(findings, f)
        assert isinstance(findings[0], dict)
        assert findings[0]["severity"] == "high"


# ---------------------------------------------------------------------------
# get_research_mcp_config tests
# ---------------------------------------------------------------------------

class TestGetResearchMcpConfig:
    @patch("orchestrator.research_cache._SEARCH_PATHS", [])
    def test_returns_none_when_server_missing(self, tmp_path: Path):
        result = get_research_mcp_config("", tmp_path)
        assert result is None

    def test_returns_none_for_nonexistent_path(self, tmp_path: Path):
        nonexistent = str(tmp_path / "no_such_dir" / "index.js")
        result = get_research_mcp_config(nonexistent, tmp_path)
        assert result is None

    def test_returns_config_when_server_found(self, tmp_path: Path):
        # Create a fake dist/index.js
        dist_dir = tmp_path / "fake-server" / "dist"
        dist_dir.mkdir(parents=True)
        fake_js = dist_dir / "index.js"
        fake_js.write_text("// fake server")

        result = get_research_mcp_config(str(fake_js), tmp_path)
        assert result is not None
        assert "mcpServers" in result
        assert "research-cache" in result["mcpServers"]
        server_conf = result["mcpServers"]["research-cache"]
        assert server_conf["command"] == "node"
        assert str(fake_js) in server_conf["args"]


# ---------------------------------------------------------------------------
# validate_cache_paths tests
# ---------------------------------------------------------------------------

class TestValidateCachePaths:
    def test_valid_paths_do_not_raise(self, tmp_path: Path):
        global_dir = Path.home() / ".orchestrator" / "research"
        local_dir = tmp_path / ".knowledge" / "research"
        # Should not raise
        validate_cache_paths(global_dir, local_dir, tmp_path)

    def test_global_dir_outside_home_raises(self, tmp_path: Path):
        bad_global = tmp_path / "not_under_home"
        local_dir = tmp_path / ".knowledge" / "research"
        with pytest.raises(ValueError, match="global_dir must be under the user home directory"):
            validate_cache_paths(bad_global, local_dir, tmp_path)

    def test_local_dir_outside_project_root_raises(self, tmp_path: Path):
        global_dir = Path.home() / ".orchestrator" / "research"
        bad_local = Path("/tmp/wrong_project/.knowledge/research")
        with pytest.raises(ValueError, match="local_dir must be under project_root"):
            validate_cache_paths(global_dir, bad_local, tmp_path)


# ---------------------------------------------------------------------------
# load_cache tests
# ---------------------------------------------------------------------------

class TestLoadCache:
    def test_load_cache_returns_empty_for_missing_dirs(self, tmp_path: Path):
        cache = load_cache(
            global_dir=tmp_path / "nonexistent_global",
            local_dir=tmp_path / "nonexistent_local",
        )
        assert cache.global_entries == []
        assert cache.local_entries == []

    def test_load_cache_reads_saved_entries(self, tmp_path: Path):
        global_dir = tmp_path / "global"
        local_dir = tmp_path / "local"
        g_cache = ResearchCache()
        l_cache = ResearchCache()

        global_entry = _make_entry(topic="Global knowledge", tags=["global"], tier="global")
        local_entry = _make_entry(topic="Local project data", tags=["local"], tier="project")

        save_entry(g_cache, global_entry, global_dir=global_dir, max_entries=500)
        save_entry(l_cache, local_entry, local_dir=local_dir, max_entries=500)

        loaded = load_cache(global_dir=global_dir, local_dir=local_dir)
        assert len(loaded.global_entries) == 1
        assert loaded.global_entries[0].topic == "Global knowledge"
        assert len(loaded.local_entries) == 1
        assert loaded.local_entries[0].topic == "Local project data"
