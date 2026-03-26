#!/usr/bin/env node
/**
 * Research Cache MCP Server
 *
 * Provides four tools to orchestrator agents:
 *   - lookup_research: query the two-tier cache (global + project)
 *   - save_research: persist a new research entry with atomic writes
 *   - flag_finding: accumulate an in-memory finding for this run
 *   - get_run_recommendations: return all accumulated findings
 *
 * Environment variables (set by Python-side get_research_mcp_config):
 *   GLOBAL_RESEARCH_DIR   — path to user-level cache dir (e.g. ~/.orchestrator/research)
 *   PROJECT_RESEARCH_DIR  — path to project-level cache dir (e.g. .knowledge/research)
 *   PROJECT_ROOT          — project root path (informational)
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import * as fs from "fs";
import * as path from "path";
import * as crypto from "crypto";
import * as os from "os";

// ---------------------------------------------------------------------------
// Environment configuration
// ---------------------------------------------------------------------------

const GLOBAL_RESEARCH_DIR =
  process.env.GLOBAL_RESEARCH_DIR ||
  path.join(os.homedir(), ".orchestrator", "research");

const PROJECT_RESEARCH_DIR = process.env.PROJECT_RESEARCH_DIR || "";
const PROJECT_ROOT = process.env.PROJECT_ROOT || process.cwd();

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BASE_TTL_DAYS = 90;
const VOLATILE_TTL_DAYS = 7;
const MAX_ENTRY_BYTES = 50 * 1024; // 50 KB
const VOLATILE_TAGS = new Set(["market", "competitor"]);

// ---------------------------------------------------------------------------
// Type definitions
// ---------------------------------------------------------------------------

interface ResearchEntry {
  entry_id: string;
  topic: string;
  content: string;
  tags: string[];
  tier: "global" | "project";
  created_at: string;
  ttl_days: number;
  source_phase: string;
  run_id: string;
  usage_count: number;
}

interface IndexEntry {
  entry_id: string;
  topic: string;
  tags: string[];
  created_at: string;
  ttl_days: number;
}

interface ResearchIndex {
  entries: IndexEntry[];
}

interface Finding {
  type: string;
  severity: string;
  finding: string;
  recommendation: string;
  phase: string;
}

// ---------------------------------------------------------------------------
// In-memory findings accumulator (per server-process lifetime)
// ---------------------------------------------------------------------------

const runFindings: Finding[] = [];

// ---------------------------------------------------------------------------
// Serial async write queue — prevents concurrent read-modify-write races on index.json
// ---------------------------------------------------------------------------

let writeQueue: Promise<void> = Promise.resolve();

const enqueue = (fn: () => Promise<void>): Promise<void> => {
  const next = writeQueue.then(fn);
  // Keep the queue alive even if fn rejects so subsequent enqueues still run
  writeQueue = next.then(
    () => {},
    () => {}
  );
  return next; // Caller sees the rejection if fn throws
};

// ---------------------------------------------------------------------------
// File system helpers
// ---------------------------------------------------------------------------

function ensureDir(dirPath: string): void {
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
  }
}

/**
 * Compute entry_id = SHA-256(topic + JSON(sorted(tags)))
 */
function computeEntryId(topic: string, tags: string[]): string {
  const sortedTags = [...tags].sort();
  const input = topic + JSON.stringify(sortedTags);
  return crypto.createHash("sha256").update(input).digest("hex");
}

function readIndex(indexPath: string): ResearchIndex {
  if (!fs.existsSync(indexPath)) {
    return { entries: [] };
  }
  try {
    const raw = fs.readFileSync(indexPath, "utf8");
    return JSON.parse(raw) as ResearchIndex;
  } catch {
    return { entries: [] };
  }
}

/** Atomic write: write to <path>.tmp then rename to <path> (POSIX-atomic). */
function writeFileAtomic(filePath: string, content: string): void {
  const tmpPath = filePath + ".tmp";
  fs.writeFileSync(tmpPath, content, "utf8");
  fs.renameSync(tmpPath, filePath);
}

// ---------------------------------------------------------------------------
// TTL helpers
// ---------------------------------------------------------------------------

