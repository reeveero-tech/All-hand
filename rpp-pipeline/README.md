# RPP: Repository Processing Pipeline

> **The Assembly Line for Repository Analysis**

---

## Vision

```
OLD WAY:
  Analyze repo 1
  Analyze repo 2
  Analyze repo 3
  ...
  1500 repos = 1500 manual analyses

NEW WAY:
  GitHub URL
       │
       ▼
  ┌─────────────────────────────────────────────────────────┐
  │                    PROCESSING PIPELINE                            │
  │                                                           │
  │  Clone ──► Normalize ──► Detect ──► Extract ──► Analyze  │
  │                                              │             │
  │  Score ──► DNA ──► Knowledge Graph ──► Search Index    │
  └─────────────────────────────────────────────────────────┘
       │
       ▼
  1500 repos analyzed = ONE unified knowledge base
```

---

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    RPP: 8 STAGES                                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  STAGE 1: Universal Normalizer                               │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Convert any repository to Universal Object           │  │
│  └─────────────────────────────────────────────────────┘  │
│                           │                                │
│                           ▼                                │
│  STAGE 2: Detector Marketplace                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  20+ pluggable detectors                            │  │
│  └─────────────────────────────────────────────────────┘  │
│                           │                                │
│                           ▼                                │
│  STAGE 3: Feature Extraction                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Extract features from all detectors                 │  │
│  └─────────────────────────────────────────────────────┘  │
│                           │                                │
│                           ▼                                │
│  STAGE 4: Knowledge Graph Builder                         │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Build global knowledge graph                        │  │
│  └─────────────────────────────────────────────────────┘  │
│                           │                                │
│                           ▼                                │
│  STAGE 5: Similarity Engine                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Find similar repositories                           │  │
│  └─────────────────────────────────────────────────────┘  │
│                           │                                │
│                           ▼                                │
│  STAGE 6: Evolution Tracker                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Track changes over time                            │  │
│  └─────────────────────────────────────────────────────┘  │
│                           │                                │
│                           ▼                                │
│  STAGE 7: Confidence Engine                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Assign confidence to all features                   │  │
│  └─────────────────────────────────────────────────────┘  │
│                           │                                │
│                           ▼                                │
│  STAGE 8: Benchmark Queue                               │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Queue repos for ARI benchmarking                     │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Stage 1: Universal Normalizer

### Purpose

Convert any repository to a Universal Repository Object (URO).

### Problem

```
Repo A: Python
  - setup.py
  - requirements.txt
  - src/
  
Repo B: Rust
  - Cargo.toml
  - src/
  - Cargo.lock
  
Repo C: Go
  - go.mod
  - cmd/
  
All different. All need different processing.

SOLUTION:
  Universal Repository Object
  All repos → Same format
  All analyzers → Work on URO
```

### URO Schema

```python
@dataclass
class UniversalRepositoryObject:
    """Universal format for all repositories."""
    
    # Identity
    url: str
    name: str
    owner: str
    
    # File Tree
    files: List[FileNode]
    directories: List[DirNode]
    
    # Dependencies (normalized)
    dependencies: List[Dependency]
    
    # Language
    primary_language: str
    all_languages: List[str]
    
    # Configuration
    configs: Dict[str, Any]
    
    # Entry Points
    entry_points: List[EntryPoint]
    
    # Metadata
    git_data: GitMetadata
    github_data: GitHubMetadata
    
    # Normalized
    normalized_at: datetime
    version: str
```

### Normalization Rules

```python
class UniversalNormalizer:
    """Normalizes repositories to URO."""
    
    def normalize(self, repo: RawRepository) -> UniversalRepositoryObject:
        """Convert to universal format."""
        
        # 1. Parse file tree
        files = self._parse_tree(repo)
        
        # 2. Normalize dependencies
        deps = self._normalize_dependencies(repo)
        
        # 3. Detect languages
        langs = self._detect_languages(repo)
        
        # 4. Parse configs
        configs = self._parse_configs(repo)
        
        # 5. Find entry points
        entries = self._find_entry_points(repo)
        
        return UniversalRepositoryObject(
            url=repo.url,
            name=repo.name,
            owner=repo.owner,
            files=files,
            directories=self._get_dirs(files),
            dependencies=deps,
            primary_language=langs[0] if langs else "Unknown",
            all_languages=langs,
            configs=configs,
            entry_points=entries,
            git_data=repo.git_data,
            github_data=repo.github_data,
            normalized_at=datetime.utcnow(),
            version="1.0.0"
        )
```

