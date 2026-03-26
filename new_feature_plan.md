# Research Cache + Research MCP — Implementation Plan

## Context

The orchestrator spawns full Claude agents (deep_researcher, market_researcher, competitor_researcher) every time research is needed — expensive (~$0.10–$0.50 per spawn) and slow (~30–60s each). Frequently, the same tech stack knowledge is re-researched across runs and projects.

**Goal:** Add a two-tier persistent research cache + a Research MCP server so agents check cached knowledge first and only do fresh research on cache miss. Surface accumulated findings as end-of-run recommendations.

---

## Files to Create

### 1. `src/orchestrator/research_cache.py` — Cache logic + MCP integration
Following `test_runner.py` pattern exactly.

```python
MCP_SERVER_KEY = "research-cache"

# Pydantic models
class ResearchEntry:
    topic: str
    content: str
    tags: list[str]
    tier: Literal["global", "project"]
    created_at: str  # ISO datetime
    ttl_days: int
    source_phase: str
    run_id: str
    usage_count: int = 0

class Finding:
    type: Literal["performance", "architecture", "security", "dependency", "quality"]
    severity: Literal["high", "medium", "low"]
    finding: str
    recommendation: str
    phase: str

# Core functions (mirror test_runner.py pattern)
def get_research_mcp_config(server_path: str, project_root: Path) -> dict | None
def ensure_research_mcp_config(server_path: str, target_project: Path) -> bool
def cleanup_research_mcp_config(target_project: Path) -> None

# Cache operations (called by engine, NOT by agents directly)
def load_cache(global_dir: Path, local_dir: Path) -> ResearchCache
def lookup(cache: ResearchCache, query: str, tags: list[str]) -> list[ResearchEntry]
def save_entry(cache: ResearchCache, entry: ResearchEntry) -> None
def extract_research_from_artifact(artifact_path: Path, phase: str, run_id: str) -> list[ResearchEntry]

# Findings accumulator (per-run, in-memory)
def flag_finding(findings: list[Finding], finding: Finding) -> None
def format_recommendations(findings: list[Finding]) -> str
```

### 2. Research MCP Server (Node.js)
Location: `../sdlc-mcp-servers/research-cache/` (sibling to existing MCP servers)

**Tools exposed to agents:**
| Tool | Purpose |
|------|---------|
| `lookup_research(query, tags?)` | Search cache, return hits or miss signal |
| `save_research(topic, content, tier, tags, ttl_days?)` | Store new finding |
| `flag_finding(type, severity, finding, recommendation)` | Accumulate actionable recommendation |
| `get_run_recommendations()` | Return all flagged findings for current run |

**Storage format:** JSON files
- `~/.orchestrator/research/index.json` — global tier index
- `~/.orchestrator/research/entries/` — individual entry files (keyed by hash of topic+tags)
- `.knowledge/research/index.json` — project tier index
- `.knowledge/research/entries/` — project entry files

**Lookup logic:** Tag intersection + substring match on topic. No vectors. Sorted by relevance (tag overlap count) then recency.

---

## Files to Modify

### 3. `src/orchestrator/models.py` — Add config models

Add after `TestRunnerConfig`:
```python
class ResearchCacheConfig(BaseModel):
    enabled: bool = True
    global_dir: str = "~/.orchestrator/research"
    local_dir: str = ".knowledge/research"
    server_path: str = ""  # empty = auto-detect
    base_ttl_days: int = 90
    volatile_ttl_days: int = 7
    max_entries: int = 500
    max_inject_bytes: int = 2048
    inject_into_phases: list[str] = ["pm", "architect", "principal_engineer"]
    auto_extract: bool = True
    cleanup_mcp_config: bool = True

class ResearchCacheContext(BaseModel):
    cache_loaded: bool = False
    mcp_configured: bool = False
    mcp_server_config: dict[str, Any] | None = None
    global_entry_count: int = 0
    local_entry_count: int = 0
    findings: list[dict] = []  # Accumulated per-run findings
```

Add to `OrchestratorConfig`:
```python
research_cache: ResearchCacheConfig = Field(default_factory=ResearchCacheConfig)
research_cache_context: ResearchCacheContext | None = None
```

### 4. `config/default.yaml` — Add research_cache section

After `test_runner:` block:
```yaml
research_cache:
  enabled: true
  global_dir: "~/.orchestrator/research"
  local_dir: ".knowledge/research"
  server_path: ""
  base_ttl_days: 90
  volatile_ttl_days: 7
  max_entries: 500
  max_inject_bytes: 2048
  inject_into_phases: [pm, architect, principal_engineer]
  auto_extract: true
  cleanup_mcp_config: true
```

### 5. `src/orchestrator/engine.py` — Integrate into pipeline

