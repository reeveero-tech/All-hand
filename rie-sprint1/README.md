# RIE: Repository Intelligence Engine v0.1

> **The First Real Project in AGOS Ecosystem**

---

## Vision

```
Input:
  https://github.com/All-Hands-AI/OpenHands
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│                   RIE (Repository Intelligence Engine)               │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  Fetcher                                               │ │
│  │  Clone repository, download files                     │ │
│  └─────────────────────────────────────────────────────┘ │
│                           │                                │
│                           ▼                                │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  DETECTORS                                            │ │
│  │                                                       │ │
│  │  ├── Technology Detector                               │ │
│  │  ├── AI Detector                                       │ │
│  │  ├── Architecture Detector                             │ │
│  │  ├── Capability Detector                              │ │
│  │  ├── Provider Detector                                │ │
│  │  ├── Tool Detector                                    │ │
│  │  ├── Quality Detector                                 │ │
│  │  └── Risk Detector                                    │ │
│  └─────────────────────────────────────────────────────┘ │
│                           │                                │
│                           ▼                                │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  DNA Generator                                        │ │
│  │  Produce unified repository DNA                       │ │
│  └─────────────────────────────────────────────────────┘ │
│                           │                                │
│                           ▼                                │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  Knowledge Graph                                      │ │
│  │  Store for future reference                          │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
Output:
  Repository DNA (JSON)
  Architecture
  Capabilities
  Providers
  Models
  Tools
  Dependencies
  Quality
  Activity
  Risk
  Summary
  Score
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        RIE Architecture                                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Repository URL                                              │
│       │                                                      │
│       ▼                                                      │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  Fetcher                                              │ │
│  │  • Git Clone                                          │ │
│  │  • File Download                                      │ │
│  │  • Snapshot Creation                                  │ │
│  └─────────────────────────────────────────────────────┘ │
│                           │                                │
│                           ▼                                │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  File Classifier                                      │ │
│  │  • Source Code                                        │ │
│  │  • Configuration                                      │ │
│  │  • Documentation                                      │ │
│  │  • Tests                                              │ │
│  │  • AI Prompts                                         │ │
│  └─────────────────────────────────────────────────────┘ │
│                           │                                │
│                           ▼                                │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  DETECTORS (Parallel)                                 │ │
│  │                                                       │ │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐  │ │
│  │  │ Technology  │ │     AI     │ │Architecture │  │ │
│  │  └─────────────┘ └─────────────┘ └─────────────┘  │ │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐  │ │
│  │  │ Capability  │ │  Provider  │ │    Tool    │  │ │
│  │  └─────────────┘ └─────────────┘ └─────────────┘  │ │
│  │  ┌─────────────┐ ┌─────────────┐                  │ │
│  │  │  Quality   │ │    Risk    │                   │ │
│  │  └─────────────┘ └─────────────┘                  │ │
│  └─────────────────────────────────────────────────────┘ │
│                           │                                │
│                           ▼                                │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  DNA Generator                                       │ │
│  │  • Combine all detector results                     │ │
│  │  • Generate unified DNA                             │ │
│  │  • Calculate score                                  │ │
│  └─────────────────────────────────────────────────────┘ │
│                           │                                │
│                           ▼                                │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  Output                                               │ │
│  │  • JSON Report                                       │ │
│  │  • Knowledge Graph                                   │ │
│  │  • Summary                                           │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 10 Detectors

### 1. Fetcher (Required First)

```
Responsibility: Download repository
Input: GitHub URL
Output: Repository Snapshot

Files to fetch:
  - README.md
  - LICENSE
  - package.json
  - pyproject.toml
  - Cargo.toml
  - go.mod
  - requirements.txt
  - Dockerfile
  - docker-compose.yml
  - workflows/*.yml
  - src/**/*
  - docs/**/*
  - examples/**/*
  - config/**/*
  - .github/**/*
```

### 2. File Classifier

```
Classifies files into categories:
  - Source Code (.py, .ts, .js, .go, .rs)
  - Configuration (.json, .yaml, .toml, .env)
  - Documentation (.md, .rst, .txt)
  - Tests (test_*, *_test.py, *.test.ts)
  - Assets (images, fonts, etc.)
  - Examples (examples/, demo/)
  - Scripts (scripts/, tools/)
  - CI/CD (.github/, .gitlab-ci.yml)
  - Infrastructure (terraform, ansible)
  - AI Prompts (prompts/, system/)
  - Templates (templates/, templates/)
