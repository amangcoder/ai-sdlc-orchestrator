# Performance Optimization Plan — Phase 2 & 3

## Executive Summary

This document outlines the remaining performance optimizations (Phase 2-3) for the orchestrator pipeline. Phase 1 (config changes + artifact caching) is complete. Phase 2 focuses on parallelizing git operations and batching artifact repairs. Phase 3 addresses wave sequencing overhead.

**Expected Total Impact:**
- Phase 1 (COMPLETE): 100-390ms per run
- Phase 2 (PENDING): 35-105 seconds per run
- Phase 3 (PENDING): 10-150 seconds per run
- **Total: 120-240 seconds (2-4 minutes) per pipeline run**

---

## Phase 2: Medium-Effort Optimizations (2-4 hours)

### Bottleneck 002: Serial Git Operations (4.5-15s per wave)

**Current State:**
- Each agent invocation creates a git worktree sequentially
- Worktree operations: `git worktree add` → `git merge` → `git branch -D`
- For 10 parallel agents: 10 agents × 3 git ops = 30 sequential subprocess calls
- Each git operation takes 0.3-0.5s, so 30 ops = 9-15 seconds total

**Location:** `src/orchestrator/agents.py` lines 84-114
- `_create_worktree()` — sequential git subprocess call
- `_merge_worktree()` — sequential git subprocess call
- `_cleanup_worktree()` — 3 sequential git subprocess calls

**Optimization Strategy:**
1. Extract worktree creation into a separate async function `_create_worktree_async()`
2. Batch all worktree creations for a wave using `asyncio.gather()`
3. Defer merge/cleanup to agent completion (already in finally block)
4. Parallelization opportunity: 10 agents can create worktrees in parallel instead of 9-15 seconds sequentially

**Expected Benefit:**
- Baseline: 9-15 seconds (30 sequential subprocess calls)
- Optimized: 1-2 seconds (3 concurrent batches of ~10 calls each)
- **Savings: 4.5-15 seconds per wave**

**Implementation:**
```python
async def _create_worktree_async(project_root: Path, branch_suffix: str) -> tuple[Path, str]:
    """Create a git worktree asynchronously via asyncio."""
    # Use asyncio.create_subprocess_exec instead of subprocess.run
    worktree_dir = Path(tempfile.mkdtemp(prefix=f"orch-wt-{branch_suffix}-"))
    branch_name = f"worktree/{branch_suffix}"

    # Run git subprocess asynchronously
    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "add", "-b", branch_name, str(worktree_dir), "HEAD",
        cwd=project_root, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, "git worktree add")
    return worktree_dir, branch_name

# In invoke_agent():
if invocation.isolation == "worktree" and project_root:
    worktree_dir, branch_name = await _create_worktree_async(project_root, suffix)
    # ... rest of logic
```

**Files to Modify:**
- `src/orchestrator/agents.py` — Add async versions of git operations, update `invoke_agent()`

---

### Bottleneck 005: Serial Artifact Repair (30-90s for 3+ bad artifacts)

**Current State:**
- Missing or invalid artifacts trigger sequential retries
- Each retry is a full agent invocation (30s each)
- Loop at `workflow_engine.py:570-649` checks artifacts → attempts rescue → retries
- For 3 bad artifacts: 3 × 30s = 90 seconds

**Location:** `src/orchestrator/workflow_engine.py` lines 570-649 (artifact retry loop)

**Optimization Strategy:**
1. Batch missing artifacts together
2. Create one compound prompt that repairs all artifacts simultaneously
3. Use parallel spawned agents to fix different artifacts
4. Combine results without nested retries

**Expected Benefit:**
- Baseline: 90 seconds (3 serial agent calls × 30s each)
- Optimized: 30 seconds (3 parallel agent calls via asyncio.gather)
- **Savings: 30-90 seconds when repairs are needed**

**Implementation:**
```python
async def _repair_artifacts_parallel(
    self, missing: list[str], invalid: dict[str, list[str]], workspace: Path
) -> bool:
    """Repair multiple artifacts in parallel using spawned agents."""
    from orchestrator.spawning import execute_spawn_requests, SpawnRequest

    # Create one spawn request per artifact
    requests = []
    for artifact_name in missing + list(invalid.keys()):
        requests.append(SpawnRequest(
            role=step.agent_role,
            prompt=f"Fix artifact {artifact_name}: <specific instructions>",
            reason=f"Repair {artifact_name}"
        ))

    # Spawn all repair agents in parallel
    results = await execute_spawn_requests(
        requests,
        parent_role="orchestrator",
        config=self.config.spawn,
        workspace_dir=str(workspace),
        project_root=str(self.project_root),
        max_concurrent=min(len(requests), 5)  # Max 5 parallel repairs
    )

    # Validate all results together
    return all(r.success for r in results)
```

**Files to Modify:**
- `src/orchestrator/workflow_engine.py` — Add `_repair_artifacts_parallel()`, integrate into retry loop

---

## Phase 3: High-Impact Optimization (6-8 hours)

### Bottleneck 004: Strict Wave Sequencing (10-150s idle time)

