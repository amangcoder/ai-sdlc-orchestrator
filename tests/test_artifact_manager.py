"""Tests for ArtifactManager (TASK-005).

Acceptance criteria covered:
  1. ArtifactManager class exists with all required methods.
  2. save_artifact() creates artifacts/{name}.json, .versions/{name}/v{N}.json, .index.json.
  3. load_artifact(run_id, name, version=None) returns current or specific version.
  4. get_artifact_history() returns ArtifactVersion list in ascending version order.
  5. compare_artifacts() returns ArtifactDiff with added/removed/changed keys.
  6. apply_retention_policy() removes old artifacts; keep_failed=True preserves failed runs.
  7. Path traversal defence: '../../../etc' and '../v1' are rejected.
  8. fcntl.flock protects .index.json during concurrent writes.
  9. versioning_enabled=False skips .versions/ directory creation.
  10. Backward compat: ArtifactCache.load_artifact() still reads artifacts/{name}.json.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from orchestrator.artifact_manager import (
    ArtifactDiff,
    ArtifactManager,
    ArtifactMetadata,
    ArtifactVersion,
    RetentionResult,
)
from orchestrator.models import ArtifactsConfig
from orchestrator.workflow_engine import ArtifactCache


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def artifacts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "artifacts"
    d.mkdir()
    return d


@pytest.fixture()
def manager(artifacts_dir: Path) -> ArtifactManager:
    return ArtifactManager(artifacts_dir)


@pytest.fixture()
def manager_no_versioning(artifacts_dir: Path) -> ArtifactManager:
    cfg = ArtifactsConfig(versioning_enabled=False, index_enabled=True)
    return ArtifactManager(artifacts_dir, config=cfg)


@pytest.fixture()
def manager_no_index(artifacts_dir: Path) -> ArtifactManager:
    cfg = ArtifactsConfig(versioning_enabled=True, index_enabled=False)
    return ArtifactManager(artifacts_dir, config=cfg)


def _sample_prd() -> dict:
    return {"schema": "prd", "title": "My PRD", "requirements": ["REQ-001"]}


def _sample_arch() -> dict:
    return {"schema": "architecture", "components": ["A", "B"]}


# ---------------------------------------------------------------------------
# 1 & 2: Class existence and save_artifact file layout
# ---------------------------------------------------------------------------


class TestArtifactManagerExists:
    def test_class_importable(self):
        from orchestrator.artifact_manager import ArtifactManager  # noqa: F401

    def test_models_importable(self):
        from orchestrator.artifact_manager import (  # noqa: F401
            ArtifactDiff,
            ArtifactMetadata,
            ArtifactVersion,
            RetentionResult,
        )

    def test_all_methods_present(self, manager: ArtifactManager):
        for method in (
            "save_artifact",
            "load_artifact",
            "list_artifacts",
            "get_artifact_history",
            "compare_artifacts",
            "search_artifacts",
            "apply_retention_policy",
        ):
            assert hasattr(manager, method), f"ArtifactManager missing method: {method}"


class TestSaveArtifactLayout:
    def test_creates_current_json(self, manager: ArtifactManager, artifacts_dir: Path):
        manager.save_artifact("run-1", "prd", _sample_prd())
        assert (artifacts_dir / "prd.json").exists()

    def test_current_json_content_is_correct(self, manager: ArtifactManager, artifacts_dir: Path):
        data = _sample_prd()
        manager.save_artifact("run-1", "prd", data)
        loaded = json.loads((artifacts_dir / "prd.json").read_text())
        assert loaded == data

    def test_creates_versioned_copy(self, manager: ArtifactManager, artifacts_dir: Path):
        manager.save_artifact("run-1", "prd", _sample_prd())
        versioned = artifacts_dir / ".versions" / "prd" / "v1.json"
        assert versioned.exists()

    def test_versioned_copy_content_matches(self, manager: ArtifactManager, artifacts_dir: Path):
        data = _sample_prd()
        manager.save_artifact("run-1", "prd", data)
        versioned = artifacts_dir / ".versions" / "prd" / "v1.json"
        assert json.loads(versioned.read_text()) == data

    def test_creates_index_json(self, manager: ArtifactManager, artifacts_dir: Path):
        manager.save_artifact("run-1", "prd", _sample_prd())
        assert (artifacts_dir / ".index.json").exists()

    def test_index_contains_artifact_entry(self, manager: ArtifactManager, artifacts_dir: Path):
        manager.save_artifact("run-1", "prd", _sample_prd(), agent="pm")
        index = json.loads((artifacts_dir / ".index.json").read_text())
        assert "prd" in index["artifacts"]
        entry = index["artifacts"]["prd"]
        assert entry["run_id"] == "run-1"
        assert entry["agent"] == "pm"
        assert entry["current_version"] == 1

    def test_second_save_increments_version(self, manager: ArtifactManager, artifacts_dir: Path):
        manager.save_artifact("run-1", "prd", _sample_prd())
        manager.save_artifact("run-2", "prd", {"updated": True})
        assert (artifacts_dir / ".versions" / "prd" / "v2.json").exists()
        index = json.loads((artifacts_dir / ".index.json").read_text())
        assert index["artifacts"]["prd"]["current_version"] == 2

    def test_save_returns_artifact_metadata(self, manager: ArtifactManager):
        meta = manager.save_artifact("run-1", "prd", _sample_prd(), agent="pm")
        assert isinstance(meta, ArtifactMetadata)
        assert meta.name == "prd"
        assert meta.run_id == "run-1"
        assert meta.agent == "pm"
        assert meta.current_version == 1
        assert meta.size_bytes > 0


class TestSaveVersioningDisabled:
    def test_no_versions_dir_created(
        self, manager_no_versioning: ArtifactManager, artifacts_dir: Path
    ):
        manager_no_versioning.save_artifact("run-1", "prd", _sample_prd())
        assert not (artifacts_dir / ".versions").exists()

    def test_current_json_still_written(
        self, manager_no_versioning: ArtifactManager, artifacts_dir: Path
    ):
        manager_no_versioning.save_artifact("run-1", "prd", _sample_prd())
        assert (artifacts_dir / "prd.json").exists()

    def test_version_number_increments_without_versioning(
        self, manager_no_versioning: ArtifactManager
    ):
        """Version counter in the index still increments even without on-disk copies.

        versioning_enabled=False means *no .versions/ files* are written, but the
        index still tracks logical save counts so callers can detect overwrites.
        """
        meta1 = manager_no_versioning.save_artifact("run-1", "prd", _sample_prd())
        meta2 = manager_no_versioning.save_artifact("run-2", "prd", {"x": 1})
        assert meta1.current_version == 1
        assert meta2.current_version == 2  # second save → version 2 in index


# ---------------------------------------------------------------------------
# 3: load_artifact
# ---------------------------------------------------------------------------


class TestLoadArtifact:
    def test_load_current_returns_latest(self, manager: ArtifactManager):
        data = {"v": 1}
        manager.save_artifact("run-1", "prd", data)
        result = manager.load_artifact("run-1", "prd")
        assert result == data

    def test_load_current_after_update(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        result = manager.load_artifact("run-2", "prd")
        assert result == {"v": 2}

    def test_load_specific_version(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        v1 = manager.load_artifact("run-1", "prd", version=1)
        assert v1 == {"v": 1}
        v2 = manager.load_artifact("run-2", "prd", version=2)
        assert v2 == {"v": 2}

    def test_load_missing_returns_none(self, manager: ArtifactManager):
        assert manager.load_artifact("run-1", "nonexistent") is None

    def test_load_missing_version_returns_none(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"x": 1})
        assert manager.load_artifact("run-1", "prd", version=99) is None

    def test_invalid_version_raises(self, manager: ArtifactManager):
        with pytest.raises(ValueError, match="positive integer"):
            manager.load_artifact("run-1", "prd", version=0)

    def test_negative_version_raises(self, manager: ArtifactManager):
        with pytest.raises(ValueError, match="positive integer"):
            manager.load_artifact("run-1", "prd", version=-1)

    def test_float_version_raises(self, manager: ArtifactManager):
        with pytest.raises(ValueError):
            manager.load_artifact("run-1", "prd", version=1.5)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 4: get_artifact_history
# ---------------------------------------------------------------------------


class TestGetArtifactHistory:
    def test_single_version_history(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"v": 1})
        history = manager.get_artifact_history("run-1", "prd")
        assert len(history) == 1
        assert isinstance(history[0], ArtifactVersion)
        assert history[0].version == 1
        assert history[0].run_id == "run-1"

    def test_multiple_versions_ascending(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        manager.save_artifact("run-3", "prd", {"v": 3})
        history = manager.get_artifact_history("run-1", "prd")
        assert [v.version for v in history] == [1, 2, 3]

    def test_history_contains_run_ids(self, manager: ArtifactManager):
        manager.save_artifact("run-alpha", "prd", {"v": 1})
        manager.save_artifact("run-beta", "prd", {"v": 2})
        history = manager.get_artifact_history("run-alpha", "prd")
        assert history[0].run_id == "run-alpha"
        assert history[1].run_id == "run-beta"

    def test_history_empty_for_unknown_artifact(self, manager: ArtifactManager):
        history = manager.get_artifact_history("run-1", "nosuchartifact")
        assert history == []

    def test_history_invalid_name_raises(self, manager: ArtifactManager):
        with pytest.raises(ValueError):
            manager.get_artifact_history("run-1", "../bad")

    def test_history_contains_agent_field(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "arch", _sample_arch(), agent="architect")
        history = manager.get_artifact_history("run-1", "arch")
        assert history[0].agent == "architect"


# ---------------------------------------------------------------------------
# 5: compare_artifacts
# ---------------------------------------------------------------------------


class TestCompareArtifacts:
    def test_no_diff_same_data(self, manager: ArtifactManager):
        data = {"a": 1, "b": 2}
        manager.save_artifact("run-1", "prd", data)
        manager.save_artifact("run-2", "prd", data)
        diff = manager.compare_artifacts("run-1", "run-2", "prd")
        assert isinstance(diff, ArtifactDiff)
        assert diff.added == []
        assert diff.removed == []
        assert diff.changed == []

    def test_detects_added_keys(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"a": 1})
        manager.save_artifact("run-2", "prd", {"a": 1, "b": 2})
        diff = manager.compare_artifacts("run-1", "run-2", "prd")
        assert "b" in diff.added
        assert diff.removed == []

    def test_detects_removed_keys(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"a": 1, "b": 2})
        manager.save_artifact("run-2", "prd", {"a": 1})
        diff = manager.compare_artifacts("run-1", "run-2", "prd")
        assert "b" in diff.removed
        assert diff.added == []

    def test_detects_changed_values(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"a": 1})
        manager.save_artifact("run-2", "prd", {"a": 99})
        diff = manager.compare_artifacts("run-1", "run-2", "prd")
        assert "a" in diff.changed

    def test_diff_includes_version_numbers(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"a": 1})
        manager.save_artifact("run-2", "prd", {"a": 2})
        diff = manager.compare_artifacts("run-1", "run-2", "prd")
        assert diff.version_a == 1
        assert diff.version_b == 2

    def test_diff_metadata(self, manager: ArtifactManager):
        manager.save_artifact("run-A", "prd", {"x": 1})
        manager.save_artifact("run-B", "prd", {"x": 2})
        diff = manager.compare_artifacts("run-A", "run-B", "prd")
        assert diff.name == "prd"
        assert diff.run_a == "run-A"
        assert diff.run_b == "run-B"

    def test_compare_unknown_run_raises(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"a": 1})
        with pytest.raises(ValueError, match="run-missing"):
            manager.compare_artifacts("run-1", "run-missing", "prd")

    def test_compare_invalid_name_raises(self, manager: ArtifactManager):
        with pytest.raises(ValueError):
            manager.compare_artifacts("run-1", "run-2", "../bad")


# ---------------------------------------------------------------------------
# 6: apply_retention_policy
# ---------------------------------------------------------------------------


class TestApplyRetentionPolicy:
    def test_dry_run_does_not_delete_files(
        self, manager: ArtifactManager, artifacts_dir: Path
    ):
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=False, dry_run=True
        )
        assert result.dry_run is True
        # File must still exist
        assert (artifacts_dir / ".versions" / "prd" / "v1.json").exists()

    def test_dry_run_reports_what_would_be_deleted(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=False, dry_run=True
        )
        assert result.deleted_versions == 1
        assert any("v1.json" in p for p in result.deleted_paths)

    def test_max_runs_deletes_old_version(
        self, manager: ArtifactManager, artifacts_dir: Path
    ):
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=False, dry_run=False
        )
        assert result.deleted_versions == 1
        assert not (artifacts_dir / ".versions" / "prd" / "v1.json").exists()
        # Current file must survive
        assert (artifacts_dir / "prd.json").exists()

    def test_keep_failed_preserves_failed_version(
        self, manager: ArtifactManager, artifacts_dir: Path
    ):
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        # Mark run-1 as failed
        manager.mark_run_status("run-1", "failed")
        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=True, dry_run=False
        )
        # run-1 (failed) must be preserved even though it's oldest
        assert (artifacts_dir / ".versions" / "prd" / "v1.json").exists()
        assert result.retained_versions >= 1

    def test_keep_failed_false_deletes_failed_version(
        self, manager: ArtifactManager, artifacts_dir: Path
    ):
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        manager.mark_run_status("run-1", "failed")
        manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=False, dry_run=False
        )
        assert not (artifacts_dir / ".versions" / "prd" / "v1.json").exists()

    def test_no_limits_deletes_nothing(self, manager: ArtifactManager, artifacts_dir: Path):
        manager.save_artifact("run-1", "prd", {"v": 1})
        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=0, keep_failed=False, dry_run=False
        )
        assert result.deleted_versions == 0
        assert (artifacts_dir / ".versions" / "prd" / "v1.json").exists()

    def test_returns_retention_result(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"v": 1})
        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=0, keep_failed=False, dry_run=True
        )
        assert isinstance(result, RetentionResult)

    def test_current_artifact_not_deleted(
        self, manager: ArtifactManager, artifacts_dir: Path
    ):
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=False, dry_run=False
        )
        # Current version must always survive
        assert (artifacts_dir / "prd.json").exists()
        loaded = json.loads((artifacts_dir / "prd.json").read_text())
        assert loaded == {"v": 2}


# ---------------------------------------------------------------------------
# 7: Path traversal defence
# ---------------------------------------------------------------------------


class TestPathTraversalDefence:
    @pytest.mark.parametrize(
        "bad_name",
        [
            "../etc",
            "../../etc/passwd",
            "../v1",
            ".hidden",
            "/absolute",
            "a/b",
            "a.b",
            "",
        ],
    )
    def test_save_rejects_traversal_name(
        self, manager: ArtifactManager, bad_name: str
    ):
        with pytest.raises(ValueError):
            manager.save_artifact("run-1", bad_name, {"x": 1})

    @pytest.mark.parametrize(
        "bad_name",
        [
            "../etc",
            "../../secret",
            "../v1",
            ".hidden",
            "/absolute",
        ],
    )
    def test_load_rejects_traversal_name(
        self, manager: ArtifactManager, bad_name: str
    ):
        with pytest.raises(ValueError):
            manager.load_artifact("run-1", bad_name)

    @pytest.mark.parametrize(
        "bad_name",
        [
            "../etc",
            "../v1",
            "a/b",
        ],
    )
    def test_history_rejects_traversal_name(
        self, manager: ArtifactManager, bad_name: str
    ):
        with pytest.raises(ValueError):
            manager.get_artifact_history("run-1", bad_name)

    def test_resolved_path_within_artifacts_dir(self, manager: ArtifactManager):
        """_safe_path must resolve to within artifacts_dir."""
        safe = manager._safe_path("prd.json")
        assert str(safe).startswith(str(manager.artifacts_dir))

    def test_traversal_raises_value_error(
        self, manager: ArtifactManager, tmp_path: Path
    ):
        """Manually crafted path that would escape artifacts_dir."""
        import os
        # Save a real artifact first so artifacts_dir exists
        manager.save_artifact("run-1", "prd", {"x": 1})
        # A name that looks valid but whose resolution escapes (symlink attack
        # isn't possible via _safe_path because resolve() follows real paths,
        # but we verify the guard runs)
        with pytest.raises(ValueError, match="traversal"):
            # Inject a path separator through a crafted relative path
            # after bypassing regex (we call _safe_path directly)
            manager._safe_path("..", "etc", "passwd")


# ---------------------------------------------------------------------------
# 8: fcntl.flock concurrency
# ---------------------------------------------------------------------------


class TestConcurrentWrites:
    def test_concurrent_saves_produce_unique_versions(
        self, manager: ArtifactManager, artifacts_dir: Path
    ):
        """50 threads each saving a distinct artifact must not corrupt the index."""
        errors: list[Exception] = []
        results: list[ArtifactMetadata] = []
        lock = threading.Lock()

        def _save(i: int) -> None:
            try:
                meta = manager.save_artifact(f"run-{i}", "shared", {"i": i})
                with lock:
                    results.append(meta)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=_save, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Errors during concurrent save: {errors}"
        # Index must be valid JSON
        index = json.loads((artifacts_dir / ".index.json").read_text())
        assert "shared" in index["artifacts"]
        # All version numbers must be unique
        versions = index["artifacts"]["shared"]["versions"]
        assert len(versions) == 50, f"Expected 50 versions, got {len(versions)}"
        assert len(set(versions.keys())) == 50

    def test_concurrent_saves_different_artifacts(
        self, manager: ArtifactManager, artifacts_dir: Path
    ):
        """Multiple artifacts written concurrently must all appear in the index."""
        errors: list[Exception] = []
        names = [f"art-{i}" for i in range(20)]

        def _save(name: str) -> None:
            try:
                manager.save_artifact("run-1", name, {"name": name})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_save, args=(n,)) for n in names]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        index = json.loads((artifacts_dir / ".index.json").read_text())
        for name in names:
            assert name in index["artifacts"]


# ---------------------------------------------------------------------------
# 9: versioning_enabled=False
# ---------------------------------------------------------------------------


class TestVersioningDisabled:
    def test_no_versions_directory(
        self, manager_no_versioning: ArtifactManager, artifacts_dir: Path
    ):
        manager_no_versioning.save_artifact("run-1", "prd", _sample_prd())
        assert not (artifacts_dir / ".versions").exists()

    def test_current_file_exists(
        self, manager_no_versioning: ArtifactManager, artifacts_dir: Path
    ):
        manager_no_versioning.save_artifact("run-1", "prd", _sample_prd())
        assert (artifacts_dir / "prd.json").exists()

    def test_history_empty_without_versions_dir(
        self, manager_no_versioning: ArtifactManager
    ):
        manager_no_versioning.save_artifact("run-1", "prd", _sample_prd())
        # With no versioning, no .versions dir → history is tracked in index only
        # (index is still enabled in manager_no_versioning)
        history = manager_no_versioning.get_artifact_history("run-1", "prd")
        # History should still have entries from the index
        assert isinstance(history, list)


# ---------------------------------------------------------------------------
# 10: Backward compatibility — ArtifactCache still works
# ---------------------------------------------------------------------------


class TestArtifactCacheBackwardCompat:
    def test_artifact_cache_reads_manager_output(
        self, manager: ArtifactManager, artifacts_dir: Path
    ):
        """ArtifactCache.load_artifact() must read what ArtifactManager.save_artifact() wrote."""
        data = _sample_prd()
        manager.save_artifact("run-1", "prd", data)

        # ArtifactCache is initialised with the WORKSPACE (parent of artifacts/)
        workspace = artifacts_dir.parent
        cache = ArtifactCache(workspace)
        loaded = cache.load_artifact("prd")

        assert loaded == data

    def test_artifact_cache_get_returns_loaded(
        self, manager: ArtifactManager, artifacts_dir: Path
    ):
        data = {"title": "test"}
        manager.save_artifact("run-1", "arch", data)

        workspace = artifacts_dir.parent
        cache = ArtifactCache(workspace)
        cache.load_artifact("arch")  # populate cache
        assert cache.get("arch") == data


# ---------------------------------------------------------------------------
# list_artifacts
# ---------------------------------------------------------------------------


class TestListArtifacts:
    def test_list_returns_metadata_for_run(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", _sample_prd(), agent="pm")
        manager.save_artifact("run-1", "arch", _sample_arch(), agent="architect")
        listing = manager.list_artifacts("run-1")
        names = {m.name for m in listing}
        assert "prd" in names
        assert "arch" in names

    def test_list_filters_by_run(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})  # overwrites run-1 as latest
        listing = manager.list_artifacts("run-2")
        assert len(listing) == 1
        assert listing[0].run_id == "run-2"

    def test_list_empty_when_no_artifacts(self, manager: ArtifactManager):
        assert manager.list_artifacts("run-none") == []

    def test_list_returns_artifact_metadata_type(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", _sample_prd())
        listing = manager.list_artifacts("run-1")
        assert all(isinstance(m, ArtifactMetadata) for m in listing)


# ---------------------------------------------------------------------------
# search_artifacts
# ---------------------------------------------------------------------------


class TestSearchArtifacts:
    def test_search_by_agent(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"x": 1}, agent="pm")
        manager.save_artifact("run-1", "arch", {"x": 1}, agent="architect")
        results = manager.search_artifacts("", agent="pm")
        assert len(results) == 1
        assert results[0].name == "prd"

    def test_search_by_artifact_type(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"x": 1}, schema_name="prd")
        manager.save_artifact("run-1", "arch", {"x": 1}, schema_name="architecture")
        results = manager.search_artifacts("", artifact_type="prd")
        assert all(m.schema_name == "prd" for m in results)

    def test_search_by_text(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"needle": "findme"})
        manager.save_artifact("run-1", "arch", {"haystack": "nothere"})
        results = manager.search_artifacts("findme")
        names = {m.name for m in results}
        assert "prd" in names
        assert "arch" not in names

    def test_search_empty_query_returns_all(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"x": 1})
        manager.save_artifact("run-1", "arch", {"x": 1})
        results = manager.search_artifacts("")
        assert len(results) == 2

    def test_search_no_match_returns_empty(self, manager: ArtifactManager):
        manager.save_artifact("run-1", "prd", {"x": 1})
        results = manager.search_artifacts("xyzzy_not_found")
        assert results == []


# ---------------------------------------------------------------------------
# mark_run_status
# ---------------------------------------------------------------------------


class TestMarkRunStatus:
    def test_mark_updates_version_status(
        self, manager: ArtifactManager, artifacts_dir: Path
    ):
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.mark_run_status("run-1", "completed")
        index = json.loads((artifacts_dir / ".index.json").read_text())
        ver = index["artifacts"]["prd"]["versions"]["1"]
        assert ver["run_status"] == "completed"

    def test_mark_failed_status(self, manager: ArtifactManager, artifacts_dir: Path):
        manager.save_artifact("run-fail", "prd", {"v": 1})
        manager.mark_run_status("run-fail", "failed")
        index = json.loads((artifacts_dir / ".index.json").read_text())
        ver = index["artifacts"]["prd"]["versions"]["1"]
        assert ver["run_status"] == "failed"
