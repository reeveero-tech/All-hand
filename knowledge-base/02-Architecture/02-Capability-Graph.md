# Capability Graph

> **We don't classify projects. We classify capabilities.**

Instead of:
```
1000 projects
```

We transform to:
```
Capabilities
  ↓
  ├── Generate Code
  │     ├── OpenHands ✅
  │     ├── Aider ✅
  │     ├── Cline ✅
  │     └── Claude Code ✅
  │
  ├── Edit Code
  │     ├── Aider ✅
  │     ├── Cline ✅
  │     └── VS Code AI ✅
  │
  ├── Review Code
  │     ├── OpenHands ✅
  │     ├── PR Review Agent ✅
  │     └── CodeRabbit ✅
  │
  ├── Use Browser
  │     ├── Browser Use ✅
  │     ├── Claude Computer Use ✅
  │     └── Playwright Agent ✅
  │
  └── Deploy
        ├── OpenHands ✅
        ├── GitHub Actions ✅
        └── Terraform Agent ✅
```

---

## Capability Graph Structure

```yaml
Capability:
  name: "Generate Backend"
  category: "Code Generation"
  tools:
    - name: "OpenHands"
      score: 9/10
      latency: Low
      cost: Medium
      reliability: 95%
    - name: "Aider"
      score: 8/10
      latency: Low
      cost: Low
      reliability: 90%
    - name: "Goose"
      score: 7/10
      latency: Medium
      cost: Low
      reliability: 85%
    - name: "Cline"
      score: 7/10
      latency: Medium
      cost: Medium
      reliability: 88%
```

---

## Master Capability List

### Code Operations

| Capability | Tools | Best Choice | Alternatives |
|------------|-------|-------------|--------------|
| Generate Code | OpenHands, Aider, Claude Code, Cline | OpenHands | Aider, Claude Code |
| Edit Code | Aider, Cline, VS Code AI | Aider | Cline, VS Code AI |
| Review Code | OpenHands, PR Review Agent, CodeRabbit | OpenHands | CodeRabbit |
| Understand Repository | Tree-sitter, AST analysis, SWE-Agent | SWE-Agent | Tree-sitter |
| Create Tests | Claude Test, RAGTest, OpenHands | Claude Test | OpenHands |
| Run Tests | Test runners, CI agents | CI agents | Direct runners |
| Fix Bugs | DebugAgent, OpenHands | OpenHands | DebugAgent |
| Refactor Code | Aider, Cline, OpenHands | Aider | OpenHands |

### Web & Browser Operations

| Capability | Tools | Best Choice | Alternatives |
|------------|-------|-------------|--------------|
| Use Browser | Browser Use, Claude Computer Use, Playwright Agent | Browser Use | Claude Computer Use |
| Search Docs | Search agents, RAG systems | RAG systems | Search agents |
| Read Database | Query agents, Database tools | Query agents | Direct queries |
| Execute Shell | CLI agents, Bash tools | CLI agents | Direct shell |

### Deployment & DevOps

| Capability | Tools | Best Choice | Alternatives |
|------------|-------|-------------|--------------|
| Deploy | OpenHands, GitHub Actions, Terraform Agent | OpenHands | GitHub Actions |
| Create PR | GitHub CLI, PR agents | PR agents | GitHub CLI |
| Merge PR | GitHub API, Merge bots | Merge bots | GitHub API |
| Read Issues | Issue trackers, API agents | API agents | Direct API |
| Manage Tasks | Task managers, Automation agents | Automation agents | Direct tools |

### Documentation & Communication

| Capability | Tools | Best Choice | Alternatives |
|------------|-------|-------------|--------------|
| Generate Docs | Doc generators, LLM-based | LLM-based | Template-based |
| Create Diagram | Mermaid agents, Diagram AI | Diagram AI | Mermaid agents |
| Analyze Logs | Log agents, SIEM tools | SIEM tools | Log agents |
| Monitor Production | APM tools, Agents | APM tools | Agents |

---

## Capability Selection Algorithm

```python
def select_best_tool(capability: str, context: dict) -> Tool:
    """
    Selection Criteria:
    1. Activity level (most active first)
    2. Documentation quality
    3. Usage metrics
    4. Extensibility
    5. Independence (no vendor lock)
    """
    candidates = capability_graph[capability].tools
    
    # Sort by composite score
    scored = [
        (tool, calculate_score(tool, context))
        for tool in candidates
    ]
    
    # Return best match
    return max(scored, key=lambda x: x[1])[0]
```

---

## Tool → Capability Mapping

| Tool | Capabilities |
|------|-------------|
| **OpenHands** | Generate Code, Edit Code, Review Code, Deploy, Create PR, Fix Bugs |
| **Aider** | Generate Code, Edit Code, Refactor |
| **Cline** | Generate Code, Edit Code, Use Terminal |
| **Claude Code** | Generate Code, Edit Code, Review Code, Use Browser |
| **Goose** | Generate Code, Edit Code, Use Terminal |
| **SWE-Agent** | Understand Repository, Debug, Fix Bugs |
| **Browser Use** | Use Browser, Search Web |
| **GitHub Copilot** | Generate Code, Edit Code |
| **Cursor** | Generate Code, Edit Code, Chat |
| **Devin** | Full Software Engineer capabilities |

---

## Capability Categories

```
Capabilities
  ├── Code Generation
  │     ├── Frontend
  │     ├── Backend
  │     ├── Scripts
  │     └── Tests
  │
  ├── Code Editing
  │     ├── Inline Edits
  │     ├── Refactoring
  │     └── Bug Fixes
  │
  ├── Code Understanding
  │     ├── Repository Analysis
  │     ├── Code Search
  │     └── Dependency Analysis
  │
  ├── Execution
  │     ├── Shell Commands
  │     ├── API Calls
  │     └── Browser Actions
  │
  ├── Collaboration
  │     ├── PR Creation
  │     ├── Code Review
  │     └── Issue Management
  │
  └── Deployment
        ├── CI/CD
        ├── Cloud Deploy
        └── Monitoring
```

---

## Adding New Capabilities

```
New capability request
        ↓
Check if capability exists in taxonomy
        ↓
No → Add to Capability Taxonomy
        ↓
Find all tools that provide this capability
        ↓
Create adapter for each tool
        ↓
Update Capability Graph
        ↓
Never modify Core Brain
```

---

## Query Examples

**Q: What tools can generate backend code?**
```
Answer: OpenHands, Aider, Claude Code, Goose, Cline
Best: OpenHands
Alternative 1: Aider
Alternative 2: Claude Code
```

**Q: Can I replace OpenHands with something else for code editing?**
```
Answer: Yes - Aider is the best alternative
Replacement Cost: Low
Adapter Available: Yes
```

**Q: What's the best tool for browser automation?**
```
Answer: Browser Use (primary), Claude Computer Use (alternative)
Selection Criteria: Activity, Documentation, Extensibility
```

---

## Related Documents

- [01-Architecture](./01-Architecture.md) - System Architecture
- [03-Capability-Taxonomy](./03-Capability-Taxonomy.md) - Full Taxonomy
- [05-Agent-Capability-Database](../03-Agents/02-Agent-Capability-Database.md) - Agent Database
