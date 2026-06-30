# Universal Tool Interface

> **The most important architectural decision.**
> Every agent in the world must transform to the same shape.

---

## The Problem

Without standardization:
```
OpenHands → Custom integration
Claude Code → Custom integration
Aider → Custom integration
Cline → Custom integration
...
1000 agents → 1000 integrations
```

This creates:
- Tight coupling
- No replaceability
- Exponential complexity
- Core changes for every new tool

---

## The Solution

Every agent becomes a Tool through an Adapter:

```
External Agent
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│                   Universal Tool Adapter                  │
│  ┌─────────────────────────────────────────────────┐   │
│  │                   Input Schema                    │   │
│  │           (Validated, Typed, Documented)          │   │
│  └─────────────────────────────────────────────────┘   │
│                         │                               │
│                         ▼                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │                    Executor                      │   │
│  │    (Isolated, Sandboxed, Timeout-protected)     │   │
│  └─────────────────────────────────────────────────┘   │
│                         │                               │
│                         ▼                               │
│  ┌─────────────────────────────────────────────────┐   │
│  │                   Output Schema                   │   │
│  │           (Validated, Typed, Documented)          │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
     │
     ▼
Core Brain
```

**We don't deal with projects directly. We deal with Adapters.**

---

## Interface Specification

### Tool Metadata

```yaml
Tool:
  id: string              # Unique identifier (e.g., "openhands-v1")
  name: string            # Human-readable name
  version: string         # Adapter version (semver)
  description: string     # What this tool does
  capabilities:           # List of capabilities
    - generate_code
    - edit_code
    - review_code
  categories: []          # Categories for this tool
  tags: []                # Searchable tags
```

### Input Schema

```yaml
InputSchema:
  type: object
  properties:
    task:
      type: string
      description: "The task to perform"
      required: true
    context:
      type: object
      description: "Additional context"
      required: false
    options:
      type: object
      description: "Tool-specific options"
      required: false
  required: ["task"]
```

### Output Schema

```yaml
OutputSchema:
  type: object
  properties:
    success:
      type: boolean
    result:
      type: object
      description: "Tool-specific result"
    errors:
      type: array
      items:
        type: string
    metadata:
      type: object
      properties:
        latency_ms: number
        cost: number
        model: string
```

### Executor

```yaml
Executor:
  timeout: number         # Max execution time in seconds
  retries: number         # Retry count on failure
  sandbox: boolean        # Run in isolated environment
  resources:              # Resource limits
    memory_mb: number
    cpu_cores: number
```

---

## Adapter Implementation

### Python Example

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

class ToolInput(BaseModel):
    task: str = Field(description="The task to perform")
    context: Optional[Dict[str, Any]] = Field(default=None)
    options: Optional[Dict[str, Any]] = Field(default=None)

class ToolOutput(BaseModel):
    success: bool
    result: Optional[Dict[str, Any]] = None
    errors: list[str] = []
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ToolMetadata(BaseModel):
    id: str
    name: str
    version: str
    capabilities: list[str]
    categories: list[str]

class UniversalTool(ABC):
    """Base class for all tools in the ecosystem."""
    
    @abstractmethod
    def get_metadata(self) -> ToolMetadata:
        """Return tool metadata."""
        pass
    
    @abstractmethod
    async def execute(self, input_data: ToolInput) -> ToolOutput:
        """Execute the tool with validated input."""
        pass
    
    @abstractmethod
    def validate_input(self, data: Dict[str, Any]) -> ToolInput:
        """Validate and parse input."""
        pass
    
    @abstractmethod
    def validate_output(self, output: Any) -> ToolOutput:
        """Validate and format output."""
        pass
```

### OpenHands Adapter Example

```python
class OpenHandsAdapter(UniversalTool):
    """OpenHands as a Universal Tool."""
    
    def __init__(self, config: OpenHandsConfig):
        self.client = OpenHandsClient(config)
        self.capabilities = [
            "generate_code",
            "edit_code",
            "review_code",
            "deploy",
            "create_pr",
            "fix_bugs"
        ]
    
    def get_metadata(self) -> ToolMetadata:
        return ToolMetadata(
            id="openhands-v1",
            name="OpenHands",
            version="1.0.0",
            capabilities=self.capabilities,
            categories=["code_generation", "code_editing"]
        )
    
    async def execute(self, input_data: ToolInput) -> ToolOutput:
        start_time = time.time()
        
        try:
            result = await self.client.execute(
                task=input_data.task,
                context=input_data.context,
                options=input_data.options
            )
            
            return ToolOutput(
                success=True,
                result=result,
                metadata={
                    "latency_ms": (time.time() - start_time) * 1000,
                    "tool": "openhands"
                }
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                errors=[str(e)],
                metadata={"latency_ms": (time.time() - start_time) * 1000}
            )
```

---

## Benefits

| Benefit | Description |
|---------|-------------|
| **Replaceability** | Swap any tool without changing Core |
| **Testability** | Mock any tool easily |
| **Observability** | Trace all tool calls |
| **Consistency** | Same interface everywhere |
| **Extensibility** | Add new tools easily |
| **Isolation** | Tool failures don't crash Core |

---

## Comparison: With vs Without

### Without Universal Tool Interface

```
Core Brain
    ↓ (coupled)
OpenHands integration (50 files)
    ↓ (coupled)
Claude Code integration (50 files)
    ↓ (coupled)
Aider integration (50 files)
...

Adding new tool = 50 files + Core changes
Removing tool = 50 files + Core changes
```

### With Universal Tool Interface

```
Core Brain
    ↓ (decoupled via interface)
Universal Tool Interface
    ↓ (adapters)
OpenHands adapter (10 files)
Aider adapter (10 files)
Claude Code adapter (10 files)
...

Adding new tool = 10 files (no Core changes)
Removing tool = delete 10 files (no Core changes)
```

---

## Adapter Registry

| Tool | Adapter | Status | Capabilities |
|------|---------|--------|-------------|
| OpenHands | universal-openhands-adapter | ✅ | 6+ |
| Aider | universal-aider-adapter | ✅ | 4+ |
| Cline | universal-cline-adapter | ✅ | 4+ |
| Claude Code | claude-code-adapter | ⚠️ | 5+ (CLI only) |
| Browser Use | browser-use-adapter | ✅ | 3+ |
| SWE-Agent | swe-agent-adapter | ✅ | 4+ |
| GitHub Copilot | copilot-adapter | ⚠️ | 2+ (limited) |

---

## Integration Checklist

For any new tool:

- [ ] Create adapter class extending UniversalTool
- [ ] Implement get_metadata()
- [ ] Implement execute() with timeout
- [ ] Implement input/output validation
- [ ] Add error handling
- [ ] Add logging
- [ ] Write unit tests
- [ ] Add to registry
- [ ] Update documentation

---

## Related Documents

- [04-Tools/Tools.md](./01-Tools.md) - Tool Registry
- [02-Architecture/Architecture.md](../02-Architecture/01-Architecture.md) - Architecture
- [03-Agents/Agent-Capability-Database.md](../03-Agents/02-Agent-Capability-Database.md) - Capability Database
