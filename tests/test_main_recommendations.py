"""Unit tests for main.py end-of-run research recommendations output.

Covers TASK-007 acceptance criteria (REQ-030, AC-011):
- When research_cache_context.findings contains 2 findings (one high, one medium),
  stdout after run summary includes a 'Recommendations:' section listing each finding's
  severity bracket, finding text, and recommendation text.
- When research_cache_context is None (feature disabled), no recommendations section
  is printed and no error is raised.
- When research_cache_context.findings is an empty list, no recommendations section
  is printed.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Any

import pytest

from orchestrator.models import (
    Finding,
    OrchestratorConfig,
    ResearchCacheConfig,
    ResearchCacheContext,
)
from orchestrator.research_cache import format_recommendations


# ---------------------------------------------------------------------------
# Helpers — replicate the main.py print-recommendations block
# ---------------------------------------------------------------------------

def _print_recommendations_block(config: OrchestratorConfig) -> str:
    """Replicate the main.py block that prints end-of-run recommendations.

    Returns the text that would be printed to stdout, or an empty string
    if the guard condition is False.
    """
    buf = io.StringIO()
    if (
        config.research_cache_context is not None
        and config.research_cache_context.findings
    ):
        findings = [
            Finding(**f) if isinstance(f, dict) else f
            for f in config.research_cache_context.findings
        ]
        with redirect_stdout(buf):
            print(format_recommendations([f.model_dump() for f in findings]))
    return buf.getvalue()


def _make_config_with_findings(findings: list[dict[str, Any]]) -> OrchestratorConfig:
    config = OrchestratorConfig(
        research_cache=ResearchCacheConfig(enabled=True)
    )
    config.research_cache_context = ResearchCacheContext(
        cache_loaded=True,
        mcp_configured=True,
        mcp_server_config=None,
        global_entry_count=0,
        local_entry_count=0,
        findings=findings,
    )
    return config


# ---------------------------------------------------------------------------
# AC-011 / REQ-030: findings present → Recommendations section printed
# ---------------------------------------------------------------------------

class TestMainRecommendationsOutput:
    def test_two_findings_prints_recommendations_header(self):
        """Stdout must contain 'Recommendations:' when findings are non-empty."""
        config = _make_config_with_findings([
            Finding(
                type="security",
                severity="high",
                finding="SQL injection risk in search endpoint",
                recommendation="Use parameterised queries",
                phase="architect",
            ).model_dump(),
            Finding(
                type="quality",
                severity="medium",
                finding="Missing input validation on signup",
                recommendation="Add Pydantic validators",
                phase="pm",
            ).model_dump(),
        ])

        output = _print_recommendations_block(config)

        assert "Recommendations:" in output, (
            f"Expected 'Recommendations:' header in output; got:\n{output!r}"
        )

    def test_high_severity_finding_appears_in_output(self):
        """High severity finding text and recommendation must appear in output."""
        config = _make_config_with_findings([
            Finding(
                type="security",
                severity="high",
                finding="SQL injection risk in search endpoint",
                recommendation="Use parameterised queries",
                phase="architect",
            ).model_dump(),
            Finding(
                type="quality",
                severity="medium",
                finding="Missing input validation on signup",
                recommendation="Add Pydantic validators",
                phase="pm",
            ).model_dump(),
        ])

        output = _print_recommendations_block(config)

        assert "[high]" in output
        assert "SQL injection risk in search endpoint" in output
        assert "Use parameterised queries" in output

    def test_medium_severity_finding_appears_in_output(self):
        """Medium severity finding text and recommendation must appear in output."""
        config = _make_config_with_findings([
            Finding(
                type="security",
                severity="high",
                finding="SQL injection risk in search endpoint",
                recommendation="Use parameterised queries",
                phase="architect",
            ).model_dump(),
            Finding(
                type="quality",
                severity="medium",
                finding="Missing input validation on signup",
                recommendation="Add Pydantic validators",
                phase="pm",
            ).model_dump(),
        ])

        output = _print_recommendations_block(config)

        assert "[medium]" in output
        assert "Missing input validation on signup" in output
        assert "Add Pydantic validators" in output

    def test_findings_use_arrow_separator(self):
        """Each finding line must use '→' to separate finding text from recommendation."""
        config = _make_config_with_findings([
            Finding(
                type="performance",
                severity="high",
                finding="N+1 query in orders endpoint",
                recommendation="Add eager loading for order items",
                phase="architect",
            ).model_dump(),
        ])

        output = _print_recommendations_block(config)

        assert "→" in output

    def test_both_findings_listed_on_separate_lines(self):
        """Each of the two findings must appear on its own line."""
        config = _make_config_with_findings([
            Finding(
                type="security",
                severity="high",
                finding="Finding one text",
                recommendation="Recommendation one",
                phase="architect",
            ).model_dump(),
            Finding(
                type="quality",
                severity="medium",
                finding="Finding two text",
                recommendation="Recommendation two",
                phase="pm",
            ).model_dump(),
        ])

        output = _print_recommendations_block(config)

        lines_with_high = [l for l in output.splitlines() if "[high]" in l]
        lines_with_medium = [l for l in output.splitlines() if "[medium]" in l]
        assert len(lines_with_high) == 1, "Exactly one line for high-severity finding"
        assert len(lines_with_medium) == 1, "Exactly one line for medium-severity finding"


# ---------------------------------------------------------------------------
# AC-011: research_cache_context is None → no output, no error
# ---------------------------------------------------------------------------

class TestMainRecommendationsWhenDisabled:
    def test_context_none_prints_nothing(self):
        """With research_cache_context=None, the block must print nothing."""
        config = OrchestratorConfig(
            research_cache=ResearchCacheConfig(enabled=False)
        )
        assert config.research_cache_context is None

        output = _print_recommendations_block(config)

        assert output == "", (
            f"Expected empty output when context is None; got:\n{output!r}"
        )

    def test_context_none_raises_no_exception(self):
        """Block must not raise any exception when research_cache_context is None."""
        config = OrchestratorConfig(
            research_cache=ResearchCacheConfig(enabled=False)
        )

        # Should complete without raising
        _print_recommendations_block(config)


# ---------------------------------------------------------------------------
# AC-011: findings is empty list → no output
# ---------------------------------------------------------------------------

class TestMainRecommendationsWhenEmpty:
    def test_empty_findings_prints_nothing(self):
        """With an empty findings list, the block must print nothing."""
        config = _make_config_with_findings([])

        output = _print_recommendations_block(config)

        assert output == "", (
            f"Expected empty output when findings list is empty; got:\n{output!r}"
        )

    def test_empty_findings_raises_no_exception(self):
        """Block must not raise any exception when findings is an empty list."""
        config = _make_config_with_findings([])

        # Should complete without raising
        _print_recommendations_block(config)


# ---------------------------------------------------------------------------
# Guard: findings stored as serialized dicts (the runtime path via flag_finding)
# ---------------------------------------------------------------------------

class TestFindingsAsDicts:
    def test_findings_as_serialized_dicts(self):
        """main.py block correctly reconstructs Finding objects from serialized dicts.

        At runtime, flag_finding() calls finding.model_dump() before appending,
        so findings in ResearchCacheContext are always dicts. The block in main.py
        calls Finding(**f) for each dict to reconstitute the model before formatting.
        """
        finding_dict = Finding(
            type="architecture",
            severity="high",
            finding="Tight coupling in auth module",
            recommendation="Extract auth into a separate service",
            phase="architect",
        ).model_dump()

        config = _make_config_with_findings([finding_dict])

        output = _print_recommendations_block(config)

        assert "Recommendations:" in output
        assert "Tight coupling in auth module" in output
        assert "Extract auth into a separate service" in output

    def test_single_finding_output_format(self):
        """A single finding produces the correct '[severity] text → recommendation' format."""
        config = _make_config_with_findings([
            Finding(
                type="dependency",
                severity="low",
                finding="Outdated dependency in package.json",
                recommendation="Upgrade to latest stable version",
                phase="reviewer",
            ).model_dump()
        ])

        output = _print_recommendations_block(config)

        assert "[low]" in output
        assert "Outdated dependency in package.json" in output
        assert "Upgrade to latest stable version" in output
        assert "→" in output
