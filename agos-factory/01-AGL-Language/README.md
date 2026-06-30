# AGOS Language (AGL) v1.0

> **The Simple DSL for Defining AGOS Objects**

---

## Overview

```
AGL = AGOS Language

Purpose:
  Define objects once, generate everything

Syntax:
  Simple, readable, concise

Example:
  object Mission {
    id: UUID
    name: String
    state: Enum [ACTIVE, INACTIVE]
  }
```

---

## Why AGL?

```
WITHOUT AGL:
─────────────────────────────────────────────
1. Write JSON Schema (100 lines)
2. Write TypeScript interface (50 lines)
3. Write Python model (80 lines)
4. Write Go struct (60 lines)
5. Write tests (100 lines)
6. Write docs (50 lines)

Total: 440 lines of manual work
Problem: 3 hours
Risk: Inconsistency between files

WITH AGL:
─────────────────────────────────────────────
1. Write AGL definition (20 lines)

Generated automatically:
  ✓ JSON Schema
  ✓ TypeScript interface
  ✓ Python model
  ✓ Go struct
  ✓ Tests
  ✓ Docs

Total: 20 lines of work
Time: 10 minutes
Risk: Zero inconsistency
```

---

## Language Specification

### File Extension

```
.agl
```

### File Structure

```agl
// Mission.agl

// Comment
object Mission {
    // Field definition
    id: UUID
    name: String
    
    // Enum
    type: Enum [BUILD, FIX, REFACTOR]
    
    // Relationships
    tasks: Task[]
    parent: Goal?
    
    // Validation
    validate {
        name: required, min(3), max(100)
    }
    
    // Events
    events {
        created: MissionCreated
        completed: MissionCompleted
    }
    
    // Permissions
    permissions {
        read: [owner, admin]
        write: [owner]
    }
    
    // Version
    version "1.0.0"
}
```

---

## Type System

### Primitive Types

```agl
// String
name: String
description: String

// Numeric
count: Int
price: Decimal
percentage: Float

// Boolean
active: Boolean

// UUID
id: UUID

// DateTime
created_at: DateTime
updated_at: DateTime

// Binary
data: Binary

// JSON
metadata: JSON
```

### Complex Types

```agl
// Enum
status: Enum [ACTIVE, INACTIVE, PENDING]

// Optional
name: String?
description: String?

// Array
tags: String[]
tasks: Task[]

// Map
labels: Map<String, String>
config: Map<String, Any>

// Object reference
parent: Goal
related: Mission[]
```

### Range Constraints

```agl
// Number ranges
progress: Decimal [0.0, 1.0]
age: Int [0, 150]
percentage: Float [0.0, 100.0]

// String lengths
name: String [3, 200]
code: String [1, 10]
description: String [0, 10000]
```

---

## Field Modifiers

### Required/Optional

```agl
// Required (default)
name: String

// Optional
description: String?

// Required with default
status: Enum [ACTIVE] = ACTIVE
count: Int = 0
```

### Unique

```agl
// Unique field
email: String {unique}
id: UUID {unique}

// Unique combination
user_id: UUID
org_id: UUID {unique_with: [user_id]}
```

### Immutable

```agl
// Cannot be changed after creation
id: UUID {immutable}
created_at: DateTime {immutable}
```

---

## Validation Rules

```agl
validate {
    // Basic
    field: required
    field: optional
    
    // String
    field: min(3)
    field: max(100)
    field: pattern("[a-z]+")
    
    // Number
    field: min(0)
    field: max(100)
    field: range(0, 100)
    
    // Multiple
    field: required, min(3), max(100)
    
    // Custom
    field: custom(validate_email)
}
```

---

## Relationships

### Parent Reference

```agl
// Parent object
parent: Goal

// Optional parent
parent: Goal?

// Multiple parents
parents: Goal[]
```

### Children Reference

```agl
// Children are defined in child object
// Mission has Tasks
// Task references Mission

object Mission {
    id: UUID
}

object Task {
    id: UUID
    mission: Mission  // References Mission
    mission_id: UUID // Foreign key
}
```

---

## State Machine

