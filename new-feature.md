# Centralized Monitoring, Artifact Management & Observability

## Context

The orchestrator has solid building blocks — Prometheus metrics (in-memory), OpenTelemetry tracing (no collector), JSONL event logging (flat files), webhook alerting, budget forecasting, timeline recording, and a FastAPI dashboard. However, these are disconnected: metrics die with the process, traces go nowhere, logs aren't searchable, artifacts have no lifecycle management, and there's no historical trend analysis. This plan unifies everything into a production-grade observability stack.

---

## Phase 1: Monitoring Stack Infrastructure (Docker Compose)

**Goal:** One-command `docker compose up` that stands up persistent Prometheus, Grafana, Jaeger, and Loki.

### New Files

| File | Purpose |
|------|---------|
| `infra/docker/docker-compose.monitoring.yml` | Monitoring stack services |
| `infra/monitoring/prometheus.yml` | Prometheus scrape config targeting orchestrator `:9090/metrics` |
| `infra/monitoring/promtail.yml` | Tails `workspace/**/run-*.jsonl` → ships to Loki with `run_id`, `event`, `agent` labels |
| `infra/monitoring/grafana/provisioning/datasources/datasources.yml` | Auto-registers Prometheus, Loki, Jaeger as Grafana data sources |
| `infra/monitoring/grafana/provisioning/dashboards/dashboard.yml` | Tells Grafana to scan `dashboards/` dir |
| `infra/scripts/start-monitoring.sh` | One-command startup + prints URLs |

### Services in `docker-compose.monitoring.yml`

| Service | Image | Ports | Volume | Notes |
|---------|-------|-------|--------|-------|
| `prometheus` | `prom/prometheus:v2.53.0` | `9091:9090` | `prometheus-data:/prometheus` | Host 9091 to avoid clash with MetricsManager on 9090 |
| `grafana` | `grafana/grafana:11.1.0` | `3000:3000` | `grafana-data:/var/lib/grafana` | Auto-provisioned datasources + dashboards |
| `jaeger` | `jaegertracing/all-in-one:1.58` | `16686:16686`, `4317:4317` | `jaeger-data:/badger` | Receives OTLP gRPC from existing `TracingManager` |
| `loki` | `grafana/loki:3.1.0` | `3100:3100` | `loki-data:/loki` | Log aggregation |
| `promtail` | `grafana/promtail:3.1.0` | — | bind workspace logs dir | Ships JSONL → Loki |

All join a new `orchestrator-monitoring` bridge network (separate from the egress-filtered `orchestrator-net`).

**Why Jaeger over Tempo:** Jaeger all-in-one is simpler (one image, built-in UI at `:16686`, Badger persistent storage). TracingManager already exports OTLP gRPC. Tempo would require Grafana-only query frontend.

### Config Changes

**Modify `src/orchestrator/monitoring/config.py`** — add fields:
```python
loki_enabled: bool = False
loki_endpoint: str = "http://localhost:3100"
grafana_url: str = "http://localhost:3000"
jaeger_ui_url: str = "http://localhost:16686"
```

**Modify `config/default.yaml`** — add corresponding entries under `monitoring:`.

**Modify `pyproject.toml`** — add `observability` optional dependency group:
```toml
observability = ["ai-sdlc-orchestrator[monitoring]", "python-logging-loki>=0.3.1"]
```

---

## Phase 2: Grafana Dashboards + New Metrics

**Goal:** Ship 5 pre-built Grafana dashboards as auto-provisioned JSON files.

### New Metrics (modify [metrics.py](src/orchestrator/monitoring/metrics.py))

Add to `MetricsManager.__init__()`:
- `burn_rate_usd_per_minute` (Gauge) — current cost velocity
- `run_duration_seconds` (Histogram, labels: `workflow_type`, `status`) — total run duration
- `artifacts_produced_total` (Counter, labels: `artifact_type`, `agent`) — artifact production

