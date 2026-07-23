"""
Learning Runtime

Allows agents to learn from experience and improve over time.
Features:
- Experience tracking
- Pattern recognition
- Success/failure analysis
- Agent evolution recommendations
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("agos.agent_production.learning")


@dataclass
class LearningPattern:
    """Represents a learned pattern."""
    pattern_id: str
    task_type: str
    approach: str
    success_rate: float
    avg_duration_ms: float
    sample_count: int
    last_updated: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "task_type": self.task_type,
            "approach": self.approach,
            "success_rate": self.success_rate,
            "avg_duration_ms": self.avg_duration_ms,
            "sample_count": self.sample_count,
            "last_updated": self.last_updated.isoformat(),
        }


@dataclass
class AgentExperience:
    """Tracks an agent's learning history."""
    agent_id: str
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    patterns: Dict[str, LearningPattern] = field(default_factory=dict)
    skills: Dict[str, float] = field(default_factory=dict)  # skill_name -> proficiency (0-1)
    
    @property
    def success_rate(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.successful_tasks / self.total_tasks
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "total_tasks": self.total_tasks,
            "successful_tasks": self.successful_tasks,
            "failed_tasks": self.failed_tasks,
            "success_rate": round(self.success_rate, 3),
            "patterns": {k: v.to_dict() for k, v in self.patterns.items()},
            "skills": self.skills,
        }


