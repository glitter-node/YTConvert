const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync("frontend/index.html", "utf8");
const css = fs.readFileSync("frontend/css/app.css", "utf8");

assert.match(html, /<html lang="en">/);
assert.doesNotMatch(html, /\sstyle\s*=/i);
assert.match(html, /<fieldset disabled id="output-fields">/);
assert.match(html, /id="create-job-button"[^>]*disabled/);
assert.match(html, /id="include-subtitles"[^>]*disabled/);
for (const id of ["preview-empty", "progress-empty", "history-empty"]) {
  assert.match(html, new RegExp(`class="[^"]*empty-state[^"]*" id="${id}"`));
}
assert.equal((html.match(/class="empty-state-copy"/g) || []).length, 3);
assert.equal((html.match(/class="empty-state-icon"/g) || []).length, 3);
assert.ok(html.indexOf('id="preview-empty"') < html.indexOf('id="progress-empty"'));
assert.ok(html.indexOf('id="progress-empty"') < html.indexOf('id="history-empty"'));
assert.match(css, /\.empty-state\{display:flex;align-items:center;gap:14px;min-width:0;margin-top:18px\}/);
assert.doesNotMatch(css, /\.empty-preview\{min-height:/);
assert.match(css, /select:disabled,button:disabled\{opacity:1;-webkit-text-fill-color:/);
assert.match(css, /input\[type="checkbox"\]:disabled\{opacity:1;accent-color:/);
assert.match(css, /\.primary-button:disabled\{box-shadow:/);
assert.match(css, /\.shell\{max-width:1504px;margin:auto;padding:0 32px\}/);
assert.match(css, /@media\(max-width:1024px\)\{\.shell\{padding:0 24px\}/);
assert.match(css, /@media\(max-width:480px\)\{\.shell\{padding:0 16px\}/);

const blocks = [...css.matchAll(/:root(?:\[data-theme="light"\])?\{([^}]*)\}/g)].map((match) => Object.fromEntries([...match[1].matchAll(/(--[\w-]+):([^;}]+)/g)].map((entry) => [entry[1], entry[2]])));
assert.equal(blocks.length, 2);
const [dark, light] = blocks;
for (const name of ["bg", "text", "border", "icon"]) {
  assert.ok(light[`--color-control-disabled-${name}`]);
  assert.ok(dark[`--color-control-disabled-${name}`]);
}
const luminance = (hex) => {
  const channels = [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16) / 255);
  const linear = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
};
const contrast = (a, b) => {
  const values = [luminance(a), luminance(b)].sort((x, y) => x - y);
  return (values[1] + 0.05) / (values[0] + 0.05);
};
const pairs = [
  ["text-primary", "surface", 4.5],
  ["text-secondary", "surface", 4.5],
  ["text-muted", "surface", 4.5],
  ["control-disabled-text", "control-disabled-bg", 4.5],
  ["control-disabled-border", "control-disabled-bg", 3],
  ["on-brand", "brand", 4.5],
  ["success", "success-subtle", 4.5],
  ["danger", "danger-subtle", 4.5],
  ["info", "info-subtle", 4.5],
  ["neutral", "neutral-subtle", 4.5],
  ["focus-ring", "surface", 3],
];
for (const [themeName, theme] of [["dark", dark], ["light", light]]) {
  for (const [foreground, background, minimum] of pairs) {
    const value = contrast(theme[`--color-${foreground}`], theme[`--color-${background}`]);
    assert.ok(value >= minimum, `${themeName} ${foreground}/${background}: ${value.toFixed(2)}:1`);
  }
}
for (const state of ["queued", "downloading", "processing", "completed", "failed", "cancelled"]) {
  assert.ok(css.includes(`.history-state[data-state="${state}"]`) || css.includes(`,.history-state[data-state="${state}"]`));
  assert.ok(css.includes(`#progress-active[data-state="${state}"]`) || css.includes(`,#progress-active[data-state="${state}"]`));
}

function element() {
  return {
    children: [], options: [], dataset: {}, attributes: {}, disabled: false, hidden: false,
    value: "", textContent: "", firstChild: { textContent: "" }, lastChild: { textContent: "" },
    classList: { add() {}, toggle() {} },
    addEventListener(name, handler) { this[name] = handler; },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
    replaceChildren(...items) { this.children = items; this.options = items; },
    append(...items) { this.children.push(...items); this.options = this.children; },
  };
}

async function main() {
  const elements = new Map();
  const get = (selector) => {
    if (!elements.has(selector)) elements.set(selector, element());
    return elements.get(selector);
  };
  const saved = new Map([["ytconvert-theme", "light"]]);
  const document = {
    querySelector: get,
    createElement: element,
    createTextNode: (value) => ({ textContent: value }),
    documentElement: { dataset: {} },
  };
  const fetch = async (path) => ({
    ok: true,
    json: async () => path === "/api/status"
      ? { capabilities: { metadata_analysis: "available", conversion_worker: "unavailable", conversion_reason: "Media worker is disabled" } }
      : { jobs: [] },
  });
  const localStorage = { getItem: (key) => saved.get(key) || null, setItem: (key, value) => saved.set(key, value) };
  const context = vm.createContext({ document, fetch, localStorage, AbortController, EventSource: class {} });
  vm.runInContext(fs.readFileSync("frontend/js/app.js", "utf8"), context);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(document.documentElement.dataset.theme, "light");
  get("#theme-toggle").click();
  assert.equal(document.documentElement.dataset.theme, "dark");
  assert.equal(saved.get("ytconvert-theme"), "dark");

  context.media = { media_id: "dQw4w9WgXcQ", title: "Sample", channel: "Channel", duration: 10, webpage_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ" };
  vm.runInContext("renderMedia(media)", context);
  assert.equal(get("#preview-empty").hidden, true);
  assert.equal(get("#media-details").hidden, false);
  vm.runInContext("resetAnalysis()", context);
  assert.equal(get("#preview-empty").hidden, false);
  assert.equal(get("#media-details").hidden, true);
  assert.equal(get("#output-fields").disabled, true);
  assert.equal(get("#create-job-button").disabled, true);

  context.jobs = ["queued", "downloading", "processing", "completed", "failed", "cancelled"].map((state, index) => ({ id: String(index), format: "mp4", state }));
  vm.runInContext("recentJobs = jobs; renderRecentJobs()", context);
  assert.equal(get("#history-empty").hidden, true);
  assert.equal(get("#history-list").hidden, false);
  assert.deepEqual(get("#history-list").children.map((item) => item.children[1].dataset.state), context.jobs.map((job) => job.state));
  vm.runInContext("recentJobs = []; renderRecentJobs()", context);
  assert.equal(get("#history-empty").hidden, false);
  assert.equal(get("#history-list").hidden, true);

  for (const state of ["queued", "downloading", "processing", "completed", "failed", "cancelled"]) {
    context.job = { job_id: "job-1", state, progress: 99.5, download_progress: 100, conversion_progress: 99, download_url: "/api/download/job-1", error: state === "failed" ? "FFmpeg conversion failed" : null };
    vm.runInContext("renderJob(job)", context);
    assert.equal(get("#progress-empty").hidden, true);
    assert.equal(get("#progress-active").hidden, false);
    assert.equal(get("#progress-active").dataset.state, state);
    assert.equal(get("#job-state").textContent, state);
    assert.equal(get("#job-progress").value, state === "completed" ? 100 : 99.5);
    assert.equal(get("#job-percent").textContent, `${state === "completed" ? "100.0" : "99.5"}% · download 100.0% · conversion 99.0%`);
    assert.equal(get("#cancel-job").disabled, ["completed", "failed", "cancelled"].includes(state));
    assert.equal(get("#download-link").hidden, state !== "completed");
  }
  context.job = { job_id: "job-2", state: "processing", progress: "not-a-number", download_progress: Infinity, conversion_progress: NaN };
  vm.runInContext("renderJob(job)", context);
  assert.equal(get("#job-percent").textContent, "0.0% · download 0.0% · conversion 0.0%");
  assert.equal(get("#api-notice").attributes.role, "alert");
  assert.equal(get("#api-notice").attributes["aria-live"], "assertive");
  vm.runInContext('showNotice("Ready")', context);
  assert.equal(get("#api-notice").attributes.role, "status");
  assert.equal(get("#api-notice").attributes["aria-live"], "polite");
  console.log("Frontend state, empty state, disabled control, theme, and contrast regressions passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
