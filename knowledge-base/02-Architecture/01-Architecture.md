# System Architecture

## Core Philosophy

```
The System consists of only three elements:

┌─────────────────────────────────────────────────────────────┐
│                         Core Brain                          │
│                    (The only "brain")                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Capability Engine                       │
│                (Routes requests to tools)                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Universal Tool Adapter                     │
│              (Standardizes all external agents)             │
└─────────────────────────────────────────────────────────────┘

Anything else = Plugin
```

---

## Architecture Constraints

> **Rules that cannot be broken:**

| Constraint | Description |
|------------|-------------|
| **No Vendor Lock** | Never depend exclusively on one provider |
| **No Local Dependency** | No hard-coded local-only dependencies |
| **Cloud Native** | Design for cloud deployment from day one |
| **Everything API** | All functionality accessible via API |
| **Everything Replaceable** | Any component can be swapped |
| **Everything Observable** | Full observability and logging |
| **Everything Versioned** | Version control for all components |
| **Everything Testable** | Testable at unit, integration, and E2E levels |
| **Everything Documented** | Complete documentation required |

---

## Layer Descriptions

### Layer 1: Core Brain
- **Purpose:** Single decision-making entity
- **Responsibilities:**
  - User intent understanding
  - Task decomposition
  - Capability routing
  - Result synthesis
- **Constraints:**
  - No direct tool execution
  - All execution via Capability Engine

### Layer 2: Capability Engine
- **Purpose:** Intelligent routing based on capabilities
- **Responsibilities:**
  - Capability matching
  - Tool selection
  - Load balancing
  - Fallback management
- **Selection Criteria:**
  1. Activity level (most active first)
  2. Documentation quality
  3. Usage metrics
  4. Extensibility
  5. Independence

### Layer 3: Universal Tool Adapter
- **Purpose:** Standardize all external agents
- **Interface:**
  ```
  Tool
    ↓
  Input Schema (validated)
    ↓
  Executor (isolated)
    ↓
  Output Schema (validated)
  ```
- **Benefits:**
  - Plug-and-play tools
  - Easy replacement
  - Consistent interface
  - No core changes for new tools

---

## Decision Rules

### Rule: Choosing Between Competing Tools

When two or more tools perform the same function:

```
1. Most Active
   ↓
2. Most Documented
   ↓
3. Most Used
   ↓
4. Most Extensible
   ↓
5. Most Independent
   ↓
Winner
```

### Rule: Adding New Capabilities

```
Request comes in
    ↓
Does capability exist in Capability Graph?
    ↓
Yes → Select best tool from Capability Graph
    ↓
No → Evaluate and add new capability
    ↓
Create Universal Tool Adapter
    ↓
Update Capability Graph
    ↓
Never modify Core Brain
```

---

## Plugin System

Everything outside Core Brain, Capability Engine, and Universal Tool Adapter is a **Plugin**.

| Plugin Type | Examples |
|-------------|---------|
| Tool Plugins | OpenHands, Cline, Aider adapters |
| Storage Plugins | PostgreSQL, Redis, S3 |
| Auth Plugins | OAuth, SAML, API keys |
| Observability Plugins | Datadog, Prometheus, Jaeger |
| Deployment Plugins | Kubernetes, AWS, GCP |

---

## Scalability Design

```
                    ┌─────────────────┐
                    │   Load Balancer │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
       ┌──────────┐   ┌──────────┐   ┌──────────┐
       │ Instance │   │ Instance │   │ Instance │
       │    1     │   │    2     │   │    N     │
       └──────────┘   └──────────┘   └──────────┘
              │              │              │
              └──────────────┼──────────────┘
                             ▼
                    ┌─────────────────┐
                    │ Capability Graph│
                    │     (State)      │
                    └─────────────────┘
```

---

## Replaceability Matrix

| Component | Replaceable? | How? |
|-----------|--------------|------|
| Core Brain | ❌ | Redesign required |
| Capability Engine | ✅ | Plugin swap |
| Universal Tool Adapter | ✅ | Adapter replacement |
| Any Tool | ✅ | New adapter |
| Storage | ✅ | Plugin swap |
| Auth | ✅ | Plugin swap |

---

## Key Principle: Exit Criteria

> **We don't move to next phase until we can answer:**
>
> - ✅ What is the best tool for each capability?
> - ✅ What is the second and third alternative?
> - ✅ How to replace any tool without changing the Core?
> - ✅ What is the cost of each tool?
> - ✅ What is the quality of each tool?
> - ✅ Does it work in the cloud?
> - ✅ Does it have API or CLI?
> - ✅ Is it production-ready?
> - ✅ Can it be integrated via Universal Tool Adapter?
> - ✅ Is there any engineering reason to reject it?

---

## Related Documents

- [03-Agents](../03-Agents/README.md) - Agent Database
- [04-Tools](../04-Tools/README.md) - Tool Registry
- [05-Capability-Graph](./02-Capability-Graph.md) - Capability Mapping
- [06-Capability-Taxonomy](./03-Capability-Taxonomy.md) - Capability Taxonomy
