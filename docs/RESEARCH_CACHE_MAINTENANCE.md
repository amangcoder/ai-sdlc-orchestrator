# Research Cache Maintenance Guide

**For:** Developers maintaining or extending the research cache feature
**Status:** Active feature with known issues (see [Review Findings](RESEARCH_CACHE_REVIEW_FINDINGS.md))

---

## Quick Reference

| Component | Location | Language | Status |
|-----------|----------|----------|--------|
| Pydantic Models | `src/orchestrator/models.py` | Python | ✅ Functional |
| Python Integration | `src/orchestrator/research_cache.py` | Python | ⚠️ Review issues |
| Engine Integration | `src/orchestrator/engine.py` | Python | ⚠️ Review issues |
| Workflow Integration | `src/orchestrator/workflow_engine.py` | Python | ⚠️ Review issues |
| Phase Injection | `src/orchestrator/phases.py` | Python | ⚠️ Review issues |
| MCP Server | `../sdlc-mcp-servers/research-cache/` | Node.js | ✅ Functional |
| Unit Tests | `tests/test_research_cache.py` | Python | ✅ Comprehensive |
| Integration Tests | `tests/integration/test_research_cache_integration.py` | Python | ✅ Comprehensive |

---

## Daily Operations

### Monitoring Cache Health

```bash
# Check global cache size
du -sh ~/.orchestrator/research/

# Check project cache size
du -sh .knowledge/research/

# List all entries in global cache
jq '.entries | length' ~/.orchestrator/research/index.json

# List entries by age (oldest first)
jq '.entries | sort_by(.created_at)' ~/.orchestrator/research/index.json | jq '.[0:5]'
```

### Inspecting Individual Entries

```bash
# Find entries by topic (substring match)
jq '.entries[] | select(.topic | contains("React"))' ~/.orchestrator/research/index.json

# Find entries by tags
jq '.entries[] | select(.tags | index("performance") != null)' ~/.orchestrator/research/index.json

# View full entry content
jq '.' ~/.orchestrator/research/entries/<hash>.json
```

### Manual Cleanup

```bash
# Remove all entries older than 180 days (Unix)
find ~/.orchestrator/research/entries -mtime +180 -delete

# Regenerate index.json to reflect deletions
# (Note: Current implementation doesn't auto-compact, manual rebuild may be needed)

# Remove specific entry by topic hash
TOPIC_HASH=$(echo -n "my topic['tag1','tag2']" | sha256sum | cut -d' ' -f1)
rm ~/.orchestrator/research/entries/${TOPIC_HASH}.json
# Then remove from index.json manually

# Emergency: Reset global cache
rm -rf ~/.orchestrator/research/
# Engine will recreate on next run with auto_extract=true
```

---

## Common Issues & Solutions

### Issue 1: Cache Not Being Used (Always Fresh Research)

**Symptoms:**
- Every run does fresh research despite prior runs
- Agents never report cache hits
- Research cost not decreasing

**Diagnosis:**

```bash
# Check if MCP server is registered
cat .mcp.json | jq '.mcpServers | keys'

# Check if research-cache key is present
# If missing, ensure_research_mcp_config was never called (known issue)

# Check if cache directories exist
ls -la ~/.orchestrator/research/index.json  # Should exist after first run
ls -la .knowledge/research/index.json       # Should exist after first run

# Check engine logs for warnings
orchestrate --dry-run "test" 2>&1 | grep -i "research\|cache"
```

**Solutions:**

1. **MCP server binary missing:**
   ```bash
   # Verify MCP server exists
   ls -la ../sdlc-mcp-servers/research-cache/dist/index.js

   # If missing, rebuild
   cd ../sdlc-mcp-servers/research-cache
   npm install && npm run build
   ```

2. **Config disabled:**
   ```bash
   # Check config
   grep "research_cache:" config/default.yaml

   # Ensure enabled: true
   ```

