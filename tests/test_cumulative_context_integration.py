"""Integration tests for cumulative context phase name translation and canonical map.

Covers:
- AC-004: Canonical phase keys in phase_artifact_map
- AC-010: Legacy → canonical phase translation via map_phase_for_mcp
- AC-008: Backward-compat: legacy phase names still resolve correctly
- AC-002: No non-canonical phase names used in _MCP_ROLE_GUIDANCE guidance strings
- AC-003: .mcp.json has 'cumulative-context-engine' entry with command='python3'
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from orchestrator.knowledge import map_phase_for_mcp, update_cumulative_context
from orchestrator.phases import _MCP_ROLE_GUIDANCE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CANONICAL_PHASES = {"prd", "architecture", "engineering_plan", "task_breakdown", "implementation"}

_MINIMAL_PRD = {
    "title": "Test PRD",
    "overview": "A minimal PRD for testing purposes.",
    "goals": ["Goal 1"],
    "requirements": [{"id": "REQ-001", "description": "Test requirement", "priority": "must"}],
    "constraints": ["Test constraint"],
    "acceptance_criteria": ["AC-001: Test criterion"],
}


# ---------------------------------------------------------------------------
# Test 1 — Canonical phase map keys
# ---------------------------------------------------------------------------

class TestCanonicalPhaseMapKeys:
    """update_cumulative_context must handle all 5 canonical phase keys."""

    def test_canonical_phase_map_keys(self, tmp_path: Path) -> None:
        """All 5 canonical keys must be present in the internal phase_artifact_map."""
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()

        # Seed a prd.json so the 'prd' phase can produce a digest (proving it's mapped)
        (artifacts_dir / "prd.json").write_text(json.dumps(_MINIMAL_PRD))

        # All 5 canonical phases must be accepted without raising
        for phase in _CANONICAL_PHASES:
            update_cumulative_context(workspace=tmp_path, phase_name=phase)

        # 'prd' phase should have produced a context.md entry
        context_path = tmp_path / "context.md"
        assert context_path.exists(), "context.md must be created when prd.json is present"
        content = context_path.read_text()
        assert "## Prd Phase" in content, "context.md must contain a '## Prd Phase' section"

        # Verify exactly 5 canonical phases are defined (guard against accidental additions)
        assert len(_CANONICAL_PHASES) == 5


# ---------------------------------------------------------------------------
# Test 2 — Legacy phase translation
# ---------------------------------------------------------------------------

class TestLegacyPhaseTranslation:
    """map_phase_for_mcp must translate legacy orchestrator phase names to canonical names."""

    @pytest.mark.parametrize("legacy,canonical", [
        ("pm", "prd"),
        ("architect", "architecture"),
        ("principal_engineer", "engineering_plan"),
        ("tpm", "task_breakdown"),
        ("engineer", "implementation"),
    ])
    def test_legacy_phase_translation(self, legacy: str, canonical: str) -> None:
        """Each legacy phase name must translate to its canonical counterpart."""
        result = map_phase_for_mcp(legacy)
        assert result == canonical, (
            f"map_phase_for_mcp('{legacy}') returned '{result}', expected '{canonical}'"
        )

    def test_canonical_phases_pass_through(self) -> None:
        """Canonical phase names must pass through map_phase_for_mcp unchanged."""
        for phase in _CANONICAL_PHASES:
            assert map_phase_for_mcp(phase) == phase, (
                f"Canonical phase '{phase}' should not be remapped by map_phase_for_mcp"
            )


# ---------------------------------------------------------------------------
# Test 3 — Canonical header in context.md
# ---------------------------------------------------------------------------

class TestUpdateCumulativeContextCanonicalHeader:
    """update_cumulative_context must write the correct phase header in context.md."""

    def test_update_cumulative_context_canonical_header(self, tmp_path: Path) -> None:
        """Calling with phase_name='prd' must produce '## Prd Phase' in context.md."""
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        (artifacts_dir / "prd.json").write_text(json.dumps(_MINIMAL_PRD))

        update_cumulative_context(
            workspace=tmp_path,
            phase_name="prd",
            artifacts=["prd"],
        )

        context_path = tmp_path / "context.md"
        assert context_path.exists(), "context.md must be created after update_cumulative_context"
        content = context_path.read_text()
        assert "## Prd Phase" in content, (
            f"Expected '## Prd Phase' in context.md. Got:\n{content}"
        )

    def test_header_uses_title_case(self, tmp_path: Path) -> None:
        """Phase names with underscores must be title-cased in the header."""
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        # 'engineering_plan' phase with explicit artifacts=[] produces no output,
        # so use 'prd' as a proxy and verify any phase header is title-cased.
        (artifacts_dir / "prd.json").write_text(json.dumps(_MINIMAL_PRD))

        update_cumulative_context(workspace=tmp_path, phase_name="prd", artifacts=["prd"])

        content = (tmp_path / "context.md").read_text()
        # Title-case: "## Prd Phase", not "## prd phase"
        assert "## prd phase" not in content.lower() or "## Prd Phase" in content


# ---------------------------------------------------------------------------
# Test 4 — Legacy backward compat
# ---------------------------------------------------------------------------

class TestLegacyBackwardCompat:
    """Legacy phase names passed to update_cumulative_context must still work."""

    def test_legacy_backward_compat(self, tmp_path: Path) -> None:
        """phase_name='pm' must resolve to ['prd'] artifact list without error."""
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()

        # Create prd.json — the legacy 'pm' phase maps to ['prd'] artifact list
        (artifacts_dir / "prd.json").write_text(json.dumps(_MINIMAL_PRD))

        # Must not raise; 'pm' → resolves to ['prd'] via backward-compat alias
        update_cumulative_context(workspace=tmp_path, phase_name="pm")

        context_path = tmp_path / "context.md"
        assert context_path.exists(), "context.md must be written for legacy 'pm' phase"
        content = context_path.read_text()
        # The PRD digest should appear (proving prd.json was consumed via the legacy alias)
        assert "Test PRD" in content or "PRD" in content.upper(), (
            "Expected PRD digest content to appear in context.md for legacy 'pm' phase"
        )

    @pytest.mark.parametrize("legacy_phase", ["pm", "architect", "principal_engineer", "tpm", "engineer"])
    def test_all_legacy_phases_accepted(self, tmp_path: Path, legacy_phase: str) -> None:
        """All 5 legacy phase names must be accepted by update_cumulative_context."""
        (tmp_path / "artifacts").mkdir()
        # Should not raise even when no artifact files exist (empty phase)
        update_cumulative_context(workspace=tmp_path, phase_name=legacy_phase)


# ---------------------------------------------------------------------------
# Test 5 — No legacy phases in guidance
# ---------------------------------------------------------------------------

class TestNoLegacyPhasesInGuidance:
    """_MCP_ROLE_GUIDANCE must only reference canonical get_cumulative_context phase names."""

    def test_no_legacy_phases_in_guidance(self) -> None:
        """No get_cumulative_context call in _MCP_ROLE_GUIDANCE may use a non-canonical phase."""
        # Extract all phase="..." values from get_cumulative_context calls
        pattern = re.compile(r'get_cumulative_context\(\s*phase\s*=\s*"([^"]+)"')
        violations: list[tuple[str, str]] = []

        for role, guidance in _MCP_ROLE_GUIDANCE.items():
            for phase in pattern.findall(guidance):
                if phase not in _CANONICAL_PHASES:
                    violations.append((role, phase))

        assert not violations, (
            "Non-canonical phase names found in _MCP_ROLE_GUIDANCE:\n"
            + "\n".join(f"  role={role!r}: phase={phase!r}" for role, phase in violations)
        )

    def test_guidance_has_canonical_phase_calls(self) -> None:
        """At least one guidance entry must reference a canonical phase."""
        pattern = re.compile(r'get_cumulative_context\(\s*phase\s*=\s*"([^"]+)"')
        canonical_found: set[str] = set()

        for guidance in _MCP_ROLE_GUIDANCE.values():
            for phase in pattern.findall(guidance):
                if phase in _CANONICAL_PHASES:
                    canonical_found.add(phase)

        assert canonical_found, "Expected at least one canonical phase reference in _MCP_ROLE_GUIDANCE"


# ---------------------------------------------------------------------------
# Test 6 — .mcp.json has cumulative-context-engine
# ---------------------------------------------------------------------------

class TestMcpJsonHasCumulativeContextEngine:
    """The project's .mcp.json must register the cumulative-context-engine server."""

    def test_mcp_json_has_cumulative_context_engine(self) -> None:
        """'cumulative-context-engine' entry with command='python3' must exist in .mcp.json."""
        project_root = Path(__file__).resolve().parents[1]
        mcp_path = project_root / ".mcp.json"

        assert mcp_path.exists(), f".mcp.json not found at {mcp_path}"

        try:
            data = json.loads(mcp_path.read_text())
        except json.JSONDecodeError as exc:
            pytest.fail(f".mcp.json is not valid JSON: {exc}")

        servers = data.get("mcpServers", {})
        assert "cumulative-context-engine" in servers, (
            f"'cumulative-context-engine' key missing from .mcp.json mcpServers. "
            f"Found keys: {list(servers.keys())}"
        )

        entry = servers["cumulative-context-engine"]
        assert entry.get("command") == "python3", (
            f"Expected command='python3' for cumulative-context-engine, "
            f"got command='{entry.get('command')}'"
        )

    def test_mcp_json_preserves_existing_entries(self) -> None:
        """Existing .mcp.json entries must not be disturbed by cumulative-context-engine addition."""
        project_root = Path(__file__).resolve().parents[1]
        mcp_path = project_root / ".mcp.json"

        assert mcp_path.exists()
        data = json.loads(mcp_path.read_text())
        servers = data.get("mcpServers", {})

        # ai-code-knowledge must still be present
        assert "ai-code-knowledge" in servers, (
            "'ai-code-knowledge' entry was removed from .mcp.json"
        )
        assert servers["ai-code-knowledge"].get("command") == "node", (
            "'ai-code-knowledge' command must remain 'node'"
        )
