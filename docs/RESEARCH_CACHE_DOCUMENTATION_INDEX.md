# Research Cache Documentation Index

**Status:** ✅ Implementation Complete | ⚠️ Review Issues Documented | 📚 Comprehensive Documentation

This index guides developers to the right documentation for their needs.

---

## 📋 Quick Navigation

### I want to...

| Goal | Document | Read Time |
|------|----------|-----------|
| Understand what the feature does | [User Guide](#user-guide) | 5 min |
| Learn about review issues found | [Review Findings](#review-findings--known-issues) | 10 min |
| Implement a fix or new feature | [Implementation Guide](#implementation-guide) | 15 min |
| Debug a problem | [Maintenance Guide](#maintenance--troubleshooting) | 10 min |
| Use the API in code | [API Reference](#api-reference) | 15 min |
| See code examples | [Examples](#code-examples) | 5 min |

---

## 📚 Documentation Map

### User Guide
**File:** `docs/features/research-cache-mcp-server.md`
**Audience:** End users, product managers, agents
**Content:**
- What is research cache?
- Benefits (cost savings, latency reduction)
- How to use it (agent workflow)
- Configuration options
- Examples

**Start here if:** You're new to the feature and want to understand how it works

---

### Review Findings & Known Issues
**File:** `RESEARCH_CACHE_REVIEW_FINDINGS.md`
**Audience:** Developers, maintainers
**Content:**
- 9 warning-level issues identified in code review
- 5 low-severity items
- Root cause analysis for each
- Recommended fixes with code examples
- Testing gaps that need to be addressed
- Maintenance checklist before production

**Start here if:** You're fixing issues or want to understand what needs attention

**Key Issues:**
1. ⚠️ MCP config return shape inconsistency
2. ⚠️ Security path validation function never called
3. ⚠️ ensure_research_mcp_config never called (pattern divergence)
4. ⚠️ Phase-to-artifact mapping incomplete
5. ⚠️ Fresh ResearchCache per extraction (eviction broken)
6. ⚠️ ensure_research_mpc_config is dead code
7. ⚠️ Atomic write lacks restrictive permissions
8. ⚠️ Prompt injection contradiction
9. ⚠️ fcntl portability concerns
10. Low: Duplicate output, exception silencing, inline imports

---

### Implementation Guide
**File:** `RESEARCH_CACHE_IMPLEMENTATION.md`
**Audience:** Developers implementing features
**Content:**
- Architecture components (4 main parts)
- Phase-by-phase implementation tasks
- Function specifications with signatures
- Acceptance criteria
- Testing strategy
- Validation checklist before merging
- Debugging tips
- Performance benchmarks
- Maintenance tasks

**Start here if:** You're implementing a new feature or fixing a component

**Phases:**
- Phase 0: Config & Models (foundation)
- Phase 1: Python Cache Module
- Phase 2: Node.js MCP Server
- Phase 3: Engine Integration
- Phase 4: Prompt Injection
- Phase 5: Agent Definitions
- Phase 6: Configuration & Testing

---

### Maintenance & Troubleshooting
**File:** `RESEARCH_CACHE_MAINTENANCE.md`
**Audience:** Operations, developers
**Content:**
- Daily operations (monitoring, inspection, cleanup)
- Common issues & solutions (6 detailed scenarios)
- Development workflows (extending the feature)
- Performance optimization
- Security checklist
- Debugging workflows
- Comprehensive troubleshooting checklist

**Start here if:** You're experiencing problems or want to maintain the cache

**Common Issues Covered:**
- Cache not being used
- Eviction not working
- Concurrent write corruption
- NFS/network filesystem issues
- Duplicate recommendations
- Sensitive data leakage

---

### API Reference
**File:** `RESEARCH_CACHE_API_REFERENCE.md`
**Audience:** Developers using the Python or MCP APIs
**Content:**
- Configuration schema with all fields explained
- Python function signatures with examples
- MCP server tool definitions
- Agent integration patterns
- Data model definitions (ResearchEntry, Finding, etc.)
- Error handling
- Code examples

**Start here if:** You're writing code that uses research cache

**Includes:**
- 12+ Python function references
- 4 MCP server tool specifications
- 4 Pydantic model definitions
- Agent integration patterns
- Copy-pastable code examples

---

## 🔗 Cross-References

### By Implementation Phase

| Phase | Files | Primary Doc |
|-------|-------|-------------|
| **Startup (Config)** | models.py, config/default.yaml | Implementation Guide: Phase 0 |
| **Python Module** | research_cache.py | Implementation Guide: Phase 1, API Reference |
| **MCP Server** | ../sdlc-mcp-servers/research-cache/ | Implementation Guide: Phase 2 |
| **Engine Init** | engine.py, workflow_engine.py | Implementation Guide: Phase 3, Review Finding #3, #5 |
| **Prompt Injection** | phases.py | Implementation Guide: Phase 4, Review Finding #8 |
| **Agent Updates** | .claude/agents/*.md | Implementation Guide: Phase 5 |
| **Testing** | tests/test_research_cache.py | Implementation Guide: Phase 6 |

### By Issue Type

| Issue Type | Document | Section |
|-----------|----------|---------|
| **Architectural** | Review Findings | #3 (pattern divergence), #5 (eviction) |
| **Security** | Review Findings | #2 (validation), #7 (permissions) |
| **Testing** | Implementation Guide | Testing section, Validation Checklist |
| **Maintenance** | Maintenance Guide | Common Issues, Troubleshooting |
| **API Usage** | API Reference | All sections |

---

## 🎯 Common Workflows

### Fixing Review Issue #1: MCP Config Shape

**Files to modify:**
- `src/orchestrator/research_cache.py` (get_research_mcp_config return value)
- `src/orchestrator/engine.py` (merge pattern)
- `src/orchestrator/workflow_engine.py` (merge pattern)

**Documents:**
1. Review Findings: "MCP Config Return Shape Inconsistency"
2. API Reference: "MCP Configuration Functions"
3. Implementation Guide: "Phase 3: Engine Integration"

---

### Fixing Review Issue #5: Eviction Accuracy

**Files to modify:**
- `src/orchestrator/engine.py` (add self._research_cache)
- `src/orchestrator/workflow_engine.py` (add self._research_cache)
- `tests/test_research_cache.py` (add test_multi_phase_extraction_evicts_correctly)

**Documents:**
1. Review Findings: "Fresh ResearchCache Per Extraction"
2. Implementation Guide: "Phase 3: Engine Integration"
3. API Reference: "load_cache" function spec

---

### Debugging Cache Not Used

**Steps:**
1. Read Maintenance Guide: "Issue 1: Cache Not Being Used"
2. Run diagnostic checks (command examples provided)
3. Check Review Findings for context (why it might happen)
4. Verify API usage in API Reference

---

### Adding New Phase to Auto-Extraction

**Steps:**
1. Read Implementation Guide: "Phase 1: Python Cache Module" (extract_research_from_artifact)
2. Add phase mapping in research_cache.py
3. Write test in tests/test_research_cache.py
4. Follow Development Workflows in Maintenance Guide

---

## 📊 Documentation Statistics

| Metric | Value |
|--------|-------|
| Total documentation files | 5 |
| Total lines of documentation | ~3000 |
| Code examples | 40+ |
| API functions documented | 12 |
| MCP tools documented | 4 |
| Data models documented | 4 |
| Review issues documented | 14 |
| Common issues covered | 6 |
| Code templates provided | 15+ |

---

## ✅ Before Using This Feature

### Checklist for New Developers

- [ ] Read User Guide (5 min) — understand what it does
- [ ] Read Review Findings (10 min) — understand known issues
- [ ] Scan Implementation Guide (5 min) — understand architecture
- [ ] Review API Reference for your use case (5-15 min)
- [ ] See code examples in API Reference (5 min)
- [ ] Run provided tests: `pytest tests/test_research_cache.py -v`

**Total onboarding time:** ~30-45 minutes

---

## 📝 Recent Changes

**Last Updated:** 2026-03-26

### What's New
- ✅ Complete implementation of research cache feature
- ✅ Comprehensive API documentation
- ✅ Review findings documented with fixes
- ✅ Maintenance and troubleshooting guide
- ✅ Implementation guide with all phases
- ⚠️ 9 warning-level issues identified (non-blocking)

### What Needs Attention
- [ ] Fix MCP config shape inconsistency (issue #1)
- [ ] Call validate_cache_paths (issue #2)
- [ ] Integrate ensure_research_mcp_config (issue #3)
- [ ] Use central phase-to-artifact mapping (issue #4)
- [ ] Initialize ResearchCache once at startup (issue #5)
- [ ] Set restrictive file permissions (issue #7)
- [ ] Fix prompt injection wording (issue #8)

See [Review Findings](RESEARCH_CACHE_REVIEW_FINDINGS.md) for details and fixes.

---

## 🔍 Finding Information

### By Keyword

**"How do I..."**
- Use the cache in code? → API Reference
- Fix a bug? → Review Findings + Implementation Guide
- Debug a problem? → Maintenance Guide
- Add a new phase? → Maintenance Guide: Development Workflows
- Understand the architecture? → Implementation Guide

**"What is..."**
- A ResearchEntry? → API Reference: Data Models
- The global vs project tier? → User Guide or Implementation Guide
- An MCP tool? → API Reference: MCP Server Tools

**"Where is..."**
- The Python module? → `src/orchestrator/research_cache.py`
- The MCP server? → `../sdlc-mcp-servers/research-cache/`
- The unit tests? → `tests/test_research_cache.py`
- The integration tests? → `tests/integration/test_research_cache_integration.py`
- The config? → `config/default.yaml`

---

## 🐛 Issue Tracking

All known issues documented in: [RESEARCH_CACHE_REVIEW_FINDINGS.md](RESEARCH_CACHE_REVIEW_FINDINGS.md)

**Format:**
- Issue #, Severity, File, Description, Recommendation, Current Behavior

**Priorities:**
1. **Critical (blocking):** None — all issues are non-blocking
2. **Warnings (should fix):** 9 issues with code examples and fixes
3. **Low (nice to have):** 5 issues (portability, cleanup)

---

## 🎓 Learning Path

### For New Team Members
1. **Day 1:** Read User Guide + Review Findings (20 min)
2. **Day 2:** Read Implementation Guide phases 0-3 (30 min)
3. **Day 3:** Review API Reference + run tests (30 min)
4. **Day 4:** Debug exercise from Maintenance Guide (30 min)

### For Maintainers
1. **Initial:** All of above + Implementation phases 4-6 (1 hour)
2. **Ongoing:** Maintenance Guide for daily operations
3. **Incident:** Maintenance Guide → Common Issues section

### For Feature Developers
1. **Setup:** Read Implementation Guide for relevant phase (15 min)
2. **Implementation:** Use API Reference + code examples
3. **Testing:** Review Implementation Guide testing section

---

## 📞 Support

### Questions?

- **"Why does X work this way?"** → Review Findings (explains design decisions)
- **"How do I use X?"** → API Reference + code examples
- **"X is broken"** → Maintenance Guide: Common Issues
- **"I'm implementing Y"** → Implementation Guide phase-by-phase

### Reporting Issues

When reporting issues, reference:
- Current behavior (what happens)
- Expected behavior (what should happen)
- Steps to reproduce
- Relevant error messages
- Affected component (see Quick Reference table)

---

## 📖 Complete File List

```
docs/
├── RESEARCH_CACHE_DOCUMENTATION_INDEX.md  ← You are here
├── RESEARCH_CACHE_REVIEW_FINDINGS.md       ← Issues & recommendations
├── RESEARCH_CACHE_IMPLEMENTATION.md        ← How to build/extend
├── RESEARCH_CACHE_MAINTENANCE.md           ← How to operate/debug
├── RESEARCH_CACHE_API_REFERENCE.md         ← API documentation
└── features/
    └── research-cache-mcp-server.md        ← User guide
```

---

## ✨ Next Steps

1. **For immediate use:** Read User Guide + API Reference
2. **For development:** Read Implementation Guide + relevant phase
3. **For maintenance:** Bookmark Maintenance Guide
4. **For understanding issues:** Read Review Findings
5. **For code examples:** See API Reference: Examples section

---

**Last Updated:** 2026-03-26 | **Status:** Complete | **Review:** Pass with 9 Warnings
