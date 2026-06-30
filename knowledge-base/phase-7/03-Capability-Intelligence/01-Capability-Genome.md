# Capability Genome

> **Instead of manually classifying providers, we give them a capability fingerprint.**

---

## Concept

```
Traditional Classification (Manual):
  Provider: OpenHands
  Type: AI Agent
  
  Problems:
  - Vague
  - Not comparable
  - No granular data
  
Capability Genome (Automatic):
  Provider: OpenHands
  Genome:
    Planning:        ████████░░ 65
    Code Generation: █████████░ 95
    Debugging:       █████████░ 92
    Testing:         ████████░░ 88
    Repository IQ:   █████████░ 91
    Deployment:      ███████░░░ 70
    Browser:         ███░░░░░░░ 35
    
  Result:
  - Granular
  - Comparable
  - Data-driven
```

---

## Genome Structure

```yaml
CapabilityGenome:
  # Provider identification
  provider_id: string
  provider_name: string
  version: string
  updated_at: datetime
  
  # Capability scores (0-100)
  scores:
    # Coding capabilities
    code_generation: 0-100
    code_editing: 0-100
    code_review: 0-100
    code_debugging: 0-100
    
    # Project capabilities
    planning: 0-100
    repository_analysis: 0-100
    dependency_management: 0-100
    
    # Testing capabilities
    test_generation: 0-100
    test_execution: 0-100
    test_debugging: 0-100
    
    # Deployment capabilities
    deployment: 0-100
    containerization: 0-100
    infrastructure: 0-100
    monitoring: 0-100
    
    # Browser capabilities
    browser_navigation: 0-100
    form_automation: 0-100
    web_scraping: 0-100
    
    # Specialized capabilities
    security_analysis: 0-100
    performance_analysis: 0-100
    refactoring: 0-100
    documentation: 0-100
    
  # Metadata
  metadata:
    total_execution_count: integer
    last_benchmark_date: datetime
    confidence_score: 0.0-1.0
    sample_size: integer
    
  # Comparative scores
  comparisons:
    best_at: list[string]      # Top 3 capabilities
    worst_at: list[string]     # Bottom 3 capabilities
    recommended_for: list[string]  # Best use cases
    not_recommended_for: list[string]  # Worst use cases
```

---

## Genome Calculation

```python
def calculate_genome(
    provider: Provider,
    executions: list[Execution]
) -> CapabilityGenome:
    """
    Calculate capability genome from execution history.
    """
    
    genome = CapabilityGenome(provider_id=provider.id)
    
    # For each capability
    for capability_id in ALL_CAPABILITIES:
        scores = []
        
        # Get all executions with this capability
        relevant_executions = [
            e for e in executions
            if e.capability_id == capability_id
        ]
        
        for execution in relevant_executions:
            # Calculate quality score
            quality = calculate_quality_score(execution)
            
            # Calculate efficiency score
            efficiency = calculate_efficiency_score(execution)
            
            # Calculate reliability score
            reliability = calculate_reliability_score(execution)
            
            # Combined score
            score = (
                quality * 0.4 +
                efficiency * 0.3 +
                reliability * 0.3
            )
            
            scores.append(score)
            
        # Average score
        if scores:
            genome.scores[capability_id] = mean(scores)
        else:
            genome.scores[capability_id] = None
            
    # Update metadata
    genome.metadata.total_execution_count = len(executions)
    genome.metadata.sample_size = len(relevant_executions)
    genome.metadata.confidence_score = calculate_confidence(genome)
    
    return genome
```

---

## Genome Comparison

```python
def match_genome_to_task(
    task: Task,
    providers: list[Provider]
) -> list[MatchResult]:
    """
    Match task requirements to provider genomes.
    """
    
    results = []
    
    # Get task genome requirements
    task_genome = task.required_capabilities
    
    for provider in providers:
        # Get provider genome
        provider_genome = get_provider_genome(provider)
        
        # Calculate match score
        match_score = calculate_match_score(
            task_genome=task_genome,
            provider_genome=provider_genome
        )
        
        results.append({
            "provider": provider,
            "genome": provider_genome,
            "match_score": match_score
        })
        
    # Sort by match score
    results.sort(key=lambda r: r["match_score"], reverse=True)
    
    return results


def calculate_match_score(
    task_genome: dict,
    provider_genome: CapabilityGenome
) -> float:
    """
    Calculate how well a provider matches task requirements.
    """
    
    total_score = 0.0
    total_weight = 0.0
    
    for capability, weight in task_genome.items():
        provider_score = provider_genome.scores.get(capability, 0)
        total_score += provider_score * weight
        total_weight += weight
        
    return total_score / total_weight if total_weight > 0 else 0.0
```

---

## Example Genomes

### OpenHands Genome

