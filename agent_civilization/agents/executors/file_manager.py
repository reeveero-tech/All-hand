class FileManagerAgent(BaseAgent):
    """A simple executor agent that logs file operations."""

    async def _execute_work(self, work_item: dict):
        # Simulate a file operation
        operation = work_item.get("operation")
        target = work_item.get("target")
        details = f"Executing {operation} on {target}"
        logger.info(details)
        # In a real system, file I/O would be performed here.
        await asyncio.sleep(0.1)

    async def _handle_message(self, message: Message):
        # Respond to status requests
        if message.content.get("action") == "ping":
            reply = Message(
                sender=self.name,
                recipient=message.sender,
                content={"status": "alive"},
                shaders="",
                timestamp=0,
            )
            await self.communication_hub.send_message(reply)
