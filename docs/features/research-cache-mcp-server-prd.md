# PRD: Research Cache MCP Server (Node.js)

**Status:** Ready for implementation
**Priority:** P0 — blocks the entire research cache feature
**Location:** `../sdlc-mcp-servers/research-cache/`

---

## Problem

The Python orchestrator and agent prompts reference four MCP tools (`lookup_research`, `save_research`, `flag_finding`, `get_run_recommendations`) but the Node.js server that exposes them does not exist. As a result:

- Agents cannot reuse cached research — they re-run expensive research agents every run ($0.10–$0.50 each, 30–60s each)
- Acceptance criteria AC-009 (concurrent write safety) and AC-015 (lookup ranking) are unverifiable
- The feature is dead at runtime even though the Python integration code is complete

---

## Goal

Build the Node.js MCP server at `../sdlc-mcp-servers/research-cache/` that exposes the four tools to agents via stdio transport, compiles to `dist/index.js`, and is auto-detected by the Python orchestrator at startup.

---

## Scope

### In Scope

- TypeScript MCP server implementing all four tools
- Two-tier JSON file storage (global `~/.orchestrator/research/` + project `.knowledge/research/`)
- TTL-based entry expiry on lookup
- Tag-overlap ranking in `lookup_research`
- Serial async write queue + atomic rename for concurrent write safety
- SHA-256 entry hashing for deterministic filenames
- LRU eviction when `max_entries` is reached
- Auto-volatile TTL for entries tagged `"market"` or `"competitor"`
- `package.json`, `tsconfig.json`, build script (`npm run build`)
- README with setup and manual testing instructions

### Out of Scope

- Vector similarity search
- Cache statistics dashboard
- Per-project cache quotas
- Any changes to the Python orchestrator (already wired up)

---

## Functional Requirements

### FR-1: `lookup_research`

**Signature:**
```typescript
lookup_research(query: string, tags?: string[]): {
  hits: ResearchEntry[],
  miss: boolean
}
```

**Behavior:**
1. Load both `globalDir/index.json` and `projectDir/index.json`
2. Filter out expired entries: `ageInDays > ttl_days`
3. Score remaining entries: count of tag intersections between entry tags and query tags
4. Also include entries where `topic` is a substring match of `query` (score = 0 if no tag overlap but substring match)
5. Sort by `(tag_overlap_count DESC, created_at DESC)`
6. Return `{ hits: matchingEntries, miss: hits.length === 0 }`
7. Increment `usage_count` on each returned entry and persist the update

**Expiry check:**
```typescript
const ageInDays = (Date.now() - new Date(entry.created_at).getTime()) / 86_400_000;
return ageInDays <= entry.ttl_days;
```

---

### FR-2: `save_research`

**Signature:**
```typescript
save_research(
  topic: string,
  content: string,
  tier: "global" | "project",
  tags: string[],
  ttl_days?: number
): { saved: boolean, entry_id: string }
```

**Behavior:**
1. Compute `entry_id` = SHA-256(`topic` + `JSON.stringify(tags.sort())`)
2. Determine `ttl_days`:
   - If provided, use it
   - If any tag matches `"market"` or `"competitor"`, use `volatile_ttl_days` (default: 7)
   - Otherwise use `base_ttl_days` (default: 90)
3. Enforce max content size: reject if `content.length > 51_200` bytes (50 KB)
4. Determine storage directory based on `tier` (`globalDir` or `projectDir`)
5. Enqueue write via serial write queue (see FR-5)
6. Write `entries/<entry_id>.json` atomically
7. Update `index.json` atomically (read → upsert → write-to-temp → rename)
8. Evict oldest entry if total exceeds `max_entries` (default: 500)
9. Return `{ saved: true, entry_id }`

**Entry file schema:**
```json
{
  "entry_id": "<sha256>",
  "topic": "...",
  "content": "...",
  "tags": [...],
  "tier": "global" | "project",
  "created_at": "<ISO 8601>",
  "ttl_days": 90,
  "source_phase": "...",
  "run_id": "...",
  "usage_count": 0
}
```

---

### FR-3: `flag_finding`

**Signature:**
```typescript
flag_finding(
  type: "performance" | "architecture" | "security" | "dependency" | "quality",
  severity: "high" | "medium" | "low",
  finding: string,
  recommendation: string,
  phase: string
): { flagged: boolean }
```

**Behavior:**
1. Append finding to in-memory `findings[]` array (per server process lifetime)
2. Return `{ flagged: true }`

---

### FR-4: `get_run_recommendations`

**Signature:**
```typescript
get_run_recommendations(): { findings: Finding[] }
```

**Behavior:**
1. Return the in-memory `findings[]` accumulated by `flag_finding` this process session
2. Returns `{ findings: [] }` if none flagged

---

### FR-5: Concurrent Write Safety

All writes to `index.json` and `entries/` must go through a **serial async Promise queue**:

```typescript
let writeQueue: Promise<void> = Promise.resolve();

function enqueueWrite(fn: () => Promise<void>): Promise<void> {
  writeQueue = writeQueue.then(fn).catch(() => {});
  return writeQueue;
}
```

