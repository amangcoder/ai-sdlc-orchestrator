"""Tests for the coercion layer and Pydantic model union types."""

import json

import pytest

from orchestrator.models import (
    EngineeringPlan,
    ImplementationPhase,
    RiskAreaDetail,
)
from orchestrator.validation import (
    _coerce_data_flow,
    _coerce_directory_structure,
    _coerce_testing_strategy,
    coerce_artifact_data,
    validate_artifact_file,
)


# ---------------------------------------------------------------------------
# _coerce_data_flow
# ---------------------------------------------------------------------------

class TestCoerceDataFlow:
    def test_list_of_dicts_to_string(self):
        value = [
            {"from": "API", "to": "DB", "data": "user record", "trigger": "POST /users"},
            {"from": "DB", "to": "Cache", "data": "session token"},
        ]
        result = _coerce_data_flow(value)
        assert isinstance(result, str)
        assert "API → DB: user record (trigger: POST /users)" in result
        assert "DB → Cache: session token" in result
        lines = result.split("\n")
        assert "(trigger:" not in lines[1]

    def test_string_passthrough(self):
        value = "Data flows from API to DB to Cache"
        assert _coerce_data_flow(value) is value

    def test_empty_list(self):
        result = _coerce_data_flow([])
        assert isinstance(result, str)
        assert result == ""

    def test_missing_keys_use_defaults(self):
        value = [{"data": "payload"}]
        result = _coerce_data_flow(value)
        assert "? → ?: payload" in result

    def test_mixed_list_entries(self):
        value = [
            {"from": "A", "to": "B", "data": "x"},
            "plain string entry",
        ]
        result = _coerce_data_flow(value)
        assert "A → B: x" in result
        assert "plain string entry" in result


# ---------------------------------------------------------------------------
# _coerce_directory_structure
# ---------------------------------------------------------------------------

class TestCoerceDirectoryStructure:
    def test_flat_path_list_to_nested_dict(self):
        value = ["src/main.py", "src/utils.py", "tests/test_main.py"]
        result = _coerce_directory_structure(value)
        assert isinstance(result, dict)
        assert "src" in result
        assert isinstance(result["src"], dict)
        assert result["src"]["main.py"] == ""
        assert result["src"]["utils.py"] == ""
        assert "tests" in result
        assert result["tests"]["test_main.py"] == ""

    def test_dict_passthrough(self):
        value = {"src": {"main.py": "entry point"}}
        assert _coerce_directory_structure(value) is value

    def test_nested_paths(self):
        value = ["src/utils/helpers.py", "src/utils/formatters.py"]
        result = _coerce_directory_structure(value)
        assert result["src"]["utils"]["helpers.py"] == ""
        assert result["src"]["utils"]["formatters.py"] == ""

    def test_empty_list(self):
        result = _coerce_directory_structure([])
        assert result == {}

    def test_non_string_entries_skipped(self):
        value = ["src/main.py", 42, None]
        result = _coerce_directory_structure(value)
        assert result == {"src": {"main.py": ""}}


# ---------------------------------------------------------------------------
# _coerce_testing_strategy
# ---------------------------------------------------------------------------

class TestCoerceTestingStrategy:
    def test_dict_to_labeled_string(self):
        value = {
            "unit": "Test all utility functions",
            "integration": "Test API endpoints",
            "e2e": "Test full user flows",
        }
        result = _coerce_testing_strategy(value)
        assert isinstance(result, str)
        assert "Unit: Test all utility functions" in result
        assert "Integration: Test API endpoints" in result
        assert "E2E: Test full user flows" in result

    def test_string_passthrough(self):
        value = "Run pytest with coverage"
        assert _coerce_testing_strategy(value) is value

    def test_non_string_values_json_serialized(self):
        value = {"unit": ["test_a", "test_b"]}
        result = _coerce_testing_strategy(value)
        assert "Unit:" in result
        assert "test_a" in result

    def test_underscore_keys_titlecased(self):
        value = {"unit_tests": "Run unit tests"}
        result = _coerce_testing_strategy(value)
        assert "Unit Tests: Run unit tests" in result


