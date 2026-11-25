const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const script = fs.readFileSync("frontend/js/app.js", "utf8");
const css = fs.readFileSync("frontend/css/app.css", "utf8");
assert.match(css, /\.panel\{[^}]*scroll-margin-top:24px/);
assert.equal((script.match(/scrollToCard\("/g) || []).length, 2);

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function response(data, ok = true) {
  return { ok, json: async () => data };
}

function setup(restoredJobs = []) {
  const elements = new Map();
  const scrolls = [];
  const frames = [];
  const listeners = new Map();
  const requests = { analyze: [], formats: [], create: [] };
  const streams = [];
  let reducedMotion = false;
  let viewportHeight = 800;

  function element(selector = "") {
    return {
      children: [], options: [], dataset: {}, attributes: {}, disabled: false, hidden: false,
      checked: false, value: "", textContent: "", firstChild: { textContent: "" }, lastChild: { textContent: "" },
      rect: { top: 1000, bottom: 1400, height: 400 },
      classList: { add() {}, toggle() {} },
      addEventListener(name, handler) { this[name] = handler; },
      setAttribute(name, value) { this.attributes[name] = value; },
      removeAttribute(name) { delete this.attributes[name]; },
      replaceChildren(...items) { this.children = items; this.options = items; },
      append(...items) { this.children.push(...items); this.options = this.children; },
      getBoundingClientRect() { return this.rect; },
      scrollIntoView(options) { scrolls.push({ selector, options }); },
    };
  }

  const get = (selector) => {
    if (!elements.has(selector)) elements.set(selector, element(selector));
    return elements.get(selector);
  };
  get("#format").options = ["mp4", "webm", "mp3", "m4a", "opus"].map((value) => ({ value, disabled: false }));
  get("#quality").options = [{ value: "" }];
  get("#output-fields").disabled = true;
  const document = { querySelector: get, createElement: () => element(), createTextNode: (value) => ({ textContent: value }), documentElement: { dataset: {} } };
  const fetch = (path, options = {}) => {
    if (path === "/api/status") return Promise.resolve(response({ capabilities: { metadata_analysis: "available", conversion_worker: "available" } }));
    if (path === "/api/jobs" && !options.method) return Promise.resolve(response({ jobs: restoredJobs }));
    const kind = path === "/api/analyze" ? "analyze" : path.startsWith("/api/formats/") ? "formats" : path === "/api/jobs" ? "create" : null;
    assert.ok(kind, path);
    const pending = deferred();
    requests[kind].push({ path, options, ...pending });
    return pending.promise;
  };
  class EventSource {
    constructor(url) { this.url = url; this.listeners = new Map(); streams.push(this); }
    addEventListener(name, handler) { this.listeners.set(name, handler); }
    close() { this.closed = true; }
    emit(data) { this.listeners.get("job")({ data: JSON.stringify(data) }); }
  }
  const context = vm.createContext({
    document, fetch, AbortController, EventSource,
    localStorage: { getItem: () => null, setItem() {} },
    requestAnimationFrame: (callback) => { frames.push(callback); },
    addEventListener: (name, handler) => { listeners.set(name, handler); },
    getComputedStyle: () => ({ scrollMarginTop: "24px" }),
    matchMedia: () => ({ matches: reducedMotion }),
    innerHeight: viewportHeight,
    innerWidth: 1440,
  });
  vm.runInContext(script, context);
  return {
    context, get, scrolls, frames, listeners, requests, streams,
    setReducedMotion(value) { reducedMotion = value; },
    setViewportHeight(value) { viewportHeight = value; context.innerHeight = value; },
    flushFrames() { while (frames.length) frames.shift()(); },
    submitAnalyze(url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ") {
      get("#source-url").value = url;
      return get("#analyze-form").submit({ preventDefault() {} });
    },
    submitJob() { return get("#job-form").submit({ preventDefault() {} }); },
  };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
const media = (id) => ({ media_id: id, title: "Sample video", channel: "Sample channel", duration: 12, thumbnail: "https://i.ytimg.com/sample.jpg", webpage_url: `https://www.youtube.com/watch?v=${id}` });
const formats = { options: [{ format: "mp4", resolution: "360p", available: true }], formats: [{ format_id: "18", extension: "mp4", has_video: true, has_audio: true }] };

async function completeAnalyze(ui, id = "dQw4w9WgXcQ") {
  const previousScrolls = ui.scrolls.length;
  ui.requests.analyze.at(-1).resolve(response(media(id)));
  await tick();
  assert.equal(ui.get("#media-name").textContent, "Sample video");
  assert.equal(ui.get("#media-thumbnail").src, "https://i.ytimg.com/sample.jpg");
  assert.equal(ui.get("#media-duration").textContent, "12 seconds");
  assert.equal(ui.scrolls.length, previousScrolls);
  ui.requests.formats.at(-1).resolve(response(formats));
  await tick();
  assert.equal(ui.get("#source-formats").hidden, false);
}

async function main() {
  const restored = setup([{ job_id: "restored", format: "mp4", state: "processing", progress: 20 }]);
  await tick();
  restored.flushFrames();
  assert.equal(restored.scrolls.length, 0);
  assert.equal(restored.get("#progress-active").dataset.state, "processing");

  const ui = setup();
  await tick();
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 0);
  ui.submitAnalyze();
  assert.equal(ui.scrolls.length, 0);
  await completeAnalyze(ui);
  assert.equal(ui.frames.length, 1);
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 1);
  assert.equal(ui.scrolls[0].selector, ".media-panel");
  assert.deepEqual({ ...ui.scrolls[0].options }, { behavior: "smooth", block: "start" });
  ui.get("#format").onchange();
  vm.runInContext("renderRecentJobs()", ui.context);
  assert.equal(ui.scrolls.length, 1);

  ui.submitAnalyze("bad-url");
  ui.requests.analyze.at(-1).resolve(response({ error: { message: "Invalid URL" } }, false));
  await tick();
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 1);

  ui.submitAnalyze("first");
  const first = ui.requests.analyze.at(-1);
  ui.submitAnalyze("second");
  first.resolve(response(media("first123456")));
  await tick();
  assert.equal(ui.requests.formats.length, 1);
  await completeAnalyze(ui, "second12345");
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 2);

  ui.submitAnalyze("manual-scroll");
  ui.listeners.get("wheel")();
  await completeAnalyze(ui);
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 2);

  ui.submitAnalyze("pending-scroll");
  await completeAnalyze(ui);
  ui.listeners.get("touchstart")();
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 2);

  ui.get(".media-panel").rect = { top: 100, bottom: 500, height: 400 };
  ui.submitAnalyze("visible");
  await completeAnalyze(ui);
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 2);
  ui.get(".media-panel").rect = { top: 1000, bottom: 1400, height: 400 };
  ui.setReducedMotion(true);
  ui.setViewportHeight(600);
  ui.context.innerWidth = 390;
  ui.submitAnalyze("reduced-motion");
  await completeAnalyze(ui);
  ui.flushFrames();
  assert.equal(ui.scrolls.at(-1).options.behavior, "instant");
  assert.equal(ui.scrolls.length, 3);

  ui.setReducedMotion(false);
  ui.submitJob();
  assert.equal(ui.scrolls.length, 3);
  const completed = { job_id: "job-created", state: "queued", progress: 0, format: "mp4", download_progress: 0, conversion_progress: 0 };
  ui.requests.create.at(-1).resolve(response(completed));
  await tick();
  assert.equal(ui.scrolls.length, 3);
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 4);
  assert.equal(ui.scrolls.at(-1).selector, ".progress-panel");
  ui.streams.at(-1).emit({ ...completed, state: "downloading", progress: 30 });
  ui.streams.at(-1).emit({ ...completed, state: "processing", progress: 80 });
  ui.streams.at(-1).emit({ ...completed, state: "completed", progress: 100, download_url: "/api/download/job-created" });
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 4);

  ui.submitJob();
  ui.requests.create.at(-1).resolve(response({ error: { message: "Job rejected" } }, false));
  await tick();
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 4);

  ui.submitJob();
  ui.listeners.get("keydown")({ key: "PageDown" });
  ui.requests.create.at(-1).resolve(response({ ...completed, job_id: "job-scrolled" }));
  await tick();
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 4);
  ui.streams.at(-1).emit({ ...completed, job_id: "job-scrolled", state: "failed", error: "Conversion failed" });
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 4);

  ui.submitJob();
  ui.requests.create.at(-1).resolve(response({ ...completed, job_id: "job-pending" }));
  await tick();
  ui.listeners.get("pointerdown")();
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 4);
  ui.streams.at(-1).emit({ ...completed, job_id: "job-pending", state: "cancelled" });
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 4);

  ui.submitJob();
  ui.submitAnalyze("new-work");
  ui.requests.create.at(-1).resolve(response({ ...completed, job_id: "job-outdated" }));
  await tick();
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 4);

  vm.runInContext('saveRecentJob("history", "mp3", "failed")', ui.context);
  ui.flushFrames();
  assert.equal(ui.scrolls.length, 4);
  console.log("Frontend step scroll regressions passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
