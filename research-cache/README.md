# research-cache MCP Server

A Node.js MCP (Model Context Protocol) server that provides a two-tier persistent research cache for the AI SDLC Orchestrator. Agents use it to avoid redundant research across runs and to accumulate actionable findings.

## Setup

```bash
cd /path/to/sdlc-mcp-servers/research-cache
npm install
npm run build
# Produces dist/index.js
```

## Running

The server is started automatically by the Python orchestrator via `get_research_mcp_config()`.
To run manually for testing:

```bash
GLOBAL_RESEARCH_DIR=~/.orchestrator/research \
PROJECT_RESEARCH_DIR=.knowledge/research \
PROJECT_ROOT=$(pwd) \
node dist/index.js
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GLOBAL_RESEARCH_DIR` | No | `~/.orchestrator/research` | Path to the user-level (global) cache directory |
| `PROJECT_RESEARCH_DIR` | No | *(empty)* | Path to the project-level cache directory. Required for `save_research(tier="project")` |
| `PROJECT_ROOT` | No | `process.cwd()` | Project root path (informational) |

## Tools

### `lookup_research`

Look up cached research entries matching a query and/or tags. Returns a sorted list of hits.

**Parameters:**
- `query` *(string, required)* — Natural-language search query (substring matched against entry topics)
- `tags` *(string[], optional)* — Tags to score by intersection with cached entry tags

**Returns:** `{ hits: ResearchEntry[], miss: boolean }`
- `hits` — Matching entries sorted by tag-overlap count (DESC), then `created_at` (DESC)
- `miss` — `true` when no relevant non-expired entries were found

**Example:**
```json
{
  "query": "React Native performance",
  "tags": ["react-native", "performance", "mobile"]
}
```

---

### `save_research`

Persist a research finding to the cache. Writes are atomic (write-to-temp-then-rename) and serialised via an async Promise queue to prevent concurrent index corruption.

**Parameters:**
- `topic` *(string, required)* — Short descriptive title
- `content` *(string, required)* — Full research content (max 50 KB)
- `tier` *(`"global" | "project"`, required)* — Storage tier
  - `"global"` → `GLOBAL_RESEARCH_DIR/entries/<id>.json` (stable, cross-project knowledge)
  - `"project"` → `PROJECT_RESEARCH_DIR/entries/<id>.json` (volatile, project-specific data)
- `tags` *(string[], required)* — Tags for future retrieval
- `ttl_days` *(number, optional)* — Cache TTL in days. Defaults to **7** for entries tagged `market` or `competitor`, **90** otherwise.

**Returns:** `{ saved: boolean, entry_id: string }`

The `entry_id` is the SHA-256 hash of `topic + JSON(sorted(tags))`.

**Example:**
```json
{
  "topic": "React Native performance optimisation patterns",
  "content": "Key findings: use Hermes engine, enable Fabric renderer...",
  "tier": "global",
  "tags": ["react-native", "performance", "hermes"],
  "ttl_days": 90
}
```

---

### `flag_finding`

Accumulate an actionable finding for the current run. Findings are held in memory for the server's process lifetime and returned by `get_run_recommendations`.

**Parameters:**
- `type` *(string, required)* — Category: `performance | architecture | security | dependency | quality`
- `severity` *(string, required)* — Level: `high | medium | low`
- `finding` *(string, required)* — Description of the issue
- `recommendation` *(string, required)* — Recommended action
- `phase` *(string, required)* — Pipeline phase where the issue was found

**Returns:** `{ flagged: boolean }`

**Example:**
```json
{
  "type": "performance",
  "severity": "high",
  "finding": "No memoisation found in the list renderer component",
  "recommendation": "Wrap FlatList renderItem with React.memo and useCallback",
  "phase": "architect"
}
```

---

### `get_run_recommendations`

Return all findings accumulated since the server started.

**Parameters:** *(none)*

**Returns:** `{ findings: Finding[] }`

**Example response:**
```json
{
  "findings": [
    {
      "type": "performance",
      "severity": "high",
      "finding": "No memoisation in list renderer",
      "recommendation": "Wrap with React.memo and useCallback",
      "phase": "architect"
    }
  ]
}
```

---

## Storage Layout

```
GLOBAL_RESEARCH_DIR/
  index.json              ← index of all global entries (topic, tags, created_at, ttl_days)
  entries/
    <sha256-hash>.json    ← full entry including content

PROJECT_RESEARCH_DIR/
  index.json              ← index of all project entries
  entries/
    <sha256-hash>.json    ← full entry
```

The `index.json` schema:
```json
{
  "entries": [
    {
      "entry_id": "abc123...",
      "topic": "React Native performance",
      "tags": ["react-native", "performance"],
      "created_at": "2026-01-15T10:30:00.000Z",
      "ttl_days": 90
    }
  ]
}
```

## Concurrency Safety

- **Serial write queue**: all `save_research` calls are serialised via a `Promise` chain (`writeQueue = writeQueue.then(fn)`) so index reads and writes are never interleaved.
- **Atomic writes**: every file write uses write-to-temp-then-rename (`fs.renameSync`) which is atomic on POSIX systems.

## Entry Naming

Entry filenames are the SHA-256 hash of `topic + JSON(sorted(tags))`:
```
entries/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855.json
```

This makes save operations idempotent: saving the same topic+tags combination overwrites the previous entry.