```yaml
Provider: OpenHands
Type: AI Agent

Genome:
  # Coding
  code_generation: 95
  code_editing: 88
  code_review: 92
  code_debugging: 92
  
  # Project
  planning: 65
  repository_analysis: 91
  dependency_management: 78
  
  # Testing
  test_generation: 88
  test_execution: 82
  test_debugging: 85
  
  # Deployment
  deployment: 70
  containerization: 75
  infrastructure: 65
  monitoring: 60
  
  # Browser
  browser_navigation: 35
  form_automation: 30
  web_scraping: 25
  
  # Specialized
  security_analysis: 80
  performance_analysis: 72
  refactoring: 85
  documentation: 78

Metadata:
  total_executions: 10000
  confidence: 0.95
  last_updated: "2024-01-15"
  
Best For:
  - Large projects
  - Complex features
  - Full-stack development
  
Not Recommended For:
  - Simple browser tasks
  - Quick fixes
```

### Browser Use Genome

```yaml
Provider: Browser Use
Type: Browser Automation

Genome:
  # Coding
  code_generation: 45
  code_editing: 50
  code_review: 55
  code_debugging: 52
  
  # Project
  planning: 10
  repository_analysis: 20
  dependency_management: 25
  
  # Testing
  test_generation: 30
  test_execution: 35
  test_debugging: 40
  
  # Deployment
  deployment: 15
  containerization: 10
  infrastructure: 10
  monitoring: 10
  
  # Browser
  browser_navigation: 99
  form_automation: 98
  web_scraping: 97
  
  # Specialized
  security_analysis: 15
  performance_analysis: 20
  refactoring: 35
  documentation: 40

Metadata:
  total_executions: 5000
  confidence: 0.92
  last_updated: "2024-01-15"
  
Best For:
  - Web automation
  - Form filling
  - Data scraping
  - UI testing
  
Not Recommended For:
  - Backend development
  - Complex refactoring
  - Architecture design
```

### Aider Genome

```yaml
Provider: Aider
Type: CLI AI Assistant

Genome:
  # Coding
  code_generation: 85
  code_editing: 92
  code_review: 78
  code_debugging: 80
  
  # Project
  planning: 40
  repository_analysis: 65
  dependency_management: 70
  
  # Testing
  test_generation: 75
  test_execution: 80
  test_debugging: 78
  
  # Deployment
  deployment: 45
  containerization: 50
  infrastructure: 40
  monitoring: 35
  
  # Browser
  browser_navigation: 10
  form_automation: 15
  web_scraping: 12
  
  # Specialized
  security_analysis: 60
  performance_analysis: 65
  refactoring: 88
  documentation: 70

Metadata:
  total_executions: 8000
  confidence: 0.90
  last_updated: "2024-01-15"
  
Best For:
  - Quick edits
  - Small changes
  - Iterative refactoring
  - CLI tasks
  
Not Recommended For:
  - Browser automation
  - Large architecture
  - Deployment tasks
```

---

## Task to Genome Matching

```yaml
Task: "Build complete e-commerce API"

Required Genome:
  code_generation: 90 (weight: 0.3)
  test_generation: 80 (weight: 0.2)
  deployment: 70 (weight: 0.2)
  security_analysis: 75 (weight: 0.15)
  planning: 65 (weight: 0.15)
  
Match Results:
  1. OpenHands: 82.5/100
     → Recommended
  
  2. Aider: 68.0/100
     → Not optimal
  
  3. Browser Use: 32.0/100
     → Not suitable
```

---

## Genome Evolution

```yaml
GenomeEvolution:
  # Genomes update over time
  
  UpdateFrequency:
    - After 100 new executions
    - Weekly minimum
    - Real-time for critical capabilities
    
  VersionHistory:
    - Every update creates new version
    - Historical genomes preserved
    - Trend analysis possible
    
  DriftDetection:
    - Detect significant score changes
    - Alert on unexpected changes
    - Validate against benchmarks
```

---

## Genome Validation

```python
async def validate_genome(genome: CapabilityGenome) -> ValidationResult:
    """
    Validate genome against benchmarks.
    """
    
    # Check all scores in range
    for capability, score in genome.scores.items():
        if score < 0 or score > 100:
            return ValidationResult(
                valid=False,
                error=f"Score out of range: {capability}"
            )
            
    # Check confidence threshold
    if genome.metadata.confidence_score < 0.5:
        return ValidationResult(
            valid=False,
            error="Insufficient sample size"
        )
        
    # Compare with manual classification
    manual = get_manual_classification(genome.provider_id)
    comparison = compare_genome_to_manual(genome, manual)
    
    if comparison.disagreement > 0.3:
        return ValidationResult(
            valid=False,
            error="Genome disagrees with manual classification",
            details=comparison
        )
        
    return ValidationResult(valid=True)
```

---

## Related Documents

- [Provider-Intelligence.md](./02-Provider-Intelligence.md)
- [Benchmark-Engine.md](../01-Knowledge-Graph/04-Benchmark-Engine.md)
