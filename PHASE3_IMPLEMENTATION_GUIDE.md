# Phase 3: Fine-Grained Dynamic Task Scheduling — Implementation Guide

## Overview

Phase 3 addresses **Bottleneck 004: Strict Wave Sequencing**, which causes tasks in later waves to wait for entire earlier waves to complete, even if they only depend on 1-2 tasks.

**Expected Impact:** 10-150 seconds saved per pipeline (3-20 task runs)
**Complexity:** High (significant refactoring required)
**Risk Level:** Medium (requires extensive testing)

---

## Current Architecture (Wave-Based)

### How It Works Now

1. **Dependency Analysis** → `_compute_dependency_waves()`
   - Converts task dependency graph to topological layers
   - Each layer = independent tasks that can run in parallel

2. **Wave Execution** → `_execute_tasks_dag()`
   ```python
   for wave_num, wave in enumerate(waves):  # Sequential loop
       # All tasks in wave run in parallel
       for task in wave:
           invoke_agent(task)
       # Wait for wave to complete
       # Rebuild knowledge index
       # Then start next wave
   ```

3. **Wave Barriers**
   - Wave 1 starts
   - Wave 1 finishes (even if Wave 2's first task only depends on Wave 1's first task)
   - Wave 2 starts
   - Knowledge index rebuilt between waves

### Problem

```
Wave 1: [TASK-1 (slow, 30s), TASK-2 (fast, 5s), TASK-3 (fast, 5s)]
Wave 2: [TASK-4 (depends on TASK-1), TASK-5 (depends on TASK-2)]

Timeline (Current — Wave-Based):
0s    TASK-1 starts
0s    TASK-2 starts
0s    TASK-3 starts
5s    TASK-2 finishes (TASK-5 could start now!)
5s    TASK-3 finishes
30s   TASK-1 finishes (Wave 1 complete)
30s   Knowledge index rebuild (5-10s)
35s   TASK-4 starts (could have started at 30s if no rebuild)
35s   TASK-5 starts (WAITS FOR TASK-1, even though ready at 5s!)
40s   TASK-4 finishes
40s   TASK-5 finishes

Total: 40s + 5-10s rebuild = 45-50s
```

### Optimized Timeline (Phase 3 — Fine-Grained)

```
0s    TASK-1, TASK-2, TASK-3 start
5s    TASK-2 finishes → TASK-5 starts immediately (doesn't wait for Wave 1)
5s    TASK-3 finishes
10s   TASK-5 finishes
30s   TASK-1 finishes → TASK-4 starts immediately
35s   TASK-4 finishes

Total: 35s (no knowledge rebuild needed between tasks)
Savings: 10-15 seconds for this example
```

---

## Proposed Solution: Dynamic Dependency-Graph Scheduler

### Architecture

#### 1. **TaskReadiness Tracker**
Tracks per-task completion status instead of per-wave

```python
class TaskReadinessTracker:
    """Tracks which tasks are ready to execute."""

    def __init__(self, tasks: list[WorkflowTaskState]):
        self.completed: set[str] = set()  # task_id -> completed
        self.in_progress: set[str] = set()  # task_id -> in progress
        self.pending: set[str] = set(t.task_id for t in tasks)

    def is_ready(self, task: WorkflowTaskState) -> bool:
        """Check if all dependencies are completed."""
        return all(dep_id in self.completed for dep_id in task.dependencies)

    def get_ready_tasks(self, pending_tasks: list[WorkflowTaskState]) -> list[WorkflowTaskState]:
        """Return all tasks whose dependencies are satisfied."""
        return [t for t in pending_tasks if self.is_ready(t)]

    def mark_started(self, task_id: str):
        self.pending.discard(task_id)
        self.in_progress.add(task_id)

    def mark_completed(self, task_id: str):
        self.in_progress.discard(task_id)
        self.completed.add(task_id)
```

#### 2. **Task Scheduler with Priority Queue**
Uses asyncio to start tasks as soon as dependencies are ready

