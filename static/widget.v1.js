/* FlyRank widget bundle v1 - served immutable from /static/widget.v1.js.
   A release ships as widget.v2.js (new URL) and bumps WIDGET_BUNDLE_VERSION. */
(function () {
  "use strict";
  if (window.__flyrankProcess) { window.__flyrankProcess(); return; }

  var ID_RE = /^wgt_[a-z0-9]{10}$/;
  var CSS = [
    ":host { all: initial; }",
    ".frw { position: relative; box-sizing: border-box; max-width: 360px; padding: 16px; border-radius: 10px;",
    "  border: 1px solid #d0d7de; background: #ffffff; color: #1f2328; box-shadow: 0 4px 16px rgba(0,0,0,.08);",
    "  font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; font-size: 14px; line-height: 1.4; }",
    ".frw.dark { background: #161b22; color: #e6edf3; border-color: #30363d; }",
    ".frw.float { position: fixed; z-index: 2147483000; width: 320px; }",
    ".frw.bottom-right { right: 20px; bottom: 20px; }",
    ".frw.bottom-left { left: 20px; bottom: 20px; }",
    ".frw.center { left: 50%; top: 50%; transform: translate(-50%, -50%); }",
    ".frw h3 { margin: 0 24px 4px 0; font-size: 16px; }",
    ".frw p.desc { margin: 0 0 12px; opacity: .8; }",
    ".frw label { display: block; margin: 8px 0 4px; font-weight: 600; }",
    ".frw input, .frw textarea { width: 100%; box-sizing: border-box; padding: 8px; border-radius: 6px;",
    "  border: 1px solid #8c959f; font: inherit; background: transparent; color: inherit; }",
    ".frw textarea { min-height: 72px; resize: vertical; }",
    ".frw button.submit { margin-top: 12px; width: 100%; padding: 9px; border: 0; border-radius: 6px;",
    "  background: #1f6feb; color: #fff; font: inherit; font-weight: 600; cursor: pointer; }",
    ".frw button.submit[disabled] { opacity: .6; cursor: wait; }",
    ".frw button.close { position: absolute; top: 6px; right: 8px; border: 0; background: none; color: inherit;",
    "  font-size: 18px; cursor: pointer; }",
    ".frw .hp { position: absolute; left: -10000px; top: auto; width: 1px; height: 1px; overflow: hidden; }",
    ".frw .err { color: #cf222e; font-size: 12px; margin-top: 2px; }",
    ".frw .status { margin-top: 10px; font-size: 13px; }",
    ".frw .status.ok { color: #1a7f37; }",
    ".frw .status.bad { color: #cf222e; }"
  ].join("\n");

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) {
      for (var key in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, key)) { node.setAttribute(key, attrs[key]); }
      }
    }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function newKey() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") { return window.crypto.randomUUID(); }
    var out = "k";
    for (var i = 0; i < 32; i++) { out += Math.floor(Math.random() * 16).toString(16); }
    return out;
  }

  function mount(item, config) {
    var host = el("div", { "data-flyrank-host": item.id });
    var slot = document.querySelector('[data-flyrank-widget="' + item.id + '"]');
    if (slot) {
      slot.appendChild(host);
    } else if (item.script && item.script.parentNode && item.script.parentNode !== document.head) {
      item.script.parentNode.insertBefore(host, item.script.nextSibling);
    } else {
      document.body.appendChild(host);
    }
    var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
    root.appendChild(el("style", null, CSS));

    var opts = config.display_options || {};
    var position = opts.position || "inline";
    var floating = config.type === "cta_popover" || position !== "inline";
    var classes = "frw" + (opts.theme === "dark" ? " dark" : "");
    if (floating) { classes += " float " + (position === "inline" ? "bottom-right" : position); }

    var box = el("div", { "class": classes, "role": "region", "aria-label": config.title });
    if (floating) {
      var close = el("button", { "class": "close", "type": "button", "aria-label": "Close" }, "x");
      close.addEventListener("click", function () { host.remove(); });
      box.appendChild(close);
    }
    box.appendChild(el("h3", null, config.title));
    if (config.description) { box.appendChild(el("p", { "class": "desc" }, config.description)); }

    var form = el("form", { "novalidate": "novalidate" });
    var inputs = {};
    var errors = {};
    (config.fields || []).forEach(function (f) {
      var inputId = "frw-" + item.id + "-" + f.name;
      form.appendChild(el("label", { "for": inputId }, f.label + (f.required ? " *" : "")));
      var input = el(f.type === "textarea" ? "textarea" : "input", {
        "id": inputId, "name": f.name, "maxlength": String(f.max_length)
      });
      if (f.type !== "textarea") { input.setAttribute("type", f.type === "email" ? "email" : "text"); }
      if (f.type === "email") { input.setAttribute("autocomplete", "email"); }
      if (f.required) { input.setAttribute("required", "required"); }
      form.appendChild(input);
      var err = el("div", { "class": "err", "aria-live": "polite" });
      form.appendChild(err);
      inputs[f.name] = input;
      errors[f.name] = err;
    });

    var honeypot = el("input", {
      "type": "text", "name": config.honeypot_field || "website", "tabindex": "-1", "autocomplete": "off"
    });
    var hpWrap = el("div", { "class": "hp", "aria-hidden": "true" });
    hpWrap.appendChild(el("label", null, "Leave this field empty"));
    hpWrap.appendChild(honeypot);
    form.appendChild(hpWrap);

    var button = el("button", { "class": "submit", "type": "submit" }, config.button_text || "Submit");
    var status = el("div", { "class": "status", "role": "status", "aria-live": "polite" });
    form.appendChild(button);
    form.appendChild(status);
    box.appendChild(form);
    root.appendChild(box);

    var pendingKey = null;
    form.addEventListener("input", function () { pendingKey = null; });

    function setStatus(text, kind) {
      status.textContent = text;
      status.className = "status" + (kind ? " " + kind : "");
    }

    function clearErrors() {
      for (var name in errors) {
        if (Object.prototype.hasOwnProperty.call(errors, name)) { errors[name].textContent = ""; }
      }
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      clearErrors();
      var fields = {};
      var missing = false;
      (config.fields || []).forEach(function (f) {
        var value = inputs[f.name].value.trim();
        if (f.required && !value) { errors[f.name].textContent = "This field is required."; missing = true; }
        if (value) { fields[f.name] = value; }
      });
      if (missing) { setStatus("Please fill in the required fields.", "bad"); return; }

      if (!pendingKey) { pendingKey = newKey(); }  // reused on retry after a network error
      button.disabled = true;
      setStatus("Sending...", "");
      fetch(config.submit_url, {
        method: "POST",
        mode: "cors",
        credentials: "omit",
        headers: { "Content-Type": "application/json", "Idempotency-Key": pendingKey },
        body: JSON.stringify({ widget_id: config.public_id, fields: fields, website: honeypot.value })
      }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (body) { return { res: res, body: body }; });
      }).then(function (result) {
        button.disabled = false;
        var code = result.res.status;
        if (code === 200 || code === 201) {
          pendingKey = null;
          form.reset();
          setStatus("Thanks! Your submission was received.", "ok");
          return;
        }
        var error = (result.body && result.body.error) || {};
        if (code === 422 && error.details) {
          error.details.forEach(function (d) {
            var name = d.loc && d.loc[d.loc.length - 1];
            if (errors[name]) { errors[name].textContent = d.msg; }
          });
          setStatus("Please correct the highlighted fields.", "bad");
        } else if (code === 429) {
          var wait = result.res.headers.get("Retry-After");
          setStatus("Too many submissions. Please try again " + (wait ? "in " + wait + "s." : "later."), "bad");
        } else {
          setStatus(error.message || "Something went wrong. Please try again.", "bad");
        }
      }).catch(function () {
        button.disabled = false;
        setStatus("Network error. Please try again.", "bad");
      });
    });
  }

  function load(item) {
    if (!ID_RE.test(item.id)) { return; }
    fetch(item.base + "/public/widgets/" + item.id + "/config", { mode: "cors", credentials: "omit" })
      .then(function (res) {
        if (!res.ok) { throw new Error("config request failed with " + res.status); }
        return res.json();
      })
      .then(function (config) { mount(item, config); })
      .catch(function (err) {
        if (window.console) { window.console.warn("[flyrank-widget] " + item.id + ": " + err.message); }
      });
  }

  function processQueue() {
    if (document.readyState === "loading") { return; }
    var queue = window.__flyrankWidgets || [];
    while (queue.length) { load(queue.shift()); }
  }

  window.__flyrankProcess = processQueue;
  document.addEventListener("DOMContentLoaded", processQueue);
  processQueue();
})();