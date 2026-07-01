# AI Detector

## Overview

Detects AI/LLM usage in repositories.

## What It Detects

### AI Frameworks

```yaml
AI Frameworks:
  - LangChain: ["langchain"]
  - LlamaIndex: ["llama_index", "llama-index"]
  - Haystack: ["haystack"]
  - LangSmith: ["langsmith"]
  
LLM Libraries:
  - LiteLLM: ["litellm"]
  - OpenAI SDK: ["openai"]
  - Anthropic SDK: ["anthropic"]
  - Google AI: ["google-generativeai", "google.ai.generativeai"]
  - Transformers: ["transformers"]
  
Agent Frameworks:
  - AutoGPT: ["autogpt"]
  - BabyAGI: ["babyagi"]
  - AgentGPT: ["agentgpt"]
  - SuperAGI: ["superagi"]
```

### Providers

```yaml
Providers:
  OpenAI:
    patterns: ["openai", "gpt-4", "gpt-3.5"]
    
  Anthropic:
    patterns: ["anthropic", "claude", "claude-3"]
    
  Google:
    patterns: ["google", "gemini", "vertexai", "palm"]
    
  Meta:
    patterns: ["meta", "llama", "llama-2", "llama3"]
    
  Mistral:
    patterns: ["mistral", "mixtral"]
    
  DeepSeek:
    patterns: ["deepseek", "deepseek-ai"]
    
  Azure:
    patterns: ["azure", "azure-openai"]
    
  AWS:
    patterns: ["bedrock", "aws-bedrock", "boto3"]
    
  Ollama:
    patterns: ["ollama"]
    
  Groq:
    patterns: ["groq"]
    
  Together:
    patterns: ["together"]
    
  Fireworks:
    patterns: ["fireworks", "fireworks-ai"]
```

### AI Patterns

```yaml
Patterns:
  API Calls:
    - "openai.ChatCompletion"
    - "anthropic.messages.create"
    - "client.chat.completions.create"
    
  Imports:
    - "from openai import"
    - "from anthropic import"
    - "import anthropic"
    
  Config:
    - "OPENAI_API_KEY"
    - "ANTHROPIC_API_KEY"
    - "MODEL_NAME"
```

## Output Schema

```python
@dataclass
class AIDetection:
    """AI detection result."""
    
    uses_llm: bool
    models: List[str]
    providers: List[str]
    frameworks: List[str]
    supports_multiple_models: bool
    uses_litellm: bool
    uses_langchain: bool
    uses_llamaindex: bool
    uses_mcp: bool
    uses_openrouter: bool
    local_models: bool
    
    confidence: float
    timestamp: datetime
```

## Implementation

```python
class AIDetector:
    """Detects AI/LLM usage."""
    
    async def detect(self, snapshot: Snapshot) -> AIDetection:
        """Detect AI usage."""
        
        # Extract all text content
        content = self._extract_content(snapshot)
        
        # Search for patterns
        models = self._detect_models(content)
        providers = self._detect_providers(content)
        frameworks = self._detect_frameworks(content)
        
        # Check for specific technologies
        uses_litellm = self._check_litellm(content)
        uses_langchain = self._check_langchain(content)
        uses_mcp = self._check_mcp(snapshot)
        uses_openrouter = self._check_openrouter(content)
        
        # Check local models
        local = self._check_local_models(content)
        
        return AIDetection(
            uses_llm=len(models) > 0,
            models=models,
            providers=providers,
            frameworks=frameworks,
            supports_multiple_models=len(models) > 1,
            uses_litellm=uses_litellm,
            uses_langchain=uses_langchain,
            uses_llamaindex=uses_llamaindex,
            uses_mcp=uses_mcp,
            uses_openrouter=uses_openrouter,
            local_models=local,
            confidence=self._calculate_confidence(models),
            timestamp=datetime.utcnow()
        )
```

---

*AI Detector finds all AI/LLM usage.*