```

### 3. Technology Detector

```
Detects:
  Languages:
    - Python, TypeScript, JavaScript, Go, Rust
    - Java, C#, Ruby, PHP, Swift, Kotlin
    
  Frameworks:
    - React, Vue, Angular, Next.js, Nuxt
    - FastAPI, Django, Flask, Express
    - Spring, Rails, Laravel
    
  Databases:
    - PostgreSQL, MySQL, MongoDB, Redis
    - Elasticsearch, Neo4j, SQLite
    
  Web Servers:
    - Nginx, Apache, Caddy
    
  Package Managers:
    - npm, yarn, pnpm
    - pip, poetry, pipenv
    - cargo, go modules
    
  Cloud Providers:
    - AWS, GCP, Azure
    - Vercel, Netlify, Railway
    
  Container Systems:
    - Docker, Kubernetes, Podman
    
  CI Systems:
    - GitHub Actions, GitLab CI, Jenkins
    - CircleCI, TravisCI
    
  Build Systems:
    - Make, CMake, Bazel, Gradle
```

### 4. AI Detector

```
Questions answered:
  - Does it use LLM?
  - Which LLM?
  - Multiple models support?
  - LiteLLM?
  - MCP (Model Context Protocol)?
  - OpenRouter?
  - Tool use?
  - Agent framework?
  
Patterns to detect:
  - openai, anthropic, google
  - litellm, langchain, llamaindex
  - vertexai, bedrock, azure openai
  - ollama, gpt4all, local models
```

### 5. Architecture Detector

```
Architecture patterns detected:
  - Single Agent
  - Multi Agent
  - Workflow
  - Planner / Executor
  - Router
  - Memory System
  - Event Bus
  - Microservices
  - Monolith
  
Patterns to detect:
  - agent.py, agents/
  - planner.py, executor.py
  - router.py, dispatcher.py
  - memory.py, store.py
  - event.py, bus.py
  - service.py, worker.py
```

### 6. Capability Detector (Most Important)

```
Capabilities detected:
  - Generate Code
  - Edit Code
  - Review Code
  - Testing
  - Refactoring
  - Planning
  - Deployment
  - Search
  - Browser
  - Git
  - Docker
  - Memory
  - Terminal
  - Filesystem
  - Documentation
  - Security
  
Methods:
  - Function names
  - Import patterns
  - Configuration
  - README mentions
  - Test coverage
```

### 7. Provider Detector

```
Providers detected:
  - Claude (Anthropic)
  - GPT-4 (OpenAI)
  - Gemini (Google)
  - DeepSeek
  - Llama (Meta)
  - Qwen (Alibaba)
  - Mistral
  - Groq
  - Ollama
  - Together
  - Fireworks
  - Azure OpenAI
  - AWS Bedrock
  - Vertex AI
  
Detection methods:
  - Import statements
  - API calls
  - Configuration
  - Documentation
```

### 8. Tool Detector

```
Tools detected:
  - GitHub
  - GitLab
  - Docker
  - Kubernetes
  - Playwright
  - Puppeteer
  - Browser automation
  - Slack
  - Discord
  - Jira
  - Notion
  - Linear
  - VSCode
  - Cursor
  
Detection:
  - Dependencies
  - Configuration
  - Integration code
```

### 9. Quality Detector

```
Metrics measured:
  - Stars
  - Forks
  - Open Issues
  - Open PRs
  - Commit frequency
  - Release frequency
  - Contributors
  - Test coverage
  - Documentation quality
  - License type
  - Activity level
  
Sources:
  - GitHub API
  - Code analysis
  - File presence
```

### 10. Risk Detector

```
Risks identified:
  - Security vulnerabilities
  - Dependency issues
  - Maintenance status
  - Activity level
  - Community size
  - License compatibility
  - Code quality
  - Technical debt
  
Risk levels:
  - LOW
  - MEDIUM
  - HIGH
  - CRITICAL
