# AI SDLC Orchestrator -- Usage Guide

> Coordinate AI agents through the full software development lifecycle: plan, implement, test, review, and release.

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Installation](#2-installation)
3. [Using in a Project](#3-using-in-a-project)
4. [CLI Reference](#4-cli-reference)
5. [Execution Modes](#5-execution-modes)
6. [Built-in Workflows](#6-built-in-workflows)
7. [Custom Workflows](#7-custom-workflows)
8. [Agent Roles](#8-agent-roles)
9. [Parallel Execution & DAG Scheduling](#9-parallel-execution--dag-scheduling)
10. [Artifact Pipeline](#10-artifact-pipeline)
11. [Configuration](#11-configuration)
12. [State Management & Resume](#12-state-management--resume)
13. [Review Feedback Loop](#13-review-feedback-loop)
14. [Budget & Cost Tracking](#14-budget--cost-tracking)
15. [Observability & Logging](#15-observability--logging)
16. [Progress Tracking](#16-progress-tracking)
17. [Workspace Layout](#17-workspace-layout)
18. [Common Recipes](#18-common-recipes)
19. [Troubleshooting](#19-troubleshooting)

---

## 1. Quick Start

```bash
# See what would happen without calling any agents
orchestrate --dry-run "Build a REST API for user management"

# Run the full feature development pipeline
orchestrate "Build a REST API for user management"

# Fix a bug with the bugfix workflow
orchestrate --workflow bugfix "Fix the login timeout on /auth endpoint"
```

The `orchestrate` command takes a plain-English feature request and runs an AI agent pipeline that produces requirements, architecture, implementation, tests, and a code review.

---

## 2. Installation

**Prerequisites:** Python 3.11+, Claude Agent SDK (or `claude` CLI as fallback).

```bash
# Clone and install
git clone <repo-url>
cd Orchestrator
pip install -e ".[dev]"

# Verify
orchestrate --help
```

---

## 3. Using in a Project

The orchestrator is designed to work **inside your project directory**. It uses your current working directory (`cwd`) as the project root -- agents explore and modify code relative to where you run the command.

### 3.1 Basic Setup

```bash
# 1. Install the orchestrator (one-time, from the orchestrator repo)
cd /path/to/Orchestrator
pip install -e ".[dev]"

# 2. Navigate to YOUR project
cd /path/to/my-webapp

# 3. Run the orchestrator from inside your project
orchestrate "Add user authentication with JWT tokens"
```

That's it. The orchestrator will:
1. Create a `workspace/` directory inside your project for artifacts, logs, and state
2. Agents will read and modify files in your project directory
3. Parallel tasks use git worktrees (so your project must be a git repo)

### 3.2 Project Directory Structure (Before and After)

**Before running:**
```
my-webapp/
  src/
  tests/
  package.json
  ...
```

**After running:**
```
my-webapp/
  src/                        # Your code (modified by agents)
  tests/                      # Your tests (modified by agents)
  package.json
  workspace/                  # Created by orchestrator
    artifacts/                # Planning artifacts (prd.json, architecture.json, etc.)
    logs/                     # Run logs
    state.json                # Run state (for resume)
  ...
```

### 3.3 Custom Workspace Location

If you don't want `workspace/` inside your project, override it in a config file:

```yaml
# my-orchestrator-config.yaml
workspace_dir: /tmp/orchestrator-runs/my-webapp
max_budget_usd: 50.0
```

```bash
cd /path/to/my-webapp
orchestrate --config ./my-orchestrator-config.yaml "Add dark mode"
```

### 3.4 Adding to .gitignore

Add the workspace directory to your project's `.gitignore` so orchestrator artifacts don't get committed:

```gitignore
# AI SDLC Orchestrator
workspace/
```

### 3.5 Existing Project vs New Project

**Existing project:** Agents read your existing codebase to understand the project structure, then make changes consistent with your existing patterns. The Architect and Engineers explore your repo before writing code.

```bash
cd my-existing-app
orchestrate "Add a /api/v2/users endpoint following existing API patterns"
```

**New project from scratch:** Agents create the full project structure. Start with an empty git repo:

```bash
mkdir my-new-app && cd my-new-app
git init
orchestrate "Build a todo app with React frontend and Express backend"
```

### 3.6 Git Requirement

Your project must be a **git repository** for parallel execution to work. Parallel implementation tasks each run in an isolated git worktree to prevent file conflicts.

```bash
# If your project isn't a git repo yet
cd my-project
git init
git add -A && git commit -m "Initial commit"

# Now you can run the orchestrator
orchestrate "Add feature X"
```

### 3.7 Step-by-Step Example: Adding a Feature to an Existing App

```bash
# 1. Go to your project
cd ~/projects/my-ecommerce-app

# 2. Make sure you're on a clean branch
git checkout -b feature/shopping-cart

# 3. Dry run first to see the plan
orchestrate --dry-run "Add a shopping cart with add/remove items and checkout"

# 4. Run for real
orchestrate "Add a shopping cart with add/remove items and checkout"

# 5. Review the generated artifacts
cat workspace/artifacts/prd.json      # Requirements
cat workspace/artifacts/tasks.json    # Task breakdown

# 6. Review the code changes
git diff

# 7. If something failed, resume
orchestrate --resume "Add a shopping cart with add/remove items and checkout"

# 8. Validate all artifacts
orchestrate validate ./workspace/artifacts
```

### 3.8 Running Different Workflows on the Same Project

You can run different workflows for different needs:

```bash
cd my-project

# Add a new feature
orchestrate --workflow feature "Add user profiles"

# Fix a bug (creates a fresh workspace/state.json)
orchestrate --workflow bugfix "Fix crash when profile image is missing"

# Audit security
orchestrate --workflow security "Audit the authentication module"

# Optimize performance
orchestrate --workflow perf "Optimize database queries for the /search endpoint"
```

> **Note:** Each run overwrites `workspace/state.json`. If you need to preserve state from a previous run, copy `workspace/` before starting a new run, or use a different `workspace_dir` in a custom config.

---

## 4. CLI Reference

### Command Syntax

```
orchestrate <feature_request> [flags]
orchestrate validate <artifacts_dir>
```

### Positional Arguments

| Argument | Description |
|----------|-------------|
| `feature_request` | Plain-English description of the feature or task. Required unless using `validate`. |
| `validate_dir` | Path to artifacts directory. Only used with `orchestrate validate`. |

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--dry-run` | bool | `false` | Print prompts without calling agents. No cost incurred. |
| `--resume` | bool | `false` | Resume from last saved state, skipping completed phases. |
| `--phase PHASE` | string | none | Run a single legacy phase: `pm`, `architect`, `engineer`, `qa`, or `reviewer`. |
| `--from-phase PHASE` | string | none | Start from this legacy phase, skipping earlier ones. |
| `--workflow TYPE` | string | none | Select a workflow: `feature`, `bugfix`, `refactor`, `perf`, or `security`. |
| `--workflow-file PATH` | path | none | Path to a custom workflow definition file. |
| `--config PATH` | path | `config/default.yaml` | Path to a config YAML file. |
| `--log-format` | choice | `console` | Log output format: `console` (pretty) or `json` (structured). |

---

## 5. Execution Modes

The orchestrator has two execution modes. It automatically picks the right one based on which flags you use.

### 5.1 Workflow Mode (Default)

Used when you run a plain command or use `--workflow`. This is the recommended mode. It supports:

- 5 built-in workflow templates with specialized steps
- Parallel task execution with dependency-aware scheduling
- Approval gates before release
- Failure routing (e.g., send back to Implementation on a failed review)

```bash
# Uses the default workflow (feature_development)
orchestrate "Add dark mode to the dashboard"

# Explicitly select a workflow
orchestrate --workflow bugfix "Fix memory leak in worker pool"
orchestrate --workflow security "Audit authentication flows"
```

### 5.2 Legacy Mode

Triggered when you use `--phase` or `--from-phase`. Runs the original fixed 5-phase pipeline:

```
pm --> architect --> engineer --> qa --> reviewer
```

Useful for running a single phase in isolation or starting from a specific phase.

```bash
# Run only the PM phase
orchestrate --phase pm "Build a todo app"

# Skip PM and Architect, start from engineer
orchestrate --from-phase engineer "Build a todo app"
```

> **Note:** Legacy mode does not support workflow-level DAG scheduling, approval gates, or failure routing.

---

## 6. Built-in Workflows

### 6.1 Feature Development (8 steps)

The default workflow. Takes a feature request from requirements all the way to release.

```
PRD --> Architecture --> Engineering Plan --> Task Breakdown --> Implementation --> Code Review --> QA --> Release
                                                                  (parallel)                         (approval gate)
```

| Step | Agent | Inputs | Outputs | Notes |
|------|-------|--------|---------|-------|
| PRD | Product Manager | -- | `prd` | |
| Architecture | Software Architect | `prd` | `architecture` | |
| Engineering Plan | Principal Engineer | `prd`, `architecture` | `engineering_plan`, `tasks` | |
| Task Breakdown | Technical Project Manager | `prd`, `architecture`, `engineering_plan` | `tasks` | Refines tasks into granular units |
| Implementation | Backend Engineer | `prd`, `architecture`, `tasks` | -- | **Parallel.** Each task dispatched to its assigned role. |
| Code Review | Backend Code Reviewer | `prd`, `architecture`, `tasks` | `review` | On fail: routes back to Implementation |
| QA | QA Executor | `prd`, `tasks` | `qa_report` | On fail: routes back to Implementation |
| Release | DevOps Engineer | `qa_report`, `review` | -- | **Requires approval** before executing |

```bash
orchestrate "Build a REST API for user management"
# or explicitly:
orchestrate --workflow feature "Build a REST API for user management"
```

### 6.2 Bugfix (6 steps)

Streamlined for fixing bugs. Starts with analysis instead of requirements gathering.

```
Bug Analysis --> Root Cause --> Fix Plan --> Implementation --> Code Review --> QA
                                              (parallel)
```

| Step | Agent | Notes |
|------|-------|-------|
| Bug Analysis | QA Planner | Analyzes the bug and produces a report |
| Root Cause | Principal Engineer | Identifies the root cause |
| Fix Plan | Software Architect | Plans the fix and creates tasks |
| Implementation | Backend Engineer | **Parallel.** Executes fix tasks. |
| Code Review | Backend Code Reviewer | On fail: back to Implementation |
| QA | QA Executor | On fail: back to Implementation |

```bash
orchestrate --workflow bugfix "Fix the login timeout on /auth endpoint"
orchestrate --workflow bug "Fix the login timeout on /auth endpoint"
```

### 6.3 Refactor (6 steps)

For restructuring existing code without changing behavior.

```
Scope Analysis --> Architecture Review --> Task Breakdown --> Implementation --> Code Review --> QA
                                                               (parallel)
```

| Step | Agent | Notes |
|------|-------|-------|
| Scope Analysis | Principal Engineer | Defines refactor boundaries |
| Architecture Review | Software Architect | Reviews current and target architecture |
| Task Breakdown | Technical Project Manager | Creates granular refactor tasks |
| Implementation | Backend Engineer | **Parallel.** |
| Code Review | Backend Code Reviewer | On fail: back to Implementation |
| QA | QA Executor | On fail: back to Implementation |

```bash
orchestrate --workflow refactor "Extract payment processing into a separate service"
```

### 6.4 Performance Optimization (6 steps)

Starts with profiling and ends with benchmarking to measure improvement.

```
Profiling --> Bottleneck Analysis --> Optimization Plan --> Implementation --> Benchmarking --> Code Review
                                                             (parallel)
```

| Step | Agent | Notes |
|------|-------|-------|
| Profiling | Caching & Performance Engineer | Produces baseline `benchmark_report` |
| Bottleneck Analysis | Principal Engineer | Identifies hotspots |
| Optimization Plan | Software Architect | Plans optimizations and creates tasks |
| Implementation | Caching & Performance Engineer | **Parallel.** |
| Benchmarking | Caching & Performance Engineer | Compares before/after. On fail: back to Implementation |
| Code Review | Backend Code Reviewer | On fail: back to Implementation |

```bash
orchestrate --workflow perf "Reduce /dashboard API response time from 2s to 200ms"
orchestrate --workflow performance "Reduce /dashboard API response time from 2s to 200ms"
```

### 6.5 Security Audit (5 steps)

Security-focused workflow with threat modeling and vulnerability verification.

```
Threat Model --> Code Scan --> Fix Plan --> Implementation --> Verification
                                             (parallel)
```

| Step | Agent | Notes |
|------|-------|-------|
| Threat Model | Security Engineer | Produces `threat_model` |
| Code Scan | Security Engineer | Scans for vulnerabilities, produces `vulnerability_report` |
| Fix Plan | Software Architect | Plans remediations |
| Implementation | Backend Engineer | **Parallel.** |
| Verification | Security Engineer | Re-scans to verify fixes. On fail: back to Implementation |

```bash
orchestrate --workflow security "Audit authentication and authorization flows"
```

### 6.6 Workflow Aliases

Multiple aliases map to the same workflow for convenience:

| Alias | Workflow |
|-------|---------|
| `feature`, `feature_development` | Feature Development |
| `bugfix`, `bug` | Bugfix |
| `refactor` | Refactor |
| `perf`, `performance`, `performance_optimization` | Performance Optimization |
| `security`, `security_audit` | Security Audit |

---

## 7. Custom Workflows

Define your own workflow by creating a text file with `STEP:` blocks.

### 7.1 Step Definition Format

```
STEP: <Step Name>
  agent: <role name>
  inputs: <comma-separated artifact names>
  outputs: <comma-separated artifact names>
  next: <next step name>
  parallel: true|false
  on_fail: <step name or "escalate">
  gate: approval
  max_retries: <integer>
```

### 7.2 Step Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `agent` | Yes | -- | The agent role to execute this step (see table below) |
| `inputs` | No | `[]` | Artifacts that must exist before this step runs |
| `outputs` | No | `[]` | Artifacts this step is expected to produce |
| `next` | No | none | Name of the next step. Omit for the final step. |
| `parallel` | No | `false` | Run tasks within this step in parallel |
| `on_fail` | No | `escalate` | Step to jump back to on failure, or `escalate` to stop |
| `gate` | No | none | Set to `approval` to require human approval before executing |
| `max_retries` | No | `3` | Number of retries before giving up |

### 7.3 Available Agent Roles

Use these strings (case-insensitive) in the `agent:` field:

| Role String | Agent |
|-------------|-------|
| `Product Manager` | Product Manager |
| `Software Architect` | Software Architect |
| `Principal Engineer` | Principal Engineer |
| `Technical Project Manager` | Technical Project Manager |
| `Frontend Engineer` | Frontend Engineer |
| `Backend Engineer` | Backend Engineer |
| `Database Engineer` | Database Engineer |
| `Caching & Performance Engineer` or `Caching Engineer` | Caching & Performance Engineer |
| `Backend Code Reviewer` | Backend Code Reviewer |
| `Frontend Code Reviewer` | Frontend Code Reviewer |
| `QA Planner` or `QA Engineer (Planner)` | QA Planner |
| `QA Executor` or `QA Engineer (Executor)` | QA Executor |
| `Automation Engineer` | Automation Engineer |
| `DevOps Engineer` | DevOps Engineer |
| `Security Engineer` | Security Engineer |
| `Observability Engineer` | Observability Engineer |
| `Documentation Engineer` | Documentation Engineer |
| `Git Manager` | Git Manager |

### 7.4 Example: Documentation Refresh Workflow

Create a file `workflows/doc-refresh.txt`:

```
STEP: Content Audit
  agent: Documentation Engineer
  inputs:
  outputs: prd
  next: Architecture Review

STEP: Architecture Review
  agent: Software Architect
  inputs: prd
  outputs: architecture
  next: Writing

STEP: Writing
  agent: Documentation Engineer
  inputs: prd, architecture
  outputs:
  next: Review
  parallel: true

STEP: Review
  agent: Frontend Code Reviewer
  inputs: prd, architecture
  outputs: review
  on_fail: Writing
```

Run it:

```bash
orchestrate --workflow-file ./workflows/doc-refresh.txt "Update all API documentation"
```

---

## 8. Agent Roles

The orchestrator has 18 specialist agent roles grouped by access level.

### 8.1 Planning Roles (Read-Only)

These agents analyze and plan but do not modify code.

| Role | Responsibility |
|------|---------------|
| Product Manager | Requirements, PRD, user value, acceptance criteria |
| Software Architect | System design, service boundaries, tech decisions, component interfaces |
| Principal Engineer | Translates architecture into engineering strategy and implementation plan |
| Technical Project Manager | Breaks work into tiny, independent, precisely-scoped tasks |
| QA Planner | Designs test strategy, edge cases, coverage plan |
| Security Engineer | Security review of architecture and code |

### 8.2 Implementation Roles (Read-Write)

These agents can read and write code.

| Role | Responsibility |
|------|---------------|
| Frontend Engineer | Implements frontend tasks (components, UI, accessibility) |
| Backend Engineer | Implements backend tasks (APIs, services, logic) |
| Database Engineer | Schema design, migrations, indexing, query optimization |
| Caching & Performance Engineer | Caching strategy, performance optimization |
| Automation Engineer | Builds automated test suites and CI pipelines |
| DevOps Engineer | CI/CD, containerization, deployment |
| Observability Engineer | Logging, monitoring, metrics |
| Documentation Engineer | Technical docs and developer guides |
| Git Manager | Branch management, staging, committing, merging worktree results |

### 8.3 Review Roles (Read-Only)

These agents evaluate code but do not modify it.

| Role | Responsibility |
|------|---------------|
| Backend Code Reviewer | Reviews backend code for correctness, security, performance |
| Frontend Code Reviewer | Reviews frontend code for UI correctness, accessibility, component architecture |
| QA Executor | Executes tests, reports failures |

---

## 9. Parallel Execution & DAG Scheduling

Steps marked `parallel: true` (like Implementation) use a dependency-aware scheduler to run tasks concurrently.

### 9.1 How Dependency Waves Work

When a parallel step executes, the orchestrator:

1. Loads all tasks from `artifacts/tasks.json`
2. Builds a dependency graph from each task's `dependencies` field
3. Computes **execution waves** using topological sort (Kahn's algorithm)
4. Executes waves sequentially -- all tasks within a wave run in parallel

```
Example: 5 tasks with dependencies

TASK-001: no deps            --> Wave 1  (2 agents spawn)
TASK-002: no deps            --> Wave 1
TASK-003: depends on 001     --> Wave 2  (2 agents spawn)
TASK-004: depends on 001     --> Wave 2
TASK-005: depends on 003,004 --> Wave 3  (1 agent spawns)

Total: 3 waves, spawning [2, 2, 1] agents respectively
```

With 20 tasks, the scheduler will **not** spawn 20 agents at once. It groups them into waves based on dependencies, only spawning agents for tasks whose dependencies have completed.

### 9.2 File Conflict Detection

Within each wave, tasks are further partitioned:

- **Non-conflicting tasks** (modifying different files): run in parallel
- **Conflicting tasks** (modifying the same file): run sequentially

This prevents merge conflicts without serializing everything.

### 9.3 Git Worktree Isolation

Each parallel task runs in an isolated git worktree (`isolation: worktree`), giving it its own filesystem copy. Changes are merged back after completion.

### 9.4 Failure Handling

If a task in a wave fails, all downstream tasks that depend on it are marked `BLOCKED` and will not execute.

---

## 10. Artifact Pipeline

Agents communicate through validated JSON artifacts stored in `workspace/artifacts/`.

### 10.1 Artifact Types

| Artifact | File | Produced By | Key Fields |
|----------|------|-------------|------------|
| PRD | `prd.json` | Product Manager | `title`, `overview`, `goals`, `requirements` (REQ-NNN), `acceptance_criteria` |
| Architecture | `architecture.json` | Software Architect | `components`, `data_flow`, `tech_decisions`, `constraints` |
| Tasks | `tasks.json` | Architect / TPM | `tasks[]` with TASK-NNN IDs, `assigned_role`, `dependencies`, `files_to_modify` |
| Engineering Plan | `engineering_plan.json` | Principal Engineer | `strategy`, `implementation_order`, `risk_areas`, `testing_strategy` |
| QA Report | `qa_report.json` | QA Executor | `test_results` (passed/failed/skipped), `issues[]`, `verdict` (pass/fail) |
| Review | `review.json` | Code Reviewer | `verdict` (approve/reject/request_changes), `issues[]`, `summary` |
| Threat Model | `threat_model.json` | Security Engineer | `threats[]` (THREAT-NNN), `attack_surface`, `recommendations` |
| Benchmark Report | `benchmark_report.json` | Caching Engineer | `results[]` (metric, before, after, improvement_pct), `bottlenecks` |
| Vulnerability Report | `vulnerability_report.json` | Security Engineer | `vulnerabilities[]`, `scan_tools_used`, `summary` |

### 10.2 Two-Layer Validation

Every artifact is validated twice before a step is considered complete:

1. **JSON Schema** (structural) -- checks field presence, types, patterns, and array lengths. Schemas are in `src/schemas/`.
2. **Pydantic model** (semantic) -- checks constraints like minimum string lengths, ID patterns (`REQ-\d+`, `TASK-\d+`), and enum values.

If validation fails, the agent is retried.

### 10.3 Validate Artifacts Manually

Check artifacts from a previous run:

```bash
orchestrate validate ./workspace/artifacts
```

Output:
```
OK   prd.json
OK   architecture.json
FAIL tasks.json
     Field 'tasks[2].task_id' does not match pattern '^TASK-\d+$'
```

---

## 11. Configuration

### 11.1 Config File Structure

The default config lives at `config/default.yaml`:

```yaml
# Top-level settings
workspace_dir: workspace          # Where artifacts, logs, and state are saved
max_review_cycles: 3              # Max review feedback loops before escalation
max_budget_usd: 50.0              # Spend limit across all agents
default_workflow: feature_development  # Workflow when --workflow is not specified

# Legacy phase configuration
phases:
  pm:
    agent: pm
    parallel: false
    max_retries: 2
    timeout_minutes: 15
  architect:
    agent: architect
    parallel: false
    max_retries: 2
    timeout_minutes: 20
  engineer:
    agent: engineer
    parallel: true
    max_retries: 2
    timeout_minutes: 30
  qa:
    agent: qa
    parallel: false
    max_retries: 1
    timeout_minutes: 15
  reviewer:
    agent: reviewer
    parallel: false
    max_retries: 1
    timeout_minutes: 15

# Agent configuration (all 17 roles)
agents:
  pm:
    name: Product Manager
    model: sonnet              # Model tier: sonnet or haiku
    max_turns: 30              # Max conversation turns per invocation
    escalation_model: null     # Model to escalate to on failure (optional)
    input_artifacts: []        # Artifacts the agent reads
    output_artifacts: [prd]    # Artifacts the agent must produce
  backend_engineer:
    name: Backend Engineer
    model: sonnet
    max_turns: 80
    escalation_model: sonnet
    input_artifacts: [prd, architecture, tasks]
    output_artifacts: []
  # ... (all 17 agents configured)
```

### 11.2 Key Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `workspace_dir` | `workspace` | Root directory for all run outputs |
| `max_review_cycles` | `3` | Maximum review-fix-review loops |
| `max_budget_usd` | `50.0` | Hard budget cap across all agent invocations |
| `default_workflow` | `feature_development` | Workflow used when `--workflow` is not specified |

### 11.3 Using a Custom Config

```bash
# Override any setting via a custom YAML
orchestrate --config ./my-config.yaml "Build a todo app"
```

Example custom config for a high-budget run:

```yaml
workspace_dir: workspace
max_review_cycles: 5
max_budget_usd: 200.0
default_workflow: feature_development
phases: {}
agents: {}
```

---

## 12. State Management & Resume

### 12.1 How State Works

After each step completes, the orchestrator saves its full state to `workspace/state.json`. This includes:

- `run_id` -- unique identifier for the run
- `workflow_type` -- which workflow is being executed
- `phases` -- status, cost, and errors for each completed step
- `completed_steps` -- list of step names that finished successfully
- `total_cost_usd` -- cumulative cost so far

### 12.2 Resuming a Run

If a run fails (e.g., an agent errors out or budget runs out), resume from where it left off:

```bash
# First run fails at the Implementation step
orchestrate "Build a todo app"
# ... Implementation fails ...

# Resume -- completed steps are skipped
orchestrate --resume "Build a todo app"
```

The engine reloads `state.json`, skips all completed steps, and continues from the first incomplete step.

### 12.3 Resume with Different Settings

You can change config when resuming (e.g., increase budget):

```bash
orchestrate --resume --config high-budget.yaml "Build a todo app"
```

---

## 13. Review Feedback Loop

When a Code Review or QA step returns a non-passing verdict, the workflow routes back to Implementation via `on_fail`:

```
Implementation --> Code Review --+--(approved)--> QA --> Release
                                 |
                                 +--(rejected)--+
                                                |
                     (re-implement with feedback)
                                                |
                Implementation <----------------+
                      |
                      +--> Code Review (cycle 2) ...
```

- **Maximum cycles:** Controlled by `max_review_cycles` (default: 3)
- **Context preserved:** Previous review feedback is passed to the engineer on each retry
- **Escalation:** If max cycles are reached without approval, the run escalates (stops and reports)

---

## 14. Budget & Cost Tracking

Every agent invocation reports its cost. The orchestrator tracks cumulative spend.

| Threshold | Behavior |
|-----------|----------|
| < 80% of `max_budget_usd` | Normal execution |
| >= 80% | Warning logged |
| >= 100% | **Hard stop.** Pipeline halts immediately. |

Default budget: `$50.00`. Override in config:

```yaml
max_budget_usd: 200.0
```

The total cost is shown in the summary table after each run and saved in `state.json`.

---

## 15. Observability & Logging

### 15.1 Run Logs

Every run produces a structured log file:

```
workspace/logs/run-{run_id}.jsonl
```

Each line is a JSON object with a timestamp and event data:

```json
{"ts": "2025-01-15T10:30:00Z", "run_id": "abc123", "event": "agent_invoke", "agent": "pm", "model": "sonnet"}
{"ts": "2025-01-15T10:31:15Z", "run_id": "abc123", "event": "agent_result", "agent": "pm", "success": true, "cost_usd": 0.12}
```

**Event types:**

| Event | Description |
|-------|-------------|
| `run_start` | Pipeline started with feature request and config |
| `agent_invoke` | An agent was called (name, model, attempt) |
| `agent_result` | Agent finished (success, cost, turns used) |
| `budget_warning` | Cost exceeded 80% of budget |
| `run_complete` | Pipeline finished (total cost, phase results) |

### 15.2 JSON Console Logs

For CI/CD pipelines or log aggregation:

```bash
orchestrate --log-format json "Build a todo app" 2>&1 | tee run.log
```

### 15.3 Reading Logs

```bash
# Pretty-print the log from the last run
cat workspace/logs/run-*.jsonl | python -m json.tool
```

---

## 16. Progress Tracking

During execution, the orchestrator displays a real-time progress panel:

- **Workflow progress bar** -- percentage of steps completed
- **Step list** -- visual status for each step:
  - `[done]` completed
  - `[>>]` in progress
  - `[..]` pending
  - `[FAIL]` failed
  - `[BLOCKED]` blocked by failed dependency
- **Task progress** -- within the current step, shows completed/total tasks
- **ETA** -- estimated time remaining based on average task completion time
- **Budget** -- cumulative cost spent

The progress display updates after each step completion, every 3rd task completion, and on failures or retries.

---

## 17. Workspace Layout

After a run, the workspace directory contains:

```
workspace/
  artifacts/
    prd.json                    # Product Requirements Document
    architecture.json           # System architecture
    tasks.json                  # Task breakdown with dependencies
    engineering_plan.json       # Engineering strategy (feature workflow)
    qa_report.json              # QA test results
    review.json                 # Code review verdict
    threat_model.json           # Threat model (security workflow)
    benchmark_report.json       # Performance benchmarks (perf workflow)
    vulnerability_report.json   # Vulnerability scan (security workflow)
  logs/
    run-{run_id}.jsonl          # Structured event log for this run
  state.json                    # Current run state (for resume)
```

Not every artifact is produced by every workflow. For example, `threat_model.json` is only created by the Security Audit workflow, and `benchmark_report.json` only by Performance Optimization.

---

## 18. Common Recipes

### Preview a workflow (no cost)

```bash
orchestrate --dry-run "Build a REST API"
orchestrate --dry-run --workflow bugfix "Fix the crash on empty input"
orchestrate --dry-run --workflow-file ./my-workflow.txt "Custom task"
```

### Select a specific workflow

```bash
orchestrate --workflow feature "Add user authentication"
orchestrate --workflow bugfix "Fix null pointer in parser"
orchestrate --workflow refactor "Extract auth into middleware"
orchestrate --workflow perf "Optimize search queries"
orchestrate --workflow security "Audit payment module"
```

### Run a single legacy phase

```bash
orchestrate --phase pm "Design a notification system"
orchestrate --phase architect "Design a notification system"
orchestrate --phase qa "Build a todo app"
```

### Start from a specific phase

```bash
# Skip PM and Architect, run from engineer onward
orchestrate --from-phase engineer "Build a todo app"

# Run only QA and Reviewer
orchestrate --from-phase qa "Build a todo app"
```

### Resume a failed run

```bash
orchestrate --resume "Build a todo app"

# Resume with a higher budget
orchestrate --resume --config high-budget.yaml "Build a todo app"
```

### Use a custom workflow file

```bash
orchestrate --workflow-file ./workflows/data-migration.txt "Migrate user data to new schema"
```

### JSON logs for CI/CD

```bash
orchestrate --log-format json "Build a REST API" 2>&1 | tee run.log
orchestrate --workflow perf --log-format json "Optimize database queries"
```

### Security audit with custom config

```bash
orchestrate --workflow security --config strict-security.yaml "Audit the payment module"
```

### Validate artifacts from a previous run

```bash
orchestrate validate ./workspace/artifacts
orchestrate validate ./other-project/artifacts
```

### Dry run then real run

```bash
# First, preview the prompts
orchestrate --dry-run --workflow refactor "Extract auth into middleware"

# Looks good -- run for real
orchestrate --workflow refactor "Extract auth into middleware"
```

### Combine multiple flags

```bash
# Custom config + specific workflow + JSON logs
orchestrate --workflow bugfix --config team-config.yaml --log-format json "Fix login bug"

# Resume a security audit with JSON logs
orchestrate --resume --workflow security --log-format json "Audit auth flows"
```

---

## 19. Troubleshooting

### "Budget exceeded"

The run hit `max_budget_usd`. Increase it in your config:

```yaml
max_budget_usd: 200.0
```

Then resume: `orchestrate --resume --config high-budget.yaml "..."`

### "Missing required input artifact"

A step expected an artifact that doesn't exist (e.g., `architecture.json` missing before Implementation). Check `workspace/artifacts/` -- the previous step may have failed validation. Look at the run log for details.

### "Artifact validation failed"

The agent produced malformed JSON. The orchestrator will retry automatically (up to `max_retries`). If it keeps failing, check the log to see what the agent produced and consider adjusting the prompt or model tier.

### "Max review cycles reached"

Code did not pass review after 3 cycles. Check `workspace/artifacts/review.json` for the reviewer's feedback. You can increase `max_review_cycles` in config and resume.

### "Unknown workflow type"

Check the available aliases: `feature`, `bugfix`, `bug`, `refactor`, `perf`, `performance`, `security`, `security_audit`, `feature_development`, `performance_optimization`.

### "Unknown phase"

Legacy phase names are: `pm`, `architect`, `engineer`, `qa`, `reviewer`. These only work with `--phase` or `--from-phase`.

### Agent SDK not found

If `claude_agent_sdk` is not installed, the orchestrator falls back to the `claude` CLI. Ensure either the SDK or the CLI is available in your PATH.

### Run seems stuck

Check the run log for the latest event:

```bash
tail -5 workspace/logs/run-*.jsonl
```

The last event will show which agent is currently running and how long it has been active.
