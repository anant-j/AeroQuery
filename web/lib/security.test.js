const test = require('node:test');
const assert = require('node:assert/strict');

const { validateQueryPayload } = require('./security.js');

test('accepts a realistic aviation query', () => {
  const result = validateQueryPayload({ query: 'How long after alcohol can I fly?', use_rag: true });
  assert.equal(result.query, 'How long after alcohol can I fly?');
  assert.equal(result.use_rag, true);
});

test('rejects empty questions', () => {
  assert.throws(() => validateQueryPayload({ query: '   ', use_rag: true }), /query is required/i);
});

test('rejects oversized payloads', () => {
  const longQuery = 'x'.repeat(5000);
  assert.throws(() => validateQueryPayload({ query: longQuery, use_rag: true }), /must be shorter/i);
});
