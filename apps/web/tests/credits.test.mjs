import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/lib/credits.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const credits = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('credits refill at the start of the next UTC day, across a month and a year end', () => {
  const refill = (iso) => credits.nextCreditRefill(new Date(iso)).toISOString();
  assert.equal(refill('2026-11-26T15:30:00Z'), '2026-11-27T00:00:00.000Z');
  assert.equal(refill('2026-11-26T00:00:00Z'), '2026-11-27T00:00:00.000Z');
  assert.equal(refill('2026-11-30T23:59:59Z'), '2026-12-01T00:00:00.000Z');
  assert.equal(refill('2026-12-31T23:59:59Z'), '2027-01-01T00:00:00.000Z');
});

test('a registered account is told when its credits refill, and a demo account is not', () => {
  const now = new Date('2026-11-26T15:30:00Z');
  assert.match(credits.outOfCreditsMessage(false, now), /refill at \d/);
  assert.doesNotMatch(credits.outOfCreditsMessage(true, now), /refill/);
});
