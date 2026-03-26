# Research Cache API Reference

**For:** Developers using or extending the research cache feature
**Last Updated:** 2026-03-26

---

## Table of Contents

1. [Configuration](#configuration)
2. [Python API](#python-api)
3. [MCP Server Tools](#mcp-server-tools)
4. [Agent Integration](#agent-integration)
5. [Data Models](#data-models)
6. [Examples](#examples)

---

## Configuration

### ResearchCacheConfig

Location: `src/orchestrator/models.py`

**Purpose:** Configures research cache behavior at startup

**Fields:**

```python
class ResearchCacheConfig(BaseModel):
    enabled: bool = True
    """Enable/disable the research cache feature"""

    global_dir: str = "~/.orchestrator/research"
    """Global cache directory (expanded from ~)"""

    local_dir: str = ".knowledge/research"
    """Project-local cache directory"""

    server_path: str = ""
    """Path to MCP server binary (auto-detected if empty)"""

    base_ttl_days: int = 90
    """Default TTL for stable knowledge (architecture, security)"""

    volatile_ttl_days: int = 7
    """Default TTL for volatile knowledge (market, competitor data)"""

    max_entries: int = 500
    """Maximum entries per tier (global, project) before eviction"""

    max_inject_bytes: int = 2048
    """Maximum bytes of cache data injected into agent prompts"""

    inject_into_phases: list[str] = ["pm", "architect", "principal_engineer"]
    """Phases where research cache is injected into prompts"""

    auto_extract: bool = True
    """Auto-extract research findings from phase artifacts"""

    cleanup_mcp_config: bool = True
    """Remove research-cache entry from .mcp.json on shutdown"""
```

**Usage in config/default.yaml:**

```yaml
research_cache:
  enabled: true
  global_dir: ~/.orchestrator/research
  local_dir: .knowledge/research
  server_path: ""
  base_ttl_days: 90
  volatile_ttl_days: 7
  max_entries: 500
  max_inject_bytes: 2048
  inject_into_phases:
    - pm
    - architect
    - principal_engineer
  auto_extract: true
  cleanup_mcp_config: true
```

**Override at runtime:**

```bash
orchestrate \
  --config-override "research_cache.enabled=true" \
  --config-override "research_cache.global_dir=/custom/path" \
  "your feature"
```

---

## Python API

### Module: `orchestrator.research_cache`

#### MCP Configuration Functions

##### `get_research_mcp_config(server_path: str, project_root: Path) -> dict[str, Any] | None`

**Purpose:** Discover and return MCP server configuration

**Parameters:**
- `server_path` (str): Explicit path to MCP server binary, or empty string for auto-detection
- `project_root` (Path): Project root directory for environment setup

**Returns:**
- `dict` with MCP server config if found: `{mcpServers: {'research-cache': {command, args, env}}}`
- `None` if server binary not found

**Example:**
```python
from pathlib import Path
from orchestrator.research_cache import get_research_mcp_config

config = get_research_mcp_config("", Path.cwd())
if config:
    print(f"MCP server found: {config}")
else:
    print("MCP server not found — research cache unavailable")
```

**Behavior:**
- Auto-detects server at: `../sdlc-mcp-servers/research-cache/dist/index.js`
- Returns None if server binary missing (graceful degradation)
- Sets environment: `GLOBAL_RESEARCH_DIR`, `PROJECT_RESEARCH_DIR`, `PROJECT_ROOT`

---

##### `ensure_research_mcp_config(server_path: str, target_project: Path) -> bool`

**Purpose:** Persist research-cache entry to `.mcp.json` file

**Parameters:**
- `server_path` (str): Path to MCP server binary
- `target_project` (Path): Project directory where `.mcp.json` is located

**Returns:**
- `True` if entry written successfully
- `False` if failed

**Example:**
```python
from pathlib import Path
from orchestrator.research_cache import ensure_research_mcp_config

success = ensure_research_mcp_config("", Path.cwd())
if success:
    print("Research cache registered in .mcp.json")
```

**Note:** Currently not called during engine initialization (see [Review Issue #3](RESEARCH_CACHE_REVIEW_FINDINGS.md#warning-ensure_research_mcp_config-never-called))

---

##### `cleanup_research_mcp_config(target_project: Path) -> None`

**Purpose:** Remove research-cache entry from `.mcp.json` on shutdown

**Parameters:**
- `target_project` (Path): Project directory containing `.mcp.json`

**Example:**
```python
from pathlib import Path
from orchestrator.research_cache import cleanup_research_mcp_config

cleanup_research_mcp_config(Path.cwd())
print("Research cache entry removed from .mcp.json")
```

**Behavior:**
- Removes only `'research-cache'` key, preserves other MCP servers
- No-op if `.mcp.json` doesn't exist
- Called during engine shutdown when `cleanup_mcp_config=true`

---

#### Cache Operations

##### `load_cache(global_dir: Path, local_dir: Path) -> ResearchCache`

**Purpose:** Load cache from both tiers (global and project-local)

**Parameters:**
- `global_dir` (Path): Global cache directory (`~/.orchestrator/research/`)
- `local_dir` (Path): Project-local cache directory (`.knowledge/research/`)

**Returns:**
- `ResearchCache` with `global_entries` and `local_entries` lists

**Example:**
```python
from pathlib import Path
from orchestrator.research_cache import load_cache

cache = load_cache(
    Path.home() / ".orchestrator" / "research",
    Path.cwd() / ".knowledge" / "research"
)
print(f"Loaded {len(cache.global_entries)} global entries")
print(f"Loaded {len(cache.local_entries)} project entries")
```

**Behavior:**
- Returns empty lists if directories don't exist (graceful)
- Parses `index.json` from each tier
- Does NOT validate TTL (that's done in `lookup()`)

---

##### `lookup(cache: ResearchCache, query: str, tags: list[str]) -> list[ResearchEntry]`

**Purpose:** Search cache for matching entries

**Parameters:**
- `cache` (ResearchCache): Cache from `load_cache()`
- `query` (str): Search query (substring match on topic)
- `tags` (list[str]): Tags to match (tag intersection)

**Returns:**
- List of `ResearchEntry` objects, sorted by:
  1. Tag overlap count (descending)
  2. Created date (most recent first)

**Example:**
```python
from orchestrator.research_cache import load_cache, lookup

cache = load_cache(...)
results = lookup(cache, "React Native", ["react-native", "performance"])

if results:
    print(f"Cache hit! Found {len(results)} entries")
    for entry in results:
        print(f"  - {entry.topic} (tags: {entry.tags})")
else:
    print("Cache miss — performing fresh research")
```

**Behavior:**
- Filters out expired entries (where `age_days > ttl_days`)
- Matches tags using intersection (all query tags must be in entry)
- Does substring match on topic (case-sensitive)
- Returns empty list if no matches

---

##### `save_entry(cache: ResearchCache, entry: ResearchEntry) -> None`

**Purpose:** Write a research entry to cache (both memory and disk)

**Parameters:**
- `cache` (ResearchCache): Cache instance to update
- `entry` (ResearchEntry): Entry to save

**Raises:**
- `ValueError` if entry validation fails
- `IOError` if disk write fails

**Example:**
```python
from datetime import datetime
from orchestrator.research_cache import save_entry
from orchestrator.models import ResearchEntry, ResearchCache

cache = load_cache(...)

entry = ResearchEntry(
    topic="React Native Performance Optimization",
    content="Based on benchmark testing, use FlatList for >1000 items...",
    tags=["react-native", "performance", "mobile"],
    tier="project",  # Project-specific findings
    created_at=datetime.now().isoformat(),
    ttl_days=7,  # Volatile data expires after 7 days
    source_phase="performance_engineer",
    run_id="run-abc123"
)

save_entry(cache, entry)
print(f"Saved: {entry.topic}")
```

**Behavior:**
- Appends to `cache.global_entries` or `cache.local_entries` (based on tier)
- Writes entry JSON to disk: `entries/<sha256_hash>.json`
- Updates `index.json` via atomic write (write-to-temp-then-rename)
- Evicts oldest entries if tier exceeds `max_entries`
- Uses fcntl advisory lock for concurrent access (Unix only)

---

##### `extract_research_from_artifact(artifact_path: Path, phase: str, run_id: str) -> list[ResearchEntry]`

**Purpose:** Auto-extract research findings from completed phase artifacts

**Parameters:**
- `artifact_path` (Path): Path to artifact JSON (e.g., `architecture.json`)
- `phase` (str): Phase name (used to determine artifact type)
- `run_id` (str): Run ID for auditability

**Returns:**
- List of `ResearchEntry` objects extracted from artifact
- Empty list if artifact doesn't exist or phase not recognized

**Example:**
```python
from orchestrator.research_cache import extract_research_from_artifact

artifact_path = Path("workspace/architect/architecture.json")
entries = extract_research_from_artifact(artifact_path, "architect", "run-123")

for entry in entries:
    print(f"Extracted: {entry.topic} (tier={entry.tier})")
    # tier='global' for stable knowledge (architecture, security)
    # tier='project' for volatile knowledge (market, competitor)
```

**Supported Artifacts:**

| Artifact | Phase | Extracted From | Tier |
|----------|-------|----------------|------|
| architecture.json | architect | `tech_decisions[]` | global |
| engineering_plan.json | principal_engineer | library selections, patterns | global |
| market_research.json | market_researcher | `trends[]`, `recommendations[]` | project |
| competitor_research.json | competitor_researcher | `competitors[]`, `feature_matrix` | project |
| threat_model.json | security_engineer | `recommendations[]` | global |
| benchmark_report.json | caching_engineer | `bottlenecks[]`, `recommendations[]` | project |

**Behavior:**
- Returns empty list if artifact file doesn't exist
- Returns empty list if phase is not recognized
- Tags extracted entries automatically from content
- Source phase and run_id recorded for auditability

---

#### Findings & Recommendations

##### `flag_finding(findings: list[dict], finding: Finding) -> None`

**Purpose:** Accumulate an actionable finding for end-of-run recommendations

**Parameters:**
- `findings` (list[dict]): Findings list to append to
- `finding` (Finding): Finding to flag

**Example:**
```python
from orchestrator.models import Finding
from orchestrator.research_cache import flag_finding

findings = []

finding = Finding(
    type="performance",
    severity="high",
    finding="Database queries in ProductList unoptimized — N+1 queries detected",
    recommendation="Add query result caching and use JOIN instead of loop queries",
    phase="architect"
)

flag_finding(findings, finding)
print(f"Flagged: {finding.finding}")
```

**Behavior:**
- Appends `finding.model_dump()` to findings list
- No disk I/O (in-memory only)
- Findings printed at end of run by engine.py

---

##### `format_recommendations(findings: list[dict]) -> str`

**Purpose:** Format flagged findings as human-readable recommendations

**Parameters:**
- `findings` (list[dict]): List of finding dicts (from `flag_finding()`)

**Returns:**
- Formatted string suitable for stdout

**Example:**
```python
from orchestrator.research_cache import format_recommendations

formatted = format_recommendations(findings)
print(formatted)

# Output:
# ✨ Recommendations:
#
# [HIGH] Database queries unoptimized — N+1 queries detected
#   → Add query result caching and use JOIN instead of loop queries
#
# [MEDIUM] Consider migrating from Redux to Context API
#   → Reduces bundle size by ~40KB and simplifies prop drilling
```

**Behavior:**
- Adds emoji and "Recommendations:" header
- Groups findings by severity (HIGH, MEDIUM, LOW)
- Each finding shows: `[SEVERITY] finding_text → recommendation`

---

### Module: `orchestrator.models`

#### ResearchCache

**Purpose:** In-memory representation of cache state

```python
class ResearchCache(BaseModel):
    global_entries: list[ResearchEntry] = []
    """Entries from global tier (~/.orchestrator/research/)"""

    local_entries: list[ResearchEntry] = []
    """Entries from project tier (.knowledge/research/)"""
```

---

#### ResearchEntry

**Purpose:** Individual cache entry

```python
class ResearchEntry(BaseModel):
    topic: str
    """Topic/query this entry addresses"""

    content: str
    """Full research content"""

    tags: list[str]
    """Tags for filtering/ranking (e.g., ['react-native', 'performance'])"""

    tier: Literal["global", "project"]
    """'global' (cross-project) or 'project' (this project only)"""

    created_at: str
    """ISO datetime when entry was created"""

    ttl_days: int
    """Time-to-live in days. Entry expires after this many days"""

    source_phase: str
    """Phase that created this entry (e.g., 'architect')"""

    run_id: str
    """Run ID when entry was created (for auditability)"""

    usage_count: int = 0
    """Times this entry was returned in lookup results"""
```

---

#### Finding

**Purpose:** Actionable issue to surface at end-of-run

```python
class Finding(BaseModel):
    type: Literal["performance", "architecture", "security", "dependency", "quality"]
    """Category of finding"""

    severity: Literal["high", "medium", "low"]
    """Severity level"""

    finding: str
    """Description of the finding"""

    recommendation: str
    """Recommended action"""

    phase: str
    """Phase that flagged this finding"""
```

---

#### ResearchCacheContext

**Purpose:** Runtime state of research cache during a run

```python
class ResearchCacheContext(BaseModel):
    cache_loaded: bool
    """True if cache was successfully loaded from disk"""

    mcp_configured: bool
    """True if MCP server is running and ready"""

    mcp_server_config: dict | None
    """MCP server config dict (from get_research_mcp_config)"""

    global_entry_count: int
    """Current number of entries in global tier"""

    local_entry_count: int
    """Current number of entries in project tier"""

    findings: list[dict]
    """Flagged findings for end-of-run recommendations"""
```

---

## MCP Server Tools

### Endpoint: `research-cache` (Node.js stdio server)

Location: `../sdlc-mcp-servers/research-cache/`

#### Tool: `lookup_research`

**Purpose:** Query cache for matching research entries

**Signature:**
```
lookup_research(query: string, tags?: string[]) → {
  hits: ResearchEntry[],
  miss: boolean
}
```

**Parameters:**
- `query` (string): Search query (substring on topic)
- `tags` (string[], optional): Tags to match (intersection)

**Response:**
- `hits` (array): Matching entries sorted by relevance
- `miss` (boolean): `true` if no entries found, `false` if hit

**Example:**

Agent calls:
```
lookup_research("React Native performance optimization", ["react-native", "performance"])

Response:
{
  "hits": [
    {
      "topic": "React Native Performance — FlatList Best Practices",
      "tags": ["react-native", "performance", "mobile"],
      "content": "For lists with >1000 items, use FlatList with...",
      "created_at": "2026-03-20T10:30:00Z",
      "ttl_days": 7
    }
  ],
  "miss": false
}
```

---

#### Tool: `save_research`

**Purpose:** Persist new research findings to cache

**Signature:**
```
save_research(
  topic: string,
  content: string,
  tier: 'global'|'project',
  tags: string[],
  ttl_days?: number
) → {
  saved: boolean,
  entry_id: string
}
```

**Parameters:**
- `topic` (string): Research topic
- `content` (string): Full research findings (max 100KB)
- `tier` (string): `'global'` for stable knowledge, `'project'` for volatile
- `tags` (string[]): Tags for later retrieval
- `ttl_days` (number, optional): Days until expiry (defaults to base_ttl_days=90 or volatile_ttl_days=7 for market/competitor tags)

**Response:**
- `saved` (boolean): `true` if successfully saved
- `entry_id` (string): SHA-256 hash of entry (for debugging)

**Example:**

Agent calls:
```
save_research(
  "React Native Performance — FlatList Best Practices",
  "Based on benchmarks with 5000+ items...FlatList provides O(1) rendering...",
  "project",
  ["react-native", "performance", "mobile"],
  7  // Volatile data, expires in 7 days
)

Response:
{
  "saved": true,
  "entry_id": "a3b2c1d0e9f8g7h6i5j4k3l2m1n0"
}
```

---

#### Tool: `flag_finding`

**Purpose:** Flag an actionable issue for end-of-run recommendations

**Signature:**
```
flag_finding(
  type: string,
  severity: string,
  finding: string,
  recommendation: string,
  phase: string
) → {
  flagged: boolean
}
```

**Parameters:**
- `type` (string): One of: `performance`, `architecture`, `security`, `dependency`, `quality`
- `severity` (string): One of: `high`, `medium`, `low`
- `finding` (string): Description of the issue
- `recommendation` (string): Suggested action
- `phase` (string): Phase that discovered this

**Response:**
- `flagged` (boolean): `true` if successfully accumulated

**Example:**

Agent calls:
```
flag_finding(
  "performance",
  "high",
  "Database N+1 queries in product list endpoint",
  "Batch queries using JOIN or add caching layer",
  "architect"
)

Response:
{
  "flagged": true
}
```

---

#### Tool: `get_run_recommendations`

**Purpose:** Retrieve all findings flagged in current run

**Signature:**
```
get_run_recommendations() → {
  findings: Finding[]
}
```

**Response:**
- `findings` (array): All flagged findings

**Example:**

Agent calls:
```
get_run_recommendations()

Response:
{
  "findings": [
    {
      "type": "performance",
      "severity": "high",
      "finding": "Database N+1 queries",
      "recommendation": "Use JOIN or add caching",
      "phase": "architect"
    },
    {
      "type": "architecture",
      "severity": "medium",
      "finding": "Redux boilerplate excessive",
      "recommendation": "Consider Context API",
      "phase": "architect"
    }
  ]
}
```

---

## Agent Integration

### How Agents Use Research Cache

#### 1. Check Cache Before Research

```python
# In agent definition or code:
result = lookup_research("React Native performance", ["react-native"])

if not result["miss"]:
    # Cache hit — use results directly
    print(f"Found {len(result['hits'])} cached findings")
    use_cached_results(result["hits"])
else:
    # Cache miss — perform fresh research
    print("Cache miss — researching...")
    findings = perform_fresh_research()
    # Save for next time
    save_research(
        "React Native performance",
        findings["content"],
        "project",  # Project-specific
        ["react-native", "performance"]
    )
```

#### 2. Save Fresh Research

```python
# After completing research on a cache miss:
save_research(
    topic="Advanced TypeScript Patterns",
    content=detailed_research_findings,
    tier="global",  # Reusable across projects
    tags=["typescript", "architecture", "patterns"],
    ttl_days=180  # Stable knowledge, long TTL
)
```

#### 3. Flag Actionable Issues

```python
# When discovering issues during analysis:
flag_finding(
    type="security",
    severity="high",
    finding="JWT tokens not rotating — potential token compromise",
    recommendation="Implement token rotation with 1-hour expiry",
    phase="security_engineer"
)
```

#### 4. Use Cached Context

```python
# In prompt injection (phases.py):
# <research-cache-data>
# ALWAYS call lookup_research before researching any topic.
# Use cached results directly on a hit (miss: false).
# Call save_research after conducting fresh research on a cache miss.
# Call flag_finding for actionable issues.
# </research-cache-data>
```

---

## Examples

### Example 1: Enable Research Cache in Config

```yaml
# config/default.yaml
research_cache:
  enabled: true
  global_dir: ~/.orchestrator/research
  local_dir: .knowledge/research
  server_path: ""  # Auto-detect
  base_ttl_days: 90
  volatile_ttl_days: 7
  max_entries: 500
  inject_into_phases:
    - pm
    - architect
    - principal_engineer
```

### Example 2: Manual Cache Lookup in Python

```python
from pathlib import Path
from orchestrator.research_cache import load_cache, lookup

# Load cache
cache = load_cache(
    Path.home() / ".orchestrator" / "research",
    Path.cwd() / ".knowledge" / "research"
)

# Search for cached React Native research
results = lookup(cache, "React Native", ["react-native"])

if results:
    print(f"✓ Cache hit: {len(results)} entries")
    for entry in results:
        print(f"  - {entry.topic}")
        print(f"    Tags: {', '.join(entry.tags)}")
else:
    print("✗ Cache miss: Research not cached")
```

### Example 3: Auto-Extract from Artifacts

```python
from pathlib import Path
from orchestrator.research_cache import extract_research_from_artifact, load_cache, save_entry

# After architect phase completes
artifact = Path("workspace/architect/architecture.json")
entries = extract_research_from_artifact(artifact, "architect", "run-abc123")

# Save extracted entries
cache = load_cache(
    Path.home() / ".orchestrator" / "research",
    Path.cwd() / ".knowledge" / "research"
)

for entry in entries:
    save_entry(cache, entry)
    print(f"Saved: {entry.topic}")
```

### Example 4: Flag Findings for End-of-Run

```python
from orchestrator.models import Finding
from orchestrator.research_cache import flag_finding, format_recommendations

findings = []

# Flag a performance issue
flag_finding(
    findings,
    Finding(
        type="performance",
        severity="high",
        finding="Database N+1 queries in ProductList component",
        recommendation="Implement query batching and result caching",
        phase="architect"
    )
)

# Print formatted recommendations
print(format_recommendations(findings))
```

---

## Error Handling

### Common Exceptions

```python
from orchestrator.research_cache import lookup, save_entry
import json

# TTL expiry (automatic in lookup)
results = lookup(cache, "query", [])
# Expired entries automatically filtered out
# No exception raised

# Invalid entry data
try:
    save_entry(cache, invalid_entry)
except ValueError as e:
    print(f"Validation error: {e}")

# Disk I/O errors
try:
    save_entry(cache, entry)
except IOError as e:
    print(f"Disk write failed: {e}")

# MCP server unavailable
mcp_config = get_research_mcp_config("", Path.cwd())
if mcp_config is None:
    print("MCP server not available — cache disabled")
```

---

## See Also

- [Implementation Guide](RESEARCH_CACHE_IMPLEMENTATION.md) — How to implement new features
- [Maintenance Guide](RESEARCH_CACHE_MAINTENANCE.md) — Troubleshooting and operations
- [Review Findings](RESEARCH_CACHE_REVIEW_FINDINGS.md) — Known issues and workarounds
- [Feature Documentation](features/research-cache-mcp-server.md) — User guide
