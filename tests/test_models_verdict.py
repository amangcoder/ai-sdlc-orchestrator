"""Test ReviewVerdict enum and normalize_verdict function."""

from __future__ import annotations

from orchestrator.models import ReviewVerdict, normalize_verdict


def test_review_verdict_no_duplicates() -> None:
    """Test that ReviewVerdict enum has no duplicate values."""
    # Get all enum members and their values
    members = list(ReviewVerdict.__members__.items())
    values = [member[1].value for member in members]

    # Check for duplicates
    assert len(values) == len(set(values)), f"Duplicate values found: {values}"


def test_review_verdict_canonical_values() -> None:
    """Test that ReviewVerdict has the canonical Orchestrator verdict values."""
    assert ReviewVerdict.APPROVE.value == "approve"
    assert ReviewVerdict.REJECT.value == "reject"
    assert ReviewVerdict.REQUEST_CHANGES.value == "request_changes"
    assert ReviewVerdict.PASS_WITH_WARNINGS.value == "pass_with_warnings"


def test_review_verdict_no_aicoder_duplicates() -> None:
    """Test that ReviewVerdict no longer has AICoder-compatible duplicate aliases."""
    members = ReviewVerdict.__members__
    assert "PASS" not in members, "PASS should not be in ReviewVerdict (use APPROVE for AICoder pass)"
    assert "FAIL" not in members, "FAIL should not be in ReviewVerdict (use REJECT for AICoder fail)"


def test_normalize_verdict_maps_aicoder_values() -> None:
    """Test that normalize_verdict correctly maps old AICoder values to new Orchestrator values."""
    # These mappings are defined in _VERDICT_NORMALIZE
    assert normalize_verdict("pass") == "approve"
    assert normalize_verdict("fail") == "reject"
    assert normalize_verdict("pass_with_warnings") == "request_changes"

    # Unknown values should pass through unchanged
    assert normalize_verdict("unknown") == "unknown"


def test_normalize_verdict_handles_canonical_values() -> None:
    """Test that normalize_verdict returns canonical Orchestrator values unchanged."""
    # Canonical values should pass through unchanged
    assert normalize_verdict("approve") == "approve"
    assert normalize_verdict("reject") == "reject"
    assert normalize_verdict("request_changes") == "request_changes"
