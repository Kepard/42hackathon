import test from "node:test";
import assert from "node:assert/strict";
import {handleApi} from "../worker/api.mjs";

const ORIGIN = "https://observer.example";
const CONTROL = "dummy-private-control-capability-at-least-32-characters";
const PASSWORD = "retired dummy password";
const HASH = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(PASSWORD)))].map((byte) => byte.toString(16).padStart(2, "0")).join("");

test("the retired password endpoint never issues a capability even with matching legacy configuration", async () => {
  const env = {
    CONTROL_TOKEN: CONTROL,
    OPERATOR_PASSWORD_SHA256: HASH,
    BUCKET: {get() {throw new Error("Retired login must not read storage");}, put() {throw new Error("Retired login must not write storage");}},
  };
  for (const password of [PASSWORD, "wrong", ""]) {
    const response = await handleApi(new Request(`${ORIGIN}/api/operator/login`, {
      method: "POST", headers: {Origin: ORIGIN, "Content-Type": "application/json", Authorization: `Bearer ${CONTROL}`}, body: JSON.stringify({password}),
    }), env);
    assert.equal(response.status, 404);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.deepEqual(await response.json(), {error: "Not found"});
  }
});

test("retired login stays unavailable for every method without reading a body or requiring secrets", async () => {
  for (const method of ["GET", "POST", "PUT", "OPTIONS"]) {
    const response = await handleApi(new Request(`${ORIGIN}/api/operator/login`, {
      method, ...(method === "POST" || method === "PUT" ? {body: "not-json"} : {}),
    }), {});
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), {error: "Not found"});
  }
});
