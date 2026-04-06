# QA-Browser Phase Configuration Guide

## Overview

This document describes how to configure the QA-Browser phase, Env Setup phase, and Fixer agent in the orchestrator. Configuration is managed through `config/default.yaml` and optional environment variable overrides.

## Speed Modes

Speed modes control which phases run and how aggressively the Fixer attempts recovery. Configure via CLI flag:

```bash
orchestrate --speed turbo        # Fast iteration (skips browser testing)
orchestrate --speed standard     # Default (includes Fixer for recovery)
orchestrate --speed thorough     # Full validation (includes browser testing)
orchestrate --speed paranoid     # Most thorough (all validations)
```

### Speed Mode Matrix

| Setting | Env Setup | QA Browser | Fixer | Est. Duration |
|---------|-----------|------------|-------|----------------|
| **turbo** | ❌ skip | ❌ skip | ❌ skip | ~5 min |
| **standard** | ❌ skip | ❌ skip | ✅ run (2 attempts) | ~10 min |
| **thorough** | ✅ run | ✅ run | ✅ run (2 attempts) | ~15 min |
| **paranoid** | ✅ run | ✅ run | ✅ run (3 attempts) | ~20 min |

### Recommended Usage

- **`turbo`** — Local development, rapid iteration on code
- **`standard`** — CI/CD default, acceptable feedback loop
- **`thorough`** — Pre-release validation, feature branches
- **`paranoid`** — Critical releases, sensitive code paths

## Configuration File Structure

### Env Setup Phase

```yaml
env_setup:
  # Agent configuration
  agent: env_setup_engineer      # Agent role name
  model: sonnet                  # Claude model (sonnet, opus, haiku)
  max_turns: 30                  # Max conversation turns with agent

  # Execution settings
  parallel: false                # Can't be parallelized (single step)
  max_retries: 2                 # Retries before escalation
  timeout_minutes: 15            # Wall-clock timeout

  # Artifact handling
  input_artifacts:
    - prd
    - architecture
    - qa_report
  output_artifacts:
    - env_setup_report
```

### QA Browser Phase

```yaml
qa_browser:
  # Agent configuration
  agent: qa_browser_engineer     # Agent role name
  model: sonnet                  # Claude model
  max_turns: 40                  # More turns for test writing
  escalation_model: opus         # Escalate to Opus if needed

  # Execution settings
  parallel: false                # Sequential (server lifecycle)
  max_retries: 1                 # Fewer retries (slow)
  timeout_minutes: 25            # Longer timeout (Docker + browser)

  # Phase-specific settings
  playwright_timeout_ms: 120000  # 120s per test file
  server_start_timeout_s: 60     # 60s to reach healthy
  install_timeout_s: 120         # 120s for npm/pip install

  # Artifact handling
  input_artifacts:
    - prd
    - qa_report
    - env_setup_report
  output_artifacts:
    - qa_browser_report
```

### Fixer Agent

```yaml
fixer:
  # Global enable/disable
  enabled: true                  # Can be toggled to disable Fixer entirely

  # Retry configuration
  max_attempts: 2                # Max times Fixer tries to fix before escalation
                                 # 1 = one attempt; 2 = one attempt + one retry

  # Speed mode gating
  speed_modes:                   # Fixer runs in these modes
    - standard
    - thorough
    - paranoid
  # Note: Fixer is DISABLED in turbo mode (speed > reliability)

  # Model configuration
  model: sonnet                  # Fast inference; sufficient for most root causes
  escalation_model: opus         # Used for complex/ambiguous root causes
  max_turns: 25                  # Max conversation turns

  # Artifact handling
  output_artifacts:
    - fixer_report
```

## Full Example Configuration

```yaml
# config/default.yaml

# Speed mode settings
speed_mode: standard

# Phase configurations
env_setup:
  agent: env_setup_engineer
  model: sonnet
  max_turns: 30
  parallel: false
  max_retries: 2
  timeout_minutes: 15
  input_artifacts: [prd, architecture, qa_report]
  output_artifacts: [env_setup_report]

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

# Fixer configuration
fixer:
  enabled: true
  max_attempts: 2
  speed_modes: [standard, thorough, paranoid]
  model: sonnet
  escalation_model: opus
  max_turns: 25
  output_artifacts: [fixer_report]

# Agent definitions
agents:
  env_setup:
    name: Environment Setup Engineer
    model: sonnet
    max_turns: 30
  qa_browser:
    name: QA Browser Engineer
    model: sonnet
    max_turns: 40
  fixer:
    name: Fixer
    model: sonnet
    escalation_model: opus
    max_turns: 25
```

