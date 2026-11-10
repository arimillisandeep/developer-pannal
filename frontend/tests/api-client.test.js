import test, { beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import http, { api } from '../src/api.js'

const requests = []

beforeEach(() => {
  requests.length = 0
  globalThis.localStorage = { getItem: () => null }
  http.defaults.adapter = async (config) => {
    requests.push(config)
    return { config, data: {}, headers: {}, status: 200, statusText: 'OK' }
  }
})

test('chat attachment helper targets the registered plural upload route', async () => {
  await api.uploadChat('room-123', new Blob(['chat data'], { type: 'text/plain' }))
  assert.equal(requests[0].method, 'post')
  assert.equal(requests[0].url, '/chat/rooms/room-123/attachments')
  assert.ok(requests[0].data instanceof FormData)
})

test('Google OAuth callback sends query parameters with GET', async () => {
  await api.googleCallback({ code: 'test-code', state: 'test-state' })
  assert.equal(requests[0].method, 'get')
  assert.equal(requests[0].url, '/meetings/google/callback')
  assert.deepEqual(requests[0].params, { code: 'test-code', state: 'test-state' })
})