3. **Config paths invalid:**
   ```bash
   # Verify global_dir is under home
   grep "global_dir:" config/default.yaml

   # Verify local_dir is valid relative path
   grep "local_dir:" config/default.yaml
   ```

---

### Issue 2: Eviction Not Working (Cache Growing Unbounded)

**Symptoms:**
- `~/.orchestrator/research/` growing very large
- `du -sh ~/.orchestrator/research/` shows >1GB
- No entries being removed despite max_entries=500

**Root Cause:** Fresh ResearchCache() instance created per phase (known issue #5)

**Temporary Solution:**
```bash
# Manual cleanup
find ~/.orchestrator/research/entries -type f | wc -l  # Count entries

# Keep only recent entries (preserve recent 500)
cd ~/.orchestrator/research/entries
ls -t | tail -n +501 | xargs rm -f
```

**Permanent Solution:** See [Review Findings](RESEARCH_CACHE_REVIEW_FINDINGS.md#warning-fresh-researchcache-per-extraction) — initialize cache once at startup, reuse across phases.

---

### Issue 3: Concurrent Write Corruption

**Symptoms:**
- `~/.orchestrator/research/index.json` contains invalid JSON
- Agents fail with "JSONDecodeError"
- Error: "Expecting value: line 1 column 1"

**Prevention:**
```python
# Verify fcntl locking is working (Unix only)
import fcntl
import os

lock_file = Path.home() / ".orchestrator" / "research" / ".index.lock"
with open(lock_file, 'w') as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    # Critical section
```

**Recovery:**
```bash
# Restore from backup (if available)
cp ~/.orchestrator/research/index.json.backup ~/.orchestrator/research/index.json

# OR regenerate from entries (lossy recovery)
jq -s '{entries: [.[] | {entry_id, topic, tags, created_at, ttl_days}]}' \
  ~/.orchestrator/research/entries/*.json > index.json.new
mv index.json.new index.json
```

---

### Issue 4: NFS/Network Filesystem Locking Fails

**Symptoms:**
- fcntl locking errors on NFS or network drives
- File corruption on concurrent writes
- "fcntl: No locks available"

**Solution:**
```bash
# Move research cache to local SSD
mkdir -p /mnt/local_ssd/.orchestrator/research

# Update config
orchestrate --config-override \
  "research_cache.global_dir=/mnt/local_ssd/.orchestrator/research" \
  "your feature"
```

**Or use project-local cache only:**
```yaml
# config/default.yaml
research_cache:
  enabled: true
  global_dir: /dev/null  # Disable global tier
  local_dir: .knowledge/research  # Use local only
  # ...
```

---

### Issue 5: Duplicate Recommendations Output

**Symptoms:**
- "Recommendations:" section printed twice at end of run
- Once from engine.py, once from main.py

**Root Cause:** Both code paths execute (known issue #10)

**Temporary Workaround:**
```bash
# Capture stderr only for duplicate check
orchestrate "feature" 2>&1 | grep -c "Recommendations:"
# If output > 1, duplicates exist
```

**Fix:** Add flag to track if printed:
```python
# In engine.py
if self.config.research_cache_context:
    self.config.research_cache_context._recommendations_printed = True

# In main.py
if config.research_cache_context and not getattr(config.research_cache_context, '_recommendations_printed', False):
    print(format_recommendations(...))
```

---

### Issue 6: Sensitive Data Leakage

**Symptoms:**
- Research entries (competitor analysis, security findings) readable by other users
- Security audit fails on file permissions

**Cause:** _atomic_write doesn't set restrictive permissions (known issue #7)

**Fix:**
```python
# In research_cache.py _atomic_write()
import os
fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write(data)
# Now temp file is only readable by owner
path.rename(tmp_path)
```

**Audit:**
```bash
# Check file permissions
ls -la ~/.orchestrator/research/entries/ | head -5
# Should show: -rw------- (0o600)
# NOT: -rw-r--r-- (0o644)

# Fix existing files
chmod 600 ~/.orchestrator/research/entries/*
chmod 600 ~/.orchestrator/research/index.json
chmod 700 ~/.orchestrator/research/
```

---

## Development Workflows

### Adding a New Phase for Auto-Extraction

**Task:** Make the caching_engineer phase auto-extract findings to cache

**Steps:**

1. **Add phase mapping in research_cache.py:**
   ```python
   # In _artifact_stem_from_phase()
   def _artifact_stem_from_phase(phase: str) -> str | None:
       mapping = {
           'architect': 'architecture',
           'principal_engineer': 'engineering_plan',
           'caching_engineer': 'caching_report',  # ← Add this
           'market_researcher': 'market_research',
           # ...
       }
       return mapping.get(phase)
   ```

2. **Verify artifact exists:**
   ```bash
   # After running caching_engineer phase, check:
   ls -la workspace/caching_engineer/caching_report.json
   ```

3. **Test extraction:**
   ```python
   # In test_research_cache.py
   def test_extract_research_from_caching_report(tmp_path):
       artifact_path = tmp_path / "caching_report.json"
       artifact_path.write_text(json.dumps({
           "recommendations": [
               {"finding": "Enable L2 cache", "benefit": "25% latency reduction"}
           ]
       }))

       entries = extract_research_from_artifact(artifact_path, 'caching_engineer', 'run-123')
       assert len(entries) > 0
       assert entries[0].source_phase == 'caching_engineer'
   ```

4. **Update engine.py and workflow_engine.py:**
   ```python
   # The central _artifact_stem_from_phase() will be used once
   # the code is refactored (see Review Finding #4)
   ```

---

### Debugging Agent Cache Usage

**Goal:** Verify agents are actually calling lookup_research and save_research

**Method 1: Check agent logs**
```bash
orchestrate --config-override observability.log_level=DEBUG \
  "your feature" 2>&1 | grep -i "lookup_research\|save_research\|flag_finding"
```

**Method 2: Modify agent definition**
```markdown
# In .claude/agents/architect.md, add debug output:

## Research Cache Protocol

When you use the research cache, log your actions:

1. ALWAYS call lookup_research(query, tags)
   - Log: `[CACHE DEBUG] Calling lookup_research: {query}, {tags}`
   - If miss=false, log: `[CACHE HIT] Found {N} entries, skipping fresh research`
   - If miss=true, log: `[CACHE MISS] No entries found, performing fresh research`

2. After fresh research, call save_research(...)
   - Log: `[CACHE DEBUG] Saving research: {topic}, tier={tier}`
```

**Method 3: Inspect cache contents**
```bash
# After run completes:

# Check if new entries were added
jq '.entries | length' ~/.orchestrator/research/index.json

# Check entry recency
jq '.entries[-1]' ~/.orchestrator/research/index.json

# Find entries from this run
RUN_ID="<from logs>"
jq ".entries[] | select(.run_id == \"$RUN_ID\")" ~/.orchestrator/research/index.json
```

---

### Adding Tests for Review Issues

**Test file:** `tests/test_research_cache.py`

```python
def test_validate_cache_paths_is_called(tmp_path, monkeypatch):
    """Verify path validation is invoked in save_entry."""
    validation_called = []

    def mock_validate(g, l, p):
        validation_called.append(True)

    monkeypatch.setattr(
        'orchestrator.research_cache.validate_cache_paths',
        mock_validate
    )

    cache = ResearchCache(global_entries=[])
    entry = ResearchEntry(
        topic="test", content="test content",
        tags=[], tier='global',
        created_at="2026-03-26T00:00:00Z", ttl_days=90,
        source_phase='test', run_id='run-1'
    )

    save_entry(cache, entry)
    assert validation_called, "validate_cache_paths was not called"
```

---

## Performance Optimization

### Cache Lookup Benchmarks

```python
import time
from orchestrator.research_cache import load_cache, lookup

# Measure lookup time at different scales
for entry_count in [10, 100, 500, 1000]:
    cache = ResearchCache(global_entries=[create_entry(f"topic_{i}") for i in range(entry_count)])

    start = time.perf_counter()
    for _ in range(100):
        results = lookup(cache, "query", ["tag"])
    elapsed = time.perf_counter() - start

    per_query = (elapsed / 100) * 1000
    print(f"{entry_count:4d} entries: {per_query:.2f}ms per lookup")
```

**Expected output:**
```
  10 entries:  0.05ms per lookup
 100 entries:  0.15ms per lookup
 500 entries:  0.50ms per lookup
1000 entries:  1.00ms per lookup
```

If lookup is significantly slower, the linear scan in `lookup()` may need optimization (e.g., tag index).

---

## Debugging MCP Server

### Manual MCP Server Test

```bash
# Terminal 1: Start MCP server
cd ../sdlc-mcp-servers/research-cache
GLOBAL_RESEARCH_DIR=~/.orchestrator/research \
PROJECT_RESEARCH_DIR=.knowledge/research \
PROJECT_ROOT=$(pwd) \
node dist/index.js

# Terminal 2: Test with MCP client (via Python orchestrator)
python -c "
from orchestrator.agents import AgentInvocation
from orchestrator.models import OrchestratorConfig

config = OrchestratorConfig()
agent = AgentInvocation(role='deep_researcher', config=config)

# This invocation will use the running MCP server
# Test via agent execution
"
```

### Verify Tool Availability

```bash
# Check that research-cache tools are registered
grep -A 50 "research-cache" .mcp.json

# Expected output:
# "research-cache": {
#   "type": "stdio",
#   "command": "node",
#   "args": ["...research-cache/dist/index.js"],
#   "env": {
#     "GLOBAL_RESEARCH_DIR": "...",
#     "PROJECT_RESEARCH_DIR": "...",
#     "PROJECT_ROOT": "..."
#   }
# }
```

---

## Security Checklist

### Before Sharing Cache on Shared Systems

- [ ] Verify file permissions are 0o600: `ls -la ~/.orchestrator/research/entries/`
- [ ] Verify directory permissions are 0o700: `ls -la ~/.orchestrator/research/`
- [ ] Check for sensitive data in entries: `jq '.content' ~/.orchestrator/research/entries/* | grep -i "password\|api\|secret"`
- [ ] Enable restrictive umask: `umask 0o077`
- [ ] Consider using separate cache directory with isolated permissions

### Before Sharing Project Cache

- [ ] Do not commit `.knowledge/research/` to git (add to .gitignore)
- [ ] Review entries for sensitive competitor data before sharing project
- [ ] Consider encrypting project cache if shared team directory

---

## Troubleshooting Checklist

Use this for systematic debugging:

- [ ] Is research_cache.enabled=true in config?
- [ ] Does MCP server binary exist? `ls -la ../sdlc-mcp-servers/research-cache/dist/index.js`
- [ ] Does MCP server build? `npm run build` in research-cache dir
- [ ] Is .mcp.json valid JSON? `jq . .mcp.json`
- [ ] Are cache directories created? `ls -la ~/.orchestrator/research/ .knowledge/research/`
- [ ] Are index.json files valid? `jq . ~/.orchestrator/research/index.json`
- [ ] Are agents calling lookup_research? Check logs with DEBUG level
- [ ] Are findings being flagged? Check research_cache_context.findings in logs
- [ ] Do recommendations appear at end of run?
- [ ] File permissions correct (0o600)? `ls -la ~/.orchestrator/research/entries/`

---

## References

- [Review Findings & Known Issues](RESEARCH_CACHE_REVIEW_FINDINGS.md)
- [Implementation Guide](RESEARCH_CACHE_IMPLEMENTATION.md)
- [Feature Documentation](features/research-cache-mcp-server.md)
- [Test Suite](../../tests/test_research_cache.py)
- [MCP Server Code](../../../sdlc-mcp-servers/research-cache/)
