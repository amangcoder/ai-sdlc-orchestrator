## MANDATORY: Use MCP Knowledge Tools for Codebase Exploration

**DO NOT use Glob, Grep, or Bash (cat/grep/sed/find/ls) for codebase exploration.**
All MCP tools are prefixed with `mcp__ai-code-knowledge__`. Use them for ALL file discovery, symbol lookup, and code understanding tasks.

| Instead of...                        | Use this MCP tool                                              |
|--------------------------------------|----------------------------------------------------------------|
| `Glob {'pattern': '**/*.py'}`        | `mcp__ai-code-knowledge__get_directory_tree`                   |
| `Grep {'pattern': 'class Foo'}`      | `mcp__ai-code-knowledge__find_symbol` with `name: "Foo"`       |
| `Grep {'pattern': 'some_function'}`  | `mcp__ai-code-knowledge__semantic_search` with `scope: "symbols"` |
| `Bash {'command': 'cat -n file.py'}` | `mcp__ai-code-knowledge__get_implementation_context`           |
| `Read` to understand a file          | `mcp__ai-code-knowledge__get_file_summary`                     |
| Multiple Glob/Grep to explore        | `mcp__ai-code-knowledge__get_project_overview` (call FIRST)    |

Only fall back to Read/Edit/Write for **actually modifying files** or reading artifact JSON. Never use Glob, Grep, or Bash for exploration.

---

### Composite tools (prefer these — fewer calls, richer context)
1. `mcp__ai-code-knowledge__get_project_overview` — file tree, tech stack, modules, entry points. **Call this FIRST on any project.**
2. `mcp__ai-code-knowledge__get_module_context` — everything about a module: summaries, symbols, dependencies, patterns
3. `mcp__ai-code-knowledge__get_implementation_context` — rich context for a single file: symbols, imports, related files. **Call before modifying a file.**
4. `mcp__ai-code-knowledge__get_batch_summaries` — summaries for up to 20 files in one call

### Targeted query tools (use when you know exactly what you need)
5. `mcp__ai-code-knowledge__find_symbol` — locate functions, classes, interfaces by name. Params: `name` (required), `type` (optional), `module` (optional)
6. `mcp__ai-code-knowledge__get_file_summary` — AI-generated summary of a single file
7. `mcp__ai-code-knowledge__get_dependencies` — module dependency graph
8. `mcp__ai-code-knowledge__find_callers` — trace who calls a symbol (impact analysis). Params: `symbol` (required), `maxDepth` (optional), `direction` (optional)
9. `mcp__ai-code-knowledge__search_architecture` — search architecture documentation
10. `mcp__ai-code-knowledge__health_check` — verify knowledge base status

### Directory & pattern tools
11. `mcp__ai-code-knowledge__get_directory_tree` — file/folder structure as a tree listing. Params: `path` (relative, optional), `depth` (1–5, default 3)
12. `mcp__ai-code-knowledge__get_code_patterns` — recurring code patterns: component, CSS, data, routing, testing. Param: `pattern_type` (optional filter)
13. `mcp__ai-code-knowledge__find_template_file` — finds most similar existing files to use as templates for consistency. Param: `description`

### Pipeline artifact tools
14. `mcp__ai-code-knowledge__get_artifact_schema` — expected JSON schema for pipeline artifact types (prd, architecture, tasks, etc.). Param: `artifact_type`
15. `mcp__ai-code-knowledge__get_artifact_store_path` — filesystem path convention where an artifact should be written. Param: `artifact_type`
16. `mcp__ai-code-knowledge__validate_artifact_draft` — pre-validates artifact JSON against expected schema before submission. Params: `artifact_type`, `json_content`
17. `mcp__ai-code-knowledge__get_cumulative_context` — digest of all artifact types produced by prior phases. Param: `phase` — valid values: `competitor_research`, `user_psychology_research`, `ux_specification`, `prd`, `architecture`, `engineering_plan`, `task_breakdown`, `implementation`. Use `implementation` for any engineering/implementation role (backend_engineer, frontend_engineer, automation_engineer, etc.).

### Search & discovery tools
18. `mcp__ai-code-knowledge__semantic_search` — hybrid BM25 + vector search with Reciprocal Rank Fusion. Params: `query`, `scope` (files|symbols|features|all), `topK` (max 50)
19. `mcp__ai-code-knowledge__explore_graph` — BFS traversal of the knowledge graph from a start node. Params: `start` (required), `edgeTypes` (optional: contains|calls|imports|depends_on|implements|similar_to), `maxDepth` (optional, max 5), `direction` (optional: outgoing|incoming|both)
20. `mcp__ai-code-knowledge__get_feature_context` — find semantic feature clusters relevant to a query. Params: `query`, `topK`

### Data tools
21. `mcp__ai-code-knowledge__get_static_data_schema` — structure of static data files: keys, exports, relationships (no params)