**At initialization (after test_runner setup, ~line 286):**
```python
if self.config.research_cache.enabled:
    rc_config = get_research_mcp_config(self.config.research_cache.server_path, self.project_root)
    if rc_config:
        self._research_mcp_config = rc_config
        self.config.research_cache_context = ResearchCacheContext(
            cache_loaded=True, mcp_configured=True, mcp_server_config=rc_config
        )
```

**In `_mcp_servers` property (~line 124):**
```python
rc = self.config.research_cache_context
if rc and rc.mcp_server_config:
    servers.update(rc.mcp_server_config)
```

**After each phase completion (post-phase hook):**
```python
if self.config.research_cache.auto_extract:
    entries = extract_research_from_artifact(artifact_path, phase_name, state.run_id)
    for entry in entries:
        save_entry(cache, entry)
```

**At end of run (before cleanup, ~line 379):**
```python
if self.config.research_cache_context and self.config.research_cache_context.findings:
    recs = format_recommendations(self.config.research_cache_context.findings)
    print(recs)  # Surface to user
```

**Cleanup:**
```python
if self.config.research_cache.cleanup_mcp_config:
    cleanup_research_mcp_config(self.project_root)
```

### 6. `src/orchestrator/workflow_engine.py` — Same MCP merge

Add to `_mcp_servers` property (mirrors engine.py):
```python
rc = self.config.research_cache_context
if rc and rc.mcp_server_config:
    servers.update(rc.mcp_server_config)
```

### 7. `src/orchestrator/phases.py` — Prompt injection

**Add `_inject_research_context(config, role)` function:**
- If role in `inject_into_phases` and research cache MCP is configured
- Add section: `## Research Cache\nUse the `lookup_research` MCP tool before doing any external research or spawning research agents. Only research fresh if cache returns no results.`
- Inject into relevant prompt builders (PM, Architect, Principal Engineer)

**Modify `_inject_mcp_role_guidance()` (~line 648):**
- Add research-cache tools to the MCP tool table for relevant roles
- Include: `lookup_research`, `save_research`, `flag_finding`

### 8. Agent definition updates

**`.claude/agents/deep_researcher.md`** — Add to top of process:
```
Before researching any topic, call lookup_research(query, tags) to check if findings already exist.
If cache returns fresh results, use them directly and skip external research.
After completing fresh research, call save_research() to cache your findings.
When you discover actionable issues, call flag_finding() to surface them as recommendations.
```

**Same pattern for:** `market_researcher.md`, `competitor_researcher.md`, `architect.md`

### 9. `src/orchestrator/main.py` — End-of-run output

After the run summary (~line 461), add:
```python
if state and hasattr(config, 'research_cache_context'):
    rc = config.research_cache_context
    if rc and rc.findings:
        print("\n💡 Recommendations:")
        for f in rc.findings:
            print(f"  [{f['severity']}] {f['finding']}")
            print(f"    → {f['recommendation']}")
```

---

## Auto-Extraction Logic

After each phase completes, extract research-worthy content from artifacts:

| Artifact | Fields to Extract | Cache Tier |
|----------|-------------------|------------|
| `architecture.json` | `tech_decisions[]` — each decision with rationale | Global (tech knowledge) |
| `engineering_plan.json` | Library selections, patterns | Global |
| `market_research.json` | `trends[]`, `recommendations[]` | Project |
| `competitor_research.json` | `competitors[]`, `feature_matrix` | Project |
| `threat_model.json` | `recommendations[]` | Global (security patterns) |
| `benchmark_report.json` | `bottlenecks[]`, `recommendations[]` | Project |

Each extracted entry gets tagged with source artifact type + key terms from content.

---

## Implementation Order

1. **Config models** (`models.py`, `default.yaml`) — foundation
2. **`research_cache.py`** — core cache logic, MCP integration functions
3. **Research MCP server** (Node.js, `../sdlc-mcp-servers/research-cache/`)
4. **Engine integration** (`engine.py`, `workflow_engine.py`) — MCP merge + post-phase extraction
5. **Prompt injection** (`phases.py`) — research-first instructions
6. **Agent updates** — add lookup-first instructions to researcher agents
7. **End-of-run output** (`main.py`) — surface recommendations
8. **Testing** — unit tests for cache operations, integration test for full flow

---

## Verification

1. **Unit test:** `lookup()` returns hits for matching tags, misses for unknown topics
2. **Unit test:** `extract_research_from_artifact()` correctly extracts from architecture.json
3. **Unit test:** TTL expiry works — entries older than TTL are treated as miss
4. **Integration test:** `orchestrate --dry-run` shows research MCP in merged server config
5. **E2E test:** Run orchestrate twice with same tech stack — second run should show cache hits in agent output and skip research agent spawns
6. **Manual:** Run `orchestrate "Add push notifications"` → check `~/.orchestrator/research/` for global entries and `.knowledge/research/` for project entries → check end-of-run recommendations output
