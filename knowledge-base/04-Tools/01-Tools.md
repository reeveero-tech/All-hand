# Tools Registry

> **Universal Tool Interface:** Every tool should be accessible through the same adapter pattern.

## Tool Schema

```yaml
Tool:
  ID: TOOL-XXX
  Name: ""
  Type: ""  # Agent, Utility, Framework
  Category: ""
  GitHub: ""
  Website: ""
  License: ""
  Stars: ""
  
  # Integration
  Adapter: ""  # Universal adapter name
  API: true/false
  CLI: true/false
  Docker: true/false
  SDK: true/false
  
  # Capabilities
  Capabilities: []
  
  # Quality
  Production Ready: true/false
  Documentation Score: 1-10
  Maintenance Status: ""  # Active, Stable, Deprecated
  
  # Risks
  Vendor Lock: true/false
  Single Point of Failure: true/false
  Replacement Cost: Low/Medium/High
```

---

## Tool Categories

### AI Agents (Code)

| Tool | Type | Stars | API | CLI | Docker | Production |
|------|------|-------|-----|-----|--------|------------|
| OpenHands | Agent | 15k+ | ✅ | ✅ | ✅ | ✅ |
| Claude Code | Agent | - | ❌ | ✅ | ❌ | ✅ |
| Cursor | IDE | - | ❌ | ❌ | ❌ | ✅ |
| Aider | CLI Agent | 10k+ | ⚠️ | ✅ | ✅ | ✅ |
| Cline | VS Code | 5k+ | ❌ | ❌ | ❌ | ✅ |
| SWE-Agent | Agent | 3k+ | ❌ | ✅ | ✅ | ✅ |
| Browser Use | Agent | 2k+ | ✅ | ❌ | ✅ | ✅ |
| Goose | CLI Agent | 2k+ | ❌ | ✅ | ❌ | ✅ |

### Frameworks

| Tool | Type | Stars | Use Case |
|------|------|-------|----------|
| LangChain | Framework | 40k+ | Agent orchestration |
| LiteLLM | Router | 8k+ | LLM proxy/routing |
| DSPy | Framework | 12k+ | Prompt optimization |
| Instructor | Library | 5k+ | Structured outputs |
| Outlines | Library | 4k+ | Structured outputs |

### Vector Databases

| Tool | Type | Stars | Cloud | Self-Host |
|------|------|-------|-------|-----------|
| Pinecone | Database | - | ✅ | ❌ |
| Weaviate | Database | 12k+ | ✅ | ✅ |
| Qdrant | Database | 15k+ | ✅ | ✅ |
| Chroma | Database | 8k+ | ❌ | ✅ |
| Milvus | Database | 20k+ | ✅ | ✅ |

### Browser Automation

| Tool | Type | Stars | AI-Native | Headless |
|------|------|-------|-----------|----------|
| Playwright | Library | 60k+ | ❌ | ✅ |
| Puppeteer | Library | 85k+ | ❌ | ✅ |
| Selenium | Library | 30k+ | ❌ | ✅ |
| Browser Use | Agent | 2k+ | ✅ | ✅ |
| DrissionPage | Library | 3k+ | ❌ | ✅ |

### Code Analysis

| Tool | Type | Stars | AST | Semantic |
|------|------|-------|-----|----------|
| Tree-sitter | Parser | 25k+ | ✅ | ❌ |
| LSP | Protocol | - | ✅ | ✅ |
| pyright | Analyzer | 10k+ | ✅ | ✅ |
| eslint | Linter | 25k+ | ✅ | ⚠️ |

---

## Tool Integration Matrix

| Tool | Universal Adapter | Input Schema | Output Schema | Executor |
|------|------------------|-------------|--------------|----------|
| OpenHands | ✅ | JSON | JSON | Sandboxed |
| Aider | ✅ | Chat | Diff | Shell |
| Cline | ✅ | VS Code Context | File Changes | VS Code API |
| Claude Code | ⚠️ | CLI Args | Files | Claude API |
| Browser Use | ✅ | URL + Task | Actions | Playwright |
| SWE-Agent | ✅ | GitHub Issue | PR | GitHub API |
| LiteLLM | ✅ | OpenAI-compatible | OpenAI-compatible | Proxy |
| LangChain | ✅ | LangChain Format | LangChain Format | Varies |

---

## Quality Scoring

| Tool | Reliability | Latency | Cost | Scalability | Overall |
|------|-------------|---------|------|-------------|---------|
| OpenHands | 95% | Medium | Medium | High | 9/10 |
| Aider | 90% | Low | Low | Medium | 8/10 |
| Claude Code | 98% | Low | High | Low | 8/10 |
| Browser Use | 90% | Medium | Medium | High | 8/10 |
| SWE-Agent | 85% | Medium | Low | Medium | 7/10 |
| LangChain | 80% | Medium | Medium | High | 7/10 |
| LiteLLM | 95% | Low | Low | High | 9/10 |

---

## Dependency Graph

```
OpenHands
  ├── LangChain (orchestration)
  ├── LiteLLM (LLM routing)
  ├── Playwright (browser)
  ├── Tree-sitter (code parsing)
  └── Docker SDK (container)

Aider
  ├── Core (no deps)
  └── Multiple LLM support

Browser Use
  ├── LangChain
  ├── Playwright
  └── OpenAI/Anthropic SDK

LiteLLM
  ├── OpenAI SDK
  ├── Anthropic SDK
  ├── Google AI SDK
  └── Redis (optional cache)
```

---

## Replacement Analysis

| Tool | Replacement | Difficulty | Alternative |
|------|-------------|------------|-------------|
| OpenHands | Aider | Easy | Claude Code |
| LangChain | LangGraph | Medium | Custom |
| LiteLLM | PortKey | Easy | Custom proxy |
| Pinecone | Qdrant | Medium | Weaviate |
| Playwright | DrissionPage | Medium | Selenium |
| Tree-sitter | AST parsing | Hard | Language Server |

---

## Related Documents

- [02-Architecture/Universal-Tool-Interface](./02-Universal-Tool-Interface.md) - Interface Spec
- [05-Technologies](./README.md) - Technology Radar
- [12-Risks](./README.md) - Risk Assessment