---

## Stage 2: Detector Marketplace

### Purpose

20+ pluggable detectors, each as a plugin.

### Available Detectors

```yaml
Detectors:
  Language:
    - Python Detector
    - JavaScript Detector
    - TypeScript Detector
    - Go Detector
    - Rust Detector
    - Java Detector
    - C# Detector
    
  Framework:
    - FastAPI Detector
    - Django Detector
    - React Detector
    - Next.js Detector
    - Spring Detector
    - Rails Detector
    
  AI:
    - LLM Detector
    - LiteLLM Detector
    - LangChain Detector
    - LlamaIndex Detector
    - MCP Detector
    - OpenRouter Detector
    
  Infrastructure:
    - Docker Detector
    - Kubernetes Detector
    - AWS Detector
    - GCP Detector
    - Azure Detector
    
  Tools:
    - GitHub Detector
    - GitLab Detector
    - Jira Detector
    - Slack Detector
    
  Security:
    - Security Scanner Detector
    - SAST Detector
    - DAST Detector
    
  Quality:
    - Testing Framework Detector
    - CI/CD Detector
    - Documentation Detector
```

### Plugin Interface

```python
class DetectorPlugin(ABC):
    """Base interface for all detectors."""
    
    @property
    def name(self) -> str:
        """Detector name."""
        pass
    
    @property
    def version(self) -> str:
        """Detector version."""
        pass
    
    @property
    def category(self) -> str:
        """Detector category."""
        pass
    
    @abstractmethod
    def detect(self, uro: UniversalRepositoryObject) -> List[Feature]:
        """Detect features in URO."""
        pass
    
    @abstractmethod
    def confidence(self, feature: Feature) -> float:
        """Calculate confidence for a feature."""
        pass
```

### Marketplace CLI

```bash
# Install detector
rpp detector install python-detector

# List detectors
rpp detector list

# Update detector
rpp detector update python-detector

# Remove detector
rpp detector remove python-detector
```

---

## Stage 3: Feature Extraction

### Purpose

Extract features from all detectors.

### Feature Schema

```python
@dataclass
class Feature:
    """A single feature extracted from repository."""
    
    name: str
    value: Any
    confidence: float
    source: str
    evidence: List[str]
    detected_at: datetime
    
@dataclass
class FeatureSet:
    """All features for a repository."""
    
    repository: str
    features: Dict[str, Feature]
    detected_at: datetime
    detectors_used: List[str]
    
    # Quick access
    @property
    def supports_mcp(self) -> bool:
        return self.features.get("mcp", Feature()).value
    
    @property
    def supports_claude(self) -> bool:
        return self.features.get("claude", Feature()).value
    
    @property
    def has_docker(self) -> bool:
        return self.features.get("docker", Feature()).value
    
    @property
    def planner(self) -> bool:
        return self.features.get("planner", Feature()).value
    
    @property
    def memory_type(self) -> str:
        return self.features.get("memory_type", Feature()).value
    
    @property
    def tool_count(self) -> int:
        return self.features.get("tool_count", Feature()).value
    
    @property
    def languages(self) -> List[str]:
        return self.features.get("languages", Feature()).value
```

### Example Output

```python
FeatureSet(
    repository="OpenHands",
    
    features={
        "mcp": Feature(value=True, confidence=1.0),
        "claude": Feature(value=True, confidence=0.98),
        "gpt4": Feature(value=True, confidence=0.95),
        "docker": Feature(value=True, confidence=1.0),
        "planner": Feature(value=True, confidence=0.85),
        "memory_type": Feature(value="vector", confidence=0.90),
        "tool_count": Feature(value=17, confidence=1.0),
        "languages": Feature(value=["Python", "TypeScript"], confidence=1.0),
        "has_browser": Feature(value=True, confidence=0.88),
        "has_git": Feature(value=True, confidence=1.0),
        "multi_agent": Feature(value=True, confidence=0.92),
    },
    
    detected_at=datetime(2024, 1, 15),
    detectors_used=["LLM", "LiteLLM", "Docker", "Python"]
)
```