```python
class TaskScheduler:
    """Schedule tasks based on dynamic dependency graph."""

    def __init__(self, tasks: list[WorkflowTaskState]):
        self.tasks = {t.task_id: t for t in tasks}
        self.readiness = TaskReadinessTracker(tasks)
        self.pending = list(tasks)

    async def schedule_all(self) -> dict[str, TaskResult]:
        """Schedule and execute all tasks dynamically."""
        results: dict[str, TaskResult] = {}

        while self.pending:
            # Get tasks ready to run
            ready = self.readiness.get_ready_tasks(self.pending)

            if not ready:
                # Deadlock detection: no tasks ready, but some pending
                raise DeadlockError(f"No ready tasks but {len(self.pending)} pending")

            # Partition ready tasks by file conflicts
            no_conflict, conflict = self._partition_by_file_conflicts(ready)

            # Launch non-conflicting tasks in parallel
            if no_conflict:
                tasks = []
                for task in no_conflict:
                    self.readiness.mark_started(task.task_id)
                    self.pending.remove(task)
                    tasks.append(self._execute_task(task, results))

                # Wait for all to complete
                await asyncio.gather(*tasks)

            # Run conflicting tasks sequentially
            for task in conflict:
                self.readiness.mark_started(task.task_id)
                self.pending.remove(task)
                await self._execute_task(task, results)

        return results

    async def _execute_task(self, task: WorkflowTaskState, results: dict):
        """Execute a single task."""
        # ... agent invocation logic ...
        result = await invoke_agent(invocation)
        self.readiness.mark_completed(task.task_id)
        results[task.task_id] = result
```

#### 3. **Integration into WorkflowEngine**
Replace wave-based loop with dynamic scheduler

```python
async def _execute_tasks_dynamic(
    self,
    step: WorkflowStepDefinition,
    tasks: list[WorkflowTaskState],
) -> bool:
    """Execute tasks using fine-grained dependency scheduling.

    Instead of wave barriers, start each task as soon as its dependencies
    are satisfied.
    """
    scheduler = TaskScheduler(tasks)
    results = await scheduler.schedule_all()

    # Process results
    for task, result in results.items():
        self._apply_result(task, result)

    return all(r.success for r in results.values())
```

---

## Implementation Steps

### Step 1: Add TaskReadinessTracker Class
**File:** `src/orchestrator/workflow_engine.py`

```python
class TaskReadinessTracker:
    """Track task completion for dynamic scheduling."""

    def __init__(self, tasks: list[WorkflowTaskState]):
        self.tasks_by_id = {t.task_id: t for t in tasks}
        self.completed: set[str] = set()
        self.in_progress: set[str] = set()

    def is_ready(self, task: WorkflowTaskState) -> bool:
        """All dependencies must be completed."""
        return all(
            dep_id in self.completed
            for dep_id in task.dependencies
        )

    def get_ready_tasks(self) -> list[WorkflowTaskState]:
        """Return all tasks ready to execute."""
        ready = []
        for task in self.tasks_by_id.values():
            if (task.task_id not in self.completed and
                task.task_id not in self.in_progress and
                self.is_ready(task)):
                ready.append(task)
        return ready

    def mark_started(self, task_id: str):
        self.in_progress.add(task_id)

    def mark_completed(self, task_id: str):
        self.in_progress.discard(task_id)
        self.completed.add(task_id)
```

### Step 2: Add TaskScheduler Class
**File:** `src/orchestrator/workflow_engine.py`

Implements the main scheduling loop using asyncio

### Step 3: Integrate into _execute_tasks_dag()

Replace the wave loop with dynamic scheduler:

```python
async def _execute_tasks_dag(self, step, tasks, workspace):
    """Execute tasks dynamically instead of by waves."""
    # Use new dynamic scheduler
    scheduler = TaskScheduler(tasks)
    results = await scheduler.schedule_all()

    # Apply results as before
    for task_id, result in results.items():
        task = next(t for t in tasks if t.task_id == task_id)
        self._apply_result(task, result)
```

### Step 4: Update File Conflict Detection
Ensure file conflicts are still honored in dynamic scheduling

### Step 5: Testing & Validation
- Unit tests for TaskReadinessTracker
- Integration tests for TaskScheduler
- Regression tests for dependency ordering

---

## Key Considerations

### 1. **Dependency Correctness**
- Must preserve ALL dependencies from original graph
- File conflicts still require serialization
- Must detect cycles (deadlock prevention)

