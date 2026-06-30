# Capability Taxonomy

> **Instead of 1000 projects → 50 Capabilities**

## Taxonomy Overview

We categorize all tools by what they CAN DO, not by their NAME.

```
All Tools in Ecosystem
        ↓
Classified by Capabilities
        ↓
50 Universal Capabilities
        ↓
Any Tool → Capability Mapping
```

---

## Level 1: Domain Categories

| Domain | Description |
|--------|-------------|
| **Code** | Code generation, editing, and analysis |
| **Execution** | Running code, commands, and processes |
| **Web** | Browser interaction and web scraping |
| **Collaboration** | PRs, issues, and team workflows |
| **Infrastructure** | Deployment, cloud, and DevOps |
| **Data** | Databases, files, and storage |
| **Observability** | Logging, monitoring, and analytics |
| **Security** | Authentication, authorization, and scanning |

---

## Level 2: Capability Groups

### Code Domain

```
Code
  ├── Generation
  │     ├── Generate Backend
  │     ├── Generate Frontend
  │     ├── Generate Scripts
  │     ├── Generate Tests
  │     ├── Generate Config
  │     └── Generate Documentation
  │
  ├── Editing
  │     ├── Edit Code
  │     ├── Refactor Code
  │     ├── Fix Bugs
  │     └── Optimize Code
  │
  ├── Analysis
  │     ├── Understand Repository
  │     ├── Analyze Code Structure
  │     ├── Find Dependencies
  │     └── Detect Patterns
  │
  └── Quality
        ├── Review Code
        ├── Lint Code
        ├── Format Code
        └── Validate Code
```

### Execution Domain

```
Execution
  ├── Shell
  │     ├── Execute Shell
  │     ├── Run Scripts
  │     └── Manage Processes
  │
  ├── API
  │     ├── Call API
  │     ├── Build API Client
  │     └── Mock API
  │
  └── Container
        ├── Run Docker
        ├── Build Image
        └── Manage Container
```

### Web Domain

```
Web
  ├── Browser
  │     ├── Open Browser
  │     ├── Click Element
  │     ├── Fill Form
  │     ├── Take Screenshot
  │     └── Extract Content
  │
  ├── Search
  │     ├── Search Web
  │     ├── Search Docs
  │     └── Search Code
  │
  └── Interaction
        ├── Wait for Element
        ├── Handle Popup
        └── Scroll Page
```

### Collaboration Domain

```
Collaboration
  ├── GitHub
  │     ├── Create PR
  │     ├── Merge PR
  │     ├── Review PR
  │     ├── Create Issue
  │     └── Manage Labels
  │
  ├── Communication
  │     ├── Send Message
  │     ├── Send Email
  │     └── Post Update
  │
  └── Task Management
        ├── Create Task
        ├── Update Task
        ├── Assign Task
        └── Track Progress
```

### Infrastructure Domain

```
Infrastructure
  ├── Deployment
  │     ├── Deploy to Cloud
  │     ├── Deploy to Container
  │     ├── Rollback
  │     └── Scale
  │
  ├── Configuration
  │     ├── Manage Config
  │     ├── Set Environment
  │     └── Configure Secrets
  │
  └── CI/CD
        ├── Run Pipeline
        ├── Run Tests
        ├── Build Artifact
        └── Publish Release
```

### Data Domain

```
Data
  ├── Database
  │     ├── Query Database
  │     ├── Write to Database
  │     ├── Migrate Schema
  │     └── Backup Database
  │
  ├── File System
  │     ├── Read File
  │     ├── Write File
  │     ├── Delete File
  │     └── List Directory
  │
  └── Storage
        ├── Upload File
        ├── Download File
        └── Manage Bucket
```

### Observability Domain

```
Observability
  ├── Logging
  │     ├── Write Log
  │     ├── Read Logs
  │     └── Search Logs
  │
  ├── Monitoring
  │     ├── Get Metrics
  │     ├── Set Alert
  │     └── Check Health
  │
  └── Tracing
        ├── Start Trace
        ├── Add Span
        └── View Trace
```

### Security Domain

```
Security
  ├── Authentication
  │     ├── Authenticate
  │     ├── Manage Token
  │     └── OAuth Flow
  │
  ├── Authorization
  │     ├── Check Permission
  │     ├── Manage Access
  │     └── Audit Access
  │
  └── Scanning
        ├── Scan Vulnerabilities
        ├── Scan Secrets
        └── Scan Dependencies
```

---

## Complete Capability Index

