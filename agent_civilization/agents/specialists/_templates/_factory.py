"""R-105 (D-049): the class factory for one specialization.

Given a template body and the 50 agent_ids of a specialization, generate the
50 DISTINCT classes (type() creates a new class object per id — not 50 names
for the same class) with the exact historical names, `__init__`/`create()`
defaults, and module attributes.

The template body uses these placeholders (str.format-style):
  {cls_prefix}{aid:04d}  — the class name
  {aid}                 — the default agent_id / create() aid
  {{aid}}               — the docstring Agent ID
  {logger_name}         — the per-file logger name (e.g. 'monitor_0428')
  {name_prefix}         — the create() name prefix (e.g. 'monitor_')
"""

from pathlib import Path
from typing import Dict, List, Type

_TPL_DIR = Path(__file__).parent


def _spec_ids(spec: str) -> List[int]:
    """The 50 historical agent_ids of a specialization (read from the baseline
    reference — an arithmetic sequence with a spec-specific start, step 20)."""
    import json
    ref_path = _TPL_DIR.parent.parent.parent.parent / "docs/audit/M6/logs/raw/r105_baseline_reference.json"
    try:
        ref = json.loads(ref_path.read_text())
        ids = sorted(c["agent_id_default"]
                     for c in ref["specs"][spec]["classes"].values())
        if len(ids) == 50:
            return ids
    except Exception:
        pass
    # fallback: derive from the spec's first file
    raise RuntimeError(f"cannot determine the 50 agent_ids for {spec!r}")


def _class_prefix(spec: str) -> str:
    """The class-name prefix of a specialization (e.g. monitor -> 'MonitorAgent')."""
    pkg = __import__(f"agent_civilization.agents.specialists.{spec}", fromlist=["x"])
    first = getattr(pkg, pkg.__all__[0])
    return first.__name__  # e.g. 'MonitorAgent0008' -> strip the number below


def generate_spec_classes(spec: str) -> Dict[str, Type]:
    """Generate the 50 classes of one specialization.

    Returns {class_name: class} in the historical sorted order — the caller
    (the per-spec package __init__) assigns them as module attributes and
    builds __all__.
    """
    template_path = _TPL_DIR / f"{spec}_template.py.tpl"
    template = template_path.read_text()

    # The prefix (e.g. 'MonitorAgent') and the ids come from the baseline.
    import json
    ref_path = _TPL_DIR.parent.parent.parent.parent / "docs/audit/M6/logs/raw/r105_baseline_reference.json"
    ref = json.loads(ref_path.read_text())
    spec_ref = ref["specs"][spec]
    first_name = spec_ref["__all__"][0]
    prefix = first_name.rstrip("0123456789")  # 'MonitorAgent0008' -> 'MonitorAgent'

    classes: Dict[str, Type] = {}
    for name in spec_ref["__all__"]:
        cls_ref = spec_ref["classes"][name]
        aid = cls_ref["agent_id_default"]
        cls_name = f"{prefix}{aid:04d}"
        assert cls_name == name, f"name mismatch: {cls_name} != {name}"

        # Render the template body for this id via plain token replacement
        # (str.format is unusable here: the bodies contain literal dict braces).
        body = (template
                .replace("@CLASS@", f"{prefix}{aid:04d}")
                .replace("@AID@", str(aid))
                .replace("@LOGGER@", f"{spec}_{aid:04d}")
                .replace("@NAMEPREFIX@", f"{spec}_"))

        ns: Dict[str, object] = {
            "__name__": f"agent_civilization.agents.specialists.{spec}.{spec}_{aid:04d}",
            "__qualname__": cls_name,
        }
        exec(compile(body, f"<r105:{spec}_{aid:04d}>", "exec"), ns)
        cls = ns[cls_name]
        # keep the per-class logger (module-level `logger` in the body)
        classes[name] = cls

    return classes


def generated_create(spec: str, cls: Type, aid_default: int):
    """The per-class create() function (kept as a plain function so the shim
    modules can re-export it with the historical signature)."""
    def create(hub, aid: int = aid_default):
        return cls(name=f"{spec}_{aid:04d}", hub=hub, agent_id=aid)
    create.__name__ = "create"
    create.__qualname__ = f"{cls.__name__}.create"
    return create
