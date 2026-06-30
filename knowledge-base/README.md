# Project Knowledge Base

> **Strategic foundation for AI coding agent ecosystem.**

---

## 🎯 Phase 0 Complete

This knowledge base contains comprehensive research and architecture decisions for building an AI coding agent system.

**Zero lines of code written. Zero random decisions made.**

---

## 📁 Structure

```
Project Knowledge Base/
├── 01-Ecosystem/           # Ecosystem overview & statistics
├── 02-Architecture/        # System architecture & constraints
├── 03-Agents/              # Agent registry & capability database
├── 04-Tools/               # Tool registry & integration
├── 05-Technologies/        # Technology radar & decisions
├── 06-Research/            # Research findings & notes
├── 07-Decisions/           # Architecture Decision Records (ADRs)
├── 08-Standards/           # Coding & documentation standards
├── 09-Roadmap/             # Project roadmap & phases
├── 10-Experiments/         # Experiment logs & results
├── 11-Competitors/         # Competitor analysis & matrix
├── 12-Risks/               # Risk register & analysis
├── 13-Glossary/            # Term definitions
└── 14-References/          # Links & resources
```

---

## 🔑 Core Philosophy

### Our System Has Three Elements Only

```
┌─────────────────────────────────────────┐
│               Core Brain                 │
│         (The only "brain")              │
└─────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│            Capability Engine             │
│        (Routes to tools)                │
└─────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│        Universal Tool Adapter            │
│    (Standardizes all external agents)    │
└─────────────────────────────────────────┘

Anything else = Plugin
```

### All Other Agents Are Tools

```
OpenHands     → Tool (not executor)
Claude Code   → Tool (not executor)
Aider         → Tool (not executor)
SWE-Agent     → Tool (not executor)
Browser Use   → Tool (not executor)
```

---

## 📊 Key Metrics

| Metric | Value |
|--------|-------|
| Categories | 40+ |
| Major Competitors | 15+ |
| Core Capabilities | 50+ |
| ADRs | 10+ |
| Experiments | 5+ |
| Architecture Constraints | 9 |

---

## ✅ Phase 0 Exit Criteria

We can now answer:

1. ✅ What is the best tool for each capability?
2. ✅ What is the second and third alternative?
3. ✅ How to replace any tool without changing Core?
4. ✅ What is the cost of each tool?
5. ✅ What is the quality of each tool?
6. ✅ Does it work in the cloud?
7. ✅ Does it have API or CLI?
8. ✅ Is it production-ready?
9. ✅ Can it be integrated via Universal Tool Adapter?
10. ✅ Is there any engineering reason to reject it?

---

## 🔐 Architecture Constraints

| Constraint | Description |
|------------|-------------|
| No Vendor Lock | Never depend exclusively on one provider |
| No Local Dependency | No hard-coded local-only dependencies |
| Cloud Native | Design for cloud from day one |
| Everything API | All functionality via API |
| Everything Replaceable | Any component can be swapped |
| Everything Observable | Full logging and tracing |
| Everything Versioned | Version control everything |
| Everything Testable | Unit, integration, E2E tests |
| Everything Documented | Complete docs required |

---

## 📈 Capability Categories

### Code Operations
- Generate Code, Edit Code, Review Code
- Understand Repository, Create Tests, Fix Bugs

### Web Operations
- Use Browser, Search Web, Extract Content

### Deployment
- Deploy to Cloud, Create PR, Manage Issues

### Infrastructure
- Run CI/CD, Build Artifacts, Monitor Systems

---

## 🏆 Selection Criteria

When choosing between competing tools:

```
1. Most Active (recent commits, issues)
   ↓
2. Most Documented (clear docs, examples)
   ↓
3. Most Used (large user base)
   ↓
4. Most Extensible (plugin system)
   ↓
5. Most Independent (no vendor lock)
   ↓
Winner
```

---

## 📦 Deliverables (Phase 0)

| # | Deliverable | Status |
|---|-------------|--------|
| 1 | Knowledge Base Structure | ✅ |
| 2 | Repositories Database | ✅ |
| 3 | Projects Classification | ✅ |
| 4 | Competitor Matrix | ✅ |
| 5 | Agent Capability Database | ✅ |
| 6 | Capability Graph | ✅ |
| 7 | Universal Tool Interface | ✅ |
| 8 | Architecture Constraints | ✅ |
| 9 | Decision Rules | ✅ |
| 10 | Capability Taxonomy | ✅ |
| 11 | Dependency Graph | ✅ |
| 12 | Technology Radar | ✅ |
| 13 | ADRs | ✅ |
| 14 | Exit Criteria Validation | ✅ |

---

## 🚀 Next Phase: Phase 1

**Phase 1: Core Implementation (10% → 20%)**

- Core Brain implementation
- Capability Engine implementation
- Universal Tool Adapter implementation
- Initial adapters (OpenHands, Aider, Browser Use)

---

## 📞 Related Documents

- [02-Architecture/Architecture.md](./02-Architecture/01-Architecture.md) - Full architecture
- [03-Agents/Agent-Capability-Database.md](./03-Agents/02-Agent-Capability-Database.md) - Capability database
- [07-Decisions/Decisions.md](./07-Decisions/01-Decisions.md) - All ADRs
- [11-Competitors/Competitors.md](./11-Competitors/01-Competitors.md) - Competitor analysis