Add corresponding `record_*` methods. Wire from [MonitoringStack](src/orchestrator/monitoring/__init__.py) event handlers.

### Dashboard Files (all in `infra/monitoring/grafana/dashboards/`)

| Dashboard | Key Panels |
|-----------|------------|
| `run-overview.json` | Success/failure rate, active agents, runs/hr, phase duration heatmap, error rate by agent, budget utilization |
| `cost-analysis.json` | Cumulative cost, cost by agent (bar), cost by model (pie), token usage (stacked), cost per run trend, burn rate |
| `agent-performance.json` | Invocations table, success rate by agent, task duration by role, retry rate, latency p50/p95/p99 |
| `error-analysis.json` | Error count by type (pie), error timeline, most error-prone agents (table), crash recovery events (Loki log panel) |
| `slo-overview.json` | SLO compliance matrix, error budget gauges, success rate trend, violation timeline (built in Phase 5) |

### Prometheus Recording Rules

New file: `infra/monitoring/prometheus-rules.yml` — pre-aggregated queries for dashboard efficiency:
```yaml
- record: orchestrator:pipeline_success_rate:rate1h
  expr: sum(rate(orchestrator_run_total{status="completed"}[1h])) / sum(rate(orchestrator_run_total[1h]))
- record: orchestrator:phase_duration_p95:5m
  expr: histogram_quantile(0.95, rate(orchestrator_phase_duration_seconds_bucket[5m]))
- record: orchestrator:error_rate_per_run:rate1h
  expr: sum(rate(orchestrator_errors_total[1h])) / sum(rate(orchestrator_run_total[1h]))
```

---

## Phase 3: Artifact Management System

**Goal:** Versioned artifact storage with indexing, retention, cross-run comparison, and search. Fully backward-compatible.

### New File: `src/orchestrator/artifact_manager.py`

**Class: `ArtifactManager`**

Key methods:
- `save_artifact(run_id, name, data, agent, schema_name)` — writes current version + versioned copy + updates index
- `load_artifact(run_id, name, version=None)` — load artifact, optionally at specific version
- `list_artifacts(run_id)` → `list[ArtifactMetadata]`
- `get_artifact_history(run_id, name)` → `list[ArtifactVersion]`
- `compare_artifacts(run_a, run_b, name)` → `ArtifactDiff` — structural diff across runs
- `search_artifacts(type, agent, since, text_query)` → `list[ArtifactSearchResult]`
- `apply_retention_policy(max_age_days, max_runs, keep_failed)` → `RetentionResult`

### Storage Layout (backward-compatible extension)

```
workspace/runs/{run_id}/artifacts/
  prd.json                          # current version (existing, unchanged)
  architecture.json                 # existing
  .versions/                        # NEW
    prd/v1.json, v2.json
    architecture/v1.json
  .index.json                       # NEW: manifest with metadata per artifact
```

Existing code reading `artifacts/prd.json` directly continues to work. `.versions/` and `.index.json` are purely additive.

### Data Models

```python
@dataclass
class ArtifactMetadata:
    name: str; schema_name: str; agent: str; created_at: str
    updated_at: str; version: int; size_bytes: int; valid: bool

@dataclass
class ArtifactVersion:
    version: int; created_at: str; agent: str; size_bytes: int; checksum: str

@dataclass
class ArtifactDiff:
    artifact_name: str; run_id_a: str; run_id_b: str
    added_keys: list[str]; removed_keys: list[str]; changed_keys: list[str]; summary: str
```

### Integration Points

- **Modify [workflow_engine.py](src/orchestrator/workflow_engine.py):** Route artifact writes through `ArtifactManager.save_artifact()` instead of direct `json.dump`. `ArtifactCache` (read cache) stays unchanged.
- **Modify `config/default.yaml`:** Add `artifacts:` section (versioning_enabled, retention_max_age_days: 90, retention_max_runs: 200, retention_keep_failed: true).
- **New CLI: `orchestrate-artifacts`** — `list <run_id>`, `compare <run_a> <run_b> <name>`, `gc --max-age 90`, `search --type prd`.
- **New tests: `tests/test_artifact_manager.py`**

