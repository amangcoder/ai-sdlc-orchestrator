# QA-Browser Phase Implementation Guide

## For Developers: Understanding and Extending the QA-Browser Phase

This guide is for developers who need to understand, maintain, or extend the QA-Browser phase internals.

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────┐
│ Orchestrator Engine (workflow_engine.py)                       │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  Phase Execution Loop:                                        │
│  1. Load step definition (name, inputs, outputs, on_fail)   │
│  2. Call _execute_step(step)                                │
│     └─ Pre-hook (stack detection, gates, docker up)        │
│     └─ Invoke agent (claude sub-agent)                      │
│     └─ Post-hook (parse results, write artifact)           │
│  3. On failure: call _invoke_fixer() if enabled           │
│  4. On success: proceed to next step                        │
│                                                                │
└────────────────────────────────────────────────────────────────┘
                          ↑
           ┌──────────────┼──────────────┐
           │              │              │
    ┌──────▼────┐  ┌──────▼────┐  ┌────▼──────┐
    │ Stack     │  │ App Test  │  │ Phases    │
    │ Detector  │  │ Server    │  │ (builders)│
    │           │  │           │  │           │
    │ Detects   │  │ Manages   │  │ Generates │
    │ framework │  │ lifecycle │  │ prompts   │
    │ & language│  │ (compose, │  │           │
    │           │  │ install,  │  │           │
    │           │  │ server)   │  │           │
    └───────────┘  └───────────┘  └───────────┘
```

## Key Components

### 1. StackDetector (stack_detector.py)

**Purpose:** Identifies the framework, language, and dev server config of a generated project.

**Key Class:**
```python
@dataclass(frozen=True)
class StackInfo:
    framework: str                # nextjs, fastapi, etc.
    language: str                 # javascript, python, etc.
    package_manager: str          # npm, pip, etc.
    install_command: str          # npm install, pip install -r requirements.txt
    dev_server_command: str       # npm run dev, uvicorn main:app --reload
    dev_server_port: int          # 3000, 8000, etc.
    base_url: str                 # http://localhost:3000
    health_check_path: str        # / or /health
    has_frontend: bool            # True for UI projects
```

**Usage:**
```python
from src.orchestrator.stack_detector import detect_stack

stack = detect_stack(Path("/generated/project"))
print(f"Framework: {stack.framework}")
print(f"Dev command: {stack.dev_server_command}")
```

**Zero-dependency design:**
- Imports **nothing** from `src/orchestrator` core
- Can be tested in complete isolation
- Pure functions for file reading and detection

### 2. AppTestServer (app_server.py — Planned)

**Purpose:** Manages the full lifecycle of a dev server: Docker Compose, installation, startup, health checks, seed script execution, teardown.

**Key Class (Design):**
```python
class AppTestServer:
    def __init__(
        self,
        project_root: Path,
        stack: StackInfo,
        timeout: float = 60.0,
    ):
        """Initialize the server manager."""
        self.project_root = project_root
        self.stack = stack
        self.timeout = timeout
        self._process = None

    async def compose_up(self) -> bool:
        """Start Docker Compose services (docker compose up -d)."""
        # Returns True if successful, False otherwise
        # Waits for health checks to pass

    async def install_packages(self) -> bool:
        """Install dependencies (npm install / pip install -r ...)."""
        # 120-second timeout
        # Captures stderr for diagnostics

    async def run_seed_script(self, seed_path: Path) -> bool:
        """Execute seed data script (SQL, Python, or TypeScript)."""
        # Language-agnostic runner

    async def start(self) -> tuple[str, int]:
        """Start the dev server on a free port."""
        # Returns (host, port) where server is running
        # Raises TimeoutError if server doesn't start within timeout

    async def wait_healthy(self, timeout: float = 60.0) -> bool:
        """Poll health check endpoint until HTTP 200."""
        # Default timeout 60 seconds
        # Returns True if healthy, False on timeout

    async def stop(self) -> None:
        """Gracefully stop the server and docker compose."""
        # Stops dev server process
        # Runs docker compose down
        # Cleanup with atexit safety net
```

**Usage:**
```python
from pathlib import Path
from src.orchestrator.stack_detector import detect_stack
from src.orchestrator.app_server import AppTestServer

# Detect stack
stack = detect_stack(Path("/generated/project"))

# Create server manager
server = AppTestServer(Path("/generated/project"), stack)

