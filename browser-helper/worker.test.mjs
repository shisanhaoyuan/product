import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';

async function setup() {
  const store = {}, sent = [];
  let receive, removed;
  const chrome = {
    runtime: { onMessage: { addListener: fn => receive = fn } },
    permissions: { contains: async () => true },
    storage: { session: {
      get: async key => key === null ? { ...store } : { [key]: store[key] },
      set: async value => Object.assign(store, value), remove: async key => { delete store[key]; },
    } },
    tabs: {
      create: async () => ({ id: 22 }), update: async () => {},
      sendMessage: async (tab, data) => sent.push({ tab, data }),
      onRemoved: { addListener: fn => removed = fn },
    },
    alarms: { create: () => {}, onAlarm: { addListener: () => {} } },
  };
  runInNewContext(await readFile(new URL('./worker.js', import.meta.url), 'utf8'), { chrome, URL, Date });
  const send = (data, tab = 11, url = 'https://catalog.test/') => new Promise(resolve => receive({
    channel: 'catalog-capture-v1', ...data,
  }, { tab: { id: tab }, url, frameId: 0 }, resolve));
  return { store, sent, send, removed };
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
  await b.send({ action: 'start', asin: 'B012345678', mode: 'source', requestId: 'source' });
  const url = 'https://www.amazon.com/dp/B012345678';
  assert.equal((await b.send({ action: 'task' }, 22, url)).mode, 'source');
  assert.equal((await b.send({ action: 'task' }, 33, url)).mode, undefined);
  await b.send({ action: 'page', asin: 'B012345678', html: 'offer' }, 22, url);
  assert.equal(b.sent[0].data.requestId, 'source');
});
