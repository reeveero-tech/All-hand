# ADR-023: Learning vs Privacy

> **Architecture Decision Record**

---

## Status

**Proposed**: 2024-01-15  
**Status**: Accepted

---

## Context

Global learning raises privacy concerns:
- User's code could be learned by others
- Proprietary algorithms could be extracted
- Secrets could be exposed
- Business logic could leak

---

## Decision

> **Knowledge is a shared resource, but user data is not.**

The system CAN learn:
- A certain architectural pattern succeeded in 1000s of projects
- A certain provider became faster
- A certain testing approach is more effective

The system CANNOT transfer:
- User's code to another user
- Secrets
- Private documents
- Sensitive data

Learning is from **abstracted knowledge and statistics**, not from **project contents**.

---

## Learning Categories

```yaml
LearningCategories:
  CAN_LEARN:
    description: "Can be learned and shared"
    
    patterns:
      - Pattern names
      - Pattern structures (templates)
      - Success rates
      - Usage contexts
      
    benchmarks:
      - Provider scores
      - Capability metrics
      - Quality indicators
      - Cost analysis
      
    decisions:
      - Strategy outcomes
      - Technology choices
      - Architecture selections
      
    lessons:
      - Best practices
      - Anti-patterns
      - Success factors
      
  CANNOT_LEARN:
    description: "Must never be learned or shared"
    
    code:
      - Source code
      - Proprietary algorithms
      - Business logic
      - Trade secrets
      
    secrets:
      - API keys
      - Passwords
      - Tokens
      - Certificates
      
    data:
      - User data
      - Customer information
      - Transaction records
      - Personal information
      
    documents:
      - Private documents
      - Confidential reports
      - Internal communications
```

---

## Abstraction Levels

```yaml
AbstractionLevels:
  CODE:
    level: 0
    example: "def calculate_revenue(items): return sum(i.price for i in items)"
    can_share: NO
    
  PATTERN:
    level: 1
    example: "Aggregation Pattern - sum values from collection"
    can_share: YES
    
  STATISTICS:
    level: 2
    example: "Aggregation used in 80% of e-commerce projects"
    can_share: YES
    
  RECOMMENDATION:
    level: 3
    example: "Use aggregation for revenue calculation"
    can_share: YES
```

---

## Enforcement

```python
class LearningEnforcement:
    """
    Enforce privacy in learning.
    """
    
    async def extract_knowledge(
        self,
        execution: Execution
    ) -> Knowledge:
        """
        Extract knowledge while preserving privacy.
        """
        
        # Extract ONLY pattern (not code)
        pattern = self.extract_pattern(execution.code)
        
        # Extract ONLY metrics (not data)
        metrics = self.extract_metrics(execution)
        
        # Extract ONLY lessons (not secrets)
        lessons = self.extract_lessons(execution)
        
        # NEVER extract
        # - Source code
        # - Proprietary logic
        # - User data
        # - Secrets
        
        return Knowledge(
            pattern=pattern,
            metrics=metrics,
            lessons=lessons
        )
        
    def extract_pattern(self, code: Code) -> Pattern:
        """
        Extract pattern template, NOT code.
        """
        
        # Analyze structure
        structure = analyze_structure(code)
        
        # Create template (not actual code)
        template = create_template(structure)
        
        return Pattern(
            type=structure.type,
            template=template,
            # NOT: actual code
        )
```

---

## Example: Learning from E-commerce Project

```
Project A: "Build e-commerce platform"

CAN LEARN:
  ✅ Pattern: "Microservices architecture"
  ✅ Success rate: 92% for e-commerce
  ✅ Provider: "OpenHands" works well
  ✅ Lesson: "Separate concerns by domain"
  
CANNOT LEARN:
  ❌ Actual code from project
  ❌ Business logic (pricing algorithm)
  ❌ Customer database
  ❌ API keys
  ❌ Payment processing logic
```

---

## User Control

```yaml
UserControl:
  # Users can control what is learned
  
  sharing_options:
    share_patterns: boolean  # default: true
    share_metrics: boolean    # default: true
    share_lessons: boolean    # default: true
    
  exclusion:
    exclude_folders: list[string]  # e.g., ["internal", "proprietary"]
    exclude_patterns: list[string]  # e.g., ["*secret*"]
    
  consent:
    explicit_consent: boolean    # default: false
    opt_in: boolean              # default: true
```

---

## Consequences

### Positive

1. **Privacy Protected** - User data never leaks
2. **Global Learning** - Everyone benefits from patterns
3. **Trust** - Users trust the platform
4. **Compliance** - Meets GDPR, HIPAA, etc.

### Negative

1. **Learning Limited** - Can't learn from actual code
2. **Abstraction Overhead** - Need abstraction layer

### Neutral

1. **Complexity** - Privacy enforcement adds complexity

---

## Related ADRs

| ADR | Title | Relationship |
|-----|-------|--------------|
| ADR-022 | Knowledge is Global | Foundation |
| **ADR-023** | **Learning vs Privacy** | **This decision** |
| ADR-025 | No Knowledge Without Evidence | Related |

---

## References

- [Knowledge-Graph-Overview.md](../01-Knowledge-Graph/01-Knowledge-Graph-Overview.md)
