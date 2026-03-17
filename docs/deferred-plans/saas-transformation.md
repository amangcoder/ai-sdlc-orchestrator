# SaaS Transformation Proposal: AI SDLC Orchestrator

## Context

The orchestrator is currently a single-tenant CLI tool with file-based state. The user wants to evaluate turning it into a SaaS product — motivated by both commercialization and IP protection (preventing reverse engineering of orchestration logic, prompts, and workflow designs). This proposal covers architecture, migration path, billing, security, and IP protection.

---

## 1. Why SaaS (vs distributed software)

- **IP protection**: Customers never receive the code. Agent prompts, workflow logic, spawn policies, and model routing stay server-side.
- **No deployment friction**: Users connect a repo and submit a feature request via API/UI — no Python install, no Claude API key management.
- **Usage-based monetization**: Metered billing on tokens/runs aligns cost with value delivered.
- **Continuous improvement**: Update agent prompts and workflows without customer upgrades.

---

## 2. Target Architecture

```
                   ┌──────────┐
                   │  Web UI  │
                   └────┬─────┘
                        │
                   ┌────▼─────┐
                   │   API    │  ← JWT auth, rate limits, tenant scoping
                   │ Gateway  │     (extends existing FastAPI dashboard)
                   └────┬─────┘
                        │
          ┌─────────────┼─────────────┐
          │             │             │
    ┌─────▼─────┐ ┌────▼────┐ ┌─────▼──────┐
    │ Job Queue │ │ Postgres│ │ S3 / Object│
    │ (Redis)   │ │ (state) │ │ Storage    │
    └─────┬─────┘ └────┬────┘ └─────┬──────┘
          │             │             │
    ┌─────▼─────────────┴─────────────┘
    │ Orchestration Workers
    │ (engine.py + workflow_engine.py)
    └──┬──────────┬───────────┐
       │          │           │
  ┌────▼───┐ ┌───▼────┐ ┌───▼──────┐
  │ Agent  │ │ Repo   │ │Knowledge │
  │Executor│ │Service │ │ Service  │
  │(pods)  │ │(GitHub)│ │ (MCP)    │
  └────────┘ └────────┘ └──────────┘
```

### Service boundaries (aligned with existing code modules)

| Service | Current code | What changes |
|---|---|---|
| **API Gateway** | `dashboard/app.py` | Add JWT auth, tenant scoping, full CRUD, billing endpoints |
| **Orchestration Worker** | `engine.py`, `workflow_engine.py` | Becomes a queue consumer; uses injected `StateStore` + `AgentExecutor` |
| **Agent Executor** | `agents.py` | Runs in ephemeral containers with repo clone + workspace artifacts |
| **Repo Service** | `_create_worktree()` in `agents.py:64-93` | GitHub App integration; clones repos, collects patches post-execution |
| **Knowledge Service** | `knowledge.py` | HTTP MCP endpoint (replacing local stdio); caches indexes per repo+commit |
| **Metering** | `observability.py` (already tracks cost/tokens) | Writes to billing tables + Stripe |

---

## 3. Multi-Tenancy Model

**Shared infrastructure, isolated data** — single PostgreSQL database with `tenant_id` on every table + Row Level Security (RLS).

| Resource | Isolation |
|---|---|
| DB rows | `tenant_id` + RLS |
| Artifacts/logs | S3 prefix: `{tenant_id}/{run_id}/` |
| Agent execution | Ephemeral container per run, no shared filesystem |
| Git repos | Per-tenant clone in tmpfs, destroyed after run |
| Config | Per-tenant overrides stored in `tenant_configs` table |
| API keys | Per-tenant, hashed with bcrypt |

---

## 4. Key Abstraction Interfaces

The critical enabler: four protocol abstractions that let the same engine code work in CLI mode (files) and SaaS mode (database/cloud).

