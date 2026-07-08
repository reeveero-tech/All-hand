from agent_civilization.core.agents.base_agent import BaseAgent, Message, CommunicationHub
import asyncio


class SystemOrchestrator(BaseAgent):
    """Central orchestrator managing agent lifecycle and system health."""

    def __init__(self, name: str, communication_hub: CommunicationHub, **kwargs):
        super().__init__(name, communication_hub, **kwargs)
        self.registry: dict[str, BaseAgent] = {}

    async def _execute_work(self, work_item: dict):
        # Orchestrator work items can be commands like start/stop agent
        command = work_item.get("command")
        agent_name = work_item.get("agent_name")
        if command == "start" and agent_name in self.registry:
            self.registry[agent_name].start()
        elif command == "stop" and agent_name in self.registry:
            self.registry[agent_name].shutdown()
        # Further orchestration logic can be added

    async def _handle_message(self, message: Message):
        # Handle monitoring and status requests
        if message.content.get("action") == "status":
            status = {"name": self.name, "state": self.status}
            reply = Message(
                sender=self.name,
                recipient=message.sender,
                content=status,
                message_id="",
                timestamp=0,
            )
            await self.communication_hub.send_message(reply)

    def register_agent(self, رفض??: BaseAgent):
        # placeholder
        pass