try:
    # Start Docker services
    if not await server.compose_up():
        print("Docker Compose failed")
        return

    # Install dependencies
    if not await server.install_packages():
        print("Install failed")
        return

    # Seed database
    if (Path("/generated/project") / "scripts" / "seed.sql").exists():
        await server.run_seed_script(Path("scripts/seed.sql"))

    # Start dev server
    host, port = await server.start()
    base_url = f"http://{host}:{port}"

    # Wait for health check
    if not await server.wait_healthy():
        print("Server unhealthy")
        return

    print(f"Server running at {base_url}")

    # Run tests against base_url
    # ...

finally:
    # Cleanup
    await server.stop()
```

### 3. Prompt Builders (phases.py)

**Purpose:** Inject context into agent prompts for the three new agent roles.

#### build_env_setup_prompt()

Generates the prompt for the Env Setup agent. Injects:
- Architecture.json (declared services, dependencies)
- PRD (domain entities, sample data)
- Stack info (framework, language)
- Seed data examples

**Signature (design):**
```python
def build_env_setup_prompt(
    feature_request: str,
    workspace: Path,
    config: OrchestratorConfig,
    task_data: dict | None = None,
) -> str:
    """Build prompt for env_setup_engineer agent."""
    # Reads prd.json and architecture.json
    # Injects service list, domain entities, examples
    # Returns formatted system + context message
```

#### build_qa_browser_prompt()

Generates the prompt for the QA Browser agent. Injects:
- Stack info (framework, language)
- Base URL (where the server is running)
- PRD acceptance criteria (formatted list)
- Playwright testing examples

**Signature (design):**
```python
def build_qa_browser_prompt(
    feature_request: str,
    workspace: Path,
    config: OrchestratorConfig,
    task_data: dict | None = None,
) -> str:
    """Build prompt for qa_browser_engineer agent."""
    # Receives stack info and base_url in task_data
    # Injects AC list, Playwright patterns
    # Instructions to use accessible selectors
    # Returns formatted prompt
```

#### build_fixer_prompt()

Generates the prompt for the Fixer agent. Injects:
- Failed step name
- Raw error output (stderr, assertion message)
- All artifacts produced so far
- Instructions to trace decision chain

**Signature (design):**
```python
def build_fixer_prompt(
    failed_step: str,
    error_output: str,
    artifact_context: dict[str, Any],
    workspace: Path,
) -> str:
    """Build prompt for fixer agent."""
    # Formats error summary
    # Reconstructs artifact chain (PM → Architect → Engineer → failure)
    # Instructs agent to identify single root cause
    # Instructs to apply minimal targeted fix
    # Returns formatted prompt
