# PRD: Knowledge Base MCP Server

**Status:** Ready for implementation
**Priority:** P0 — context window exhaustion blocks productive AI-assisted development across all projects
**Location:** `~/Projects/knowledge-base-mcp/` (standalone repo — not tied to any specific project)
**npm package:** `@anthropic-tools/knowledge-base-mcp` (future publish)

---

## Problem

Across 19 active projects there are **736 markdown files totaling ~10MB** of knowledge (PRDs, architecture docs, strategy docs, technical specs). When Claude Code or the orchestrator needs information from these docs, it reads full files into context — often 80–148KB each. This causes:

- **Context window exhaustion:** 2–3 large doc reads consume 30–50% of available context, leaving insufficient room for actual code reasoning and generation
- **Irrelevant noise:** A 4,757-line architecture doc is loaded when only a 20-line section about auth middleware is needed
- **Repeated reads:** The same docs are re-read across conversations because there's no persistent search layer
- **Cross-project blindness:** Knowledge in the Yoga project's security docs can't inform work in the orchestrator project without manually reading those files
- **Duplicate waste:** Multiple projects contain near-duplicate docs (e.g., two copies of security architecture, two copies of notification system docs) that both get loaded

### Measured Impact

| Project | Files | Size | Largest File |
|---------|-------|------|-------------|
| orchestrator-for-mobile-ui | 181 | 1.7 MB | — |
| Replaci | 139 | 1.3 MB | — |
| Orchestrator | 92 | 936 KB | — |
| orchestrator-new | 85 | 772 KB | — |
| Yoga | 84 | 3.6 MB | 148 KB (Teacher Marketplace) |
| CM360 | 44 | 632 KB | — |
| CoachingManagement | 31 | 724 KB | — |
| **Total** | **736** | **~10 MB** | — |

Reading even 5% of this corpus (500KB) in a single conversation occupies a significant share of the context window and degrades Claude's output quality.

---

## Goal

Deploy a local MCP server that indexes all project knowledge bases, chunks them by heading structure, builds vector embeddings, and exposes semantic search + targeted retrieval tools — so Claude fetches **only the 2–5 relevant paragraphs** instead of full files.

**Target:** Reduce per-query context consumption from **~100KB (full file read)** to **~2–5KB (relevant chunks only)** — a 95% reduction.

---

## Scope

### In Scope

