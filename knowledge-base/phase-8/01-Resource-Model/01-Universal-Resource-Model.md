# Universal Resource Model

> **Everything in the system is a Resource.**

---

## Concept

```
Everything is a Resource (ADR-028):

Mission          → Resource
Project          → Resource
Capability       → Resource
Provider         → Resource
Workspace        → Resource
Artifact         → Resource
Memory           → Resource
Knowledge        → Resource
Execution        → Resource
Model            → Resource
Repository       → Resource
Document         → Resource
Database         → Resource
User             → Resource
Organization      → Resource
Policy           → Resource
Secret           → Resource
Event            → Resource
```

---

## Resource Schema

```yaml
UniversalResource:
  # ========== Identification ==========
  id: string                    # RID (Resource ID)
  version: string               # Current version
  type: string                  # Resource type
  name: string                 # Human-readable name
  
  # ========== Metadata ==========
  metadata:
    created_at: datetime
    updated_at: datetime
    created_by: string
    updated_by: string
    tags: list[string]
    
  # ========== Ownership ==========
  ownership:
    owner_id: string           # User or Org ID
    owner_type: enum           # USER, ORGANIZATION
    tenant_id: string          # For multi-tenancy
    
  # ========== Permissions ==========
  permissions:
    owner: list[string]        # r, w, x, d, s, c, a, e
    group: list[string]
    others: list[string]
    
  # ========== Dependencies ==========
  dependencies:
    depends_on: list[RID]     # Resources this depends on
    depended_by: list[RID]    # Resources that depend on this
    
  # ========== State ==========
  state:
    status: enum              # ACTIVE, INACTIVE, DELETED, ARCHIVED
    health: enum              # HEALTHY, DEGRADED, UNHEALTHY
    lock: enum                # NONE, READ, WRITE
    
  # ========== Lifecycle ==========
  lifecycle:
    created: datetime
    updated: datetime
    deleted: datetime
    archived: datetime
    
  # ========== Audit ==========
  audit:
    version_history: list[Version]
    change_history: list[Change]
    created_at: datetime
    last_accessed: datetime
    
  # ========== Relationships ==========
  relationships:
    - type: string
      target_rid: string
      properties: dict
```

---

## Resource Types

```yaml
ResourceTypes:
  MISSION:
    description: "Top-level objective"
    properties:
      - goal: string
      - status: enum
      - progress: decimal
      
  PROJECT:
    description: "Collection of missions"
    properties:
      - domain: string
      - architecture: string
      
  CAPABILITY:
    description: "System capability"
    properties:
      - category: string
      - version: string
      
  PROVIDER:
    description: "External system provider"
    properties:
      - type: string
      - capabilities: list[string]
      
  WORKSPACE:
    description: "Isolated execution environment"
    properties:
      - resources: dict
      - isolation_level: string
      
  ARTIFACT:
    description: "Output from execution"
    properties:
      - type: string
      - size: integer
      - checksum: string
      
  MEMORY:
    description: "Mission memory"
    properties:
      - mission_id: string
      
  KNOWLEDGE:
    description: "Learned knowledge"
    properties:
      - type: string
      - confidence: decimal
      
  EXECUTION:
    description: "Execution instance"
    properties:
      - capability: string
      - status: enum
      
  MODEL:
    description: "AI model"
    properties:
      - provider: string
      - version: string
      
  POLICY:
    description: "System policy"
    properties:
      - type: string
      - rules: list[string]
      
  SECRET:
    description: "Credential or key"
    properties:
      - type: string
      - encrypted: boolean
      
  EVENT:
    description: "System event"
    properties:
      - type: string
      - timestamp: datetime
```

---

## Resource ID (RID)

```yaml
ResourceID:
  # Standard format: type:tenant:uuid:version
  
  examples:
    mission: "mission:tenant1:uuid123:v1"
    project: "project:tenant1:uuid456:v1"
    provider: "provider:global:uuid789:v2"
    workspace: "workspace:tenant1:uuid101:v1"
    
  components:
    type: string        # mission, project, provider, etc.
    tenant: string      # tenant identifier
    uuid: string        # unique identifier
    version: string     # version number
```

---

## Resource Operations

```yaml
ResourceOperations:
  CRUD:
    - create(resource)
    - read(rid)
    - update(rid, changes)
    - delete(rid)
    - archive(rid)
    
  Lifecycle:
    - clone(source_rid, target)
    - snapshot(rid)
    - restore(rid, snapshot)
    - migrate(rid, target_tenant)
    
  Relationships:
    - link(source, target, type)
    - unlink(source, target)
    - get_dependencies(rid)
    - get_dependents(rid)
    
  Access:
    - grant(rid, principal, permissions)
    - revoke(rid, principal)
    - check_permission(rid, principal, action)
```

---

## Resource Graph

```yaml
ResourceGraph:
  # All resources are connected
  
  edges:
    - from: "mission_001"
      to: "project_001"
      type: "CONTAINS"
      
    - from: "project_001"
      to: "capability_001"
      type: "USES"
      
    - from: "capability_001"
      to: "provider_001"
      type: "PROVIDED_BY"
      
    - from: "capability_001"
      to: "execution_001"
      type: "EXECUTED_AS"
      
    - from: "execution_001"
      to: "workspace_001"
      type: "RUNS_IN"
      
    - from: "execution_001"
      to: "artifact_001"
      type: "PRODUCES"
      
    - from: "execution_001"
      to: "knowledge_001"
      type: "GENERATES"
```

---

## Example: Resource in System

```yaml
Resource: Mission
RID: mission:tenant1:mq12345:v3

{
  id: "mission:tenant1:mq12345:v3",
  version: "3",
  type: "MISSION",
  name: "Build E-commerce Platform",
  
  ownership: {
    owner_id: "org:tenant1:org567",
    tenant_id: "tenant1"
  },
  
  permissions: {
    owner: ["r", "w", "x", "d", "s", "c", "a"],
    group: ["r", "x"],
    others: []
  },
  
  dependencies: {
    depends_on: [],
    depended_by: ["project:p1", "project:p2"]
  },
  
  state: {
    status: "ACTIVE",
    health: "HEALTHY"
  },
  
  lifecycle: {
    created: "2024-01-01T00:00:00Z",
    updated: "2024-01-15T10:30:00Z"
  },
  
  audit: {
    version_history: [
      {version: "1", timestamp: "2024-01-01"},
      {version: "2", timestamp: "2024-01-10"},
      {version: "3", timestamp: "2024-01-15"}
    ]
  }
}
```

---

## Related Documents

- [Resource-Graph.md](./02-Resource-Graph.md)
- [Organization-Engine.md](./03-Organization-Engine.md)