## Environment Variable Overrides

You can override config values via environment variables. Format: `ORCHESTRATOR_<SECTION>_<KEY>=value`

```bash
# Override Fixer settings
export ORCHESTRATOR_FIXER_ENABLED=false
export ORCHESTRATOR_FIXER_MAX_ATTEMPTS=1

# Override speed mode (use CLI flag instead of env var if possible)
export ORCHESTRATOR_SPEED_MODE=thorough

# Override phase timeouts
export ORCHESTRATOR_QA_BROWSER_TIMEOUT_MINUTES=30
export ORCHESTRATOR_ENV_SETUP_TIMEOUT_MINUTES=20

# Override model (e.g., for cost-cutting)
export ORCHESTRATOR_ENV_SETUP_MODEL=haiku
export ORCHESTRATOR_QA_BROWSER_MODEL=haiku
```

## Customizing for Your Environment

### Scenario 1: Disable Fixer (Use Existing on_fail Routing)

If you prefer the pipeline to route failures through `on_fail` handlers (Reviewer, Implementation) rather than attempt auto-recovery:

```yaml
fixer:
  enabled: false  # Skip Fixer entirely
```

Or via CLI:
```bash
orchestrate --config config/no-fixer.yaml "..."
```

### Scenario 2: Run Browser Testing in Standard Mode

If you want browser testing enabled by default (trade off: slower feedback):

```yaml
qa_browser:
  # Add condition to run in standard mode
  # Note: This requires custom logic in workflow_engine.py
  # Default behavior gates only by speed_mode check
```

**Not recommended.** Standard mode is intentionally lightweight. For thorough testing, use `--speed thorough`.

### Scenario 3: Extend Playwright Timeouts for Slow Networks

If your CI environment is slow and tests timeout frequently:

```yaml
qa_browser:
  playwright_timeout_ms: 180000  # 3 minutes instead of 2
  server_start_timeout_s: 90     # 90s instead of 60s
  install_timeout_s: 180         # 3 minutes instead of 2
```

### Scenario 4: Use Only Opus for Complex Projects

If your generated apps require sophisticated test generation:

```yaml
env_setup:
  model: opus                     # More capable, more expensive
qa_browser:
  model: opus
  escalation_model: opus
fixer:
  model: opus                     # Handles complex root causes natively
```

**Cost impact:** ~3-5x higher token usage per run. Only recommended for critical workflows.

### Scenario 5: Disable Browser Testing (Keep Env Setup)

If you want Docker Compose + seed data but not browser testing:

Modify `workflow_engine.py` to skip QA Browser step:

```python
if phase_name == "qa_browser":
    # Skip this phase, produce stub report
    return {
        "server_status": "skipped",
        "verdict": "pass",
        "issues": ["Browser testing disabled via configuration"],
    }
```

Or use speed mode gating (default behavior).

## Docker Compose Configuration

The Env Setup agent writes `docker-compose.yml` to the generated project root. To customize Docker behavior:

### Custom Docker Network

The generated compose files use a named network so services can resolve by hostname:

```yaml
services:
  postgres:
    networks:
      - app_network
  redis:
    networks:
      - app_network
  app:
    networks:
      - app_network

networks:
  app_network:
    driver: bridge
```

### Health Checks

All services include health checks:

```yaml
postgres:
  healthcheck:
    test: ["CMD", "pg_isready", "-U", "postgres"]
    interval: 10s
    timeout: 5s
    retries: 5
```

The harness waits for all health checks before starting the dev server.

### Resource Limits

Docker Compose can optionally enforce resource limits:

```yaml
services:
  postgres:
    deploy:
      resources:
        limits:
          cpus: "1"
          memory: 512M
```

To enable in generated compose files, modify `.claude/agents/env_setup.md` to include resource limit guidance.

## Seed Data Configuration

The Env Setup agent writes language-specific seed scripts. To customize:

### SQL (PostgreSQL/MySQL)

```sql
-- scripts/seed.sql (idempotent)
INSERT INTO users (id, email, name)
VALUES
  (1, 'alice@example.com', 'Alice'),
  (2, 'bob@example.com', 'Bob')
ON CONFLICT (id) DO NOTHING;  -- Makes it idempotent

INSERT INTO todos (id, user_id, title, completed)
VALUES
  (1, 1, 'Learn Orchestrator', false),
  (2, 1, 'Build an app', false),
  (3, 2, 'Deploy to production', true)
ON CONFLICT (id) DO NOTHING;
```