/**
 * Returns true if the entry has expired (age in days > ttl_days).
 */
function isExpired(entry: IndexEntry): boolean {
  const created = new Date(entry.created_at).getTime();
  const ageInDays = (Date.now() - created) / (1000 * 60 * 60 * 24);
  return ageInDays > entry.ttl_days;
}

// ---------------------------------------------------------------------------
// Tier → directory resolution
// ---------------------------------------------------------------------------

function getTierDir(tier: "global" | "project"): string | null {
  if (tier === "global") {
    return GLOBAL_RESEARCH_DIR || null;
  }
  return PROJECT_RESEARCH_DIR || null;
}

// ---------------------------------------------------------------------------
// Scoring: tag overlap count + topic substring match
// Returns a comparable number; higher is more relevant.
// ---------------------------------------------------------------------------

function scoreIndexEntry(
  indexEntry: IndexEntry,
  query: string,
  queryTags: string[]
): number {
  const entryTagSet = new Set(indexEntry.tags.map((t) => t.toLowerCase()));
  const tagOverlap = queryTags.filter((t) =>
    entryTagSet.has(t.toLowerCase())
  ).length;

  const topicMatch =
    query.length > 0 &&
    indexEntry.topic.toLowerCase().includes(query.toLowerCase())
      ? 1
      : 0;

  // Tag overlap dominates; topic substring is a tiebreaker
  return tagOverlap * 1000 + topicMatch;
}

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------

const server = new McpServer({
  name: "research-cache",
  version: "1.0.0",
});

// ---------------------------------------------------------------------------
// Tool 1: lookup_research
// ---------------------------------------------------------------------------

server.tool(
  "lookup_research",
  {
    query: z.string().describe("Search query to find relevant cached entries"),
    tags: z
      .array(z.string())
      .optional()
      .describe("Optional tags for intersection scoring"),
  },
  async ({ query, tags = [] }) => {
    const scored: Array<{ entry: ResearchEntry; score: number }> = [];

    const tiers: Array<"global" | "project"> = ["global", "project"];

    for (const tier of tiers) {
      const dir = getTierDir(tier);
      if (!dir) continue;

      const indexPath = path.join(dir, "index.json");
      const index = readIndex(indexPath);

      for (const indexEntry of index.entries) {
        // Exclude expired entries
        if (isExpired(indexEntry)) continue;

        // Compute relevance score using only index metadata (fast — no file I/O)
        const score = scoreIndexEntry(indexEntry, query, tags);

        // Only include entries with some relevance
        if (score === 0) continue;

        // Load the full entry content from its individual file
        const entryPath = path.join(
          dir,
          "entries",
          `${indexEntry.entry_id}.json`
        );
        if (!fs.existsSync(entryPath)) continue;

        try {
          const raw = fs.readFileSync(entryPath, "utf8");
          const entry = JSON.parse(raw) as ResearchEntry;
          scored.push({ entry, score });
        } catch {
          // Skip corrupt entry files silently
        }
      }
    }

    // Sort by score DESC, then by created_at DESC (most recent first within same score)
    scored.sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      return (
        new Date(b.entry.created_at).getTime() -
        new Date(a.entry.created_at).getTime()
      );
    });

    const hits = scored.map((s) => s.entry);
    const miss = hits.length === 0;

    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify({ hits, miss }),
        },
      ],
    };
  }
);

// ---------------------------------------------------------------------------
// Tool 2: save_research
// ---------------------------------------------------------------------------