### Dashboard API Endpoints (modify [app.py](src/orchestrator/dashboard/app.py))

```
GET  /api/v1/runs/{run_id}/artifacts          → list with metadata
GET  /api/v1/runs/{run_id}/artifacts/{name}/versions → version history
GET  /api/v1/runs/{run_id}/artifacts/{name}/versions/{v} → specific version
GET  /api/v1/artifacts/compare?run_a=&run_b=&artifact= → diff
GET  /api/v1/artifacts/search?type=&agent=&q= → search
POST /api/v1/artifacts/retention              → trigger cleanup
```

---

## Phase 4: Log Aggregation & Correlation

**Goal:** Make JSONL events queryable with trace correlation, both via Loki and without it.

### New File: `src/orchestrator/monitoring/log_shipper.py`

**Class: `LokiLogShipper`** — pushes events to Loki via HTTP (alternative to Promtail for non-Docker runs):
- Background thread with batching (flush every 1s or 100 events)
- Graceful fallback if Loki unreachable
- Labels: `job=orchestrator`, `run_id`, `event`, `agent`

### Modify [observability.py](src/orchestrator/observability.py)

Enrich `log_event()` records with:
- `trace_id` — from `TracingManager` for Loki→Jaeger linking in Grafana
- `task_id` — explicit top-level field for correlation
- `agent` — top-level for Loki label extraction
- `level` — INFO/WARN/ERROR derived from event type

### Modify [MonitoringStack](src/orchestrator/monitoring/__init__.py)

Wire `LokiLogShipper` into `__init__()` when `loki_enabled=True`. Add `on_event()` method that `RunLogger` calls for every event to forward to the shipper.

### Standard LogQL Queries (document in `docs/observability.md`)

```
{job="orchestrator", run_id="abc123"}                              # all events for a run
{job="orchestrator", event="agent_result"} |= `"success":false`    # agent failures
{job="orchestrator", event="budget_warning"}                       # budget warnings
{job="orchestrator", event="step_end"} | json | duration_s > 60    # slow phases
```

---

## Phase 5: SLI/SLO Framework

**Goal:** Measurable service level indicators with error budgets.

### New File: `src/orchestrator/monitoring/slo.py`

**Class: `SLOTracker`**

| SLI | Target (default) |
|-----|---------|
| `pipeline_success_rate` | >= 95% |
| `phase_duration_p95` | <= 300s per phase |
| `cost_per_run_p50` | <= $0.50 (standard speed) |
| `artifact_validation_rate` | >= 99% |
| `error_rate` | <= 2 per run |
| `recovery_success_rate` | >= 90% |

Key methods: `record_run_result()`, `record_phase_result()`, `record_artifact_validation()`, `evaluate_slos(window_hours)` → `SLOReport`, `error_budget_remaining(slo_name)` → float.

Uses Prometheus HTTP API for evaluation when available, falls back to in-memory counters.

### Config Addition (`config/default.yaml`)

```yaml
monitoring:
  slo:
    enabled: false
    pipeline_success_rate: 0.95
    phase_duration_p95_seconds: 300
    cost_per_run_p50_usd: 0.50
    artifact_validation_rate: 0.99
    max_errors_per_run: 2
    evaluation_window_hours: 24
```

### Prometheus Alert Rules (add to `prometheus-rules.yml`)

```yaml
- alert: PipelineSuccessRateLow
  expr: orchestrator:pipeline_success_rate:rate1h < 0.95
- alert: ErrorBudgetExhausted
  expr: orchestrator:error_budget_remaining < 0.1
```

### New tests: `tests/test_slo.py`

---

## Phase 6: Enhanced Dashboard & CLI

