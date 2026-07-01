# Capability Detector

## Overview

Detects capabilities/features in repositories. **Most important detector.**

## Capability Taxonomy

```yaml
Capabilities:
  Code Generation:
    - Generate Code
    - Edit Code
    - Refactor Code
    - Complete Code
    
  Testing:
    - Unit Testing
    - Integration Testing
    - E2E Testing
    - Performance Testing
    
  Review:
    - Code Review
    - Security Review
    - Performance Review
    
  Deployment:
    - Build
    - Deploy
    - Rollback
    - Containerize
    
  Analysis:
    - Static Analysis
    - Dynamic Analysis
    - Code Analysis
    
  Planning:
    - Task Planning
    - Project Planning
    - Sprint Planning
    
  Memory:
    - Short-term Memory
    - Long-term Memory
    - Context Management
    
  Tools:
    - Git Operations
    - Docker Operations
    - Browser Operations
    - Terminal Operations
    - File Operations
    - Search Operations
    
  Documentation:
    - Generate Docs
    - Update Docs
    - Format Docs
```

## Detection Patterns

```yaml
Code Generation:
  patterns:
    - "def generate"
    - "def create"
    - "def build"
    - "scaffold"
    - "template"
  files:
    - "generator.py"
    - "creator.py"
    
Testing:
  patterns:
    - "test_"
    - "_test.py"
    - "pytest"
    - "unittest"
    - "vitest"
    - "jest"
  files:
    - "test_*.py"
    - "*_test.py"
    
Deployment:
  patterns:
    - "docker"
    - "deploy"
    - "kubernetes"
    - "helm"
    - "ci/cd"
  files:
    - "Dockerfile"
    - "docker-compose"
    - ".github/workflows"
    
Memory:
  patterns:
    - "memory"
    - "context"
    - "history"
    - "cache"
  files:
    - "memory.py"
    - "context.py"
    
Planning:
  patterns:
    - "planner"
    - "plan"
    - "task"
    - "分解"
  files:
    - "planner.py"
    - "task*.py"
```

## Output Schema

```python
@dataclass
class CapabilityDetection:
    """Capability detection result."""
    
    capabilities: List[str]
    details: Dict[str, CapabilityDetail]
    
    # Specific capabilities
    can_generate_code: bool
    can_edit_code: bool
    can_review_code: bool
    can_test: bool
    can_deploy: bool
    can_plan: bool
    can_use_memory: bool
    can_use_git: bool
    can_use_docker: bool
    can_browse: bool
    
    confidence: float
    timestamp: datetime

@dataclass
class CapabilityDetail:
    confidence: float
    evidence: List[str]
    files: List[str]
```

## Implementation

```python
class CapabilityDetector:
    """Detects capabilities."""
    
    async def detect(self, snapshot: Snapshot) -> CapabilityDetection:
        """Detect all capabilities."""
        
        capabilities = []
        details = {}
        
        # Detect code generation
        cg = self._detect_code_generation(snapshot)
        if cg.detected:
            capabilities.append("Generate Code")
            details["Generate Code"] = cg
            
        # Detect testing
        testing = self._detect_testing(snapshot)
        if testing.detected:
            capabilities.append("Testing")
            details["Testing"] = testing
            
        # Detect deployment
        deploy = self._detect_deployment(snapshot)
        if deploy.detected:
            capabilities.append("Deployment")
            details["Deployment"] = deploy
            
        # Detect memory
        memory = self._detect_memory(snapshot)
        if memory.detected:
            capabilities.append("Memory")
            details["Memory"] = memory
            
        # Detect planning
        planning = self._detect_planning(snapshot)
        if planning.detected:
            capabilities.append("Planning")
            details["Planning"] = planning
            
        return CapabilityDetection(
            capabilities=capabilities,
            details=details,
            can_generate_code="Generate Code" in capabilities,
            can_edit_code="Edit Code" in capabilities,
            can_review_code="Code Review" in capabilities,
            can_test="Testing" in capabilities,
            can_deploy="Deployment" in capabilities,
            can_plan="Planning" in capabilities,
            can_use_memory="Memory" in capabilities,
            can_use_git=self._detect_git(snapshot),
            can_use_docker=self._detect_docker(snapshot),
            can_browse=self._detect_browser(snapshot),
            confidence=self._calculate_confidence(details),
            timestamp=datetime.utcnow()
        )
```

---

*Capability Detector is the most important detector.*
