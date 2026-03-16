# System Verification Prompt: Cohesion, Correctness & Usefulness Audit

You are auditing the AI SDLC Orchestrator — a Python system that coordinates AI agents through a structured software development lifecycle pipeline. Your job is to perform a rigorous, structured verification across three dimensions: **cohesion**, **correctness**, and **usefulness**. Be brutally honest. Flag real problems, not hypotheticals.

---

## Phase 1: Structural Cohesion

Verify that all parts of the system reference each other consistently and form a unified whole.

### 1.1 Agent ↔ Config Alignment
- For every agent defined in `config/default.yaml` under `agents:`, verify a corresponding `.claude/agents/{name}.md` file exists.
- For every `.claude/agents/*.md` file, verify the agent is registered in `config/default.yaml`.
- Check that model tiers in config (`sonnet`, `opus`, `haiku`) match the `model:` frontmatter in each agent's `.md` file.
- Report any orphaned agents (defined in one place but not the other).

### 1.2 Role Registry ↔ Agent Files
- For every role in `src/orchestrator/roles.py` `ROLE_REGISTRY`, verify the `agent_file` path points to an existing `.claude/agents/*.md` file.
- Verify that `AgentRole` enum members in `models.py` have corresponding entries in `ROLE_REGISTRY`.
- Check for any roles referenced in workflow definitions (`workflows.py`) that don't exist in the registry.

### 1.3 Artifact Schema ↔ Model ↔ Prompt Chain
- For every artifact type in `ARTIFACT_MODELS` (models.py), verify a corresponding `src/schemas/{name}.schema.json` exists.
- For every `.schema.json` file, verify a corresponding Pydantic model exists in `ARTIFACT_MODELS`.
- Verify that the required fields in each JSON schema match the required fields in the corresponding Pydantic model.
- Check that agent prompts (in `phases.py` and agent `.md` files) instruct agents to produce output matching the schema — specifically, verify field names mentioned in prompts align with schema field names.

### 1.4 Workflow ↔ Phase ↔ Agent Wiring
- For each built-in workflow in `workflows.py` (FEATURE_DEVELOPMENT, BUGFIX, REFACTOR, etc.), verify every `agent_role` referenced in workflow steps maps to a valid `AgentRole` enum value.
- Verify that `input_artifacts` referenced in workflow steps are actually produced as `output_artifacts` by a preceding step.
- Check that the legacy `PHASE_ORDER` in `engine.py` is consistent with the phase definitions in `phases.py`.
- Verify `output_artifacts` listed in config match what the prompt builders actually instruct agents to produce.

### 1.5 Import & Dependency Coherence
- Check for circular imports between modules.
- Verify that all cross-module references (e.g., `engine.py` importing from `phases.py`, `workflow_engine.py` importing from `models.py`) use types and functions that actually exist in the referenced module.
- Flag any dead code: functions/classes defined but never imported or called anywhere.

---

## Phase 2: Correctness

Verify that the system will actually work when executed — logic bugs, race conditions, missing error handling.

### 2.1 Workflow Engine Logic
- Trace the dependency resolution in `workflow_engine.py`: does the topological sort correctly compute execution waves? Are there edge cases where a cycle would cause infinite loops or silent hangs?
- Verify file conflict detection in `_partition_by_file_conflicts()`: does it correctly group tasks that share `files_to_modify`? What happens when `files_to_modify` is empty or None?
- Check the retry/escalation logic: when a task fails, does the retry count correctly decrement? Does escalation actually change the model tier?

### 2.2 Artifact Validation Pipeline
- Trace the validation flow in `validation.py`: does it correctly load both JSON schema and Pydantic model for each artifact type?
- What happens if an artifact file contains valid JSON but doesn't match any known artifact type?
- Verify that validation errors actually block phase completion (not just logged and ignored).
- Check if schema file paths are correctly resolved relative to the project root in all execution contexts (installed package vs. dev mode vs. CLI).

### 2.3 Parallel Execution Safety
- In `agents.py` `invoke_agents_parallel()`: is the semaphore correctly limiting concurrency? Are exceptions from one agent properly isolated from others?
- In the engineer phase (`engine.py` `_run_engineer_phase()`): are worktree paths guaranteed unique? What happens if two tasks get assigned the same worktree?
- Is `RunLogger.total_cost` thread-safe? Check if the lock in `observability.py` correctly protects concurrent cost updates.