**Goal:** Tie everything together in the FastAPI dashboard + new CLI tools.

### New Dashboard Pages (modify [app.py](src/orchestrator/dashboard/app.py))

| Route | Template | Content |
|-------|----------|---------|
| `/cost-analytics` | `cost_analytics.html` | Cost trends (Chart.js), agent/model breakdown, burn rate |
| `/artifacts` | `artifacts.html` | Artifact browser with search, version history, cross-run diff viewer |
| `/slo` | `slo.html` | SLO compliance matrix with color-coded status |
| `/observability` | `observability.html` | Deep-links to Grafana dashboards, Jaeger trace search (pre-filtered by run_id), Loki log explorer |

**Modify [base.html](src/orchestrator/dashboard/templates/base.html):** Add nav items for new pages.

**Modify [data.py](src/orchestrator/dashboard/data.py):** Add `get_cost_analytics()`, `get_cost_by_agent()`, `get_cost_by_model()`, `get_error_trends()`.

**Design decision: Standalone Grafana with deep links** (not iframe embedding). The `/observability` page generates context-aware links like `{grafana_url}/d/run-overview?var-run_id={run_id}`.

### New CLI: `orchestrate-monitoring`

Add to `pyproject.toml`:
```toml
orchestrate-monitoring = "orchestrator.monitoring.cli:main"
```

Commands:
- `orchestrate-monitoring start` — `docker compose -f docker-compose.monitoring.yml up -d`
- `orchestrate-monitoring stop` — `docker compose down`
- `orchestrate-monitoring status` — health check all services
- `orchestrate-monitoring reset` — remove volumes + restart clean

### New File: `src/orchestrator/monitoring/cli.py`

---

## Phase Dependency Graph

```
Phase 1 (Infrastructure) ─────┬──→ Phase 2 (Grafana Dashboards)
                               ├──→ Phase 4 (Log Aggregation)
                               │
Phase 3 (Artifact Mgmt) ──────┤    (parallel with 1-2)
                               │
                               └──→ Phase 5 (SLI/SLO) ──→ Phase 6 (Dashboard + CLI)
```

Phases 1 and 3 can proceed in parallel. Phase 5 requires metrics (Phase 1) and artifact tracking (Phase 3). Phase 6 ties everything together.

---

## Files Summary

### New Files (24)

| File | Phase |
|------|-------|
| `infra/docker/docker-compose.monitoring.yml` | 1 |
| `infra/monitoring/prometheus.yml` | 1 |
| `infra/monitoring/prometheus-rules.yml` | 2, 5 |
| `infra/monitoring/promtail.yml` | 1 |
| `infra/monitoring/grafana/provisioning/datasources/datasources.yml` | 1 |
| `infra/monitoring/grafana/provisioning/dashboards/dashboard.yml` | 1 |
| `infra/monitoring/grafana/dashboards/run-overview.json` | 2 |
| `infra/monitoring/grafana/dashboards/cost-analysis.json` | 2 |
| `infra/monitoring/grafana/dashboards/agent-performance.json` | 2 |
| `infra/monitoring/grafana/dashboards/error-analysis.json` | 2 |
| `infra/monitoring/grafana/dashboards/slo-overview.json` | 5 |
| `infra/scripts/start-monitoring.sh` | 1 |
| `src/orchestrator/artifact_manager.py` | 3 |
| `src/orchestrator/monitoring/log_shipper.py` | 4 |
| `src/orchestrator/monitoring/slo.py` | 5 |
| `src/orchestrator/monitoring/cli.py` | 6 |
| `src/orchestrator/dashboard/templates/cost_analytics.html` | 6 |
| `src/orchestrator/dashboard/templates/artifacts.html` | 6 |
| `src/orchestrator/dashboard/templates/slo.html` | 6 |
| `src/orchestrator/dashboard/templates/observability.html` | 6 |
| `docs/observability.md` | 4 |
| `tests/test_artifact_manager.py` | 3 |
| `tests/test_slo.py` | 5 |
| `tests/test_log_shipper.py` | 4 |

