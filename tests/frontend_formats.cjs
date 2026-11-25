const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function element() {
  return {
    children: [],
    options: [],
    disabled: false,
    hidden: false,
    value: "",
    textContent: "",
    firstChild: { textContent: "" },
    lastChild: { textContent: "" },
    classList: { add() {}, toggle() {} },
    addEventListener() {},
    replaceChildren(...items) {
      this.children = items;
      this.options = items;
    },
    append(...items) {
      this.children.push(...items);
      this.options = this.children;
    },
    removeAttribute() {},
  };
}

async function main() {
  const elements = new Map();
  const get = (selector) => {
    if (!elements.has(selector)) elements.set(selector, element());
    return elements.get(selector);
  };
  const format = get("#format");
  format.options = ["mp4", "webm", "mp3", "m4a", "opus"].map((value) => ({ value, disabled: false }));
  const quality = get("#quality");
  quality.options = [{ value: "" }];
  get("#output-fields").disabled = true;
  const document = {
    querySelector: get,
    createElement: () => element(),
    createTextNode: (value) => ({ textContent: value }),
    documentElement: { dataset: {} },
  };
  const fetch = async (path) => ({
    ok: true,
    json: async () => path === "/api/status"
      ? { capabilities: { metadata_analysis: "available", conversion_worker: "unavailable", conversion_reason: "Media worker is disabled" } }
      : { jobs: [] },
  });
  const context = vm.createContext({ document, fetch, localStorage: { getItem: () => null, setItem() {} }, AbortController, EventSource: class {} });
  vm.runInContext(fs.readFileSync("frontend/js/app.js", "utf8"), context);
  await new Promise((resolve) => setImmediate(resolve));
  context.formats = {
    options: [
      { format: "mp4", resolution: "360p", available: true, requires_audio_merge: true },
      { format: "mp4", resolution: "1080p", available: true },
      { format: "webm", resolution: "360p", available: false, unavailable_reason: "Required FFmpeg encoder unavailable: libvpx-vp9" },
      { format: "mp3", audio_bitrate_kbps: 128, available: true },
      { format: "mp3", audio_bitrate_kbps: 192, available: true },
      { format: "m4a", audio_bitrate_kbps: 128, available: true },
      { format: "opus", audio_bitrate_kbps: 128, available: true },
    ],
    formats: [],
  };
  vm.runInContext("currentMediaId = 'dQw4w9WgXcQ'; renderFormats(formats)", context);
  assert.equal(get("#output-fields").disabled, false);
  assert.equal(format.disabled, false);
  assert.deepEqual(format.options.filter((item) => !item.disabled).map((item) => item.value), ["mp4", "mp3", "m4a", "opus"]);
  assert.equal(format.options.find((item) => item.value === "webm").disabled, true);
  assert.match(get("#format-availability").textContent, /WEBM unavailable:.*libvpx-vp9/);
  assert.deepEqual(quality.options.map((item) => item.value), ["360p", "1080p"]);
  assert.equal(quality.value, "360p");
  assert.equal(quality.disabled, false);
  assert.equal(get("#create-job-button").disabled, true);
  format.value = "mp3";
  format.onchange();
  assert.deepEqual(quality.options.map((item) => item.value), ["128", "192"]);
  assert.equal(quality.value, "128");
  format.value = "m4a";
  format.onchange();
  assert.deepEqual(quality.options.map((item) => item.value), ["128"]);
  format.value = "opus";
  format.onchange();
  assert.deepEqual(quality.options.map((item) => item.value), ["128"]);
  vm.runInContext("workerReady = true; updateJobButton()", context);
  assert.equal(get("#create-job-button").disabled, false);
  vm.runInContext("resetAnalysis()", context);
  assert.equal(get("#output-fields").disabled, true);
  assert.equal(get("#create-job-button").disabled, true);
  assert.equal(quality.value, "");
  context.newFormats = {
    options: [
      { format: "mp4", resolution: "144p", available: true },
      { format: "mp4", resolution: "480p", available: true },
      { format: "mp3", audio_bitrate_kbps: 128, available: true },
    ],
    formats: [],
  };
  vm.runInContext("currentMediaId = 'other123456'; renderFormats(newFormats)", context);
  assert.deepEqual(quality.options.map((item) => item.value), ["144p", "480p"]);
  assert.equal(quality.value, "144p");
  assert.equal(get("#create-job-button").disabled, false);
  format.value = "mp3";
  format.onchange();
  assert.deepEqual(quality.options.map((item) => item.value), ["128"]);
  format.value = "mp4";
  format.onchange();
  assert.deepEqual(quality.options.map((item) => item.value), ["144p", "480p"]);
  context.emptyFormats = { options: [], formats: [] };
  vm.runInContext("renderFormats(emptyFormats)", context);
  assert.equal(quality.options.length, 0);
  assert.equal(get("#create-job-button").disabled, true);
  console.log("Frontend format selection regression passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