### Python (SQLAlchemy/Django ORM)

```python
# scripts/seed.py (idempotent)
import os
import sys
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth.models import User
from app.models import Todo

User.objects.get_or_create(
    username="alice",
    defaults={"email": "alice@example.com"}
)

Todo.objects.get_or_create(
    id=1,
    defaults={
        "title": "Learn Orchestrator",
        "completed": False
    }
)
```

### TypeScript (Prisma/Sequelize)

```typescript
// scripts/seed.ts (idempotent)
import { PrismaClient } from "@prisma/client";

const prisma = new PrismaClient();

async function main() {
  const alice = await prisma.user.upsert({
    where: { email: "alice@example.com" },
    update: {},
    create: {
      email: "alice@example.com",
      name: "Alice",
    },
  });

  await prisma.todo.upsert({
    where: { id: 1 },
    update: {},
    create: {
      title: "Learn Orchestrator",
      userId: alice.id,
      completed: false,
    },
  });
}

main()
  .then(() => prisma.$disconnect())
  .catch((e) => {
    console.error(e);
    process.exit(1);
  });
```

## Playwright Configuration

The QA Browser agent generates TypeScript Playwright test files. A minimal `playwright.config.ts` is auto-generated if missing:

```typescript
// playwright.config.ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e/generated",
  fullyParallel: false,  // Sequential (safer for shared server state)
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : 1,  // Single worker (single server instance)
  timeout: 120000,  // 120s per test (includes server startup)
  expect: {
    timeout: 10000,  // 10s per assertion
  },
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:3000",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
```

To customize:
1. Edit `.claude/agents/qa_browser.md` system prompt
2. Add custom Playwright config template to `phases.py`
3. Agent will respect existing `playwright.config.ts` if present

## Monitoring & Logging

### Check Phase Status

```bash
# View recent phase logs
tail -f workspace/logs/orchestrator.log | grep -E "env_setup|qa_browser|fixer"

# List produced artifacts
orchestrate --list-artifacts "run-id-123"
```

### Artifact Inspection

```bash
# View Env Setup Report
orchestrate --read-artifact run-123 env_setup_report

# View QA Browser Report
orchestrate --read-artifact run-123 qa_browser_report

# View Fixer Report (if applied)
orchestrate --read-artifact run-123 fixer_report
```

### Docker Debugging

```bash
# Inspect generated compose file
cat workspace/orchestrator-for-mobile-ui/docker-compose.yml

# Manually run compose
cd workspace/orchestrator-for-mobile-ui
docker compose up -d

# Check service health
docker compose ps
docker compose logs postgres
docker compose logs app

# Clean up
docker compose down -v
```

## Troubleshooting Configuration

### "Fixer is not auto-recovering failures"

**Check:**
1. Is Fixer enabled? → `fixer.enabled: true`
2. Is speed mode in `fixer.speed_modes`? → `--speed thorough` or `--speed paranoid`
3. Is `max_attempts > 0`? → `fixer.max_attempts: 2`

**Solution:**
```bash
orchestrate --speed thorough --debug "..."  # Shows Fixer invocation logs
```

### "QA Browser phase is skipped"

**Check:**
1. Is speed mode gated? → `--speed thorough` or `--speed paranoid` (turbo/standard skip it)
2. Does project have frontend? → Stack detection should show `has_frontend: true`

**Solution:**
```bash
orchestrate --speed thorough "..."  # Explicitly request thorough mode
```

### "Playwright tests timeout"

**Check:**
1. Is server starting within `server_start_timeout_s`? → Check `docker compose logs app`
2. Are tests taking >120s? → Check individual test duration in `qa_browser_report.json`

**Solution:**
```yaml
qa_browser:
  server_start_timeout_s: 90     # Increase server startup timeout
  playwright_timeout_ms: 180000  # Increase test timeout to 3 min
```

### "Docker Compose fails to start"

**Check:**
1. Is Docker available? → `docker --version`
2. Is `docker-compose.yml` valid YAML? → `docker compose config`
3. Are ports already in use? → `lsof -i :3000`

**Solution:**
```bash
# Manually test compose
cd workspace/orchestrator-for-mobile-ui
docker compose up -d
docker compose logs
docker compose down -v
```

## See Also

- [QA-Browser Phase Feature Guide](./features/QA_BROWSER_PHASE.md)
- [Architecture Decision Record](./adr/qa_browser_phase.md)
- [Stack Detector API](./STACK_DETECTOR_API.md)
- [Playwright Testing Guide](./PLAYWRIGHT_TESTING_GUIDE.md)