```python
# Replace persistence.py's save_run_state()
class StateStore(Protocol):
    async def save_run(self, state: RunState) -> None: ...
    async def load_run(self, run_id: str) -> RunState | None: ...
    async def list_runs(self, tenant_id: str, ...) -> list[RunSummary]: ...

# Replace agents.py's invoke_agent()
class AgentExecutor(Protocol):
    async def invoke(self, invocation: AgentInvocation) -> AgentResult: ...
    async def invoke_parallel(self, invocations: list[AgentInvocation]) -> list[AgentResult]: ...

# Replace worktree functions in agents.py
class RepoManager(Protocol):
    async def prepare(self, repo_id: str, branch: str) -> str: ...  # returns path
    async def collect_changes(self, path: str) -> Patch: ...
    async def cleanup(self, path: str) -> None: ...

# Replace RunLogger's JSONL writes
class EventStore(Protocol):
    async def append(self, run_id: str, event_type: str, data: dict) -> None: ...
    async def stream(self, run_id: str, after: str | None) -> AsyncIterator[Event]: ...
```

Current implementations (`FileStateStore`, `LocalAgentExecutor`, `LocalRepoManager`, `FileEventStore`) preserve CLI mode. SaaS adds `DatabaseStateStore`, `ContainerAgentExecutor`, `CloudRepoManager`, `DatabaseEventStore`.

---

## 5. Database Schema (key tables)

```sql
-- Tenancy
tenants       (id, name, slug, plan, settings_json, created_at)
users         (id, tenant_id, email, role, auth_provider, created_at)
api_keys      (id, tenant_id, key_hash, scopes, expires_at)

-- Repos
repos         (id, tenant_id, provider, clone_url, access_token_enc, default_branch)

-- Runs (replaces workspace/state.json)
runs          (id, tenant_id, repo_id, user_id, feature_request, workflow_type,
               status, current_step, total_cost_usd, config_json, created_at)

-- Steps (replaces PhaseState dict)
run_steps     (id, run_id, step_name, agent_role, status, cost_usd, error)

-- Tasks (replaces workflow_tasks list)
run_tasks     (id, run_id, step_id, description, status, dependencies_json)

-- Artifacts (replaces workspace/artifacts/*.json)
artifacts     (id, run_id, artifact_name, content_json, s3_path, schema_valid)

-- Events (replaces JSONL logs)
run_events    (id, run_id, event_type, data_json, created_at)

-- Billing
usage_records (id, tenant_id, run_id, metric_type, quantity, unit_cost_usd)
```

---

## 6. API Design (key endpoints)

```
POST   /api/v1/runs                          # Submit feature request (replaces CLI)
GET    /api/v1/runs                          # List runs (tenant-scoped)
GET    /api/v1/runs/{id}                     # Run detail
GET    /api/v1/runs/{id}/stream              # SSE live events (already exists)
POST   /api/v1/runs/{id}/cancel              # Cancel
GET    /api/v1/runs/{id}/artifacts/{name}    # Download artifact

POST   /api/v1/repos                         # Connect GitHub repo
GET    /api/v1/usage                         # Usage summary
PATCH  /api/v1/config                        # Tenant config overrides
```

Run creation request replaces CLI args:
```json
{
  "feature_request": "Build a todo app",
  "repo_id": "repo_abc",
  "workflow_type": "feature_development",
  "options": { "debate": false, "knowledge": true, "max_budget_usd": 25 },
  "webhook_url": "https://example.com/hooks/status"
}
```

---

## 7. Agent Execution in Cloud

1. Orchestration worker puts `AgentInvocation` on task queue
2. Agent executor pod provisions ephemeral container with:
   - Customer's repo (shallow clone from Repo Service)
   - Workspace artifacts from S3
   - Agent `.md` system prompt injected server-side
   - Network restricted to Anthropic API + Knowledge Service MCP endpoint
3. `claude_agent_sdk.query()` runs inside container
4. Result collected: output text, cost, modified files (git diff)
5. Container destroyed; patches merged by orchestration worker

**Parallel engineers**: Each gets its own clone (replacing local worktrees). Patches collected and merged sequentially post-execution.

---

## 8. Billing

| Metric | Source | Unit |
|---|---|---|
| Input tokens | `agent_result.input_tokens` | Per 1M |
| Output tokens | `agent_result.output_tokens` | Per 1M |
| Runs | Run count | Per run |
| Storage | S3 usage | Per GB/month |

**Tiers**: Free (5 runs/mo, Sonnet only) → Pro ($49/mo + usage) → Team ($199/mo) → Enterprise (custom)

