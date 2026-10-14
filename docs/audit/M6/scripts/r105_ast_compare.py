import ast, hashlib, json, re, subprocess
from pathlib import Path
from collections import defaultdict

def norm_dump(src, spec):
    class N(ast.NodeTransformer):
        def visit_ClassDef(self, node):
            node.name = re.sub(r"Agent\d{4}$", "AgentN", node.name); self.generic_visit(node); return node
        def visit_FunctionDef(self, node):
            node.name = re.sub(r"Agent\d{4}$", "AgentN", node.name)
            for d in node.args.defaults:
                if isinstance(d, ast.Constant) and isinstance(d.value, int): d.value = -999
            self.generic_visit(node); return node
        def visit_Name(self, node):
            node.id = re.sub(r"Agent\d{4}$", "AgentN", node.id); return node
        def visit_Constant(self, node):
            if isinstance(node.value, str):
                node.value = re.sub(r"Agent\d{4}", "AgentN", node.value)
                node.value = re.sub(r"Agent ID: \d+", "Agent ID: N", node.value)
                node.value = re.sub(rf"{spec}_\d{{4}}", f"{spec}_N", node.value)
            return node
    return ast.dump(N().visit(ast.parse(src)))

r = subprocess.run(["git", "ls-tree", "-r", "--name-only", "9e72b0e", "agent_civilization/agents/specialists/"], capture_output=True, text=True)
files = [f for f in r.stdout.splitlines() if f.endswith(".py") and "__init__" not in f and "_templates" not in f]
groups = defaultdict(list)
for f in files:
    spec = f.split("/")[3]
    src = subprocess.run(["git", "show", f"9e72b0e:{f}"], capture_output=True, text=True).stdout
    groups[hashlib.md5(norm_dump(src, spec).encode()).hexdigest()].append(f)
print(len(files), "->", len(groups))
