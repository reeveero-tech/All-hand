// R-160 (task record contract v1): the web task view on the SAME example
// payloads the Python contract tests validate (docs/contracts/examples/ —
// tests/test_r160_contracts.py checks they match live API shapes). Covers the
// three record variants, unknown fields, malformed/missing fields and a
// newer-API record (version drift): the view must keep working and ignore
// what it does not know.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, dirname } from 'node:path';
import { taskView } from '../src/taskView.js';

const here = dirname(fileURLToPath(import.meta.url));
const ex = (name) => JSON.parse(readFileSync(join(here, '..', '..', 'docs', 'contracts', 'examples', name), 'utf8'));

test('memory source: agent and duration come from the memory record (were blank before R-160)', () => {
  const list = ex('task_list.memory.json');
  const done = list.tasks.find((t) => t.status === 'completed' && t.specialization);
  const v = taskView(done);
  assert.equal(v.id, done.id);
  assert.equal(v.agent, done.agent);                        // memory key is "agent", not "agent_name"
  assert.equal(v.elapsedS, done.result.elapsed_s);          // duration lives in the outcome
  assert.equal(v.status, 'completed');
  assert.equal(v.createdMs, done.created_at * 1000);        // epoch seconds
  assert.deepEqual(v.result, done.result);
});

test('memory source: a queued record has no agent/duration/result yet', () => {
  const q = ex('task_list.memory.json').tasks.find((t) => t.status === 'queued');
  const v = taskView(q);
  assert.equal(v.agent, null);
  assert.equal(v.elapsedS, null);
  assert.equal(v.result, null);
  assert.equal(v.prompt, q.prompt);
});

test('memory source: a /civilization/execute record shows its plan primary and message', () => {
  const e = ex('task_list.memory.json').tasks.find((t) => t.message !== undefined);
  const v = taskView(e);
  assert.equal(v.specialization, e.plan.primary);
  assert.equal(v.prompt, e.message);
  assert.equal(v.id, e.id);
});

test('database source: agent_name/elapsed_s/ISO created_at of a stored row', () => {
  const row = ex('task_list.database.json').tasks[0];
  const v = taskView(row);
  assert.equal(v.id, row.task_id);
  assert.equal(v.agent, row.agent_name);
  assert.equal(v.elapsedS, row.elapsed_s);
  assert.equal(v.createdMs, Date.parse(row.created_at));
});

test('elapsed 0 is shown as 0 (not hidden as falsy)', () => {
  const row = { ...ex('task_list.database.json').tasks[0], elapsed_s: 0 };
  assert.equal(taskView(row).elapsedS, 0);
});

test('unknown fields (a newer API) are ignored', () => {
  const row = { ...ex('task_list.database.json').tasks[0], trace_id: 'x', lease: { owner: 'w' }, contract_version: 2 };
  const v = taskView(row);
  assert.equal(v.agent, row.agent_name);
  assert.equal(Object.keys(v).sort().join(','), 'agent,createdMs,elapsedS,id,prompt,result,specialization,status');
});

test('malformed or missing fields degrade to null / queued, never throw', () => {
  for (const bad of [null, undefined, 42, 'x', [], {}, { status: 'exploded', created_at: 'not a date', elapsed_s: 'fast',
    agent: 5, result: 'oops', plan: 'p', id: 7 }]) {
    const v = taskView(bad);
    assert.equal(v.status, 'queued');
    assert.equal(v.agent, null);
    assert.equal(v.elapsedS, null);
    assert.equal(v.createdMs, null);
    assert.equal(v.result, null);
  }
});

test('an unknown status from a newer API is shown as queued (not as a crash)', () => {
  const t = { ...ex('task_list.memory.json').tasks[0], status: 'cancelled' };
  assert.equal(taskView(t).status, 'queued');
});