---

## Stage 4: Knowledge Graph Builder

### Purpose

Build global knowledge graph from all repositories.

### Graph Schema

```python
class KnowledgeGraph:
    """Global knowledge graph."""
    
    def add_repository(self, repo: FeatureSet):
        """Add repository to graph."""
        
        # Create repo node
        repo_node = Node(
            type="REPOSITORY",
            id=repo.repository,
            properties=self._repo_properties(repo)
        )
        self.graph.add(repo_node)
        
        # Add feature nodes and edges
        for feature_name, feature in repo.features.items():
            feature_node = self._get_or_create_feature(feature_name)
            
            # Edge: REPO --uses--> FEATURE
            edge = Edge(
                from_node=repo_node,
                to_node=feature_node,
                type="USES",
                confidence=feature.confidence,
                evidence=feature.evidence
            )
            self.graph.add(edge)
    
    def query(self, query: str) -> List[Dict]:
        """Query the graph."""
        # Find repos with specific features
        pass
    
    def find_similar(self, repo: str) -> List[SimilarRepo]:
        """Find similar repositories."""
        pass
```

### Graph Visualization

```
┌─────────────────────────────────────────────────────────────┐
│                  KNOWLEDGE GRAPH                                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  OpenHands                                                 │
│    ├── uses ──► LiteLLM                                    │
│    ├── uses ──► Claude                                     │
│    ├── uses ──► GPT-4                                      │
│    ├── supports ──► MCP                                    │
│    ├── has ──► Planner                                     │
│    ├── has ──► Browser                                     │
│    ├── has ──► Docker                                      │
│    └── similar_to ──► Goose (85%)                          │
│                                                             │
│  Cline                                                     │
│    ├── uses ──► Claude                                     │
│    ├── supports ──► MCP                                    │
│    ├── has ──► Editor                                     │
│    └── similar_to ──► Cursor (82%)                         │
│                                                             │
│  Goose                                                     │
│    ├── uses ──► GPT-4                                      │
│    ├── has ──► Planner                                     │
│    └── similar_to ──► OpenHands (79%)                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Stage 5: Similarity Engine

### Purpose

Find similar repositories.

### Algorithm

```python
class SimilarityEngine:
    """Find similar repositories."""
    
    def find_similar(
        self, 
        repo: str, 
        limit: int = 10
    ) -> List[SimilarRepo]:
        """Find most similar repositories."""
        
        repo_features = self.graph.get_features(repo)
        
        similarities = []
        for other_repo in self.graph.all_repos():
            if other_repo == repo:
                continue
                
            other_features = self.graph.get_features(other_repo)
            
            # Calculate similarity
            similarity = self._calculate_similarity(
                repo_features,
                other_features
            )
            
            similarities.append(SimilarRepo(
                repo=other_repo,
                similarity=similarity,
                common_features=self._find_common(repo_features, other_features),
                differences=self._find_differences(repo_features, other_features)
            ))
        
        # Sort by similarity
        similarities.sort(key=lambda x: x.similarity, reverse=True)
        
        return similarities[:limit]
    
    def _calculate_similarity(
        self, 
        features_a: Dict, 
        features_b: Dict
    ) -> float:
        """Jaccard + cosine similarity."""
        
        # Feature overlap (Jaccard)
        keys_a = set(features_a.keys())
        keys_b = set(features_b.keys())
        overlap = len(keys_a & keys_b)
        union = len(keys_a | keys_b)
        jaccard = overlap / union if union > 0 else 0
        
        # Value similarity (cosine)
        values_a = [1 if features_a[k].value else 0 for k in keys_a & keys_b]
        values_b = [1 if features_b[k].value else 0 for k in keys_a & keys_b]
        cosine = self._cosine(values_a, values_b)
        
        return (jaccard + cosine) / 2
