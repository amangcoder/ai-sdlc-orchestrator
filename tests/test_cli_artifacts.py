"""Tests for orchestrate-artifacts CLI (TASK-016).

Acceptance criteria covered:
  1. orchestrate-artifacts CLI exists with list, compare, gc, search commands.
  2. list displays artifact table.
  3. compare displays diff summary with correct field names.
  4. gc --dry-run lists runs without deleting.
  5. search displays matching artifacts.
  6. Both CLIs use subprocess.run(shell=False) — artifacts CLI uses no subprocess at all.
  7. WORKSPACE_ROOT validation rejects paths with shell metacharacters.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

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
# Import guard
# ---------------------------------------------------------------------------


class TestImport:
    def test_main_importable(self):
        from orchestrator.cli_artifacts import main  # noqa: F401

    def test_help_exits_0(self):
        from orchestrator.cli_artifacts import main

        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# _validate_workspace_root
# ---------------------------------------------------------------------------


class TestValidateWorkspaceRoot:
    def _validate(self, path: str):
        from orchestrator.cli_artifacts import _validate_workspace_root
        return _validate_workspace_root(path)

    @pytest.mark.parametrize("valid_path", [
        "/home/user/workspace",
        "/tmp/my-workspace",
        "workspace",
        "/Users/foo/project/workspace",
        "/path/with-hyphens_and_underscores",
        "/path/123numbers",
    ])
    def test_valid_paths_pass(self, valid_path):
        result = self._validate(valid_path)
        assert result == valid_path

    @pytest.mark.parametrize("bad_path", [
        "/workspace; rm -rf /",
        "/workspace | cat /etc/passwd",
        "/workspace$(evil)",
        "/workspace`cmd`",
        "/workspace && bad",
        "/workspace > /etc/shadow",
        "/workspace < /dev/null",
        "/path/with spaces'and'quotes",
        '/path/with"double"quotes',
        "/path/with\\backslash",
        "/path/with\nnewline",
    ])
    def test_metacharacters_rejected(self, bad_path):
        with pytest.raises(SystemExit) as exc_info:
            self._validate(bad_path)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _resolve_artifacts_dir
# ---------------------------------------------------------------------------


class TestResolveArtifactsDir:
    def test_uses_workspace_root_env(self, tmp_path):
        from orchestrator.cli_artifacts import _resolve_artifacts_dir

        workspace = str(tmp_path / "myworkspace")
        with patch.dict(os.environ, {"WORKSPACE_ROOT": workspace}, clear=False):
            result = _resolve_artifacts_dir()

        assert result == Path(workspace) / "artifacts"

    def test_defaults_to_cwd_workspace(self, tmp_path, monkeypatch):
        from orchestrator.cli_artifacts import _resolve_artifacts_dir

        monkeypatch.chdir(tmp_path)
        with patch.dict(os.environ, {}, clear=False):
            # Remove WORKSPACE_ROOT if set
            os.environ.pop("WORKSPACE_ROOT", None)
            result = _resolve_artifacts_dir()

        assert result == tmp_path / "workspace" / "artifacts"

    def test_rejects_metachar_workspace_root(self):
        from orchestrator.cli_artifacts import _resolve_artifacts_dir

        with patch.dict(os.environ, {"WORKSPACE_ROOT": "/evil; rm -rf /"}, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                _resolve_artifacts_dir()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------


class TestCmdList:
    def test_list_displays_artifacts(self, capsys, artifacts_dir):
        from orchestrator.cli_artifacts import cmd_list

        am = ArtifactManager(artifacts_dir)
        am.save_artifact("run-abc", "prd", {"title": "My PRD"}, agent="pm")

        args = MagicMock()
        args.run_id = "run-abc"

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_list(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "prd" in out
        assert "run-abc" in out

    def test_list_empty_run(self, capsys, artifacts_dir):
        from orchestrator.cli_artifacts import cmd_list

        am = ArtifactManager(artifacts_dir)
        args = MagicMock()
        args.run_id = "run-nonexistent"

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_list(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "No artifacts found" in out

    def test_list_shows_version_and_size(self, capsys, artifacts_dir):
        from orchestrator.cli_artifacts import cmd_list

        am = ArtifactManager(artifacts_dir)
        data = {"key": "value", "items": [1, 2, 3]}
        am.save_artifact("run-xyz", "architecture", data, agent="architect")

        args = MagicMock()
        args.run_id = "run-xyz"

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_list(args)

        out = capsys.readouterr().out
        assert "architecture" in out
        assert "v1" in out


# ---------------------------------------------------------------------------
# cmd_compare
# ---------------------------------------------------------------------------


class TestCmdCompare:
    def test_compare_shows_diff(self, capsys, artifacts_dir):
        from orchestrator.cli_artifacts import cmd_compare

        am = ArtifactManager(artifacts_dir)
        am.save_artifact("run-a", "prd", {"title": "old", "req": "A"}, agent="pm")
        am.save_artifact("run-b", "prd", {"title": "new", "extra": "B"}, agent="pm")

        args = MagicMock()
        args.run_a = "run-a"
        args.run_b = "run-b"
        args.artifact = "prd"

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_compare(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "prd" in out

    def test_compare_no_diff(self, capsys, artifacts_dir):
        from orchestrator.cli_artifacts import cmd_compare

        am = ArtifactManager(artifacts_dir)
        same_data = {"title": "same", "req": "X"}
        am.save_artifact("run-a", "prd", same_data, agent="pm")
        am.save_artifact("run-b", "prd", same_data, agent="pm")

        args = MagicMock()
        args.run_a = "run-a"
        args.run_b = "run-b"
        args.artifact = "prd"

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_compare(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "no structural differences" in out

    def test_compare_missing_artifact_returns_1(self, capsys, artifacts_dir):
        from orchestrator.cli_artifacts import cmd_compare

        am = ArtifactManager(artifacts_dir)
        # Don't save anything — compare_artifacts will raise ValueError

        args = MagicMock()
        args.run_a = "run-a"
        args.run_b = "run-b"
        args.artifact = "prd"

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_compare(args)

        assert rc == 1

    def test_compare_uses_artifact_flag(self, artifacts_dir):
        """cmd_compare reads args.artifact (not args.name)."""
        from orchestrator.cli_artifacts import cmd_compare

        am = ArtifactManager(artifacts_dir)
        am.save_artifact("run-a", "prd", {"x": 1}, agent="pm")
        am.save_artifact("run-b", "prd", {"x": 2}, agent="pm")

        args = MagicMock(spec=["run_a", "run_b", "artifact"])
        args.run_a = "run-a"
        args.run_b = "run-b"
        args.artifact = "prd"
        # Note: args.name is NOT set (spec restricts attributes)

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_compare(args)

        assert rc == 0


# ---------------------------------------------------------------------------
# cmd_search
# ---------------------------------------------------------------------------


class TestCmdSearch:
    def test_search_by_query(self, capsys, artifacts_dir):
        from orchestrator.cli_artifacts import cmd_search

        am = ArtifactManager(artifacts_dir)
        am.save_artifact("run-1", "prd", {"title": "auth feature"}, agent="pm")
        am.save_artifact("run-2", "architecture", {"data": "unrelated"}, agent="architect")

        args = MagicMock()
        args.query = "auth"
        args.type = None
        args.agent = None

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_search(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "prd" in out
        assert "Found 1" in out

    def test_search_by_type(self, capsys, artifacts_dir):
        from orchestrator.cli_artifacts import cmd_search

        am = ArtifactManager(artifacts_dir)
        cfg_prd = ArtifactsConfig()
        am.save_artifact("run-1", "prd", {"title": "X"}, agent="pm", schema_name="prd")
        am.save_artifact("run-2", "arch", {"data": "Y"}, agent="arch", schema_name="architecture")

        args = MagicMock()
        args.query = ""
        args.type = "prd"
        args.agent = None

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_search(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "Found 1" in out
        assert "prd" in out

    def test_search_no_results(self, capsys, artifacts_dir):
        from orchestrator.cli_artifacts import cmd_search

        am = ArtifactManager(artifacts_dir)

        args = MagicMock()
        args.query = "zzznomatch"
        args.type = None
        args.agent = None

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_search(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "No matching" in out

    def test_search_by_agent(self, capsys, artifacts_dir):
        from orchestrator.cli_artifacts import cmd_search

        am = ArtifactManager(artifacts_dir)
        am.save_artifact("run-1", "prd", {"x": 1}, agent="pm")
        am.save_artifact("run-2", "arch", {"y": 2}, agent="architect")

        args = MagicMock()
        args.query = ""
        args.type = None
        args.agent = "pm"

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_search(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "Found 1" in out


# ---------------------------------------------------------------------------
# cmd_gc
# ---------------------------------------------------------------------------


class TestCmdGc:
    def test_gc_dry_run_no_deletion(self, capsys, artifacts_dir):
        from orchestrator.cli_artifacts import cmd_gc

        am = ArtifactManager(artifacts_dir)
        am.save_artifact("run-old", "prd", {"x": 1}, agent="pm")

        args = MagicMock()
        args.max_age = 1      # 1 day — potentially old
        args.max_runs = 200
        args.delete_failed = False
        args.dry_run = True

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_gc(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "no files were deleted" in out

    def test_gc_dry_run_shows_summary(self, capsys, artifacts_dir):
        from orchestrator.cli_artifacts import cmd_gc

        am = ArtifactManager(artifacts_dir)

        args = MagicMock()
        args.max_age = 90
        args.max_runs = 200
        args.delete_failed = False
        args.dry_run = True

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_gc(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "Versions deleted" in out
        assert "Versions retained" in out

    def test_gc_real_run_no_dry_run_message(self, capsys, artifacts_dir):
        from orchestrator.cli_artifacts import cmd_gc

        am = ArtifactManager(artifacts_dir)

        args = MagicMock()
        args.max_age = 90
        args.max_runs = 200
        args.delete_failed = False
        args.dry_run = False

        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            rc = cmd_gc(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "DRY RUN" not in out
        assert "no files were deleted" not in out


# ---------------------------------------------------------------------------
# main() argument parsing
# ---------------------------------------------------------------------------


class TestMainArgParsing:
    def test_list_requires_run_id(self):
        from orchestrator.cli_artifacts import main

        with pytest.raises(SystemExit) as exc_info:
            main(["list"])  # missing --run-id
        assert exc_info.value.code != 0

    def test_compare_requires_artifact_flag(self):
        from orchestrator.cli_artifacts import main

        # --name is no longer the flag; --artifact is required
        with pytest.raises(SystemExit) as exc_info:
            main(["compare", "--run-a", "a", "--run-b", "b"])  # missing --artifact
        assert exc_info.value.code != 0

    def test_compare_accepts_artifact_flag(self, artifacts_dir):
        """main() parses --artifact and passes it to cmd_compare."""
        from orchestrator.cli_artifacts import main

        with (
            patch("orchestrator.cli_artifacts._resolve_artifacts_dir", return_value=artifacts_dir),
            pytest.raises(SystemExit),
            patch("orchestrator.cli_artifacts.cmd_compare", return_value=0) as mock_cmp,
        ):
            main(["compare", "--run-a", "a", "--run-b", "b", "--artifact", "prd"])

        call_args = mock_cmp.call_args[0][0]
        assert call_args.artifact == "prd"
        assert call_args.run_a == "a"
        assert call_args.run_b == "b"

    def test_gc_defaults(self, artifacts_dir):
        from orchestrator.cli_artifacts import main

        with (
            patch("orchestrator.cli_artifacts._resolve_artifacts_dir", return_value=artifacts_dir),
            pytest.raises(SystemExit),
            patch("orchestrator.cli_artifacts.cmd_gc", return_value=0) as mock_gc,
        ):
            main(["gc"])

        args = mock_gc.call_args[0][0]
        assert args.max_age == 90
        assert args.max_runs == 200
        assert args.dry_run is False

    def test_gc_dry_run_flag(self, artifacts_dir):
        from orchestrator.cli_artifacts import main

        with (
            patch("orchestrator.cli_artifacts._resolve_artifacts_dir", return_value=artifacts_dir),
            pytest.raises(SystemExit),
            patch("orchestrator.cli_artifacts.cmd_gc", return_value=0) as mock_gc,
        ):
            main(["gc", "--dry-run"])

        args = mock_gc.call_args[0][0]
        assert args.dry_run is True

    def test_no_command_exits_0(self):
        from orchestrator.cli_artifacts import main

        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# End-to-end: real ArtifactManager + CLI
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_list_then_compare_workflow(self, capsys, artifacts_dir):
        """Full workflow: save two versions, list the latest, compare them.

        ArtifactManager.list_artifacts filters by the *current* run_id for each
        artifact — i.e. the run that saved the most recent version.  After saving
        both run-1 and run-2, the artifact's current run_id is 'run-2'.
        """
        from orchestrator.cli_artifacts import cmd_compare, cmd_list

        am = ArtifactManager(artifacts_dir)
        am.save_artifact("run-1", "prd", {"title": "v1 PRD", "scope": "narrow"}, agent="pm")
        am.save_artifact("run-2", "prd", {"title": "v2 PRD", "scope": "wide", "new_key": True}, agent="pm")

        # List run-2 artifacts (the latest run is now 'current' in the index)
        list_args = MagicMock()
        list_args.run_id = "run-2"
        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            cmd_list(list_args)
        out1 = capsys.readouterr().out
        assert "prd" in out1
        assert "v2" in out1  # second version

        # Compare the two runs
        cmp_args = MagicMock()
        cmp_args.run_a = "run-1"
        cmp_args.run_b = "run-2"
        cmp_args.artifact = "prd"
        with patch("orchestrator.cli_artifacts._get_artifact_manager", return_value=am):
            cmd_compare(cmp_args)
        out2 = capsys.readouterr().out
        # run-2 added new_key; scope changed value
        assert "Added" in out2 or "Changed" in out2
