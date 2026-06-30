# Execution Scheduler

> **Not just a Queue. A smart Scheduler.**

---

## Scheduler Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Execution Scheduler                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐ │
│  │                   Multi-Queue System                     │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐│ │
│  │  │ Critical │ │   High  │ │  Normal  │ │Background││ │
│  │  │  Queue   │ │  Queue  │ │  Queue   │ │  Queue   ││ │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘│ │
│  └─────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐ │
│  │              Priority & Dependency Engine                │ │
│  │  - Priority scoring                                   │ │
│  │  - Dependency resolution                            │ │
│  │  - Deadline awareness                               │ │
│  │  - Resource balancing                               │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐ │
│  │                  Resource Allocator                    │ │
│  │  - CPU, Memory, GPU                                 │ │
│  │  - Bandwidth, Storage                               │ │
│  │  - Token budgets                                   │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Queue Types

```yaml
Queues:
  CRITICAL:
    priority: 1
    description: "Production down, urgent fixes"
    max_wait_time: "1 minute"
    preemption: true
    
  HIGH:
    priority: 2
    description: "Major features, important bugs"
    max_wait_time: "15 minutes"
    preemption: true
    
  NORMAL:
    priority: 3
    description: "Regular development"
    max_wait_time: "1 hour"
    preemption: false
    
  BACKGROUND:
    priority: 4
    description: "Non-urgent tasks, batch jobs"
    max_wait_time: "24 hours"
    preemption: false
    
  LEARNING:
    priority: 5
    description: "Learning and optimization tasks"
    max_wait_time: "1 week"
    preemption: false
```

---

## Priority Calculation

```python
def calculate_priority(execution: Execution) -> float:
    """
    Calculate priority score for an execution.
    """
    
    score = 0.0
    
    # Base priority from queue
    score += execution.queue.priority * 100
    
    # Deadline urgency
    if execution.deadline:
        hours_until_deadline = (execution.deadline - now()).hours
        if hours_until_deadline < 1:
            score += 500  # Critical
        elif hours_until_deadline < 4:
            score += 300  # High
        elif hours_until_deadline < 24:
            score += 100  # Medium
        else:
            score += 0
            
    # Dependency weight
    if execution.blocked_by:
        # More important if blocking others
        dependent_count = count_dependents(execution)
        score += dependent_count * 50
        
    # Cost optimization
    if execution.cost_efficient:
        score += 10
        
    # Resource availability
    if resources_available(execution):
        score += 20
        
    return score
```

---

## Scheduling Algorithm

```python
class ExecutionScheduler:
    """
    Smart execution scheduler.
    """
    
    async def schedule(self) -> list[Execution]:
        """
        Schedule executions from queues.
        """
        
        scheduled = []
        
        # 1. Check critical queue
        critical = await self.get_next("CRITICAL")
        if critical:
            scheduled.append(critical)
            
        # 2. Check high queue
        high = await self.get_next("HIGH")
        if high:
            scheduled.append(high)
            
        # 3. Balance normal and background
        normal = await self.get_next("NORMAL")
        background = await self.get_next("BACKGROUND")
        
        if normal and self.can_schedule(normal):
            scheduled.append(normal)
            
        if background and self.can_schedule(background):
            scheduled.append(background)
            
        # 4. Check learning queue (low priority)
        learning = await self.get_next("LEARNING")
        if learning and self.has_capacity():
            scheduled.append(learning)
            
        return scheduled
        
    async def get_next(self, queue_type: str) -> Execution:
        """
        Get next execution from queue.
        """
        
        queue = self.queues[queue_type]
        
        # Sort by priority score
        sorted_executions = sorted(
            queue.executions,
            key=lambda e: calculate_priority(e),
            reverse=True
        )
        
        # Return highest priority
        if sorted_executions:
            return sorted_executions[0]
            
        return None
```

---

## Resource Constraints

```yaml
ResourceConstraints:
  CPU:
    unit: "cores"
    limit_per_execution: 8
    limit_per_mission: 32
    limit_total: 1000
    
  MEMORY:
    unit: "MB"
    limit_per_execution: 16384  # 16GB
    limit_per_mission: 65536  # 64GB
    limit_total: 1000000
    
  GPU:
    unit: "units"
    limit_per_execution: 1
    limit_per_mission: 4
    limit_total: 100
    
  TOKENS:
    unit: "tokens"
    limit_per_execution: 100000
    limit_per_mission: 1000000
    limit_total: 10000000
    
  CONCURRENT:
    unit: "executions"
    limit_per_provider: 10
    limit_per_mission: 50
    limit_total: 1000
```

---

## Deadline Management

```yaml
DeadlineManagement:
  # How scheduler handles deadlines
  
  approach:
    - "Always respect hard deadlines"
    - "Preempt lower priority for critical deadlines"
    - "Warn when deadline at risk"
    
  strategies:
    deadline_critical:
      hours_left: < 1
      action: "Preempt, allocate maximum resources"
      
    deadline_high:
      hours_left: < 4
      action: "High priority, reserve resources"
      
    deadline_medium:
      hours_left: < 24
      action: "Normal priority, monitor"
      
    deadline_low:
      hours_left: > 24
      action: "Can be delayed if needed"
```

---

## Parallel Execution

```python
class ParallelScheduler:
    """
    Schedules parallel executions.
    """
    
    async def schedule_parallel(
        self,
        executions: list[Execution]
    ) -> list[ExecutionGroup]:
        """
        Group executions for parallel execution.
        """
        
        # 1. Identify independent executions
        independent = self.find_independent(executions)
        
        # 2. Group by resource requirements
        groups = []
        
        for exec_group in self.group_by_resources(independent):
            # 3. Check if can run in parallel
            if self.can_run_parallel(exec_group):
                groups.append(exec_group)
            else:
                # 4. Schedule sequentially
                for ex in exec_group:
                    groups.append([ex])
                    
        return groups
        
    def find_independent(
        self,
        executions: list[Execution]
    ) -> list[Execution]:
        """
        Find executions with no dependencies.
        """
        
        independent = []
        
        for execution in executions:
            # Check if all dependencies are complete
            deps_met = all(
                dep.state == State.COMPLETED
                for dep in execution.dependencies
            )
            
            if deps_met:
                independent.append(execution)
                
        return independent
```

---

## Example: Scheduling 100 Tasks

```yaml
Scenario:
  100 tasks
  10 critical
  30 high priority
  40 normal
  20 background
  All independent (can run parallel)

Scheduler Decision:
  1. Schedule 10 critical first
  2. Schedule 30 high
  3. Use remaining capacity for normal
  4. Fill remaining with background

Result:
  - All critical tasks started immediately
  - High priority tasks next
  - Normal tasks running in parallel
  - Background tasks queued
```

---

## Related Documents

- [Execution-Object.md](./01-Execution-Object.md)
- [Parallel-Execution.md](./04-Parallel-Execution.md)
