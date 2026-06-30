# Architecture Decision Records (ADRs)

> **Every architectural decision is a standalone document.**

---

## ADR-001: Why One Agent?

**Date:** 2024-01-01
**Status:** Accepted
**Context: We considered multi-agent architectures where multiple agents work together.

**Decision:** We use ONE Core Agent that orchestrates all capabilities.

**Rationale:**
1. Simplicity - Single decision point
2. Consistency - One way to handle tasks
3. Debugging - Easier to trace issues
4. Performance - No inter-agent communication overhead

**Consequences:**
- Core Agent must be capable of delegation
- External agents become Tools, not peers
- All capabilities route through the Core

---

## ADR-002: Why Agents as Tools Only?

**Date:** 2024-01-01
**Status:** Accepted

**Context:** Other projects use multiple agents as equals.

**Decision:** All external agents are Tools, not independent agents.

```
OpenHands → Tool
Claude Code → Tool
Aider → Tool
SWE-Agent → Tool
Browser Use → Tool
```

**Rationale:**
1. **Composability** - Standard interface
2. **Replaceability** - Swap any tool without changing Core
3. **Testability** - Mock any tool easily
4. **Observability** - Trace all tool calls

**Consequences:**
- Must create adapters for each tool
- Core never executes directly
- All tools have same interface

---

## ADR-003: Why Cloud-First?

**Date:** 2024-01-01
**Status:** Accepted

**Context:** We considered local-first architecture.

**Decision:** Cloud-native by default.

**Rationale:**
1. **Scalability** - Horizontal scaling on demand
2. **Accessibility** - Available anywhere
3. **Maintenance** - Centralized updates
4. **Resources** - Unlimited compute (with cost)

**Consequences:**
- Requires internet for most features
- Offline mode is secondary
- Cost management required

---

## ADR-004: Why Universal Tool Adapter?

**Date:** 2024-01-01
**Status:** Accepted

**Context:** Direct tool integration creates coupling.

**Decision:** Every tool goes through Universal Tool Adapter.

```
Tool
  ↓
Input Schema (validated)
  ↓
Executor (isolated)
  ↓
Output Schema (validated)
```

**Rationale:**
1. **Isolation** - Tool failures don't crash Core
2. **Validation** - Input/output checked
3. **Flexibility** - Any tool can be added
4. **Debugging** - Clear boundaries

**Consequences:**
- Adapter development required
- Slight performance overhead
- More code to maintain

---

## ADR-005: Why Capability-Based Selection?

**Date:** 2024-01-01
**Status:** Accepted

**Context:** We could select tools by name or category.

**Decision:** Select by capability.

**Rationale:**
1. **Flexibility** - Best tool for each task
2. **Resilience** - If one tool fails, switch
3. **Optimal** - Always using best tool
4. **Future-proof** - New tools fit naturally

**Consequences:**
- Capability taxonomy required
- Continuous evaluation needed
- Tool selection logic complexity

---

## ADR-006: Why Everything Observable?

**Date:** 2024-01-01
**Status:** Accepted

**Decision:** Full observability for all components.

**Rationale:**
1. **Debugging** - Trace issues quickly
2. **Performance** - Identify bottlenecks
3. **Compliance** - Audit trails
4. **Trust** - User confidence

**Consequences:**
- Logging infrastructure required
- Storage costs
- Performance monitoring overhead

---

## ADR-007: Why Everything Versioned?

**Date:** 2024-01-01
**Status:** Accepted

**Decision:** Version everything - code, config, data.

**Rationale:**
1. **Reproducibility** - Same results every time
2. **Rollback** - Easy to revert
3. **Audit** - Track all changes
4. **Collaboration** - Merge conflict resolution

**Consequences:**
- Version control overhead
- Release process complexity
- Storage for history

---

## ADR-008: Why Everything Replaceable?

**Date:** 2024-01-01
**Status:** Accepted

**Decision:** No component is irreplaceable.

**Rationale:**
1. **Vendor independence** - Not locked to provider
2. **Technology evolution** - Upgrade easily
3. **Risk mitigation** - Failure doesn't end system
4. **Freedom** - Choose best tools

**Consequences:**
- Abstract interfaces required
- Adapter overhead
- More integration code

---

## ADR-009: Why No Local Dependencies?

**Date:** 2024-01-01
**Status:** Accepted

**Decision:** All dependencies must be cloud-accessible or containerized.

**Rationale:**
1. **Consistency** - Same environment everywhere
2. **Portability** - Deploy anywhere
3. **Scalability** - Easy horizontal scaling
4. **Maintenance** - Single dependency source

**Consequences:**
- Internet required
- Container overhead
- Dependency on external services

---

## ADR-010: Why Structured Selection Criteria?

**Date:** 2024-01-01
**Status:** Accepted

**Decision:** When choosing between competing tools, use structured criteria.

**Selection Order:**
```
1. Most Active (recent commits, issues)
2. Most Documented (clear docs, examples)
3. Most Used (large user base)
4. Most Extensible (plugin system)
5. Most Independent (no vendor lock)
```

**Rationale:**
1. **Objectivity** - Less bias
2. **Consistency** - Same decision process
3. **Justifiability** - Clear reasoning
4. **Reproducibility** - Others can verify

**Consequences:**
- Evaluation process required
- Subjectivity in scoring
- Time investment

---

## Decision Log

| ADR | Title | Date | Status |
|-----|-------|------|--------|
| ADR-001 | Why One Agent? | 2024-01-01 | Accepted |
| ADR-002 | Why Agents as Tools Only? | 2024-01-01 | Accepted |
| ADR-003 | Why Cloud-First? | 2024-01-01 | Accepted |
| ADR-004 | Why Universal Tool Adapter? | 2024-01-01 | Accepted |
| ADR-005 | Why Capability-Based Selection? | 2024-01-01 | Accepted |
| ADR-006 | Why Everything Observable? | 2024-01-01 | Accepted |
| ADR-007 | Why Everything Versioned? | 2024-01-01 | Accepted |
| ADR-008 | Why Everything Replaceable? | 2024-01-01 | Accepted |
| ADR-009 | Why No Local Dependencies? | 2024-01-01 | Accepted |
| ADR-010 | Why Structured Selection Criteria? | 2024-01-01 | Accepted |

---

## Related Documents

- [02-Architecture/Architecture.md](../02-Architecture/01-Architecture.md) - Architecture Overview
- [02-Architecture/Capability-Graph.md](../02-Architecture/02-Capability-Graph.md) - Capability Selection
