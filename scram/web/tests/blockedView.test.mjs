import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadSource } from './load-source.mjs';

const { blockedDetail, gauge, footer, lockCause, C } = loadSource('ui/derive.ts');

for (const [reason, tool, detail] of [
  ['SAFETY_BYPASS_FORBIDDEN', 'disable_safety_function', 'Safety functions must remain enabled.'],
  ['NO_AUTHORITY', 'unknown', 'No rule authorizes this action.'],
  ['PARSE_UNCERTAIN', 'unknown', 'The requested action could not be determined reliably.']
]) {
  test(`${reason} shows its actual constraint without a boundary target`, () => {
    const run = { current_output_percent: 72, block_reasons: [reason], decision: { reason_code: reason },
      action: { tool, expected_postcondition: {} } };
    assert.deepEqual(blockedDetail(run), { reason, detail });
    assert.equal(gauge(run, 'red').label, 'Output · unchanged');
    assert.equal(gauge(run, 'red').target, null);
  });
}

for (const [output, detail] of [[95, '95 % > ceiling 80 %'], [55, '55 % < floor 60 %']]) {
  test(`boundary detail uses the actual ${output} percent limit`, () => {
    const run = { block_reasons: ['BOUNDARY_EXCEEDED'], action: { expected_postcondition: { output_percent: output } } };
    assert.equal(blockedDetail(run).detail, detail);
  });
}

test('dispatch block reason takes precedence over the original YELLOW decision', () => {
  assert.equal(blockedDetail({ block_reasons: ['STATE_CHANGED'], decision: { reason_code: 'HUMAN_APPROVAL_REQUIRED' } }).reason, 'STATE_CHANGED');
});

for (const [audit, label] of [['error', 'AUDIT FAILED'], ['pending', 'AUDIT PENDING'], ['ok', 'AUDITED']]) {
  test(`terminal audit status ${audit} is represented honestly`, () => {
    const run = { machine_state: 'LOCKED', block_reasons: ['CONTROL_UNAVAILABLE'], trace_spans: [{ name: 'audit', status: audit }] };
    const item = footer(run, 'locked').at(-1);
    assert.equal(item.label, label);
    if (audit === 'error') assert.equal(item.color, C.red);
  });
}

test('audit-only lock does not claim recovery failed', () => {
  assert.equal(lockCause({ block_reasons: ['CONTROL_UNAVAILABLE'] }), 'Audit unavailable');
  assert.equal(lockCause({ block_reasons: ['ROLLBACK_FAILED', 'CONTROL_UNAVAILABLE'] }), 'Recovery failed');
});