All writes must be **atomic** (write-to-temp-then-rename):

```typescript
import { writeFileSync, renameSync } from "fs";

function atomicWrite(targetPath: string, data: unknown): void {
  const tmp = targetPath + ".tmp";
  writeFileSync(tmp, JSON.stringify(data, null, 2), "utf8");
  renameSync(tmp, targetPath); // POSIX atomic
}
```

---

### FR-6: Environment Variables

The server reads these from environment (set by the Python orchestrator):

| Variable | Purpose | Default |
|----------|---------|---------|
| `GLOBAL_RESEARCH_DIR` | Global cache directory | `~/.orchestrator/research` |
| `PROJECT_RESEARCH_DIR` | Project cache directory | `.knowledge/research` |
| `PROJECT_ROOT` | Project root path | `process.cwd()` |

On startup, create missing directories with `mkdirSync({ recursive: true })`.

---

### FR-7: Transport

Use `StdioServerTransport` from `@modelcontextprotocol/sdk/server/stdio.js`. The server is launched by the orchestrator as a child process over stdio — no HTTP, no ports.

---

## Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| `lookup_research` latency (500 entries) | < 10ms |
| `save_research` latency | < 50ms |
| Concurrent `save_research` (2+ simultaneous) | No index.json corruption |
| Compile target | Node.js 18+, ES2022 |
| Dependencies | `@modelcontextprotocol/sdk` only; no external runtime deps |

---

## File Structure

```
../sdlc-mcp-servers/research-cache/
├── package.json
├── tsconfig.json
├── src/
│   └── index.ts          ← server implementation
├── dist/
│   └── index.js          ← compiled output (gitignored)
└── README.md
```

---

## Acceptance Criteria

| ID | Criterion | Verified By |
|----|-----------|-------------|
| AC-009 | Two concurrent `save_research` calls do not corrupt `index.json` | `npm test` concurrent write test |
| AC-015 | `lookup_research` results sorted by tag overlap count descending | `npm test` sort test |
| AC-T1 | `npm run build` succeeds with zero TypeScript errors | CI |
| AC-T2 | `lookup_research` returns `miss: true` for unknown topic | unit test |
| AC-T3 | `lookup_research` filters out entries where `ageInDays > ttl_days` | unit test |
| AC-T4 | `save_research` with `"market"` tag uses `volatile_ttl_days` | unit test |
| AC-T5 | `save_research` rejects `content` > 50 KB | unit test |
| AC-T6 | Entry filename is deterministic SHA-256 of `topic + sorted(tags)` | unit test |
| AC-T7 | `get_run_recommendations` returns findings from this session | unit test |
| AC-T8 | `dist/index.js` is detected and configured by Python orchestrator | integration test |

---

## Implementation Notes

### index.json Schema

```json
{
  "entries": [
    {
      "entry_id": "<sha256>",
      "topic": "React Native performance patterns",
      "tags": ["react-native", "performance"],
      "created_at": "2026-03-25T10:30:00Z",
      "ttl_days": 90
    }
  ]
}
```

The index file stores metadata only (no content). Full entry content lives in `entries/<id>.json`.

### LRU Eviction

When `entries.length >= max_entries` after a write, remove the entry with the oldest `created_at` timestamp from both the index and the entry file.

### Volatile TTL Auto-Select

```typescript
const isVolatile = tags.some(t => t === "market" || t === "competitor");
const resolvedTtl = ttl_days ?? (isVolatile ? VOLATILE_TTL_DAYS : BASE_TTL_DAYS);
```

### SHA-256 Hashing

```typescript
import { createHash } from "crypto";

const entryId = createHash("sha256")
  .update(topic + JSON.stringify(tags.sort()))
  .digest("hex");
```

---

## Dependencies

```json
{
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.0.0"
  },
  "devDependencies": {
    "typescript": "^5.0.0",
    "@types/node": "^20.0.0"
  }
}
```

---

## Testing

```bash
cd ../sdlc-mcp-servers/research-cache

# Build
npm install
npm run build

# Verify startup (should wait for MCP messages, not exit)
GLOBAL_RESEARCH_DIR=~/.orchestrator/research \
PROJECT_RESEARCH_DIR=.knowledge/research \
PROJECT_ROOT=$(pwd) \
node dist/index.js

# Run tests
npm test

# End-to-end: verify Python orchestrator detects the server
cd /path/to/orchestrator
orchestrate --dry-run "Add push notifications"
# Expect: "Research cache MCP server configured: research-cache"
```

---

## Related Issues (from QA report)

The following issues in `workspace/artifacts/qa_report.json` are unblocked once this server is built:

| Issue | Description |
|-------|-------------|
| Critical | `../sdlc-mcp-servers/research-cache/` does not exist |
| Minor | AC-009 (concurrent write safety) unverifiable without server |
| Minor | AC-015 (lookup ranking) unverifiable without server |

Agent definition updates (major issues in the QA report) are a **separate task** — see [agent-cache-instructions PRD](#) — but depend on this server existing first.
