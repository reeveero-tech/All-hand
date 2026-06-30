# ADR-028: Everything is a Resource

> **Architecture Decision Record**

---

## Status

**Proposed**: 2024-01-15  
**Status**: Accepted

---

## Context

System has many different types of entities:
- Missions
- Projects
- Capabilities
- Providers
- Workspaces
- Artifacts
- Knowledge
- Policies
- Events

Previous approach:
- Different models for each
- Inconsistent interfaces
- No unified management
- Complex dependency tracking

---

## Decision

> **Everything in the system is a Resource.**

```
Universal Resource Model:
- Mission = Resource
- Project = Resource
- Capability = Resource
- Provider = Resource
- Workspace = Resource
- Artifact = Resource
- Memory = Resource
- Knowledge = Resource
- Execution = Resource
- Model = Resource
- Repository = Resource
- Document = Resource
- Database = Resource
- User = Resource
- Organization = Resource
- Policy = Resource
- Secret = Resource
- Event = Resource
```

All share the same:
- Identification (RID)
- Versioning
- Permissions
- Dependencies
- Lifecycle
- Audit

---

## Consequences

### Positive

1. **Uniformity** - Same model for everything
2. **Simplicity** - One way to manage resources
3. **Consistency** - Consistent interfaces
4. **Graph** - Easy to build resource graph
5. **Policies** - Same policy model for all

### Negative

1. **Abstraction** - May seem generic
2. **Complexity** - Rich model needed

### Neutral

1. **Learning Curve** - Need to understand resource model

---

## Resource ID Format

```
rid = type:tenant:uuid:version

Examples:
- mission:tenant1:abc123:v1
- provider:global:xyz789:v2
- artifact:tenant2:def456:v3
```

---

## Related ADRs

| ADR | Title | Relationship |
|-----|-------|--------------|
| **ADR-028** | **Everything is Resource** | **This decision** |
| ADR-029 | No Single Point of Failure | Related |
| ADR-030 | Everything Versioned | Related |

---

## References

- [Universal-Resource-Model.md](../01-Resource-Model/01-Universal-Resource-Model.md)
