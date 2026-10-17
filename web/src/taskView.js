// R-160 (task record contract v1): one view of a task record for the UI,
// whatever variant /api/tasks returned. The API answers with three record
// shapes (docs/governance/contracts/README.md):
//   - "memory" task/chat record: {id, specialization, prompt, status, agent,
//     result: {ok, agent, elapsed_s, result, ...}, created_at (epoch s), ...}
//   - "database" row: {task_id, specialization, prompt, status, agent_name,
//     elapsed_s, result, created_at (ISO string), ...}
//   - "/civilization/execute" record (memory only): {id, message, plan:
//     {primary, ...}, status, result, created_at, ...}
// Before R-160 the list read only the database names (agent_name, elapsed_s),
// so with the memory source (no storage configured) the agent and the duration
// were never shown, and execute records showed no specialization/prompt.
// Unknown fields are ignored; missing or wrongly-typed ones become null.

const STATUSES = new Set(['queued', 'running', 'completed', 'failed'])

const str = (v) => (typeof v === 'string' && v !== '' ? v : null)
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

export function taskView(task) {
  const t = task && typeof task === 'object' ? task : {}
  const outcome = t.result && typeof t.result === 'object' ? t.result : null
  const plan = t.plan && typeof t.plan === 'object' ? t.plan : null
  const created = t.created_at
  let createdMs = null
  if (typeof created === 'number' && Number.isFinite(created)) createdMs = created * 1000
  else if (typeof created === 'string' && !Number.isNaN(Date.parse(created))) createdMs = Date.parse(created)
  return {
    id: str(t.task_id) || str(t.id),
    status: STATUSES.has(t.status) ? t.status : 'queued',
    specialization: str(t.specialization) || (plan && str(plan.primary)) || null,
    prompt: str(t.prompt) ?? str(t.message),
    agent: str(t.agent_name) || str(t.agent) || (outcome && str(outcome.agent)) || null,
    elapsedS: num(t.elapsed_s) ?? (outcome && num(outcome.elapsed_s)),
    createdMs,
    result: outcome,
  }
}
