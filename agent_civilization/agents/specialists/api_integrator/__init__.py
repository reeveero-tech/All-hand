# api_integrator agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/api_integrator_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("api_integrator")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "ApiIntegratorAgent0001",
    "ApiIntegratorAgent0021",
    "ApiIntegratorAgent0041",
    "ApiIntegratorAgent0061",
    "ApiIntegratorAgent0081",
    "ApiIntegratorAgent0101",
    "ApiIntegratorAgent0121",
    "ApiIntegratorAgent0141",
    "ApiIntegratorAgent0161",
    "ApiIntegratorAgent0181",
    "ApiIntegratorAgent0201",
    "ApiIntegratorAgent0221",
    "ApiIntegratorAgent0241",
    "ApiIntegratorAgent0261",
    "ApiIntegratorAgent0281",
    "ApiIntegratorAgent0301",
    "ApiIntegratorAgent0321",
    "ApiIntegratorAgent0341",
    "ApiIntegratorAgent0361",
    "ApiIntegratorAgent0381",
    "ApiIntegratorAgent0401",
    "ApiIntegratorAgent0421",
    "ApiIntegratorAgent0441",
    "ApiIntegratorAgent0461",
    "ApiIntegratorAgent0481",
    "ApiIntegratorAgent0501",
    "ApiIntegratorAgent0521",
    "ApiIntegratorAgent0541",
    "ApiIntegratorAgent0561",
    "ApiIntegratorAgent0581",
    "ApiIntegratorAgent0601",
    "ApiIntegratorAgent0621",
    "ApiIntegratorAgent0641",
    "ApiIntegratorAgent0661",
    "ApiIntegratorAgent0681",
    "ApiIntegratorAgent0701",
    "ApiIntegratorAgent0721",
    "ApiIntegratorAgent0741",
    "ApiIntegratorAgent0761",
    "ApiIntegratorAgent0781",
    "ApiIntegratorAgent0801",
    "ApiIntegratorAgent0821",
    "ApiIntegratorAgent0841",
    "ApiIntegratorAgent0861",
    "ApiIntegratorAgent0881",
    "ApiIntegratorAgent0901",
    "ApiIntegratorAgent0921",
    "ApiIntegratorAgent0941",
    "ApiIntegratorAgent0961",
    "ApiIntegratorAgent0981",
]
