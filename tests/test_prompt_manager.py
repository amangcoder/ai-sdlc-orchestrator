"""Unit tests for prompt_manager.py (TASK-006)."""

from __future__ import annotations

import json
import time
import threading
from pathlib import Path

import pytest

from orchestrator.prompt_manager import (
    cleanup_prompt_files,
    poll_for_response,
    write_prompt,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


class TestWritePrompt:
    """write_prompt creates .prompt-{run_id}.json with all required fields."""

    def test_creates_prompt_file(self, workspace: Path):
        prompt_id = write_prompt(
            run_id="test123",
            workspace=workspace,
            question="Confirm tech stack?",
            prompt_type="single_choice",
            options=["Confirm", "Abort"],
        )

        prompt_file = workspace / ".prompt-test123.json"
        assert prompt_file.exists()

        data = json.loads(prompt_file.read_text())
        assert data["prompt_id"] == prompt_id
        assert data["question"] == "Confirm tech stack?"
        assert data["type"] == "single_choice"
        assert data["options"] == ["Confirm", "Abort"]
        assert "created_at" in data

    def test_free_text_prompt(self, workspace: Path):
        prompt_id = write_prompt(
            run_id="test456",
            workspace=workspace,
            question="What do you prefer?",
            prompt_type="free_text",
        )

        data = json.loads((workspace / ".prompt-test456.json").read_text())
        assert data["type"] == "free_text"
        assert data["options"] is None

    def test_atomic_write(self, workspace: Path):
        """Temp file should not remain after write."""
        write_prompt("test789", workspace, "Q?", "free_text")
        tmp_file = workspace / ".prompt-test789.json.tmp"
        assert not tmp_file.exists()


class TestPollForResponse:
    """poll_for_response returns response or None on timeout."""

    def test_returns_response_when_file_created(self, workspace: Path):
        prompt_id = write_prompt("run1", workspace, "Q?", "free_text")

        # Write response file in a separate thread after a short delay
        def write_response():
            time.sleep(0.5)
            response_file = workspace / ".response-run1.json"
            response_file.write_text(json.dumps({
                "prompt_id": prompt_id,
                "response": "Yes",
                "submitted_at": "2024-01-15T10:00:00Z",
            }))

        t = threading.Thread(target=write_response)
        t.start()

        result = poll_for_response(
            run_id="run1",
            workspace=workspace,
            prompt_id=prompt_id,
            timeout_seconds=5,
            poll_interval=0.2,
        )

        t.join()
        assert result == "Yes"

    def test_returns_none_on_timeout(self, workspace: Path):
        result = poll_for_response(
            run_id="run2",
            workspace=workspace,
            prompt_id="nonexistent",
            timeout_seconds=1,
            poll_interval=0.2,
        )
        assert result is None


class TestCleanupPromptFiles:
    """cleanup_prompt_files removes both files safely."""

    def test_cleanup_removes_files(self, workspace: Path):
        prompt_id = write_prompt("run3", workspace, "Q?", "free_text")
        response_file = workspace / ".response-run3.json"
        response_file.write_text(json.dumps({
            "prompt_id": prompt_id,
            "response": "answer",
        }))

        cleanup_prompt_files("run3", workspace)

        assert not (workspace / ".prompt-run3.json").exists()
        assert not (workspace / ".response-run3.json").exists()

    def test_cleanup_safe_when_no_files(self, workspace: Path):
        # Should not raise
        cleanup_prompt_files("nonexistent_run", workspace)
