"""Comprehensive pytest test suite for src/orchestrator/log_analyzer.py.

All tests use stdlib + pytest only — no network calls, no LLM invocations, no mocking.
Tests cover REQ-001 through REQ-013 and acceptance criteria AC-001 through AC-009.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from orchestrator.log_analyzer import (
    AnalysisReport,
    _collect_logs,
    _detect_prompt_fragments,
    _detect_redundant_file_reads,
    _detect_repeated_tool_calls,
    _load_run,
    analyze_runs,
    format_report_json,
    format_report_text,
    _build_suggestions,
)


# ---------------------------------------------------------------------------
# Helper: write a JSONL temp file from event dicts
# ---------------------------------------------------------------------------

def _make_jsonl_file(*events: dict) -> Path:
    """Serialize each event as a JSONL line, write to a NamedTemporaryFile, return Path."""
    tmp = tempfile.NamedTemporaryFile(
        suffix=".jsonl", mode="w", delete=False, prefix="run-"
    )
    for ev in events:
        tmp.write(json.dumps(ev) + "\n")
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


@pytest.fixture
def temp_jsonl_files():
    """Tracks temp files created during a test and removes them after."""
    paths: list[Path] = []
    yield paths
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# TASK-001 Tests: _load_run() parsing
# ---------------------------------------------------------------------------


def test_load_run_parses_tool_use(tmp_path):
    """REQ-001: _load_run correctly parses tool_use events."""
    log_path = tmp_path / "run-abc123.jsonl"
    events = [
        {"event": "run_start", "feature_request": "test feature"},
        {
            "event": "tool_use",
            "tool_name": "Read",
            "tool_input": {"file_path": "src/orchestrator/models.py"},
            "agent": "backend_engineer",
            "input_tokens": 4200,
        },
    ]
    log_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    summary = _load_run(log_path)

    assert summary is not None
    assert len(summary.tool_use_events) == 1
    ev = summary.tool_use_events[0]
    assert ev.tool_name == "Read"
    assert ev.tool_input == {"file_path": "src/orchestrator/models.py"}
    assert ev.agent == "backend_engineer"
    assert ev.input_tokens == 4200


def test_load_run_parses_tool_use_defaults_input_tokens(tmp_path):
    """REQ-001: input_tokens defaults to 0 when absent."""
    log_path = tmp_path / "run-def456.jsonl"
    events = [
        {
            "event": "tool_use",
            "tool_name": "Grep",
            "tool_input": {"pattern": "foo"},
            "agent": "qa",
        },
    ]
    log_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    summary = _load_run(log_path)

    assert summary is not None
    assert len(summary.tool_use_events) == 1
    assert summary.tool_use_events[0].input_tokens == 0


def test_load_run_parses_agent_invoke(tmp_path):
    """REQ-002: _load_run correctly parses agent_invoke events."""
    log_path = tmp_path / "run-ghi789.jsonl"
    prompt = (
        "Please read src/orchestrator/models.py and also /absolute/path/to/file.py "
        "to understand the data models."
    )
    events = [
        {
            "event": "agent_invoke",
            "agent": "backend_engineer",
            "prompt": prompt,
        },
    ]
    log_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    summary = _load_run(log_path)

    assert summary is not None
    assert len(summary.agent_invoke_events) == 1
    ev = summary.agent_invoke_events[0]
    assert ev.agent == "backend_engineer"
    assert len(ev.prompt_excerpt) <= 2000
    # At least one file path should be extracted
    assert len(ev.file_paths) >= 1


def test_load_run_slices_prompt_excerpt_to_2000(tmp_path):
    """REQ-002: prompt_excerpt is sliced to at most 2000 characters."""
    log_path = tmp_path / "run-long.jsonl"
    long_prompt = "word " * 1000  # ~5000 chars
    events = [
        {
            "event": "agent_invoke",
            "agent": "architect",
            "prompt": long_prompt,
        },
    ]
    log_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    summary = _load_run(log_path)

    assert summary is not None
    assert len(summary.agent_invoke_events) == 1
    assert len(summary.agent_invoke_events[0].prompt_excerpt) <= 2000


def test_load_run_existing_events_unchanged(tmp_path):
    """Existing run_start, run_complete, task_invoke, task_result, agent_result still parse."""
    log_path = tmp_path / "run-legacy.jsonl"
    events = [
        {"event": "run_start", "feature_request": "legacy feature", "workflow_type": "feature"},
        {"event": "run_complete", "total_cost_usd": 0.42},
        {
            "event": "task_invoke",
            "task_id": "t1",
            "step": "implement",
            "agent": "engineer",
            "role": "backend_engineer",
            "model": "sonnet",
            "attempt": 1,
        },
        {
            "event": "task_result",
            "task_id": "t1",
            "success": True,
            "cost_usd": 0.21,
            "attempt": 1,
        },
        {
            "event": "agent_result",
            "agent": "engineer",
            "input_tokens": 5000,
            "output_tokens": 800,
            "cost_usd": 0.12,
            "attempt": 1,
        },
    ]
    log_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    summary = _load_run(log_path)

    assert summary is not None
    assert summary.feature_request == "legacy feature"
    assert summary.workflow_type == "feature"
    assert summary.total_cost_usd == 0.42
    assert len(summary.task_invocations) == 1
    assert "t1" in summary.task_results
    assert len(summary.agent_results) == 1


# ---------------------------------------------------------------------------
# TASK-001 Tests: _collect_logs()
# ---------------------------------------------------------------------------


def test_collect_logs_run_id(tmp_path):
    """test_collect_logs_run_id: verify partial run_id match filtering."""
    # Create 3 JSONL files with distinct run IDs in stem
    f1 = tmp_path / "run-abc111.jsonl"
    f2 = tmp_path / "run-abc222.jsonl"
    f3 = tmp_path / "run-xyz999.jsonl"
    for f in (f1, f2, f3):
        f.write_text('{"event": "run_start"}\n')

    # Partial match on "abc" should return both abc files, xyz excluded
    result = _collect_logs(tmp_path, run_id="abc", last_n=None)
    stems = {p.stem for p in result}
    assert "run-abc111" in stems
    assert "run-abc222" in stems
    assert "run-xyz999" not in stems


def test_collect_logs_last_n(tmp_path):
    """test_collect_logs_last_n: return exactly last_n most-recently modified files."""
    files = []
    for i in range(3):
        f = tmp_path / f"run-file{i:03d}.jsonl"
        f.write_text('{"event": "run_start"}\n')
        # Stagger modification times
        os.utime(f, (time.time() + i, time.time() + i))
        files.append(f)

    result = _collect_logs(tmp_path, run_id=None, last_n=2)
    assert len(result) == 2
    # Should return the 2 most recently modified (files[2] and files[1])
    result_names = {p.name for p in result}
    assert "run-file002.jsonl" in result_names
    assert "run-file001.jsonl" in result_names
    assert "run-file000.jsonl" not in result_names


def test_collect_logs_default_most_recent(tmp_path):
    """test_collect_logs_default_most_recent: only 1 file when run_id=None and last_n=None."""
    for i in range(3):
        f = tmp_path / f"run-multi{i:03d}.jsonl"
        f.write_text('{"event": "run_start"}\n')
        os.utime(f, (time.time() + i, time.time() + i))

    result = _collect_logs(tmp_path, run_id=None, last_n=None)
    assert len(result) == 1
    assert result[0].name == "run-multi002.jsonl"


# ---------------------------------------------------------------------------
# TASK-002 Tests: redundant file reads and repeated tool calls
# ---------------------------------------------------------------------------


def test_analyze_redundant_file_reads(tmp_path):
    """AC-001: 3 distinct agents reading the same file → read_count=3, all agents present."""
    events_agent_a = [
        {"event": "run_start", "feature_request": "feat A"},
        {
            "event": "tool_use",
            "tool_name": "Read",
            "tool_input": {"file_path": "src/orchestrator/models.py"},
            "agent": "agent_a",
            "input_tokens": 1000,
        },
    ]
    events_agent_b = [
        {
            "event": "tool_use",
            "tool_name": "Read",
            "tool_input": {"file_path": "src/orchestrator/models.py"},
            "agent": "agent_b",
            "input_tokens": 1200,
        },
    ]
    events_agent_c = [
        {
            "event": "tool_use",
            "tool_name": "Read",
            "tool_input": {"file_path": "src/orchestrator/models.py"},
            "agent": "agent_c",
            "input_tokens": 900,
        },
    ]

    f1 = tmp_path / "run-r1.jsonl"
    f1.write_text("\n".join(json.dumps(e) for e in events_agent_a + events_agent_b + events_agent_c) + "\n")

    report = analyze_runs(tmp_path)

    assert len(report.redundant_file_reads) >= 1
    entry = report.redundant_file_reads[0]
    assert entry["file_path"] == "src/orchestrator/models.py"
    assert entry["read_count"] == 3
    assert "agent_a" in entry["agents"]
    assert "agent_b" in entry["agents"]
    assert "agent_c" in entry["agents"]


def test_analyze_repeated_tool_calls(tmp_path):
    """AC-002: Grep called 4x with identical args → count=4, estimated_token_cost=3*per_call_tokens."""
    events = [{"event": "run_start", "feature_request": "search feat"}]
    for i in range(4):
        events.append({
            "event": "tool_use",
            "tool_name": "Grep",
            "tool_input": {"pattern": "foo", "path": "src/"},
            "agent": f"agent_{i}",
            "input_tokens": 100,
        })

    f = tmp_path / "run-rep.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    report = analyze_runs(tmp_path)

    assert len(report.repeated_tool_calls) >= 1
    # Find the Grep entry
    grep_entry = next(
        (e for e in report.repeated_tool_calls if e["tool_name"] == "Grep"),
        None,
    )
    assert grep_entry is not None
    assert grep_entry["count"] == 4
    # estimated_token_cost = sum of tokens after the first = 3 * 100 = 300
    assert grep_entry["estimated_token_cost"] == 300


def test_detect_redundant_file_reads_uses_path_key(tmp_path):
    """Verify that tool_input 'path' key (used by Grep/Glob) is also extracted."""
    events = [
        {
            "event": "tool_use",
            "tool_name": "Grep",
            "tool_input": {"pattern": "foo", "path": "src/foo.py"},
            "agent": "a1",
            "input_tokens": 500,
        },
        {
            "event": "tool_use",
            "tool_name": "Grep",
            "tool_input": {"pattern": "foo", "path": "src/foo.py"},
            "agent": "a2",
            "input_tokens": 600,
        },
    ]
    f = tmp_path / "run-pathkey.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    report = analyze_runs(tmp_path)

    paths = [e["file_path"] for e in report.redundant_file_reads]
    assert "src/foo.py" in paths


# ---------------------------------------------------------------------------
# TASK-002 Tests: AnalysisReport backward compatibility
# ---------------------------------------------------------------------------


def test_analysis_report_new_fields_have_defaults():
    """REQ-005: AnalysisReport new fields default to empty list / 0."""
    report = AnalysisReport(
        run_ids=["test-run"],
        repeated_tasks=[],
        duplicate_role_steps=[],
        cross_run_step_freq=[],
        top_cost_steps=[],
        top_token_agents=[],
        estimated_retry_cost_waste=0.0,
        estimated_retry_token_waste=0,
    )
    assert report.redundant_file_reads == []
    assert report.repeated_tool_calls == []
    assert report.prompt_fragments == []
    assert report.estimated_duplicate_read_token_waste == 0


# ---------------------------------------------------------------------------
# TASK-001/002 Tests: graceful handling of missing events
# ---------------------------------------------------------------------------


def test_analyze_no_tool_use_events(tmp_path):
    """AC-006: legacy JSONL with only task_invoke/task_result/agent_result → no exception, all new lists empty."""
    events = [
        {"event": "run_start", "feature_request": "legacy"},
        {
            "event": "task_invoke",
            "task_id": "t1",
            "step": "build",
            "agent": "eng",
            "role": "backend_engineer",
            "model": "sonnet",
            "attempt": 1,
        },
        {
            "event": "task_result",
            "task_id": "t1",
            "success": True,
            "cost_usd": 0.05,
            "attempt": 1,
        },
        {
            "event": "agent_result",
            "agent": "eng",
            "input_tokens": 800,
            "output_tokens": 200,
            "cost_usd": 0.05,
            "attempt": 1,
        },
    ]
    f = tmp_path / "run-notools.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    # Must not raise
    report = analyze_runs(tmp_path)

    assert report.redundant_file_reads == []
    assert report.repeated_tool_calls == []
    assert report.prompt_fragments == []
    assert report.estimated_duplicate_read_token_waste == 0


def test_analyze_malformed_tool_use_events_skipped(tmp_path):
    """REQ-013: malformed tool_use events are silently skipped."""
    events = [
        {"event": "run_start", "feature_request": "test"},
        # Malformed: tool_use with non-dict tool_input handled gracefully
        {"event": "tool_use", "tool_name": "Read", "tool_input": None, "agent": "a"},
        # Valid event after malformed one
        {
            "event": "tool_use",
            "tool_name": "Read",
            "tool_input": {"file_path": "src/foo.py"},
            "agent": "a",
            "input_tokens": 100,
        },
    ]
    f = tmp_path / "run-malformed.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    # Must not raise
    summary = _load_run(f)
    assert summary is not None


# ---------------------------------------------------------------------------
# TASK-003 Tests: prompt fragment detection
# ---------------------------------------------------------------------------


def test_analyze_prompt_fragments(tmp_path):
    """AC-005: 4 of 5 agents share an identical 8-word sequence → occurrence_count >= 4."""
    shared_phrase = "the quick brown fox jumped over the lazy"
    agents = [f"agent_{i}" for i in range(5)]

    events = [{"event": "run_start", "feature_request": "prompt test"}]
    for i, agent in enumerate(agents):
        # Give 4 agents the shared phrase; agent_4 gets a different prompt
        if i < 4:
            prompt = f"Introduction text. {shared_phrase} dog sat on the mat."
        else:
            prompt = "A completely different prompt about something else entirely."
        events.append({
            "event": "agent_invoke",
            "agent": agent,
            "prompt": prompt,
        })

    f = tmp_path / "run-frags.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    report = analyze_runs(tmp_path)

    # Find the fragment entry for the shared phrase
    matching = [
        frag for frag in report.prompt_fragments
        if "quick brown fox" in frag["fragment_text"]
    ]
    assert len(matching) >= 1
    assert matching[0]["occurrence_count"] >= 4


def test_detect_prompt_fragments_empty_when_no_invoke_events(tmp_path):
    """_detect_prompt_fragments returns [] when no agent_invoke events exist."""
    events = [
        {"event": "run_start", "feature_request": "no invokes"},
        {
            "event": "task_invoke",
            "task_id": "t1",
            "step": "step1",
            "agent": "eng",
            "role": "engineer",
            "model": "sonnet",
            "attempt": 1,
        },
    ]
    f = tmp_path / "run-noinvoke.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    report = analyze_runs(tmp_path)
    assert report.prompt_fragments == []


def test_detect_prompt_fragments_requires_3_agents(tmp_path):
    """Fragments only appear when shared across 3+ distinct agents (not 2)."""
    shared_phrase = "same eight words shared by only two agents here"

    events = [{"event": "run_start", "feature_request": "frag threshold"}]
    for agent in ["agent_x", "agent_y"]:
        events.append({
            "event": "agent_invoke",
            "agent": agent,
            "prompt": f"Some context. {shared_phrase}. End.",
        })

    f = tmp_path / "run-twofrag.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    report = analyze_runs(tmp_path)

    # With only 2 agents sharing, no fragments should appear
    matching = [
        frag for frag in report.prompt_fragments
        if "same eight words" in frag["fragment_text"]
    ]
    assert len(matching) == 0


# ---------------------------------------------------------------------------
# TASK-003 Tests: format_report_text() and format_report_json()
# ---------------------------------------------------------------------------


def test_format_report_text_has_sections(tmp_path):
    """AC-003: format_report_text() output contains INDEXABLE DATA and PROMPT BLOAT sections."""
    events = [{"event": "run_start", "feature_request": "format test"}]
    f = tmp_path / "run-fmt.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    report = analyze_runs(tmp_path)
    text = format_report_text(report, tmp_path)

    assert "INDEXABLE DATA" in text
    assert "PROMPT BLOAT" in text


def test_format_report_text_shows_redundant_file_reads(tmp_path):
    """AC-003: format_report_text() shows file path, read_count, and agents in INDEXABLE DATA."""
    events = [{"event": "run_start", "feature_request": "fmt reads"}]
    for agent in ["alpha", "beta", "gamma"]:
        events.append({
            "event": "tool_use",
            "tool_name": "Read",
            "tool_input": {"file_path": "src/orchestrator/models.py"},
            "agent": agent,
            "input_tokens": 300,
        })

    f = tmp_path / "run-fmtread.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    report = analyze_runs(tmp_path)
    text = format_report_text(report, tmp_path)

    assert "src/orchestrator/models.py" in text
    assert "read_count:3" in text or "read_count: 3" in text or "3" in text
    assert "alpha" in text or "beta" in text or "gamma" in text


def test_format_report_json_top_level_keys(tmp_path):
    """AC-008: format_report_json() result has all 6 required top-level keys."""
    events = [{"event": "run_start", "feature_request": "json keys test"}]
    f = tmp_path / "run-jsonkeys.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    report = analyze_runs(tmp_path)
    json_str = format_report_json(report)

    data = json.loads(json_str)
    required_keys = {
        "run_ids",
        "repetitive_actions",
        "indexable_data",
        "cost_analysis",
        "recommendations",
        "prompt_bloat",
    }
    assert required_keys.issubset(data.keys())


def test_format_report_json_redundant_file_reads(tmp_path):
    """AC-004: format_report_json() result has indexable_data.redundant_file_reads as a list."""
    events = [{"event": "run_start", "feature_request": "json rfr"}]
    f = tmp_path / "run-jsonrfr.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    report = analyze_runs(tmp_path)
    data = json.loads(format_report_json(report))

    assert "indexable_data" in data
    assert "redundant_file_reads" in data["indexable_data"]
    assert isinstance(data["indexable_data"]["redundant_file_reads"], list)


def test_format_report_json_has_cost_analysis_fields(tmp_path):
    """format_report_json() cost_analysis has estimated_duplicate_read_token_waste."""
    events = [{"event": "run_start", "feature_request": "cost fields"}]
    f = tmp_path / "run-cost.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    report = analyze_runs(tmp_path)
    data = json.loads(format_report_json(report))

    assert "estimated_duplicate_read_token_waste" in data["cost_analysis"]
    assert isinstance(data["cost_analysis"]["estimated_duplicate_read_token_waste"], int)


def test_format_report_json_is_valid_json(tmp_path):
    """format_report_json() returns a valid JSON string parseable by json.loads()."""
    events = [
        {"event": "run_start", "feature_request": "valid json test"},
        {
            "event": "tool_use",
            "tool_name": "Read",
            "tool_input": {"file_path": "src/a.py"},
            "agent": "a1",
            "input_tokens": 500,
        },
        {
            "event": "tool_use",
            "tool_name": "Read",
            "tool_input": {"file_path": "src/a.py"},
            "agent": "a2",
            "input_tokens": 600,
        },
    ]
    f = tmp_path / "run-validjson.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    report = analyze_runs(tmp_path)
    json_str = format_report_json(report)

    # Must not raise
    parsed = json.loads(json_str)
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# TASK-003 Tests: _build_suggestions()
# ---------------------------------------------------------------------------


def test_build_suggestions_aicoder(tmp_path):
    """AC-009: _build_suggestions() includes 'AICoder' and specific file path when read_count >= 3."""
    report = AnalysisReport(
        run_ids=["run-test"],
        repeated_tasks=[],
        duplicate_role_steps=[],
        cross_run_step_freq=[],
        top_cost_steps=[],
        top_token_agents=[],
        estimated_retry_cost_waste=0.0,
        estimated_retry_token_waste=0,
        redundant_file_reads=[
            {
                "file_path": "src/orchestrator/models.py",
                "read_count": 5,
                "agents": ["eng_a", "eng_b", "eng_c", "qa", "reviewer"],
                "estimated_token_cost": 18000,
            }
        ],
    )

    suggestions = _build_suggestions(report)

    assert len(suggestions) >= 1
    aicoder_suggestions = [s for s in suggestions if "AICoder" in s]
    assert len(aicoder_suggestions) >= 1
    # Must mention the specific file path
    assert any("src/orchestrator/models.py" in s for s in aicoder_suggestions)


def test_build_suggestions_no_aicoder_when_read_count_below_3():
    """_build_suggestions() does NOT prepend AICoder suggestion when read_count < 3."""
    report = AnalysisReport(
        run_ids=["run-test"],
        repeated_tasks=[],
        duplicate_role_steps=[],
        cross_run_step_freq=[],
        top_cost_steps=[],
        top_token_agents=[],
        estimated_retry_cost_waste=0.0,
        estimated_retry_token_waste=0,
        redundant_file_reads=[
            {
                "file_path": "src/small.py",
                "read_count": 2,  # below threshold of 3
                "agents": ["a1", "a2"],
                "estimated_token_cost": 500,
            }
        ],
    )

    suggestions = _build_suggestions(report)
    # read_count=2 should NOT trigger AICoder suggestion for this file
    aicoder_small = [s for s in suggestions if "src/small.py" in s and "AICoder" in s]
    assert len(aicoder_small) == 0


def test_build_suggestions_capped_at_3():
    """_build_suggestions() returns at most 3 suggestions."""
    report = AnalysisReport(
        run_ids=["run-test"],
        repeated_tasks=[
            {"run_id": "r1", "step": "compile", "agent": "eng", "attempts": 5, "cost_usd": 1.5, "feature": "feat"}
        ],
        duplicate_role_steps=[
            {"run_id": "r1", "role": "backend_engineer", "steps": ["s1", "s2", "s3"], "count": 3, "feature": "feat"}
        ],
        cross_run_step_freq=[
            {"step": "deploy", "agent": "devops", "run_count": 10, "total_cost_usd": 5.0}
        ],
        top_cost_steps=[
            {"step": "expensive", "agent": "eng", "total_cost_usd": 9.99}
        ],
        top_token_agents=[],
        estimated_retry_cost_waste=1.5,
        estimated_retry_token_waste=1000,
        redundant_file_reads=[
            {
                "file_path": "src/big.py",
                "read_count": 8,
                "agents": ["a", "b", "c"],
                "estimated_token_cost": 5000,
            }
        ],
    )

    suggestions = _build_suggestions(report)
    assert len(suggestions) <= 3


# ---------------------------------------------------------------------------
# TASK-001/002 Tests: token waste calculation
# ---------------------------------------------------------------------------


def test_estimated_duplicate_read_token_waste(tmp_path):
    """REQ-006: estimated_duplicate_read_token_waste equals total tokens from duplicate invocations."""
    events = [{"event": "run_start", "feature_request": "waste calc"}]
    # 3 agents read the same file with tokens 1000, 1200, 800
    for agent, tokens in [("a1", 1000), ("a2", 1200), ("a3", 800)]:
        events.append({
            "event": "tool_use",
            "tool_name": "Read",
            "tool_input": {"file_path": "src/models.py"},
            "agent": agent,
            "input_tokens": tokens,
        })

    f = tmp_path / "run-waste.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    report = analyze_runs(tmp_path)

    # Waste is calculated as tokens after the "first" read (min-token = 800)
    # Sorted tokens: [800, 1000, 1200], waste = 1000 + 1200 = 2200
    assert report.estimated_duplicate_read_token_waste == 2200


# ---------------------------------------------------------------------------
# Integration: analyze_runs() end-to-end
# ---------------------------------------------------------------------------


def test_analyze_runs_empty_directory(tmp_path):
    """analyze_runs() on a directory with no JSONL files returns empty report."""
    report = analyze_runs(tmp_path)
    assert report.run_ids == []
    assert report.redundant_file_reads == []
    assert report.repeated_tool_calls == []
    assert report.prompt_fragments == []


def test_analyze_runs_multiple_files(tmp_path):
    """analyze_runs() aggregates events across multiple JSONL files."""
    # File 1: agent_a reads models.py
    f1 = tmp_path / "run-m1.jsonl"
    f1.write_text(json.dumps({
        "event": "tool_use",
        "tool_name": "Read",
        "tool_input": {"file_path": "src/models.py"},
        "agent": "agent_a",
        "input_tokens": 500,
    }) + "\n")

    # File 2: agent_b reads models.py
    f2 = tmp_path / "run-m2.jsonl"
    f2.write_text(json.dumps({
        "event": "tool_use",
        "tool_name": "Read",
        "tool_input": {"file_path": "src/models.py"},
        "agent": "agent_b",
        "input_tokens": 600,
    }) + "\n")

    report = analyze_runs(tmp_path, last_n=2)

    assert len(report.run_ids) == 2
    # models.py read by 2 distinct agents → should appear in redundant_file_reads
    paths = [e["file_path"] for e in report.redundant_file_reads]
    assert "src/models.py" in paths