**Current State:**
- Tasks are organized into dependency waves via topological sort
- Each wave must fully complete before the next wave starts
- Example: Task in Wave 3 depends on Task-5 from Wave 2, but waits for all 10 Wave 2 tasks
- Creates 10-150s of idle time for heterogeneous task distributions

**Location:** `src/orchestrator/workflow_engine.py` lines 984-1156 (`_execute_tasks_dag()`)

**Optimization Strategy:**
1. Replace wave-based execution with true dependency-graph scheduling
2. Use a topological sort DAG with per-task readiness tracking
3. Schedule tasks as soon as their dependencies complete
4. Maintain file-conflict detection within dynamic scheduling

**Expected Benefit:**
- Baseline: Waves force all tasks to wait for slowest in their wave
- Optimized: Early tasks in Wave 3 can start as soon as their Wave 2 dependencies complete
- **Savings: 10-150 seconds for heterogeneous task distributions (3-20 tasks)**

**Complexity Note:** This requires refactoring the task execution model to track individual task completion rather than wave completion. High impact but significant development effort.

---

## Benchmarking Strategy

### Metrics to Track

1. **Git Operation Parallelism** (Bottleneck 002)
   - Baseline: Sequential git ops (1 op at a time)
   - Target: 3-5 ops in parallel
   - Measurement: Log timestamps before/after git subprocess batches

2. **Artifact Repair Time** (Bottleneck 005)
   - Baseline: 30s per artifact × count
   - Target: 30s for all artifacts (parallelized)
   - Measurement: Track time per repair attempt in artifact retry loop

3. **Task Scheduling Efficiency** (Bottleneck 004)
   - Baseline: Wave completion time (longest task in wave)
   - Target: True DAG scheduling (sum of critical path)
   - Measurement: Timestamp each task start/end, compute idle time

### Benchmark Test Suite

```python
# tests/test_performance_phase2.py

@pytest.mark.slow
async def test_worktree_creation_parallelism():
    """Verify that 10 worktrees are created in parallel, not sequentially."""
    # Create 10 agents, measure time for all worktree creations
    # Expected: ~1-2 seconds (not 9-15 seconds)

@pytest.mark.slow
async def test_artifact_repair_batching():
    """Verify that 3 bad artifacts are repaired in parallel."""
    # Create step with 3 missing artifacts, measure repair time
    # Expected: ~30 seconds (not 90+ seconds)

@pytest.mark.slow
async def test_dynamic_scheduling_efficiency():
    """Verify that Wave 3 tasks start as soon as their dependencies are ready."""
    # Create DAG with Wave 2 (slow task), Wave 3 (fast, depends on Wave 2 task 1)
    # Expected: Wave 3 fast task starts ~2 seconds after Wave 2 task 1 completes
```

---

## Implementation Checklist

### Phase 2: Parallelize Git Operations
- [ ] Add `_create_worktree_async()` function
- [ ] Update `invoke_agent()` to use async version
- [ ] Update `invoke_agents_parallel()` to pre-create all worktrees
- [ ] Add logging for git operation timing
- [ ] Write performance benchmark for git parallelism
- [ ] Test with 10+ concurrent agents

### Phase 2: Batch Artifact Repairs
- [ ] Add `_repair_artifacts_parallel()` method
- [ ] Integrate into artifact retry loop
- [ ] Update artifact writer prompts for batch context
- [ ] Add logging for repair attempt timing
- [ ] Write performance benchmark for artifact repair
- [ ] Handle mixed missing+invalid artifacts

### Phase 3: Fine-Grained Scheduling
- [ ] Redesign task execution to use per-task readiness tracking
- [ ] Update dependency graph computation
- [ ] Replace wave-based loop with priority queue
- [ ] Add task scheduler state machine
- [ ] Preserve file-conflict detection
- [ ] Write performance benchmark for scheduling efficiency

---

## Risk Assessment

### Phase 2 (Low Risk)
- Git parallelization is isolated to `invoke_agent()` and `invoke_agents_parallel()`
- Artifact batching is localized to retry loop
- Behavioral impact: minimal (same end result, just faster)
- Rollback: revert git calls to synchronous, revert retry loop to sequential

### Phase 3 (Medium Risk)
- Requires refactoring task execution model
- Dependency tracking becomes more complex
- File-conflict detection must be reimplemented for dynamic scheduling
- Requires extensive testing to ensure no task ordering violations
- Rollback: revert to wave-based execution

---

## Success Criteria

1. **Phase 2 Implemented:**
   - [ ] Git operations complete in 1-2 seconds (vs 9-15 seconds)
   - [ ] Artifact repairs complete in 30 seconds (vs 90+ seconds)
   - [ ] All benchmarks pass
   - [ ] No regressions in artifact correctness

2. **Phase 3 Implemented:**
   - [ ] Dynamic scheduling reduces idle time by 30-50%
   - [ ] Task ordering correctness verified (no dependency violations)
   - [ ] File-conflict detection still works
   - [ ] Performance benchmark confirms 10-150s improvement

3. **Full Pipeline:**
   - [ ] Total 120-240s savings validated on 5+ end-to-end runs
   - [ ] p50, p95, p99 latencies measured and documented
   - [ ] Knowledge base rebuild count ≤1 per run
   - [ ] Artifact disk reads ≤3 per phase
