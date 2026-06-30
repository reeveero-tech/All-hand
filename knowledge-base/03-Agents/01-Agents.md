# Agents Database

> **Core Principle:** All other agents are Tools, not executors or planners.

## Agent Registry

Every agent in our ecosystem is registered here as an **External Capability Provider**.

---

## Agent Schema

```yaml
Agent:
  Name: ""
  Repository: ""
  Input: []
  Output: []
  Strength: []
  Weakness: []
  Latency: ""  # Low, Medium, High
  Cost: ""  # Low, Medium, High
  Required Model: ""
  
  # Deployment Options
  Offline: true/false
  Cloud: true/false
  API: true/false
  CLI: true/false
  Docker: true/false
  SDK: true/false
  
  # Capabilities
  Can Execute Code: true/false
  Can Edit Code: true/false
  Can Browse: true/false
  Can Debug: true/false
  Can Test: true/false
  Can Review: true/false
  Can Search: true/false
  Can Deploy: true/false
  Can Refactor: true/false
  
  # Quality Metrics
  Reliability: 0-100%
  Replacement: ""  # Easy, Medium, Hard
```

---

## Major Agents

### OpenHands

```yaml
Name: OpenHands
Repository: https://github.com/All-Hands/AI-Hands
Website: https://app.all-hands.dev

Input: Natural language instructions, Repository context
Output: Code changes, PRs, Reports

Strength:
  - Open source with active community
  - Cloud-native architecture
  - Extensive plugin ecosystem
  - MCP support
  - Strong documentation

Weakness:
  - Complex initial setup
  - High resource consumption

Latency: Medium
Cost: Medium (cloud) / Low (self-hosted)
Required Model: GPT-4, Claude, or compatible

Offline: Yes
Cloud: Yes
API: Yes
CLI: Yes
Docker: Yes
SDK: Yes

Can Execute Code: Yes
Can Edit Code: Yes
Can Browse: Yes
Can Debug: Yes
Can Test: Yes
Can Review: Yes
Can Search: Yes
Can Deploy: Yes
Can Refactor: Yes

Reliability: 95%
Replacement: Easy
```

### Claude Code

```yaml
Name: Claude Code
Repository: https://claude.ai/code
Website: https://claude.ai/code

Input: Natural language instructions, File context
Output: Code changes, Explanations

Strength:
  - Strong reasoning capabilities
  - Excellent code quality
  - Deep context understanding
  - Computer use for browser automation

Weakness:
  - Closed source
  - Anthropic-only models
  - Limited extensibility

Latency: Low
Cost: High
Required Model: Claude (Anthropic)

Offline: No
Cloud: Yes
API: No (CLI only)
CLI: Yes
Docker: No
SDK: No

Can Execute Code: Yes
Can Edit Code: Yes
Can Browse: Yes
Can Debug: Yes
Can Test: Yes
Can Review: Yes
Can Search: Yes
Can Deploy: Limited
Can Refactor: Yes

Reliability: 98%
Replacement: Medium
```

### Cursor

```yaml
Name: Cursor
Repository: https://cursor.sh
Website: https://cursor.sh

Input: Chat, File context, Terminal commands
Output: Code changes, Suggestions

Strength:
  - Integrated IDE experience
  - Real-time code completion
  - Multiple AI models
  - Collaborative features

Weakness:
  - IDE-dependent
  - Desktop application only

Latency: Low
Cost: Medium
Required Model: Multiple (GPT-4, Claude, etc.)

Offline: Yes
Cloud: Partial
API: No
CLI: No
Docker: No
SDK: No

Can Execute Code: Yes
Can Edit Code: Yes
Can Browse: Limited
Can Debug: Limited
Can Test: Yes
Can Review: Yes
Can Search: Yes
Can Deploy: No
Can Refactor: Yes

Reliability: 92%
Replacement: Medium
```

### Aider

```yaml
Name: Aider
Repository: https://github.com/paul-gauthier/aider
Website: https://aider.chat

Input: Chat with git repo, Shell commands
Output: Code changes, Git commits

Strength:
  - Git-native workflow
  - Excellent CLI experience
  - Multiple model support
  - Minimal dependencies
  - Strong for refactoring

Weakness:
  - No browser capabilities
  - Desktop-required workflow

Latency: Low
Cost: Low (self-hosted)
Required Model: Multiple LLMs

Offline: Yes
Cloud: No
API: No
CLI: Yes
Docker: Yes
SDK: Python API

Can Execute Code: Yes
Can Edit Code: Yes
Can Browse: No
Can Debug: Yes
Can Test: Yes
Can Review: Yes
Can Search: Yes
Can Deploy: No
Can Refactor: Excellent

Reliability: 90%
Replacement: Easy
```

### Cline

