# Research Notes

> **Research findings, experiments, and learnings.**

---

## Core Research Questions

### Q1: What is the best architecture for an AI coding agent?

**Summary:** Single agent with tool orchestration beats multi-agent for simplicity.

**Key Findings:**
- Single decision point is easier to debug
- Tool abstraction enables flexibility
- Capability-based routing is future-proof

**Sources:**
- SWE-Agent paper
- LangChain documentation
- Anthropic's agent design

### Q2: How to integrate multiple agents?

**Summary:** Standardized adapter pattern with input/output schemas.

**Key Findings:**
- Each agent becomes a Tool
- Universal interface enables plug-and-play
- No core changes when adding agents

### Q3: What makes an agent production-ready?

**Summary:** Reliability, observability, and replaceability.

**Key Criteria:**
- 95%+ reliability
- Full logging/tracing
- Easy tool replacement
- Active maintenance
- Good documentation

---

## Technology Research

### LLM Routing

**Question:** Should we use LiteLLM or build custom?

**Research:**
```
LiteLLM Pros:
- Battle-tested
- 100+ models supported
- Easy to switch models
- Good documentation

LiteLLM Cons:
- Additional dependency
- Proxy overhead

Custom Pros:
- Full control
- No dependency

Custom Cons:
- More work
- Less tested

Decision: LiteLLM (Adopt)
```

### Vector Databases

**Question:** Which vector DB for production?

**Research:**
```
Criteria: Scalability, Cost, Self-hosting, Performance

Qdrant:
- ✅ High performance
- ✅ Self-hosted option
- ✅ Good Rust-based implementation
- ⚠️ Smaller community than Pinecone

Weaviate:
- ✅ Mature
- ✅ Good documentation
- ✅ Hybrid search
- ⚠️ Higher resource usage

Pinecone:
- ✅ Managed (no ops)
- ✅ Scalable
- ❌ Not self-hostable
- ⚠️ Vendor lock

Decision: Qdrant for self-hosted, Pinecone for managed
```

### Browser Automation

**Question:** Playwright vs Selenium vs Browser Use?

**Research:**
```
Playwright:
- ✅ Cross-browser
- ✅ Fast
- ✅ Active development
- ✅ Good API

Selenium:
- ✅ Widely used
- ❌ Slower
- ❌ Older architecture

Browser Use:
- ✅ AI-native
- ✅ Natural language control
- ⚠️ Newer project
- ⚠️ Less mature

Decision: Playwright as base, Browser Use as AI interface
```

---

## Experiment Log

### Experiment 001: Multi-Agent vs Single-Agent

**Date:** 2024-01-15
**Hypothesis:** Multi-agent is better for complex tasks
**Method:** Compare SWE-Agent vs OpenHands
**Result:** Single agent with tool routing performed better
**Learnings:** Inter-agent communication adds complexity without benefits

### Experiment 002: Tool Selection Algorithm

**Date:** 2024-02-01
**Hypothesis:** Capability-based selection outperforms rule-based
**Method:** A/B test different selection methods
**Result:** Capability-based with fallback chains is most resilient
**Learnings:** Always have fallbacks

### Experiment 003: Adapter Performance

**Date:** 2024-02-15
**Hypothesis:** Adapters add significant overhead
**Method:** Benchmark with and without adapters
**Result:** Overhead is <5% with proper async
**Learnings:** Adapters are worth the cost for flexibility

---

## Papers to Read

| Paper | Status | Key Takeaway |
|-------|--------|-------------|
| SWE-Agent | ✅ Read | Agent + Tools pattern |
| ReAct | ✅ Read | Reasoning + Acting |
| Reflexion | 🔄 Reading | Self-reflection improves performance |
| Toolformer | ✅ Read | LLM tool use |
| AutoGPT | 🔄 Reading | Autonomous agents |
| Voyager | 🔄 Reading | Lifelong learning agents |

---

## Questions to Explore

- [ ] How to measure agent reliability in production?
- [ ] What is the optimal context window for code tasks?
- [ ] How to handle tool failures gracefully?
- [ ] What is the best model for code generation?
- [ ] How to create a universal adapter standard?

---

## Related Documents

- [05-Technologies](./../05-Technologies/01-Technologies.md) - Technology Radar
- [10-Experiments](./../10-Experiments/README.md) - Experiment Results