```

## Workflow Integration Points

### In engine.py

**PHASE_ORDER update:**
```python
PHASE_ORDER = [
    "pm",
    "architect",
    "engineer",
    "qa",
    "env_setup",           # NEW
    "qa_browser",          # NEW
    "reviewer",
]
```

Note: `"fixer"` is **NOT** in PHASE_ORDER. It's invoked inline on failure.

### In workflows.py

**FEATURE_DEVELOPMENT workflow:**
```python
WorkflowStepDefinition(
    name="QA",
    agent_role=AgentRole.QA,
    inputs=["prd", "architecture", "engineering_plan"],
    outputs=["qa_report"],
    next="Env Setup",  # CHANGED from "Reviewer"
    on_fail="Implementation",
),
WorkflowStepDefinition(
    name="Env Setup",  # NEW
    agent_role=AgentRole.ENV_SETUP_ENGINEER,
    inputs=["prd", "architecture", "qa_report"],
    outputs=["env_setup_report"],
    next="QA Browser",
    on_fail="Implementation",
),
WorkflowStepDefinition(
    name="QA Browser",  # NEW
    agent_role=AgentRole.QA_BROWSER_ENGINEER,
    inputs=["prd", "qa_report", "env_setup_report"],
    outputs=["qa_browser_report"],
    next="Reviewer",
    on_fail="Implementation",
),
```

### In roles.py

**ROLE_REGISTRY additions:**
```python
ROLE_REGISTRY = {
    # ... existing roles ...
    AgentRole.ENV_SETUP_ENGINEER: RoleDefinition(
        role=AgentRole.ENV_SETUP_ENGINEER,
        title="Environment Setup Engineer",
        responsibility="Writes docker-compose.yml and seed data scripts",
        access=RoleAccess.READ_WRITE,
        agent_file="env_setup.md",
    ),
    AgentRole.QA_BROWSER_ENGINEER: RoleDefinition(
        role=AgentRole.QA_BROWSER_ENGINEER,
        title="QA Browser Engineer",
        responsibility="Generates Playwright tests for acceptance criteria",
        access=RoleAccess.READ_WRITE,
        agent_file="qa_browser.md",
    ),
    AgentRole.FIXER: RoleDefinition(
        role=AgentRole.FIXER,
        title="Fixer",
        responsibility="Diagnoses and applies targeted fixes for pipeline failures",
        access=RoleAccess.READ_WRITE,
        agent_file="fixer.md",
    ),
}
```

### In model_routing.py

**ROLE_CATEGORY additions:**
```python
ROLE_CATEGORY = {
    # ... existing mappings ...
    AgentRole.ENV_SETUP_ENGINEER: AgentCategory.CODING,
    AgentRole.QA_BROWSER_ENGINEER: AgentCategory.VERIFICATION,
    AgentRole.FIXER: AgentCategory.VERIFICATION,
}
```

## Workflow Engine Hooks

### In workflow_engine.py

#### Pre-hook for QA Browser Step

Runs **before** the agent is invoked:

```python
async def _execute_step(self, step: WorkflowStepDefinition) -> dict:
    if step.name == "QA Browser":
        # 1. Detect stack
        stack = detect_stack(self.state.project_root)
        if not stack.has_frontend:
            return {
                "server_status": "skipped",
                "verdict": "pass",
                "issues": ["Backend-only project; browser testing not applicable"],
            }

        # 2. Check speed mode gate
        if self.config.speed_mode in ["turbo", "standard"]:
            return {
                "server_status": "skipped",
                "verdict": "pass",
                "issues": [f"Skipped in {self.config.speed_mode} mode"],
            }

        # 3. Start app server
        server = AppTestServer(self.state.project_root, stack)
        try:
            await server.compose_up()
            await server.install_packages()
            seed_script = self.state.project_root / "scripts" / "seed.sql"
            if seed_script.exists():
                await server.run_seed_script(seed_script)
            host, port = await server.start()
            base_url = f"http://{host}:{port}"

            # 4. Inject context into agent prompt
            task_data = {
                "stack": stack,
                "base_url": base_url,
            }

            # 5. Invoke agent
            agent_result = await invoke_agent(
                AgentInvocation(
                    agent_name="qa_browser",
                    prompt=build_qa_browser_prompt(
                        self.config.feature_request,
                        self.state.workspace_dir,
                        self.config,
                        task_data,
                    ),
                    model="sonnet",
                    max_turns=40,
                )
            )

            # 6. Run Playwright tests
            test_result = await self._run_playwright_tests(
                self.state.project_root,
                base_url,
            )

            # 7. Parse results and construct report
            report = self._construct_qa_browser_report(
                stack, base_url, test_result
            )

            return report
        finally:
            # 8. Cleanup
            await server.stop()
```

#### Fixer Invocation

Automatically called when a step fails:

```python
async def _invoke_fixer(
    self,
    failed_step: WorkflowStepDefinition,
    error_output: str,
    attempt: int,
) -> bool:
    """Invoke Fixer to attempt recovery. Returns True if fix applied."""
    # Check if Fixer is enabled and appropriate for speed mode
    if not self.config.fixer.enabled:
        return False
    if self.config.speed_mode not in self.config.fixer.speed_modes:
        return False
    if attempt >= self.config.fixer.max_attempts:
        return False

    # Collect all artifacts produced so far
    artifact_context = self._collect_all_artifacts()

    # Build Fixer prompt
    prompt = build_fixer_prompt(
        failed_step=failed_step.name,
        error_output=error_output,
        artifact_context=artifact_context,
        workspace=self.state.workspace_dir,
    )

    # Invoke Fixer agent
    try:
        fixer_result = await invoke_agent(
            AgentInvocation(
                agent_name="fixer",
                prompt=prompt,
                model="sonnet",
                max_turns=25,
                workspace_dir=self.state.workspace_dir,
            )
        )

        # Read fixer_report.json
        fixer_report = self._load_artifact("fixer_report")
        if not fixer_report:
            return False

        # Return True if fix was successful, False if escalating
        return fixer_report.get("verdict") == "fixed"

    except Exception as e:
        # Never crash due to Fixer failure
        logger.error(f"Fixer crashed: {e}")
        return False