| ID | Capability | Category | Domain | Tools Available |
|----|------------|----------|--------|----------------|
| CAP-001 | Generate Backend | Generation | Code | 5+ tools |
| CAP-002 | Generate Frontend | Generation | Code | 5+ tools |
| CAP-003 | Generate Scripts | Generation | Code | 8+ tools |
| CAP-004 | Generate Tests | Generation | Code | 4+ tools |
| CAP-005 | Generate Config | Generation | Code | 3+ tools |
| CAP-006 | Generate Documentation | Generation | Code | 3+ tools |
| CAP-007 | Edit Code | Editing | Code | 6+ tools |
| CAP-008 | Refactor Code | Editing | Code | 5+ tools |
| CAP-009 | Fix Bugs | Editing | Code | 4+ tools |
| CAP-010 | Optimize Code | Editing | Code | 3+ tools |
| CAP-011 | Understand Repository | Analysis | Code | 4+ tools |
| CAP-012 | Analyze Code Structure | Analysis | Code | 5+ tools |
| CAP-013 | Find Dependencies | Analysis | Code | 4+ tools |
| CAP-014 | Detect Patterns | Analysis | Code | 3+ tools |
| CAP-015 | Review Code | Quality | Code | 5+ tools |
| CAP-016 | Lint Code | Quality | Code | 6+ tools |
| CAP-017 | Format Code | Quality | Code | 7+ tools |
| CAP-018 | Validate Code | Quality | Code | 4+ tools |
| CAP-019 | Execute Shell | Shell | Execution | 8+ tools |
| CAP-020 | Run Scripts | Shell | Execution | 6+ tools |
| CAP-021 | Manage Processes | Shell | Execution | 4+ tools |
| CAP-022 | Call API | API | Execution | 5+ tools |
| CAP-023 | Build API Client | API | Execution | 3+ tools |
| CAP-024 | Run Docker | Container | Execution | 5+ tools |
| CAP-025 | Build Image | Container | Execution | 4+ tools |
| CAP-026 | Open Browser | Browser | Web | 4+ tools |
| CAP-027 | Click Element | Browser | Web | 4+ tools |
| CAP-028 | Fill Form | Browser | Web | 4+ tools |
| CAP-029 | Extract Content | Browser | Web | 5+ tools |
| CAP-030 | Search Web | Search | Web | 6+ tools |
| CAP-031 | Search Docs | Search | Web | 4+ tools |
| CAP-032 | Create PR | GitHub | Collaboration | 5+ tools |
| CAP-033 | Merge PR | GitHub | Collaboration | 4+ tools |
| CAP-034 | Review PR | GitHub | Collaboration | 5+ tools |
| CAP-035 | Create Issue | GitHub | Collaboration | 4+ tools |
| CAP-036 | Send Message | Communication | Collaboration | 3+ tools |
| CAP-037 | Deploy to Cloud | Deployment | Infrastructure | 4+ tools |
| CAP-038 | Rollback | Deployment | Infrastructure | 3+ tools |
| CAP-039 | Run Pipeline | CI/CD | Infrastructure | 5+ tools |
| CAP-040 | Build Artifact | CI/CD | Infrastructure | 4+ tools |
| CAP-041 | Query Database | Database | Data | 5+ tools |
| CAP-042 | Write to Database | Database | Data | 4+ tools |
| CAP-043 | Read File | File System | Data | 8+ tools |
| CAP-044 | Write File | File System | Data | 8+ tools |
| CAP-045 | Upload File | Storage | Data | 4+ tools |
| CAP-046 | Write Log | Logging | Observability | 5+ tools |
| CAP-047 | Read Logs | Logging | Observability | 4+ tools |
| CAP-048 | Get Metrics | Monitoring | Observability | 4+ tools |
| CAP-049 | Check Health | Monitoring | Observability | 5+ tools |
| CAP-050 | Scan Vulnerabilities | Scanning | Security | 4+ tools |

---

## Capability to Tools Mapping

```yaml
"Generate Backend":
  - OpenHands
  - Aider
  - Claude Code
  - Goose
  - Cline

"Edit Code":
  - Aider
  - Cline
  - VS Code AI
  - Cursor
  - OpenHands

"Use Browser":
  - Browser Use
  - Claude Computer Use
  - Playwright Agent
  - Selenium Agent

"Understand Repository":
  - SWE-Agent
  - Tree-sitter
  - AST analysis tools
  - OpenHands

"Create PR":
  - GitHub CLI
  - PR agents
  - OpenHands
  - Claude Code

"Deploy":
  - OpenHands
  - GitHub Actions
  - Terraform
  - Pulumi agents
```

---

## Related Documents

- [02-Capability-Graph](./02-Capability-Graph.md) - Capability Graph
- [04-Tools](../04-Tools/README.md) - Tool Registry
- [03-Agents/02-Agent-Capability-Database](../03-Agents/02-Agent-Capability-Database.md) - Agent Database
