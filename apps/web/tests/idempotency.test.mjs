import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source.replaceAll('import.meta.env', '({})'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const api = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

const sse = (...lines) => new Response(lines.map((line) => `data: ${line}\n\n`).join(''), {
  status: 200, headers: { 'Content-Type': 'text/event-stream' },
});

test('a retry after a dropped stream sends the same idempotency key, and the next message a new one', async (t) => {
  const keys = [];
  let call = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url === '/auth/csrf') {
      return new Response(JSON.stringify({ csrf_token: 'token' }), { headers: { 'Content-Type': 'application/json' } });
    }
    keys.push(new Headers(options.headers).get('Idempotency-Key'));
    call += 1;
    // The first connection drops before [DONE]; the retry gets the whole reply.
    return call === 1 ? sse('{"token":"Added "}') : sse('{"token":"Added task."}', '[DONE]');
  });

  const reply = await api.streamChat('buy milk', 'pm-1', () => {});
  assert.equal(reply, 'Added task.');
  assert.equal(keys.length, 2);
  assert.ok(keys[0] && keys[0].length >= 32);
  assert.equal(keys[1], keys[0]);

  await api.streamChat('call mom', 'pm-1', () => {});
  assert.notEqual(keys[2], keys[0]);
});

test('keys do not repeat', () => {
  const keys = new Set(Array.from({ length: 1000 }, () => api.newIdempotencyKey()));
  assert.equal(keys.size, 1000);
});
