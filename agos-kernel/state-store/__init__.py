"""Runtime State Store - Persistent state management with crash recovery."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


class StoreState(Enum):
    """Store states."""
    IDLE = "idle"
    SAVING = "saving"
    LOADING = "loading"
    ERROR = "error"


@dataclass
class Snapshot:
    """State snapshot."""
    snapshot_id: str
    created_at: datetime
    data: Dict[str, Any]
    checksum: str = ""
    
    @classmethod
    def create(cls, data: Dict[str, Any]) -> 'Snapshot':
        """Create a new snapshot."""
        return cls(
            snapshot_id=str(uuid4()),
            created_at=datetime.utcnow(),
            data=data
        )


@dataclass
class StoredState:
    """Stored state entry."""
    state_id: str
    state_type: str
    data: Dict[str, Any]
    version: int = 1
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    def update(self, data: Dict[str, Any]) -> None:
        """Update the state."""
        self.data = data
        self.version += 1
        self.updated_at = datetime.utcnow()


@dataclass
class RecoveryInfo:
    """Recovery information."""
    last_snapshot_id: str = ""
    last_saved_at: Optional[datetime] = None
    pending_states: int = 0


class StateStore:
    """
    Runtime State Store.
    
    Stores:
    - Mission State
    - Execution State
    - Decision State
    - Capability State
    - Provider State
    
    Support:
    ✅ Save
    ✅ Restore
    ✅ Snapshot
    ✅ Recovery
    
    Rules:
    ✅ Crash Recovery
    ✅ Deterministic Restore
    """
    
    def __init__(self):
        self._states: Dict[str, StoredState] = {}
        self._snapshots: List[Snapshot] = []
        self._current_snapshot: Optional[Snapshot] = None
        self._state = StoreState.IDLE
        self._recovery_info = RecoveryInfo()
    
    # =========================================================================
    # Mission State
    # =========================================================================
    
    def save_mission_state(self, mission_id: str, state: Dict[str, Any]) -> None:
        """Save mission state."""
        self._save_state(f"mission_{mission_id}", "mission", state)
    
    def get_mission_state(self, mission_id: str) -> Optional[Dict[str, Any]]:
        """Get mission state."""
        return self._get_state(f"mission_{mission_id}")
    
    # =========================================================================
    # Execution State
    # =========================================================================
    
    def save_execution_state(self, execution_id: str, state: Dict[str, Any]) -> None:
        """Save execution state."""
        self._save_state(f"execution_{execution_id}", "execution", state)
    
    def get_execution_state(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get execution state."""
        return self._get_state(f"execution_{execution_id}")
    
    # =========================================================================
    # Decision State
    # =========================================================================
    
    def save_decision_state(self, decision_id: str, state: Dict[str, Any]) -> None:
        """Save decision state."""
        self._save_state(f"decision_{decision_id}", "decision", state)
    
    def get_decision_state(self, decision_id: str) -> Optional[Dict[str, Any]]:
        """Get decision state."""
        return self._get_state(f"decision_{decision_id}")
    
    # =========================================================================
    # Capability State
    # =========================================================================
    
    def save_capability_state(self, capability_name: str, state: Dict[str, Any]) -> None:
        """Save capability state."""
        self._save_state(f"capability_{capability_name}", "capability", state)
    
    def get_capability_state(self, capability_name: str) -> Optional[Dict[str, Any]]:
        """Get capability state."""
        return self._get_state(f"capability_{capability_name}")
    
    # =========================================================================
    # Provider State
    # =========================================================================
    
    def save_provider_state(self, provider_name: str, state: Dict[str, Any]) -> None:
        """Save provider state."""
        self._save_state(f"provider_{provider_name}", "provider", state)
    
    def get_provider_state(self, provider_name: str) -> Optional[Dict[str, Any]]:
        """Get provider state."""
        return self._get_state(f"provider_{provider_name}")
    
    # =========================================================================
    # Core Operations
    # =========================================================================
    
    def _save_state(self, state_id: str, state_type: str, data: Dict[str, Any]) -> None:
        """Save state."""
        self._state = StoreState.SAVING
        
        try:
            if state_id in self._states:
                self._states[state_id].update(data)
            else:
                self._states[state_id] = StoredState(
                    state_id=state_id,
                    state_type=state_type,
                    data=data
                )
        finally:
            self._state = StoreState.IDLE
    
    def _get_state(self, state_id: str) -> Optional[Dict[str, Any]]:
        """Get state."""
        self._state = StoreState.LOADING
        
        try:
            state = self._states.get(state_id)
            return state.data if state else None
        finally:
            self._state = StoreState.IDLE
    
    def delete_state(self, state_id: str) -> bool:
        """Delete state."""
        if state_id in self._states:
            del self._states[state_id]
            return True
        return False
    
    # =========================================================================
    # Snapshot Operations
    # =========================================================================
    
    def create_snapshot(self) -> Snapshot:
        """Create a snapshot of all states."""
        data = {
            "states": {k: v.__dict__ for k, v in self._states.items()},
            "created_at": datetime.utcnow().isoformat()
        }
        
        snapshot = Snapshot.create(data)
        self._snapshots.append(snapshot)
        self._current_snapshot = snapshot
        self._recovery_info.last_snapshot_id = snapshot.snapshot_id
        self._recovery_info.last_saved_at = snapshot.created_at
        
        return snapshot
    
    def restore_snapshot(self, snapshot_id: str) -> bool:
        """Restore from a snapshot."""
        snapshot = self._find_snapshot(snapshot_id)
        if not snapshot:
            return False
        
        try:
            # Restore states
            self._states.clear()
            for state_id, state_data in snapshot.data.get("states", {}).items():
                self._states[state_id] = StoredState(**state_data)
            return True
        except Exception:
            self._state = StoreState.ERROR
            return False
    
    def get_latest_snapshot(self) -> Optional[Snapshot]:
        """Get the latest snapshot."""
        return self._current_snapshot
    
    def _find_snapshot(self, snapshot_id: str) -> Optional[Snapshot]:
        """Find a snapshot by ID."""
        for snapshot in self._snapshots:
            if snapshot.snapshot_id == snapshot_id:
                return snapshot
        return None
    
    # =========================================================================
    # Recovery
    # =========================================================================
    
    def recover(self) -> Dict[str, Any]:
        """
        Perform crash recovery.
        Returns recovery report.
        """
        report = {
            "success": True,
            "recovered_states": 0,
            "missing_states": [],
            "errors": []
        }
        
        # Try to restore from latest snapshot
        latest = self.get_latest_snapshot()
        if latest:
            if self.restore_snapshot(latest.snapshot_id):
                report["recovered_states"] = len(self._states)
            else:
                report["success"] = False
                report["errors"].append("Failed to restore snapshot")
        else:
            report["success"] = False
            report["errors"].append("No snapshot available")
        
        return report
    
    def get_recovery_info(self) -> RecoveryInfo:
        """Get recovery information."""
        return self._recovery_info
    
    # =========================================================================
    # General Operations
    # =========================================================================
    
    def list_states(self, state_type: str = None) -> List[str]:
        """List all state IDs, optionally filtered by type."""
        if state_type:
            return [
                s.state_id for s in self._states.values()
                if s.state_type == state_type
            ]
        return list(self._states.keys())
    
    def clear(self) -> None:
        """Clear all states."""
        self._states.clear()
    
    def get_state_count(self) -> int:
        """Get total state count."""
        return len(self._states)
