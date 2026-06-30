# Repositories Database

> **Template for all repositories in the ecosystem.**
> Each repository should be documented with this schema.

---

## Repository Schema

```yaml
ID: REPO-001
Name: 
GitHub: 
Website: 
License: 
Stars: 
Language: 
Created: 
Last Commit: 
Contributors: 
Maintainers: 
Category: 
Subcategory: 
Tags: 
Dependencies: 
Dependents: 
Architecture: 
Documentation Score: 
Community Score: 
Production Ready: 
Security: 
Performance: 
Scalability: 
Extensibility: 
Plugin Support: 
API Quality: 
Testing: 
CI/CD: 
Examples: 
Strengths: 
Weaknesses: 
Alternatives: 
Replacement Cost: 
Risk Level: 
Notes: 
```

---

## Example Entry

```yaml
ID: REPO-001
Name: OpenHands
GitHub: https://github.com/All-Hands/AI-Hands
Website: https://app.all-hands.dev
License: Apache-2.0
Stars: 15000+
Language: Python
Created: 2024-01-15
Last Commit: 2024-12-01
Contributors: 100+
Maintainers: 10
Category: AI Agent
Subcategory: Coding Agent
Tags: [agent, coding, automation, AI, development]
Dependencies: [LangChain, LiteLLM, Playwright]
Dependents: [OpenHands-CLI, OpenHands-UI]
Architecture: Multi-Agent with Tool Integration
Documentation Score: 8/10
Community Score: 9/10
Production Ready: Yes
Security: Enterprise-grade with sandboxing
Performance: High throughput with parallel execution
Scalability: Horizontal with cloud deployment
Extensibility: Plugin system, MCP support
Plugin Support: Yes - Extensive
API Quality: REST + WebSocket
Testing: Unit, Integration, E2E
CI/CD: GitHub Actions
Examples: Code review, Bug fixing, Feature development
Strengths: [Open-source, Extensible, Cloud-native]
Weaknesses: [Complex setup, High resource usage]
Alternatives: [SWE-Agent, Claude-Code, Cursor]
Replacement Cost: Medium - Standardized interfaces
Risk Level: Low - Active maintenance
Notes: Core reference agent for our ecosystem
```

---

## Repository Index

| ID | Name | Category | Stars | Production Ready |
|----|------|----------|-------|-----------------|
| REPO-001 | OpenHands | AI Agent | 15000+ | ✅ |
| REPO-002 | Claude Code | AI Agent | - | ✅ |
| REPO-003 | Cursor | AI IDE | - | ✅ |
| REPO-004 | GitHub Copilot | AI Assistant | - | ✅ |
| REPO-005 | Aider | CLI Coding | 10000+ | ✅ |
| REPO-006 | Cline | VS Code Extension | 5000+ | ✅ |
| REPO-007 | Roo Code | VS Code Extension | 3000+ | ✅ |
| REPO-008 | Goose | CLI Agent | 2000+ | ✅ |
| REPO-009 | OpenCode | CLI Agent | 1000+ | ✅ |
| REPO-010 | Devin | AI Engineer | - | ✅ |
| REPO-011 | Lovable | App Builder | - | ✅ |
| REPO-012 | Bolt | Web Builder | - | ✅ |
| REPO-013 | Replit Agent | Web Dev | - | ✅ |
| REPO-014 | Firebase Studio | Cloud IDE | - | ✅ |
| REPO-015 | Codex | API Agent | - | ✅ |
| REPO-016 | Continue | IDE Plugin | 5000+ | ✅ |
| REPO-017 | SWE-Agent | SWE Bench | 3000+ | ✅ |
| REPO-018 | Browser Use | Web Agent | 2000+ | ✅ |
| REPO-019 | LiteLLM | LLM Router | 8000+ | ✅ |
| REPO-020 | LangChain | Agent Framework | 40000+ | ✅ |

---

## Adding New Repositories

To add a new repository:

1. Create a new entry using the template above
2. Fill in all available fields
3. Update the Repository Index table
4. Add to appropriate categories
5. Link in Capability Graph

---

## Maintenance

- Update weekly with GitHub API data
- Review quarterly for accuracy
- Archive repositories with no activity for 6+ months
