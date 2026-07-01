# AGOS Kernel v1.0.0

> **The only executable component in AGOS.**

---

## Directory Structure

```
agos-kernel/
├── core/
├── context/
├── decision/
├── execution/
├── mission/
├── registry/
├── contracts/
├── events/
├── interfaces/
├── shared/
├── capabilities/
├── providers/
├── discovery/
├── resolvers/
├── pipeline/
├── mission-engine/
├── skill-engine/
├── container/
├── event-engine/
├── bootstrapper/
├── contract-engine/
├── diagnostics/
├── knowledge/
├── models/
├── decision-pipeline/
├── execution-context/
├── api/
├── runtime/               ← EXEC-000026
├── scheduler/             ← EXEC-000027
├── resource-manager/       ← EXEC-000028
├── state-store/           ← EXEC-000029
├── observability/          ← EXEC-000030
├── sdks/
├── main.py
├── test_integration.py
└── test_validation.py
```

---

## Runtime Layer

| Component | Description |
|-----------|-------------|
| Runtime | Runtime lifecycle management |
| Scheduler | Mission queuing and dispatching |
| Resource Manager | CPU, Memory, Execution tracking |
| State Store | Persistent state with crash recovery |
| Observability | Logging, Metrics, Tracing, Health |

---

## Version

```
v1.0.0 - EXEC-000026 to EXEC-000030
RUNTIME LAYER - COMPLETE
```

---

*AGOS Kernel - The foundation of everything.*
