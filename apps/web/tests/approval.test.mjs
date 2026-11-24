import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/approval.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { parseApproval } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

const block = (id, summary, risk = 'medium') =>
  `Approval required [${id}]: ${summary}\nRisk: ${risk}.\nReply \`approve ${id}\` to go ahead, or \`reject ${id}\` to cancel.`;

test('a reply that is only an approval becomes a card with nothing around it', () => {
  const parsed = parseApproval(block('504548b3', 'Add schedule event: rehearse pitch — Wed 25 Nov, 8 AM–9 AM', 'low'));
  assert.deepEqual(parsed, {
    id: '504548b3',
    summary: 'Add schedule event: rehearse pitch — Wed 25 Nov, 8 AM–9 AM',
    risk: 'low',
    before: '',
    after: '',
  });
});

test('a change made right away is kept above the card for the part that needs approval', () => {
  const reply = 'Added task: email Sam the meeting notes — no due date.\n' +
    block('9310c291', 'Update schedule event: Team standup — 10 AM–10:20 AM (was 9:30 AM–9:50 AM)');
  const parsed = parseApproval(reply);
  assert.equal(parsed.id, '9310c291');
  assert.equal(parsed.summary, 'Update schedule event: Team standup — 10 AM–10:20 AM (was 9:30 AM–9:50 AM)');
  assert.equal(parsed.risk, 'medium');
  assert.equal(parsed.before, 'Added task: email Sam the meeting notes — no due date.');
  assert.equal(parsed.after, '');
});

test('the block is found when it follows a sentence on the same line', () => {
  const parsed = parseApproval('Added task: email Sam — no due date. ' + block('9310c291', 'Update schedule event: Team standup'));
  assert.equal(parsed.id, '9310c291');
  assert.equal(parsed.before, 'Added task: email Sam — no due date.');
});

test('a batch keeps its heading and one line per change, after earlier done lines', () => {
  const reply = 'Added task: email Sam — no due date.\n' +
    block('ab12cd34', '2 changes:\n- Update schedule event: standup — 10 AM–10:15 AM (was 9 AM–9:15 AM)\n- Remove todo: rotate API keys');
  const parsed = parseApproval(reply);
  assert.equal(parsed.summary,
    '2 changes:\n- Update schedule event: standup — 10 AM–10:15 AM (was 9 AM–9:15 AM)\n- Remove todo: rotate API keys');
  assert.equal(parsed.before, 'Added task: email Sam — no due date.');
});

test('text after the block is kept, and the typed approve/reject line is dropped', () => {
  const parsed = parseApproval(block('ab12cd34', 'Remove todo: rotate API keys') + '\n\nAnything else?');
  assert.equal(parsed.after, 'Anything else?');
});

test('ordinary replies and mentions of approval are not cards', () => {
  assert.equal(parseApproval("Done, I've moved lunch with Maya to 1 PM."), null);
  assert.equal(parseApproval('Approval required for that, but I need a time first.'), null);
  assert.equal(parseApproval('You approved [ab12cd34] earlier.'), null);
});
