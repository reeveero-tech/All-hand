# Glossary

## Core Terms

| Term | Definition |
|------|------------|
| **Core Brain** | The single decision-making entity that orchestrates all capabilities |
| **Capability Engine** | Routes requests to appropriate tools based on capabilities |
| **Universal Tool Adapter** | Standardized interface for all external agents/tools |
| **Plugin** | Any component outside Core Brain, Capability Engine, and Universal Tool Adapter |
| **Tool** | An external agent converted to work through the Universal Tool Adapter |

---

## Architecture Terms

| Term | Definition |
|------|------------|
| **Capability** | A specific function or action a tool can perform |
| **Capability Graph** | Mapping of capabilities to tools that provide them |
| **Capability Taxonomy** | Hierarchical classification of all capabilities |
| **Adapter** | Bridge between a tool and the Universal Tool Interface |
| **Input Schema** | Validated input format for a tool |
| **Output Schema** | Validated output format from a tool |
| **Executor** | Sandboxed execution environment for a tool |

---

## Agent Terms

| Term | Definition |
|------|------------|
| **Agent** | An AI system that can perform tasks autonomously |
| **External Capability Provider** | An agent used as a tool, not as a peer |
| **SWE-Agent** | Software Engineering Agent (specialized for bug fixes) |
| **Claude Code** | Anthropic's CLI coding tool |
| **Computer Use** | Anthropic's technology for AI-controlled computers |

---

## Tool Terms

| Term | Definition |
|------|------------|
| **LLM Router** | System that routes requests between different LLM providers |
| **Vector Database** | Database optimized for similarity search |
| **MCP (Model Context Protocol)** | Anthropic's protocol for agent-tool communication |
| **Playwright** | Cross-browser automation library |

---

## Development Terms

| Term | Definition |
|------|------------|
| **ADR (Architecture Decision Record)** | Document explaining an architectural decision |
| **Technology Radar** | Classification of technologies into Adopt/Trial/Assess/Hold/Avoid |
| **SPOF (Single Point of Failure)** | Component whose failure causes system failure |
| **Vendor Lock** | Dependency on a single vendor that is hard to escape |

---

## Selection Criteria

| Criterion | Definition |
|-----------|------------|
| **Activity** | How actively maintained the project is |
| **Documentation** | Quality and completeness of docs |
| **Usage** | Number of users and adoption |
| **Extensibility** | How easy to extend or customize |
| **Independence** | Freedom from vendor lock |

---

## Project Categories

| Category | Examples |
|----------|----------|
| **Foundation Models** | GPT-4, Claude, Gemini, Llama |
| **Code Generation** | OpenHands, Aider, Claude Code |
| **Code Editing** | Aider, Cline, VS Code AI |
| **Browser Automation** | Browser Use, Playwright Agent |
| **Vector Database** | Qdrant, Weaviate, Pinecone |
| **LLM Router** | LiteLLM, PortKey |

---

## Phase Terms

| Phase | Description |
|-------|-------------|
| **Phase 0** | Strategic Research & Architecture Foundation (0% → 10%) |
| **Phase 1** | Core Implementation (10% → 20%) |
| **Phase 2** | Integration & Testing (20% → 40%) |
| **Phase 3** | Production Readiness (40% → 60%) |
| **Phase 4** | Ecosystem Expansion (60% → 80%) |
| **Phase 5** | Optimization & Polish (80% → 100%) |

---

## Related Documents

- [02-Architecture](../02-Architecture/01-Architecture.md) - Architecture
- [07-Decisions](../07-Decisions/01-Decisions.md) - ADRs