```agl
state_machine MissionState {
    initial: DISCOVERY_EXPLORING
    
    // State transitions
    DISCOVERY_EXPLORING -> DISCOVERY_COMPLETED
    DISCOVERY_EXPLORING -> FAILED
    
    DISCOVERY_COMPLETED -> ARCHITECTURE_DESIGNING
    
    ARCHITECTURE_DESIGNING -> ARCHITECTURE_REVIEWING
    ARCHITECTURE_DESIGNING -> FAILED
    
    ARCHITECTURE_REVIEWING -> ARCHITECTURE_APPROVED
    ARCHITECTURE_REVIEWING -> ARCHITECTURE_DESIGNING
    
    // Conditional transitions
    ARCHITECTURE_APPROVED -> PLANNING_PLANNING
    PLANNING_PLANNING -> PLANNING_APPROVED | FAILED
    
    PLANNING_APPROVED -> EXECUTION_QUEUED
    EXECUTION_QUEUED -> EXECUTION_RUNNING | EXECUTION_FAILED
    EXECUTION_RUNNING -> EXECUTION_COMPLETED | EXECUTION_PAUSED | EXECUTION_FAILED
    
    EXECUTION_COMPLETED -> VERIFICATION_TESTING
    VERIFICATION_TESTING -> VERIFICATION_PASSED | VERIFICATION_FAILED
    
    VERIFICATION_PASSED -> DEPLOYMENT_DEPLOYING
    DEPLOYMENT_DEPLOYING -> DEPLOYMENT_DEPLOYED | DEPLOYMENT_ROLLED_BACK
    
    DEPLOYMENT_DEPLOYED -> MONITORING_ACTIVE
}
```

### State Machine Syntax

```agl
state_machine <Name> {
    initial: <InitialState>
    
    <State> -> <NextState>
    <State> -> <StateA> | <StateB>
    <State> -> <NextState> [guard_condition]
}
```

---

## Events

### Event Definition

```agl
events {
    created: ObjectCreated
    updated: ObjectUpdated
    deleted: ObjectDeleted
    state_changed: StateChanged
    completed: ObjectCompleted
    failed: ObjectFailed
}
```

### Event Payload

```agl
events {
    // With payload
    created: MissionCreated {
        mission_id: UUID
        name: String
        created_by: UUID
        timestamp: DateTime
    }
    
    state_changed: MissionStateChanged {
        mission_id: UUID
        from_state: MissionState
        to_state: MissionState
        changed_by: UUID
        timestamp: DateTime
    }
}
```

---

## Permissions

### Permission Definition

```agl
permissions {
    // Roles
    read: [owner, admin, viewer]
    write: [owner, admin]
    delete: [admin]
    
    // Field-level
    read: [all]
    write: {
        status: [admin]
        name: [owner, admin]
        description: [owner, admin, editor]
    }
}
```

### Permission Syntax

```agl
permissions {
    <action>: [<role1>, <role2>]
    
    // Or with field-level
    <action>: {
        <field>: [<role1>]
        <field>: [<role2>]
    }
}
```

---

## Versioning

### Version Declaration

```agl
object Mission {
    // ... fields ...
    version "1.0.0"
}

object Mission {
    // ... modified fields ...
    version "1.1.0"  // Minor change
}

object Mission {
    // ... modified fields ...
    version "2.0.0"  // Breaking change
}
```

### Version History

```agl
// In version history file
versions:
  v1.0.0:
    date: "2024-01-01"
    changes: "Initial version"
    
  v1.1.0:
    date: "2024-02-01"
    changes:
      - "Added priority field"
      - "Added tags field"
```

---

## Includes

### Include Other Files

```agl
// Base.agl
object Base {
    id: UUID
    created_at: DateTime
    updated_at: DateTime
    owner_id: UUID
    tenant_id: UUID
    status: Enum [ACTIVE, INACTIVE]
}

// Mission.agl
include "Base.agl"

object Mission extends Base {
    name: String
    description: String
}
```

---

## Annotations

### Custom Metadata

```agl
object Mission {
    // Documentation
    @doc("The mission object represents a work unit")
    @doc("Missions are created from goals")
    name: String
    
    // Examples
    @example("Build authentication system")
    name: String
    
    // Deprecation
    @deprecated("Use 'description' instead")
    note: String?
    
    // Custom
    @feature("v2.0")
    priority: Int
}
```

---

## Complete Example