### 2. **Knowledge Index Rebuilding**
- Current: Rebuild between waves (5-10s per wave)
- Optimized: Rebuild only when new symbols are needed, or batch rebuilds
- Could save 50-100ms per step

### 3. **Progress Tracking**
- Wave-based: Progress updates per wave
- Dynamic: Progress updates per task
- UI updates may need refactoring

### 4. **Error Handling**
- Partial failures: Some tasks fail, others continue
- Deadlock detection: No tasks ready but some pending
- Timeout management: Overall step timeout vs per-task timeout

### 5. **Logging & Observability**
- Task start/end timestamps
- Dependency chain visualization
- Idle time metrics

---

## Testing Strategy

### Unit Tests
```python
def test_task_readiness_tracker():
    """Verify readiness tracking works correctly."""

def test_task_scheduler_respects_dependencies():
    """Ensure all dependencies are satisfied before task execution."""

def test_task_scheduler_handles_file_conflicts():
    """Verify file conflicts serialize tasks even if independent."""

def test_task_scheduler_detects_deadlock():
    """Catch cases where tasks are pending but none are ready."""
```

### Integration Tests
```python
def test_dynamic_scheduling_vs_wave_based():
    """Compare execution time and task ordering between approaches."""

def test_dynamic_scheduling_with_mixed_durations():
    """Test with tasks that have very different runtimes."""

def test_dynamic_scheduling_respects_all_constraints():
    """Verify correctness: same results, just faster."""
```

### Regression Tests
```python
def test_all_tasks_complete_in_correct_order():
    """Ensure no dependencies are violated."""

def test_artifact_correctness_unchanged():
    """Verify outputs are identical to wave-based execution."""

def test_error_propagation_unchanged():
    """Failed tasks still fail consistently."""
```

---

## Performance Predictions

### Theoretical Speedup

For a heterogeneous pipeline with tasks of varying durations:

```
Scenario 1: Sequential waves
Wave 1: [10s, 10s, 10s] = 10s
Wave 2: [10s, 10s, 10s] = 10s
Wave 3: [10s] = 10s
Total: 30s

Scenario 2: Dynamic scheduling (no wave barriers)
0-10s:  TASK-1, 2, 3 in parallel
10-20s: TASK-4, 5, 6 in parallel (start as soon as deps ready)
20-30s: TASK-7 (starts immediately after TASK-4 done)
Total: 30s (no savings for evenly-distributed tasks)

Scenario 3: Highly heterogeneous
Wave 1: [30s (slow), 5s (fast), 5s (fast)] = 30s
Wave 2: [5s, 5s, 5s, 5s] depends only on Wave 1 fast task
Total with waves: 30 + 5 = 35s
Total dynamic: ~15s (fast tasks in Wave 2 start at 5s, not 30s)
Savings: ~20s (57% improvement)
```

### Realistic Impact
- **Best case:** 30-40% improvement for heterogeneous task distributions
- **Average case:** 10-20% improvement
- **Worst case:** No improvement (already well-balanced)
- **Overall:** 10-150s savings per pipeline (depending on task distribution)

---

## Rollback Plan

If Phase 3 introduces regressions:

1. Keep wave-based execution as fallback
2. Add feature flag: `use_dynamic_scheduling: true/false`
3. Log execution metrics from both approaches
4. Revert to wave-based if issues detected

---

## Success Criteria

- [ ] All dependencies respected (no tasks run before their deps)
- [ ] File conflicts still serialized
- [ ] No deadlocks or hangs
- [ ] Artifact correctness unchanged
- [ ] Performance improvement validated (10-150s)
- [ ] All regression tests pass
- [ ] Documentation updated

---

## Timeline Estimate

- **Design & Architecture:** 2 hours
- **Implementation:** 4-6 hours
- **Testing & Validation:** 2-3 hours
- **Documentation:** 1 hour
- **Total:** 9-12 hours (1-2 days)

---

## Next Steps

1. Start with TaskReadinessTracker implementation
2. Add comprehensive unit tests
3. Build TaskScheduler with dynamic loop
4. Integration test with actual tasks
5. Benchmark and validate speedup
6. Merge to main branch
