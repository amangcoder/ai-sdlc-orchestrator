# Feature: Research Cache + Research MCP Server

## Overview

The **Research Cache** is a two-tier persistent knowledge store that reduces research costs and pipeline latency by caching and reusing research findings across orchestration runs. Instead of spawning expensive research agents (deep_researcher, market_researcher, competitor_researcher) for every run, agents check the cache first and only perform fresh research on cache misses.

### Problem

The orchestrator currently spawns full Claude research agents for every run:
- **Cost:** $0.10–$0.50 per research agent spawn
- **Latency:** 30–60 seconds per research agent
- **Waste:** The same technology stack knowledge is re-researched across multiple runs and projects
- **Manual work:** Developers must parse agent output to find actionable recommendations

### Solution

A two-tier cache system with four MCP tools that agents use to:
1. **Check cache first** before spawning external research
2. **Persist findings** from fresh research for reuse
3. **Flag actionable issues** (performance, security, architecture concerns)
4. **Surface recommendations** at end of run

---

## Architecture

### Two-Tier Cache System

| Tier | Storage | Scope | Examples |
|------|---------|-------|----------|
| **Global** | `~/.orchestrator/research/` | User-level, cross-project | Tech decisions, security patterns, architecture patterns |
| **Project** | `.knowledge/research/` | Project-specific | Market trends, competitor data, project-specific benchmarks |

### Cache Entry Structure

Each cached research entry contains:
- **topic**: Research topic (e.g., "React Native performance patterns")
- **content**: Cached research findings (text)
- **tags**: Searchable keywords (e.g., `["react-native", "performance", "mobile"]`)
- **tier**: `"global"` or `"project"`
- **created_at**: ISO datetime when entry was created
- **ttl_days**: Time-to-live in days (entries expire after this period)
- **source_phase**: Which pipeline phase generated this (e.g., "architect")
- **run_id**: ID of the run that created this entry
- **usage_count**: How many times this entry has been retrieved (incremented on cache hit)

### Auto-Extraction

Research findings are automatically extracted from completed phase artifacts:

| Artifact | Source Fields | Tier | Example |
|----------|---------------|------|---------|
| `architecture.json` | `tech_decisions[]` | global | "Use PostgreSQL for schema flexibility" |
| `engineering_plan.json` | Library selections, patterns | global | "Next.js with TypeScript recommended" |
| `market_research.json` | `trends[]`, `recommendations[]` | project | "Market favors mobile-first design" |
| `competitor_research.json` | `competitors[]`, `feature_matrix` | project | "Competitor A uses real-time sync" |
| `threat_model.json` | `recommendations[]` | global | "OWASP Top 10 mitigation patterns" |
| `benchmark_report.json` | `bottlenecks[]`, `recommendations[]` | project | "N+1 query bottleneck identified" |

---

## MCP Tools

The Research Cache exposes four tools to agents via the `research-cache` MCP server.

### 1. `lookup_research`

Check the cache for existing research before spawning fresh research.

**Usage:**
```
lookup_research(query: string, tags?: string[]) -> {hits: ResearchEntry[], miss: boolean}
```

**Parameters:**
- `query` (required): Search query (e.g., "React Native performance")
- `tags` (optional): Keywords to narrow search (e.g., `["react-native", "performance"]`)

**Response:**
```json
{
  "hits": [
    {
      "topic": "React Native performance patterns",
      "content": "Use FlatList for large lists, avoid inline functions...",
      "tags": ["react-native", "performance", "mobile"],
      "created_at": "2026-03-15T10:30:00Z",
      "ttl_days": 90,
      "usage_count": 5
    }
  ],
  "miss": false
}
```

**Behavior:**
- Returns matching entries sorted by **tag overlap count** (descending), then by **recency** (descending)
- Excludes expired entries (entries where `created_at` is older than `ttl_days`)
- Returns `miss: true` if no entries match
- **Tag intersection matching:** Entry tags must have overlap with query tags

**Example Agent Usage:**
```python
# Agent: deep_researcher
findings = lookup_research(
    query="Python async/await best practices",
    tags=["python", "async", "performance"]
)
if findings["miss"]:
    # No cache hit — perform fresh research
    fresh_research = external_research_api.search("...")
    save_research(
        topic="Python async/await best practices",
        content=fresh_research,
        tier="global",
        tags=["python", "async", "performance"]
    )
else:
    # Use cached results directly
    return findings["hits"][0]["content"]
```

---

### 2. `save_research`

Persist new research findings to the cache after completing fresh research.

**Usage:**
```
save_research(
  topic: string,
  content: string,
  tier: "global" | "project",
  tags: string[],
  ttl_days?: number
) -> {saved: boolean, entry_id: string}
```

