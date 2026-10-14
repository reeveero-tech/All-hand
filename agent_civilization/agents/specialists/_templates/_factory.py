"""R-105 (D-049): the class factory — ONE real base class per specialization,
50 generated classes via type().

Design (per the blocking review): the implementation lives ONCE per
specialization in a real, inspectable Python class (`<Prefix>BaseAgent` in
`<spec>_base.py`). The 50 historical classes are generated with `type()`
subclasses that override only the class name, the `agent_id` default, and
`__module__` — they share the base implementation (verified by the identity
tests), and they are DISTINCT class objects (verified per spec).
"""

import importlib
from pathlib import Path
from typing import Dict, Type

_TPL_DIR = Path(__file__).parent

from agent_civilization.agents.specialists._templates._spec_layout import SPEC_CLASSES  # noqa: E402


def _base_module(spec: str):
    """The real template module of a specialization."""
    return importlib.import_module(f"agent_civilization.agents.specialists._templates.{spec}_base")


def _base_class(spec: str, prefix: str) -> Type:
    mod = _base_module(spec)
    return getattr(mod, f"{prefix}BaseAgent")


def generate_spec_classes(spec: str) -> Dict[str, Type]:
    """Generate the 50 historical classes of one specialization.

    Returns {class_name: class} in the historical sorted order — the caller
    (the per-spec package __init__) assigns them as module attributes and
    builds __all__. Each class is a DISTINCT type() object sharing the base
    implementation, with the historical name and agent_id default.
    """
    entries = SPEC_CLASSES[spec]
    prefix = entries[0][0].rstrip("0123456789")  # 'MonitorAgent0008' -> 'MonitorAgent'
    base = _base_class(spec, prefix)

    classes: Dict[str, Type] = {}
    for name, aid in entries:
        # type() subclass: same implementation, historical name + default.
        # The __init__ default for agent_id is per-class; build a subclass
        # whose __init__ has the right signature default without redefining
        # the body (a thin wrapper that fills the default).
        def _make_init(default_aid):
            def __init__(self, name, hub, agent_id=default_aid, **kw):
                base.__init__(self, name, hub, agent_id=agent_id, **kw)
            __init__.__defaults__ = (default_aid,)
            return __init__

        cls = type(
            name,
            (base,),
            {
                "__init__": _make_init(aid),
                "__module__": f"agent_civilization.agents.specialists.{spec}.{spec}_{aid:04d}",
                "__qualname__": name,
                "_r105_default_agent_id": aid,
            },
        )
        classes[name] = cls
    return classes


def generate_create(spec: str, cls: Type, aid_default: int):
    """The per-class create() (historical signature; the name is computed at
    CALL time, not generation time — the R-105 round-1 bug hardcoded it)."""

    def create(hub, aid: int = aid_default):
        return cls(name=f"{spec}_{aid:04d}", hub=hub, agent_id=aid)
    create.__name__ = "create"
    create.__qualname__ = f"{cls.__name__}.create"
    return create
