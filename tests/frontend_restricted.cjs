const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync("frontend/index.html", "utf8");
const css = fs.readFileSync("frontend/css/app.css", "utf8");
const script = fs.readFileSync("frontend/js/app.js", "utf8");
assert.match(html, /<form id="job-form" novalidate>/);
assert.match(html, /<dialog[^>]*id="restricted-service-dialog"[^>]*aria-modal="true"[^>]*aria-labelledby="restricted-dialog-title"[^>]*aria-describedby="restricted-dialog-description"/);
assert.match(html, /<h2 id="restricted-dialog-title">Restricted Service Access<\/h2>/);
assert.match(html, /<p id="restricted-dialog-description">This installation permits conversion only from hosts and networks authorized by its operator\. Metadata analysis does not grant permission to download or convert media\.<\/p>/);
assert.match(html, /Conversion requires operator-authorized network access and permission to process the media\./);
assert.match(html, /Metadata analysis requests source information without downloading a media file\./);
assert.match(html, /I confirm I have permission to process this media/);
assert.match(html, /Analysis and available formats do not grant permission\./);
assert.match(html, /<form method="dialog"><button[^>]*id="restricted-dialog-dismiss"[^>]*>Understood<\/button><\/form>/);
assert.doesNotMatch(html, /\sstyle\s*=|<script(?![^>]*\ssrc=)/i);
assert.match(css, /\.restricted-dialog::backdrop\{/);
assert.match(css, /@media\(max-width:480px\)\{\.restricted-dialog\{/);

function setup(initialJobsStatus = 200) {
  const elements = new Map();
  const requests = [];
  let activeElement = null;
  let dialogOpens = 0;
  function element() {
    return {
      children: [], options: [], dataset: {}, disabled: false, hidden: false, checked: false,
      value: "", textContent: "", firstChild: { textContent: "" }, lastChild: { textContent: "" },
      classList: { add() {}, toggle() {} },
      addEventListener(name, handler) { this[name] = handler; },
      setAttribute() {}, removeAttribute() {},
      replaceChildren(...items) { this.children = items; this.options = items; },
      append(...items) { this.children.push(...items); this.options = this.children; },
      focus() { activeElement = this; },
    };
  }
  function get(selector) {
    if (!elements.has(selector)) elements.set(selector, element());
    return elements.get(selector);
  }
  const dialog = get("#restricted-service-dialog");
  dialog.showModal = () => { dialog.open = true; dialogOpens += 1; };
  dialog.close = () => { dialog.open = false; dialog.onclose(); };
  const document = { querySelector: get, createElement: element, createTextNode: (value) => ({ textContent: value }), documentElement: { dataset: {} } };
  const fetch = (path, options = {}) => {
    if (path === "/api/status") return Promise.resolve({ ok: true, json: async () => ({ capabilities: { metadata_analysis: "available", conversion_worker: "available" } }) });
    if (path === "/api/jobs" && !options.method) return Promise.resolve(reply(initialJobsStatus === 200 ? { jobs: [] } : { error: { code: "network_restricted", message: "Conversion is restricted to authorized networks" } }, initialJobsStatus));
    return new Promise((resolve) => requests.push({ path, options, resolve }));
  };
  const context = vm.createContext({ document, fetch, localStorage: { getItem: () => null, setItem() {} }, AbortController, EventSource: class { addEventListener() {} close() {} }, requestAnimationFrame() {} });
  vm.runInContext(script, context);
  return { context, get, requests, dialog, get dialogOpens() { return dialogOpens; }, get activeElement() { return activeElement; } };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });

async function main() {
  const blockedLoad = setup(403);
  await tick();
  assert.equal(blockedLoad.dialogOpens, 0);
  const ui = setup();
  await tick();
  assert.equal(ui.dialogOpens, 0);
  ui.get("#source-url").value = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
  ui.get("#analyze-form").submit({ preventDefault() {} });
  ui.requests.shift().resolve(reply({ error: { code: "private_video", message: "This video is private" } }, 403));
  await tick();
  assert.equal(ui.dialogOpens, 0);
  assert.equal(ui.get("#api-notice").textContent, "This video is private");

  ui.get("#output-fields").disabled = false;
  ui.get("#format").value = "mp4";
  ui.get("#quality").value = "360p";
  ui.get("#quality").options = [{ value: "360p" }];
  ui.get("#confirm-rights").checked = true;
  vm.runInContext('currentMediaId = "dQw4w9WgXcQ"; availableOutputFormats = new Set(["mp4"]); updateJobButton()', ui.context);
  assert.equal(ui.get("#create-job-button").disabled, false);
  ui.get("#job-form").submit({ preventDefault() {} });
  assert.equal(ui.requests[0].path, "/api/jobs");
  ui.requests.shift().resolve(reply({ error: { code: "network_restricted", message: "Conversion is restricted to authorized networks" } }, 403));
  await tick();
  assert.equal(ui.dialogOpens, 1);
  assert.equal(ui.activeElement, ui.get("#restricted-dialog-dismiss"));
  assert.equal(ui.get("#create-job-button").disabled, false);
  assert.equal(vm.runInContext("currentJobId", ui.context), null);
  ui.dialog.close();
  assert.equal(ui.activeElement, ui.get("#create-job-button"));

  ui.get("#job-form").submit({ preventDefault() {} });
  ui.requests.shift().resolve(reply({ error: { code: "invalid_origin", message: "Invalid request origin" } }, 403));
  await tick();
  assert.equal(ui.dialogOpens, 1);
  assert.equal(ui.get("#api-notice").textContent, "Invalid request origin");

  ui.get("#job-form").submit({ preventDefault() {} });
  ui.requests.shift().resolve(reply({ job_id: "created-job", format: "mp4", state: "queued", progress: 0 }));
  await tick();
  assert.equal(vm.runInContext("currentJobId", ui.context), "created-job");
  assert.equal(ui.dialogOpens, 1);
  ui.get("#job-form").submit({ preventDefault() {} });
  const staleJobRequest = ui.requests.shift();
  ui.get("#analyze-form").submit({ preventDefault() {} });
  staleJobRequest.resolve(reply({ error: { code: "network_restricted" } }, 403));
  await tick();
  assert.equal(ui.dialogOpens, 1);
  console.log("Restricted dialog and authorized job creation regressions passed");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