**Parameters:**
- `topic` (required): Research topic title
- `content` (required): Full research findings (max 50KB)
- `tier` (required): `"global"` for stable knowledge (tech decisions, security patterns), `"project"` for volatile knowledge (market trends, competitor data)
- `tags` (required): Searchable keywords (e.g., `["react-native", "performance"]`)
- `ttl_days` (optional): Time-to-live in days. If omitted:
  - Defaults to `base_ttl_days` (90 days)
  - Auto-selects `volatile_ttl_days` (7 days) for entries tagged with `"market"` or `"competitor"`

**Response:**
```json
{
  "saved": true,
  "entry_id": "a3f8e2c9d7b1..."
}
```

**Storage:**
- Entry files stored as JSON at: `~/.orchestrator/research/entries/<hash>.json` (global) or `.knowledge/research/entries/<hash>.json` (project)
- Hash computed as SHA-256 of `topic + sorted(tags)`
- Index updated atomically (write to temp file, then rename)

**Example Agent Usage:**
```python
# Agent: market_researcher
fresh_research = conduct_market_analysis()
save_research(
    topic="SaaS market trends 2026",
    content=fresh_research,
    tier="project",  # project-specific, volatile
    tags=["market", "saas", "trends"],
    ttl_days=30  # expires faster than stable knowledge
)
```

**Concurrent Write Safety:**
- The MCP server uses a serial async write queue to prevent concurrent read-modify-write races
- All writes are atomic (write-to-temp-then-rename) to prevent index corruption

---

### 3. `flag_finding`

Flag actionable issues discovered during research to surface as end-of-run recommendations.

**Usage:**
```
flag_finding(
  type: "performance" | "architecture" | "security" | "dependency" | "quality",
  severity: "high" | "medium" | "low",
  finding: string,
  recommendation: string,
  phase: string
) -> {flagged: boolean}
```

**Parameters:**
- `type` (required): Issue category
  - `"performance"`: Performance bottleneck or optimization opportunity
  - `"architecture"`: Architectural concern or pattern mismatch
  - `"security"`: Security vulnerability or threat pattern
  - `"dependency"`: Dependency or library issue
  - `"quality"`: Code quality, maintainability, or testing gap
- `severity` (required): `"high"`, `"medium"`, or `"low"`
- `finding` (required): Description of the issue (e.g., "N+1 query problem in user dashboard")
- `recommendation` (required): Suggested action (e.g., "Batch queries using dataloader pattern")
- `phase` (required): Pipeline phase that discovered this (e.g., "architect")

**Example Agent Usage:**
```python
# Agent: architect
if detects_n_plus_one_query():
    flag_finding(
        type="performance",
        severity="high",
        finding="N+1 query issue in user dashboard endpoint",
        recommendation="Implement batch loading with dataloader or similar pattern",
        phase="architect"
    )
```

---

### 4. `get_run_recommendations`

Retrieve all findings flagged during the current run.

**Usage:**
```
get_run_recommendations() -> {findings: Finding[]}
```

**Response:**
```json
{
  "findings": [
    {
      "type": "performance",
      "severity": "high",
      "finding": "N+1 query issue in user dashboard",
      "recommendation": "Implement batch loading with dataloader",
      "phase": "architect"
    },
    {
      "type": "security",
      "severity": "high",
      "finding": "No rate limiting on API endpoints",
      "recommendation": "Add rate limiter middleware (e.g., express-rate-limit)",
      "phase": "principal_engineer"
    }
  ]
}
```

---

## Configuration

### Enable/Disable the Feature

Add to `config/default.yaml`:

```yaml
research_cache:
  enabled: true                          # Set to false to disable the feature
  global_dir: "~/.orchestrator/research" # User-level cache location
  local_dir: ".knowledge/research"       # Project-level cache location
  server_path: ""                        # Auto-detect by default; set to custom path if needed
  base_ttl_days: 90                      # Default expiry for stable knowledge
  volatile_ttl_days: 7                   # Expiry for market/competitor data
  max_entries: 500                       # Max cache size per tier (LRU eviction)
  max_inject_bytes: 2048                 # Max bytes of cache context injected into prompts
  inject_into_phases: [pm, architect, principal_engineer]  # Which phases get cache injection
  auto_extract: true                     # Auto-extract findings from artifacts
  cleanup_mcp_config: true               # Remove MCP server config after run
```

### Customization Example

Override defaults for a specific project:

```bash
# Use a faster expiry for market research in this project
orchestrate \
  --config-override research_cache.volatile_ttl_days=3 \
  "Add real-time notifications"
```

---

