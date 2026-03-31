# Async Patterns

## Core Principles

- Use `async/await` for all I/O-bound operations: API calls, file I/O, subprocess invocation.
- Never block the event loop with synchronous calls. Use `asyncio.to_thread()` for unavoidable blocking work.
- One event loop per process. Do not create nested event loops.

## Task Groups and Concurrency

Use `asyncio.TaskGroup` (Python 3.11+) for structured concurrency:

```python
async def run_parallel_engineers(tasks: list[EngineerTask]) -> list[PhaseResult]:
    results: list[PhaseResult] = []
    async with asyncio.TaskGroup() as tg:
        for task in tasks:
            tg.create_task(run_engineer(task, results))
    return results
```

- TaskGroup ensures all tasks complete or all are cancelled on first failure.
- Prefer TaskGroup over raw `asyncio.gather()` for error handling clarity.
- Use `asyncio.gather(*tasks, return_exceptions=True)` only when you need partial results despite failures.

## Semaphores for Rate Limiting

```python
# Limit concurrent API calls
api_semaphore = asyncio.Semaphore(4)

async def call_api(prompt: str) -> str:
    async with api_semaphore:
        return await client.messages.create(...)
```

## Async Context Managers

Use `async with` for resources that require async setup/teardown:

```python
class AgentSession:
    async def __aenter__(self) -> "AgentSession":
        self._client = await create_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self._client.close()
```

## Error Handling in Async Code

```python
async def run_with_retry(
    coro_factory: Callable[[], Coroutine],
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> Any:
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning("Rate limited, retrying in %.1fs (attempt %d/%d)", delay, attempt + 1, max_retries)
            await asyncio.sleep(delay)
```

### Rules

- Always catch specific exceptions, not bare `Exception`.
- Log retry attempts with attempt count and delay.
- Set a maximum retry count. Never retry indefinitely.
- Distinguish transient errors (retry) from permanent errors (fail immediately).

## Cancellation

- Respect `asyncio.CancelledError`. Let it propagate unless you need cleanup.
- Use `asyncio.shield()` sparingly -- only for critical cleanup operations.
- Set timeouts on all external calls: `async with asyncio.timeout(30):`.

## Async Subprocess

```python
async def run_command(cmd: list[str], timeout: float = 30.0) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    if proc.returncode != 0:
        raise SubprocessError(f"Command failed: {stderr.decode()}")
    return stdout.decode()
```

## Testing Async Code

- Use `pytest-asyncio` with `asyncio_mode = "auto"`.
- Use `AsyncMock` for mocking async functions.
- Use `asyncio.timeout()` in tests to prevent hanging on broken async code.
