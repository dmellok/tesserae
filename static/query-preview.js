// Delegation also covers option forms injected by the canvas editor.
(() => {
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };
  function httpsUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" && !url.username && !url.password ? url.href : "";
    } catch {
      return "";
    }
  }
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-query-preview]");
    if (!trigger) return;
    event.preventDefault();
    event.stopPropagation();
    const scope = trigger.closest("[data-opt-scope]") || trigger.closest("form");
    if (!scope) return;
    const endpoint = new URL(trigger.dataset.queryPreview, location.href);
    if (endpoint.origin !== location.origin) return;
    const params = new URLSearchParams();
    scope.querySelectorAll("[name^='opt_']").forEach((field) => {
      if (["checkbox", "radio"].includes(field.type) && !field.checked) return;
      if (field.multiple)
        Array.from(field.selectedOptions).forEach((option) =>
          params.append(field.name, option.value)
        );
      else params.append(field.name, field.value);
    });
    params.set("cell_w", trigger.dataset.cellW);
    params.set("cell_h", trigger.dataset.cellH);

    const dialog = make("dialog", "query-preview");
    dialog.setAttribute("aria-label", "Query preview");
    const header = make("header", "query-preview-head");
    const heading = make("div");
    heading.append(make("h2", "", "Query preview"));
    heading.append(
      make(
        "p",
        "query-preview-context",
        `${trigger.dataset.cellW} x ${trigger.dataset.cellH} cell / current filters`
      )
    );
    const tagField = make("div", "field--switch query-preview-tag-toggle");
    const tagLabel = make("label", "switch");
    const showTags = make("input");
    showTags.type = "checkbox";
    showTags.setAttribute("role", "switch");
    showTags.checked = true;
    const track = make("span", "switch-track");
    track.setAttribute("aria-hidden", "true");
    track.append(make("span", "switch-thumb"));
    tagLabel.append(showTags, track, make("span", "switch-label", "Show tags"));
    tagField.append(tagLabel);
    heading.append(tagField);
    showTags.addEventListener("change", () => {
      dialog.classList.toggle("query-preview-hide-tags", !showTags.checked);
    });
    const close = make("button", "btn query-preview-close");
    close.type = "button";
    close.autofocus = true;
    close.setAttribute("aria-label", "Close query preview");
    close.innerHTML =
      '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';
    header.append(heading, close);
    const status = make("p", "query-preview-status", "Finding matching images...");
    status.setAttribute("role", "status");
    const notice = make("p", "query-preview-notice");
    notice.hidden = true;
    const grid = make("div", "query-preview-grid");
    const foot = make("footer", "query-preview-foot");
    foot.append(make("p", "", "From the cached collection. Preview does not advance rotation."));
    const retry = make("button", "btn", "Try again");
    retry.type = "button";
    retry.hidden = true;
    foot.append(retry);
    dialog.append(header, status, notice, grid, foot);
    document.body.append(dialog);
    let controller;
    close.addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) {
        const rect = dialog.getBoundingClientRect();
        if (
          event.clientX < rect.left ||
          event.clientX > rect.right ||
          event.clientY < rect.top ||
          event.clientY > rect.bottom
        )
          dialog.close();
      }
    });
    dialog.addEventListener(
      "close",
      () => {
        controller?.abort();
        dialog.remove();
        if (trigger.isConnected) trigger.focus();
      },
      { once: true }
    );
    async function load() {
      controller?.abort();
      controller = new AbortController();
      const signal = controller.signal;
      const timer = setTimeout(() => controller.abort(), 15000);
      retry.hidden = true;
      notice.hidden = true;
      status.textContent = "Finding matching images...";
      grid.replaceChildren();
      grid.setAttribute("aria-busy", "true");
      for (let i = 0; i < 8; i++) {
        const skeleton = make("div", "query-preview-skeleton");
        skeleton.setAttribute("aria-hidden", "true");
        grid.append(skeleton);
      }
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          body: params,
          signal,
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error("Preview could not be loaded. Try again shortly.");
        const data = await response.json();
        if (signal.aborted || !dialog.open) return;
        const images = (Array.isArray(data.images) ? data.images : [])
          .filter((item) => item && httpsUrl(item.url))
          .slice(0, 20);
        grid.replaceChildren();
        const total = Number.isInteger(data.total)
          ? Math.max(images.length, data.total)
          : images.length;
        status.textContent = images.length
          ? `${images.length} of ${total} eligible images`
          : "No matching images";
        const messages = [
          data.error,
          data.warning,
          data.stale
            ? "Showing cached matches while the service is unavailable or refreshing."
            : "",
        ].filter((value) => typeof value === "string" && value);
        if (!images.length && !messages.length)
          messages.push("Try fewer filters, a different search, or allowing smaller images.");
        if (messages.length) {
          notice.textContent = messages.join(" ");
          notice.hidden = false;
        }
        retry.hidden = !data.error && images.length > 0;
        images.forEach((item) => {
          const figure = make("figure", "query-preview-item");
          const media = make("div", "query-preview-media");
          const img = make("img");
          img.src = httpsUrl(item.thumbnail) || httpsUrl(item.url);
          img.alt =
            item.prompt || (item.username ? `Artwork by ${item.username}` : "Matching artwork");
          img.loading = "lazy";
          img.decoding = "async";
          img.referrerPolicy = "no-referrer";
          img.addEventListener(
            "error",
            () => {
              img.replaceWith(make("p", "query-preview-image-error", "Image unavailable"));
            },
            { once: true }
          );
          const badges = make("div", "query-preview-badges");
          const matches =
            Array.isArray(item.matches) && item.matches.length ? item.matches : ["Matching result"];
          matches
            .slice(0, 10)
            .forEach((match) => badges.append(make("span", "query-preview-badge", match)));
          media.append(img, badges);
          const link = httpsUrl(item.permalink);
          if (link) {
            const anchor = make("a");
            anchor.href = link;
            anchor.target = "_blank";
            anchor.rel = "noopener noreferrer";
            anchor.setAttribute(
              "aria-label",
              item.username ? `Open artwork by ${item.username}` : "Open original artwork"
            );
            anchor.append(media);
            figure.append(anchor);
          } else figure.append(media);
          const dimensions =
            Number(item.width) > 0 && Number(item.height) > 0
              ? `${item.width} x ${item.height}`
              : "";
          const caption = [item.provider === "lexica" ? "Lexica" : item.username, dimensions]
            .filter(Boolean)
            .join(" / ");
          figure.append(make("figcaption", "", caption));
          grid.append(figure);
        });
      } catch (error) {
        if (!dialog.open) return;
        grid.replaceChildren();
        status.textContent = "Preview unavailable";
        notice.textContent = signal.aborted
          ? "The preview took too long. Try again shortly."
          : error.message;
        notice.hidden = false;
        retry.hidden = false;
      } finally {
        clearTimeout(timer);
        grid.setAttribute("aria-busy", "false");
      }
    }
    retry.addEventListener("click", load);
    dialog.showModal();
    load();
  });
})();