### 2.4 State Management & Resume
- Verify that `RunState` serialization/deserialization preserves all fields correctly.
- When resuming (`--resume`), does the engine correctly skip completed phases and restart from the right point?
- Check for race conditions: if the process crashes mid-phase, is the state file left in a consistent state?

### 2.5 CLI Argument Handling
- Verify that mutually exclusive options (e.g., `--phase` vs `--resume` vs `--self-orchestrate`) are properly validated.
- Check that `--dry-run` truly prevents all agent invocations (no SDK or CLI calls).
- Verify error messages are helpful when required arguments are missing.

### 2.6 Budget & Cost Tracking
- Trace the budget check flow: when does the system actually check `max_budget_usd`? Is it before each agent call, after, or only at phase boundaries?
- What happens when the budget is exceeded mid-phase? Does it hard-stop or complete the current agent call?
- Verify cost aggregation is correct across parallel agent invocations.

---

## Phase 3: Usefulness

Evaluate whether this system actually solves real problems and delivers value.

### 3.1 Agent Prompt Quality
- Read 5-6 agent `.md` files and evaluate: are the instructions specific enough to produce consistently useful output? Or are they so vague that output quality will be highly variable?
- Check if prompts include concrete examples of expected output format.
- Evaluate whether the prompt instructions align with what the validation schemas actually enforce — i.e., will an agent following the prompt naturally produce schema-valid output?

### 3.2 Workflow Completeness
- For the FEATURE_DEVELOPMENT workflow: does the artifact chain form a complete information pipeline? Is there any phase that receives insufficient context from prior phases to do its job well?
- Are there obvious SDLC steps missing from any workflow? (e.g., does BUGFIX workflow include regression testing?)
- Do the specialist agents (40+) actually get invoked in any workflow, or are they defined but never wired into any execution path?

### 3.3 Failure Modes & Recovery
- What happens when an agent produces garbage output that fails validation? Does the system provide actionable feedback for the retry, or just "validation failed"?
- What happens when an agent exceeds `max_turns` without completing?
- Is there a meaningful distinction between retryable and non-retryable failures?
- How does the system handle partial success (e.g., 3 of 5 engineer tasks succeed)?

### 3.4 Configuration Ergonomics
- Can a user reasonably configure a custom workflow without reading the source code?
- Is the config schema documented anywhere?
- Are default values sensible for a first-time user?

### 3.5 Observability & Debugging
- When something goes wrong, does the log output provide enough information to diagnose the issue?
- Can a user understand what happened during a run by reading the JSONL log?
- Is there any way to inspect intermediate artifacts during a run (not just after completion)?

---

## Output Format

Structure your findings as:

```
## COHESION FINDINGS

### Critical Issues (system will malfunction)
- [C1] Description — files involved — suggested fix

### Warnings (inconsistencies that may cause confusion)
- [W1] Description — files involved — suggested fix

### Verified OK
- [OK] What was checked and found consistent

---

## CORRECTNESS FINDINGS

### Bugs (will cause errors at runtime)
- [B1] Description — file:line — root cause — suggested fix

### Logic Gaps (edge cases not handled)
- [L1] Description — scenario — impact — suggested fix

### Race Conditions / Concurrency Issues
- [R1] Description — scenario — impact — suggested fix

### Verified OK
- [OK] What was checked and found correct

---

## USEFULNESS FINDINGS

### High Impact Issues (significantly reduces value)
- [H1] Description — impact — suggested improvement

### Medium Impact (reduces value but workable)
- [M1] Description — impact — suggested improvement

### Strengths (things done well)
- [S1] Description — why it matters

---

## SUMMARY SCORECARD

| Dimension   | Score (1-10) | Key Issue |
|-------------|-------------|-----------|
| Cohesion    |             |           |
| Correctness |             |           |
| Usefulness  |             |           |
| Overall     |             |           |

## TOP 5 RECOMMENDED FIXES (ordered by impact)
1. ...
2. ...
3. ...
4. ...
5. ...
```

Be specific. Cite file paths and line numbers. Don't pad findings — if something is fine, say "Verified OK" and move on. The goal is to surface the issues that matter most for making this system reliable and useful.
