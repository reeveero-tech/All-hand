# validator agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/validator_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("validator")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "ValidatorAgent0019",
    "ValidatorAgent0039",
    "ValidatorAgent0059",
    "ValidatorAgent0079",
    "ValidatorAgent0099",
    "ValidatorAgent0119",
    "ValidatorAgent0139",
    "ValidatorAgent0159",
    "ValidatorAgent0179",
    "ValidatorAgent0199",
    "ValidatorAgent0219",
    "ValidatorAgent0239",
    "ValidatorAgent0259",
    "ValidatorAgent0279",
    "ValidatorAgent0299",
    "ValidatorAgent0319",
    "ValidatorAgent0339",
    "ValidatorAgent0359",
    "ValidatorAgent0379",
    "ValidatorAgent0399",
    "ValidatorAgent0419",
    "ValidatorAgent0439",
    "ValidatorAgent0459",
    "ValidatorAgent0479",
    "ValidatorAgent0499",
    "ValidatorAgent0519",
    "ValidatorAgent0539",
    "ValidatorAgent0559",
    "ValidatorAgent0579",
    "ValidatorAgent0599",
    "ValidatorAgent0619",
    "ValidatorAgent0639",
    "ValidatorAgent0659",
    "ValidatorAgent0679",
    "ValidatorAgent0699",
    "ValidatorAgent0719",
    "ValidatorAgent0739",
    "ValidatorAgent0759",
    "ValidatorAgent0779",
    "ValidatorAgent0799",
    "ValidatorAgent0819",
    "ValidatorAgent0839",
    "ValidatorAgent0859",
    "ValidatorAgent0879",
    "ValidatorAgent0899",
    "ValidatorAgent0919",
    "ValidatorAgent0939",
    "ValidatorAgent0959",
    "ValidatorAgent0979",
    "ValidatorAgent0999",
]