```

## Configuration (default.yaml)

```yaml
# Speed mode gating
speed_mode: standard

# Env Setup phase config
env_setup:
  agent: env_setup_engineer
  model: sonnet
  max_turns: 30
  parallel: false
  max_retries: 2
  timeout_minutes: 15
  input_artifacts: [prd, architecture, qa_report]
  output_artifacts: [env_setup_report]

# QA Browser phase config
qa_browser:
  agent: qa_browser_engineer
  model: sonnet
  max_turns: 40
  escalation_model: opus
  parallel: false
  max_retries: 1
  timeout_minutes: 25
  playwright_timeout_ms: 120000
  server_start_timeout_s: 60
  install_timeout_s: 120
  input_artifacts: [prd, qa_report, env_setup_report]
  output_artifacts: [qa_browser_report]

# Fixer config
fixer:
  enabled: true
  max_attempts: 2
  speed_modes: [standard, thorough, paranoid]
  model: sonnet
  escalation_model: opus
  max_turns: 25
  output_artifacts: [fixer_report]
```

## Testing Implementation

### Unit Tests for StackDetector

```python
# tests/unit/test_stack_detector.py
import json
from pathlib import Path
from src.orchestrator.stack_detector import detect_stack

def test_detect_nextjs(tmp_path):
    """Test Next.js detection from package.json."""
    pkg = {
        "name": "my-app",
        "dependencies": {"next": "^13.0.0"},
        "scripts": {"dev": "next dev"},
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg))

    info = detect_stack(tmp_path)

    assert info.framework == "nextjs"
    assert info.has_frontend is True
    assert info.dev_server_port == 3000
```

### Integration Tests for QA Browser Phase

```python
# tests/integration/test_qa_browser_phase.py
@pytest.mark.asyncio
async def test_qa_browser_phase_with_nextjs_app():
    """Test full QA Browser phase with a Next.js app."""
    # Create a minimal Next.js project
    project_root = tmp_path / "nextjs-app"
    # ... setup files ...

    # Run phase
    report = await run_qa_browser_phase(project_root)

    # Verify report structure
    assert report["server_status"] in ["started", "failed", "skipped"]
    assert "verdict" in report
    assert isinstance(report["test_results"], list)
```

## Extending the Phase

### Add Support for New Framework

1. **Extend stack_detector.py:**
   ```python
   def _detect_my_framework(config: dict) -> StackInfo | None:
       if "my-framework" in str(config):
           return StackInfo(
               framework="my-framework",
               # ...
           )
   ```

2. **Add unit test:**
   ```python
   def test_detect_my_framework(tmp_path):
       # ... setup ...
       info = detect_stack(tmp_path)
       assert info.framework == "my-framework"
   ```

### Customize Playwright Test Generation

Modify `.claude/agents/qa_browser.md` system prompt to:
- Prefer certain selectors for your framework (e.g., data-testid for Angular)
- Add framework-specific testing patterns
- Provide examples for your tech stack

### Adjust Timeouts for Slow Environments

```yaml
qa_browser:
  server_start_timeout_s: 90    # Increase from 60s
  install_timeout_s: 180        # Increase from 120s
  playwright_timeout_ms: 180000 # Increase from 120s
```

## Debugging Tips

### Enable Debug Logging

```bash
export ORCHESTRATOR_LOG_LEVEL=DEBUG
orchestrate --speed thorough "..."
```

### Inspect Generated Artifacts

```bash
# View env_setup_report
jq . workspace/artifacts/env_setup_report.json

# View qa_browser_report
jq .test_results[] workspace/artifacts/qa_browser_report.json

# View fixer_report (if applied)
jq . workspace/artifacts/fixer_report.json
```

### Manually Test Docker Compose

```bash
cd /workspace/generated/project
docker compose config  # Validate compose file
docker compose up -d   # Start services
docker compose logs    # View logs
docker compose down    # Stop services
```

### Manually Run Playwright Tests

```bash
cd /workspace/generated/project
npx playwright test tests/e2e/generated/ --headed --debug
```

## See Also

- [QA-Browser Feature Guide](./features/QA_BROWSER_PHASE.md)
- [Configuration Guide](./QA_BROWSER_CONFIGURATION.md)
- [Stack Detector API](./STACK_DETECTOR_API.md)
- [Playwright Testing Guide](./PLAYWRIGHT_TESTING_GUIDE.md)
