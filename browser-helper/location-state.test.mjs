import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';
const context = {};
runInNewContext(await readFile(new URL('./location-state.js', import.meta.url),'utf8'),context);
test('a loading or temporarily empty address header does not reveal the page', () => {
  const state = context.jiwuLocationState();
  assert.equal(state('',false,0),'pending');
  assert.equal(state('',false,10000),'pending');
  assert.equal(state('',true,11000),'pending');
  assert.equal(state('',true,16000),'pending');
  assert.equal(state('New York 10001',true,18000),'valid');
});
test('stable wrong ZIP triggers manual setup, then verified ZIP resumes background work', () => {
  const state = context.jiwuLocationState();
  assert.equal(state('98101',true,0),'pending');
  assert.equal(state('98101',true,8000),'invalid');
  assert.equal(state('New York 10001',true,10000),'valid');
  assert.equal(state('New York 10001',true,15000),'valid');
});
test('missing address eventually requests human assistance instead of waiting forever', () => {
  const state = context.jiwuLocationState();
  assert.equal(state('',true,0),'pending');
  assert.equal(state('',true,19000),'pending');
  assert.equal(state('',true,20000),'invalid');
});
