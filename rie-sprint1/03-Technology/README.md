# Technology Detector

## Overview

Detects all technologies used in a repository.

## What It Detects

### Languages

```yaml
Languages:
  Python:
    patterns: ["*.py", "requirements.txt", "setup.py"]
    confidence: high
    
  TypeScript:
    patterns: ["*.ts", "*.tsx", "package.json"]
    confidence: high
    
  JavaScript:
    patterns: ["*.js", "*.jsx", "package.json"]
    confidence: high
    
  Go:
    patterns: ["*.go", "go.mod", "go.sum"]
    confidence: high
    
  Rust:
    patterns: ["*.rs", "Cargo.toml", "Cargo.lock"]
    confidence: high
    
  Java:
    patterns: ["*.java", "pom.xml", "build.gradle"]
    confidence: high
    
  C#:
    patterns: ["*.cs", "*.sln", "*.csproj"]
    confidence: high
    
  Ruby:
    patterns: ["*.rb", "Gemfile", "Gemfile.lock"]
    confidence: high
    
  PHP:
    patterns: ["*.php", "composer.json"]
    confidence: high
    
  Swift:
    patterns: ["*.swift", "Package.swift"]
    confidence: high
    
  Kotlin:
    patterns: ["*.kt", "build.gradle.kts"]
    confidence: high
```

### Frameworks

```yaml
Frameworks:
  Python:
    - FastAPI: ["fastapi", "uvicorn"]
    - Django: ["django"]
    - Flask: ["flask"]
    - PyTorch: ["torch", "pytorch"]
    - TensorFlow: ["tensorflow"]
    
  JavaScript:
    - React: ["react", "react-dom"]
    - Vue: ["vue"]
    - Angular: ["@angular/core"]
    - Next.js: ["next"]
    - Express: ["express"]
    
  Go:
    - Gin: ["gin-gonic/gin"]
    - Echo: ["labstack/echo"]
    - Fiber: ["gofiber/fiber"]
    
  TypeScript:
    - NestJS: ["@nestjs/core"]
    - Next.js: ["next"]
    - Remix: ["@remix-run/react"]
```

### Databases

```yaml
Databases:
  - PostgreSQL: ["postgresql", "postgres"]
  - MySQL: ["mysql"]
  - MongoDB: ["mongodb", "mongoose"]
  - Redis: ["redis"]
  - Elasticsearch: ["elasticsearch"]
  - Neo4j: ["neo4j"]
  - SQLite: ["sqlite"]
  - SQLite: ["sqlite3"]
```

### Infrastructure

```yaml
Infrastructure:
  Containers:
    - Docker: ["Dockerfile", "docker-compose"]
    - Kubernetes: ["kubernetes", "k8s", "helm"]
    
  Cloud:
    - AWS: ["boto3", "aws-sdk"]
    - GCP: ["google-cloud"]
    - Azure: ["azure"]
    
  CI/CD:
    - GitHub Actions: [".github/workflows"]
    - GitLab CI: [".gitlab-ci.yml"]
    - Jenkins: ["Jenkinsfile"]
    - CircleCI: [".circleci"]
```

## Output Schema

```python
@dataclass
class TechnologyDetection:
    """Technology detection result."""
    
    languages: List[DetectedLanguage]
    frameworks: List[DetectedFramework]
    databases: List[str]
    infrastructure: List[str]
    package_managers: List[str]
    build_tools: List[str]
    
    confidence: float
    timestamp: datetime
```

## Implementation

```python
class TechnologyDetector:
    """Detects technologies in repository."""
    
    async def detect(self, snapshot: Snapshot) -> TechnologyDetection:
        """Detect all technologies."""
        
        languages = []
        frameworks = []
        databases = []
        infrastructure = []
        
        # Detect from file patterns
        languages = self._detect_languages(snapshot)
        frameworks = self._detect_frameworks(snapshot)
        databases = self._detect_databases(snapshot)
        infrastructure = self._detect_infrastructure(snapshot)
        
        # Detect from imports
        imports = self._extract_imports(snapshot.source_files)
        frameworks.extend(self._detect_from_imports(imports))
        
        # Detect from config
        frameworks.extend(self._detect_from_config(snapshot))
        
        return TechnologyDetection(
            languages=languages,
            frameworks=frameworks,
            databases=databases,
            infrastructure=infrastructure,
            confidence=self._calculate_confidence(languages),
            timestamp=datetime.utcnow()
        )
```

---

*Technology Detector finds all tech in the repo.*
