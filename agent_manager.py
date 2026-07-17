#!/usr/bin/env python3
"""Real agent manager for the AGOS AI Agent Civilization.

The manager now has one policy: every generated local agent is a proxy to the
real dispatcher. If an open-source source URL exists, it is cloned or updated;
if it does not, the generated file still executes real AGOS capability code
instead of returning canned data.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml

try:
    from git import Repo
    GITPYTHON_AVAILABLE = True
except Exception:  # pragma: no cover
    GITPYTHON_AVAILABLE = False

CATALOGUE_FILE = "agents.yaml"
AGENTS_ROOT = Path("agent_civilization")


def load_catalogue() -> list[dict[str, Any]]:
    if not os.path.exists(CATALOGUE_FILE):
        raise FileNotFoundError(f"Catalogue file {CATALOGUE_FILE} not found")
    with open(CATALOGUE_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("agents", [])


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def run_cmd(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def _clone_via_subprocess(repo_url: str, branch: str, target_dir: Path) -> bool:
    if target_dir.exists() and (target_dir / ".git").exists():
        result = run_cmd(["git", "-C", str(target_dir), "pull", "origin", branch])
        return result.returncode == 0
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    result = run_cmd(["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(target_dir)])
    return result.returncode == 0


def clone_agent(spec: dict[str, Any]) -> bool:
    name = spec["name"]
    repo_url = spec["source"]
    branch = spec.get("branch", "main")
    target_subdir = spec.get("specialization")
    target_dir = AGENTS_ROOT / target_subdir if target_subdir else AGENTS_ROOT / name
    ensure_dir(target_dir.parent)
    if GITPYTHON_AVAILABLE:
        try:
            if target_dir.exists() and (target_dir / ".git").exists():
                Repo(str(target_dir)).remotes.origin.pull()
            else:
                if target_dir.exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
                Repo.clone_from(repo_url, to_path=str(target_dir), branch=branch, depth=1)
            print(f"ready: {name} -> {target_dir}")
            return True
        except Exception as exc:
            print(f"gitpython failed for {name}: {exc}; trying git CLI")
    ok = _clone_via_subprocess(repo_url, branch, target_dir)
    print(("ready" if ok else "clone_failed") + f": {name} -> {target_dir}")
    return ok


def _class_name(name: str) -> str:
    return "".join(part.capitalize() for part in name.replace("-", "_").split("_")) + "Agent"


def generate_dispatch_proxy(spec: dict[str, Any]) -> Path:
    """Generate a local agent that routes all work into the real dispatcher."""
    name = spec["name"]
    specialization = (spec.get("specialization") or "agents/real/generic").split("/")[-1]
    class_name = _class_name(name)
    target_dir = AGENTS_ROOT / (spec.get("specialization") or name)
    ensure_dir(target_dir)
    init_file = target_dir / "__init__.py"
    agent_file = target_dir / f"{name}.py"
    init_file.write_text(f'"""{name} real dispatch package."""\nfrom .{name} import {class_name}\n', encoding="utf-8")
    source_repr = repr(spec.get("source"))
    agent_file.write_text(f'''#!/usr/bin/env python3
"""{name}: generated real-dispatch AGOS worker."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from agent_civilization.core.agents.base_agent import BaseAgent, Message
from agent_civilization.core.agents.communication_hub import CommunicationHub
from agent_civilization.core.agents.real_dispatcher import dispatch

logger = logging.getLogger("{name}")


class {class_name}(BaseAgent):
    def __init__(self, name: str, communication_hub: CommunicationHub, **kwargs):
        super().__init__(name, communication_hub, **kwargs)
        self.specialization = "{specialization}"
        self.source_url = {source_repr}
        self.task_history: list[dict[str, Any]] = []

    async def _process_task(self, work_item: Dict[str, Any]) -> Dict[str, Any]:
        result = await dispatch(self.specialization, work_item or {{}})
        if result is None:
            return {{"ok": False, "error": f"no dispatcher for {{self.specialization}}"}}
        self.task_history.append({{"input_keys": sorted((work_item or {{}}).keys()), "ok": result.get("ok", False)}})
        return result

    async def _handle_message(self, message: Message):
        content = message.content or {{}}
        if content.get("action") == "task" or content.get("type") == "task":
            self.add_work({{**content, "original_sender": message.sender}})
        elif content.get("action") == "status_request":
            await self.send_to(message.sender, {{"action": "status", "status": self.get_status()}}, reply_to=message.message_id)
        else:
            logger.info("message stored from %s", message.sender)
            self.memory.setdefault("messages", []).append(message.to_dict())

    def get_capabilities(self) -> list[str]:
        return [self.specialization, "real_dispatch", "message_handling"]


def register_agent(hub: CommunicationHub, name: Optional[str] = None) -> {class_name}:
    agent = {class_name}(name=name or "{name}", communication_hub=hub)
    hub.register_agent(agent, agent.name)
    return agent
''', encoding="utf-8")
    print(f"generated real proxy: {agent_file}")
    return agent_file


def fetch_agent(spec: dict[str, Any]) -> None:
    if spec.get("source") and clone_agent(spec):
        return
    generate_dispatch_proxy(spec)


def self_update() -> None:
    result = run_cmd(["git", "pull", "origin", "main"])
    print("updated" if result.returncode == 0 else result.stderr.strip() or "update failed")


def iter_selected(agents: Iterable[dict[str, Any]], name: str | None) -> Iterable[dict[str, Any]]:
    for spec in agents:
        if name is None or spec.get("name") == name:
            yield spec


def list_agents() -> None:
    agents = load_catalogue()
    print(f"Known agents ({len(agents)}):")
    for a in agents:
        src = a.get("source", "[AGOS real dispatcher]")
        spec = a.get("specialization", "agents/real/generic")
        print(f" - {a['name']:24} | source: {src:56} | specialization: {spec}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Real Agent Manager for AGOS Civilization")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List known agents")
    fetch_p = subparsers.add_parser("fetch", help="Clone sourced agents; generate real dispatch proxies for the rest")
    fetch_p.add_argument("--name", default=None)
    gen_p = subparsers.add_parser("generate", help="Generate real dispatch proxy agents")
    gen_p.add_argument("--name", default=None)
    subparsers.add_parser("update-self", help="Pull the latest repository version")
    args = parser.parse_args()

    if args.command == "list":
        list_agents()
        return
    agents = load_catalogue()
    if args.command == "fetch":
        for spec in iter_selected(agents, args.name):
            fetch_agent(spec)
    elif args.command == "generate":
        for spec in iter_selected(agents, args.name):
            generate_dispatch_proxy(spec)
    elif args.command == "update-self":
        self_update()


if __name__ == "__main__":
    if not os.path.exists(CATALOGUE_FILE):
        print(f"ERROR: run from repository root; missing {CATALOGUE_FILE}")
        sys.exit(1)
    main()
