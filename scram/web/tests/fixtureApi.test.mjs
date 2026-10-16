import assert from 'node:assert/strict';
import { beforeEach, after, test } from 'node:test';
import { loadSource } from './load-source.mjs';

const { fixtureApi: api, STEP_MS } = loadSource('api/fixtureApi.ts');
const { actionHash } = loadSource('api/hash.ts');
const originalNow = Date.now;
let now = originalNow();
Date.now = () => now;
after(() => { Date.now = originalNow; });
beforeEach(async () => { await api.reset(); await api.setFault(false); });

async function execute(run) {
  run ??= await api.createRun('C2');
  await api.approve(run, 'operator');
  now += STEP_MS * 5;
  return api.getRun(run.run_id);
}

test('clean runs accumulate live output and hash the new target', async () => {
  const first = await execute();
  const second = await api.createRun('C2');
  assert.equal(second.action.expected_postcondition.output_percent, 76);
  assert.notEqual(second.action.action_hash, first.action.action_hash);
  assert.equal(second.action.action_hash, await actionHash({
    tool: 'adjust_output', target: 'reactor_sim_01',
    normalized_parameters: { delta_percent: 2 }, expected_postcondition: { output_percent: 76 }
  }));
  assert.equal((await execute(second)).current_output_percent, 76);
  assert.equal((await api.getRun(first.run_id)).current_output_percent, 76);
  assert.equal(second.voice_line, null);
});

test('fault rollback restores the latest checkpoint', async () => {
  await execute();
  await api.setFault(true);
  const result = await execute();
  assert.equal(result.checkpoint.before_state.output_percent, 74);
  assert.equal(result.execution.resulting_state.output_percent, 94);
  assert.equal(result.current_output_percent, 74);
  assert.equal(result.machine_state, 'RESTORED');
  assert.equal(result.rollback.restored_checksum, result.checkpoint.checksum);
});

test('new fixture requests use verified output during recovery', async () => {
  await api.setFault(true);
  const first = await api.createRun('C2');
  await api.approve(first, 'operator');
  now += STEP_MS * 2;
  assert.equal((await api.getRun(first.run_id)).current_output_percent, 92);
  const second = await api.createRun('C2');
  assert.equal(second.action.expected_postcondition.output_percent, 74);
  assert.equal(second.decision.state, 'YELLOW');
});

test('new requests advance unpolled executions and enforce the operating band', async () => {
  const first = await api.createRun('C2');
  await api.approve(first, 'operator');
  now += STEP_MS * 5;
  assert.equal((await api.createRun('C2')).action.expected_postcondition.output_percent, 76);
  for (let i = 0; i < 3; i++) await execute();
  const beyond = await api.createRun('C2');
  assert.equal(beyond.machine_state, 'BLOCKED');
  assert.deepEqual(beyond.block_reasons, ['BOUNDARY_EXCEEDED']);
});

test('queued approvals reject stale postconditions without a second write', async () => {
  const first = await api.createRun('C2');
  const second = await api.createRun('C2');
  await api.approve(first, 'operator');
  await api.approve(second, 'operator');
  now += STEP_MS * 5;
  const result = await api.getRun(second.run_id);
  assert.equal(result.machine_state, 'BLOCKED');
  assert.deepEqual(result.block_reasons, ['STATE_CHANGED']);
  assert.equal(result.execution, null);
  assert.equal(result.current_output_percent, 74);
});

test('replay retains the historical hash and reset restores the initial output', async () => {
  const first = await execute();
  const replay = await api.createRun('C4');
  assert.equal(replay.approved_hash, first.action.action_hash);
  assert.deepEqual(replay.block_reasons, ['HASH_MISMATCH', 'APPROVAL_ALREADY_USED']);
  await api.reset();
  const next = await api.createRun('C2');
  assert.equal(next.current_output_percent, 72);
  assert.equal(next.action.expected_postcondition.output_percent, 74);
  assert.notEqual(next.run_id, first.run_id);
});