```agl
// Mission.agl
// Represents a mission that achieves part of a goal

object Mission {
    // Inherited from Base
    id: UUID
    created_at: DateTime
    updated_at: DateTime
    owner_id: UUID
    tenant_id: UUID
    status: Enum [ACTIVE, INACTIVE]
    
    // Mission-specific fields
    name: String [3, 200]
    description: String [0, 10000]
    goal_id: UUID
    mission_type: Enum [DISCOVERY, ARCHITECTURE, IMPLEMENTATION, TESTING, DEPLOYMENT]
    priority: Enum [CRITICAL, HIGH, MEDIUM, LOW] = MEDIUM
    
    // Progress
    progress: Decimal [0.0, 1.0] = 0.0
    
    // Relationships
    tasks: Task[]
    artifacts: Artifact[]
    
    // Validation
    validate {
        name: required, min(3), max(200)
        description: max(10000)
        goal_id: required
        mission_type: required
        progress: range(0.0, 1.0)
    }
    
    // State machine
    state_machine MissionState {
        initial: DISCOVERY_EXPLORING
        
        DISCOVERY_EXPLORING -> DISCOVERY_COMPLETED, FAILED
        DISCOVERY_COMPLETED -> ARCHITECTURE_DESIGNING
        ARCHITECTURE_DESIGNING -> ARCHITECTURE_REVIEWING, FAILED
        ARCHITECTURE_REVIEWING -> ARCHITECTURE_APPROVED, ARCHITECTURE_DESIGNING
        ARCHITECTURE_APPROVED -> PLANNING_PLANNING
        PLANNING_PLANNING -> PLANNING_APPROVED, FAILED
        PLANNING_APPROVED -> EXECUTION_QUEUED
        EXECUTION_QUEUED -> EXECUTION_RUNNING, EXECUTION_FAILED
        EXECUTION_RUNNING -> EXECUTION_COMPLETED, EXECUTION_PAUSED, EXECUTION_FAILED
        EXECUTION_COMPLETED -> VERIFICATION_TESTING
        VERIFICATION_TESTING -> VERIFICATION_PASSED, VERIFICATION_FAILED
        VERIFICATION_PASSED -> DEPLOYMENT_DEPLOYING
        DEPLOYMENT_DEPLOYING -> DEPLOYMENT_DEPLOYED, DEPLOYMENT_ROLLED_BACK
        DEPLOYMENT_DEPLOYED -> MONITORING_ACTIVE
    }
    
    // Events
    events {
        created: MissionCreated {
            mission_id: UUID
            name: String
            goal_id: UUID
            created_by: UUID
            timestamp: DateTime
        }
        
        state_changed: MissionStateChanged {
            mission_id: UUID
            from_state: MissionState
            to_state: MissionState
            changed_by: UUID
            timestamp: DateTime
        }
        
        completed: MissionCompleted {
            mission_id: UUID
            duration: Duration
            artifacts_count: Int
            completed_by: UUID
            timestamp: DateTime
        }
        
        failed: MissionFailed {
            mission_id: UUID
            reason: String
            failed_by: UUID
            timestamp: DateTime
        }
    }
    
    // Permissions
    permissions {
        read: [owner, tenant_admin, system]
        write: [owner, tenant_admin]
        delete: [tenant_admin]
    }
    
    // Documentation
    @doc("Represents a mission that achieves part of a goal")
    @doc("Missions have a lifecycle from discovery to monitoring")
    @example("Build the authentication service")
    
    // Version
    version "1.0.0"
}
```

---

## Grammar

```ebnf
// AGL Grammar

program         = { object | state_machine | include }

object          = "object" identifier "{" 
                   { field }
                   { validate_block }
                   { state_machine_ref }
                   { events_block }
                   { permissions_block }
                   { annotations }
                   version
                 "}"

field           = identifier ":" type [constraints] ["=" default]
type            = primitive 
                | enum_type
                | array_type
                | optional_type
                | map_type
                | reference_type

primitive       = "String" | "Int" | "Float" | "Decimal" | "Boolean" 
                | "UUID" | "DateTime" | "Binary" | "JSON"

enum_type       = "Enum" "[" enum_values "]"

array_type      = type "[]"

optional_type   = type "?"

map_type        = "Map" "<" type "," type ">"

reference_type  = identifier

validate_block  = "validate" "{" validation_rules "}"

validation_rule = identifier ":" rule { "," rule }

rule            = "required" | "optional" 
                | "min" "(" number ")" 
                | "max" "(" number ")"
                | "range" "(" number "," number ")"
                | "pattern" "(" string ")"

state_machine   = "state_machine" identifier "{" 
                   "initial" ":" state
                   { state "->" state { "|" state } }
                 "}"

events_block    = "events" "{" event { event } "}"

event           = identifier ":" event_name ["{" payload "}"]

permissions     = "permissions" "{" permission { permission } "}"

version         = "version" string

include         = "include" string
```

---

## Keywords

```
Reserved Keywords:
  object, state_machine, validate, events, permissions
  include, version, extends
  required, optional, min, max, range, pattern
  unique, immutable, default
  String, Int, Float, Decimal, Boolean
  UUID, DateTime, Binary, JSON
  Enum, Map, Array
```

---

## Editor Support

### VSCode Extension

```json
{
  "language": "agl",
  "extensions": [".agl"],
  "configuration": {
    "agl": {
      "formatter": "agos-formatter",
      "linter": "agos-linter",
      "validator": "agos-validator"
    }
  }
}
```

---

## Tools

### CLI

```bash
# Parse AGL file
agos parse Mission.agl

# Validate AGL file
agos validate Mission.agl

# Generate from AGL
agos generate Mission.agl

# Generate all
agos generate-all --input specs/

# Generate specific target
agos generate --input Mission.agl --target typescript
agos generate --input Mission.agl --target python
agos generate --input Mission.agl --target go
agos generate --input Mission.agl --target schema
agos generate --input Mission.agl --target tests
```

---

## Status

```
AGL v1.0: SPECIFICATION COMPLETE
Parser: PLANNING
Generator: PLANNING
```

---

## Related Documents

- [AGOS Factory](../README.md)
- [Object Generator](../02-Object-Generator/README.md)
