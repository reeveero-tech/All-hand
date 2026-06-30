# Universal Knowledge Graph

> **Every piece of information becomes a Node.**

---

## Knowledge Graph Concept

```
┌─────────────────────────────────────────────────────────────┐
│                  Universal Knowledge Graph                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐       │
│  │ Project │  │Provider │  │Capability│  │Pattern │       │
│  │  Node  │  │  Node   │  │   Node  │  │  Node  │       │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘       │
│       │            │            │            │             │
│       └────────────┴────────────┴────────────┘             │
│                         │                                    │
│                         ▼                                    │
│                    RELATIONSHIPS                            │
│                                                             │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐       │
│  │  Bug   │  │ Solution│  │Decision │  │Benchmark│       │
│  │  Node  │  │  Node   │  │   Node  │  │  Node  │       │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Node Types

```yaml
NodeTypes:
  PROJECT:
    properties:
      - name: string
      - domain: string
      - architecture: string
      - technologies: list[string]
      - outcomes: list[string]
      - quality_score: decimal
      - cost: decimal
      - duration: duration
      
  PROVIDER:
    properties:
      - name: string
      - type: string
      - capabilities: list[CapabilityID]
      - benchmarks: dict
      - reliability: decimal
      - cost_per_use: decimal
      
  CAPABILITY:
    properties:
      - name: string
      - category: string
      - success_rate: decimal
      - typical_cost: decimal
      - typical_duration: duration
      - best_providers: list[ProviderID]
      
  PATTERN:
    properties:
      - name: string
      - type: string
      - context: list[string]
      - success_rate: decimal
      - usage_count: integer
      
  ARCHITECTURE:
    properties:
      - name: string
      - style: string
      - maintainability: decimal
      - scalability: decimal
      - security: decimal
      - performance: decimal
      
  BUG:
    properties:
      - type: string
      - severity: enum
      - reason: string
      - solution: string
      - occurrence_count: integer
      
  SOLUTION:
    properties:
      - description: string
      - pattern: string
      - effectiveness: decimal
      - success_count: integer
      
  DECISION:
    properties:
      - type: string
      - alternatives: list[string]
      - selected: string
      - reasoning: string
      - outcome: string
      
  LESSON:
    properties:
      - description: string
      - category: string
      - evidence: list[string]
      - confidence: decimal
      
  BENCHMARK:
    properties:
      - provider_id: string
      - capability_id: string
      - score: decimal
      - metrics: dict
      - timestamp: datetime
```

---

## Relationship Types

```yaml
RelationshipTypes:
  USES:
    description: "Project uses Provider"
    properties:
      - frequency: integer
      - success_rate: decimal
      
  IMPLEMENTS:
    description: "Project implements Pattern"
    properties:
      - context: string
      
  ACHIEVED_WITH:
    description: "Project achieved with Provider"
    properties:
      - quality: decimal
      - cost: decimal
      
  SIMILAR_TO:
    description: "Pattern similar to Pattern"
    properties:
      - similarity: decimal
      
  SUCCEEDED_WITH:
    description: "Provider succeeded with Capability"
    properties:
      - success_rate: decimal
      
  FAILED_WITH:
    description: "Provider failed with Capability"
    properties:
      - failure_rate: decimal
      
  LEARNED_FROM:
    description: "Lesson learned from Project"
    properties:
      - confidence: decimal
      
  RESULTED_IN:
    description: "Decision resulted in Outcome"
    properties:
      - outcome: string
```

---

## Graph Schema

```yaml
KnowledgeGraph:
  # Graph metadata
  id: string
  name: string
  version: string
  created_at: datetime
  updated_at: datetime
  
  # Statistics
  stats:
    total_nodes: integer
    total_relationships: integer
    node_types: dict[string]integer
    relationship_types: dict[string]integer
    
  # Nodes by type
  nodes:
    projects: list[ProjectNode]
    providers: list[ProviderNode]
    capabilities: list[CapabilityNode]
    patterns: list[PatternNode]
    architectures: list[ArchitectureNode]
    bugs: list[BugNode]
    solutions: list[SolutionNode]
    decisions: list[DecisionNode]
    lessons: list[LessonNode]
    benchmarks: list[BenchmarkNode]
    
  # Query indexes
  indexes:
    - type: "by_name"
    - type: "by_type"
    - type: "by_property"
    - type: "by_relationship"
```

---

## Example Graph

```yaml
KnowledgeGraph:
  nodes:
    - id: "project_001"
      type: "PROJECT"
      properties:
        name: "E-commerce Platform"
        domain: "E-commerce"
        architecture: "microservices"
        quality_score: 0.92
        
    - id: "provider_openhands"
      type: "PROVIDER"
      properties:
        name: "OpenHands"
        type: "ai_agent"
        
    - id: "pattern_microservices"
      type: "PATTERN"
      properties:
        name: "Microservices Architecture"
        success_rate: 0.88
        
    - id: "cap_generate_backend"
      type: "CAPABILITY"
      properties:
        name: "Generate Backend"
        success_rate: 0.90
        
  relationships:
    - from: "project_001"
      to: "provider_openhands"
      type: "USES"
      properties:
        frequency: 25
        success_rate: 0.92
        
    - from: "project_001"
      to: "pattern_microservices"
      type: "IMPLEMENTS"
      properties:
        context: "high_scale"
        
    - from: "project_001"
      to: "cap_generate_backend"
      type: "ACHIEVED_WITH"
      properties:
        quality: 0.95
        cost: 0.05
