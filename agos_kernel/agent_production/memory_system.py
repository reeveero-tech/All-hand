"""
Agent Memory System

Provides persistent memory for the civilization.
Features:
- Long-term memory storage
- Cross-session persistence
- Knowledge sharing between agents
- Experience retention
"""

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("agos.agent_production.memory")


class MemoryType(str, Enum):
    """Types of memory."""
    EPISODIC = "episodic"      # Specific experiences
    SEMANTIC = "semantic"       # General knowledge
    PROCEDURAL = "procedural"    # How to do things
    WORKING = "working"         # Short-term context


@dataclass
class MemoryEntry:
    """A single memory entry."""
    memory_id: str
    content: str
    memory_type: MemoryType
    tags: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5  # 0-1 importance score
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_accessed: datetime = field(default_factory=datetime.utcnow)
    access_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "tags": list(self.tags),
            "metadata": self.metadata,
            "importance": self.importance,
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
            "access_count": self.access_count,
        }
    
    def touch(self) -> None:
        """Update access time and count."""
        self.last_accessed = datetime.utcnow()
        self.access_count += 1


@dataclass
class MemoryQuery:
    """Query parameters for memory search."""
    query: Optional[str] = None
    memory_type: Optional[MemoryType] = None
    tags: Optional[Set[str]] = None
    min_importance: float = 0.0
    since: Optional[datetime] = None
    limit: int = 10


