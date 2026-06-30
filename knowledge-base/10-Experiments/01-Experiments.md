# Experiments Log

> **All experiments, results, and learnings.**

---

## Experiment Template

```yaml
Experiment:
  ID: EXP-XXX
  Title: ""
  Date: YYYY-MM-DD
  Hypothesis: ""
  Method: ""
  Result: ""  # Success/Failure/Partial
  Learnings: []
  Status: Proposed/Running/Completed/Archived
```

---

## Completed Experiments

### EXP-001: Single Agent vs Multi-Agent

| Field | Value |
|-------|-------|
| **Date** | 2024-01-15 |
| **Hypothesis** | Multi-agent is better for complex tasks |
| **Method** | Compare SWE-Agent (single) vs multi-agent orchestration |
| **Result** | ❌ Single agent performed better |
| **Learnings** | Inter-agent communication adds complexity; simpler is better |

### EXP-002: Tool Selection Methods

| Field | Value |
|-------|-------|
| **Date** | 2024-02-01 |
| **Hypothesis** | Capability-based selection outperforms rule-based |
| **Method** | A/B test with real workloads |
| **Result** | ✅ Capability-based with fallbacks is most resilient |
| **Learnings** | Always have fallback chains |

### EXP-003: Adapter Overhead

| Field | Value |
|-------|-------|
| **Date** | 2024-02-15 |
| **Hypothesis** | Adapters add significant latency |
| **Method** | Benchmark with/without adapters |
| **Result** | ✅ Overhead is <5% with async |
| **Learnings** | Adapters worth the cost for flexibility |

### EXP-004: Context Window Size

| Field | Value |
|-------|-------|
| **Date** | 2024-03-01 |
| **Hypothesis** | Larger context = better code understanding |
| **Method** | Test with 4K, 32K, 128K contexts |
| **Result** | ⚠️ Diminishing returns after 32K |
| **Learnings** | 32K is sweet spot for cost/quality |

### EXP-005: Model Comparison for Code

| Field | Value |
|-------|-------|
| **Date** | 2024-03-15 |
| **Hypothesis** | Claude outperforms GPT-4 for code |
| **Method** | Compare on SWE-bench |
| **Result** | ⚠️ Both perform well, different strengths |
| **Learnings** | Use both for different tasks |

---

## Running Experiments

### EXP-006: Universal Adapter Standard

| Field | Value |
|-------|-------|
| **Hypothesis** | Standard adapter format enables any-to-any integration |
| **Method** | Implement 5 adapters, test interoperability |
| **Status** | 🔄 Running |

---

## Archived Experiments

| ID | Title | Reason Archived |
|----|-------|----------------|
| EXP-OLD-001 | AutoGPT for code | Not production-ready |
| EXP-OLD-002 | LangChain as core | Too complex |
| EXP-OLD-003 | Direct tool execution | Security concerns |

---

## Experiment Index

| ID | Title | Status | Result |
|----|-------|--------|--------|
| EXP-001 | Single vs Multi Agent | ✅ Complete | Negative |
| EXP-002 | Tool Selection Methods | ✅ Complete | Positive |
| EXP-003 | Adapter Overhead | ✅ Complete | Positive |
| EXP-004 | Context Window Size | ✅ Complete | Partial |
| EXP-005 | Model Comparison | ✅ Complete | Partial |
| EXP-006 | Universal Adapter | 🔄 Running | - |

---

## Related Documents

- [06-Research/Research.md](../06-Research/01-Research.md) - Research Notes
- [05-Technologies/Technologies.md](../05-Technologies/01-Technologies.md) - Technology Radar
