"""Tests for the observability dashboard route and get_observability_urls()."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, quote, unquote, urlparse

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from orchestrator.dashboard.data import get_observability_urls
from orchestrator.dashboard.routes.observability import (
    _RUN_ID_RE,
    _load_monitoring_config,
    create_observability_router,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "orchestrator" / "dashboard" / "templates"


def _make_app(config_path: Optional[Path] = None) -> FastAPI:
    """Create a minimal FastAPI test app with the observability router mounted."""
    app = FastAPI()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    router = create_observability_router(templates, config_path)
    app.include_router(router)
    return app


def _client(config_path: Optional[Path] = None) -> TestClient:
    return TestClient(_make_app(config_path), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# get_observability_urls() unit tests
# ---------------------------------------------------------------------------


class TestGetObservabilityUrls:
    """Unit tests for the standalone URL-builder function."""

    def test_returns_dict_with_eight_keys(self) -> None:
        result = get_observability_urls()
        assert len(result) == 8

    def test_all_keys_present(self) -> None:
        result = get_observability_urls()
        expected_keys = {
            "grafana_run_overview",
            "grafana_cost_analysis",
            "grafana_agent_performance",
            "grafana_error_analysis",
            "grafana_slo_overview",
            "jaeger_trace_search",
            "loki_explore",
            "loki_query_api",
        }
        assert set(result.keys()) == expected_keys

    def test_all_none_when_no_config(self) -> None:
        result = get_observability_urls()
        assert all(v is None for v in result.values())

    # --- Grafana -----------------------------------------------------------

    def test_grafana_urls_use_correct_uid(self) -> None:
        result = get_observability_urls(grafana_url="http://localhost:3000")
        assert result["grafana_run_overview"] is not None
        assert "/d/run-overview/run-overview" in result["grafana_run_overview"]
        assert result["grafana_cost_analysis"] is not None
        assert "/d/cost-analysis/cost-analysis" in result["grafana_cost_analysis"]
        assert result["grafana_agent_performance"] is not None
        assert "/d/agent-performance/agent-performance" in result["grafana_agent_performance"]
        assert result["grafana_error_analysis"] is not None
        assert "/d/error-analysis/error-analysis" in result["grafana_error_analysis"]
        assert result["grafana_slo_overview"] is not None
        assert "/d/slo-overview/slo-overview" in result["grafana_slo_overview"]

    def test_grafana_urls_include_run_id_param(self) -> None:
        result = get_observability_urls(
            run_id="abc123",
            grafana_url="http://localhost:3000",
        )
        for key in [
            "grafana_run_overview",
            "grafana_cost_analysis",
            "grafana_agent_performance",
            "grafana_error_analysis",
            "grafana_slo_overview",
        ]:
            url = result[key]
            assert url is not None, f"{key} should not be None"
            assert "var-run_id=abc123" in url, f"{key} missing var-run_id param"

    def test_grafana_urls_without_run_id_omit_run_id_param(self) -> None:
        result = get_observability_urls(grafana_url="http://localhost:3000")
        assert result["grafana_run_overview"] is not None
        assert "var-run_id" not in result["grafana_run_overview"]

    def test_grafana_urls_none_when_no_grafana_url(self) -> None:
        result = get_observability_urls(run_id="abc", jaeger_ui_url="http://jaeger:16686")
        for key in ["grafana_run_overview", "grafana_cost_analysis"]:
            assert result[key] is None

    def test_grafana_base_url_trailing_slash_stripped(self) -> None:
        result = get_observability_urls(grafana_url="http://localhost:3000/")
        assert result["grafana_run_overview"] is not None
        assert "//d/" not in result["grafana_run_overview"]

    # --- Jaeger ------------------------------------------------------------

    def test_jaeger_url_format_with_run_id(self) -> None:
        result = get_observability_urls(
            run_id="myrun123",
            jaeger_ui_url="http://localhost:16686",
        )
        jaeger = result["jaeger_trace_search"]
        assert jaeger is not None
        assert jaeger.startswith("http://localhost:16686/search?service=orchestrator")
        # Colon between key and value (URL-encoded as %3A)
        assert "run_id%3Amyrun123" in jaeger

    def test_jaeger_url_without_run_id_has_no_tags_filter(self) -> None:
        result = get_observability_urls(jaeger_ui_url="http://localhost:16686")
        jaeger = result["jaeger_trace_search"]
        assert jaeger is not None
        assert "service=orchestrator" in jaeger
        assert "run_id" not in jaeger

    def test_jaeger_none_when_no_jaeger_url(self) -> None:
        result = get_observability_urls(grafana_url="http://grafana:3000")
        assert result["jaeger_trace_search"] is None

    # --- Loki Explore ------------------------------------------------------

    def test_loki_explore_uses_grafana_url(self) -> None:
        result = get_observability_urls(
            grafana_url="http://localhost:3000",
            loki_endpoint="http://localhost:3100",
        )
        explore = result["loki_explore"]
        assert explore is not None
        assert explore.startswith("http://localhost:3000/explore")

    def test_loki_explore_contains_logql_query_with_run_id(self) -> None:
        result = get_observability_urls(
            run_id="run42",
            grafana_url="http://localhost:3000",
        )
        explore = result["loki_explore"]
        assert explore is not None
        # The `left` param should be URL-encoded JSON containing the LogQL expression
        parsed = urlparse(explore)
        params = parse_qs(parsed.query)
        left_raw = params.get("left", [""])[0]
        left_decoded = unquote(left_raw)
        left_json = json.loads(left_decoded)
        expr = left_json["queries"][0]["expr"]
        assert 'job="orchestrator"' in expr
        assert 'run_id="run42"' in expr

    def test_loki_explore_logql_without_run_id(self) -> None:
        result = get_observability_urls(grafana_url="http://localhost:3000")
        explore = result["loki_explore"]
        assert explore is not None
        parsed = urlparse(explore)
        params = parse_qs(parsed.query)
        left_decoded = unquote(params["left"][0])
        expr = json.loads(left_decoded)["queries"][0]["expr"]
        assert 'job="orchestrator"' in expr
        assert "run_id" not in expr

    def test_loki_explore_none_when_no_grafana_url(self) -> None:
        result = get_observability_urls(loki_endpoint="http://loki:3100")
        assert result["loki_explore"] is None

    # --- Loki Query API ----------------------------------------------------

    def test_loki_query_api_url_with_run_id(self) -> None:
        result = get_observability_urls(
            run_id="xyz789",
            loki_endpoint="http://localhost:3100",
        )
        api = result["loki_query_api"]
        assert api is not None
        assert api.startswith("http://localhost:3100/loki/api/v1/query_range")
        decoded_query = unquote(urlparse(api).query)
        assert 'job="orchestrator"' in decoded_query
        assert 'run_id="xyz789"' in decoded_query

    def test_loki_query_api_none_when_no_loki_endpoint(self) -> None:
        result = get_observability_urls(grafana_url="http://grafana:3000")
        assert result["loki_query_api"] is None

    # --- Edge cases --------------------------------------------------------

    def test_run_id_url_encoded_in_all_urls(self) -> None:
        """run_id with unusual but safe characters is properly encoded."""
        result = get_observability_urls(
            run_id="run-abc_123",
            grafana_url="http://localhost:3000",
            jaeger_ui_url="http://localhost:16686",
            loki_endpoint="http://localhost:3100",
        )
        # All non-None values should not contain raw un-encoded spaces or quotes
        for key, url in result.items():
            if url is not None:
                assert " " not in url, f"{key} contains un-encoded space"

    def test_empty_run_id_treated_as_no_run_id(self) -> None:
        result_none = get_observability_urls(
            run_id=None,
            grafana_url="http://localhost:3000",
        )
        result_empty = get_observability_urls(
            run_id="",
            grafana_url="http://localhost:3000",
        )
        assert result_none == result_empty


# ---------------------------------------------------------------------------
# _load_monitoring_config() unit tests
# ---------------------------------------------------------------------------


class TestLoadMonitoringConfig:
    def test_returns_none_when_no_config_path(self) -> None:
        assert _load_monitoring_config(None) is None

    def test_returns_none_when_file_not_found(self, tmp_path: Path) -> None:
        assert _load_monitoring_config(tmp_path / "nonexistent.yaml") is None

    def test_returns_none_when_no_monitoring_section(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("phases:\n  pm: {}\n")
        assert _load_monitoring_config(cfg) is None

    def test_loads_grafana_url(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            textwrap.dedent("""\
            monitoring:
              grafana_url: "http://localhost:3000"
              jaeger_ui_url: "http://localhost:16686"
              loki_endpoint: "http://localhost:3100"
            """)
        )
        result = _load_monitoring_config(cfg)
        assert result is not None
        assert result.grafana_url == "http://localhost:3000"
        assert result.jaeger_ui_url == "http://localhost:16686"
        assert result.loki_endpoint == "http://localhost:3100"

    def test_returns_none_on_invalid_yaml(self, tmp_path: Path) -> None:
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("monitoring: {\n  bad yaml: [[[")
        assert _load_monitoring_config(cfg) is None


# ---------------------------------------------------------------------------
# Run ID regex
# ---------------------------------------------------------------------------


class TestRunIdRegex:
    @pytest.mark.parametrize(
        "run_id",
        ["abc123", "a1b2c3d4e5f6", "run-001", "my_run_42", "A" * 64],
    )
    def test_valid_run_ids(self, run_id: str) -> None:
        assert _RUN_ID_RE.match(run_id), f"Expected {run_id!r} to match"

    @pytest.mark.parametrize(
        "run_id",
        ["", "a b", "<script>", "../../etc/passwd", "a" * 65],
    )
    def test_invalid_run_ids(self, run_id: str) -> None:
        assert not _RUN_ID_RE.match(run_id), f"Expected {run_id!r} NOT to match"


# ---------------------------------------------------------------------------
# GET /observability route integration tests
# ---------------------------------------------------------------------------


class TestObservabilityRoute:
    """Integration tests for the GET /observability endpoint."""

    def test_returns_200(self) -> None:
        client = _client()
        resp = client.get("/observability")
        assert resp.status_code == 200

    def test_content_type_is_html(self) -> None:
        client = _client()
        resp = client.get("/observability")
        assert "text/html" in resp.headers["content-type"]

    def test_page_title_in_html(self) -> None:
        client = _client()
        resp = client.get("/observability")
        assert "Observability" in resp.text

    def test_three_sections_present(self) -> None:
        client = _client()
        html = client.get("/observability").text
        assert "Grafana Dashboards" in html
        assert "Jaeger Distributed Tracing" in html
        assert "Loki Log Explorer" in html

    def test_no_iframes(self) -> None:
        client = _client()
        html = client.get("/observability").text
        assert "<iframe" not in html.lower()

    def test_config_missing_warning_shown_when_no_config(self) -> None:
        client = _client()
        html = client.get("/observability").text
        assert "Not Configured" in html or "config" in html.lower()

    def test_links_open_in_new_tab_when_configured(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            textwrap.dedent("""\
            monitoring:
              grafana_url: "http://localhost:3000"
              jaeger_ui_url: "http://localhost:16686"
              loki_endpoint: "http://localhost:3100"
            """)
        )
        client = _client(cfg)
        html = client.get("/observability").text
        # All external links must have target="_blank"
        import re

        href_links = re.findall(r'<a\s[^>]+href="http[^"]*"[^>]*>', html)
        for link in href_links:
            assert 'target="_blank"' in link, f"Link missing target=_blank: {link[:120]}"

    def test_run_id_filter_included_in_links_when_provided(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            textwrap.dedent("""\
            monitoring:
              grafana_url: "http://localhost:3000"
              jaeger_ui_url: "http://localhost:16686"
              loki_endpoint: "http://localhost:3100"
            """)
        )
        client = _client(cfg)
        html = client.get("/observability?run_id=abc123").text
        assert "abc123" in html

    def test_unsafe_run_id_sanitised(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            textwrap.dedent("""\
            monitoring:
              grafana_url: "http://localhost:3000"
            """)
        )
        client = _client(cfg)
        # XSS attempt — should be silently dropped
        resp = client.get("/observability?run_id=<script>alert(1)</script>")
        assert resp.status_code == 200
        assert "<script>alert" not in resp.text

    def test_grafana_dashboards_all_five_shown(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            textwrap.dedent("""\
            monitoring:
              grafana_url: "http://localhost:3000"
            """)
        )
        client = _client(cfg)
        html = client.get("/observability").text
        for name in ["Run Overview", "Cost Analysis", "Agent Performance", "Error Analysis", "SLO Overview"]:
            assert name in html, f"Dashboard card '{name}' missing from page"

    def test_page_works_without_config_file(self) -> None:
        """Page renders gracefully when config_path is None."""
        client = _client(None)
        resp = client.get("/observability")
        assert resp.status_code == 200

    def test_page_works_with_partial_config(self, tmp_path: Path) -> None:
        """Page renders when only some services are configured."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            textwrap.dedent("""\
            monitoring:
              grafana_url: "http://localhost:3000"
            """)
        )
        client = _client(cfg)
        resp = client.get("/observability")
        assert resp.status_code == 200
        html = resp.text
        # Grafana should show links, Jaeger/Loki should show 'Not configured'
        assert "run-overview" in html
        assert "Not configured" in html

    def test_loki_explore_link_visible_when_grafana_configured(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            textwrap.dedent("""\
            monitoring:
              grafana_url: "http://localhost:3000"
              loki_endpoint: "http://localhost:3100"
            """)
        )
        client = _client(cfg)
        html = client.get("/observability").text
        assert "Grafana Explore" in html
        assert "Loki Query API" in html

    def test_jaeger_link_visible_when_jaeger_configured(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            textwrap.dedent("""\
            monitoring:
              jaeger_ui_url: "http://localhost:16686"
            """)
        )
        client = _client(cfg)
        html = client.get("/observability").text
        assert "Trace Search" in html
        assert "localhost:16686" in html

    def test_run_id_query_param_reflected_in_page(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            textwrap.dedent("""\
            monitoring:
              grafana_url: "http://localhost:3000"
              jaeger_ui_url: "http://localhost:16686"
              loki_endpoint: "http://localhost:3100"
            """)
        )
        client = _client(cfg)
        html = client.get("/observability?run_id=deadbeef").text
        assert "deadbeef" in html

    def test_clear_link_present_when_run_id_given(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("monitoring:\n  grafana_url: 'http://localhost:3000'\n")
        client = _client(cfg)
        html = client.get("/observability?run_id=abc").text
        assert "/observability" in html  # Clear link href
