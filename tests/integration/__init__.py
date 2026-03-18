"""Integration tests for the Orchestrator Mobile API.

These tests validate boundaries between:
  - HTTP clients (Flutter app) ↔ FastAPI mobile backend
  - FastAPI routes ↔ filesystem (state files, JSONL logs, config YAML)
  - WebSocket protocol ↔ JSONL event stream
  - Auth middleware ↔ protected API endpoints
  - Artifacts endpoint ↔ workspace/artifacts/ directory
  - Config endpoint ↔ config YAML file

Test scope deliberately excludes OrchestratorEngine.run() (mocked) and
Flutter Dart code (tested in mobile/test/). Every other boundary is tested
against real implementations.
"""
