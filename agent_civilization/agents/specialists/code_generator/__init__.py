# code_generator agents — 50 agents, GENERATED (R-105 / D-049)
# The 50 classes are generated at import time from the single template body in
# _templates/code_generator_template.py via the factory — same names, same __all__,
# same agent_id/create() defaults as the historical 50 files (which remain as
# thin compatibility shims re-exporting the generated classes).
from agent_civilization.agents.specialists._templates._factory import generate_spec_classes

_classes = generate_spec_classes("code_generator")
for _name in _classes:
    globals()[_name] = _classes[_name]

__all__ = [
    "CodeGeneratorAgent0003",
    "CodeGeneratorAgent0023",
    "CodeGeneratorAgent0043",
    "CodeGeneratorAgent0063",
    "CodeGeneratorAgent0083",
    "CodeGeneratorAgent0103",
    "CodeGeneratorAgent0123",
    "CodeGeneratorAgent0143",
    "CodeGeneratorAgent0163",
    "CodeGeneratorAgent0183",
    "CodeGeneratorAgent0203",
    "CodeGeneratorAgent0223",
    "CodeGeneratorAgent0243",
    "CodeGeneratorAgent0263",
    "CodeGeneratorAgent0283",
    "CodeGeneratorAgent0303",
    "CodeGeneratorAgent0323",
    "CodeGeneratorAgent0343",
    "CodeGeneratorAgent0363",
    "CodeGeneratorAgent0383",
    "CodeGeneratorAgent0403",
    "CodeGeneratorAgent0423",
    "CodeGeneratorAgent0443",
    "CodeGeneratorAgent0463",
    "CodeGeneratorAgent0483",
    "CodeGeneratorAgent0503",
    "CodeGeneratorAgent0523",
    "CodeGeneratorAgent0543",
    "CodeGeneratorAgent0563",
    "CodeGeneratorAgent0583",
    "CodeGeneratorAgent0603",
    "CodeGeneratorAgent0623",
    "CodeGeneratorAgent0643",
    "CodeGeneratorAgent0663",
    "CodeGeneratorAgent0683",
    "CodeGeneratorAgent0703",
    "CodeGeneratorAgent0723",
    "CodeGeneratorAgent0743",
    "CodeGeneratorAgent0763",
    "CodeGeneratorAgent0783",
    "CodeGeneratorAgent0803",
    "CodeGeneratorAgent0823",
    "CodeGeneratorAgent0843",
    "CodeGeneratorAgent0863",
    "CodeGeneratorAgent0883",
    "CodeGeneratorAgent0903",
    "CodeGeneratorAgent0923",
    "CodeGeneratorAgent0943",
    "CodeGeneratorAgent0963",
    "CodeGeneratorAgent0983",
]
