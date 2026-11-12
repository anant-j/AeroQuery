import test from "node:test";
import assert from "node:assert/strict";
import { officialSourceUrl } from "../app/lib/source-url.js";

test("only official CAR source links are allowed", () => {
  const url = "https://laws-lois.justice.gc.ca/eng/regulations/SOR-96-433/section-602.06.html";
  assert.equal(officialSourceUrl(url), url);
  assert.equal(officialSourceUrl(undefined), null);
  const warn = console.warn;
  console.warn = () => {};
  try {
    for (const invalid of ["javascript:alert(1)", "https://laws-lois.justice.gc.ca.evil.test/eng/regulations/SOR-96-433/a", "https://laws-lois.justice.gc.ca/other", "not a url"]) {
      assert.equal(officialSourceUrl(invalid), null);
    }
  } finally {
    console.warn = warn;
  }
});