**Enforcement**: Existing `check_budget()` in `observability.py` already gates on `max_budget_usd`. Wire it to the metering service for per-tenant enforcement.

**Integration**: Stripe subscriptions + metered billing. Each tenant = Stripe Customer.

---

## 9. IP Protection (layered)

| Layer | What it protects | Strength |
|---|---|---|
| **SaaS-only execution** | Everything — code never leaves the server | Very strong |
| **Compiled core** (for enterprise on-prem) | `engine.py`, `phases.py`, `spawning.py`, `agents.py` via Cython → `.so` | Strong |
| **Encrypted prompts** (for on-prem) | Agent `.md` files encrypted at rest, decrypted with license key | Moderate |
| **License server** | Premium features gated by heartbeat-validated license | Moderate |

SaaS model is the primary protection. For enterprise on-prem, Cython compilation of core modules avoids a Rust rewrite while making reverse engineering significantly harder than shipping `.py` source.

---

## 10. Migration Path

### Phase 0: Abstractions (4 weeks)
- Create `StateStore`, `AgentExecutor`, `RepoManager`, `EventStore` protocols
- Refactor `persistence.py`, `agents.py`, `observability.py` to use them
- Implement file-based defaults (CLI keeps working)
- **Files**: `persistence.py`, `agents.py`, `observability.py`, `engine.py`, `workflow_engine.py`

### Phase 1: Database + API (6 weeks)
- `DatabaseStateStore` via SQLAlchemy + asyncpg
- `S3ArtifactStore` for artifacts/logs
- Extend `dashboard/app.py` → full API Gateway with JWT auth
- Add tenant/user models, auth middleware
- **Files**: `dashboard/app.py`, `dashboard/data.py`, new `db/` module

### Phase 2: Job Queue + Multi-Tenancy (6 weeks)
- Redis job queue (run dispatch replaces synchronous `engine.run()`)
- `ContainerAgentExecutor` (K8s pods)
- `CloudRepoManager` (GitHub App)
- PostgreSQL RLS, rate limiting, budget enforcement
- **Files**: `agents.py`, new `queue/` module, new `repos/` module

### Phase 3: Billing + Dashboard (4 weeks)
- Stripe integration, usage metering
- React/Next.js frontend (replace Jinja2 templates)
- Webhook notifications, SSO, audit logging

### Phase 4: Enterprise (8 weeks)
- Custom workflow builder UI
- Team collaboration
- Self-hosted Helm chart
- Cython-compiled distribution for on-prem

---

## 11. Infrastructure Cost Estimate (~100 tenants, ~500 runs/month)

| Component | Monthly |
|---|---|
| K8s cluster (3 nodes) | ~$400 |
| RDS PostgreSQL (multi-AZ) | ~$300 |
| Redis | ~$150 |
| S3 | ~$12 |
| Agent compute (autoscaled pods) | ~$200 |
| Load balancer + networking | ~$50 |
| Monitoring | ~$50 |
| **Total** | **~$1,160** |

Break-even: ~24 Pro subscribers ($49/mo).

---

## 12. Key Risks

| Risk | Mitigation |
|---|---|
| Agent container sandbox escape | gVisor/Firecracker, dropped capabilities, no host network |
| API cost spikes | Per-tenant budget enforcement (already built), platform circuit breaker |
| Git clone latency | Shallow clones, S3-cached base layers, warm pool |
| Noisy neighbor | K8s resource quotas, priority classes by tier |
| Claude SDK breaking changes | Pin version, abstract behind `AgentExecutor` protocol |

---

## Verification

After Phase 0 (abstractions), verify:
- `pytest` passes with `FileStateStore` (no regression)
- `orchestrate --dry-run "test"` works identically via CLI
- New protocols are type-checked with `mypy`

After Phase 1 (database + API):
- `POST /api/v1/runs` creates a run visible in `GET /api/v1/runs`
- Run state persists across API server restarts (PostgreSQL)
- SSE streaming works for real-time events

After Phase 2 (multi-tenancy):
- Two tenants cannot see each other's runs
- Agent containers have no filesystem access outside their workspace
- Budget enforcement halts a run at limit
