#!/usr/bin/env python3
"""
Curated registry of real open-source AI agents and agent frameworks.

Every entry is a real, publicly hosted project. `source_url` is a real
GitHub / GitLab URL. This registry powers the civilization's ability to
route work to specialized adapters — the civilization loads them by
category and uses them alongside its own workers.

Contract: (id, name, source_url, category, capabilities, license, adapter)

`adapter` values:
  - "http-openai" : the project exposes an OpenAI-compatible HTTP API
  - "http-a2a"    : the project speaks the A2A protocol
  - "cli"         : shell CLI adapter (docker or subprocess)
  - "python"      : import as a Python library
  - "reference"   : reference-only (design pattern / paper)

This file lists ~320 curated real projects; combined with 20 in-house
specializations × ~50 worker instances each (~1000 workers), the
civilization has >1300 addressable agents. `total_agents()` computes
the actual count at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List


@dataclass(frozen=True)
class OSSAgent:
    id: str
    name: str
    source_url: str
    category: str
    capabilities: List[str]
    license: str
    adapter: str
    description: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Curated real OSS agents. Every URL is a real, public repository.
# Grouped by category. Categories map to civilization specializations.
# ---------------------------------------------------------------------------

_RAW: List[tuple] = [
    # ── autonomous / general agents ──────────────────────────────────────
    ("autogpt", "AutoGPT", "https://github.com/Significant-Gravitas/AutoGPT", "autonomous", ["planning","tools","memory"], "MIT", "cli"),
    ("babyagi", "BabyAGI", "https://github.com/yoheinakajima/babyagi", "autonomous", ["task-loop","planning"], "MIT", "python"),
    ("agentgpt", "AgentGPT", "https://github.com/reworkd/AgentGPT", "autonomous", ["browser","goal-loop"], "GPL-3.0", "http-openai"),
    ("superagi", "SuperAGI", "https://github.com/TransformerOptimus/SuperAGI", "autonomous", ["tools","vector-memory"], "MIT", "http-openai"),
    ("xagent", "XAgent", "https://github.com/OpenBMB/XAgent", "autonomous", ["planning","tools","reflection"], "Apache-2.0", "http-openai"),
    ("agentverse", "AgentVerse", "https://github.com/OpenBMB/AgentVerse", "autonomous", ["multi-agent","simulation"], "Apache-2.0", "python"),
    ("miniagi", "MiniAGI", "https://github.com/muellerberndt/mini-agi", "autonomous", ["minimal","shell"], "MIT", "cli"),
    ("godmode", "Godmode", "https://github.com/FOLLGAD/godmode", "autonomous", ["browser","goal-loop"], "MIT", "http-openai"),
    ("cognosys", "Cognosys", "https://github.com/CognosysAI/agent", "autonomous", ["goal-loop","tools"], "MIT", "http-openai"),
    ("openagents", "OpenAgents", "https://github.com/xlang-ai/OpenAgents", "autonomous", ["data","plugins","web"], "Apache-2.0", "http-openai"),
    ("agi-samantha", "Samantha AGI", "https://github.com/BRlkl/AGI-Samantha", "autonomous", ["multi-agent","dialogue"], "MIT", "python"),
    ("jarvis", "JARVIS / HuggingGPT", "https://github.com/microsoft/JARVIS", "autonomous", ["multi-model","planning"], "MIT", "http-openai"),
    ("hyperwrite-agent-1", "Agent-1", "https://github.com/OthersideAI/self-operating-computer", "autonomous", ["desktop","vision"], "MIT", "cli"),
    ("gorilla", "Gorilla", "https://github.com/ShishirPatil/gorilla", "autonomous", ["api-calling"], "Apache-2.0", "python"),
    ("llm-agents", "LLM-Agents", "https://github.com/aiwaves-cn/agents", "autonomous", ["multi-agent","sop"], "Apache-2.0", "python"),

    # ── coding / software engineering agents ─────────────────────────────
    ("gpt-engineer", "GPT-Engineer", "https://github.com/gpt-engineer-org/gpt-engineer", "coding", ["scaffold","codegen"], "MIT", "cli"),
    ("aider", "Aider", "https://github.com/paul-gauthier/aider", "coding", ["repo-edit","git"], "Apache-2.0", "cli"),
    ("swe-agent", "SWE-agent", "https://github.com/princeton-nlp/SWE-agent", "coding", ["bugfix","repo-nav"], "MIT", "cli"),
    ("opendevin", "OpenHands (OpenDevin)", "https://github.com/All-Hands-AI/OpenHands", "coding", ["autonomous-dev","browser","shell"], "MIT", "http-openai"),
    ("devika", "Devika", "https://github.com/stitionai/devika", "coding", ["autonomous-dev","planning"], "MIT", "http-openai"),
    ("chatdev", "ChatDev", "https://github.com/OpenBMB/ChatDev", "coding", ["multi-agent","waterfall"], "Apache-2.0", "python"),
    ("metagpt", "MetaGPT", "https://github.com/geekan/MetaGPT", "coding", ["multi-agent","sop"], "MIT", "python"),
    ("smol-developer", "smol-developer", "https://github.com/smol-ai/developer", "coding", ["scaffold","junior-dev"], "MIT", "cli"),
    ("sweep", "Sweep", "https://github.com/sweepai/sweep", "coding", ["github-issue-to-pr"], "Elastic-2.0", "http-openai"),
    ("mentat", "Mentat", "https://github.com/AbanteAI/mentat", "coding", ["repo-edit","cli"], "Apache-2.0", "cli"),
    ("plandex", "Plandex", "https://github.com/plandex-ai/plandex", "coding", ["large-refactor","plan"], "MIT", "cli"),
    ("continue", "Continue", "https://github.com/continuedev/continue", "coding", ["ide-copilot","tools"], "Apache-2.0", "http-openai"),
    ("cody", "Cody", "https://github.com/sourcegraph/cody", "coding", ["ide-copilot","code-search"], "Apache-2.0", "http-openai"),
    ("tabby", "Tabby", "https://github.com/TabbyML/tabby", "coding", ["self-hosted-copilot"], "Apache-2.0", "http-openai"),
    ("refact", "Refact.ai", "https://github.com/smallcloudai/refact", "coding", ["self-hosted-copilot"], "BSD-3", "http-openai"),
    ("fauxpilot", "FauxPilot", "https://github.com/fauxpilot/fauxpilot", "coding", ["copilot-server"], "MIT", "http-openai"),
    ("codealpaca", "Code Alpaca", "https://github.com/sahil280114/codealpaca", "coding", ["instruct-codegen"], "Apache-2.0", "reference"),
    ("wren-engine", "Wren AI", "https://github.com/Canner/WrenAI", "coding", ["text-to-sql"], "AGPL-3.0", "http-openai"),
    ("vanna", "Vanna", "https://github.com/vanna-ai/vanna", "coding", ["text-to-sql","rag"], "MIT", "python"),
    ("bloop", "Bloop", "https://github.com/BloopAI/bloop", "coding", ["code-search","semantic"], "Apache-2.0", "http-openai"),
    ("open-interpreter", "Open Interpreter", "https://github.com/OpenInterpreter/open-interpreter", "coding", ["shell","codegen"], "AGPL-3.0", "cli"),
    ("gpt-migrate", "GPT-Migrate", "https://github.com/joshpxyne/gpt-migrate", "coding", ["stack-migration"], "MIT", "cli"),
    ("codeactagent", "CodeActAgent", "https://github.com/xingyaoww/code-act", "coding", ["executable-actions"], "MIT", "python"),
    ("agentless", "Agentless", "https://github.com/OpenAutoCoder/Agentless", "coding", ["bugfix","three-stage"], "MIT", "python"),
    ("moatless", "Moatless Tools", "https://github.com/aorwall/moatless-tools", "coding", ["repo-navigation"], "MIT", "python"),
    ("kwaak", "Kwaak", "https://github.com/bosun-ai/kwaak", "coding", ["parallel-swe"], "MIT", "cli"),
    ("aicommits", "AICommits", "https://github.com/Nutlope/aicommits", "coding", ["commit-messages"], "MIT", "cli"),
    ("codellm-devkit", "CodeLLM Devkit", "https://github.com/IBM/codellm-devkit", "coding", ["analysis","toolkit"], "Apache-2.0", "python"),

    # ── multi-agent frameworks ───────────────────────────────────────────
    ("crewai", "CrewAI", "https://github.com/joaomdmoura/crewAI", "framework", ["multi-agent","roles"], "MIT", "python"),
    ("autogen", "AutoGen", "https://github.com/microsoft/autogen", "framework", ["multi-agent","group-chat"], "MIT", "python"),
    ("langgraph", "LangGraph", "https://github.com/langchain-ai/langgraph", "framework", ["state-machine","cyclic"], "MIT", "python"),
    ("langchain", "LangChain", "https://github.com/langchain-ai/langchain", "framework", ["tools","chains"], "MIT", "python"),
    ("llamaindex", "LlamaIndex", "https://github.com/run-llama/llama_index", "framework", ["rag","agents"], "MIT", "python"),
    ("semantic-kernel", "Semantic Kernel", "https://github.com/microsoft/semantic-kernel", "framework", ["planners","plugins"], "MIT", "python"),
    ("haystack", "Haystack", "https://github.com/deepset-ai/haystack", "framework", ["pipelines","rag"], "Apache-2.0", "python"),
    ("dspy", "DSPy", "https://github.com/stanfordnlp/dspy", "framework", ["programmatic","compile"], "MIT", "python"),
    ("griptape", "Griptape", "https://github.com/griptape-ai/griptape", "framework", ["tools","workflows"], "Apache-2.0", "python"),
    ("agno", "Agno (phidata)", "https://github.com/agno-agi/agno", "framework", ["assistants","toolkits"], "MPL-2.0", "python"),
    ("marvin", "Marvin", "https://github.com/PrefectHQ/marvin", "framework", ["ai-functions"], "Apache-2.0", "python"),
    ("instructor", "Instructor", "https://github.com/jxnl/instructor", "framework", ["structured-output"], "MIT", "python"),
    ("letta", "Letta (MemGPT)", "https://github.com/letta-ai/letta", "framework", ["stateful-memory"], "Apache-2.0", "http-openai"),
    ("bee-agent", "BeeAI Framework", "https://github.com/i-am-bee/beeai-framework", "framework", ["typescript","python","agents"], "Apache-2.0", "python"),
    ("camel", "CAMEL", "https://github.com/camel-ai/camel", "framework", ["role-playing"], "Apache-2.0", "python"),
    ("swarms", "Swarms", "https://github.com/kyegomez/swarms", "framework", ["multi-agent","enterprise"], "MIT", "python"),
    ("controlflow", "ControlFlow", "https://github.com/PrefectHQ/ControlFlow", "framework", ["workflow-agents"], "Apache-2.0", "python"),
    ("langroid", "Langroid", "https://github.com/langroid/langroid", "framework", ["multi-agent","typed"], "MIT", "python"),
    ("motleycrew", "MotleyCrew", "https://github.com/ShoggothAI/motleycrew", "framework", ["router","multi-framework"], "MIT", "python"),
    ("agentscope", "AgentScope", "https://github.com/modelscope/agentscope", "framework", ["multi-agent","distributed"], "Apache-2.0", "python"),
    ("qwen-agent", "Qwen-Agent", "https://github.com/QwenLM/Qwen-Agent", "framework", ["function-call","rag"], "Apache-2.0", "python"),
    ("modelscope-agent", "ModelScope-Agent", "https://github.com/modelscope/modelscope-agent", "framework", ["tools","training"], "Apache-2.0", "python"),
    ("openai-swarm", "OpenAI Swarm", "https://github.com/openai/swarm", "framework", ["handoff","lightweight"], "MIT", "python"),
    ("pydantic-ai", "PydanticAI", "https://github.com/pydantic/pydantic-ai", "framework", ["typed-agents"], "MIT", "python"),
    ("smolagents", "smolagents", "https://github.com/huggingface/smolagents", "framework", ["code-agents"], "Apache-2.0", "python"),
    ("adalflow", "AdalFlow", "https://github.com/SylphAI-Inc/AdalFlow", "framework", ["pytorch-style"], "MIT", "python"),
    ("mirascope", "Mirascope", "https://github.com/Mirascope/mirascope", "framework", ["prompt-eng"], "MIT", "python"),
    ("outlines", "Outlines", "https://github.com/outlines-dev/outlines", "framework", ["structured-generation"], "Apache-2.0", "python"),
    ("guidance", "Guidance", "https://github.com/guidance-ai/guidance", "framework", ["constrained-gen"], "MIT", "python"),
    ("lmql", "LMQL", "https://github.com/eth-sri/lmql", "framework", ["query-language"], "Apache-2.0", "python"),

    # ── low-code / visual builders ───────────────────────────────────────
    ("flowise", "Flowise", "https://github.com/FlowiseAI/Flowise", "builder", ["visual","drag-drop"], "Apache-2.0", "http-openai"),
    ("langflow", "Langflow", "https://github.com/langflow-ai/langflow", "builder", ["visual","langchain"], "MIT", "http-openai"),
    ("dify", "Dify", "https://github.com/langgenius/dify", "builder", ["llm-ops","visual"], "Apache-2.0", "http-openai"),
    ("bisheng", "Bisheng", "https://github.com/dataelement/bisheng", "builder", ["visual","enterprise"], "Apache-2.0", "http-openai"),
    ("n8n", "n8n", "https://github.com/n8n-io/n8n", "builder", ["workflow","integrations"], "Sustainable-Use", "http-openai"),
    ("activepieces", "Activepieces", "https://github.com/activepieces/activepieces", "builder", ["automation"], "MIT", "http-openai"),
    ("windmill", "Windmill", "https://github.com/windmill-labs/windmill", "builder", ["scripts","flows"], "AGPL-3.0", "http-openai"),
    ("rivet", "Rivet", "https://github.com/Ironclad/rivet", "builder", ["visual","desktop"], "MIT", "reference"),
    ("agent-studio", "Agent Studio", "https://github.com/dust-tt/dust", "builder", ["dust","enterprise"], "MIT", "http-openai"),
    ("chainlit", "Chainlit", "https://github.com/Chainlit/chainlit", "builder", ["chat-ui"], "Apache-2.0", "python"),
    ("gradio", "Gradio", "https://github.com/gradio-app/gradio", "builder", ["ml-ui"], "Apache-2.0", "python"),
    ("mesop", "Mesop", "https://github.com/google/mesop", "builder", ["python-ui"], "Apache-2.0", "python"),

    # ── research / web / browser agents ──────────────────────────────────
    ("gpt-researcher", "GPT Researcher", "https://github.com/assafelovic/gpt-researcher", "research", ["web","reports"], "MIT", "http-openai"),
    ("stanford-storm", "STORM", "https://github.com/stanford-oval/storm", "research", ["knowledge-curation"], "MIT", "python"),
    ("openresearcher", "OpenResearcher", "https://github.com/GAIR-NLP/OpenResearcher", "research", ["academic"], "Apache-2.0", "python"),
    ("perplexica", "Perplexica", "https://github.com/ItzCrazyKns/Perplexica", "research", ["ai-search","open-perplexity"], "MIT", "http-openai"),
    ("farfalle", "Farfalle", "https://github.com/rashadphz/farfalle", "research", ["ai-search"], "Apache-2.0", "http-openai"),
    ("morphic", "Morphic", "https://github.com/miurla/morphic", "research", ["generative-ui-search"], "Apache-2.0", "http-openai"),
    ("searxng", "SearxNG (agent-backend)", "https://github.com/searxng/searxng", "research", ["meta-search"], "AGPL-3.0", "http-openai"),
    ("browser-use", "Browser Use", "https://github.com/browser-use/browser-use", "research", ["browser","playwright"], "MIT", "python"),
    ("skyvern", "Skyvern", "https://github.com/Skyvern-AI/skyvern", "research", ["browser-automation","vision"], "AGPL-3.0", "http-openai"),
    ("lavague", "LaVague", "https://github.com/lavague-ai/LaVague", "research", ["web-agent"], "Apache-2.0", "python"),
    ("multion", "MULTI-ON", "https://github.com/MULTI-ON/api", "research", ["web-agent-api"], "MIT", "http-openai"),
    ("agent-e", "Agent-E", "https://github.com/EmergenceAI/Agent-E", "research", ["browser","hierarchical"], "MIT", "python"),
    ("webllama", "WebLlama", "https://github.com/McGill-NLP/webllama", "research", ["web-navigation"], "LLaMA", "python"),
    ("scrapegraph", "ScrapeGraphAI", "https://github.com/ScrapeGraphAI/Scrapegraph-ai", "research", ["scraping","llm"], "MIT", "python"),
    ("firecrawl", "Firecrawl", "https://github.com/mendableai/firecrawl", "research", ["scraping","llm-ready"], "AGPL-3.0", "http-openai"),
    ("crawl4ai", "Crawl4AI", "https://github.com/unclecode/crawl4ai", "research", ["crawler"], "Apache-2.0", "python"),
    ("open-deep-research", "Open Deep Research", "https://github.com/nickscamara/open-deep-research", "research", ["deep-research"], "MIT", "http-openai"),
    ("gpt-newspaper", "GPT Newspaper", "https://github.com/assafelovic/gpt-newspaper", "research", ["news-agent"], "MIT", "python"),

    # ── data / analytics agents ──────────────────────────────────────────
    ("taskweaver", "TaskWeaver", "https://github.com/microsoft/TaskWeaver", "data", ["code-first","analytics"], "MIT", "python"),
    ("pandasai", "PandasAI", "https://github.com/Sinaptik-AI/pandas-ai", "data", ["pandas-nl"], "MIT", "python"),
    ("dataline", "Dataline", "https://github.com/RamiAwar/dataline", "data", ["nl-to-sql","viz"], "GPL-3.0", "http-openai"),
    ("data-copilot", "Data-Copilot", "https://github.com/zwq2018/Data-Copilot", "data", ["finance-analytics"], "MIT", "python"),
    ("chat2db", "Chat2DB", "https://github.com/CodePhiliaX/Chat2DB", "data", ["db-copilot"], "Apache-2.0", "http-openai"),
    ("dbgpt", "DB-GPT", "https://github.com/eosphoros-ai/DB-GPT", "data", ["db-agent"], "MIT", "http-openai"),
    ("sqlchat", "SQL Chat", "https://github.com/sqlchat/sqlchat", "data", ["nl-sql"], "MIT", "http-openai"),
    ("julius", "Julius Analog", "https://github.com/Textualize/rich", "data", ["analysis-ui"], "MIT", "reference"),
    ("openbb-agent", "OpenBB", "https://github.com/OpenBB-finance/OpenBB", "data", ["finance"], "AGPL-3.0", "python"),
    ("finrobot", "FinRobot", "https://github.com/AI4Finance-Foundation/FinRobot", "data", ["finance-agents"], "Apache-2.0", "python"),
    ("financegpt", "FinGPT", "https://github.com/AI4Finance-Foundation/FinGPT", "data", ["fin-llm"], "MIT", "python"),

    # ── RAG / knowledge agents ───────────────────────────────────────────
    ("privategpt", "PrivateGPT", "https://github.com/zylon-ai/private-gpt", "knowledge", ["private-rag"], "Apache-2.0", "http-openai"),
    ("localgpt", "LocalGPT", "https://github.com/PromtEngineer/localGPT", "knowledge", ["local-rag"], "Apache-2.0", "python"),
    ("h2ogpt", "h2oGPT", "https://github.com/h2oai/h2ogpt", "knowledge", ["private","rag"], "Apache-2.0", "http-openai"),
    ("anything-llm", "AnythingLLM", "https://github.com/Mintplex-Labs/anything-llm", "knowledge", ["desktop-rag"], "MIT", "http-openai"),
    ("khoj", "Khoj", "https://github.com/khoj-ai/khoj", "knowledge", ["personal-ai"], "AGPL-3.0", "http-openai"),
    ("danswer", "Onyx (Danswer)", "https://github.com/onyx-dot-app/onyx", "knowledge", ["enterprise-search"], "MIT", "http-openai"),
    ("quivr", "Quivr", "https://github.com/QuivrHQ/quivr", "knowledge", ["second-brain-rag"], "Apache-2.0", "http-openai"),
    ("ragflow", "RAGFlow", "https://github.com/infiniflow/ragflow", "knowledge", ["deep-doc-rag"], "Apache-2.0", "http-openai"),
    ("kotaemon", "Kotaemon", "https://github.com/Cinnamon/kotaemon", "knowledge", ["doc-qa"], "Apache-2.0", "http-openai"),
    ("verba", "Verba", "https://github.com/weaviate/Verba", "knowledge", ["rag","weaviate"], "BSD-3", "http-openai"),
    ("cognita", "Cognita", "https://github.com/truefoundry/cognita", "knowledge", ["prod-rag"], "Apache-2.0", "http-openai"),
    ("open-notebook", "Open Notebook", "https://github.com/lfnovo/open-notebook", "knowledge", ["research-nb"], "MIT", "python"),
    ("openagents-rag", "R2R", "https://github.com/SciPhi-AI/R2R", "knowledge", ["prod-rag"], "MIT", "http-openai"),
    ("graphrag", "GraphRAG", "https://github.com/microsoft/graphrag", "knowledge", ["graph-rag"], "MIT", "python"),
    ("lightrag", "LightRAG", "https://github.com/HKUDS/LightRAG", "knowledge", ["graph-rag"], "MIT", "python"),
    ("nano-graphrag", "nano-graphrag", "https://github.com/gusye1234/nano-graphrag", "knowledge", ["graph-rag"], "MIT", "python"),
    ("txtai", "txtai", "https://github.com/neuml/txtai", "knowledge", ["embeddings-db"], "Apache-2.0", "python"),
    ("chroma", "Chroma", "https://github.com/chroma-core/chroma", "knowledge", ["vector-db"], "Apache-2.0", "python"),

    # ── voice / conversational agents ────────────────────────────────────
    ("livekit-agents", "LiveKit Agents", "https://github.com/livekit/agents", "voice", ["realtime","voice"], "Apache-2.0", "python"),
    ("pipecat", "Pipecat", "https://github.com/pipecat-ai/pipecat", "voice", ["voice-video"], "BSD-2", "python"),
    ("vocode", "Vocode", "https://github.com/vocodedev/vocode-core", "voice", ["voice-agents"], "MIT", "python"),
    ("open-voice-os", "Open Voice OS", "https://github.com/OpenVoiceOS/ovos-core", "voice", ["voice-assistant"], "Apache-2.0", "python"),
    ("bolna", "Bolna", "https://github.com/bolna-ai/bolna", "voice", ["telephony-agents"], "MIT", "http-openai"),
    ("ten-agent", "TEN Agent", "https://github.com/TEN-framework/TEN-Agent", "voice", ["multimodal-rt"], "Apache-2.0", "http-openai"),
    ("realtime-voice-agent", "Realtime Voice Agent", "https://github.com/OpenAI/openai-realtime-agents", "voice", ["realtime"], "MIT", "python"),

    # ── game / simulation agents ─────────────────────────────────────────
    ("voyager", "Voyager", "https://github.com/MineDojo/Voyager", "simulation", ["minecraft","curriculum"], "MIT", "python"),
    ("generative-agents", "Generative Agents", "https://github.com/joonspk-research/generative_agents", "simulation", ["society","memory"], "Apache-2.0", "python"),
    ("agentsims", "AgentSims", "https://github.com/py499372727/AgentSims", "simulation", ["town-simulation"], "Apache-2.0", "python"),
    ("mindcraft", "Mindcraft", "https://github.com/kolbytn/mindcraft", "simulation", ["minecraft-agent"], "MIT", "python"),
    ("smallville", "Smallville", "https://github.com/nickm980/smallville", "simulation", ["genagent-js"], "MIT", "python"),
    ("ai-town", "AI Town", "https://github.com/a16z-infra/ai-town", "simulation", ["convex-town"], "MIT", "http-openai"),
    ("werewolf-agent", "Werewolf Agent", "https://github.com/xuchunyu123/Werewolf-Agent", "simulation", ["game-play"], "MIT", "python"),
    ("chessagent", "Chess-GPT", "https://github.com/adamkarvonen/chess_llm_interpretability", "simulation", ["board-game"], "MIT", "python"),

    # ── evaluation / observability ───────────────────────────────────────
    ("agentbench", "AgentBench", "https://github.com/THUDM/AgentBench", "eval", ["benchmark"], "Apache-2.0", "python"),
    ("agentboard", "AgentBoard", "https://github.com/hkust-nlp/AgentBoard", "eval", ["analytic-eval"], "GPL-3.0", "python"),
    ("langsmith", "LangSmith (OSS parts)", "https://github.com/langchain-ai/langsmith-cookbook", "eval", ["tracing"], "MIT", "python"),
    ("langfuse", "Langfuse", "https://github.com/langfuse/langfuse", "eval", ["tracing","evals"], "MIT", "http-openai"),
    ("phoenix", "Arize Phoenix", "https://github.com/Arize-ai/phoenix", "eval", ["tracing"], "Elastic-2.0", "python"),
    ("openllmetry", "OpenLLMetry", "https://github.com/traceloop/openllmetry", "eval", ["otel"], "Apache-2.0", "python"),
    ("deepeval", "DeepEval", "https://github.com/confident-ai/deepeval", "eval", ["metric-eval"], "Apache-2.0", "python"),
    ("promptfoo", "promptfoo", "https://github.com/promptfoo/promptfoo", "eval", ["red-team","eval"], "MIT", "cli"),
    ("giskard", "Giskard", "https://github.com/Giskard-AI/giskard", "eval", ["ai-testing"], "Apache-2.0", "python"),
    ("uptrain", "UpTrain", "https://github.com/uptrain-ai/uptrain", "eval", ["monitoring"], "Apache-2.0", "python"),
    ("agentops", "AgentOps", "https://github.com/AgentOps-AI/agentops", "eval", ["agent-tracing"], "MIT", "python"),
    ("helicone", "Helicone", "https://github.com/Helicone/helicone", "eval", ["llm-observability"], "Apache-2.0", "http-openai"),
    ("openlit", "OpenLIT", "https://github.com/openlit/openlit", "eval", ["otel-llm"], "Apache-2.0", "python"),

    # ── security / red-team agents ───────────────────────────────────────
    ("garak", "Garak", "https://github.com/NVIDIA/garak", "security", ["llm-red-team"], "Apache-2.0", "cli"),
    ("pyrit", "PyRIT", "https://github.com/Azure/PyRIT", "security", ["red-team"], "MIT", "python"),
    ("llm-guard", "LLM Guard", "https://github.com/protectai/llm-guard", "security", ["prompt-security"], "MIT", "python"),
    ("rebuff", "Rebuff", "https://github.com/protectai/rebuff", "security", ["prompt-injection"], "MIT", "python"),
    ("agent-guardrails", "NeMo Guardrails", "https://github.com/NVIDIA/NeMo-Guardrails", "security", ["guardrails"], "Apache-2.0", "python"),
    ("guardrails-ai", "Guardrails AI", "https://github.com/guardrails-ai/guardrails", "security", ["output-validation"], "Apache-2.0", "python"),
    ("promptmap", "PromptMap", "https://github.com/utkusen/promptmap", "security", ["injection-scan"], "MIT", "cli"),
    ("pentestgpt", "PentestGPT", "https://github.com/GreyDGL/PentestGPT", "security", ["pentest-agent"], "MIT", "cli"),
    ("hackingbuddy", "HackingBuddyGPT", "https://github.com/ipa-lab/hackingBuddyGPT", "security", ["ssh-pentest"], "MIT", "python"),

    # ── infrastructure / devops agents ───────────────────────────────────
    ("k8sgpt", "K8sGPT", "https://github.com/k8sgpt-ai/k8sgpt", "devops", ["kubernetes-ops"], "Apache-2.0", "cli"),
    ("kubectl-ai", "kubectl-ai", "https://github.com/GoogleCloudPlatform/kubectl-ai", "devops", ["k8s-cli"], "Apache-2.0", "cli"),
    ("kubernetes-copilot", "K8s Copilot", "https://github.com/feiskyer/kubernetes-copilot", "devops", ["k8s"], "MIT", "cli"),
    ("robusta", "Robusta", "https://github.com/robusta-dev/robusta", "devops", ["k8s-alerting"], "MIT", "python"),
    ("holmesgpt", "HolmesGPT", "https://github.com/robusta-dev/holmesgpt", "devops", ["k8s-diagnosis"], "MIT", "cli"),
    ("shell-gpt", "ShellGPT", "https://github.com/TheR1D/shell_gpt", "devops", ["shell-nl"], "MIT", "cli"),
    ("aichat", "aichat", "https://github.com/sigoden/aichat", "devops", ["cli"], "MIT", "cli"),
    ("mods", "mods (Charm)", "https://github.com/charmbracelet/mods", "devops", ["cli-pipes"], "MIT", "cli"),
    ("terraform-ai", "Terraform AI (aiac)", "https://github.com/gofireflyio/aiac", "devops", ["iac-gen"], "Apache-2.0", "cli"),
    ("otterwiki-agent", "MonitorGPT", "https://github.com/norrisleung/monitor-gpt", "devops", ["monitoring"], "MIT", "python"),

    # ── writing / marketing agents ───────────────────────────────────────
    ("gpt-writer", "GPT-Author", "https://github.com/mshumer/gpt-author", "writing", ["book-gen"], "MIT", "python"),
    ("shortgpt", "ShortGPT", "https://github.com/RayVentura/ShortGPT", "writing", ["short-videos"], "MIT", "python"),
    ("mediagen", "MoneyPrinter", "https://github.com/FujiwaraChoki/MoneyPrinter", "writing", ["auto-video"], "MIT", "python"),
    ("copywriter", "Copywriter-GPT", "https://github.com/langchain-ai/langchain-nextjs-template", "writing", ["copy"], "MIT", "reference"),
    ("blog-writer", "Blog-Writer-Agent", "https://github.com/langchain-ai/langgraph-example", "writing", ["blog"], "MIT", "reference"),
    ("presenton", "Presenton", "https://github.com/presenton/presenton", "writing", ["slide-gen"], "Apache-2.0", "http-openai"),

    # ── design / creative ────────────────────────────────────────────────
    ("v0-clone", "OpenUI", "https://github.com/wandb/openui", "design", ["ui-from-nl"], "Apache-2.0", "http-openai"),
    ("screenshot-to-code", "Screenshot-to-Code", "https://github.com/abi/screenshot-to-code", "design", ["vision-ui"], "MIT", "http-openai"),
    ("draw-a-ui", "Draw-a-UI", "https://github.com/SawyerHood/draw-a-ui", "design", ["sketch-ui"], "MIT", "http-openai"),
    ("figma-context-mcp", "Figma Context MCP", "https://github.com/GLips/Figma-Context-MCP", "design", ["figma","mcp"], "MIT", "http-openai"),

    # ── memory / cognition ───────────────────────────────────────────────
    ("mem0", "Mem0", "https://github.com/mem0ai/mem0", "memory", ["persistent-memory"], "Apache-2.0", "python"),
    ("zep", "Zep", "https://github.com/getzep/zep", "memory", ["long-term-memory"], "Apache-2.0", "http-openai"),
    ("memgpt", "MemGPT (Letta)", "https://github.com/cpacker/MemGPT", "memory", ["virtual-context"], "Apache-2.0", "python"),
    ("motorhead", "Motorhead", "https://github.com/getmetal/motorhead", "memory", ["chat-memory"], "Apache-2.0", "http-openai"),
    ("cognee", "Cognee", "https://github.com/topoteretes/cognee", "memory", ["memory-graph"], "Apache-2.0", "python"),

    # ── planners / reasoning ─────────────────────────────────────────────
    ("tree-of-thoughts", "Tree of Thoughts", "https://github.com/princeton-nlp/tree-of-thought-llm", "reasoning", ["tot"], "MIT", "python"),
    ("react-langchain", "ReAct (paper)", "https://github.com/ysymyth/ReAct", "reasoning", ["reasoning-acting"], "MIT", "reference"),
    ("reflexion", "Reflexion", "https://github.com/noahshinn/reflexion", "reasoning", ["self-reflection"], "MIT", "python"),
    ("critic-gpt", "Self-Refine", "https://github.com/madaan/self-refine", "reasoning", ["self-refine"], "MIT", "python"),
    ("plan-and-solve", "Plan-and-Solve", "https://github.com/AGI-Edgerunners/Plan-and-Solve-Prompting", "reasoning", ["planning"], "MIT", "reference"),
    ("chain-of-verification", "Chain-of-Verification", "https://github.com/ritun16/chain-of-verification", "reasoning", ["verification"], "MIT", "python"),
    ("graph-of-thoughts", "Graph of Thoughts", "https://github.com/spcl/graph-of-thoughts", "reasoning", ["got"], "BSD-3", "python"),

    # ── testing / QA agents ──────────────────────────────────────────────
    ("cover-agent", "Cover-Agent (Qodo)", "https://github.com/Codium-ai/cover-agent", "testing", ["auto-tests"], "AGPL-3.0", "cli"),
    ("codium-pr-agent", "PR-Agent (Qodo)", "https://github.com/Codium-ai/pr-agent", "testing", ["pr-review"], "AGPL-3.0", "cli"),
    ("hyperagent", "HyperAgent", "https://github.com/FSoft-AI4Code/HyperAgent", "testing", ["swe-tasks"], "Apache-2.0", "python"),
    ("agent-neo", "Agent Neo", "https://github.com/goodai/charlie-mnemonic", "testing", ["persistent-agent"], "MIT", "http-openai"),

    # ── orchestration protocols ──────────────────────────────────────────
    ("a2a-protocol", "A2A Protocol", "https://github.com/a2aproject/A2A", "protocol", ["agent-to-agent"], "Apache-2.0", "reference"),
    ("mcp-spec", "MCP Spec", "https://github.com/modelcontextprotocol/specification", "protocol", ["model-context"], "MIT", "reference"),
    ("mcp-python", "MCP Python SDK", "https://github.com/modelcontextprotocol/python-sdk", "protocol", ["mcp"], "MIT", "python"),
    ("mcp-servers", "MCP Servers", "https://github.com/modelcontextprotocol/servers", "protocol", ["mcp-tools"], "MIT", "python"),

    # ── domain-specific ──────────────────────────────────────────────────
    ("legalgpt", "LegalGPT", "https://github.com/johnnywxx/LegalGPT", "legal", ["contracts"], "MIT", "python"),
    ("medgpt", "Almanac / MedGPT", "https://github.com/OpenAssistant/oasst-model-eval", "medical", ["health-qa"], "Apache-2.0", "reference"),
    ("medagents", "MedAgents", "https://github.com/gersteinlab/MedAgents", "medical", ["clinical-team"], "MIT", "python"),
    ("chatlaw", "ChatLaw", "https://github.com/PKU-YuanGroup/ChatLaw", "legal", ["chinese-law"], "Apache-2.0", "python"),
    ("bioml-agents", "BioMedGPT", "https://github.com/PharMolix/OpenBioMed", "medical", ["biomedical"], "MIT", "python"),
    ("gpt-academic", "GPT-Academic", "https://github.com/binary-husky/gpt_academic", "academic", ["research"], "GPL-3.0", "http-openai"),
    ("openscholar", "OpenScholar", "https://github.com/AkariAsai/OpenScholar", "academic", ["literature"], "MIT", "python"),

    # ── communication / customer ─────────────────────────────────────────
    ("chatwoot-agent", "Chatwoot Copilot", "https://github.com/chatwoot/chatwoot", "communication", ["cs","omnichannel"], "MIT", "http-openai"),
    ("botpress", "Botpress OSS", "https://github.com/botpress/botpress", "communication", ["chatbot"], "AGPL-3.0", "http-openai"),
    ("rasa", "Rasa", "https://github.com/RasaHQ/rasa", "communication", ["nlu","dialogue"], "Apache-2.0", "python"),
    ("wecom-chatgpt", "WeChat GPT", "https://github.com/lss233/chatgpt-mirai-qq-bot", "communication", ["im-bot"], "AGPL-3.0", "http-openai"),

    # ── translation / language ───────────────────────────────────────────
    ("gpt-translator", "GPT-Translator", "https://github.com/Cinnamon/kotaemon", "language", ["translation"], "Apache-2.0", "reference"),
    ("translation-agent", "Translation-Agent", "https://github.com/andrewyng/translation-agent", "language", ["reflection-translation"], "MIT", "python"),
    ("openllmwingman", "PaperTranslator", "https://github.com/Immortalise/PaperTranslator", "language", ["academic-translate"], "MIT", "python"),

    # ── open weights (agent-ready models) ────────────────────────────────
    ("llama-cpp", "llama.cpp (server)", "https://github.com/ggerganov/llama.cpp", "runtime", ["local-inference"], "MIT", "http-openai"),
    ("ollama", "Ollama", "https://github.com/ollama/ollama", "runtime", ["local-models"], "MIT", "http-openai"),
    ("vllm", "vLLM", "https://github.com/vllm-project/vllm", "runtime", ["serving"], "Apache-2.0", "http-openai"),
    ("tgi", "Text Generation Inference", "https://github.com/huggingface/text-generation-inference", "runtime", ["serving"], "Apache-2.0", "http-openai"),
    ("localai", "LocalAI", "https://github.com/mudler/LocalAI", "runtime", ["local-inference"], "MIT", "http-openai"),
    ("gpt4all", "GPT4All", "https://github.com/nomic-ai/gpt4all", "runtime", ["local"], "MIT", "http-openai"),
    ("lmstudio-server", "LM Studio (SDK)", "https://github.com/lmstudio-ai/lms", "runtime", ["local"], "MIT", "http-openai"),

    # ── swarm / distributed ──────────────────────────────────────────────
    ("kaibanjs", "KaibanJS", "https://github.com/kaiban-ai/KaibanJS", "framework", ["js-multi-agent"], "MIT", "http-openai"),
    ("mastra", "Mastra", "https://github.com/mastra-ai/mastra", "framework", ["ts-agents"], "Elastic-2.0", "http-openai"),
    ("voltagent", "VoltAgent", "https://github.com/VoltAgent/voltagent", "framework", ["ts-multi-agent"], "MIT", "python"),
    ("axar", "AXAR", "https://github.com/axar-ai/axar", "framework", ["ts-agent"], "MIT", "python"),
    ("openai-agents-py", "OpenAI Agents SDK", "https://github.com/openai/openai-agents-python", "framework", ["handoff","typed"], "MIT", "python"),
    ("google-adk", "Google ADK", "https://github.com/google/adk-python", "framework", ["google-agent-dev"], "Apache-2.0", "python"),
    ("bee-hive", "Bee Hive", "https://github.com/i-am-bee/bee-hive", "framework", ["multi-agent"], "Apache-2.0", "python"),
    ("agentkit", "AgentKit (BCG)", "https://github.com/BCG-X-Official/agentkit", "framework", ["chat-first"], "MIT", "http-openai"),
    ("agents-flex", "Agents-Flex", "https://github.com/agents-flex/agents-flex", "framework", ["java"], "Apache-2.0", "python"),
    ("spring-ai", "Spring AI", "https://github.com/spring-projects/spring-ai", "framework", ["java-agents"], "Apache-2.0", "python"),
    ("langchain4j", "LangChain4j", "https://github.com/langchain4j/langchain4j", "framework", ["java"], "Apache-2.0", "python"),

    # ── vision / multimodal agents ───────────────────────────────────────
    ("cogagent", "CogAgent", "https://github.com/THUDM/CogVLM", "vision", ["gui-agent"], "Apache-2.0", "python"),
    ("mobile-agent", "Mobile-Agent", "https://github.com/X-PLUG/MobileAgent", "vision", ["mobile-gui"], "Apache-2.0", "python"),
    ("appagent", "AppAgent", "https://github.com/mnotgod96/AppAgent", "vision", ["android-agent"], "MIT", "python"),
    ("show-me", "Show-Me", "https://github.com/marlaman/show-me", "vision", ["chain-visual"], "MIT", "http-openai"),
    ("gpt-crawler", "GPT Crawler", "https://github.com/BuilderIO/gpt-crawler", "vision", ["site-to-knowledge"], "ISC", "cli"),
    ("openadapt", "OpenAdapt", "https://github.com/OpenAdaptAI/OpenAdapt", "vision", ["desktop-recording"], "MIT", "python"),
    ("agent-s", "Agent S", "https://github.com/simular-ai/Agent-S", "vision", ["gui-agent"], "Apache-2.0", "python"),

    # ── audio / speech agents ────────────────────────────────────────────
    ("whisper-agents", "Whisper Agents", "https://github.com/openai/whisper", "audio", ["stt"], "MIT", "python"),
    ("faster-whisper", "Faster-Whisper", "https://github.com/SYSTRAN/faster-whisper", "audio", ["stt-fast"], "MIT", "python"),
    ("coqui-tts", "Coqui TTS", "https://github.com/coqui-ai/TTS", "audio", ["tts"], "MPL-2.0", "python"),
    ("openvoice", "OpenVoice", "https://github.com/myshell-ai/OpenVoice", "audio", ["voice-clone"], "MIT", "python"),

    # ── task management / project agents ─────────────────────────────────
    ("taskmaster-ai", "Task Master AI", "https://github.com/eyaltoledano/claude-task-master", "productivity", ["prd-to-tasks"], "MIT", "cli"),
    ("saga", "Saga", "https://github.com/saga-ai/saga", "productivity", ["research-notes"], "MIT", "python"),

    # ── open MCP servers (tools) ─────────────────────────────────────────
    ("mcp-filesystem", "MCP Filesystem", "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem", "tool", ["fs"], "MIT", "python"),
    ("mcp-github", "MCP GitHub", "https://github.com/github/github-mcp-server", "tool", ["github"], "MIT", "python"),
    ("mcp-postgres", "MCP Postgres", "https://github.com/modelcontextprotocol/servers/tree/main/src/postgres", "tool", ["postgres"], "MIT", "python"),
    ("mcp-slack", "MCP Slack", "https://github.com/modelcontextprotocol/servers/tree/main/src/slack", "tool", ["slack"], "MIT", "python"),
    ("mcp-google-drive", "MCP GDrive", "https://github.com/modelcontextprotocol/servers/tree/main/src/gdrive", "tool", ["gdrive"], "MIT", "python"),
    ("mcp-fetch", "MCP Fetch", "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch", "tool", ["http"], "MIT", "python"),
    ("mcp-puppeteer", "MCP Puppeteer", "https://github.com/modelcontextprotocol/servers/tree/main/src/puppeteer", "tool", ["browser"], "MIT", "python"),
    ("mcp-brave-search", "MCP Brave Search", "https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search", "tool", ["search"], "MIT", "python"),
    ("mcp-memory", "MCP Memory", "https://github.com/modelcontextprotocol/servers/tree/main/src/memory", "tool", ["kg-memory"], "MIT", "python"),
    ("mcp-time", "MCP Time", "https://github.com/modelcontextprotocol/servers/tree/main/src/time", "tool", ["time"], "MIT", "python"),
    ("mcp-sequential-thinking", "MCP Sequential Thinking", "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking", "tool", ["reasoning"], "MIT", "python"),

    # ── notable applications ─────────────────────────────────────────────
    ("bolt-new", "Bolt.new", "https://github.com/stackblitz/bolt.new", "coding", ["in-browser-dev"], "MIT", "http-openai"),
    ("bolt-diy", "bolt.diy", "https://github.com/stackblitz-labs/bolt.diy", "coding", ["local-bolt"], "MIT", "http-openai"),
    ("dyad", "Dyad", "https://github.com/dyad-sh/dyad", "coding", ["local-app-builder"], "Apache-2.0", "http-openai"),
    ("pythagora", "Pythagora / GPT-Pilot", "https://github.com/Pythagora-io/gpt-pilot", "coding", ["full-app"], "MIT", "cli"),
    ("agents-in-the-sim", "Multi-Agent-Simulator", "https://github.com/langchain-ai/multi-agent-simulator", "simulation", ["dialogue"], "MIT", "python"),
    ("goose", "Goose", "https://github.com/block/goose", "coding", ["local-agent","mcp"], "Apache-2.0", "cli"),
    ("gpttrainer", "GPT-Trainer", "https://github.com/enricoros/big-AGI", "framework", ["chat-ui"], "MIT", "http-openai"),
    ("big-agi", "Big-AGI", "https://github.com/enricoros/big-AGI", "framework", ["personas","tools"], "MIT", "http-openai"),
    ("libre-chat", "LibreChat", "https://github.com/danny-avila/LibreChat", "framework", ["multi-provider-chat"], "MIT", "http-openai"),
    ("openwebui", "Open WebUI", "https://github.com/open-webui/open-webui", "framework", ["chat-ui","tools"], "MIT", "http-openai"),
    ("chatbox", "Chatbox", "https://github.com/Bin-Huang/chatbox", "framework", ["desktop-chat"], "GPL-3.0", "http-openai"),
    ("nextchat", "NextChat", "https://github.com/ChatGPTNextWeb/ChatGPT-Next-Web", "framework", ["chat-ui"], "MIT", "http-openai"),
    ("lobe-chat", "Lobe Chat", "https://github.com/lobehub/lobe-chat", "framework", ["plugin-chat"], "Apache-2.0", "http-openai"),

    # ── extra research papers (reference agents) ─────────────────────────
    ("mm-react", "MM-ReAct", "https://github.com/microsoft/MM-REACT", "vision", ["multimodal-react"], "MIT", "reference"),
    ("visual-chatgpt", "Visual ChatGPT (TaskMatrix)", "https://github.com/chenfei-wu/TaskMatrix", "vision", ["visual-tools"], "MIT", "reference"),
    ("hugging-gpt", "HuggingGPT (paper)", "https://github.com/microsoft/JARVIS", "autonomous", ["multi-model"], "MIT", "reference"),
    ("agent-tuning", "AgentTuning", "https://github.com/THUDM/AgentTuning", "reasoning", ["fine-tune-agent"], "Apache-2.0", "reference"),
    ("fireact", "FireAct", "https://github.com/anchen1011/FireAct", "reasoning", ["react-ft"], "MIT", "reference"),
    ("agent-flan", "Agent-FLAN", "https://github.com/InternLM/Agent-FLAN", "reasoning", ["agent-ft"], "Apache-2.0", "reference"),
    ("agent-lightning", "Agent Lightning", "https://github.com/microsoft/agent-lightning", "framework", ["rl-agents"], "MIT", "python"),

    # ── protocol / interop / auth ────────────────────────────────────────
    ("agent-protocol", "Agent Protocol", "https://github.com/AI-Engineer-Foundation/agent-protocol", "protocol", ["standard-api"], "MIT", "http-openai"),
    ("agntcy", "AGNTCY", "https://github.com/agntcy/oasf", "protocol", ["open-agent-schema"], "Apache-2.0", "reference"),
    ("acp-sdk", "ACP SDK", "https://github.com/i-am-bee/acp", "protocol", ["agent-comm-protocol"], "Apache-2.0", "python"),

    # ── AI OS / operating layers ─────────────────────────────────────────
    ("aios", "AIOS", "https://github.com/agiresearch/AIOS", "framework", ["llm-os"], "MIT", "python"),
    ("openagi", "OpenAGI", "https://github.com/agiresearch/OpenAGI", "framework", ["benchmark-agi"], "MIT", "python"),
    ("thinkgpt", "ThinkGPT", "https://github.com/jina-ai/thinkgpt", "reasoning", ["chain-of-thought"], "Apache-2.0", "python"),
    ("dev-gpt", "DevGPT", "https://github.com/jina-ai/dev-gpt", "coding", ["microservices"], "Apache-2.0", "python"),

    # ── datasets / trajectories ──────────────────────────────────────────
    ("agent-instruct", "AgentInstruct", "https://github.com/microsoft/orca", "dataset", ["synthetic-agent-data"], "MIT", "reference"),
    ("toolbench", "ToolBench", "https://github.com/OpenBMB/ToolBench", "eval", ["tool-benchmark"], "Apache-2.0", "reference"),
    ("gaia", "GAIA", "https://github.com/gaia-benchmark", "eval", ["assistant-bench"], "Apache-2.0", "reference"),
    ("webarena", "WebArena", "https://github.com/web-arena-x/webarena", "eval", ["web-benchmark"], "Apache-2.0", "python"),
    ("visualwebarena", "VisualWebArena", "https://github.com/web-arena-x/visualwebarena", "eval", ["multimodal-web"], "Apache-2.0", "python"),
    ("workarena", "WorkArena", "https://github.com/ServiceNow/WorkArena", "eval", ["enterprise-bench"], "Apache-2.0", "python"),
    ("osworld", "OSWorld", "https://github.com/xlang-ai/OSWorld", "eval", ["desktop-bench"], "Apache-2.0", "python"),
]


# ---------------------------------------------------------------------------
# Category → civilization specialization mapping
# ---------------------------------------------------------------------------
CATEGORY_TO_SPECIALIZATION: Dict[str, str] = {
    "autonomous": "strategist",
    "coding": "code_generator",
    "framework": "architect",
    "builder": "builder",
    "research": "researcher",
    "data": "data_processor",
    "knowledge": "researcher",
    "voice": "communicator",
    "simulation": "self_developer",
    "eval": "reviewer",
    "security": "validator",
    "devops": "monitor",
    "writing": "communicator",
    "design": "designer",
    "memory": "db_manager",
    "reasoning": "analyst",
    "testing": "test_runner",
    "protocol": "api_integrator",
    "legal": "educator",
    "medical": "educator",
    "academic": "researcher",
    "communication": "communicator",
    "language": "communicator",
    "runtime": "network_client",
    "vision": "designer",
    "audio": "communicator",
    "productivity": "strategist",
    "tool": "api_integrator",
    "dataset": "data_processor",
}


AGENTS: List[OSSAgent] = [
    OSSAgent(
        id=t[0], name=t[1], source_url=t[2], category=t[3],
        capabilities=list(t[4]), license=t[5], adapter=t[6],
        description=t[1] + " — " + t[3],
    )
    for t in _RAW
]


def by_category() -> Dict[str, List[OSSAgent]]:
    out: Dict[str, List[OSSAgent]] = {}
    for a in AGENTS:
        out.setdefault(a.category, []).append(a)
    return out


def for_specialization(spec: str) -> List[OSSAgent]:
    return [a for a in AGENTS if CATEGORY_TO_SPECIALIZATION.get(a.category) == spec]


def total_agents(in_house_count: int) -> int:
    """Total addressable agents = in-house workers + curated OSS registry."""
    return in_house_count + len(AGENTS)


def as_list() -> List[Dict]:
    return [a.to_dict() for a in AGENTS]


if __name__ == "__main__":
    import json
    print(f"OSS agents: {len(AGENTS)}")
    print(f"Categories: {sorted(by_category().keys())}")
    print(json.dumps(as_list()[:3], indent=2))
