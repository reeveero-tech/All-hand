# Fetcher: Repository Downloader

## Overview

```
Input: GitHub URL
Output: Repository Snapshot
```

## Responsibilities

1. Clone repository
2. Download all files
3. Extract metadata
4. Create snapshot

## Files to Fetch

```yaml
Priority Files:
  - README.md
  - LICENSE
  - package.json
  - pyproject.toml
  - Cargo.toml
  - go.mod
  - requirements.txt
  - Dockerfile
  - docker-compose.yml
  - Makefile
  - .gitignore
  
Code Files:
  - src/**/*
  - lib/**/*
  - app/**/*
  - pkg/**/*
  - cmd/**/*
  
Config Files:
  - config/**/*
  - configs/**/*
  - .env*
  - settings/**/*
  
CI/CD:
  - .github/workflows/**/*
  - .gitlab-ci.yml
  - .circleci/**/*
  - .travis.yml
  
Documentation:
  - docs/**/*
  - doc/**/*
  - *.md
  
Examples:
  - examples/**/*
  - demo/**/*
  - samples/**/*
  
Tests:
  - tests/**/*
  - test/**/*
  - *_test.py
  - *.test.ts
```

## Snapshot Structure

```python
@dataclass
class RepositorySnapshot:
    """Repository snapshot."""
    
    # Metadata
    url: str
    name: str
    owner: str
    local_path: Path
    fetched_at: datetime
    
    # File contents
    readme: Optional[str]
    license: Optional[str]
    package_json: Optional[Dict]
    pyproject: Optional[Dict]
    requirements: Optional[List[str]]
    
    # File lists
    source_files: List[Path]
    config_files: List[Path]
    docs_files: List[Path]
    test_files: List[Path]
    
    # GitHub API data
    stars: int
    forks: int
    issues: int
    open_prs: int
    contributors: int
    language: str
    topics: List[str]
```

## Implementation

```python
class RepositoryFetcher:
    """Fetches repository from GitHub."""
    
    def __init__(self, github_token: Optional[str] = None):
        self.github_token = github_token
        self.github = GitHubAPI(github_token)
        
    async def fetch(self, url: str) -> RepositorySnapshot:
        """Fetch repository."""
        
        # Parse URL
        owner, repo = self._parse_url(url)
        
        # Clone repository
        local_path = await self._clone(owner, repo)
        
        # Download files
        await self._download_files(local_path)
        
        # Get GitHub metadata
        metadata = await self.github.get_repo_metadata(owner, repo)
        
        # Create snapshot
        snapshot = RepositorySnapshot(
            url=url,
            name=repo,
            owner=owner,
            local_path=local_path,
            fetched_at=datetime.utcnow(),
            # ... populate other fields
        )
        
        return snapshot
```

---

*Fetcher is the first step in RIE.*