class LearningRuntime:
    """
    Runtime for agent learning and evolution.
    
    Tracks agent experience and provides insights for improvement.
    """
    
    def __init__(self, decay_factor: float = 0.95):
        """
        Initialize learning runtime.
        
        Args:
            decay_factor: How quickly old patterns decay (0-1)
        """
        self._experience: Dict[str, AgentExperience] = {}
        self._global_patterns: Dict[str, LearningPattern] = {}
        self._decay_factor = decay_factor
        
        logger.info(f"LearningRuntime initialized (decay={decay_factor})")
    
    def record_task(
        self,
        agent_id: str,
        task_type: str,
        approach: str,
        success: bool,
        duration_ms: float,
    ) -> None:
        """
        Record a task execution for learning.
        
        Args:
            agent_id: Agent that executed the task
            task_type: Type of task
            approach: Approach used
            success: Whether task succeeded
            duration_ms: Task duration
        """
        # Get or create agent experience
        if agent_id not in self._experience:
            self._experience[agent_id] = AgentExperience(agent_id=agent_id)
        
        exp = self._experience[agent_id]
        exp.total_tasks += 1
        if success:
            exp.successful_tasks += 1
        else:
            exp.failed_tasks += 1
        
        # Update or create pattern
        pattern_key = f"{task_type}:{approach}"
        
        if pattern_key in exp.patterns:
            pattern = exp.patterns[pattern_key]
            # Update with new data
            pattern.sample_count += 1
            pattern.success_rate = (
                (pattern.success_rate * (pattern.sample_count - 1) + (1 if success else 0))
                / pattern.sample_count
            )
            pattern.avg_duration_ms = (
                (pattern.avg_duration_ms * (pattern.sample_count - 1) + duration_ms)
                / pattern.sample_count
            )
            pattern.last_updated = datetime.utcnow()
        else:
            exp.patterns[pattern_key] = LearningPattern(
                pattern_id=pattern_key,
                task_type=task_type,
                approach=approach,
                success_rate=1.0 if success else 0.0,
                avg_duration_ms=duration_ms,
                sample_count=1,
            )
        
        # Update global patterns
        self._update_global_pattern(task_type, approach)
        
        logger.debug(f"Recorded task for {agent_id}: {task_type} -> {success}")
    
    def _update_global_pattern(self, task_type: str, approach: str) -> None:
        """Update global pattern across all agents."""
        # Aggregate patterns from all agents
        task_patterns = [
            exp.patterns.get(f"{task_type}:{approach}")
            for exp in self._experience.values()
            if f"{task_type}:{approach}" in exp.patterns
        ]
        
        if not task_patterns:
            return
        
        # Calculate global stats
        total_samples = sum(p.sample_count for p in task_patterns)
        global_success = sum(p.success_rate * p.sample_count for p in task_patterns) / total_samples
        global_duration = sum(p.avg_duration_ms * p.sample_count for p in task_patterns) / total_samples
        
        pattern_key = f"{task_type}:{approach}"
        self._global_patterns[pattern_key] = LearningPattern(
            pattern_id=pattern_key,
            task_type=task_type,
            approach=approach,
            success_rate=global_success,
            avg_duration_ms=global_duration,
            sample_count=total_samples,
        )
    
    def get_best_approach(self, task_type: str) -> Optional[str]:
        """
        Get the best approach for a task type based on learning.
        
        Args:
            task_type: Type of task
            
        Returns:
            Best approach or None if no data
        """
        patterns = [
            p for p in self._global_patterns.values()
            if p.task_type == task_type
        ]
        
        if not patterns:
            return None
        
        # Return approach with highest success rate
        best = max(patterns, key=lambda p: (p.success_rate, -p.avg_duration_ms))
        return best.approach
    
    def get_agent_insights(self, agent_id: str) -> Dict[str, Any]:
        """
        Get learning insights for an agent.
        
        Args:
            agent_id: Agent ID
            
        Returns:
            Insights dictionary
        """
        exp = self._experience.get(agent_id)
        if not exp:
            return {"status": "no_data"}
        
        # Find strengths and weaknesses
        task_patterns = defaultdict(list)
        for pattern in exp.patterns.values():
            task_patterns[pattern.task_type].append(pattern)
        
        strengths = []
        weaknesses = []
        
        for task_type, patterns in task_patterns.items():
            avg_rate = sum(p.success_rate for p in patterns) / len(patterns)
            if avg_rate > 0.8:
                strengths.append(task_type)
            elif avg_rate < 0.5:
                weaknesses.append(task_type)
        
        # Get best and worst patterns
        sorted_patterns = sorted(
            exp.patterns.values(),
            key=lambda p: (p.success_rate, -p.avg_duration_ms),
        )
        
        return {
            "agent_id": agent_id,
            "total_tasks": exp.total_tasks,
            "success_rate": round(exp.success_rate, 3),
            "strengths": strengths,
            "weaknesses": weaknesses,
            "best_patterns": [p.to_dict() for p in sorted_patterns[-3:]],
            "worst_patterns": [p.to_dict() for p in sorted_patterns[:3]],
        }
    
    def get_evolution_recommendation(self, agent_id: str) -> Dict[str, Any]:
        """
        Get recommendation for agent evolution.
        
        Args:
            agent_id: Agent ID
            
        Returns:
            Evolution recommendation
        """
        exp = self._experience.get(agent_id)
        if not exp or exp.total_tasks < 10:
            return {
                "recommendation": "not_ready",
                "reason": "Insufficient data for evolution",
            }
        
        # Analyze performance trends
        recent_success = sum(
            1 for p in exp.patterns.values()
            if (datetime.utcnow() - p.last_updated) < timedelta(days=7)
            and p.success_rate > 0.7
        )
        
        if exp.success_rate > 0.9 and recent_success > 5:
            return {
                "recommendation": "specialize",
                "reason": "Agent is performing well, should specialize",
                "suggested_focus": self.get_best_approach(list(exp.patterns.values())[0].task_type)
                    if exp.patterns else None,
            }
        elif exp.success_rate < 0.5:
            return {
                "recommendation": "retrain",
                "reason": "Agent success rate is low, needs retraining",
                "focus_areas": self.get_agent_insights(agent_id).get("weaknesses", []),
            }
        else:
            return {
                "recommendation": "continue",
                "reason": "Agent is performing adequately",
                "confidence": exp.success_rate,
            }
    
    def apply_decay(self) -> None:
        """Apply time-based decay to patterns."""
        cutoff = datetime.utcnow() - timedelta(days=30)
        
        for exp in self._experience.values():
            for pattern in list(exp.patterns.values()):
                if pattern.last_updated < cutoff:
                    # Decay the pattern
                    pattern.success_rate *= self._decay_factor
                    pattern.sample_count = max(1, int(pattern.sample_count * self._decay_factor))
    
    def get_global_stats(self) -> Dict[str, Any]:
        """Get global learning statistics."""
        total_tasks = sum(e.total_tasks for e in self._experience.values())
        total_success = sum(e.successful_tasks for e in self._experience.values())
        
        return {
            "total_agents_learning": len(self._experience),
            "total_tasks": total_tasks,
            "overall_success_rate": round(total_success / total_tasks if total_tasks else 0, 3),
            "unique_patterns": len(self._global_patterns),
            "top_patterns": [
                p.to_dict() for p in sorted(
                    self._global_patterns.values(),
                    key=lambda p: p.sample_count,
                    reverse=True
                )[:10]
            ],
        }
    
    def export_knowledge(self) -> Dict[str, Any]:
        """Export all learning knowledge."""
        return {
            "global_patterns": {k: v.to_dict() for k, v in self._global_patterns.items()},
            "agent_experiences": {k: v.to_dict() for k, v in self._experience.items()},
            "stats": self.get_global_stats(),
        }


# Global learning runtime
_learning_runtime: Optional[LearningRuntime] = None


def get_learning_runtime() -> LearningRuntime:
    """Get or create global learning runtime."""
    global _learning_runtime
    if _learning_runtime is None:
        _learning_runtime = LearningRuntime()
    return _learning_runtime
