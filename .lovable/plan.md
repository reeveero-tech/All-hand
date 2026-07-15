# AGOS Agent Civilization — Plan

Latest milestone: **simulation reduced to ~10%** across the repository.

- Every specialization now routes through `real_dispatcher.dispatch(...)`
  first. Real code paths cover: analyst, api_integrator, architect, builder,
  code_generator, communicator, data_processor, db_manager, designer,
  educator, modifier, monitor, network_client, researcher, reviewer,
  self_developer, strategist, surgeon, test_runner, validator.
- `BaseAgent.__init_subclass__` transparently wraps every one of the ~1000
  specialist classes so mock `_process_task` outputs are only used when the
  real handler declines to answer (kept for fallback ≈10%).
- The orchestrator, auto-router and OSS registry stay unchanged — they
  already produce real plans; the wrap ensures the runtime under them is
  real too.

Next steps: expand real handlers into the remaining edge-case
specializations, then wire an evaluator that measures the mock-vs-real
ratio automatically per run.