- MCP server wrapping [`docs-mcp-server`](https://github.com/arabold/docs-mcp-server) (1,166 stars, TypeScript, actively maintained) as the indexing/search engine
- Multi-project source configuration (all 19 projects indexed as separate sources)
- Claude Code MCP integration (global user-level config so it's available in every project)
- Heading-based markdown chunking with overlap for context continuity
- Semantic vector search + keyword fallback (hybrid retrieval)
- Project-scoped search (search within one project or across all)
- Incremental re-indexing on file change (watch mode)
- CLAUDE.md instructions for each project teaching Claude to use search instead of full reads

### Out of Scope

- Custom embedding model training
- Cloud-hosted search (everything runs locally)
- Non-markdown file indexing (code files, images) — future phase
- Write-back tools (no doc editing via MCP)
- Integration with the orchestrator pipeline (separate from research-cache)

---

## Quick Start (Any User, Any Project)

```bash
# 1. Install
npm install -g knowledge-base-mcp

# 2. Auto-detect and index docs in your project
cd ~/my-project
knowledge-base-mcp init --scan .
# → Finds all .md files, creates sources.json, builds index

# 3. Add to Claude Code (one-time)
claude mcp add knowledge-base -- npx -y knowledge-base-mcp

# 4. Done — Claude now searches your docs instead of reading full files
```

For multiple projects:
```bash
knowledge-base-mcp init --scan ~/Projects/project-a --name project-a
knowledge-base-mcp init --scan ~/Projects/project-b --name project-b --append
# Both projects now searchable, scoped by name
```

---

## Architecture

### Standalone Design

This server is a **general-purpose tool** — it is not coupled to the SDLC orchestrator or any specific project. Any developer with markdown docs can use it.

```
~/Projects/knowledge-base-mcp/     ← standalone repo
├── package.json                   ← publishable to npm
├── src/
├── dist/
└── README.md                      ← setup for any user, any project

~/Projects/sdlc-mcp-servers/       ← orchestrator-specific MCP servers
├── research-cache/                ← coupled to orchestrator pipeline
├── cumulative-context-engine/     ← coupled to orchestrator pipeline
└── test-runner/                   ← coupled to orchestrator pipeline
```

The knowledge-base server is **complementary** to the orchestrator's research-cache but **independent** of it:
- **knowledge-base** → "What do my docs say about X?" (static knowledge, read path, any project)
- **research-cache** → "What did previous orchestrator runs discover about X?" (dynamic findings, orchestrator-specific)

### Distribution

| Method | Command | Audience |
|--------|---------|----------|
| **npx (zero-install)** | `npx knowledge-base-mcp` | Any developer, no clone needed |
| **Global install** | `npm i -g knowledge-base-mcp` | Frequent users |
| **From source** | `git clone → npm run build` | Contributors |

Any user on any machine can add it to their Claude Code config with a single line — no dependency on the orchestrator, sdlc-mcp-servers, or any other project.

### System Design

```
┌─────────────────────────────────────────────────────────┐
│                    Claude Code / Orchestrator            │
│                                                         │
│  Instead of: Read("Docs/Backend/00-NESTJS-DEEP-ARCH..") │
│  Now calls:  search_knowledge("NestJS auth middleware")  │
│              → returns 3 chunks, ~2KB total              │
└──────────────┬──────────────────────────────────────────┘
               │ stdio (MCP)
               ▼
┌──────────────────────────────────────────────────────────┐
│              knowledge-base MCP Server                    │
│                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │   Indexer    │  │  Vector Store │  │  Chunk Store   │  │
│  │ (md → chunks│→ │  (embeddings) │  │  (full text)   │  │
│  │  by heading) │  │  SQLite/FAISS │  │  SQLite        │  │
│  └─────────────┘  └──────────────┘  └────────────────┘  │
│                                                          │
│  Sources:                                                │
│   ~/Projects/Yoga/Docs/**/*.md          (source: yoga)   │
│   ~/Projects/Yoga/PRD/**/*.md           (source: yoga)   │
│   ~/Projects/orchestrator-for-mobile-ui/docs/**/*.md     │
│   ~/Projects/Replaci/docs/**/*.md                        │
│   ~/Projects/CM360/docs/**/*.md                          │
│   ... (all 19 projects)                                  │
└──────────────────────────────────────────────────────────┘
```

### Chunking Strategy

Markdown files are split by heading structure (H1/H2/H3), with each chunk containing:

| Field | Description |
|-------|-------------|
| `chunk_id` | SHA-256 of `source + filepath + heading_path` |
| `source` | Project name (e.g., `yoga`, `orchestrator`) |
| `filepath` | Relative path within project |
| `heading_path` | Breadcrumb path (e.g., `Architecture > Auth Middleware > JWT Flow`) |
| `content` | Chunk text (target: 500–2000 tokens) |
| `level` | Heading depth (1–6) |

Chunks that are too small (< 100 tokens) are merged with the next sibling. Chunks that are too large (> 2000 tokens) are split at paragraph boundaries with 50-token overlap.

---

## Functional Requirements

### FR-1: `search_knowledge`

**Signature:**
```typescript
search_knowledge(
  query: string,
  source?: string,      // filter to specific project (e.g., "yoga")
  max_results?: number  // default: 5, max: 20
): {
  results: ChunkResult[],
  total_matches: number
}
```

**`ChunkResult` schema:**
```typescript
{
  chunk_id: string,
  source: string,         // "yoga", "orchestrator", etc.
  filepath: string,       // "Docs/Backend/00-NESTJS-DEEP-ARCHITECTURE.md"
  heading_path: string,   // "Architecture > Auth Middleware > JWT Flow"
  content: string,        // the actual chunk text
  score: number,          // relevance score 0.0–1.0
  line_range: [number, number]  // start/end line in original file
}
```

**Behavior:**
1. Generate embedding for `query` using local model
2. Perform vector similarity search across all indexed chunks
3. If `source` is provided, filter to chunks from that project only
4. Apply keyword boost: if exact query terms appear in chunk, boost score by 0.2
5. Deduplicate: if multiple chunks from the same file score > 0.7, return only the highest-scoring one plus a `see_also` reference
6. Return top `max_results` chunks sorted by score descending

**Example usage by Claude:**
```
User: "How does auth work in the Yoga backend?"
Claude calls: search_knowledge("authentication NestJS auth middleware JWT", source="yoga")
→ Returns 3 chunks (~2KB) instead of reading the full 140KB architecture doc
```

### FR-2: `list_sources`

**Signature:**
```typescript
list_sources(): {
  sources: SourceInfo[]
}
```

**`SourceInfo` schema:**
```typescript
{
  name: string,          // "yoga"
  path: string,          // "/Users/amangupta/Projects/Yoga/Docs"
  file_count: number,    // 84
  chunk_count: number,   // ~420
  last_indexed: string,  // ISO 8601
  total_size_kb: number  // 3600
}
```

**Behavior:**
1. Return metadata for all configured sources
2. Include indexing freshness so Claude knows if data might be stale

### FR-3: `get_document_section`

**Signature:**
```typescript
get_document_section(
  filepath: string,       // full or relative path
  heading?: string,       // optional heading to extract
  source?: string         // project name for disambiguation
): {
  content: string,
  heading_path: string,
  line_range: [number, number]
}
```

**Behavior:**
1. If `heading` is provided, return only that section (and its subsections)
2. If no `heading`, return the document's table of contents (all headings with levels)
3. This is the "targeted read" — Claude can drill into a specific section after `search_knowledge` points it there

**Example workflow:**
```
1. search_knowledge("JWT refresh token flow") → chunk from "00-NESTJS-DEEP-ARCHITECTURE.md#Auth Middleware"
2. get_document_section("Docs/Backend/00-NESTJS-DEEP-ARCHITECTURE.md", heading="Auth Middleware")
   → returns the full Auth Middleware section (~3KB) instead of the entire 140KB file
```

### FR-4: `reindex_source`

**Signature:**
```typescript
reindex_source(
  source?: string  // specific project name, or omit for all
): {
  reindexed: string[],
  chunks_added: number,
  chunks_removed: number,
  duration_ms: number
}
```

**Behavior:**
1. Re-scan the source directory for new/changed/deleted markdown files
2. Only re-index files whose `mtime` has changed since last index (incremental)
3. Update vector store and chunk store
4. Return summary of changes

### FR-5: CLI Commands (`bin/cli.ts`)

The package exposes a `knowledge-base-mcp` CLI for setup and management:

```bash
# Auto-scan a directory for .md files and add as a source
knowledge-base-mcp init --scan <path> [--name <source-name>] [--append]

# Manually trigger a full or incremental re-index
knowledge-base-mcp index [--source <name>] [--full]

# List all configured sources with stats
knowledge-base-mcp sources

# Search from terminal (debugging / quick lookup)
knowledge-base-mcp search "auth middleware" [--source yoga] [--limit 5]

# Start the MCP server (normally called by Claude Code, not manually)
knowledge-base-mcp serve
```

`init --scan` walks the directory tree, finds all `.md` files (excluding `node_modules`, `.git`, `dist`), and writes/appends to `sources.json`. If `--name` is omitted, it uses the directory basename.

---

## Configuration

### Source Registry

File: `~/.config/knowledge-base-mcp/sources.json`

```json
{
  "sources": [
    {
      "name": "yoga",
      "paths": [
        "/Users/amangupta/Projects/Yoga/Docs/**/*.md",
        "/Users/amangupta/Projects/Yoga/PRD/**/*.md",
        "/Users/amangupta/Projects/Yoga/*.md"
      ],
      "exclude": ["**/node_modules/**"]
    },
    {
      "name": "orchestrator",
      "paths": [
        "/Users/amangupta/Projects/orchestrator-for-mobile-ui/docs/**/*.md"
      ]
    },
    {
      "name": "replaci",
      "paths": [
        "/Users/amangupta/Projects/Replaci/**/*.md"
      ],
      "exclude": ["**/node_modules/**", "**/dist/**"]
    },
    {
      "name": "cm360",
      "paths": [
        "/Users/amangupta/Projects/CM360/**/*.md"
      ]
    },
    {
      "name": "coaching",
      "paths": [
        "/Users/amangupta/Projects/CoachingManagement/**/*.md"
      ]
    }
  ],
  "embedding_model": "all-MiniLM-L6-v2",
  "chunk_max_tokens": 2000,
  "chunk_overlap_tokens": 50,
  "auto_reindex_on_startup": true
}
```

### Claude Code MCP Registration

Added to `~/.claude/settings.json` (global, available in all projects):

```json
{
  "mcpServers": {
    "knowledge-base": {
      "command": "npx",
      "args": ["-y", "knowledge-base-mcp"],
      "env": {
        "CONFIG_PATH": "~/.config/knowledge-base-mcp/sources.json",
        "INDEX_DIR": "~/.config/knowledge-base-mcp/index"
      }
    }
  }
}
```

Or, if running from source:

```json
{
  "mcpServers": {
    "knowledge-base": {
      "command": "node",
      "args": ["/Users/amangupta/Projects/knowledge-base-mcp/dist/index.js"],
      "env": {
        "CONFIG_PATH": "~/.config/knowledge-base-mcp/sources.json",
        "INDEX_DIR": "~/.config/knowledge-base-mcp/index"
      }
    }
  }
}
```

### CLAUDE.md Integration

Each project's CLAUDE.md gets this directive:

```markdown
## Knowledge Base Search

This project's docs are indexed by the `knowledge-base` MCP server.

**Rule:** Before reading any full .md doc file, ALWAYS call `search_knowledge` first
to find the relevant section. Only use `get_document_section` to read the specific
section you need. Never read a full doc file unless explicitly asked.

Available tools:
- `search_knowledge(query, source="<project-name>")` — semantic search
- `get_document_section(filepath, heading)` — read a specific section
- `list_sources()` — see all indexed projects
```

---

## Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| Initial full index (736 files, 10MB) | < 60 seconds |
| Incremental re-index (10 changed files) | < 5 seconds |
| `search_knowledge` latency | < 200ms |
| `get_document_section` latency | < 50ms |
| Index storage size | < 100MB (embeddings + chunks) |
| Memory usage at runtime | < 512MB |
| Embedding model size | < 100MB (local, no API calls) |
| Startup time | < 3 seconds |
| Supported platforms | macOS (primary), Linux |

---

## File Structure

```
~/Projects/knowledge-base-mcp/       ← standalone repo
├── package.json                     ← name: "knowledge-base-mcp", publishable to npm
├── tsconfig.json
├── bin/
│   └── cli.ts                       ← CLI entry: `knowledge-base-mcp init`, `knowledge-base-mcp index`
├── src/
│   ├── index.ts                     ← MCP server entry point (stdio transport)
│   ├── indexer.ts                   ← markdown chunking + embedding pipeline
│   ├── search.ts                    ← vector similarity search + keyword boost
│   ├── chunker.ts                   ← heading-based markdown splitter
│   ├── store.ts                     ← SQLite storage for chunks + vectors
│   └── config.ts                    ← source registry loader
├── dist/
│   └── index.js                     ← compiled output (gitignored)
├── test/
│   ├── chunker.test.ts              ← markdown splitting tests
│   ├── search.test.ts               ← retrieval quality tests
│   └── integration.test.ts          ← full index + search cycle
├── LICENSE                          ← MIT
└── README.md                        ← setup guide for any user
```

---

## Embedding Strategy

### Build vs. External API

| Option | Latency | Cost | Privacy | Offline |
|--------|---------|------|---------|---------|
| Local model (`all-MiniLM-L6-v2` via `onnxruntime-node`) | ~5ms/chunk | $0 | Full | Yes |
| OpenAI `text-embedding-3-small` | ~50ms/chunk | $0.02/1M tokens | Docs sent to OpenAI | No |
| Anthropic (no embedding API) | N/A | N/A | N/A | N/A |

**Decision: Local model.** All docs stay on-machine. No API costs. Works offline. The 80MB model download is a one-time cost.

### Why `all-MiniLM-L6-v2`

- 384-dimension embeddings (compact, fast similarity search)
- 22M parameters (runs on CPU in ~5ms per chunk)
- Trained on 1B+ sentence pairs — strong on technical docs
- Available via `onnxruntime-node` (no Python dependency)
- Used by docs-mcp-server, mcp-local-rag, and other proven MCP servers

---

## Implementation Plan

### Phase 1: Core Server (MVP) — 1 session

1. Scaffold TypeScript project in `~/Projects/knowledge-base-mcp/`
2. Implement heading-based markdown chunker
3. Implement SQLite chunk + vector store
4. Wire up `all-MiniLM-L6-v2` via `onnxruntime-node`
5. Implement `search_knowledge` and `list_sources` MCP tools
6. Index 1 project (Yoga) as proof-of-concept
7. Register in Claude Code settings

### Phase 2: Full Multi-Project + Targeted Retrieval

1. Add source registry config (`sources.json`)
2. Index all 19 projects
3. Implement `get_document_section` (heading-level extraction)
4. Implement `reindex_source` (incremental, mtime-based)
5. Add CLAUDE.md directives to top 5 projects

### Phase 3: Watch Mode + Quality Tuning

1. File watcher for auto-reindex on save (via `fs.watch` or `chokidar`)
2. Retrieval quality tuning: adjust chunk sizes, overlap, keyword boost weights
3. Add deduplication detection for near-identical docs across projects
4. Usage analytics: which queries return poor results → tune index

---

## Acceptance Criteria

| ID | Criterion | Verified By |
|----|-----------|-------------|
| AC-001 | `npm run build` succeeds with zero TypeScript errors | CI |
| AC-002 | Full index of Yoga project (84 files, 3.6MB) completes in < 30s | manual timing |
| AC-003 | `search_knowledge("NestJS auth middleware", source="yoga")` returns relevant chunks from `00-NESTJS-DEEP-ARCHITECTURE.md` | manual test |
| AC-004 | Returned chunks are < 2KB each, not full files | unit test |
| AC-005 | `source` filter correctly scopes results to a single project | unit test |
| AC-006 | `list_sources` returns all configured sources with accurate file counts | unit test |
| AC-007 | `get_document_section` returns only the requested heading section | unit test |
| AC-008 | Incremental reindex only processes files with changed `mtime` | unit test |
| AC-009 | Server starts in < 3s and responds to MCP tool calls | integration test |
| AC-010 | No network calls during indexing or search (fully local) | network audit |
| AC-011 | Claude Code in Yoga project can call `search_knowledge` and get results | end-to-end test |
| AC-012 | Keyword-exact matches rank above vaguely-similar semantic matches | retrieval test |

---

## Dependencies

```json
{
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.0.0",
    "better-sqlite3": "^11.0.0",
    "onnxruntime-node": "^1.18.0"
  },
  "devDependencies": {
    "typescript": "^5.0.0",
    "@types/node": "^20.0.0",
    "@types/better-sqlite3": "^7.0.0",
    "vitest": "^2.0.0"
  }
}
```

**Embedding model:** `all-MiniLM-L6-v2` ONNX (~80MB, downloaded on first run to `~/.config/knowledge-base-mcp/models/`)

---

## Context Budget Analysis

### Before (current state)

| Action | Context consumed |
|--------|-----------------|
| Read `00-NESTJS-DEEP-ARCHITECTURE.md` | ~140KB (~35K tokens) |
| Read `SECURITY-PRIVACY-ARCHITECTURE.md` | ~108KB (~27K tokens) |
| Read `00-TEACHER-MARKETPLACE-PLATFORM.md` | ~148KB (~37K tokens) |
| **Total for 3 file reads** | **~396KB (~99K tokens)** |

With a 200K token context window, reading 3 docs consumes ~50% of context before any code work begins.

### After (with knowledge-base MCP)

| Action | Context consumed |
|--------|-----------------|
| `search_knowledge("NestJS auth middleware")` → 3 chunks | ~2KB (~500 tokens) |
| `search_knowledge("security session tokens")` → 3 chunks | ~2KB (~500 tokens) |
| `get_document_section(file, "Teacher Onboarding")` → 1 section | ~3KB (~750 tokens) |
| **Total for equivalent information** | **~7KB (~1,750 tokens)** |

**Result: 98% reduction in context consumed for the same information retrieval.**

---

## Relationship to Other Tools

### Standalone Usage (any developer)

The knowledge-base MCP server works on its own. A developer with 50 markdown docs in one project can:
1. `npx knowledge-base-mcp init` → generates `sources.json` by scanning the current directory
2. Add MCP config to Claude Code settings
3. Claude now searches docs instead of reading full files

No orchestrator, no other MCP servers, no special setup needed.

### Within the Orchestrator Ecosystem

When used alongside the SDLC orchestrator's MCP servers, it fills the "static knowledge" gap:

| Server | Repo | Purpose | Data Flow |
|--------|------|---------|-----------|
| **knowledge-base** (this PRD) | `~/Projects/knowledge-base-mcp/` (standalone) | Search existing project docs | Read-only. Indexes `.md` files on disk. |
| **research-cache** | `~/Projects/sdlc-mcp-servers/research-cache/` | Cache orchestrator run findings | Read + Write. Pipeline-specific. |
| **cumulative-context-engine** | `~/Projects/sdlc-mcp-servers/cumulative-context-engine/` | Accumulate context across phases | Write. Pipeline-specific. |
| **test-runner** | `~/Projects/sdlc-mcp-servers/test-runner/` | Execute and report test results | Execute. Pipeline-specific. |

Together they form the **knowledge stack:**
1. **knowledge-base** provides background project knowledge (static docs) — **works independently**
2. **research-cache** provides learned knowledge from past runs (dynamic findings) — orchestrator-coupled
3. **cumulative-context-engine** provides rolling context within a run — orchestrator-coupled
4. **test-runner** provides runtime verification — orchestrator-coupled

---

## Testing

```bash
cd ~/Projects/knowledge-base-mcp

# Build
npm install
npm run build

# Quick start: auto-detect docs in a project
knowledge-base-mcp init --scan ~/Projects/Yoga
# → creates ~/.config/knowledge-base-mcp/sources.json with Yoga docs

# Run unit tests
npm test

# Manual: index and start server
CONFIG_PATH=~/.config/knowledge-base-mcp/sources.json \
INDEX_DIR=~/.config/knowledge-base-mcp/index \
node dist/index.js

# Manual: verify search via MCP Inspector
npx @modelcontextprotocol/inspector node dist/index.js

# Zero-install test (after npm publish)
npx knowledge-base-mcp --scan ~/Projects/Yoga

# End-to-end: open Claude Code in Yoga project, call:
#   search_knowledge("Flutter offline sync strategy", source="yoga")
# Expect: 3–5 chunks from Docs/Mobile/00-OFFLINE-FIRST-STRATEGY.md
```

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Embedding model too large for fast startup | Slow first launch | Download model on `npm run setup`, not at runtime. Cache in `~/.config/`. |
| Chunk boundaries cut mid-paragraph | Poor retrieval quality | Overlap strategy (50 tokens) + paragraph-aware splitting |
| `onnxruntime-node` platform issues on macOS ARM | Server won't start | Fall back to TF.js backend; test on M-series Mac during dev |
| Stale index after doc edits | Claude gets outdated info | Auto-reindex on startup + `reindex_source` manual trigger |
| Too many results across 19 projects | Noisy cross-project results | Default to current project scope via `source` parameter; `CLAUDE.md` instructs project-scoped queries |
