// Exercise the shipped browser script against Cognito and DOM boundaries.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const config = JSON.parse(fs.readFileSync(0, "utf8"));
const scenario = process.argv[3];
const elements = new Map();
function element(id) {
  if (!elements.has(id))
    elements.set(id, {
      value: "",
      textContent: "",
      style: {},
      hidden: false,
      disabled: false,
      listeners: {},
      focus() {},
      addEventListener(name, callback) {
        this.listeners[name] = callback;
      },
    });
  return elements.get(id);
}
element("meural-auth-config").textContent = JSON.stringify(config);
const provider = [];
const callbacks = [];
const token = "c".repeat(120);
let initiated = 0;
const reply = (data, ok = true) => ({ ok, status: ok ? 200 : 400, json: async () => data });
async function fetch(url, options) {
  const body = JSON.parse(options.body);
  if (url === config.cognito_url) {
    provider.push(body);
    assert.equal(options.credentials, "omit");
    if (options.headers["X-Amz-Target"].endsWith("InitiateAuth")) {
      initiated++;
      if (scenario === "migration") {
        if (initiated === 1) return reply({ __type: "UserNotFoundException" }, false);
        assert.equal(body.AuthFlow, "USER_PASSWORD_AUTH");
        assert.equal(body.AuthParameters.PASSWORD, "private-password");
        return reply({ AuthenticationResult: { AccessToken: token } });
      }
      return reply({
        ChallengeName: scenario === "otp-first" ? "EMAIL_OTP" : "CUSTOM_CHALLENGE",
        Session: "session-1",
        ChallengeParameters: {},
      });
    }
    if (body.ChallengeName === "CUSTOM_CHALLENGE") {
      assert.equal(body.ChallengeResponses.ANSWER, "private-password");
      return reply({ ChallengeName: "EMAIL_MFA", Session: "session-2", ChallengeParameters: {} });
    }
    const code =
      body.ChallengeResponses[scenario === "otp-first" ? "EMAIL_OTP_CODE" : "EMAIL_MFA_CODE"];
    if (code !== "123456") return reply({ message: "Wrong verification code" }, false);
    return reply({ AuthenticationResult: { AccessToken: token } });
  }
  assert.equal(url, "/settings/devices/test_frame/meural/login");
  assert.equal(options.credentials, "same-origin");
  assert.deepEqual(Object.keys(body).sort(), [
    "cognito_access_token",
    "email",
    "state",
    "trust_id",
  ]);
  assert.equal(body.state, config.state);
  assert.equal(body.trust_id, config.trust_id);
  assert.equal(body.cognito_access_token, token);
  callbacks.push(body);
  return reply({ return_url: "/settings/devices/test_frame#connection" });
}
const context = {
  document: { getElementById: element },
  window: {
    location: { pathname: "/settings/devices/test_frame/meural/login", search: "" },
    addEventListener() {},
  },
  AbortSignal,
  fetch,
};
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), context);
(async () => {
  element("email").value = "owner@example.com";
  element("password").value = "private-password";
  await element("login-form").listeners.submit({ preventDefault() {} });
  if (scenario !== "migration") {
    assert.equal(element("code-form").style.display, "block");
    assert.equal(element("password").value, "");
    assert.equal(callbacks.length, 0);
    element("code").value = "000000";
    await element("code-form").listeners.submit({ preventDefault() {} });
    assert.equal(callbacks.length, 0);
    assert.match(element("status").textContent, /Wrong verification code/);
    element("code").value = "123456";
    await element("code-form").listeners.submit({ preventDefault() {} });
  }
  assert.equal(callbacks.length, 1);
  assert.equal(element("done").style.display, "block");
  assert.equal(element("password").value, "");
  assert.equal(element("code").value, "");
  if (scenario === "otp-first") assert.equal(provider.length, 3);
  process.stdout.write(JSON.stringify({ signed_in: true, callbacks: callbacks.length }));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
