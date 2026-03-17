"""Structured error codes and exception hierarchy for the orchestrator."""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    ARTIFACT_VALIDATION_FAILED = "ARTIFACT_VALIDATION_FAILED"
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    AGENT_CRASHED = "AGENT_CRASHED"
    MAX_RETRIES_EXHAUSTED = "MAX_RETRIES_EXHAUSTED"
    MISSING_INPUT_ARTIFACT = "MISSING_INPUT_ARTIFACT"
    WORKFLOW_STEP_FAILED = "WORKFLOW_STEP_FAILED"
    CIRCULAR_DEPENDENCY = "CIRCULAR_DEPENDENCY"
    FILE_CONFLICT = "FILE_CONFLICT"
    CONFIGURATION_INVALID = "CONFIGURATION_INVALID"
    UNKNOWN = "UNKNOWN"


class OrchestratorError(Exception):
    """Base exception with a structured error code."""

    def __init__(self, message: str, error_code: ErrorCode = ErrorCode.UNKNOWN) -> None:
        super().__init__(message)
        self.error_code = error_code


class BudgetExceededError(OrchestratorError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.BUDGET_EXCEEDED)


class ArtifactValidationError(OrchestratorError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.ARTIFACT_VALIDATION_FAILED)


class AgentTimeoutError(OrchestratorError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.AGENT_TIMEOUT)


class AgentCrashedError(OrchestratorError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.AGENT_CRASHED)


class MaxRetriesExhaustedError(OrchestratorError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.MAX_RETRIES_EXHAUSTED)


class MissingInputArtifactError(OrchestratorError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.MISSING_INPUT_ARTIFACT)


class WorkflowStepFailedError(OrchestratorError):
    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.WORKFLOW_STEP_FAILED)


class ConfigurationError(OrchestratorError):
    """Raised when the orchestrator YAML configuration is invalid.

    The message always identifies the offending field(s) and expected range so
    users can fix their config without reading a raw Pydantic stack-trace.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.CONFIGURATION_INVALID)
