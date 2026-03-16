"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    (tmp_path / "artifacts").mkdir()
    return tmp_path


@pytest.fixture
def valid_prd_data() -> dict:
    return {
        "title": "Dark Mode Toggle",
        "overview": "Add a dark mode toggle to the settings page so users can switch themes.",
        "goals": ["Improve user experience", "Support accessibility preferences"],
        "requirements": [
            {"id": "REQ-001", "description": "Toggle switch in settings", "priority": "must"},
            {"id": "REQ-002", "description": "Persist preference", "priority": "should"},
        ],
        "constraints": ["Must not break existing light theme"],
        "acceptance_criteria": ["Toggle is visible in settings", "Theme persists across sessions"],
    }


@pytest.fixture
def valid_tasks_data() -> dict:
    return {
        "tasks": [
            {
                "task_id": "TASK-001",
                "title": "Add toggle component",
                "description": "Implement the dark mode toggle UI component in settings page",
                "assigned_role": "engineer",
                "dependencies": [],
                "acceptance_criteria": ["Toggle renders correctly", "Emits change event"],
                "files_to_modify": ["src/settings.py"],
                "estimated_complexity": "low",
            }
        ]
    }
