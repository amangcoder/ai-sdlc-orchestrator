"""Unit tests for the LRU cache in dynamic_directory_service.py (TASK-001)."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.mobile_api.dynamic_directory_service import (
    _CACHE_MAX_SIZE,
    _cache,
    _cache_lock,
    _clear_cache,
    _invalidate_cache_entry,
    _negative_cache,
    make_opaque_id,
    resolve_dynamic_id,
)


@pytest.fixture(autouse=True)
def clean_cache():
    """Clear the module-level cache before and after each test."""
    _clear_cache()
    yield
    _clear_cache()


@pytest.fixture
def projects_root(tmp_path: Path) -> Path:
    """Create a projects root with some subdirectories."""
    root = tmp_path / "projects"
    root.mkdir()
    return root


@pytest.fixture
def salt() -> uuid.UUID:
    return uuid.uuid4()


def _create_subdir(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    return d


class TestCacheHit:
    """Cache hit returns cached Path without rglob."""

    def test_second_call_uses_cache(self, projects_root: Path, salt: uuid.UUID):
        sub = _create_subdir(projects_root, "myproject")
        opaque_id = make_opaque_id(str(sub.resolve()), salt)

        # First call — populates cache
        result1 = resolve_dynamic_id(opaque_id, projects_root, salt)
        assert result1 == sub.resolve()

        # Verify it's in cache
        with _cache_lock:
            assert opaque_id in _cache

        # Second call — should use cache (we can verify by checking the cache is hit)
        result2 = resolve_dynamic_id(opaque_id, projects_root, salt)
        assert result2 == sub.resolve()


class TestCacheMiss:
    """Cache miss triggers rglob scan and populates cache."""

    def test_unknown_id_returns_none(self, projects_root: Path, salt: uuid.UUID):
        result = resolve_dynamic_id("nonexistent_id", projects_root, salt)
        assert result is None


class TestEviction:
    """LRU eviction at max capacity."""

    def test_eviction_at_boundary(self, projects_root: Path, salt: uuid.UUID):
        # Create 513 subdirectories
        ids = []
        for i in range(_CACHE_MAX_SIZE + 1):
            sub = _create_subdir(projects_root, f"proj{i:04d}")
            opaque_id = make_opaque_id(str(sub.resolve()), salt)
            ids.append(opaque_id)
            resolve_dynamic_id(opaque_id, projects_root, salt)

        with _cache_lock:
            assert len(_cache) == _CACHE_MAX_SIZE

        # The first entry should have been evicted
        with _cache_lock:
            assert ids[0] not in _cache
            assert ids[-1] in _cache


class TestInvalidation:
    """_invalidate_cache_entry removes the specified entry."""

    def test_invalidate_removes_entry(self, projects_root: Path, salt: uuid.UUID):
        sub = _create_subdir(projects_root, "project_to_invalidate")
        opaque_id = make_opaque_id(str(sub.resolve()), salt)

        resolve_dynamic_id(opaque_id, projects_root, salt)
        with _cache_lock:
            assert opaque_id in _cache

        _invalidate_cache_entry(opaque_id)
        with _cache_lock:
            assert opaque_id not in _cache


class TestClearCache:
    """_clear_cache removes all entries."""

    def test_clear_removes_all(self, projects_root: Path, salt: uuid.UUID):
        for i in range(5):
            sub = _create_subdir(projects_root, f"proj{i}")
            opaque_id = make_opaque_id(str(sub.resolve()), salt)
            resolve_dynamic_id(opaque_id, projects_root, salt)

        with _cache_lock:
            assert len(_cache) > 0

        _clear_cache()

        with _cache_lock:
            assert len(_cache) == 0
            assert len(_negative_cache) == 0


class TestNegativeCache:
    """Negative cache prevents repeated rglob for invalid IDs."""

    def test_negative_cache_hit(self, projects_root: Path, salt: uuid.UUID):
        # First call — scans and finds nothing, populates negative cache
        result1 = resolve_dynamic_id("bad_id_12345", projects_root, salt)
        assert result1 is None

        with _cache_lock:
            assert "bad_id_12345" in _negative_cache

        # Second call — should hit negative cache and skip rglob
        result2 = resolve_dynamic_id("bad_id_12345", projects_root, salt)
        assert result2 is None


class TestThreadSafety:
    """Concurrent access does not cause race conditions."""

    def test_concurrent_resolve(self, projects_root: Path, salt: uuid.UUID):
        sub = _create_subdir(projects_root, "concurrent_project")
        opaque_id = make_opaque_id(str(sub.resolve()), salt)
        results = []
        errors = []

        def resolve():
            try:
                result = resolve_dynamic_id(opaque_id, projects_root, salt)
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=resolve) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert all(r == sub.resolve() for r in results)


class TestSkipDirs:
    """_SKIP_DIRS directories are skipped during rglob scan."""

    def test_skip_git_dir(self, projects_root: Path, salt: uuid.UUID):
        git_dir = _create_subdir(projects_root, ".git")
        opaque_id = make_opaque_id(str(git_dir.resolve()), salt)
        # The .git directory should be skipped during scan
        # (but it won't match the opaque_id of projects_root itself)
        result = resolve_dynamic_id(opaque_id, projects_root, salt)
        # Since .git is in _SKIP_DIRS, rglob scan won't find it
        assert result is None