```

---

## DNA Output Schema

```json
{
  "version": "1.0.0",
  "generated_at": "2024-01-15T10:30:00Z",
  "repository": {
    "name": "OpenHands",
    "url": "https://github.com/All-Hands-AI/OpenHands",
    "description": "...",
    "stars": 25000,
    "forks": 3000,
    "language": "Python",
    "license": "MIT",
    "created_at": "2024-01-01",
    "last_commit": "2024-01-14"
  },
  "category": "Coding Agent",
  "architecture": {
    "pattern": "Multi-Agent",
    "components": [
      "Planner",
      "Executor",
      "Memory",
      "Router"
    ],
    "confidence": 0.95
  },
  "technologies": {
    "languages": ["Python", "TypeScript"],
    "frameworks": ["FastAPI", "React"],
    "databases": ["PostgreSQL"],
    "infrastructure": ["Docker", "Kubernetes"]
  },
  "ai": {
    "uses_llm": true,
    "models": ["Claude", "GPT-4"],
    "providers": ["Anthropic", "OpenAI"],
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
    "Git"
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
      "description": "..."
    }
  ],
  "summary": "OpenHands is a multi-agent coding assistant...",
  "score": 92
}
```

---

## Detectors Interface

```python
class Detector(ABC):
    """Base interface for all detectors."""
    
    @property
    def name(self) -> str:
        """Detector name."""
        pass
        
    @property
    def priority(self) -> int:
        """Detection priority (1 = highest)."""
        pass
        
    @abstractmethod
    def detect(self, snapshot: Snapshot) -> DetectionResult:
        """Run detection on repository snapshot."""
        pass
```

---

## Snapshot Structure

```python
@dataclass
class Snapshot:
    """Repository snapshot."""
    
    url: str
    local_path: Path
    fetched_at: datetime
    
    # File categories
    source_files: Dict[str, List[Path]]
    config_files: Dict[str, List[Path]]
    docs_files: Dict[str, List[Path]]
    test_files: Dict[str, List[Path]]
    
    # Key files
    readme: Optional[str]
    license: Optional[str]
    package_json: Optional[Dict]
    pyproject: Optional[Dict]
    requirements: Optional[List[str]]
    dockerfile: Optional[str]
    docker_compose: Optional[Dict]
    workflows: List[Dict]
    
    # Metadata
    github_data: Optional[GitHubMetadata]
```

---

## Usage Example

```python
# Initialize RIE
rie = RIE()

# Analyze repository
result = await rie.analyze(
    url="https://github.com/All-Hands-AI/OpenHands"
)

# Get DNA
dna = result.dna

print(f"Repository: {dna.repository.name}")
print(f"Architecture: {dna.architecture.pattern}")
print(f"Capabilities: {dna.capabilities}")
print(f"Score: {dna.score}")

# Export
dna.save_json("openhands-dna.json")
dna.save_knowledge_graph("openhands-kg.json")
```

---

## Repository Structure

```
rie-sprint1/
├── 01-Spec/              # This file
├── 02-Fetcher/           # Repository fetcher
├── 03-Technology/        # Technology detector
├── 04-AI-Detector/      # AI/LLM detector
├── 05-Architecture/     # Architecture detector
├── 06-Capability/        # Capability detector
├── 07-Tools/            # Tool detector
├── 08-Quality/          # Quality detector
├── 09-DNA/              # DNA generator
└── 10-Tests/            # Tests
```

---

## Golden Rule

```
No code specific to any repository.
All detectors are general-purpose.
Tomorrow we can analyze:
  - OpenHands
  - Cline
  - Goose
  - Devin-like
  - Aider
  - Any new project

Without modifying the core.
```

---

## Success Criteria

```
1. Can analyze any GitHub repository
2. Produces consistent DNA format
3. All 10 detectors work
4. No hardcoded repository-specific logic
5. Can be extended with new detectors
6. Output is machine-readable
7. Can be integrated with AGOS
```

---

## Next Steps

```
Sprint 1: RIE v0.1 (NOW)
  - All 10 detectors
  - Basic DNA output
  
Sprint 2: RIE v0.2
  - Knowledge Graph integration
  - Batch analysis
  
Sprint 3: RIE v1.0
  - Production ready
  - AGOS integration
```

---

*First real project in AGOS ecosystem.*
*Analyze any repository. Generate DNA. Feed the brain.*