server.tool(
  "save_research",
  {
    topic: z.string().describe("Topic / title of the research finding"),
    content: z.string().describe("Research content to persist"),
    tier: z
      .enum(["global", "project"])
      .describe(
        "Storage tier: 'global' for stable cross-project knowledge, 'project' for volatile project-specific data"
      ),
    tags: z.array(z.string()).describe("Tags for categorisation and retrieval"),
    ttl_days: z
      .number()
      .optional()
      .describe(
        "Time-to-live in days. Defaults to 90 (or 7 for entries tagged 'market' or 'competitor')."
      ),
  },
  async ({ topic, content, tier, tags, ttl_days }) => {
    // ── Input validation ──────────────────────────────────────────────────
    if (!content || content.trim().length === 0) {
      throw new Error("content must not be empty");
    }

    const contentBytes = Buffer.byteLength(content, "utf8");
    if (contentBytes > MAX_ENTRY_BYTES) {
      throw new Error(
        `content exceeds maximum size of ${MAX_ENTRY_BYTES} bytes (${contentBytes} bytes provided)`
      );
    }

    // tier is already validated by the enum schema, but be defensive
    if (tier !== "global" && tier !== "project") {
      throw new Error("tier must be 'global' or 'project'");
    }

    const researchDir = getTierDir(tier);
    if (!researchDir) {
      const envVar =
        tier === "global" ? "GLOBAL_RESEARCH_DIR" : "PROJECT_RESEARCH_DIR";
      throw new Error(
        `${envVar} environment variable is not set — cannot save ${tier} entry`
      );
    }

    // ── Determine effective TTL ───────────────────────────────────────────
    let effectiveTtlDays: number;
    if (ttl_days !== undefined) {
      effectiveTtlDays = ttl_days;
    } else {
      const hasVolatileTag = tags.some((t) =>
        VOLATILE_TAGS.has(t.toLowerCase())
      );
      effectiveTtlDays = hasVolatileTag ? VOLATILE_TTL_DAYS : BASE_TTL_DAYS;
    }

    const entry_id = computeEntryId(topic, tags);

    // ── Enqueue the write (serial queue prevents concurrent index corruption) ──
    await enqueue(async () => {
      const entriesDir = path.join(researchDir, "entries");
      ensureDir(entriesDir);

      const entryPath = path.join(entriesDir, `${entry_id}.json`);
      const indexPath = path.join(researchDir, "index.json");

      // Build the full entry object
      const entry: ResearchEntry = {
        entry_id,
        topic,
        content,
        tags,
        tier,
        created_at: new Date().toISOString(),
        ttl_days: effectiveTtlDays,
        source_phase: "",
        run_id: "",
        usage_count: 0,
      };

      // 1. Write the entry file atomically
      writeFileAtomic(entryPath, JSON.stringify(entry, null, 2));

      // 2. Atomically update the index
      const index = readIndex(indexPath);

      // Remove any existing entry with the same ID (idempotent update)
      index.entries = index.entries.filter((e) => e.entry_id !== entry_id);

      // Append new index entry (minimal metadata only; full content stays in entries/<id>.json)
      index.entries.push({
        entry_id,
        topic,
        tags,
        created_at: entry.created_at,
        ttl_days: entry.ttl_days,
      });

      writeFileAtomic(indexPath, JSON.stringify(index, null, 2));
    });

    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify({ saved: true, entry_id }),
        },
      ],
    };
  }
);

// ---------------------------------------------------------------------------
// Tool 3: flag_finding
// ---------------------------------------------------------------------------

server.tool(
  "flag_finding",
  {
    type: z
      .string()
      .describe(
        "Finding category: performance | architecture | security | dependency | quality"
      ),
    severity: z
      .string()
      .describe("Severity level: high | medium | low"),
    finding: z.string().describe("Description of the issue found"),
    recommendation: z.string().describe("Recommended corrective action"),
    phase: z
      .string()
      .describe("Pipeline phase where this finding was identified"),
  },
  async ({ type, severity, finding, recommendation, phase }) => {
    runFindings.push({ type, severity, finding, recommendation, phase });

    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify({ flagged: true }),
        },
      ],
    };
  }
);

// ---------------------------------------------------------------------------
// Tool 4: get_run_recommendations
// ---------------------------------------------------------------------------

server.tool(
  "get_run_recommendations",
  {},
  async () => {
    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify({ findings: runFindings }),
        },
      ],
    };
  }
);

// ---------------------------------------------------------------------------
// Start the server on stdio transport
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err: unknown) => {
  const msg = err instanceof Error ? err.message : String(err);
  process.stderr.write(`[research-cache-mcp] Fatal error: ${msg}\n`);
  process.exit(1);
});