### Modified Files (10)

| File | Phase | Changes |
|------|-------|---------|
| `src/orchestrator/monitoring/metrics.py` | 2 | Add burn_rate, run_duration, artifacts_produced metrics |
| `src/orchestrator/monitoring/config.py` | 1 | Add loki_*, grafana_url, jaeger_ui_url, SLO config fields |
| `src/orchestrator/monitoring/__init__.py` | 4, 5 | Wire LokiLogShipper + SLOTracker into MonitoringStack |
| `src/orchestrator/observability.py` | 4 | Add trace_id correlation, structured enrichment, shipper forwarding |
| `src/orchestrator/workflow_engine.py` | 3 | Route artifact writes through ArtifactManager |
| `src/orchestrator/dashboard/app.py` | 3, 6 | Add artifact, cost, SLO, observability routes |
| `src/orchestrator/dashboard/data.py` | 6 | Add cost analytics, error trends methods |
| `src/orchestrator/dashboard/templates/base.html` | 6 | Add nav items |
| `config/default.yaml` | 1, 3, 5 | Add artifacts:, monitoring.slo:, monitoring.loki_* sections |
| `pyproject.toml` | 1, 3, 6 | Add observability deps, orchestrate-artifacts + orchestrate-monitoring entry points |

---

## Verification Plan

### Phase 1 Verification
```bash
bash infra/scripts/start-monitoring.sh
# Verify all 5 services healthy:
docker compose -f infra/docker/docker-compose.monitoring.yml ps
curl http://localhost:9091/-/healthy          # Prometheus
curl http://localhost:3000/api/health         # Grafana
curl http://localhost:16686/                  # Jaeger UI
curl http://localhost:3100/ready              # Loki
```

### Phase 2 Verification
- Open Grafana at http://localhost:3000, verify 4 dashboards auto-provisioned
- Run `orchestrate --dry-run "test"` with `metrics_enabled: true`
- Verify metrics appear in Prometheus targets and Grafana panels populate

### Phase 3 Verification
```bash
pytest tests/test_artifact_manager.py -v
# Run an orchestration, then:
orchestrate-artifacts list <run_id>
orchestrate-artifacts compare <run_a> <run_b> prd
orchestrate-artifacts gc --max-age 90 --dry-run
```

### Phase 4 Verification
- Run with `loki_enabled: true`, verify events appear in Grafana Explore → Loki
- Query: `{job="orchestrator"} | json` returns structured events
- Verify trace_id correlation links to Jaeger traces

### Phase 5 Verification
```bash
pytest tests/test_slo.py -v
# After several runs, check SLO dashboard in Grafana
# Verify Prometheus recording rules: curl http://localhost:9091/api/v1/rules
```

### Phase 6 Verification
- Navigate dashboard: `/cost-analytics`, `/artifacts`, `/slo`, `/observability`
- Verify `orchestrate-monitoring status` reports all services healthy
- Verify deep links from `/observability` page open correct Grafana/Jaeger pages

---

## Key Design Decisions

1. **Separate compose file** (`docker-compose.monitoring.yml`) — monitoring is infrastructure, not the orchestrator. Users opt in explicitly.
2. **Jaeger over Tempo** — simpler (one image, built-in UI, Badger storage). TracingManager already exports OTLP gRPC natively.
3. **Standalone Grafana with deep links** — no iframe embedding (avoids auth bypass, security concerns). Dashboard generates context-aware URLs.
4. **Backward-compatible artifact versioning** — `.versions/` directory is additive. Existing `artifacts/prd.json` reads unchanged.
5. **All features opt-in** — follows existing `HAS_*` / `try/except ImportError` graceful degradation pattern.
6. **Dual log shipping** — Promtail (Docker) + LokiLogShipper (native Python) for flexibility.