# ---------------------------------------------------------------------------
# coerce_artifact_data
# ---------------------------------------------------------------------------

class TestCoerceArtifactData:
    def test_architecture_routes_data_flow(self):
        data = {
            "data_flow": [
                {"from": "Client", "to": "Server", "data": "request"},
            ],
            "components": [],
        }
        result = coerce_artifact_data(data, "architecture")
        assert isinstance(result["data_flow"], str)
        assert "Client → Server" in result["data_flow"]

    def test_architecture_routes_directory_structure(self):
        data = {
            "directory_structure": ["src/app.py", "tests/test_app.py"],
        }
        result = coerce_artifact_data(data, "architecture")
        assert isinstance(result["directory_structure"], dict)
        assert "src" in result["directory_structure"]

    def test_engineering_plan_routes_testing_strategy(self):
        data = {
            "testing_strategy": {"unit": "pytest", "e2e": "playwright"},
        }
        result = coerce_artifact_data(data, "engineering_plan")
        assert isinstance(result["testing_strategy"], str)
        assert "Unit: pytest" in result["testing_strategy"]

    def test_generic_fallback_json_serializes_unknown_string_field(self):
        # engineering_plan's 'strategy' is type=string in schema, no specific coercer
        data = {"strategy": {"approach": "modular"}}
        result = coerce_artifact_data(data, "engineering_plan")
        assert isinstance(result["strategy"], str)
        assert "modular" in result["strategy"]

    def test_does_not_mutate_original(self):
        data = {
            "data_flow": [{"from": "A", "to": "B", "data": "x"}],
        }
        coerce_artifact_data(data, "architecture")
        assert isinstance(data["data_flow"], list)

    def test_unknown_artifact_passes_through(self):
        data = {"foo": "bar", "baz": 42}
        result = coerce_artifact_data(data, "nonexistent_artifact")
        assert result == data

    def test_non_dict_returns_as_is(self):
        assert coerce_artifact_data("not a dict", "architecture") == "not a dict"


# ---------------------------------------------------------------------------
# Pydantic model union types
# ---------------------------------------------------------------------------

class TestEngineeringPlanUnions:
    def test_mixed_implementation_order(self):
        plan = EngineeringPlan(
            strategy="A comprehensive modular implementation approach using layered architecture",
            implementation_order=[
                "Set up project scaffolding",
                ImplementationPhase(
                    phase="Core Logic",
                    description="Implement business rules",
                    tasks=["TASK-1", "TASK-2"],
                ),
                "Write integration tests",
            ],
            testing_strategy="Run pytest with full coverage and integration tests",
        )
        assert isinstance(plan.implementation_order[0], str)
        assert isinstance(plan.implementation_order[1], ImplementationPhase)
        assert plan.implementation_order[1].phase == "Core Logic"
        assert isinstance(plan.implementation_order[2], str)

    def test_mixed_risk_areas(self):
        plan = EngineeringPlan(
            strategy="A comprehensive modular implementation approach using layered architecture",
            implementation_order=["Phase 1: scaffolding"],
            risk_areas=[
                "Third-party API rate limits",
                RiskAreaDetail(
                    area="Database migration",
                    risk="Data loss during schema change",
                    mitigation="Use expand-contract pattern",
                ),
            ],
            testing_strategy="Run pytest with full coverage and integration tests",
        )
        assert isinstance(plan.risk_areas[0], str)
        assert isinstance(plan.risk_areas[1], RiskAreaDetail)
        assert plan.risk_areas[1].area == "Database migration"

    def test_pure_string_implementation_order_backward_compat(self):
        plan = EngineeringPlan(
            strategy="A comprehensive modular implementation approach using layered architecture",
            implementation_order=["Step 1", "Step 2", "Step 3"],
            testing_strategy="Run pytest with full coverage and integration tests",
        )
        assert all(isinstance(s, str) for s in plan.implementation_order)

    def test_pure_string_risk_areas_backward_compat(self):
        plan = EngineeringPlan(
            strategy="A comprehensive modular implementation approach using layered architecture",
            implementation_order=["Step 1"],
            risk_areas=["Risk A", "Risk B"],
            testing_strategy="Run pytest with full coverage and integration tests",
        )
        assert all(isinstance(s, str) for s in plan.risk_areas)

    def test_implementation_order_from_raw_dicts(self):
        plan = EngineeringPlan(
            strategy="A comprehensive modular implementation approach using layered architecture",
            implementation_order=[
                "Setup step",
                {"phase": "Build", "tasks": ["TASK-1"], "dependencies": []},
            ],
            testing_strategy="Run pytest with full coverage and integration tests",
        )
        assert isinstance(plan.implementation_order[0], str)
        assert isinstance(plan.implementation_order[1], ImplementationPhase)

    def test_risk_areas_from_raw_dicts(self):
        plan = EngineeringPlan(
            strategy="A comprehensive modular implementation approach using layered architecture",
            implementation_order=["Step 1"],
            risk_areas=[
                {"area": "Perf", "risk": "Latency spike under load"},
            ],
            testing_strategy="Run pytest with full coverage and integration tests",
        )
        assert isinstance(plan.risk_areas[0], RiskAreaDetail)


