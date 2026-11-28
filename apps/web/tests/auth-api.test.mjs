import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source.replaceAll('import.meta.env', '({})'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
let sequence = 0;
async function api() {
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}#${sequence++}`);
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

test('failed logout reports failure and preserves the token for another attempt', async (t) => {
  const module = await api();
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    if (url === '/auth/csrf') return json({ csrf_token: 'signed-token' });
    return json({ detail: 'unavailable' }, 503);
  });
  await module.authCsrf();
  await assert.rejects(module.authLogout(), /Sign out failed/);
  await assert.rejects(module.authLogout(), /Sign out failed/);
  assert.equal(calls.filter((call) => call.url === '/auth/csrf').length, 1);
  assert.equal(calls[2].options.headers.get('X-CSRF-Token'), 'signed-token');
});

test('successful logout discards the token and the next session obtains a new one', async (t) => {
  const module = await api();
  let issued = 0;
  const headers = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url === '/auth/csrf') return json({ csrf_token: `token-${++issued}` });
    headers.push(options.headers.get('X-CSRF-Token'));
    return new Response(null, { status: 204 });
  });
  await module.authLogout();
  await module.authLogout();
  assert.deepEqual(headers, ['token-1', 'token-2']);
});

test('CSRF rejection does not replay a mutation into a different account', async (t) => {
  const module = await api();
  const target = new EventTarget();
  const oldWindow = globalThis.window;
  globalThis.window = target;
  t.after(() => { globalThis.window = oldWindow; });
  let rejected = 0;
  let writes = 0;
  let issued = 0;
  target.addEventListener(module.AUTH_REQUIRED_EVENT, () => rejected++);
  t.mock.method(globalThis, 'fetch', async (url) => {
    if (url === '/auth/csrf') {
      issued++;
      return json({ csrf_token: 'old-session-token' });
    }
    writes++;
    return json({ detail: 'Invalid or missing CSRF token' }, 403);
  });
  await module.authCsrf();
  await assert.rejects(module.authLogout(), /Sign out failed/);
  assert.equal(writes, 1);
  assert.equal(issued, 1);
  assert.equal(rejected, 1);
});

test('failed CSRF acquisition prevents the write', async (t) => {
  const module = await api();
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url) => {
    calls.push(url);
    return json({ detail: 'unavailable' }, 503);
  });
  await assert.rejects(module.authLogout(), /503/);
  assert.deepEqual(calls, ['/auth/csrf']);
});

test('email recovery sends public JSON requests without acquiring a session', async (t) => {
  const module = await api();
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    return json({ detail: 'Check your inbox' });
  });
  await module.requestAccountEmail('person@example.com');
  await module.requestAccountEmail('person@example.com', true);
  assert.deepEqual(calls.map(c => c.url), ['/auth/password/forgot', '/auth/verification/request']);
  assert.equal(calls[0].options.headers.get('Content-Type'), 'application/json');
});

test('login submits the second factor and never treats its rejection as a session', async (t) => {
  const module = await api();
  let payload;
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    payload = JSON.parse(options.body);
    return json({ detail: 'Enter a valid authenticator or recovery code' }, 401);
  });
  await assert.rejects(module.authLogin('person@example.com', 'password', '123456'), /authenticator/);
  assert.equal(payload.code, '123456');
});

test('successful password recovery clears local auth and uses the token only in the request body', async (t) => {
  const module = await api();
  const oldWindow = globalThis.window;
  globalThis.window = new EventTarget();
  t.after(() => { globalThis.window = oldWindow; });
  let events = 0;
  globalThis.window.addEventListener(module.AUTH_REQUIRED_EVENT, () => events++);
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    return json({ detail: 'Password saved' });
  });
  await module.confirmAccountEmail('one-time-secret-token', 'a-long-password');
  assert.equal(calls[0].url, '/auth/password/reset');
  assert.equal(JSON.parse(calls[0].options.body).token, 'one-time-secret-token');
  assert.equal(events, 1);
});

test('MFA management requires CSRF and never retries a rejected factor', async (t) => {
  const module = await api();
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    if (url === '/auth/csrf') return json({ csrf_token: 'session-bound' });
    return json({ detail: 'Invalid authenticator code' }, 400);
  });
  await assert.rejects(module.updateMfa('disable', 'password', '123456'), /Invalid authenticator/);
  assert.deepEqual(calls.map(c => c.url), ['/auth/csrf', '/auth/mfa/disable']);
  assert.equal(calls[1].options.headers.get('X-CSRF-Token'), 'session-bound');
});

test('export returns the file under the name the server gave, and a refusal carries its reason', async (t) => {
  const module = await api();
  const calls = [];
  let refuse = true;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    if (url === '/auth/csrf') return json({ csrf_token: 'session-bound' });
    if (refuse) return json({ detail: 'Your session or password is invalid.' }, 400);
    return new Response('zip-bytes', { headers: {
      'Content-Type': 'application/zip',
      'Content-Disposition': 'attachment; filename="kairo-export-2026-11-26.zip"',
    } });
  });
  await assert.rejects(module.exportAccount('wrong', ''), /password is invalid/);
  refuse = false;
  const { blob, filename } = await module.exportAccount('password', '123456');
  assert.equal(filename, 'kairo-export-2026-11-26.zip');
  assert.equal(await blob.text(), 'zip-bytes');
  assert.deepEqual(calls.map(c => c.url), ['/auth/csrf', '/auth/account/export', '/auth/account/export']);
  assert.equal(calls[2].options.headers.get('X-CSRF-Token'), 'session-bound');
  assert.deepEqual(JSON.parse(calls[2].options.body), { password: 'password', code: '123456' });
});

test('deleting the account ends the session locally only when the server confirms it', async (t) => {
  const module = await api();
  const calls = [];
  let issued = 0;
  let refuse = true;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, token: options?.headers?.get?.('X-CSRF-Token') });
    if (url === '/auth/csrf') return json({ csrf_token: `token-${++issued}` });
    if (url === '/auth/account/delete' && refuse) return json({ detail: 'Enter a valid authenticator or recovery code.' }, 400);
    return json({ detail: 'ok' });
  });
  await assert.rejects(module.deleteAccount('password', ''), /authenticator or recovery code/);
  refuse = false;
  await module.deleteAccount('password', '654321');
  // The refused attempt kept its token; after the deletion the next write has to ask for a new one.
  await module.updateMfa('recovery', 'password', '111111');
  assert.deepEqual(calls.map(c => [c.url, c.token]), [
    ['/auth/csrf', undefined], ['/auth/account/delete', 'token-1'], ['/auth/account/delete', 'token-1'],
    ['/auth/csrf', undefined], ['/auth/mfa/recovery', 'token-2'],
  ]);
});
