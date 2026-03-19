"""Unit tests for Pydantic models added in TASK-002."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.mobile_api.models import (
    PROMPT_FILE_PATTERN,
    RESPONSE_FILE_PATTERN,
    PendingPromptResponse,
    ProjectEntry,
    RespondRequest,
    RespondResponse,
    RunStartRequest,
    _RUN_ID_RE,
)


class TestProjectEntry:
    """ProjectEntry serializes all four fields with no 'path' field."""

    def test_serialize_all_fields(self):
        entry = ProjectEntry(
            id="abc123",
            name="my-project",
            last_modified="2024-01-15T10:00:00Z",
            run_count=5,
        )
        data = entry.model_dump()
        assert data == {
            "id": "abc123",
            "name": "my-project",
            "last_modified": "2024-01-15T10:00:00Z",
            "run_count": 5,
        }
        assert "path" not in data


class TestPendingPromptResponse:
    """PendingPromptResponse type is constrained to free_text or single_choice."""

    def test_free_text_type(self):
        prompt = PendingPromptResponse(
            prompt_id="abc",
            question="What is your name?",
            type="free_text",
            options=None,
            created_at="2024-01-15T10:00:00Z",
        )
        assert prompt.type == "free_text"
        assert prompt.options is None

    def test_single_choice_type(self):
        prompt = PendingPromptResponse(
            prompt_id="abc",
            question="Pick one",
            type="single_choice",
            options=["A", "B", "C"],
            created_at="2024-01-15T10:00:00Z",
        )
        assert prompt.type == "single_choice"
        assert prompt.options == ["A", "B", "C"]

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            PendingPromptResponse(
                prompt_id="abc",
                question="Pick one",
                type="invalid_type",  # type: ignore
                options=None,
                created_at="2024-01-15T10:00:00Z",
            )


class TestRespondRequest:
    """RespondRequest raises validation error for empty response."""

    def test_empty_response_rejected(self):
        with pytest.raises(ValidationError):
            RespondRequest(prompt_id="abc", response="")

    def test_valid_response(self):
        req = RespondRequest(prompt_id="abc", response="Yes, confirmed")
        assert req.response == "Yes, confirmed"


class TestRespondResponse:
    """RespondResponse serializes correctly."""

    def test_serialize(self):
        resp = RespondResponse(status="submitted", prompt_id="abc")
        data = resp.model_dump()
        assert data == {"status": "submitted", "prompt_id": "abc"}


class TestRunIdRegex:
    """_RUN_ID_RE validation."""

    def test_valid_uuid_hex(self):
        assert _RUN_ID_RE.match("aabb1122ccdd3344") is not None

    def test_valid_with_hyphens(self):
        assert _RUN_ID_RE.match("abc-def-123") is not None

    def test_valid_with_underscores(self):
        assert _RUN_ID_RE.match("abc_def_123") is not None

    def test_reject_path_traversal(self):
        assert _RUN_ID_RE.match("../etc/passwd") is None

    def test_reject_dots(self):
        assert _RUN_ID_RE.match("..") is None

    def test_reject_slashes(self):
        assert _RUN_ID_RE.match("foo/bar") is None

    def test_reject_empty(self):
        assert _RUN_ID_RE.match("") is None


class TestFilePatternConstants:
    """PROMPT_FILE_PATTERN and RESPONSE_FILE_PATTERN are importable."""

    def test_prompt_pattern(self):
        assert PROMPT_FILE_PATTERN == ".prompt-{run_id}.json"
        assert PROMPT_FILE_PATTERN.format(run_id="abc123") == ".prompt-abc123.json"

    def test_response_pattern(self):
        assert RESPONSE_FILE_PATTERN == ".response-{run_id}.json"
        assert RESPONSE_FILE_PATTERN.format(run_id="abc123") == ".response-abc123.json"


class TestRunStartRequestWorkspaceId:
    """RunStartRequest.workspace_id must be 32-char lowercase hex or None.

    Security: rejects arbitrary strings before any filesystem I/O,
    preventing DoS via rglob flood attacks on the DynamicDirectoryService.
    """

    def test_valid_32char_hex_accepted(self):
        req = RunStartRequest(
            feature_request="Add dark mode",
            workspace_id="aabbccddeeff00112233445566778899",
        )
        assert req.workspace_id == "aabbccddeeff00112233445566778899"

    def test_none_accepted(self):
        req = RunStartRequest(
            feature_request="Add dark mode",
            workspace_id=None,
        )
        assert req.workspace_id is None

    def test_uppercase_hex_rejected(self):
        """Pattern requires lowercase hex only ([a-f0-9]) per security design."""
        with pytest.raises(ValidationError):
            RunStartRequest(
                feature_request="Add dark mode",
                workspace_id="AABBCCDDEEFF00112233445566778899",
            )

    def test_too_short_rejected(self):
        with pytest.raises(ValidationError):
            RunStartRequest(
                feature_request="Add dark mode",
                workspace_id="aabb1122",  # Only 8 chars
            )

    def test_too_long_rejected(self):
        with pytest.raises(ValidationError):
            RunStartRequest(
                feature_request="Add dark mode",
                workspace_id="a" * 33,  # 33 chars (one too many)
            )

    def test_path_traversal_rejected(self):
        """Path traversal strings must be rejected."""
        with pytest.raises(ValidationError):
            RunStartRequest(
                feature_request="Add dark mode",
                workspace_id="../../../etc/passwd00000000000000",
            )

    def test_non_hex_chars_rejected(self):
        """Strings with non-hex characters (e.g. 'g'-'z') must be rejected."""
        with pytest.raises(ValidationError):
            RunStartRequest(
                feature_request="Add dark mode",
                workspace_id="zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
            )