## Usage Patterns

### Pattern 1: Cache-First Research

Agents should always check the cache first before performing external research:

```python
# Step 1: Check cache
cache_results = lookup_research(
    query="React Native performance optimization",
    tags=["react-native", "performance"]
)

# Step 2: Use cache if available
if not cache_results["miss"]:
    findings = cache_results["hits"][0]["content"]
    # Use cached findings directly
    return recommendations_from_findings(findings)

# Step 3: Fresh research only on cache miss
fresh_findings = perform_external_research(...)

# Step 4: Save findings for future runs
save_research(
    topic="React Native performance optimization",
    content=fresh_findings,
    tier="global",
    tags=["react-native", "performance"],
    ttl_days=90
)
```

### Pattern 2: Surfacing Actionable Issues

When agents discover actionable issues (performance bottlenecks, security gaps, architecture concerns), they should flag them:

```python
# Discover issue
if analysis.has_n_plus_one_queries:
    flag_finding(
        type="performance",
        severity="high",
        finding=f"N+1 query in {endpoint}: {description}",
        recommendation=f"Use dataloader pattern or batch query API",
        phase="architect"
    )
```

### Pattern 3: Project-Specific vs. Global Knowledge

- **Global tier**: Tech decisions, architecture patterns, security patterns (reusable across projects)
- **Project tier**: Market trends, competitor analysis, project benchmarks (specific to this project)

```python
# Global — technology knowledge
save_research(
    topic="PostgreSQL performance tuning",
    tier="global",  # Reuse across projects
    tags=["postgresql", "performance", "database"]
)

# Project — market insights
save_research(
    topic="React Native adoption in our market segment",
    tier="project",  # Only for this project
    tags=["market", "react-native"],
    ttl_days=30  # Volatile — expires faster
)
```

---

## End-of-Run Recommendations

At the end of each run, all flagged findings are displayed as recommendations:

```
💡 Recommendations:

  [high] N+1 query issue in user dashboard endpoint
    → Implement batch loading with dataloader or similar pattern

  [medium] Missing type hints in api.py module
    → Add type annotations for better IDE support and maintainability

  [high] No rate limiting on public API endpoints
    → Add rate limiter middleware (e.g., express-rate-limit) to prevent abuse
```

These recommendations are:
1. Printed to stdout after the run summary
2. Available via `get_run_recommendations()` MCP tool
3. Stored in `config.research_cache_context.findings` for programmatic access

---

## Setup

### Prerequisites

- Python 3.11+
- Node.js 16+ (for the MCP server)

### Installation

The research cache is integrated into the orchestrator and requires no additional installation beyond the standard orchestrator setup:

```bash
# Install orchestrator (if not already done)
cd /path/to/orchestrator
pip install -e ".[dev]"

# Verify research cache is available
orchestrate --help | grep -i research
```

The research MCP server is auto-detected at:
```
../sdlc-mcp-servers/research-cache/dist/index.js
```

If the server binary is not found, the pipeline logs a warning and continues without cache functionality.

### First Run

On the first run, the cache directories are created automatically:

```bash
orchestrate "Add push notifications to mobile app"

# Creates:
# ~/.orchestrator/research/
#   ├── index.json
#   └── entries/
# .knowledge/research/
#   ├── index.json
#   └── entries/
```

---

## API Reference

### Environment Variables

The research MCP server reads these environment variables (set by the Python orchestrator):

| Variable | Purpose | Example |
|----------|---------|---------|
| `GLOBAL_RESEARCH_DIR` | Global cache directory | `~/.orchestrator/research` |
| `PROJECT_RESEARCH_DIR` | Project cache directory | `.knowledge/research` |
| `PROJECT_ROOT` | Project root path | `/home/user/my-project` |

### File Format

Cache entries are stored as JSON files:

**Index file** (`index.json`):
```json
{
  "entries": [
    {
      "entry_id": "a3f8e2c9d7b1...",
      "topic": "React Native performance patterns",
      "tags": ["react-native", "performance", "mobile"],
      "created_at": "2026-03-15T10:30:00Z",
      "ttl_days": 90
    }
  ]
}
```

**Entry file** (`entries/<hash>.json`):
```json
{
  "entry_id": "a3f8e2c9d7b1...",
  "topic": "React Native performance patterns",
  "content": "Key patterns for React Native performance optimization...",
  "tags": ["react-native", "performance", "mobile"],
  "tier": "global",
  "created_at": "2026-03-15T10:30:00Z",
  "ttl_days": 90,
  "source_phase": "architect",
  "run_id": "run-abc123",
  "usage_count": 5
}
```

---

## Troubleshooting

### Cache Not Working (Always Cache Miss)