```yaml
Name: Cline
Repository: https://github.com/cline/cline
Website: https://marketplace.visualstudio.com/items?itemName=cursorchen24.cline

Input: VS Code context, Natural language
Output: Code changes, File modifications

Strength:
  - VS Code integration
  - Multi-model support
  - File system access
  - Terminal commands

Weakness:
  - VS Code dependent
  - Limited browser capabilities

Latency: Low
Cost: Medium
Required Model: Multiple

Offline: Yes
Cloud: No
API: No
CLI: No
Docker: No
SDK: No

Can Execute Code: Yes
Can Edit Code: Yes
Can Browse: Limited
Can Debug: Yes
Can Test: Yes
Can Review: Yes
Can Search: Yes
Can Deploy: No
Can Refactor: Yes

Reliability: 88%
Replacement: Easy
```

### GitHub Copilot

```yaml
Name: GitHub Copilot
Repository: (Proprietary)
Website: https://github.com/features/copilot

Input: IDE context, Comments
Output: Code suggestions, Completions

Strength:
  - Deep IDE integration
  - Real-time suggestions
  - Wide language support
  - Microsoft backing

Weakness:
  - Closed source
  - Limited customization
  - No agent mode (historically)

Latency: Very Low
Cost: Medium
Required Model: (Proprietary)

Offline: Yes
Cloud: Yes
API: Limited
CLI: No
Docker: No
SDK: No

Can Execute Code: Suggestions only
Can Edit Code: Suggestions only
Can Browse: No
Can Debug: No
Can Test: No
Can Review: No
Can Search: No
Can Deploy: No
Can Refactor: Suggestions only

Reliability: 95%
Replacement: Medium
```

### SWE-Agent

```yaml
Name: SWE-Agent
Repository: https://github.com/princeton-nlp/SWE-agent
Website: https://swe-agent.com

Input: GitHub Issue, Repository
Output: Fix PR, Explanation

Strength:
  - SWE-bench optimized
  - Open source
  - Research-backed
  - Specializes in bug fixes

Weakness:
  - Narrow use case
  - GitHub-specific workflow

Latency: Medium
Cost: Low
Required Model: GPT-4, Claude

Offline: Yes
Cloud: No
API: No
CLI: Yes
Docker: Yes
SDK: No

Can Execute Code: Yes
Can Edit Code: Yes
Can Browse: Yes
Can Debug: Excellent
Can Test: Yes
Can Review: Limited
Can Search: Yes
Can Deploy: No
Can Refactor: Yes

Reliability: 85%
Replacement: Medium
```

### Browser Use

```yaml
Name: Browser Use
Repository: https://github.com/browser-use/browser-use
Website: https://browser-use.com

Input: Natural language, URL
Output: Actions, Data extraction

Strength:
  - AI-native browser automation
  - LangChain integration
  - Open source
  - Computer use capability

Weakness:
  - Single-purpose
  - No code editing directly

Latency: Medium
Cost: Medium
Required Model: GPT-4, Claude

Offline: No
Cloud: Yes
API: Yes
CLI: No
Docker: Yes
SDK: Python

Can Execute Code: No
Can Edit Code: No
Can Browse: Excellent
Can Debug: No
Can Test: No
Can Review: No
Can Search: Yes
Can Deploy: No
Can Refactor: No

Reliability: 90%
Replacement: Easy
```

---

## Agent Comparison Matrix

| Agent | Code Gen | Code Edit | Browser | Deploy | API | CLI | Docker | Self-Host |
|-------|----------|-----------|---------|--------|-----|-----|--------|-----------|
| OpenHands | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Claude Code | ✅ | ✅ | ✅ | Limited | No | Yes | No | No |
| Cursor | ✅ | ✅ | Limited | No | No | No | No | Partial |
| Aider | ✅ | ✅ | No | No | No | Yes | Yes | Yes |
| Cline | ✅ | ✅ | Limited | No | No | No | No | Yes |
| Copilot | ✅ | Suggestions | No | No | No | No | No | No |
| SWE-Agent | ✅ | ✅ | ✅ | No | No | Yes | Yes | Yes |
| Browser Use | No | No | ✅ | No | Yes | No | Yes | No |

---

## Capability Coverage

| Capability | Best Agent | Alternative 1 | Alternative 2 |
|------------|------------|---------------|--------------|
| Generate Backend | OpenHands | Aider | Claude Code |
| Generate Frontend | OpenHands | Claude Code | Cursor |
| Edit Code | Aider | Cline | OpenHands |
| Review Code | OpenHands | Claude Code | PR Review Agent |
| Use Browser | Browser Use | Claude Computer Use | Playwright Agent |
| Understand Repo | SWE-Agent | OpenHands | Tree-sitter |
| Fix Bugs | OpenHands | SWE-Agent | Claude Code |
| Deploy | OpenHands | GitHub Actions | Terraform Agent |

---

## Related Documents

- [02-Agent-Capability-Database](./02-Agent-Capability-Database.md) - Detailed Capability Database
- [11-Competitors](../11-Competitors/README.md) - Competitor Analysis
- [02-Architecture/Capability-Graph](../02-Architecture/02-Capability-Graph.md) - Capability Graph