```

### Example Output

```json
{
  "repository": "OpenHands",
  "similar": [
    {
      "repo": "Goose",
      "similarity": 0.85,
      "common": ["Planner", "Claude", "Docker", "Git"],
      "differences": ["OpenHands has MCP, Goose has not"]
    },
    {
      "repo": "Cline",
      "similarity": 0.74,
      "common": ["Claude", "Multi-file editing"],
      "differences": ["Cline is VSCode extension, OpenHands is CLI"]
    },
    {
      "repo": "Aider",
      "similarity": 0.63,
      "common": ["Code editing", "Git integration"],
      "differences": ["Aider is terminal-first"]
    }
  ]
}
```

---

## Stage 6: Evolution Tracker

### Purpose

Track repository changes over time.

### Tracking

```python
class EvolutionTracker:
    """Track repository evolution."""
    
    def track(self, repo: str, new_features: FeatureSet):
        """Track changes in repository."""
        
        old_features = self.get_last_snapshot(repo)
        
        if old_features is None:
            # First analysis
            self._create_snapshot(repo, new_features)
            return
        
        # Find changes
        changes = self._find_changes(old_features, new_features)
        
        if changes:
            # Store change
            self._store_evolution(repo, changes)
            
            # Update snapshot
            self._create_snapshot(repo, new_features)
            
            # Alert if significant
            if self._is_significant(changes):
                self._alert(changes)
    
    def _find_changes(self, old: FeatureSet, new: FeatureSet) -> List[Change]:
        """Find what changed."""
        
        changes = []
        
        # New features
        for key, feature in new.features.items():
            if key not in old.features:
                changes.append(Change(
                    type="NEW_FEATURE",
                    feature=key,
                    value=feature.value
                ))
        
        # Removed features
        for key in old.features:
            if key not in new.features:
                changes.append(Change(
                    type="REMOVED_FEATURE",
                    feature=key
                ))
        
        # Changed features
        for key in old.features & new.features:
            if old.features[key].value != new.features[key].value:
                changes.append(Change(
                    type="CHANGED_FEATURE",
                    feature=key,
                    old_value=old.features[key].value,
                    new_value=new.features[key].value
                ))
        
        return changes
```

### Evolution Events

```yaml
EvolutionEvents:
  - type: NEW_CAPABILITY
    example: "Added MCP support"
    
  - type: NEW_PROVIDER
    example: "Added Gemini support"
    
  - type: NEW_ARCHITECTURE
    example: "Switched from single to multi-agent"
    
  - type: REMOVED_CAPABILITY
    example: "Removed Docker support"
    
  - type: ARCHITECTURE_CHANGE
    example: "Added memory system"
    
  - type: VERSION_CHANGE
    example: "Upgraded to v2.0"
```

---

## Stage 7: Confidence Engine

### Purpose

Assign confidence to all features.

### Confidence Levels

```yaml
ConfidenceLevels:
  0.95-1.00: VERY_HIGH
    - Direct import detection
    - Config file presence
    - File pattern match
    
  0.80-0.95: HIGH
    - Pattern in code
    - Documentation mention
    - Indirect detection
    
  0.60-0.80: MEDIUM
    - Heuristic detection
    - Partial match
    - Inference
    
  0.40-0.60: LOW
    - Weak signal
    - Multiple possibilities
    - Uncertain
    
  0.00-0.40: VERY_LOW
    - Very weak signal
    - Likely false positive
    - Needs verification
```

### Confidence Rules

```python
class ConfidenceEngine:
    """Calculate feature confidence."""
    
    def calculate(self, feature: str, evidence: List[str]) -> float:
        """Calculate confidence for a feature."""
        
        confidence = 1.0
        
        # Evidence types
        evidence_weights = {
            "import": 1.0,
            "config": 0.95,
            "file": 0.90,
            "documentation": 0.80,
            "code": 0.85,
            "inference": 0.50
        }
        
        for ev in evidence:
            weight = evidence_weights.get(ev.type, 0.5)
            confidence *= weight
        
        # Number of evidence
        if len(evidence) > 3:
            confidence *= 1.1  # Boost
        elif len(evidence) == 0:
            confidence *= 0.1  # Penalty
        
        return min(confidence, 1.0)
    
    def filter_by_confidence(
        self, 
        features: FeatureSet, 
        threshold: float = 0.6
    ) -> FeatureSet:
        """Filter features by confidence."""
        
        filtered = {
            k: v for k, v in features.features.items()
            if v.confidence >= threshold
        }
        
        return FeatureSet(
            repository=features.repository,
            features=filtered,
            detected_at=features.detected_at,
            detectors_used=features.detectors_used
        )
