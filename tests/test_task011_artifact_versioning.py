"""Integration tests for TASK-011: Artifact Versioning, Concurrent Access, and Retention.

Acceptance criteria covered:
  AC-005: save_artifact() creates .versions/{name}/v{N}.json + .index.json with full metadata
           (including SHA-256 checksum per version and valid field on ArtifactMetadata)
  AC-006: get_artifact_history() returns 3 entries with versions 1,2,3 and distinct checksums
  compare_artifacts latest version: run with 3 prd versions → compare uses v3, not v1
  fcntl.flock: 10 parallel save_artifact() calls → no index corruption, no version collisions
  AC-009: apply_retention_policy() with age and count limits removes old runs
  keep_failed: failed runs preserved when keep_failed=True
  search_artifacts: filters correctly by type, agent, and text content
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.artifact_manager import (
    ArtifactDiff,
    ArtifactManager,
    ArtifactMetadata,
    ArtifactVersion,
    RetentionResult,
)
from orchestrator.models import ArtifactsConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def artifacts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "artifacts"
    d.mkdir()
    return d


@pytest.fixture()
def manager(artifacts_dir: Path) -> ArtifactManager:
    return ArtifactManager(artifacts_dir)


# ---------------------------------------------------------------------------
# AC-005: .versions/ subdirectory and .index.json layout
# ---------------------------------------------------------------------------


class TestAC005VersioningLayout:
    """AC-005: save_artifact() creates .versions/ subdirectory and .index.json correctly."""

    def test_versions_subdirectory_created_on_first_save(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-005: .versions/prd/ directory is created by the first save."""
        manager.save_artifact("run-1", "prd", {"title": "PRD v1"})
        assert (artifacts_dir / ".versions" / "prd").is_dir(), (
            "AC-005: .versions/prd/ directory was not created by save_artifact()"
        )

    def test_versioned_file_v1_json_created(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-005: .versions/prd/v1.json created on first save."""
        manager.save_artifact("run-1", "prd", {"title": "PRD v1"})
        assert (artifacts_dir / ".versions" / "prd" / "v1.json").exists(), (
            "AC-005: .versions/prd/v1.json not found after save_artifact()"
        )

    def test_versioned_file_content_matches_saved_data(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-005: .versions/prd/v1.json content equals what was saved."""
        data = {"title": "My PRD", "requirements": ["REQ-001", "REQ-002"]}
        manager.save_artifact("run-1", "prd", data)
        v1 = json.loads((artifacts_dir / ".versions" / "prd" / "v1.json").read_text())
        assert v1 == data

    def test_index_json_file_created(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-005: .index.json is created by save_artifact()."""
        manager.save_artifact("run-1", "prd", {"x": 1})
        assert (artifacts_dir / ".index.json").exists(), (
            "AC-005: .index.json not created by save_artifact()"
        )

    def test_index_json_has_required_metadata_fields(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-005: .index.json entry contains all required metadata fields."""
        data = {"title": "PRD"}
        manager.save_artifact("run-1", "prd", data, agent="pm", schema_name="prd")
        index = json.loads((artifacts_dir / ".index.json").read_text())
        entry = index["artifacts"]["prd"]

        assert entry["name"] == "prd"
        assert entry["current_version"] == 1
        assert entry["run_id"] == "run-1"
        assert entry["agent"] == "pm"
        assert entry["schema_name"] == "prd"
        assert "created_at" in entry
        assert "updated_at" in entry
        assert entry["size_bytes"] > 0

    def test_index_json_version_entry_has_checksum(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-005: .index.json versions entry has 'checksum' field (SHA-256 hex)."""
        manager.save_artifact("run-1", "prd", {"key": "value"})
        index = json.loads((artifacts_dir / ".index.json").read_text())
        ver_data = index["artifacts"]["prd"]["versions"]["1"]

        assert "checksum" in ver_data, (
            "AC-005: .index.json versions entry is missing 'checksum' field. "
            "save_artifact() must compute and store a SHA-256 checksum per version."
        )
        checksum = ver_data["checksum"]
        assert len(checksum) == 64, (
            f"Checksum must be a 64-char SHA-256 hex digest, got {len(checksum)} chars: {checksum!r}"
        )
        assert all(c in "0123456789abcdef" for c in checksum), (
            f"Checksum must be lowercase hex, got: {checksum!r}"
        )

    def test_checksum_in_index_matches_versioned_file(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-005: checksum in .index.json matches SHA-256 of the versioned file bytes."""
        data = {"title": "PRD", "requirements": ["REQ-001"]}
        manager.save_artifact("run-1", "prd", data)

        index = json.loads((artifacts_dir / ".index.json").read_text())
        stored_checksum = index["artifacts"]["prd"]["versions"]["1"]["checksum"]

        file_bytes = (artifacts_dir / ".versions" / "prd" / "v1.json").read_bytes()
        expected = hashlib.sha256(file_bytes).hexdigest()
        assert stored_checksum == expected, (
            f"Index checksum {stored_checksum!r} does not match "
            f"SHA-256({file_bytes[:40]!r}...) = {expected!r}"
        )

    def test_second_save_creates_v2_and_increments_index(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-005: Second save creates v2.json and bumps current_version to 2."""
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        assert (artifacts_dir / ".versions" / "prd" / "v2.json").exists()
        index = json.loads((artifacts_dir / ".index.json").read_text())
        assert index["artifacts"]["prd"]["current_version"] == 2

    def test_save_artifact_returns_metadata_with_valid_field(
        self, manager: ArtifactManager
    ) -> None:
        """AC-005: ArtifactMetadata returned by save_artifact() has a 'valid' bool field."""
        meta = manager.save_artifact("run-1", "prd", {"x": 1})
        assert isinstance(meta, ArtifactMetadata)
        assert hasattr(meta, "valid"), (
            "ArtifactMetadata must have a 'valid' field. "
            "Architecture: class ArtifactMetadata(BaseModel): valid: bool = True"
        )
        assert meta.valid is True

    def test_save_artifact_metadata_fields_complete(
        self, manager: ArtifactManager
    ) -> None:
        """AC-005: ArtifactMetadata has name, current_version, run_id, agent, size_bytes."""
        meta = manager.save_artifact("run-1", "prd", {"title": "test"}, agent="pm")
        assert meta.name == "prd"
        assert meta.run_id == "run-1"
        assert meta.agent == "pm"
        assert meta.current_version == 1
        assert meta.size_bytes > 0


# ---------------------------------------------------------------------------
# AC-006: get_artifact_history — 3 versions with distinct checksums
# ---------------------------------------------------------------------------


class TestAC006ArtifactHistory:
    """AC-006: get_artifact_history() returns 3 entries with versions 1,2,3 and distinct checksums."""

    def test_three_saves_return_three_history_entries(
        self, manager: ArtifactManager
    ) -> None:
        """AC-006: 3 saves produce 3 history entries."""
        manager.save_artifact("run-1", "prd", {"v": 1, "data": "alpha"})
        manager.save_artifact("run-2", "prd", {"v": 2, "data": "beta"})
        manager.save_artifact("run-3", "prd", {"v": 3, "data": "gamma"})
        history = manager.get_artifact_history("run-1", "prd")
        assert len(history) == 3, f"Expected 3 history entries, got {len(history)}"

    def test_history_versions_are_1_2_3_ascending(
        self, manager: ArtifactManager
    ) -> None:
        """AC-006: History versions are [1, 2, 3] in ascending order."""
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        manager.save_artifact("run-3", "prd", {"v": 3})
        history = manager.get_artifact_history("run-1", "prd")
        assert [v.version for v in history] == [1, 2, 3]

    def test_history_entries_are_artifact_version_instances(
        self, manager: ArtifactManager
    ) -> None:
        """AC-006: Each history entry is an ArtifactVersion instance."""
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        manager.save_artifact("run-3", "prd", {"v": 3})
        history = manager.get_artifact_history("run-1", "prd")
        assert all(isinstance(v, ArtifactVersion) for v in history)

    def test_history_run_ids_match_saves(self, manager: ArtifactManager) -> None:
        """AC-006: run_id in each history entry matches the run that saved it."""
        manager.save_artifact("run-alpha", "prd", {"v": 1})
        manager.save_artifact("run-beta", "prd", {"v": 2})
        manager.save_artifact("run-gamma", "prd", {"v": 3})
        history = manager.get_artifact_history("run-alpha", "prd")
        assert [v.run_id for v in history] == ["run-alpha", "run-beta", "run-gamma"]

    def test_history_has_checksum_field_on_each_entry(
        self, manager: ArtifactManager
    ) -> None:
        """AC-006: Every ArtifactVersion entry has a 'checksum' field."""
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        manager.save_artifact("run-3", "prd", {"v": 3})
        history = manager.get_artifact_history("run-1", "prd")
        for v in history:
            assert hasattr(v, "checksum"), (
                f"ArtifactVersion v{v.version} missing 'checksum' field. "
                "Architecture: class ArtifactVersion(BaseModel): checksum: str = ''"
            )

    def test_three_different_payloads_produce_three_distinct_checksums(
        self, manager: ArtifactManager
    ) -> None:
        """AC-006: 3 distinct payloads → 3 distinct non-empty checksums."""
        manager.save_artifact("run-1", "prd", {"v": 1, "data": "first_unique_abc"})
        manager.save_artifact("run-2", "prd", {"v": 2, "data": "second_unique_xyz"})
        manager.save_artifact("run-3", "prd", {"v": 3, "data": "third_unique_999"})
        history = manager.get_artifact_history("run-1", "prd")

        checksums = [v.checksum for v in history]

        # Each must be a non-empty 64-char hex string
        for i, cs in enumerate(checksums, 1):
            assert cs, f"v{i} checksum is empty — SHA-256 must be stored"
            assert len(cs) == 64, f"v{i} checksum has wrong length: {cs!r}"

        # All three must be distinct (different payloads → different checksums)
        assert len(set(checksums)) == 3, (
            f"Expected 3 distinct checksums for 3 different versions, "
            f"got: {checksums}"
        )

    def test_identical_payloads_produce_identical_checksums(
        self, manager: ArtifactManager
    ) -> None:
        """AC-006: Same payload saved twice → identical checksums (deterministic)."""
        same_data = {"v": 1, "stable": True, "name": "fixed"}
        manager.save_artifact("run-1", "prd", same_data)
        manager.save_artifact("run-2", "prd", same_data)
        history = manager.get_artifact_history("run-1", "prd")
        assert history[0].checksum == history[1].checksum, (
            "Identical payloads must produce identical SHA-256 checksums"
        )

    def test_history_run_id_arg_is_informational(
        self, manager: ArtifactManager
    ) -> None:
        """AC-006: get_artifact_history(run_id, name) returns ALL versions, not just run_id's."""
        manager.save_artifact("run-a", "prd", {"v": 1})
        manager.save_artifact("run-b", "prd", {"v": 2})
        manager.save_artifact("run-c", "prd", {"v": 3})
        # Calling with run-a's id should still return all 3 versions
        history = manager.get_artifact_history("run-a", "prd")
        assert len(history) == 3


# ---------------------------------------------------------------------------
# compare_artifacts: must use LATEST version per run
# ---------------------------------------------------------------------------


class TestCompareArtifactsLatestVersion:
    """Verify compare_artifacts() uses the latest (not first) version from each run."""

    def test_run_a_with_3_versions_uses_v3(self, manager: ArtifactManager) -> None:
        """run_a saves prd 3 times → compare_artifacts must use v3 (latest), not v1."""
        # run-a iteratively saves prd (v1, v2, v3 — all by run-a)
        manager.save_artifact("run-a", "prd", {"gen": 1, "content": "first draft"})
        manager.save_artifact("run-a", "prd", {"gen": 2, "content": "second draft"})
        manager.save_artifact("run-a", "prd", {"gen": 3, "content": "FINAL VERSION"})
        # run-b saves once (v4)
        manager.save_artifact("run-b", "prd", {"gen": 4, "content": "reviewer copy"})

        diff = manager.compare_artifacts("run-a", "run-b", "prd")

        assert diff.version_a == 3, (
            f"compare_artifacts should use run-a's LATEST version (v3), "
            f"but got version_a={diff.version_a}. "
            "Bug: _find_version() was iterating forward (returning v1). "
            "Fix: iterate history in reverse so first match = latest version."
        )
        assert diff.version_b == 4

    def test_compare_uses_latest_content_not_stale_v1(
        self, manager: ArtifactManager
    ) -> None:
        """Diff content reflects latest version's data, not stale first version."""
        # run-a: v1 has 'old_key', v2 drops 'old_key' and adds 'new_key'
        manager.save_artifact("run-a", "prd", {"old_key": "stale", "shared": "x"})
        manager.save_artifact("run-a", "prd", {"new_key": "fresh", "shared": "x"})
        # run-b mirrors run-a v2 exactly
        manager.save_artifact("run-b", "prd", {"new_key": "fresh", "shared": "x"})

        diff = manager.compare_artifacts("run-a", "run-b", "prd")
        # v2 (run-a) == v3 (run-b) — no diff if latest version is used
        assert diff.added == [], (
            f"Expected no added keys (v2 == v3), got added={diff.added}. "
            "This indicates v1 was compared instead of the latest v2."
        )
        assert diff.removed == [], (
            f"Expected no removed keys, got removed={diff.removed}. "
            "This indicates v1 was compared instead of the latest v2."
        )
        assert diff.changed == []

    def test_both_runs_have_multiple_versions_latest_used(
        self, manager: ArtifactManager
    ) -> None:
        """Both run_a and run_b have multiple versions → each returns latest."""
        manager.save_artifact("run-a", "prd", {"v": 1})   # v1 (run-a)
        manager.save_artifact("run-b", "prd", {"v": 2})   # v2 (run-b)
        manager.save_artifact("run-a", "prd", {"v": 3})   # v3 (run-a latest)
        manager.save_artifact("run-b", "prd", {"v": 4})   # v4 (run-b latest)

        diff = manager.compare_artifacts("run-a", "run-b", "prd")
        assert diff.version_a == 3, f"run-a latest should be v3, got {diff.version_a}"
        assert diff.version_b == 4, f"run-b latest should be v4, got {diff.version_b}"
        # v3 = {"v": 3}, v4 = {"v": 4} → "v" key changed
        assert "v" in diff.changed

    def test_single_version_per_run_compare_unchanged(
        self, manager: ArtifactManager
    ) -> None:
        """When each run has exactly one version, compare_artifacts is unaffected."""
        manager.save_artifact("run-x", "prd", {"alpha": 1})
        manager.save_artifact("run-y", "prd", {"alpha": 1, "beta": 2})
        diff = manager.compare_artifacts("run-x", "run-y", "prd")
        assert diff.version_a == 1
        assert diff.version_b == 2
        assert "beta" in diff.added
        assert diff.removed == []
        assert diff.changed == []

    def test_compare_returns_artifact_diff_type(self, manager: ArtifactManager) -> None:
        """compare_artifacts returns ArtifactDiff with correct run/name metadata."""
        manager.save_artifact("run-p", "prd", {"a": 1})
        manager.save_artifact("run-q", "prd", {"a": 2})
        diff = manager.compare_artifacts("run-p", "run-q", "prd")
        assert isinstance(diff, ArtifactDiff)
        assert diff.name == "prd"
        assert diff.run_a == "run-p"
        assert diff.run_b == "run-q"


# ---------------------------------------------------------------------------
# fcntl.flock concurrency guard
# ---------------------------------------------------------------------------


class TestFcntlConcurrencyGuard:
    """Test fcntl.flock (or threading.Lock fallback) protects .index.json under parallel load."""

    def test_10_parallel_saves_no_corruption_no_data_loss(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """10 parallel save_artifact() calls → no corruption or data loss in index."""
        errors: list[Exception] = []
        results: list[ArtifactMetadata] = []
        result_lock = threading.Lock()

        def _save(i: int) -> None:
            try:
                meta = manager.save_artifact(
                    f"run-{i:03d}",
                    "shared-artifact",
                    {"thread_id": i, "payload": f"data-{i}"},
                )
                with result_lock:
                    results.append(meta)
            except Exception as exc:
                with result_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=_save, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Zero errors
        assert errors == [], f"Errors during 10 concurrent saves: {errors}"

        # All 10 saves completed and returned metadata
        assert len(results) == 10, f"Expected 10 results, got {len(results)}"

        # .index.json must be valid parseable JSON
        index_text = (artifacts_dir / ".index.json").read_text()
        index = json.loads(index_text)  # raises on corruption
        assert "shared-artifact" in index["artifacts"]

        # All 10 version numbers must be present and unique
        versions_dict = index["artifacts"]["shared-artifact"]["versions"]
        assert len(versions_dict) == 10, (
            f"Expected 10 version entries in .index.json, got {len(versions_dict)}. "
            f"Keys present: {sorted(versions_dict.keys())}"
        )
        version_keys = {int(k) for k in versions_dict.keys()}
        assert version_keys == set(range(1, 11)), (
            f"Version numbers must be 1–10 without gaps, got: {sorted(version_keys)}"
        )

    def test_10_parallel_saves_all_versioned_files_on_disk(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """10 parallel saves → all 10 v{N}.json files physically written to .versions/."""
        barrier = threading.Barrier(10)

        def _save(i: int) -> None:
            barrier.wait()  # synchronise all threads to start simultaneously
            manager.save_artifact(
                f"run-par{i}", "parallel-test", {"i": i}
            )

        threads = [threading.Thread(target=_save, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        versions_dir = artifacts_dir / ".versions" / "parallel-test"
        assert versions_dir.is_dir(), ".versions/parallel-test/ directory not created"
        version_files = sorted(versions_dir.glob("v*.json"))
        assert len(version_files) == 10, (
            f"Expected 10 versioned files on disk, found {len(version_files)}: "
            f"{[f.name for f in version_files]}"
        )

    def test_index_json_never_corrupt_during_concurrent_writes(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """A background thread continuously reads .index.json — must never see corrupt JSON."""
        done = threading.Event()
        parse_errors: list[str] = []

        def _save_loop(i: int) -> None:
            for j in range(3):
                manager.save_artifact(
                    f"run-c{i}-{j}", f"artifact-{i}", {"i": i, "j": j}
                )

        def _monitor() -> None:
            import time
            while not done.is_set():
                idx_path = artifacts_dir / ".index.json"
                if idx_path.exists():
                    try:
                        json.loads(idx_path.read_text())
                    except json.JSONDecodeError as exc:
                        parse_errors.append(str(exc))
                time.sleep(0.001)

        writers = [threading.Thread(target=_save_loop, args=(i,)) for i in range(10)]
        monitor = threading.Thread(target=_monitor, daemon=True)
        monitor.start()
        for t in writers:
            t.start()
        for t in writers:
            t.join()
        done.set()

        assert parse_errors == [], (
            f".index.json was corrupt during concurrent writes: {parse_errors[:3]}"
        )

    def test_fcntl_guard_importable_cross_platform(self) -> None:
        """Module imports without error regardless of fcntl availability."""
        import orchestrator.artifact_manager as am
        # If fcntl is not available, HAS_FCNTL must be False (no ImportError raised)
        assert hasattr(am, "HAS_FCNTL"), (
            "artifact_manager must export HAS_FCNTL for platform-detection verification"
        )
        # HAS_FCNTL must be a bool
        assert isinstance(am.HAS_FCNTL, bool)

    def test_locked_index_context_manager_present(
        self, manager: ArtifactManager
    ) -> None:
        """_locked_index context manager exists and acquires/releases lock correctly."""
        assert hasattr(manager, "_locked_index"), "_locked_index must exist on ArtifactManager"
        # Exercise the context manager round-trip (no exception = lock acquired+released)
        with manager._locked_index() as index:
            assert isinstance(index, dict)
            assert "artifacts" in index


# ---------------------------------------------------------------------------
# AC-009: Retention policy — age limit + count limit
# ---------------------------------------------------------------------------


class TestAC009RetentionPolicy:
    """AC-009: apply_retention_policy() removes old runs by age and count."""

    def test_max_runs_limit_prunes_oldest_versions(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-009: max_runs=2 with 5 runs → oldest 3 versions deleted."""
        for i in range(1, 6):
            manager.save_artifact(f"run-{i:02d}", "prd", {"run": i})

        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=2, keep_failed=False, dry_run=False
        )

        assert result.deleted_versions >= 3, (
            f"Expected ≥3 deletions (5 runs − max_runs=2), got {result.deleted_versions}"
        )
        assert isinstance(result, RetentionResult)

    def test_max_runs_current_file_always_survives(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-009: Retention never deletes artifacts/prd.json (current version file)."""
        for i in range(1, 6):
            manager.save_artifact(f"run-{i:02d}", "prd", {"run": i})

        manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=False, dry_run=False
        )

        assert (artifacts_dir / "prd.json").exists(), (
            "prd.json (current version) must NEVER be deleted by retention policy"
        )
        current = json.loads((artifacts_dir / "prd.json").read_text())
        assert current == {"run": 5}, "Current file must contain the latest (run 5) data"

    def test_max_age_removes_versions_older_than_cutoff(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-009: max_age_days removes versioned copies older than the cutoff."""
        manager.save_artifact("run-old", "prd", {"era": "ancient"})
        manager.save_artifact("run-new", "prd", {"era": "recent"})

        # Simulate retention running 100 days in the future → both saves are >50 days old
        future_now = datetime.now(timezone.utc) + timedelta(days=100)
        with patch("orchestrator.artifact_manager.datetime") as mock_dt:
            mock_dt.now.return_value = future_now
            mock_dt.fromisoformat = datetime.fromisoformat
            result = manager.apply_retention_policy(
                max_age_days=50, max_runs=0, keep_failed=False, dry_run=False
            )

        assert result.deleted_versions >= 1, (
            f"Expected ≥1 age-based deletion (>50 days), got {result.deleted_versions}"
        )
        assert not (artifacts_dir / ".versions" / "prd" / "v1.json").exists(), (
            "run-old v1 must be deleted — it exceeds max_age_days=50"
        )

    def test_dry_run_reports_without_deleting(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-009: dry_run=True reports candidates but leaves files untouched."""
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})
        manager.save_artifact("run-3", "prd", {"v": 3})

        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=False, dry_run=True
        )

        assert result.dry_run is True
        assert result.deleted_versions >= 2, (
            f"Dry run should report ≥2 candidates (max_runs=1 out of 3), "
            f"got {result.deleted_versions}"
        )
        # Files must be untouched
        assert (artifacts_dir / ".versions" / "prd" / "v1.json").exists(), (
            "dry_run=True must not delete any files"
        )
        assert (artifacts_dir / ".versions" / "prd" / "v2.json").exists()

    def test_no_limits_deletes_nothing(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-009: max_age_days=0 and max_runs=0 → zero deletions."""
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})

        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=0, keep_failed=False, dry_run=False
        )

        assert result.deleted_versions == 0
        assert (artifacts_dir / ".versions" / "prd" / "v1.json").exists()
        assert (artifacts_dir / ".versions" / "prd" / "v2.json").exists()

    def test_retention_applies_across_multiple_artifacts(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-009: Retention spans all artifact types, not just the first in index."""
        for run_i in range(1, 4):
            for artifact in ["prd", "arch", "tasks"]:
                manager.save_artifact(f"run-{run_i}", artifact, {"run": run_i})

        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=False, dry_run=False
        )

        # 3 artifacts × 2 old runs = 6 deleted versions
        assert result.deleted_versions >= 4, (
            f"Expected ≥4 deletions across 3 artifacts × 2 old runs, "
            f"got {result.deleted_versions}"
        )
        # Current files always survive
        for artifact in ["prd", "arch", "tasks"]:
            assert (artifacts_dir / f"{artifact}.json").exists(), (
                f"{artifact}.json (current) must survive retention"
            )

    def test_deleted_paths_list_contains_correct_files(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-009: deleted_paths lists paths to files that were actually deleted."""
        manager.save_artifact("run-1", "prd", {"v": 1})
        manager.save_artifact("run-2", "prd", {"v": 2})

        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=False, dry_run=False
        )

        assert len(result.deleted_paths) >= 1
        assert any("v1.json" in p for p in result.deleted_paths), (
            f"Expected v1.json in deleted_paths, got: {result.deleted_paths}"
        )


# ---------------------------------------------------------------------------
# Failed run preservation with keep_failed=True
# ---------------------------------------------------------------------------


class TestKeepFailedPreservation:
    """Failed runs are preserved when keep_failed=True (AC-009)."""

    def test_failed_run_preserved_with_keep_failed_true(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-009: keep_failed=True preserves versions from runs marked 'failed'."""
        manager.save_artifact("run-fail", "prd", {"v": 1, "status": "failed run"})
        manager.save_artifact("run-ok", "prd", {"v": 2, "status": "success"})
        manager.mark_run_status("run-fail", "failed")

        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=True, dry_run=False
        )

        assert (artifacts_dir / ".versions" / "prd" / "v1.json").exists(), (
            "AC-009: Failed run v1 must be preserved with keep_failed=True, "
            "even though it is the oldest run exceeding max_runs=1"
        )
        assert result.retained_versions >= 1, (
            "retained_versions should count the preserved failed version"
        )

    def test_failed_run_deleted_with_keep_failed_false(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """AC-009: keep_failed=False does NOT exempt failed runs from deletion."""
        manager.save_artifact("run-fail", "prd", {"v": 1})
        manager.save_artifact("run-ok", "prd", {"v": 2})
        manager.mark_run_status("run-fail", "failed")

        manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=False, dry_run=False
        )

        assert not (artifacts_dir / ".versions" / "prd" / "v1.json").exists(), (
            "Failed run v1 should be deleted when keep_failed=False"
        )

    def test_multiple_failed_runs_all_preserved(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """Multiple failed runs → all preserved when keep_failed=True."""
        for i in range(1, 5):
            manager.save_artifact(f"run-{i}", "prd", {"v": i})
        manager.mark_run_status("run-1", "failed")
        manager.mark_run_status("run-2", "failed")

        manager.apply_retention_policy(
            max_age_days=0, max_runs=2, keep_failed=True, dry_run=False
        )

        assert (artifacts_dir / ".versions" / "prd" / "v1.json").exists(), (
            "Failed run-1 must be preserved (keep_failed=True)"
        )
        assert (artifacts_dir / ".versions" / "prd" / "v2.json").exists(), (
            "Failed run-2 must be preserved (keep_failed=True)"
        )

    def test_mark_run_status_updates_all_artifacts_for_run(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """mark_run_status() updates run_status for ALL artifacts saved by that run."""
        for artifact in ["prd", "arch", "tasks"]:
            manager.save_artifact("run-x", artifact, {"v": 1})
        manager.mark_run_status("run-x", "failed")

        index = json.loads((artifacts_dir / ".index.json").read_text())
        for artifact in ["prd", "arch", "tasks"]:
            ver_data = index["artifacts"][artifact]["versions"]["1"]
            assert ver_data["run_status"] == "failed", (
                f"Artifact '{artifact}' v1 should have run_status='failed' "
                f"after mark_run_status('run-x', 'failed')"
            )

    def test_failed_run_not_in_deleted_paths_on_dry_run(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """keep_failed=True with dry_run=True: failed runs not listed in deleted_paths."""
        manager.save_artifact("run-fail", "prd", {"v": 1})
        manager.save_artifact("run-ok", "prd", {"v": 2})
        manager.mark_run_status("run-fail", "failed")

        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=True, dry_run=True
        )

        failed_in_deleted = any("v1.json" in p for p in result.deleted_paths)
        assert not failed_in_deleted, (
            "Failed run versions must NOT appear in deleted_paths when keep_failed=True; "
            f"got deleted_paths={result.deleted_paths}"
        )

    def test_mark_run_status_completed_allows_retention(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """Runs marked 'completed' are eligible for retention (not protected)."""
        manager.save_artifact("run-old", "prd", {"v": 1})
        manager.save_artifact("run-new", "prd", {"v": 2})
        manager.mark_run_status("run-old", "completed")

        result = manager.apply_retention_policy(
            max_age_days=0, max_runs=1, keep_failed=True, dry_run=False
        )

        assert result.deleted_versions >= 1, (
            "Completed runs are eligible for deletion by retention policy"
        )
        assert not (artifacts_dir / ".versions" / "prd" / "v1.json").exists(), (
            "Completed run-old v1 must be deleted — keep_failed only protects 'failed' status"
        )


# ---------------------------------------------------------------------------
# search_artifacts: by type, agent, and text content
# ---------------------------------------------------------------------------


class TestSearchArtifacts:
    """search_artifacts() filters correctly by artifact_type, agent, and text."""

    def test_search_by_artifact_type_returns_matching_only(
        self, manager: ArtifactManager
    ) -> None:
        """search by artifact_type='prd' returns only prd-schema artifacts."""
        manager.save_artifact("run-1", "prd", {"title": "My PRD"}, schema_name="prd")
        manager.save_artifact("run-1", "arch", {"x": 1}, schema_name="architecture")
        manager.save_artifact("run-1", "tasks", {"x": 2}, schema_name="tasks")

        results = manager.search_artifacts("", artifact_type="prd")

        assert len(results) == 1, f"Expected 1 result for artifact_type='prd', got {len(results)}"
        assert results[0].schema_name == "prd"
        assert results[0].name == "prd"

    def test_search_by_type_no_false_positives(self, manager: ArtifactManager) -> None:
        """search by artifact_type='architecture' does not return prd or tasks."""
        manager.save_artifact("run-1", "prd", {"x": 1}, schema_name="prd")
        manager.save_artifact("run-1", "arch", {"x": 2}, schema_name="architecture")

        results = manager.search_artifacts("", artifact_type="architecture")
        names = {r.name for r in results}
        assert "prd" not in names
        assert "arch" in names

    def test_search_by_agent_returns_matching_artifacts(
        self, manager: ArtifactManager
    ) -> None:
        """search by agent='pm' returns only artifacts saved by 'pm'."""
        manager.save_artifact("run-1", "prd", {"x": 1}, agent="pm")
        manager.save_artifact("run-1", "arch", {"x": 2}, agent="architect")
        manager.save_artifact("run-1", "tasks", {"x": 3}, agent="pm")

        results = manager.search_artifacts("", agent="pm")
        names = {r.name for r in results}
        assert names == {"prd", "tasks"}, f"Expected {{prd, tasks}}, got {names}"

    def test_search_by_agent_no_false_positives(self, manager: ArtifactManager) -> None:
        """search by agent='pm' excludes artifacts from other agents."""
        manager.save_artifact("run-1", "prd", {"x": 1}, agent="pm")
        manager.save_artifact("run-1", "arch", {"x": 2}, agent="architect")

        results = manager.search_artifacts("", agent="pm")
        assert all(r.agent == "pm" for r in results)

    def test_search_by_text_content(self, manager: ArtifactManager) -> None:
        """Text search finds artifacts whose JSON content contains the query."""
        manager.save_artifact("run-1", "prd", {"needle": "findme_unique_xyz"})
        manager.save_artifact("run-1", "arch", {"haystack": "nothing_here"})

        results = manager.search_artifacts("findme_unique_xyz")
        assert len(results) == 1
        assert results[0].name == "prd"

    def test_search_text_is_case_insensitive(self, manager: ArtifactManager) -> None:
        """Text search is case-insensitive."""
        manager.save_artifact("run-1", "prd", {"content": "UPPERCASE_VALUE_ABC"})

        results = manager.search_artifacts("uppercase_value_abc")
        assert len(results) == 1, "Case-insensitive text search must match"

    def test_search_by_type_and_agent_combined_and_filter(
        self, manager: ArtifactManager
    ) -> None:
        """artifact_type AND agent filters are applied together (AND logic)."""
        manager.save_artifact("run-1", "prd", {"x": 1}, agent="pm", schema_name="prd")
        manager.save_artifact("run-1", "arch", {"x": 1}, agent="pm", schema_name="architecture")
        manager.save_artifact("run-1", "tasks", {"x": 1}, agent="eng", schema_name="tasks")

        results = manager.search_artifacts("", artifact_type="prd", agent="pm")
        assert len(results) == 1
        assert results[0].name == "prd"

    def test_search_empty_query_returns_all(self, manager: ArtifactManager) -> None:
        """Empty query string returns all indexed artifacts."""
        for name in ["prd", "arch", "tasks", "review"]:
            manager.save_artifact("run-1", name, {"x": 1})

        results = manager.search_artifacts("")
        assert len(results) == 4

    def test_search_no_match_returns_empty_list(self, manager: ArtifactManager) -> None:
        """No-match search returns [] (not None or error)."""
        manager.save_artifact("run-1", "prd", {"x": 1})

        results = manager.search_artifacts("", artifact_type="nonexistent_schema_type")
        assert results == []

    def test_search_returns_artifact_metadata_instances(
        self, manager: ArtifactManager
    ) -> None:
        """search_artifacts returns list[ArtifactMetadata]."""
        manager.save_artifact("run-1", "prd", {"x": 1}, schema_name="prd")

        results = manager.search_artifacts("", artifact_type="prd")
        assert all(isinstance(r, ArtifactMetadata) for r in results)

    def test_search_uses_artifact_name_as_type_when_schema_name_is_none(
        self, manager: ArtifactManager
    ) -> None:
        """When schema_name is None, the artifact name serves as the type fallback."""
        # Save without schema_name — 'prd' name used as type
        manager.save_artifact("run-1", "prd", {"title": "x"})

        results = manager.search_artifacts("", artifact_type="prd")
        assert len(results) == 1, (
            "search_artifacts must fall back to artifact name when schema_name is None"
        )

    def test_search_by_agent_returns_metadata_with_correct_agent(
        self, manager: ArtifactManager
    ) -> None:
        """search by agent returns metadata with agent field populated correctly."""
        manager.save_artifact("run-1", "prd", {"x": 1}, agent="qa-engineer")

        results = manager.search_artifacts("", agent="qa-engineer")
        assert len(results) == 1
        assert results[0].agent == "qa-engineer"


# ---------------------------------------------------------------------------
# Data round-trip integrity
# ---------------------------------------------------------------------------


class TestDataRoundTripIntegrity:
    """Verify data survives the full save → versioned copy → load round-trip."""

    def test_unicode_and_emoji_content_preserved(self, manager: ArtifactManager) -> None:
        """Unicode/emoji data survives save/load through versioned storage."""
        data = {"emoji": "🚀🎯✅", "unicode": "日本語テスト", "special": "café résumé"}
        manager.save_artifact("run-1", "prd", data)
        loaded = manager.load_artifact("run-1", "prd")
        assert loaded == data

    def test_specific_version_load_returns_correct_snapshot(
        self, manager: ArtifactManager
    ) -> None:
        """load_artifact(version=N) returns the exact data saved at that version."""
        for i in range(1, 4):
            manager.save_artifact(f"run-{i}", "prd", {"iteration": i, "era": f"v{i}"})

        for i in range(1, 4):
            loaded = manager.load_artifact(f"run-{i}", "prd", version=i)
            assert loaded is not None, f"v{i} should exist"
            assert loaded["iteration"] == i, f"v{i} must contain iteration={i}"
            assert loaded["era"] == f"v{i}"

    def test_current_file_always_holds_latest_version(
        self, manager: ArtifactManager, artifacts_dir: Path
    ) -> None:
        """artifacts/prd.json always holds the content of the most recent save."""
        for i in range(1, 6):
            manager.save_artifact(f"run-{i}", "prd", {"iteration": i})

        current = json.loads((artifacts_dir / "prd.json").read_text())
        assert current["iteration"] == 5, (
            "artifacts/prd.json must always reflect the latest (5th) save"
        )

    def test_large_artifact_no_truncation(self, manager: ArtifactManager) -> None:
        """Large artifact (>100 KB) survives the round-trip without truncation."""
        large_data = {"items": [{"id": i, "value": "x" * 100} for i in range(1000)]}
        manager.save_artifact("run-1", "prd", large_data)
        loaded = manager.load_artifact("run-1", "prd")
        assert loaded == large_data
        assert len(loaded["items"]) == 1000
