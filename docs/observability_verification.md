# Observability & Monitoring — End-to-End Verification Checklist

**Feature:** Centralized Monitoring, Artifact Management & Observability
**Document Version:** 1.0
**Status:** QA Sign-off Required
**Dependencies:** Docker ≥ 24, Python 3.11+, `pip install .[observability]`

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Section 1 — Infrastructure Setup](#section-1--infrastructure-setup)
3. [Section 2 — Grafana Auto-Provisioning](#section-2--grafana-auto-provisioning)
4. [Section 3 — Metrics Flow (Prometheus)](#section-3--metrics-flow-prometheus)
5. [Section 4 — Artifact Versioning](#section-4--artifact-versioning)
6. [Section 5 — Log Aggregation (Loki)](#section-5--log-aggregation-loki)
7. [Section 6 — SLO Tracking](#section-6--slo-tracking)
8. [Section 7 — Dashboard Pages](#section-7--dashboard-pages)
9. [Section 8 — CLI Tools](#section-8--cli-tools)
10. [Section 9 — Graceful Degradation](#section-9--graceful-degradation)
11. [Section 10 — Backward Compatibility](#section-10--backward-compatibility)
12. [Sign-off Matrix](#sign-off-matrix)
13. [Troubleshooting](#troubleshooting)

---

## Prerequisites

Before running any verification steps, ensure the following are in place:

| Requirement | Check Command | Expected Result |
|---|---|---|
| Docker Engine running | `docker info` | No error; shows server version |
| Docker Compose v2 | `docker compose version` | `Docker Compose version v2.x.x` |
| Python 3.11+ | `python --version` | `Python 3.11.x` or newer |
| Observability extras installed | `python -c "import httpx; print('ok')"` | `ok` |
| Project repo checked out | `ls infra/docker/docker-compose.monitoring.yml` | File exists |
| No port conflicts | `lsof -i :9091,3000,16686,3100 2>/dev/null` | Empty output (ports free) |
| Workspace directory exists | `ls workspace/` | Directory listed |

**Install observability extras if not already installed:**
```bash
pip install -e ".[observability]"
```

**On Linux only — add Docker host gateway:**
```bash
# Prometheus uses host.docker.internal; on Linux, add this line to
# docker-compose.monitoring.yml under the prometheus service:
#   extra_hosts:
#     - "host.docker.internal:host-gateway"
```

---

## Section 1 — Infrastructure Setup

**Goal:** Verify that all 5 monitoring services start, become healthy, bind the correct host ports, and use named Docker volumes for persistence.

### Step 1.1 — Start the monitoring stack

```bash
bash infra/scripts/start-monitoring.sh
```

**Expected output:**
```
Starting orchestrator monitoring stack...
[+] Running 5/5
 ✔ Container orchestrator-prometheus  Started
 ✔ Container orchestrator-grafana     Started
 ✔ Container orchestrator-jaeger      Started
 ✔ Container orchestrator-loki        Started
 ✔ Container orchestrator-promtail    Started

Monitoring stack is running:
  Prometheus  → http://localhost:9091
  Grafana     → http://localhost:3000  (admin / admin)
  Jaeger      → http://localhost:16686
  Loki        → http://localhost:3100
```

**Pass criteria:** Command exits with code 0; all 5 container names printed as `Started`.
**Fail criteria:** Any container shows `Error` or `Exited`.

---

### Step 1.2 — Verify all 5 services healthy

Wait up to 60 seconds for health checks to pass, then run:

```bash
docker compose -f infra/docker/docker-compose.monitoring.yml ps
```

**Expected output (abbreviated):**

| Name | Status | Ports |
|---|---|---|
| orchestrator-prometheus | `Up (healthy)` | `0.0.0.0:9091->9090/tcp` |
| orchestrator-grafana | `Up (healthy)` | `0.0.0.0:3000->3000/tcp` |
| orchestrator-jaeger | `Up (healthy)` | `0.0.0.0:16686->16686/tcp, 0.0.0.0:4317->4317/tcp` |
| orchestrator-loki | `Up (healthy)` | `0.0.0.0:3100->3100/tcp` |
| orchestrator-promtail | `Up` | — |

**Pass criteria:** Prometheus, Grafana, Jaeger, and Loki all show `(healthy)` in Status column within 60 seconds of startup.
**Fail criteria:** Any service shows `(starting)` after 60 seconds, `(unhealthy)`, or `Exited`.

---

### Step 1.3 — Verify port bindings

```bash
bash infra/scripts/start-monitoring.sh --status
```

**Expected output:**
```
Monitoring stack status:

NAME                        STATUS           PORTS
orchestrator-grafana        Up ...           0.0.0.0:3000->3000/tcp
orchestrator-jaeger         Up ...           0.0.0.0:16686->16686/tcp, 0.0.0.0:4317->4317/tcp, 0.0.0.0:4318->4318/tcp
orchestrator-loki           Up ...           0.0.0.0:3100->3100/tcp
orchestrator-prometheus     Up ...           0.0.0.0:9091->9090/tcp
orchestrator-promtail       Up ...

Service health:
  Prometheus: healthy
  Grafana: healthy
  Jaeger: healthy
  Loki: healthy
```

**Pass criteria:** All 4 listed services report `healthy`. Port 9091 (not 9090) used for Prometheus to avoid conflict with the orchestrator's in-process MetricsManager.
**Fail criteria:** Any service reports `unreachable`; Prometheus listening on 9090 (conflict).

**Individual health checks (verify manually):**
```bash
curl -sf http://localhost:9091/-/healthy && echo "Prometheus: OK"
curl -sf http://localhost:3000/api/health && echo "Grafana: OK"
curl -sf http://localhost:16686/ -o /dev/null && echo "Jaeger: OK"
curl -sf http://localhost:3100/ready && echo "Loki: OK"
```

Each command should print the `OK` suffix with exit code 0.

---

### Step 1.4 — Verify Docker volumes created

```bash
docker volume ls | grep -E 'prometheus-data|grafana-data|jaeger-data|loki-data'
```

**Expected output:**
```
local     prometheus-data
local     grafana-data
local     jaeger-data
local     loki-data
```

**Pass criteria:** All 4 named volumes listed.
**Fail criteria:** Missing volumes (data will not persist across restarts).

**Verify persistence (optional but recommended):**
```bash
# Restart stack and verify data survives
bash infra/scripts/start-monitoring.sh --stop
bash infra/scripts/start-monitoring.sh
# Navigate to http://localhost:3000 — previously configured dashboards should still be present
```

---

## Section 2 — Grafana Auto-Provisioning

**Goal:** Verify that Grafana auto-provisions data sources and dashboards on first startup without any manual configuration.

### Step 2.1 — Verify data sources registered

1. Open **http://localhost:3000** in a browser
2. Log in with **admin / admin** (change on first login prompt or click "Skip")
3. Navigate to **Connections → Data Sources** (left sidebar)

**Expected data sources (3 total):**

| Name | Type | URL | Status |
|---|---|---|---|
| Prometheus | prometheus | `http://prometheus:9090` | Green checkmark |
| Loki | loki | `http://loki:3100` | Green checkmark |
| Jaeger | jaeger | `http://jaeger:16686` | Green checkmark |

**Pass criteria:** All 3 data sources listed without manual configuration; each shows `Data source connected` when tested.
**Fail criteria:** Data sources absent; "Data source not found" errors; manual setup required.

**CLI alternative verification:**
```bash
curl -sf -u admin:admin http://localhost:3000/api/datasources | python -m json.tool | grep '"name"'
```
Expected output:
```
"name": "Prometheus",
"name": "Loki",
"name": "Jaeger",
```

---

### Step 2.2 — Verify 5 dashboards auto-provisioned

Navigate to **Dashboards** in the Grafana sidebar.

**Expected dashboards (5 total):**

| Dashboard Title | File | Purpose |
|---|---|---|
| Orchestrator — Run Overview | `run-overview.json` | Success/failure rates, active agents, phase heatmap |
| Orchestrator — Cost Analysis | `cost-analysis.json` | Cumulative cost, cost by agent/model, burn rate |
| Orchestrator — Agent Performance | `agent-performance.json` | Invocations, success rate, latency percentiles |
| Orchestrator — Error Analysis | `error-analysis.json` | Error counts, error-prone agents, crash recovery |
| Orchestrator — SLO Overview | `slo-overview.json` | SLO compliance matrix, error budget gauges |

**Pass criteria:** Exactly 5 dashboards visible; each opens without "Dashboard not found" errors; panels render (may show "No data" if metrics not yet flowing — panels should not show errors).
**Fail criteria:** Fewer than 5 dashboards; dashboards missing panels; JSON parse errors in Grafana logs.

**CLI alternative verification:**
```bash
curl -sf -u admin:admin http://localhost:3000/api/search?type=dash-db | python -m json.tool | grep '"title"'
```
Expected: 5 title entries printed.

---

### Step 2.3 — Verify default home dashboard

Open **http://localhost:3000** after login — the browser should land on the **Run Overview** dashboard, not the Grafana default home page.

**Pass criteria:** Run Overview dashboard displayed as home.
**Fail criteria:** Generic Grafana "Welcome to Grafana" home page shown.

---

## Section 3 — Metrics Flow (Prometheus)

**Goal:** Verify that enabling `metrics_enabled: true` causes metrics to appear in Prometheus and that dashboard panels populate.

### Step 3.1 — Enable metrics and run the orchestrator

Edit `config/default.yaml` (or set environment variable for a one-off run):

```yaml
# config/default.yaml
monitoring:
  metrics_enabled: true
  tracing_enabled: true  # optional, for trace correlation
```

Run a minimal orchestration (or a test script that exercises the pipeline):
```bash
python -m orchestrator.main --config config/default.yaml --feature "test feature for metrics verification"
```

Alternatively, to trigger metrics without a full run:
```bash
python - <<'EOF'
from orchestrator.monitoring.metrics import MetricsManager
from prometheus_client import CollectorRegistry
r = CollectorRegistry()
m = MetricsManager(port=9090, registry=r)
m.record_run_start("test-run-001", "feature_development")
m.record_burn_rate("feature_development", 0.05)
m.record_run_duration("feature_development", "success", 45.2)
m.record_artifact_produced("prd", "pm")
print("Metrics recorded")
EOF
```

---

### Step 3.2 — Verify metrics in Prometheus

Open **http://localhost:9091** (Prometheus UI) and execute these queries in the **Graph** tab:

| Metric | Query | Expected Result |
|---|---|---|
| Cost burn rate gauge | `orchestrator_burn_rate_usd_per_minute` | Numeric value |
| Run duration histogram | `orchestrator_run_duration_seconds_count` | Integer ≥ 1 |
| Artifacts produced counter | `orchestrator_artifacts_produced_total` | Integer ≥ 1 |

**Verify recording rules:**
```
orchestrator:pipeline_success_rate:rate1h
orchestrator:phase_duration_p95:5m
orchestrator:error_rate_per_run:rate1h
```
Each should return a numeric result (not "no data").

**Pass criteria:** All 3 new metrics present with correct label sets; recording rules return values.
**Fail criteria:** Metrics absent; `No datapoints found`; scrape target `orchestrator` in **Status → Targets** shows `DOWN`.

**Verify scrape target:**
```bash
curl -sf 'http://localhost:9091/api/v1/targets' | python -m json.tool | grep -E '"health"|"job"'
```
Expected: `"health": "up"` for `orchestrator` job.

---

### Step 3.3 — Verify dashboard panels populate

1. Open **http://localhost:3000/d/run-overview** (Run Overview dashboard)
2. Set time range to **Last 15 minutes**
3. Observe panels

**Expected panels to show data:**
- **Run Count** — shows total runs
- **Success Rate** — percentage gauge
- **Active Agents** — gauge or stat panel

4. Open **http://localhost:3000/d/cost-analysis** (Cost Analysis dashboard)

**Expected panels:**
- **Burn Rate** — current USD/minute gauge
- **Cost by Agent** — bar chart (may be empty if no full run completed)

**Pass criteria:** At least the metric-backed panels display values rather than "No data"; no panel shows a red error state.
**Fail criteria:** All panels show "No data" after 2 full minutes post-metrics-emission; panels show "Error executing query" messages.

---

## Section 4 — Artifact Versioning

**Goal:** Verify that `ArtifactManager` creates `.versions/` subdirectory, `v{N}.json` files, and `.index.json` on each artifact save, without breaking existing direct file reads.

### Step 4.1 — Enable artifact versioning

Ensure `config/default.yaml` has:
```yaml
artifacts:
  versioning_enabled: true
  index_enabled: true
```

---

### Step 4.2 — Save an artifact and verify versioning

```python
# verify_artifacts.py  (run from project root)
import json, pathlib, sys
sys.path.insert(0, "src")

from orchestrator.artifact_manager import ArtifactManager
from orchestrator.models import OrchestratorConfig

config = OrchestratorConfig()
config.artifacts.versioning_enabled = True
config.artifacts.index_enabled = True

# Use a dedicated test run ID
run_id = "verify-run-001"
workspace_dir = pathlib.Path("workspace")
runs_dir = workspace_dir / "runs" / run_id / "artifacts"
runs_dir.mkdir(parents=True, exist_ok=True)

from orchestrator.workspace_manager import WorkspaceManager
wm = WorkspaceManager(workspace_dir)

am = ArtifactManager(wm, config.artifacts)

# Save artifact 3 times
for i in range(1, 4):
    am.save_artifact(run_id, "prd", {"version_marker": i, "title": f"PRD v{i}"}, agent="pm")
    print(f"Saved version {i}")

# Verify files
artifact_dir = runs_dir
versions_dir = artifact_dir / ".versions" / "prd"
index_file = artifact_dir / ".index.json"

assert (artifact_dir / "prd.json").exists(), "FAIL: prd.json not found"
assert versions_dir.exists(), "FAIL: .versions/prd/ not created"
assert index_file.exists(), "FAIL: .index.json not created"

versions = sorted(versions_dir.glob("*.json"))
assert len(versions) == 3, f"FAIL: expected 3 versions, got {len(versions)}"

index = json.loads(index_file.read_text())
assert "prd" in index, "FAIL: prd not in .index.json"
assert index["prd"]["version"] == 3, f"FAIL: version count wrong: {index['prd']['version']}"

print("PASS: All artifact versioning checks passed")
print(f"  Current file:  {artifact_dir / 'prd.json'}")
print(f"  Versions:      {[v.name for v in versions]}")
print(f"  Index version: {index['prd']['version']}")
```

```bash
python verify_artifacts.py
```

**Expected output:**
```
Saved version 1
Saved version 2
Saved version 3
PASS: All artifact versioning checks passed
  Current file:  workspace/runs/verify-run-001/artifacts/prd.json
  Versions:      ['v1.json', 'v2.json', 'v3.json']
  Index version: 3
```

---

### Step 4.3 — Verify filesystem layout

```bash
find workspace/runs/verify-run-001/artifacts -type f | sort
```

**Expected output:**
```
workspace/runs/verify-run-001/artifacts/.index.json
workspace/runs/verify-run-001/artifacts/.versions/prd/v1.json
workspace/runs/verify-run-001/artifacts/.versions/prd/v2.json
workspace/runs/verify-run-001/artifacts/.versions/prd/v3.json
workspace/runs/verify-run-001/artifacts/prd.json
```

**Pass criteria:** All 5 files present; version numbers sequential starting at 1; `.index.json` present.
**Fail criteria:** `.versions/` directory absent; version gaps; `.index.json` missing.

---

### Step 4.4 — Verify `.index.json` schema

```bash
python -c "
import json, pathlib
idx = json.loads(pathlib.Path('workspace/runs/verify-run-001/artifacts/.index.json').read_text())
prd = idx['prd']
required = {'name', 'version', 'agent', 'saved_at', 'size_bytes'}
missing = required - set(prd.keys())
assert not missing, f'Missing keys: {missing}'
print('PASS: .index.json has all required fields')
print(f'  name={prd[\"name\"]}, version={prd[\"version\"]}, agent={prd[\"agent\"]}')
"
```

**Pass criteria:** Script prints `PASS`; all required metadata fields present.
**Fail criteria:** `KeyError` or `AssertionError`.

---

## Section 5 — Log Aggregation (Loki)

**Goal:** Verify that pipeline events appear in Loki with the correct labels (`job`, `run_id`, `event`, `agent`) and that `trace_id` enables correlation to Jaeger traces.

### Step 5.1 — Enable Loki shipping

```yaml
# config/default.yaml
monitoring:
  loki_enabled: true
  loki_endpoint: "http://localhost:3100"
  tracing_enabled: true
```

---

### Step 5.2 — Run a pipeline and trigger log events

```bash
python -m orchestrator.main --config config/default.yaml --feature "loki test run" 2>&1 | head -20
```

Or directly push test events:
```python
# test_loki_push.py
import sys, time
sys.path.insert(0, "src")
from orchestrator.monitoring.loki import LokiLogShipper

shipper = LokiLogShipper("http://localhost:3100")
test_run_id = f"test-loki-{int(time.time())}"

events = [
    {"event": "run_start", "run_id": test_run_id, "agent": "pm", "level": "INFO", "trace_id": "abc123def456"},
    {"event": "phase_complete", "run_id": test_run_id, "agent": "architect", "level": "INFO", "trace_id": "abc123def456", "phase": "pm"},
    {"event": "agent_error", "run_id": test_run_id, "agent": "qa", "level": "ERROR", "trace_id": "abc123def456", "error": "validation failed"},
]

for e in events:
    shipper.push(e)

shipper.flush()
shipper.shutdown()
print(f"Pushed 3 events with run_id={test_run_id}")
print(f"Query Loki: {{job=\"orchestrator\", run_id=\"{test_run_id}\"}}")
```

```bash
python test_loki_push.py
```

---

### Step 5.3 — Verify events appear in Loki

**Via Grafana Explore:**
1. Open **http://localhost:3000/explore**
2. Select **Loki** datasource
3. Enter LogQL query:
   ```logql
   {job="orchestrator"} | json
   ```
4. Click **Run query**

**Expected results:**
- Log lines appear with labels visible in the left panel
- Each entry shows structured JSON fields including `run_id`, `event`, `agent`, `level`

**Verify specific run events:**
```logql
{job="orchestrator", run_id="<your-test-run-id>"} | json
```
Expected: 3 log lines for the test run.

**Via Loki HTTP API:**
```bash
curl -sG 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={job="orchestrator"}' \
  --data-urlencode "start=$(date -v-5M +%s)000000000" \
  --data-urlencode "end=$(date +%s)000000000" \
  | python -m json.tool | grep -c '"stream"'
```
Expected: Integer ≥ 1 (number of log streams found).

**Pass criteria:** Events appear in Loki within 5 seconds of push; correct labels present (`job=orchestrator`, `run_id`, `event`, `agent`); structured JSON fields visible.
**Fail criteria:** No results in Loki after 30 seconds; labels missing; `trace_id` field absent.

---

### Step 5.4 — Verify `trace_id` correlation to Jaeger

If `tracing_enabled: true` and a run has produced traces:

1. Copy a `trace_id` from a Loki log entry (visible in JSON fields in Grafana Explore)
2. Open **http://localhost:16686** (Jaeger UI)
3. In the top-right search, paste the `trace_id`

**Expected result:** Trace appears with spans from the orchestrator run.

**Pass criteria:** `trace_id` from Loki links to a valid trace in Jaeger.
**Fail criteria:** Trace not found (may indicate tracing not enabled or Jaeger not receiving spans).

---

### Step 5.5 — Verify Promtail file-based shipping (Docker deployments)

If running via Docker Compose with Promtail:

```bash
# Check Promtail is tailing workspace logs
docker logs orchestrator-promtail 2>&1 | tail -20
```

**Expected log fragment:**
```
level=info msg="Tailing new file" path=/workspace/logs/run-*.jsonl
```

**Pass criteria:** Promtail tailing JSONL files from `/workspace/logs/`.
**Fail criteria:** Errors finding workspace mount; no files tailed.

---

## Section 6 — SLO Tracking

**Goal:** Verify that the SLO dashboard page (`/slo`) displays the correct compliance matrix with all 6 SLIs, correct target values, and color-coded status.

### Step 6.1 — Enable SLO tracking

```yaml
# config/default.yaml
monitoring:
  slo:
    enabled: true
    pipeline_success_rate: 0.95
    phase_duration_p95_seconds: 300
    cost_per_run_p50_usd: 0.50
    artifact_validation_rate: 0.99
    max_errors_per_run: 2
    recovery_success_rate: 0.90
    evaluation_window_hours: 24
```

---

### Step 6.2 — Start the dashboard

```bash
orchestrate-dashboard --port 8080 --config config/default.yaml
```

Or if running the orchestrator server directly:
```bash
python -m orchestrator.dashboard.cli --port 8080
```

---

### Step 6.3 — Verify `/slo` page renders

Open **http://localhost:8080/slo** (with valid `DASHBOARD_TOKEN` if auth is enabled).

**Expected page content:**

| SLI Name | Target | Color |
|---|---|---|
| Pipeline Success Rate | ≥ 95% | Green / Yellow / Red based on actual |
| Phase Duration p95 | ≤ 300s | Green / Yellow / Red |
| Cost per Run p50 | ≤ $0.50 | Green / Yellow / Red |
| Artifact Validation Rate | ≥ 99% | Green / Yellow / Red |
| Error Rate per Run | ≤ 2 | Green / Yellow / Red |
| Recovery Success Rate | ≥ 90% | Green / Yellow / Red |

**Color coding rules:**
- 🟢 **Green**: Error budget remaining ≥ 80%
- 🟡 **Yellow**: Error budget remaining 20–80%
- 🔴 **Red**: Error budget remaining < 20%

**Via API:**
```bash
curl -sf http://localhost:8080/api/v1/slo | python -m json.tool
```

**Expected JSON shape:**
```json
{
  "evaluated_at": "<ISO timestamp>",
  "evaluation_window_hours": 24,
  "all_passing": true,
  "slis": [
    {
      "name": "pipeline_success_rate",
      "target": 0.95,
      "actual": <float>,
      "passing": true,
      "error_budget_remaining_pct": <float 0-100>
    },
    ...
  ]
}
```

**Pass criteria:** Page renders without 500 errors; 6 SLI rows visible; all target values match `config/default.yaml`; JSON response has correct schema.
**Fail criteria:** 500 Internal Server Error; fewer than 6 SLIs; missing `error_budget_remaining_pct` field.

---

### Step 6.4 — Verify SLO math correctness

```python
# slo_math_verify.py
import sys
sys.path.insert(0, "src")
from orchestrator.monitoring.slo import SLOTracker
from orchestrator.monitoring.config import SLOConfig

cfg = SLOConfig(
    enabled=True,
    pipeline_success_rate=0.95,
    phase_duration_p95_seconds=300,
    cost_per_run_p50_usd=0.50,
    artifact_validation_rate=0.99,
    max_errors_per_run=2,
    recovery_success_rate=0.90,
)
tracker = SLOTracker(cfg)

# Simulate 100 runs with 3 failures (97% success)
for i in range(97):
    tracker.record_run(success=True, cost_usd=0.30, duration_s=120, errors=0)
for i in range(3):
    tracker.record_run(success=False, cost_usd=0.30, duration_s=120, errors=2)

report = tracker.evaluate_slos()

psr = next(s for s in report.slis if s.name == "pipeline_success_rate")
print(f"Actual success rate: {psr.actual:.2%}")
print(f"Passing (>=95%): {psr.passing}")
print(f"Error budget remaining: {psr.error_budget_remaining_pct:.1f}%")

assert abs(psr.actual - 0.97) < 0.01, f"Expected ~97%, got {psr.actual:.2%}"
assert psr.passing, "Should be passing (97% > 95% target)"
assert 35 < psr.error_budget_remaining_pct < 45, f"Budget should be ~40%, got {psr.error_budget_remaining_pct:.1f}%"
print("PASS: SLO math is correct")
```

```bash
python slo_math_verify.py
```

**Expected output:**
```
Actual success rate: 97.00%
Passing (>=95%): True
Error budget remaining: ~40.0%
PASS: SLO math is correct
```

**Pass criteria:** Script prints `PASS`; actual ≈ 97%; error budget ≈ 40%.
**Fail criteria:** `AssertionError`; budget calculation wrong.

---

## Section 7 — Dashboard Pages

**Goal:** Verify that all 4 new dashboard pages render without errors, navigation links work, and deep links to external tools open correctly.

### Step 7.1 — Start the dashboard (if not already running)

```bash
orchestrate-dashboard --port 8080 --config config/default.yaml &
DASHBOARD_PID=$!
sleep 2
```

---

### Step 7.2 — Verify all 4 new pages render

Test each page with a curl request (substitute `TOKEN` with your `DASHBOARD_TOKEN` if auth is configured):

```bash
# Check each page returns HTTP 200 with HTML content
for path in /cost-analytics /artifacts /slo /observability; do
  STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "http://localhost:8080$path")
  if [ "$STATUS" = "200" ]; then
    echo "PASS: $path → HTTP 200"
  else
    echo "FAIL: $path → HTTP $STATUS"
  fi
done
```

**Expected output:**
```
PASS: /cost-analytics → HTTP 200
PASS: /artifacts → HTTP 200
PASS: /slo → HTTP 200
PASS: /observability → HTTP 200
```

---

### Step 7.3 — Verify navigation links in base template

```bash
curl -sf http://localhost:8080/ | grep -E 'href="/(cost-analytics|artifacts|slo|observability)"'
```

**Expected output (4 lines):**
```html
<a ... href="/cost-analytics">💰 Cost Analytics</a>
<a ... href="/artifacts">📦 Artifacts</a>
<a ... href="/slo">📊 SLO</a>
<a ... href="/observability">🔍 Observability</a>
```

**Pass criteria:** All 4 href values present in the base HTML response.
**Fail criteria:** Missing navigation links; 404 responses for linked pages.

---

### Step 7.4 — Verify deep links on observability page

```bash
curl -sf http://localhost:8080/observability | python - <<'EOF'
import sys
html = sys.stdin.read()
checks = [
    ("Grafana run-overview link", 'localhost:3000' in html),
    ("Jaeger search link", 'localhost:16686' in html),
    ("Loki explore link", 'localhost:3000' in html and 'explore' in html.lower()),
    ("No iframes", '<iframe' not in html.lower()),
    ("Links open in new tab", 'target="_blank"' in html),
]
all_pass = True
for name, result in checks:
    status = "PASS" if result else "FAIL"
    print(f"  {status}: {name}")
    if not result:
        all_pass = False
sys.exit(0 if all_pass else 1)
EOF
```

**Expected output:**
```
  PASS: Grafana run-overview link
  PASS: Jaeger search link
  PASS: Loki explore link
  PASS: No iframes
  PASS: Links open in new tab
```

**Pass criteria:** All checks pass; no `<iframe>` elements (security requirement per REQ-019).
**Fail criteria:** Any check fails; iframes detected.

---

### Step 7.5 — Verify cost analytics API endpoint

```bash
curl -sf http://localhost:8080/api/v1/cost-analytics | python -m json.tool | grep -E '"cost_trend"|"by_agent"|"by_model"|"burn_rate"'
```

**Expected output (keys present):**
```
"cost_trend": [...],
"by_agent": [...],
"by_model": [...],
"burn_rate": ...
```

**Pass criteria:** JSON response has all 4 top-level keys.
**Fail criteria:** Missing keys; 500 error; non-JSON response.

---

### Step 7.6 — Verify artifact browser API endpoint

```bash
# List artifacts for the verify-run-001 test run
curl -sf "http://localhost:8080/api/v1/runs/verify-run-001/artifacts" | python -m json.tool
```

**Expected output (array of metadata objects):**
```json
[
  {
    "name": "prd",
    "version": 3,
    "agent": "pm",
    "saved_at": "...",
    "size_bytes": ...,
    "valid": true
  }
]
```

**Pass criteria:** HTTP 200; JSON array returned; required fields present.
**Fail criteria:** 404 (run not found); 500; missing metadata fields.

---

## Section 8 — CLI Tools

**Goal:** Verify that `orchestrate-monitoring` and `orchestrate-artifacts` CLI commands are installed and functional.

### Step 8.1 — Verify CLI tools installed

```bash
which orchestrate-monitoring && echo "orchestrate-monitoring: found"
which orchestrate-artifacts && echo "orchestrate-artifacts: found"
```

**Expected output:**
```
/path/to/venv/bin/orchestrate-monitoring
orchestrate-monitoring: found
/path/to/venv/bin/orchestrate-artifacts
orchestrate-artifacts: found
```

If not found, reinstall:
```bash
pip install -e ".[observability]"
```

---

### Step 8.2 — Verify `orchestrate-monitoring status` (stack running)

With the monitoring stack running:

```bash
orchestrate-monitoring status
```

**Expected output:**
```
Monitoring stack status:

  Prometheus: healthy
  Grafana: healthy
  Jaeger: healthy
  Loki: healthy
  Promtail: running
```

**Pass criteria:** All 5 services reported as `healthy` / `running`; command exits with code 0.
**Fail criteria:** Services show `unreachable`; non-zero exit code; `command not found`.

---

### Step 8.3 — Verify `orchestrate-monitoring status` (stack stopped)

```bash
bash infra/scripts/start-monitoring.sh --stop
orchestrate-monitoring status
```

**Expected output:**
```
Monitoring stack status:

  Prometheus: unreachable
  Grafana: unreachable
  Jaeger: unreachable
  Loki: unreachable
  Promtail: stopped
```

**Pass criteria:** All services reported as `unreachable` / `stopped`; command exits gracefully (0 or non-zero, but no crash).
**Fail criteria:** Command crashes with unhandled exception; misleading `healthy` status.

Restart the stack for subsequent steps:
```bash
bash infra/scripts/start-monitoring.sh
```

---

### Step 8.4 — Verify `orchestrate-artifacts list`

```bash
orchestrate-artifacts list --run-id verify-run-001
```

**Expected output (table format):**
```
Artifacts for run: verify-run-001
┌──────┬─────────┬───────┬─────────────────────────┬───────────┐
│ Name │ Version │ Agent │ Saved At                │ Size      │
├──────┼─────────┼───────┼─────────────────────────┼───────────┤
│ prd  │ 3       │ pm    │ 2026-03-27 ...          │ ... bytes │
└──────┴─────────┴───────┴─────────────────────────┴───────────┘
```

**Pass criteria:** Table rendered; `prd` artifact listed with version 3.
**Fail criteria:** Empty output; error; no table rendered.

---

### Step 8.5 — Verify `orchestrate-artifacts gc --dry-run`

```bash
orchestrate-artifacts gc --max-age 90 --max-runs 200 --dry-run
```

**Expected output:**
```
Dry-run mode: no files will be deleted.
Checking retention policy: max_age=90 days, max_runs=200, keep_failed=True

Runs that would be removed:
  (none — all runs within retention policy)

Dry-run complete. 0 runs would be removed.
```

(If old test runs exist, they would be listed.)

**Pass criteria:** Command completes without error; `--dry-run` is respected (no files actually deleted); output clearly states "Dry-run mode".
**Fail criteria:** Files actually deleted despite `--dry-run`; command errors out.

---

### Step 8.6 — Verify `orchestrate-artifacts search`

```bash
orchestrate-artifacts search --type prd
```

**Expected output:**
```
Searching artifacts... type=prd

Found 1 result(s):
  run_id=verify-run-001, name=prd, version=3, agent=pm
```

**Pass criteria:** Command returns results; type filtering works.
**Fail criteria:** No results returned; command crashes.

---

## Section 9 — Graceful Degradation

**Goal:** Verify that stopping Loki and Prometheus does not cause pipeline failures — the orchestrator must degrade gracefully and continue writing local JSONL files.

### Step 9.1 — Stop Loki and Prometheus

```bash
docker stop orchestrator-loki orchestrator-prometheus
```

Verify they are stopped:
```bash
docker ps --filter "name=orchestrator-loki" --filter "name=orchestrator-prometheus"
```
Expected: Empty output (containers not running).

---

### Step 9.2 — Run a pipeline with monitoring enabled

```bash
python -m orchestrator.main \
  --config config/default.yaml \
  --feature "degradation test run" \
  2>&1 | tee /tmp/degradation_test.log
```

---

### Step 9.3 — Verify pipeline completes without errors

```bash
grep -E 'ERROR|CRITICAL|Exception|Traceback' /tmp/degradation_test.log | grep -v 'Loki\|Prometheus\|unreachable\|connection refused'
```

**Expected output:** Empty (no unexpected errors).

Also check for expected graceful degradation log messages:
```bash
grep -E 'Loki|Prometheus' /tmp/degradation_test.log
```

**Expected output (examples):**
```
WARNING: LokiLogShipper: connection refused to http://localhost:3100 — skipping push
WARNING: SLOTracker: Prometheus unavailable, using in-memory fallback
```

**Pass criteria:** Pipeline run completes (exit code 0 or expected non-error exit); only Loki/Prometheus warnings in error output (no pipeline-blocking errors); local JSONL log files still written.
**Fail criteria:** Pipeline crashes with unhandled exception; runs fail due to missing monitoring dependencies; exceptions propagate from `LokiLogShipper`.

---

### Step 9.4 — Verify local JSONL fallback

```bash
ls -la workspace/logs/*.jsonl | tail -3
```

**Expected output:** JSONL files present with recent modification timestamps.

```bash
tail -5 workspace/logs/*.jsonl | python -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if line and not line.startswith(('=','-','>')):
        try:
            obj = json.loads(line)
            print(f'  event={obj.get(\"event\",\"?\")} agent={obj.get(\"agent\",\"?\")}')
        except:
            pass
" | head -10
```

**Expected output:** Lines showing event/agent fields from the degradation test run.

**Pass criteria:** JSONL files written even with Loki unavailable; events contain expected fields.
**Fail criteria:** No JSONL files written; files empty.

---

### Step 9.5 — Restart monitoring services

```bash
docker start orchestrator-loki orchestrator-prometheus
```

Verify they're healthy again:
```bash
bash infra/scripts/start-monitoring.sh --status
```

---

## Section 10 — Backward Compatibility

**Goal:** Verify that the existing `ArtifactCache` read path is completely unchanged — code that reads artifacts directly from `workspace/runs/{run_id}/artifacts/{name}.json` continues to work without modification.

### Step 10.1 — Verify `ArtifactCache.load_artifact` works unchanged

```python
# backward_compat_verify.py
import sys, pathlib
sys.path.insert(0, "src")
from orchestrator.persistence import ArtifactCache

# Verify the test artifact from Section 4 can be read via ArtifactCache
run_id = "verify-run-001"
workspace_dir = pathlib.Path("workspace")

cache = ArtifactCache(workspace_dir)
artifact = cache.load_artifact(run_id, "prd")

assert artifact is not None, "FAIL: ArtifactCache.load_artifact returned None"
assert "title" in artifact, f"FAIL: Expected 'title' in artifact, got keys: {list(artifact.keys())}"
assert artifact.get("version_marker") == 3, f"FAIL: Expected version_marker=3 (latest), got {artifact.get('version_marker')}"
print(f"PASS: ArtifactCache.load_artifact returned correct data")
print(f"  title={artifact.get('title')}")
print(f"  version_marker={artifact.get('version_marker')}")
```

```bash
python backward_compat_verify.py
```

**Expected output:**
```
PASS: ArtifactCache.load_artifact returned correct data
  title=PRD v3
  version_marker=3
```

**Pass criteria:** `load_artifact` returns current (latest) version data unchanged; no API changes required in calling code.
**Fail criteria:** `None` returned; wrong version returned; exception raised.

---

### Step 10.2 — Verify direct file read works unchanged

```bash
python -c "
import json, pathlib
# Direct file read (as existing code does)
artifact_path = pathlib.Path('workspace/runs/verify-run-001/artifacts/prd.json')
assert artifact_path.exists(), f'FAIL: {artifact_path} not found'
data = json.loads(artifact_path.read_text())
assert data.get('version_marker') == 3, f'FAIL: Expected 3, got {data.get(\"version_marker\")}'
print('PASS: Direct file read works unchanged')
print(f'  version_marker={data[\"version_marker\"]}')
"
```

**Expected output:**
```
PASS: Direct file read works unchanged
  version_marker=3
```

**Pass criteria:** Direct `json.load` from `artifacts/{name}.json` returns current-version data; no change in file location or format.
**Fail criteria:** File missing; wrong data returned; JSON format changed.

---

### Step 10.3 — Verify `.versions/` does not interfere with existing glob patterns

```python
# glob_compat_verify.py
import sys, pathlib
sys.path.insert(0, "src")

artifacts_dir = pathlib.Path("workspace/runs/verify-run-001/artifacts")
# Simulate the existing pattern of discovering artifacts
json_artifacts = list(artifacts_dir.glob("*.json"))
names = [f.stem for f in json_artifacts]

# Should NOT include .index (hidden file) but SHOULD include prd
assert "prd" in names, f"FAIL: 'prd' not found in {names}"
assert ".index" not in names, f"FAIL: .index.json unexpectedly included in glob results"
print(f"PASS: Glob pattern finds expected artifacts: {names}")
```

```bash
python glob_compat_verify.py
```

**Pass criteria:** Existing glob patterns still find artifact JSON files; hidden `.index.json` does not cause issues.
**Fail criteria:** `prd` not found; `.index` or versioned files polluting artifact list.

---

### Step 10.4 — Run existing integration tests

```bash
python -m pytest tests/integration/test_data_roundtrip.py -v
```

**Expected output:**
```
tests/integration/test_data_roundtrip.py::... PASSED
...
====== X passed in X.Xs ======
```

**Pass criteria:** All data round-trip integration tests pass.
**Fail criteria:** Any test fails that previously passed.

---

## Sign-off Matrix

| Section | Description | Tester | Date | Pass/Fail | Notes |
|---|---|---|---|---|---|
| §1 | Infrastructure Setup | | | | |
| §2 | Grafana Auto-Provisioning | | | | |
| §3 | Metrics Flow (Prometheus) | | | | |
| §4 | Artifact Versioning | | | | |
| §5 | Log Aggregation (Loki) | | | | |
| §6 | SLO Tracking | | | | |
| §7 | Dashboard Pages | | | | |
| §8 | CLI Tools | | | | |
| §9 | Graceful Degradation | | | | |
| §10 | Backward Compatibility | | | | |

**QA Sign-off:** _________________________  **Date:** ___________

**Criteria for full sign-off:** All 10 sections must pass. Any section with `Fail` requires a bug filed, a fix, and re-verification before sign-off.

---

## Troubleshooting

### T1 — Port conflict: address already in use

**Symptom:** `docker compose up` fails with `Bind for 0.0.0.0:3000 failed: port is already allocated`.

**Cause:** Another process is using one of the monitoring ports (3000, 9091, 16686, 3100).

**Fix:**
```bash
# Find the conflicting process
lsof -i :3000 -i :9091 -i :16686 -i :3100

# Kill the specific process (replace PID)
kill -9 <PID>

# Or change the host port in docker-compose.monitoring.yml, e.g.:
# "9092:9090"  (maps to host 9092 instead of 9091)
```

---

### T2 — Prometheus scrape target shows `DOWN`

**Symptom:** In Prometheus UI → Status → Targets, the `orchestrator` target shows `State: DOWN`.

**Cause:** Prometheus cannot reach the orchestrator's `/metrics` endpoint at `host.docker.internal:9090`.

**Fix (macOS):** Ensure the orchestrator is running with `metrics_enabled: true` and listening on port 9090.

**Fix (Linux):** The `host.docker.internal` hostname may not resolve. Add to `docker-compose.monitoring.yml` under the `prometheus` service:
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```
Then restart: `docker compose -f infra/docker/docker-compose.monitoring.yml restart prometheus`

---

### T3 — Grafana dashboards not loading (blank/empty)

**Symptom:** Grafana starts but dashboard list is empty, or dashboards show "Dashboard not found".

**Cause:** Dashboard provisioning directory mount is incorrect or JSON files are invalid.

**Fix:**
```bash
# Verify dashboard JSON files exist
ls infra/monitoring/grafana/dashboards/

# Verify provisioning config
cat infra/monitoring/grafana/provisioning/dashboards/dashboard.yml

# Check Grafana logs for provisioning errors
docker logs orchestrator-grafana 2>&1 | grep -i 'provision\|error\|dashboard'

# Force Grafana to reload provisioning
curl -sf -u admin:admin -X POST http://localhost:3000/api/admin/provisioning/dashboards/reload
```

---

### T4 — Loki returns `429 Too Many Requests`

**Symptom:** `LokiLogShipper` logs warn: `HTTP 429: Too Many Requests from Loki`.

**Cause:** Loki rate limiting triggered by high event volume during testing.

**Fix:**
```yaml
# Add to loki's docker-compose config under environment or config file:
# limits_config:
#   ingestion_rate_mb: 64
#   ingestion_burst_size_mb: 128
```

For testing, reduce batch rate:
```python
shipper = LokiLogShipper("http://localhost:3100", flush_interval=2.0, batch_size=50)
```

---

### T5 — `orchestrate-monitoring` / `orchestrate-artifacts` command not found

**Symptom:** `command not found: orchestrate-monitoring`

**Cause:** Package not installed with entry points.

**Fix:**
```bash
pip install -e ".[observability]"

# Verify entry points registered
pip show orchestrator-for-mobile-ui | grep -i location
python -c "from orchestrator.monitoring.cli import main; print('CLI importable')"
```

---

### T6 — Artifact `.versions/` directory not created

**Symptom:** After saving artifacts, `.versions/` directory is absent.

**Cause:** `versioning_enabled` is `False` in config (default is now `True` in `config/default.yaml`).

**Fix:**
```yaml
# Ensure in config/default.yaml:
artifacts:
  versioning_enabled: true
```

Verify the config is loaded:
```python
from orchestrator.models import OrchestratorConfig
c = OrchestratorConfig.from_yaml("config/default.yaml")
print(c.artifacts.versioning_enabled)  # must be True
```

---

### T7 — SLO page returns 500 Internal Server Error

**Symptom:** `GET /slo` returns HTTP 500.

**Cause:** `SLOTracker` not initialized (SLO not enabled); or `evaluate_slos()` raising an unhandled exception.

**Fix:**
```bash
# Check dashboard logs
python -m orchestrator.dashboard.cli --port 8080 2>&1 | grep -E 'ERROR|Exception'
```

If SLO not enabled, the page should render with neutral/default values, not 500. Ensure the `get_slo_report()` data layer function handles `slo_tracker is None` gracefully.

---

### T8 — Docker volumes not persisting after restart

**Symptom:** Grafana loses dashboards after `docker compose restart`.

**Cause:** Using `--volumes` or `-v` flag with `docker compose down`, which destroys named volumes.

**Fix:**
```bash
# Stop WITHOUT removing volumes:
docker compose -f infra/docker/docker-compose.monitoring.yml down
# (NOT: down -v)

# Only remove volumes intentionally with:
bash infra/scripts/start-monitoring.sh --reset  # destroys all historical data
```

---

### T9 — `trace_id` missing from Loki log entries

**Symptom:** Log entries in Loki don't have a `trace_id` field.

**Cause:** `tracing_enabled` is `False`, or `TracingManager` not initialized.

**Fix:**
```yaml
monitoring:
  tracing_enabled: true
  tracing_endpoint: "http://localhost:4317"
```

Verify Jaeger is accepting OTLP spans:
```bash
docker logs orchestrator-jaeger 2>&1 | grep -i 'grpc\|otlp\|collector'
```

---

### T10 — `httpx` not installed (Loki/SLO fallback issues)

**Symptom:** Warning: `LokiLogShipper using urllib fallback (httpx not installed)`.

**Cause:** `httpx` optional dependency not installed.

**Fix:**
```bash
pip install httpx>=0.24.0
# or
pip install -e ".[observability]"  # installs all observability deps
```

---

*End of Observability Verification Checklist*
*For questions, see the architecture documentation at `workspace/artifacts/architecture.json` or the feature PRD at `workspace/artifacts/prd.json`.*