```

---

## Stage 8: Benchmark Queue

### Purpose

Queue repos for ARI benchmarking.

### Queue Rules

```python
class BenchmarkQueue:
    """Queue repositories for benchmarking."""
    
    def should_benchmark(self, repo: FeatureSet) -> bool:
        """Should this repo be benchmarked?"""
        
        # Must have executable capabilities
        if not repo.has_capabilities:
            return False
        
        # Must have CI/CD
        if not repo.has_cicd:
            return False
        
        # Must have tests
        if not repo.has_tests:
            return False
        
        # Priority based on stars
        stars = repo.github_data.stars
        
        if stars > 10000:
            priority = "HIGH"
        elif stars > 1000:
            priority = "MEDIUM"
        else:
            priority = "LOW"
        
        return True
    
    def enqueue(self, repo: str, priority: str):
        """Add to benchmark queue."""
        
        queue_item = QueueItem(
            repo=repo,
            priority=priority,
            queued_at=datetime.utcnow(),
            status="PENDING"
        )
        
        self.queue.add(queue_item)
        
        # Trigger ARI benchmark
        if priority == "HIGH":
            self._trigger_ari_benchmark(repo)
```

---

## Query Interface

### What You Can Ask

```python
# Q1: All repos supporting MCP
repos = graph.query("""
    MATCH (r:REPOSITORY)-[:USES]->(f:FEATURE {name: 'mcp'})
    RETURN r.name
""")

# Q2: All repos using multiple models
repos = graph.query("""
    MATCH (r:REPOSITORY)
    WHERE size([(r)-[:USES]->(:FEATURE {name: 'claude'})]) > 0
      AND size([(r)-[:USES]->(:FEATURE {name: 'gpt4'})]) > 0
    RETURN r.name
""")

# Q3: Best 20 planning repos
repos = graph.query("""
    MATCH (r:REPOSITORY)-[:USES]->(:FEATURE {name: 'planner'})
    RETURN r.name
    ORDER BY r.stars DESC
    LIMIT 20
""")

# Q4: Cloud-only repos
repos = graph.query("""
    MATCH (r:REPOSITORY)-[:USES]->(:FEATURE {name: 'cloud_deployment'})
    WHERE NOT (r)-[:USES]->(:FEATURE {name: 'local_only'})
    RETURN r.name
""")

# Q5: Unique capabilities
repos = graph.query("""
    MATCH (r:REPOSITORY)-[:USES]->(f:FEATURE)
    WHERE NOT EXISTS {
        MATCH (other:REPOSITORY)-[:USES]->(f)
        WHERE other <> r
    }
    RETURN r.name, f.name as unique_feature
""")
```

---

## Repository Structure

```
rpp-pipeline/
├── 01-Normalizer/
│   └── README.md
├── 02-Marketplace/
│   └── README.md
├── 03-Features/
│   └── README.md
├── 04-Knowledge-Graph/
│   └── README.md
├── 05-Similarity/
│   └── README.md
├── 06-Evolution/
│   └── README.md
├── 07-Confidence/
│   └── README.md
├── 08-Benchmark-Queue/
│   └── README.md
└── README.md (this file)
```

---

## Success Metrics

```
1500 repos analyzed
     │
     ▼
ONE unified knowledge base
     │
     ▼
Queryable knowledge graph
     │
     ▼
Similarity engine working
     │
     ▼
Evolution tracking active
     │
     ▼
Benchmark queue flowing
```

---

## The Transformation

```
OLD:
  "We have 1500 repositories"
  
NEW:
  "We have 1500 data points"
  "We have a queryable knowledge graph"
  "We have similarity scores"
  "We have evolution tracking"
  "We have a competitive intelligence system"
```

---

*From repositories to knowledge.*
*From analysis to intelligence.*
