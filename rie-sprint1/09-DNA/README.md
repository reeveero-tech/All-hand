# DNA Generator

## Overview

Combines all detector results into a unified Repository DNA.

## DNA Schema

```python
@dataclass
class RepositoryDNA:
    """Repository DNA - unified representation."""
    
    # Metadata
    version: str = "1.0.0"
    generated_at: datetime
    
    # Repository Info
    repository: RepositoryInfo
    
    # Detection Results
    category: str
    architecture: ArchitectureInfo
    technologies: TechnologyInfo
    ai: AIInfo
    capabilities: List[str]
    tools: List[str]
    quality: QualityInfo
    risks: List[RiskInfo]
    
    # Summary
    summary: str
    score: float
    
    # Raw data
    detectors_used: List[str]
    confidence: float

@dataclass
class RepositoryInfo:
    name: str
    url: str
    description: str
    stars: int
    forks: int
    language: str
    license: str
    created_at: str
    last_commit: str
    contributors: int

@dataclass
class ArchitectureInfo:
    pattern: str
    components: List[str]
    confidence: float

@dataclass
class TechnologyInfo:
    languages: List[str]
    frameworks: List[str]
    databases: List[str]
    infrastructure: List[str]

@dataclass
class AIInfo:
    uses_llm: bool
    models: List[str]
    providers: List[str]
    supports_multiple: bool
    litellm: bool
    mcp: bool
    local_models: bool

@dataclass
class QualityInfo:
    score: int
    test_coverage: int
    documentation: int
    activity: str
    maintenance: str

@dataclass
class RiskInfo:
    type: str
    level: str
    description: str
```

## Example Output

```json
{
  "version": "1.0.0",
  "generated_at": "2024-01-15T10:30:00Z",
  
  "repository": {
    "name": "OpenHands",
    "url": "https://github.com/All-Hands-AI/OpenHands",
    "description": "OpenHands is an AI Agent...",
    "stars": 25000,
    "forks": 3000,
    "language": "Python",
    "license": "MIT",
    "contributors": 150
  },
  
  "category": "Coding Agent",
  "architecture": {
    "pattern": "Multi-Agent",
    "components": ["Planner", "Executor", "Memory", "Router"],
    "confidence": 0.95
  },
  
  "technologies": {
    "languages": ["Python", "TypeScript"],
    "frameworks": ["FastAPI", "React", "LangChain"],
    "databases": ["PostgreSQL", "Redis"],
    "infrastructure": ["Docker", "Kubernetes", "GitHub Actions"]
  },
  
  "ai": {
    "uses_llm": true,
    "models": ["Claude", "GPT-4", "Gemini"],
    "providers": ["Anthropic", "OpenAI", "Google"],
    "supports_multiple": true,
    "litellm": true,
    "mcp": true,
    "local_models": false
  },
  
  "capabilities": [
    "Generate Code",
    "Edit Code",
    "Review Code",
    "Testing",
    "Deployment",
    "Browser",
    "Git",
    "Docker"
  ],
  
  "tools": [
    "GitHub",
    "Docker",
    "Playwright",
    "Git",
    "Slack"
  ],
  
  "quality": {
    "score": 85,
    "test_coverage": 75,
    "documentation": 90,
    "activity": "High",
    "maintenance": "Active"
  },
  
  "risks": [
    {
      "type": "dependency",
      "level": "LOW",
      "description": "Dependencies are up to date"
    }
  ],
  
  "summary": "OpenHands is a multi-agent coding assistant...",
  "score": 92,
  
  "detectors_used": [
    "Technology", "AI", "Architecture", 
    "Capability", "Tool", "Quality", "Risk"
  ],
  "confidence": 0.88
}
```

## Implementation

```python
class DNAGenerator:
    """Generates repository DNA from detector results."""
    
    def generate(
        self,
        snapshot: Snapshot,
        detections: Dict[str, DetectionResult]
    ) -> RepositoryDNA:
        """Generate DNA from all detections."""
        
        # Combine all results
        tech = detections["technology"]
        ai = detections["ai"]
        arch = detections["architecture"]
        cap = detections["capability"]
        tools = detections["tools"]
        quality = detections["quality"]
        risk = detections["risk"]
        
        # Generate DNA
        dna = RepositoryDNA(
            version="1.0.0",
            generated_at=datetime.utcnow(),
            
            repository=RepositoryInfo(
                name=snapshot.name,
                url=snapshot.url,
                description=snapshot.description,
                stars=snapshot.stars,
                forks=snapshot.forks,
                language=tech.languages[0] if tech.languages else "Unknown",
                license=snapshot.license,
                created_at=snapshot.created_at,
                last_commit=snapshot.last_commit,
                contributors=snapshot.contributors
            ),
            
            category=self._determine_category(cap),
            architecture=self._summarize_architecture(arch),
            technologies=self._summarize_technologies(tech),
            ai=self._summarize_ai(ai),
            capabilities=cap.capabilities,
            tools=tools.tools,
            quality=self._summarize_quality(quality),
            risks=risk.risks,
            
            summary=self._generate_summary(detections),
            score=self._calculate_score(detections),
            
            detectors_used=list(detections.keys()),
            confidence=self._calculate_confidence(detections)
        )
        
        return dna
```

## Scoring Algorithm

```python
def calculate_score(self, detections: Dict) -> float:
    """Calculate overall repository score."""
    
    scores = []
    
    # Quality score (30%)
    quality = detections["quality"].score
    scores.append(("quality", quality, 0.30))
    
    # AI sophistication (25%)
    ai = detections["ai"]
    ai_score = self._score_ai_sophistication(ai)
    scores.append(("ai", ai_score, 0.25))
    
    # Architecture (20%)
    arch = detections["architecture"]
    arch_score = arch.confidence * 100
    scores.append(("architecture", arch_score, 0.20))
    
    # Capabilities (15%)
    cap = detections["capability"]
    cap_score = len(cap.capabilities) * 5  # 5 points per capability
    cap_score = min(cap_score, 100)
    scores.append(("capabilities", cap_score, 0.15))
    
    # Activity (10%)
    activity = detections["quality"].activity
    activity_score = {"High": 100, "Medium": 70, "Low": 40}[activity]
    scores.append(("activity", activity_score, 0.10))
    
    # Weighted average
    total = sum(s * w for _, s, w in scores)
    
    return round(total, 1)
```

## Save Formats

```python
class RepositoryDNA:
    """Repository DNA with export methods."""
    
    def to_json(self) -> str:
        """Export as JSON."""
        return json.dumps(asdict(self), indent=2)
    
    def to_yaml(self) -> str:
        """Export as YAML."""
        return yaml.dump(asdict(self))
    
    def to_markdown(self) -> str:
        """Export as Markdown report."""
        return self._generate_markdown()
    
    def save(self, path: Path):
        """Save to file."""
        path.write_text(self.to_json())
```

---

*DNA Generator combines everything into a unified output.*
