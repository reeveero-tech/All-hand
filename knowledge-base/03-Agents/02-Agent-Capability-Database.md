# Agent Capability Database

> **The most important database in the project.**
> Core Concept: All other agents are not executors or planners, but Tools.

## Universal Tool Interface

Every agent in the world should transform to the same shape:

```
┌─────────────────────────────────────────┐
│                  Tool                     │
│  ┌─────────────────────────────────┐    │
│  │         Input Schema             │    │
│  │  (Validated, Typed)             │    │
│  └─────────────────────────────────┘    │
│                 │                        │
│                 ▼                        │
│  ┌─────────────────────────────────┐    │
│  │          Executor               │    │
│  │  (Isolated, Sandbox)            │    │
│  └─────────────────────────────────┘    │
│                 │                        │
│                 ▼                        │
│  ┌─────────────────────────────────┐    │
│  │         Output Schema            │    │
│  │  (Validated, Typed)             │    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
```

**We don't deal with projects directly. We deal with Adapters.**

---

## Agent → Tool Transformation

| Agent | Becomes Tool | Adapter Status |
|-------|-------------|----------------|
| OpenHands | ✅ | Available |
| Claude Code | ✅ | CLI Adapter |
| Cursor | ✅ | IDE Adapter |
| Aider | ✅ | CLI Adapter |
| Cline | ✅ | VS Code Adapter |
| SWE-Agent | ✅ | SWE Adapter |
| Browser Use | ✅ | Browser Adapter |
| GitHub Copilot | ⚠️ | Limited Adapter |
| Goose | ✅ | CLI Adapter |
| Devin | ⚠️ | No Adapter |

---

## Capability Database Schema

```yaml
CapabilityRecord:
  Name: "Generate Backend"
  Repository: ""
  GitHub: ""
  
  # Input/Output
  Input: []
  Output: []
  
  # Quality Metrics
  Strength: []
  Weakness: []
  
  # Performance
  Latency: Low/Medium/High
  Cost: Low/Medium/High
  
  # Requirements
  Required Model: ""
  
  # Deployment
  Offline: true/false
  Cloud: true/false
  API: true/false
  CLI: true/false
  Docker: true/false
  SDK: true/false
  
  # Functional Capabilities
  Can Execute Code: true/false
  Can Edit Code: true/false
  Can Browse: true/false
  Can Debug: true/false
  Can Test: true/false
  Can Review: true/false
  Can Search: true/false
  Can Deploy: true/false
  Can Refactor: true/false
  
  # Quality
  Reliability: 0-100%
  Replacement: Easy/Medium/Hard
```

---

## Capability Index

### Code Generation Capabilities

| Capability | Primary Tool | Alt 1 | Alt 2 | Alt 3 |
|------------|-------------|-------|-------|-------|
| Generate Backend API | OpenHands | Aider | Claude Code | Goose |
| Generate REST API | OpenHands | Claude Code | - | - |
| Generate GraphQL API | OpenHands | - | - | - |
| Generate Frontend | OpenHands | Claude Code | Cursor | - |
| Generate React | OpenHands | Claude Code | Cursor | - |
| Generate Tests | Claude Test | OpenHands | - | - |
| Generate Config | OpenHands | - | - | - |
| Generate Docs | OpenHands | Claude Code | - | - |

### Code Editing Capabilities

| Capability | Primary Tool | Alt 1 | Alt 2 | Alt 3 |
|------------|-------------|-------|-------|-------|
| Edit Code | Aider | Cline | OpenHands | Claude Code |
| Refactor | Aider | OpenHands | Claude Code | - |
| Fix Bugs | OpenHands | SWE-Agent | Claude Code | - |
| Optimize | OpenHands | Claude Code | - | - |
| Lint | Cline | Claude Code | - | - |
| Format | Cline | Claude Code | - | - |

### Code Understanding Capabilities

| Capability | Primary Tool | Alt 1 | Alt 2 | Alt 3 |
|------------|-------------|-------|-------|-------|
| Understand Repository | SWE-Agent | OpenHands | Tree-sitter | - |
| Analyze Structure | OpenHands | Tree-sitter | AST | - |
| Find Dependencies | OpenHands | - | - | - |
| Search Code | OpenHands | Claude Code | - | - |

### Browser Capabilities

| Capability | Primary Tool | Alt 1 | Alt 2 | Alt 3 |
|------------|-------------|-------|-------|-------|
| Use Browser | Browser Use | Claude Computer Use | Playwright Agent | - |
| Search Web | Browser Use | Claude Computer Use | - | - |
| Fill Forms | Browser Use | Playwright Agent | - | - |
| Extract Content | Browser Use | Claude Code | - | - |

### Deployment Capabilities

| Capability | Primary Tool | Alt 1 | Alt 2 | Alt 3 |
|------------|-------------|-------|-------|-------|
| Deploy to Cloud | OpenHands | GitHub Actions | - | - |
| Run CI/CD | GitHub Actions | OpenHands | - | - |
| Create PR | GitHub CLI | OpenHands | Claude Code | - |
| Manage Issues | GitHub API | OpenHands | - | - |

### Execution Capabilities

| Capability | Primary Tool | Alt 1 | Alt 2 | Alt 3 |
|------------|-------------|-------|-------|-------|
| Execute Shell | OpenHands | Claude Code | Aider | Cline |
| Run Docker | OpenHands | - | - | - |
| Run Tests | OpenHands | Claude Code | - | - |

---

## Decision Matrix

### When to Choose Each Tool

| Scenario | Best Tool | Why |
|----------|-----------|-----|
| Need full-stack development | OpenHands | All capabilities |
| Need CLI-only workflow | Aider | Lightweight, git-native |
| Need VS Code integration | Cline | IDE-native |
| Need browser automation | Browser Use | AI-first browser agent |
| Need SWE-bench style fixes | SWE-Agent | Specialized for bugs |
| Need Claude's reasoning | Claude Code | Best reasoning |
| Need quick suggestions | GitHub Copilot | Low latency |

---

## Selection Algorithm

```python
def select_tool(capability: str, context: dict) -> Tool:
    """
    Selection Criteria (in order):
    1. Activity level (most active first)
    2. Documentation quality
    3. Usage metrics
    4. Extensibility
    5. Independence
    """
    candidates = get_candidates(capability)
    
    for criterion in SELECTION_CRITERIA:
        candidates = sort_by(candidates, criterion)
    
    return candidates[0]  # Best match
```

---

## Adapter Registry

| Tool | Adapter | Status | Version |
|------|---------|--------|---------|
| OpenHands | universal-openhands-adapter | ✅ Stable | 1.0 |
| Aider | universal-aider-adapter | ✅ Stable | 1.0 |
| Cline | universal-cline-adapter | ✅ Stable | 1.0 |
| Claude Code | claude-code-adapter | ⚠️ CLI only | 0.5 |
| Browser Use | browser-use-adapter | ✅ Stable | 1.0 |
| SWE-Agent | swe-agent-adapter | ✅ Stable | 1.0 |

---

## Related Documents

- [01-Agents](./01-Agents.md) - Agent Registry
- [02-Architecture/Capability-Graph](../02-Architecture/02-Capability-Graph.md) - Capability Graph
- [11-Competitors/Competitor-Matrix](../11-Competitors/Competitor-Matrix.md) - Competitor Comparison