```

---

## Graph Operations

### Add Node

```python
async def add_node(graph: KnowledgeGraph, node: Node):
    """
    Add a node to the graph.
    """
    
    # Validate node
    if not node.validate():
        raise InvalidNodeError(node)
        
    # Check for duplicates
    existing = graph.find_node(node.id)
    if existing:
        raise DuplicateNodeError(node.id)
        
    # Add to graph
    graph.nodes[node.type].append(node)
    
    # Update indexes
    graph.update_indexes(node)
    
    # Update statistics
    graph.stats.total_nodes += 1
    graph.stats.node_types[node.type] += 1
```

### Add Relationship

```python
async def add_relationship(
    graph: KnowledgeGraph,
    relationship: Relationship
):
    """
    Add a relationship to the graph.
    """
    
    # Validate nodes exist
    from_node = graph.find_node(relationship.from_id)
    to_node = graph.find_node(relationship.to_id)
    
    if not from_node or not to_node:
        raise NodeNotFoundError()
        
    # Add relationship
    graph.relationships.append(relationship)
    
    # Update statistics
    graph.stats.total_relationships += 1
```

### Query Graph

```python
async def query_graph(
    graph: KnowledgeGraph,
    query: GraphQuery
) -> list[Node]:
    """
    Query the knowledge graph.
    """
    
    results = []
    
    # Find by type
    if query.node_type:
        results = graph.nodes[query.node_type]
        
    # Filter by properties
    if query.properties:
        results = filter_by_properties(results, query.properties)
        
    # Filter by relationships
    if query.related_to:
        results = filter_by_relationship(results, query.related_to)
        
    # Sort
    if query.sort_by:
        results = sort_by(results, query.sort_by)
        
    # Limit
    if query.limit:
        results = results[:query.limit]
        
    return results
```

---

## Advanced Queries

### Find Best Provider for Capability

```python
async def find_best_provider(
    graph: KnowledgeGraph,
    capability_id: string
) -> list[ProviderNode]:
    """
    Find best providers for a capability.
    """
    
    # Find capability node
    capability = graph.find_node(capability_id)
    
    # Find all providers that succeeded with this capability
    providers = graph.query(
        node_type="PROVIDER",
        related_to=capability_id,
        relationship_type="SUCCEEDED_WITH"
    )
    
    # Sort by success rate
    sorted_providers = sorted(
        providers,
        key=lambda p: p.benchmarks[capability_id].success_rate,
        reverse=True
    )
    
    return sorted_providers
```

### Find Patterns for Domain

```python
async def find_patterns_for_domain(
    graph: KnowledgeGraph,
    domain: string
) -> list[PatternNode]:
    """
    Find successful patterns for a domain.
    """
    
    # Find all projects in domain
    projects = graph.query(
        node_type="PROJECT",
        properties={"domain": domain}
    )
    
    # Find patterns used by successful projects
    patterns = []
    for project in projects:
        if project.quality_score > 0.8:
            project_patterns = graph.get_relationships(
                from_node=project.id,
                type="IMPLEMENTS"
            )
            patterns.extend(project_patterns)
            
    # Sort by success rate
    sorted_patterns = sorted(
        patterns,
        key=lambda p: p.success_rate,
        reverse=True
    )
    
    return sorted_patterns
```

---

## Graph Updates

```python
class GraphUpdateScheduler:
    """
    Schedule graph updates.
    """
    
    async def run_updates(self):
        """
        Run periodic graph updates.
        """
        
        # 1. Update benchmarks
        await self.update_benchmarks()
        
        # 2. Update pattern success rates
        await self.update_pattern_success_rates()
        
        # 3. Update provider rankings
        await self.update_provider_rankings()
        
        # 4. Clean up stale nodes
        await self.cleanup_stale_nodes()
        
        # 5. Validate graph integrity
        await self.validate_graph()
        
    async def update_benchmarks(self):
        """
        Update benchmark data from all providers.
        """
        
        for provider in graph.nodes["PROVIDER"]:
            benchmarks = await provider.fetch_benchmarks()
            
            for cap_id, benchmark in benchmarks.items():
                node = graph.find_node(provider.id)
                node.benchmarks[cap_id] = benchmark
```

---

## Privacy Considerations

```yaml
PrivacyRules:
  # What enters the graph
  
  CAN_ENTER:
    - Pattern names and descriptions
    - Architecture styles
    - Success rates
    - Average costs
    - Technology categories
    - Benchmark scores
    - General lessons
    
  CANNOT_ENTER:
    - Source code
    - Proprietary business logic
    - API keys or secrets
    - User credentials
    - Personal information
    - Private documents
    
  AGGREGATED_ONLY:
    - Cost per project
    - Duration per project
    - Specific technologies used
```

---

## Related Documents

- [Pattern-Engine.md](./02-Pattern-Engine.md)
- [Benchmark-Engine.md](./04-Benchmark-Engine.md)
