# Ecosystem Overview

## AI Coding Agents Landscape

### Core Philosophy
> **Our Project's Core Principle:** All other agents are tools (Tools), not executors or planners.

```
Our Core Agent
     ↓
All External Agents → External Capability Providers
     ↓
OpenHands → Tool
     ↓
Cline → Tool
     ↓
Aider → Tool
     ↓
SWE Agent → Tool
     ↓
Goose → Tool
     ↓
Browser Use → Tool
```

### Agent Categories in Ecosystem

| Category | Description | Examples |
|----------|-------------|----------|
| **Foundation Models** | LLMs that power agents | GPT-4, Claude, Gemini, Llama |
| **Inference Providers** | API services for model access | OpenAI, Anthropic, Google AI |
| **LLM Routers** | Intelligent routing between models | LiteLLM, PortKey |
| **Prompt Engineering** | Framework for prompt optimization | DSPy, Promptify |
| **Structured Output** | JSON/schemed outputs from LLMs | Instructor, Outlines |
| **Memory** | Persistence and context management | Mem0, Letta |
| **Knowledge Graph** | Structured knowledge representation | Neo4j, Amazon Neptune |
| **Vector Databases** | Semantic search and embedding storage | Pinecone, Weaviate, Qdrant |
| **Planning** | Task decomposition and planning | LangChain, AutoGPT |
| **Reflection** | Self-evaluation and improvement | Reflexion |
| **Reasoning** | Chain-of-thought and reasoning | o1, DeepSeek |
| **Tool Calling** | Function calling and tool integration | Toolformer, OpenAI Functions |
| **MCP** | Model Context Protocol | Anthropic MCP |
| **Browser** | Web interaction capabilities | Playwright, Puppeteer |
| **Computer Use** | OS-level interaction | Claude Computer Use |
| **Sandbox** | Isolated execution environments | Docker, gVisor |
| **Repository Analysis** | Codebase understanding | Tree-sitter, AST analysis |
| **Code Generation** | Writing new code | CodeGen, StarCoder |
| **Code Editing** | Modifying existing code | Aider, Cline |
| **Testing** | Test generation and execution | Claude Test, RAGTest |
| **Debugging** | Bug detection and fixing | DebugAgent |
| **Code Review** | PR and code analysis | PR Review agents |
| **Deployment** | CI/CD and cloud deployment | DeployBot, Flux |

---

## Universal Tool Interface Concept

Every agent in the world should transform to the same shape:

```
Tool
  ↓
Input Schema
  ↓
Executor
  ↓
Output Schema
```

**We don't deal with projects directly. We deal with Adapters.**

---

## Ecosystem Statistics

| Metric | Count |
|--------|-------|
| Total AI Coding Agents | 1000+ |
| Major Competitors | 15+ |
| Core Capabilities | 50+ |
| Integration Adapters | 100+ |

---

## Key Ecosystem Trends

1. **Multi-Agent Orchestration** - Systems using multiple specialized agents
2. **Universal Tool Interface** - Standardized agent integration
3. **Cloud-First Architecture** - SaaS and API-first design
4. **Capability-Based Selection** - Choosing tools by capability, not name
5. **Replaceability** - Designing for easy tool replacement

---

## Next Sections

- [02-Architecture](./02-Architecture/README.md) - System Architecture
- [03-Agents](./03-Agents/README.md) - Agent Database
- [04-Tools](./04-Tools/README.md) - Tool Registry
- [11-Competitors](./11-Competitors/README.md) - Competitor Analysis
