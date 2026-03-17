## MCP Knowledge Tools — USE THESE FIRST

When MCP knowledge tools are available, you MUST use them instead of Bash/Glob/Grep for codebase exploration.
Start with `get_project_overview()` for a full project map, then drill down:

### Composite tools (prefer these — fewer calls, richer context)
1. `get_project_overview` — file tree, tech stack, modules, entry points. **Call this FIRST on any project.**
2. `get_module_context` — everything about a module: summaries, symbols, dependencies, patterns
3. `get_implementation_context` — rich context for a single file: symbols, imports, related files. **Call before modifying a file.**
4. `get_batch_summaries` — summaries for up to 20 files in one call

### Targeted query tools (use when you know exactly what you need)
5. `find_symbol` — locate functions, classes, interfaces by name
6. `get_file_summary` — AI-generated summary of a single file
7. `get_dependencies` — module dependency graph
8. `find_callers` — trace who calls a symbol (impact analysis)
9. `search_architecture` — search architecture documentation
10. `health_check` — verify knowledge base status

### Directory & pattern tools
11. `get_directory_tree` — file/folder structure as a tree listing. Params: `path` (relative, optional), `depth` (1–5, default 3)
12. `get_code_patterns` — recurring code patterns: component, CSS, data, routing, testing. Param: `pattern_type` (optional filter)
13. `find_template_file` — finds most similar existing files to use as templates for consistency. Param: `description`

### Pipeline artifact tools
14. `get_artifact_schema` — expected JSON schema for pipeline artifact types (prd, architecture, tasks, etc.). Param: `artifact_type`
15. `get_artifact_store_path` — filesystem path convention where an artifact should be written. Param: `artifact_type`
16. `validate_artifact_draft` — pre-validates artifact JSON against expected schema before submission. Params: `artifact_type`, `json_content`
17. `get_cumulative_context` — digest of all artifact types produced by prior phases. Param: `phase`

### Data tools
18. `get_static_data_schema` — structure of static data files: keys, exports, relationships (no params)

Only fall back to Read/Grep/Glob if MCP tools are unavailable or return no results.
Do NOT use Bash find/ls, Agent Explore, or broad Glob scanning when MCP tools are available.
