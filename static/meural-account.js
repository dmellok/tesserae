// Meural account block (templates/_meural_account.html), on the device page
// and in the Add device card. The server renders the current state; this
// script moves between states without a reload and never fails silently.
// Requests are same-origin with the session cookie; the server's provenance
// check (app/csrf.py) needs no token. No password, code or token passes
// through this page: sign-in happens on the separate Netgear sign-in page.
"use strict";
(() => {
  // ---- Backend contract: the only place that knows field names. --------
  //
  // JSON replies are read by ``normalize``. Accepted today:
  //   {state: "signed_out|signing_in|signed_in|session_expired",
  //    account|email, frames: [{id, name}], chosen: {id, name},
  //    selected (id, older), saved, paused, held}
  //   {status: "ok", return_url}       (current routes, no state yet)
  //   {error: "..."}                    (any failure)
  // A plain redirect (older routes without JSON) counts as success.
  const CHANNEL = "tesserae-meural";

  function normalize(data) {
    if (!data || typeof data !== "object") return {};
    if (data.account && typeof data.account === "object") data = data.account;
    data = { ...data, chosen: data.chosen_frame || data.chosen };
    const frames = Array.isArray(data.frames || data.devices)
      ? (data.frames || data.devices)
          .filter((f) => f && f.id != null)
          .map((f) => ({ id: String(f.id), name: String(f.name || f.alias || f.id) }))
      : null;
    let chosen = null;
    if (data.chosen && data.chosen.id != null) {
      chosen = { id: String(data.chosen.id), name: String(data.chosen.name || "") };
    } else if (data.selected) {
      chosen = { id: String(data.selected), name: "" };
    }
    if (chosen && !chosen.name && frames) {
      const match = frames.find((f) => f.id === chosen.id);
      if (match) chosen.name = match.name;
    }
    let state = data.state || null;
    if (!state && "signed_in" in data) {
      state = data.expired ? "session_expired" : data.signed_in ? "signed_in" : "signed_out";
    }
    return {
      state,
      label: data.account_label || data.account || data.email || null,
      frames,
      chosen,
      saved: data.saved === true,
      paused: data.paused === true,
      held: data.held === true,
      error: typeof data.error === "string" ? data.error : null,
    };
  }

  // Map a backend state to the block's view (data-mx-state).
  function viewFor(info) {
    switch (info.state) {
      case "signed_in":
        if (info.paused) return info.held ? "held" : "released";
        return info.chosen && info.chosen.name ? "locked" : "pick";
      case "session_expired":
        return "expired";
      case "signing_in":
        return "signing_in";
      case "signed_out":
        return "signed_out";
      default:
        return null;
    }
  }

  async function send(url, form, extra) {
    const body = new FormData(form || undefined);
    if (extra) for (const [k, v] of Object.entries(extra)) body.set(k, v);
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
        body,
      });
    } catch {
      return { ok: false, error: "Can't reach Tesserae. Check the connection and try again." };
    }
    let data = null;
    if ((response.headers.get("content-type") || "").includes("application/json")) {
      try {
        data = await response.json();
      } catch {
        data = null;
      }
    }
    const info = normalize(data);
    if (!response.ok || info.error) {
      return {
        ok: false,
        error:
          info.error ||
          `Tesserae couldn't save this (error ${response.status}). Reload the page and try again.`,
      };
    }
    return { ok: true, info };
  }

  async function loadState(url) {
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return null;
      return normalize(await response.json());
    } catch {
      return null;
    }
  }

  // ---- The block -------------------------------------------------------
  function init(root) {
    const $ = (sel) => root.querySelector(sel);
    const $$ = (sel) => Array.from(root.querySelectorAll(sel));
    const mode = root.dataset.mxMode;
    const live = $("[data-mx-live]");
    const pick = $("[data-mx-pick]");
    const select = $("[data-mx-select]");
    const useBtn = $("[data-mx-use]");
    const pickError = $("[data-mx-pick-error]");
    const globalError = $("[data-mx-error]");
    const cancelChange = $("[data-mx-cancel-change]");
    let beforeSignIn = null;
    let savedTimer = 0;

    const announce = (text) => {
      live.textContent = "";
      // A fresh text node after a tick is announced even when it repeats.
      window.setTimeout(() => (live.textContent = text), 30);
    };

    function setError(node, message) {
      if (!node) return;
      node.textContent = message || "";
      node.hidden = !message;
    }

    function setState(view) {
      root.dataset.mxState = view;
      $$("[data-mx-show]").forEach((el) => {
        el.hidden = !el.dataset.mxShow.split(" ").includes(view);
      });
      if (view !== "pick") {
        cancelChange && (cancelChange.hidden = true);
        setError(pickError, "");
        select && select.removeAttribute("aria-invalid");
      }
    }

    function setFrames(frames, chosenId) {
      if (!select || !frames) return;
      const keep = select.options[0];
      select.replaceChildren(keep);
      for (const f of frames) {
        const option = new Option(f.name, f.id, false, f.id === chosenId);
        select.add(option);
      }
      if (frames.length) pick.removeAttribute("data-mx-empty");
    }

    function setChosen(chosen) {
      $$("[data-mx-frame-name]").forEach((el) => {
        el.textContent = chosen.name;
        if (el.hasAttribute("title")) el.title = chosen.name;
      });
      const input = $("[data-mx-chosen-input]");
      if (input) input.value = chosen.id;
    }

    function apply(info) {
      if (info.label) $$("[data-mx-label]").forEach((el) => (el.textContent = info.label));
      if (info.frames) setFrames(info.frames, info.chosen && info.chosen.id);
      if (info.chosen && info.chosen.name) setChosen(info.chosen);
      const view = viewFor(info);
      if (view) setState(view);
      return view;
    }

    function setBusy(button, busy, busyLabel) {
      button.disabled = busy;
      if (busy) button.setAttribute("aria-busy", "true");
      else button.removeAttribute("aria-busy");
      const spinner = button.querySelector(".mx-spin");
      if (spinner) spinner.hidden = !busy;
      const label = button.querySelector("[data-mx-use-label]");
      if (label) {
        label.dataset.idle = label.dataset.idle || label.textContent;
        label.textContent = busy ? busyLabel : label.dataset.idle;
      }
    }

    function lockIn(chosen, justSaved) {
      setChosen(chosen);
      setState("locked");
      const badge = $("[data-mx-saved]");
      if (justSaved && badge) {
        window.clearTimeout(savedTimer);
        badge.classList.remove("is-fading");
        badge.hidden = false;
        savedTimer = window.setTimeout(() => {
          badge.classList.add("is-fading");
          savedTimer = window.setTimeout(() => (badge.hidden = true), 450);
        }, 4000);
      }
      const line = $("[data-mx-locked-line]");
      if (justSaved && line) line.focus({ preventScroll: true });
    }

    // Use this frame: busy, then locked in, or the reason it failed.
    if (pick) {
      pick.addEventListener("submit", async (event) => {
        event.preventDefault();
        setError(pickError, "");
        setError(globalError, "");
        const option = select.selectedOptions[0];
        if (!select.value || !option) {
          select.setAttribute("aria-invalid", "true");
          setError(pickError, "Choose a frame first.");
          select.focus();
          return;
        }
        select.removeAttribute("aria-invalid");
        const chosen = { id: select.value, name: option.textContent.trim() };
        const url = root.dataset.frameUrl;
        if (!url) {
          // Add device: the choice goes in with the Add form.
          lockIn(chosen, false);
          $("[data-mx-locked-line]")?.focus({ preventScroll: true });
          announce(`Frame chosen. Tesserae will show pages on ${chosen.name}.`);
          return;
        }
        setBusy(useBtn, true, "Saving…");
        announce("Saving frame…");
        const result = await send(url, pick);
        setBusy(useBtn, false);
        if (!result.ok) {
          select.setAttribute("aria-invalid", "true");
          setError(pickError, result.error);
          announce(`Frame not saved. ${result.error}`);
          return;
        }
        const info = result.info;
        lockIn(info.chosen && info.chosen.name ? info.chosen : chosen, true);
        announce(`Frame saved. Showing on ${chosen.name}.`);
      });
    }

    // Change re-opens the picker on the current frame; Cancel closes it.
    $$("[data-mx-change]").forEach((btn) =>
      btn.addEventListener("click", () => {
        setState("pick");
        if (cancelChange) cancelChange.hidden = false;
        select.focus();
      })
    );
    if (cancelChange) {
      cancelChange.addEventListener("click", () => {
        setState("locked");
        $("[data-mx-change]")?.focus();
      });
    }

    // Sign out, Resume, Let Tesserae manage: post, then show the new state.
    $$("form[data-mx-post]").forEach((form) =>
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const button = event.submitter || form.querySelector("button");
        setError(globalError, "");
        setBusy(button, true);
        const extra = button && button.name ? { [button.name]: button.value } : null;
        const result = await send(form.action, form, extra);
        if (!result.ok) {
          setBusy(button, false);
          setError(globalError, result.error);
          return;
        }
        if (result.info && viewFor(result.info)) {
          setBusy(button, false);
          apply(result.info);
          announce("Saved.");
        } else {
          window.location.reload();
        }
      })
    );

    // Sign in opens Netgear in a new tab; this block waits for it.
    $$("[data-mx-signin]").forEach((link) =>
      link.addEventListener("click", () => {
        if (root.dataset.mxState !== "signing_in") beforeSignIn = root.dataset.mxState;
        setError(globalError, "");
        setState("signing_in");
        announce("Waiting for Netgear sign-in in the new tab.");
      })
    );
    $("[data-mx-cancel-signin]")?.addEventListener("click", () => {
      setState(beforeSignIn || "signed_out");
      announce("Sign-in cancelled.");
    });

    async function signedIn() {
      const stateUrl = root.dataset.stateUrl;
      if (!stateUrl) {
        window.location.reload();
        return;
      }
      const info = await loadState(stateUrl);
      if (!info || !viewFor(info)) {
        setError(
          globalError,
          "Signed in, but Tesserae couldn't load your frames. Reload the page."
        );
        return;
      }
      const view = apply(info);
      announce(view === "pick" ? "Signed in. Choose a frame." : "Signed in.");
      if (view === "pick") select?.focus();
    }

    const loginPath = (() => {
      try {
        return root.dataset.loginUrl ? new URL(root.dataset.loginUrl, location.href).pathname : "";
      } catch {
        return "";
      }
    })();
    if (loginPath && typeof BroadcastChannel === "function") {
      const channel = new BroadcastChannel(CHANNEL);
      channel.addEventListener("message", (event) => {
        const msg = event.data || {};
        if (msg.type === "signed-in" && msg.login === loginPath) signedIn();
      });
    }

    // Add device without a pending sign-in: remember to point at Sign in
    // once the frame's page opens.
    $("[data-mx-add-then-signin]")?.addEventListener("click", () => {
      const form = document.getElementById(root.dataset.addForm || "");
      const id = form && form.elements.id ? form.elements.id.value.trim() : "";
      try {
        if (id) sessionStorage.setItem("tesserae.meural.signin", id);
      } catch {
        /* storage off: the page still shows Sign in */
      }
    });
    if (mode === "device") {
      let want = null;
      try {
        want = sessionStorage.getItem("tesserae.meural.signin");
        sessionStorage.removeItem("tesserae.meural.signin");
      } catch {
        want = null;
      }
      const signIn = root.querySelector("[data-mx-signin]:not([hidden])");
      if (want && signIn && location.pathname.endsWith("/" + want)) {
        root.closest("details")?.setAttribute("open", "");
        root.scrollIntoView({ block: "center" });
        signIn.focus({ preventScroll: true });
        announce("Frame added. Sign in to Netgear to choose it.");
      }
    }

    root.meuralAccount = { apply, setState, lockIn, normalize };
  }

  function start() {
    document.querySelectorAll("[data-meural-account]").forEach((root) => {
      if (!root.meuralAccount) init(root);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
