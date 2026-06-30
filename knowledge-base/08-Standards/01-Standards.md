# Standards & Guidelines

## Coding Standards

### General Principles

| Principle | Description |
|-----------|-------------|
| **KISS** | Keep It Simple, Stupid |
| **DRY** | Don't Repeat Yourself |
| **YAGNI** | You Aren't Gonna Need It |
| **SOLID** | Single responsibility, Open-closed, Liskov substitution, Interface segregation, Dependency inversion |

### Code Quality

```python
# ✅ Good
def calculate_score(tool, context):
    """Calculate tool score based on selection criteria."""
    weights = {
        'activity': 0.3,
        'documentation': 0.2,
        'usage': 0.2,
        'extensibility': 0.15,
        'independence': 0.15
    }
    return sum(getattr(tool, k) * v for k, v in weights.items())

# ❌ Bad
def calc(t, c):
    return t.activity * 0.3 + t.doc * 0.2
```

---

## Documentation Standards

### ADR Format

Every architecture decision must have:
1. **Title** - Clear, descriptive
2. **Date** - When decided
3. **Status** - Proposed/Accepted/Deprecated
4. **Context** - The situation
5. **Decision** - What we decided
6. **Rationale** - Why this choice
7. **Consequences** - What happens next

### README Format

```markdown
# Tool Name

## Quick Info
- Type:
- License:
- Stars:
- Status: Production/Staging/Experimental

## Capabilities
- [ ] Capability 1
- [ ] Capability 2

## Usage
```bash
# installation
# basic usage
```

## Alternatives
| Tool | When to use instead |
|------|-------------------|
```

---

## Interface Standards

### Universal Tool Interface

```yaml
Tool:
  name: string           # Unique identifier
  version: semver        # Adapter version
  input_schema:          # JSON Schema
    type: object
    properties: {}
    required: []
  output_schema:         # JSON Schema
    type: object
    properties: {}
  capabilities: []       # List of capabilities
  config: {}             # Tool-specific config
```

### API Standards

- REST for synchronous operations
- WebSocket for real-time
- OpenAPI 3.0 specification
- JSON for data exchange
- Version in URL: `/v1/`, `/v2/`

---

## Testing Standards

| Level | Coverage | Tools |
|-------|----------|-------|
| Unit | 80%+ | pytest, unittest |
| Integration | 70%+ | pytest, testcontainers |
| E2E | Critical paths | Playwright, Selenium |

---

## Security Standards

1. **No secrets in code** - Use environment variables
2. **Input validation** - Always validate schemas
3. **Output sanitization** - Never trust external tools
4. **Least privilege** - Minimal permissions
5. **Audit logging** - Track all operations

---

## Related Documents

- [07-Decisions](../07-Decisions/01-Decisions.md) - Architecture Decisions