class MemorySystem:
    """
    Memory system for the civilization.
    
    Provides persistent storage for:
    - Agent experiences
    - Task outcomes
    - Knowledge accumulation
    - Cross-agent learning
    """
    
    def __init__(
        self,
        max_entries: int = 10000,
        persistence_file: Optional[str] = None,
    ):
        """
        Initialize memory system.
        
        Args:
            max_entries: Maximum memory entries to store
            persistence_file: Optional file for persistence
        """
        self._memories: Dict[str, MemoryEntry] = {}
        self._index_by_type: Dict[MemoryType, Set[str]] = defaultdict(set)
        self._index_by_tag: Dict[str, Set[str]] = defaultdict(set)
        self._persistence_file = persistence_file
        self._max_entries = max_entries
        
        logger.info(f"MemorySystem initialized (max_entries={max_entries})")
    
    async def store(
        self,
        content: str,
        memory_type: MemoryType,
        tags: Optional[Set[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        importance: float = 0.5,
        memory_id: Optional[str] = None,
    ) -> str:
        """
        Store a memory entry.
        
        Args:
            content: Memory content
            memory_type: Type of memory
            tags: Optional tags
            metadata: Optional metadata
            importance: Importance score (0-1)
            memory_id: Optional custom ID
            
        Returns:
            Memory ID
        """
        memory_id = memory_id or f"mem_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
        
        entry = MemoryEntry(
            memory_id=memory_id,
            content=content,
            memory_type=memory_type,
            tags=tags or set(),
            metadata=metadata or {},
            importance=importance,
        )
        
        # Store entry
        self._memories[memory_id] = entry
        
        # Update indexes
        self._index_by_type[memory_type].add(memory_id)
        for tag in entry.tags:
            self._index_by_tag[tag].add(memory_id)
        
        # Prune if necessary
        await self._prune()
        
        logger.debug(f"Memory stored: {memory_id} ({memory_type.value})")
        return memory_id
    
    async def retrieve(self, memory_id: str) -> Optional[MemoryEntry]:
        """
        Retrieve a specific memory.
        
        Args:
            memory_id: Memory ID
            
        Returns:
            Memory entry or None
        """
        entry = self._memories.get(memory_id)
        if entry:
            entry.touch()
        return entry
    
    async def search(self, query: MemoryQuery) -> List[MemoryEntry]:
        """
        Search memories.
        
        Args:
            query: Search parameters
            
        Returns:
            List of matching memories
        """
        results = set(self._memories.keys())
        
        # Filter by type
        if query.memory_type:
            results &= self._index_by_type.get(query.memory_type, set())
        
        # Filter by tags
        if query.tags:
            tag_results = None
            for tag in query.tags:
                tag_set = self._index_by_tag.get(tag, set())
                if tag_results is None:
                    tag_results = tag_set
                else:
                    tag_results &= tag_set
            if tag_results:
                results &= tag_results
            elif query.tags:
                return []
        
        # Filter by content (simple substring match)
        if query.query:
            results = {
                mid for mid in results
                if query.query.lower() in self._memories[mid].content.lower()
            }
        
        # Filter by importance
        results = {
            mid for mid in results
            if self._memories[mid].importance >= query.min_importance
        }
        
        # Filter by date
        if query.since:
            results = {
                mid for mid in results
                if self._memories[mid].created_at >= query.since
            }
        
        # Sort by importance and recency
        memories = [self._memories[mid] for mid in results]
        memories.sort(
            key=lambda m: (m.importance, m.last_accessed.timestamp()),
            reverse=True,
        )
        
        return memories[:query.limit]
    
    async def recall(
        self,
        context: str,
        memory_type: Optional[MemoryType] = None,
        limit: int = 5,
    ) -> List[MemoryEntry]:
        """
        Recall relevant memories based on context.
        
        Args:
            context: Context to search for
            memory_type: Optional type filter
            limit: Max results
            
        Returns:
            Relevant memories
        """
        query = MemoryQuery(
            query=context,
            memory_type=memory_type,
            min_importance=0.3,
            limit=limit,
        )
        return await self.search(query)
    
    async def forget(self, memory_id: str) -> bool:
        """
        Forget a memory.
        
        Args:
            memory_id: Memory ID to forget
            
        Returns:
            True if forgotten, False if not found
        """
        if memory_id not in self._memories:
            return False
        
        entry = self._memories[memory_id]
        
        # Remove from indexes
        self._index_by_type[entry.memory_type].discard(memory_id)
        for tag in entry.tags:
            self._index_by_tag[tag].discard(memory_id)
        
        # Remove from storage
        del self._memories[memory_id]
        
        logger.debug(f"Memory forgotten: {memory_id}")
        return True
    
    async def learn_from_task(
        self,
        agent_id: str,
        task_type: str,
        prompt: str,
        result: Dict[str, Any],
        success: bool,
    ) -> None:
        """
        Store task outcome as a memory.
        
        Args:
            agent_id: Agent that performed task
            task_type: Type of task
            prompt: Task prompt
            result: Task result
            success: Whether task succeeded
        """
        content = f"Task '{task_type}' by {agent_id}: {'SUCCESS' if success else 'FAILED'}"
        if not success:
            content += f" - Error: {result.get('error', 'unknown')}"
        
        await self.store(
            content=content,
            memory_type=MemoryType.EPISODIC,
            tags={task_type, agent_id, "success" if success else "failure"},
            metadata={
                "agent_id": agent_id,
                "task_type": task_type,
                "prompt": prompt,
                "result": result,
                "success": success,
            },
            importance=0.7 if success else 0.9,  # Failures are more important
        )
    
    async def get_knowledge(self, topic: str, limit: int = 10) -> List[MemoryEntry]:
        """
        Get knowledge about a topic.
        
        Args:
            topic: Topic to query
            limit: Max results
            
        Returns:
            Knowledge entries
        """
        return await self.search(MemoryQuery(
            query=topic,
            memory_type=MemoryType.SEMANTIC,
            min_importance=0.5,
            limit=limit,
        ))
    
    async def store_knowledge(
        self,
        content: str,
        topic: str,
        source: Optional[str] = None,
    ) -> str:
        """
        Store general knowledge.
        
        Args:
            content: Knowledge content
            topic: Topic/subject
            source: Optional source reference
            
        Returns:
            Memory ID
        """
        return await self.store(
            content=content,
            memory_type=MemoryType.SEMANTIC,
            tags={topic, "knowledge"},
            metadata={"source": source} if source else {},
            importance=0.6,
        )
    
    async def _prune(self) -> None:
        """Prune old/less important memories if over limit."""
        if len(self._memories) <= self._max_entries:
            return
        
        # Sort by importance and access time
        sorted_memories = sorted(
            self._memories.values(),
            key=lambda m: (m.importance, m.last_accessed.timestamp()),
        )
        
        # Remove least important/oldest
        to_remove = len(self._memories) - self._max_entries
        for memory in sorted_memories[:to_remove]:
            await self.forget(memory.memory_id)
        
        logger.info(f"Pruned {to_remove} memories")
    
    async def persist(self) -> None:
        """Persist memory to disk."""
        if not self._persistence_file:
            return
        
        data = {
            memory_id: entry.to_dict()
            for memory_id, entry in self._memories.items()
        }
        
        try:
            with open(self._persistence_file, 'w') as f:
                json.dump(data, f)
            logger.info(f"Memory persisted to {self._persistence_file}")
        except Exception as e:
            logger.error(f"Failed to persist memory: {e}")
    
    async def load(self) -> None:
        """Load memory from disk."""
        if not self._persistence_file:
            return
        
        import os
        if not os.path.exists(self._persistence_file):
            return
        
        try:
            with open(self._persistence_file, 'r') as f:
                data = json.load(f)
            
            for memory_id, entry_data in data.items():
                entry_data['tags'] = set(entry_data.get('tags', []))
                entry = MemoryEntry(**{
                    k: v for k, v in entry_data.items()
                    if k in MemoryEntry.__dataclass_fields__
                })
                self._memories[memory_id] = entry
                
                # Rebuild indexes
                self._index_by_type[entry.memory_type].add(memory_id)
                for tag in entry.tags:
                    self._index_by_tag[tag].add(memory_id)
            
            logger.info(f"Memory loaded from {self._persistence_file}")
        except Exception as e:
            logger.error(f"Failed to load memory: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        return {
            "total_memories": len(self._memories),
            "by_type": {
                mem_type.value: len(memory_ids)
                for mem_type, memory_ids in self._index_by_type.items()
            },
            "top_tags": [
                {"tag": tag, "count": len(memory_ids)}
                for tag, memory_ids in sorted(
                    self._index_by_tag.items(),
                    key=lambda x: len(x[1]),
                    reverse=True,
                )[:10]
            ],
            "max_entries": self._max_entries,
        }


# Global memory system
_memory_system: Optional[MemorySystem] = None


def get_memory_system() -> MemorySystem:
    """Get or create global memory system."""
    global _memory_system
    if _memory_system is None:
        _memory_system = MemorySystem()
    return _memory_system
