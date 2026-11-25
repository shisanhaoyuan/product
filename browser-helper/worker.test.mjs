import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';

async function setup() {
  const store = {}, sent = [], opened = [], focused = [], closed = [];
  let receive, removed;
  const manifest = JSON.parse(await readFile(new URL('./manifest.json', import.meta.url), 'utf8'));
  const chrome = {
    runtime: { getManifest: () => manifest, onMessage: { addListener: fn => receive = fn } },
    permissions: { contains: async () => true },
    storage: { session: {
      get: async key => key === null ? { ...store } : { [key]: store[key] },
      set: async value => Object.assign(store, value), remove: async key => { delete store[key]; },
    } },
    tabs: {
      create: async options => { opened.push(options); return { id: 22 }; }, update: async (id,options) => { focused.push({id,...options}); },
      remove: async tab => { closed.push(tab); },
      sendMessage: async (tab, data) => sent.push({ tab, data }),
      onRemoved: { addListener: fn => removed = fn },
    },
    alarms: { create: () => {}, onAlarm: { addListener: () => {} } },
  };
  runInNewContext(await readFile(new URL('./worker.js', import.meta.url), 'utf8'), { chrome, URL, Date });
  const send = (data, tab = 11, url = 'https://catalog.test/') => new Promise(resolve => receive({
    channel: 'catalog-capture-v1', ...data,
  }, { tab: { id: tab }, url, frameId: 0 }, resolve));
  return { store, sent, send, removed, opened, focused, closed };
}

test('only the bound Amazon tab and ASIN may complete a pending job', async () => {
  const b = await setup();
  assert.equal((await b.send({ action: 'start', asin: 'B012345678', requestId: 'first' })).started, true);
  const result = { action: 'page', asin: 'B012345678', html: 'gallery' };
  const url = 'https://www.amazon.com/dp/B012345678';
  await b.send(result, 33, url);
  await b.send({ ...result, asin: 'B999999999' }, 22, url);
  await b.send(result, 22, 'https://www.amazon.com/dp/B999999999');
  assert.equal(b.sent.length, 0);
  await b.send(result, 22, url);
  assert.equal(b.sent.length, 1);
  assert.equal(b.sent[0].tab, 11);
  assert.equal(b.sent[0].data.requestId, 'first');
  assert.equal(Object.keys(b.store).length, 0);
  await b.send(result, 22, url);
  assert.equal(b.sent.length, 1);
});

test('closed verification tabs and cancellations never return gallery success', async () => {
  const b = await setup();
  await b.send({ action: 'start', asin: 'B012345678', requestId: 'first' });
  await b.send({ action: 'cancel', requestId: 'unrelated' });
  assert.equal(Object.keys(b.store).length, 1);
  await b.removed(22);
  assert.match(b.sent[0].data.error, /关闭/);
  await b.send({ action: 'start', asin: 'B012345678', requestId: 'second' });
  await b.send({ action: 'cancel', requestId: 'second' });
  assert.equal(Object.keys(b.store).length, 0);
});

test('source mode is announced and remains bound to its requesting tab', async () => {
  const b = await setup();
  const probe = await b.send({ action: 'probe' });
  assert.equal(probe.modes.includes('source'), true);
  assert.equal(probe.version, JSON.parse(await readFile(new URL('./manifest.json', import.meta.url))).version);
  assert.equal(probe.currentProductImages, true);
  await b.send({ action: 'start', asin: 'B012345678', mode: 'source', requestId: 'source' });
  const url = 'https://www.amazon.com/dp/B012345678';
  assert.equal((await b.send({ action: 'task' }, 22, url)).mode, 'source');
  assert.equal((await b.send({ action: 'task' }, 33, url)).mode, undefined);
  await b.send({ action: 'page', asin: 'B012345678', html: 'offer' }, 22, url);
  assert.equal(b.sent[0].data.requestId, 'source');
});


test('source collection stays in background until the bound page needs manual ZIP setup', async () => {
  const b = await setup();
  await b.send({ action: 'start', asin: 'B012345678', mode: 'source', requestId: 'source' });
  assert.equal(b.opened[0].active, false);
  const url = 'https://www.amazon.com/dp/B012345678';
  await b.send({ action: 'attention' }, 33, url);
  assert.equal(b.focused.length, 0);
  await b.send({ action: 'attention' }, 22, url);
  await b.send({ action: 'attention' }, 22, url);
  assert.deepEqual(b.focused, [{ id: 22, active: true }]);
  await b.send({ action: 'page', asin: 'B012345678', html: '10001 offer' }, 22, url);
  assert.equal(b.focused[1].id, 11);
});

test('a verified background source capture does not steal foreground focus', async () => {
  const b = await setup();
  await b.send({ action: 'start', asin: 'B012345678', mode: 'source', requestId: 'source' });
  await b.send({ action: 'page', asin: 'B012345678', html: 'offer' }, 22, 'https://www.amazon.com/dp/B012345678');
  assert.equal(b.focused.length, 0);
  assert.deepEqual(b.closed, [22]);
});


test('manual foreground applies to one request only, without changing later collection', async () => {
  const b = await setup();
  assert.equal((await b.send({ action: 'probe' })).backgroundSource, true);
  await b.send({ action: 'start', asin: 'B012345678', mode: 'source', foreground: true, requestId: 'manual' });
  assert.equal(b.opened[0].active, true);
  await b.send({ action: 'page', asin: 'B012345678', html: '10001' }, 22, 'https://www.amazon.com/dp/B012345678');
  assert.equal(b.closed.length, 0);
  await b.send({ action: 'start', asin: 'B012345679', mode: 'source', requestId: 'next' });
  assert.equal(b.opened[1].active, false);
});

test('import verification stays background; only ZIP or explicit reveal focuses', async () => {
  const b = await setup();
  await b.send({ action: 'start', asin: 'B012345678', mode: 'import', requestId: 'import1' });
  const url = 'https://www.amazon.com/dp/B012345678';
  await b.send({ action: 'attention', reason: 'verification' }, 22, url);
  assert.equal(b.focused.length, 0);
  assert.equal(b.sent[0].data.attention, 'verification');
  await b.send({ action: 'reveal', requestId: 'wrong' });
  assert.equal(b.focused.length, 0);
  await b.send({ action: 'attention', reason: 'location' }, 22, url);
  assert.equal(b.focused.length, 1);
  await b.send({ action: 'attention', reason: 'location' }, 22, url);
  assert.equal(b.focused.length, 1);
  await b.send({ action: 'page', asin: 'B012345678', html: 'product' }, 22, url);
  await b.send({ action: 'start', asin: 'B012345678', mode: 'import', requestId: 'import2' });
  assert.equal(b.opened[1].active, false);
  await b.send({ action: 'reveal', requestId: 'import2' });
  assert.equal(b.focused.at(-1).id, 22);
});
