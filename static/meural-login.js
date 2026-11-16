// Adapted from GuySie/ha-meural mobile_auth.py (MIT).
// Copyright (c) 2020 Guy Sie. Full license in NOTICES.md.
"use strict";
(() => {
  const config = JSON.parse(document.getElementById("meural-auth-config").textContent);
  const COGNITO_CLIENT_ID = config.cognito_client_id;
  const COGNITO_URL = config.cognito_url;
  const RESPONSE_KEY_MAP = config.response_keys;
  const PASSWORD_CHALLENGE_EXCLUSION_KEYWORDS = config.password_exclusions;
  const USER_MIGRATION_KEYWORDS = config.migration_keywords;
  const WAF_ERROR_KEYWORDS = config.waf_keywords;
  const WAF_BLOCK_PAGE_KEYWORDS = config.waf_page_keywords;
  const loginForm = document.getElementById("login-form");
  const codeForm = document.getElementById("code-form");
  const loginButton = document.getElementById("login-button");
  const codeButton = document.getElementById("code-button");
  const emailInput = document.getElementById("email");
  const passwordInput = document.getElementById("password");
  const codeInput = document.getElementById("code");
  const statusNode = document.getElementById("status");
  let currentEmail = "";
  let currentPassword = "";
  let trustId = "";
  let pending = null;

  function setStatus(message, isError = false) {
    statusNode.textContent = message;
    statusNode.className = isError ? "error" : "";
  }

  function setBusy(busy) {
    loginButton.disabled = busy;
    codeButton.disabled = busy;
  }

  function clearSecrets() {
    currentPassword = "";
    passwordInput.value = "";
    codeInput.value = "";
    pending = null;
  }

  async function cognitoCall(target, payload) {
    const response = await fetch(COGNITO_URL, {
      method: "POST",
      mode: "cors",
      credentials: "omit",
      cache: "no-store",
      signal: AbortSignal.timeout(20000),
      headers: {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "AWSCognitoIdentityProviderService." + target,
      },
      body: JSON.stringify(payload),
    });
    let body = {};
    try {
      body = await response.json();
    } catch {
      /* No response body. */
    }
    if (!response.ok) {
      const failure = new Error(
        String(body.message || body.Message || "NETGEAR rejected the sign-in.")
      );
      failure.details = JSON.stringify(body).toLowerCase();
      failure.status = response.status;
      throw failure;
    }
    return body;
  }

  function metadata() {
    return { trustID: trustId, sourceEvent: "login", language: "en-US", appType: "meural" };
  }

  async function initiate() {
    try {
      return await cognitoCall("InitiateAuth", {
        AuthFlow: "CUSTOM_AUTH",
        ClientId: COGNITO_CLIENT_ID,
        AuthParameters: { USERNAME: currentEmail },
        ClientMetadata: metadata(),
      });
    } catch (error) {
      const details = String(error.details || "");
      if (!USER_MIGRATION_KEYWORDS.some((keyword) => details.includes(keyword))) throw error;
      return cognitoCall("InitiateAuth", {
        AuthFlow: "USER_PASSWORD_AUTH",
        ClientId: COGNITO_CLIENT_ID,
        AuthParameters: { USERNAME: currentEmail, PASSWORD: currentPassword },
        ClientMetadata: metadata(),
      });
    }
  }

  function responseKey(name) {
    return RESPONSE_KEY_MAP[name] || "ANSWER";
  }

  function passwordAnswersChallenge(name, parameters, attempt) {
    if (name !== "CUSTOM_CHALLENGE" || attempt !== 1) return false;
    const description = JSON.stringify(parameters || {}).toLowerCase();
    return !PASSWORD_CHALLENGE_EXCLUSION_KEYWORDS.some((keyword) => description.includes(keyword));
  }

  async function respond(challenge, answer) {
    return cognitoCall("RespondToAuthChallenge", {
      ChallengeName: challenge.name,
      ClientId: COGNITO_CLIENT_ID,
      Session: challenge.session,
      ChallengeResponses: {
        USERNAME: currentEmail,
        [responseKey(challenge.name)]: answer,
      },
      ClientMetadata: metadata(),
    });
  }

  async function processResponse(response, attempt = 0) {
    while (!response.AuthenticationResult) {
      attempt += 1;
      const name = response.ChallengeName;
      const session = response.Session;
      const parameters = response.ChallengeParameters || {};
      if (!name || !session) throw new Error("NETGEAR returned an unsupported sign-in response.");
      const challenge = { name, session, parameters, attempt };
      if (passwordAnswersChallenge(name, parameters, attempt)) {
        response = await respond(challenge, currentPassword);
        continue;
      }
      pending = challenge;
      currentPassword = "";
      passwordInput.value = "";
      loginForm.style.display = "none";
      codeForm.style.display = "block";
      setBusy(false);
      setStatus("Enter the verification code sent by NETGEAR.");
      codeInput.focus();
      return;
    }
    const accessToken = response.AuthenticationResult.AccessToken;
    if (typeof accessToken !== "string" || accessToken.length < 100) {
      throw new Error("NETGEAR did not return a usable sign-in token.");
    }
    setStatus("Saving your Meural session...");
    const handoff = await fetch(window.location.pathname + window.location.search, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      signal: AbortSignal.timeout(60000),
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        state: config.state,
        email: currentEmail,
        cognito_access_token: accessToken,
        trust_id: trustId,
      }),
    });
    const result = await handoff.json();
    if (!handoff.ok)
      throw new Error(String(result.error || "Tesserae rejected the sign-in result."));
    document.getElementById("return-link").href = result.return_url;
    clearSecrets();
    document.getElementById("login").style.display = "none";
    document.getElementById("done").style.display = "block";
  }

  function isWafError(error) {
    const details = String(error.details || "");
    if (WAF_ERROR_KEYWORDS.some((keyword) => details.includes(keyword))) return true;
    return (
      error.status === 403 && WAF_BLOCK_PAGE_KEYWORDS.some((keyword) => details.includes(keyword))
    );
  }

  function showFailure(error) {
    setBusy(false);
    document.getElementById("restart-link").hidden = false;
    if (isWafError(error)) {
      setStatus(
        "Netgear blocked this connection. Try this page on a phone using mobile data.",
        true
      );
    } else {
      setStatus(String(error.message || "Sign-in failed."), true);
    }
  }

  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    currentEmail = emailInput.value.trim();
    currentPassword = passwordInput.value;
    trustId = config.trust_id;
    pending = null;
    setBusy(true);
    setStatus("Contacting Netgear...");
    try {
      await processResponse(await initiate());
    } catch (error) {
      showFailure(error);
    }
  });

  codeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!pending) return;
    const challenge = pending;
    pending = null;
    setBusy(true);
    setStatus("Checking the verification code...");
    try {
      await processResponse(await respond(challenge, codeInput.value.trim()), challenge.attempt);
    } catch (error) {
      pending = challenge;
      showFailure(error);
    }
  });
  emailInput.value = config.email || "";
  window.addEventListener("pagehide", clearSecrets);
})();