# ---------------------------------------------------------------------------
# End-to-end: validate_artifact_file
# ---------------------------------------------------------------------------

class TestEndToEndValidation:
    def test_architecture_with_list_data_flow_and_dir_structure(self, tmp_path):
        artifact = {
            "components": [
                {
                    "name": "API Server",
                    "responsibility": "Handle HTTP requests and route to handlers",
                    "interfaces": ["REST /api/v1"],
                    "dependencies": ["Database"],
                }
            ],
            "data_flow": [
                {"from": "Client", "to": "API Server", "data": "HTTP request", "trigger": "user action"},
                {"from": "API Server", "to": "Database", "data": "SQL query"},
            ],
            "tech_decisions": [
                {
                    "decision": "Use FastAPI",
                    "rationale": "Async support and auto-generated OpenAPI docs",
                    "alternatives_considered": ["Flask", "Django"],
                }
            ],
            "constraints": ["Must run on Python 3.11+"],
            "directory_structure": ["src/main.py", "src/routes/api.py", "tests/test_api.py"],
        }
        artifact_path = tmp_path / "architecture.json"
        artifact_path.write_text(json.dumps(artifact))

        result = validate_artifact_file(artifact_path, "architecture")
        assert result.valid, f"Expected valid but got errors: {result.errors}"

    def test_engineering_plan_with_dict_testing_and_mixed_order(self, tmp_path):
        artifact = {
            "strategy": "Implement in three phases: scaffolding, core logic, and integration testing layer",
            "implementation_order": [
                "Set up project structure and dependencies",
                {
                    "phase": "Core Implementation",
                    "description": "Build business logic",
                    "tasks": ["TASK-1", "TASK-2"],
                    "dependencies": [],
                },
                "Final integration tests",
            ],
            "risk_areas": [
                "External API availability",
                {
                    "area": "Schema migration",
                    "risk": "Potential data loss",
                    "mitigation": "Use blue-green deployment",
                },
            ],
            "testing_strategy": {
                "unit": "Test all service functions with pytest",
                "integration": "Test API endpoints with httpx",
                "e2e": "Test full flows with playwright",
            },
        }
        artifact_path = tmp_path / "engineering_plan.json"
        artifact_path.write_text(json.dumps(artifact))

        result = validate_artifact_file(artifact_path, "engineering_plan")
        assert result.valid, f"Expected valid but got errors: {result.errors}"

        # Verify the file was rewritten with coerced values
        rewritten = json.loads(artifact_path.read_text())
        assert isinstance(rewritten["testing_strategy"], str)
        assert "Unit:" in rewritten["testing_strategy"]