1. **Check if MCP server is running:**
   ```bash
   # The server should be detected at start of run
   # Look for: "Research cache MCP server configured: research-cache"
   ```

2. **Verify cache directories exist:**
   ```bash
   ls ~/.orchestrator/research/index.json
   ls .knowledge/research/index.json
   ```

3. **Enable debug logging:**
   ```bash
   orchestrate --log-level debug "Your feature request"
   ```

### Stale Cache Entries

Cache entries automatically expire after `ttl_days`. If you want to force-refresh:

```bash
# Option 1: Delete project cache (keeps global cache)
rm -rf .knowledge/research/

# Option 2: Delete all cache (includes global)
rm -rf ~/.orchestrator/research/

# Option 3: Override TTL for a single run
orchestrate --config-override research_cache.volatile_ttl_days=0 "Your request"
```

### MCP Server Not Found

If the orchestrator logs: `"Research cache MCP server not found"`:

1. Verify the sibling MCP server path:
   ```bash
   ls ../sdlc-mcp-servers/research-cache/dist/index.js
   ```

2. Rebuild if needed:
   ```bash
   cd ../sdlc-mcp-servers/research-cache
   npm install
   npm run build
   ```

3. Specify the path explicitly:
   ```bash
   orchestrate \
     --config-override research_cache.server_path=/path/to/dist/index.js \
     "Your feature"
   ```

### Cache Corruption

If `index.json` becomes corrupted (invalid JSON):

1. Backup the corrupted file:
   ```bash
   cp .knowledge/research/index.json .knowledge/research/index.json.backup
   ```

2. Delete and let the system regenerate:
   ```bash
   rm .knowledge/research/index.json
   orchestrate "Your feature"
   ```

---

## Performance Characteristics

### Lookup Performance

| Cache Size | Avg Lookup Time | Notes |
|------------|-----------------|-------|
| 100 entries | <5ms | Linear scan with tag scoring |
| 500 entries | <10ms | Typical cache size |
| 1000+ entries | ~20ms | Consider evicting old entries |

### Storage

| Tier | Typical Size | Max Size |
|------|--------------|----------|
| Global (~.orchestrator/) | 50-200 MB | Unbounded (manual cleanup) |
| Project (.knowledge/research/) | 5-50 MB | ~500 entries × 100KB avg |

### Cost Savings

Typical scenario: "Add push notifications" research

| Without Cache | With Cache (Hit) |
|---------------|-----------------|
| 3 research agents × $0.15 = $0.45 | 1 cache lookup = $0.00 |
| ~90 seconds latency | ~1 second latency |

**Payback period**: Approximately 3 runs before cost savings break even.

---

## Agent Definitions

Agents are instructed to use the research cache via updated agent definition files:

### deep_researcher.md

```markdown
## Research Cache

Before researching any topic:
1. Call `lookup_research(query, tags)` to check the cache
2. If cache returns results (miss=false), use them directly
3. If cache miss, conduct fresh research
4. After fresh research, call `save_research()` to persist findings
5. When you discover actionable issues, call `flag_finding()` for end-of-run recommendations
```

### market_researcher.md, competitor_researcher.md, architect.md

Same instructions with tier guidance:
- Use `tier="global"` for stable knowledge (architecture, security patterns)
- Use `tier="project"` for volatile knowledge (market trends, competitor data)

---

## FAQ

**Q: Can I share cache between projects?**
> No, by design. Project-tier cache is isolated to each project's `.knowledge/research/` directory. Global-tier cache is shared but only contains stable cross-project knowledge (tech decisions, architecture patterns).

**Q: What happens if the MCP server crashes mid-run?**
> The pipeline logs a warning and continues normally without cache functionality. Agents will perform fresh research as if the cache were disabled.

**Q: How do I clear the cache?**
> - **Project cache only:** `rm -rf .knowledge/research/`
> - **Global cache only:** `rm -rf ~/.orchestrator/research/`
> - **Selective:** Delete specific entry files in `entries/` directories

**Q: Can I manually add entries to the cache?**
> Yes, place JSON files in `~/.orchestrator/research/entries/` or `.knowledge/research/entries/` (keyed by SHA-256 hash) and update the corresponding `index.json` file. But this is not recommended — use the MCP tool instead.

**Q: How are entries ranked in search results?**
> Sorted by: (1) Tag overlap count (descending), then (2) Created date (newest first). An entry with 2-tag overlap ranks before an entry with 1-tag overlap.

---

## See Also

- [Configuration Reference](../USAGE.md#11-configuration)
- [MCP Integration Guide](../CONTAINER_API.md)
- [Architecture Decisions](../adr/)
