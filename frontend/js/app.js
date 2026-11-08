const statusLabel = document.querySelector("#service-status");
const notice = document.querySelector("#api-notice");
const analyzeForm = document.querySelector("#analyze-form");
const analyzeButton = document.querySelector("#analyze-button");
const themeToggle = document.querySelector("#theme-toggle");
const outputFields = document.querySelector("#output-fields");
const formatSelect = document.querySelector("#format");
const qualitySelect = document.querySelector("#quality");
const jobForm = document.querySelector("#job-form");
const createJobButton = document.querySelector("#create-job-button");
const restrictedDialog = document.querySelector("#restricted-service-dialog");
let currentMediaId = null;
let currentJobId = null;
let jobEvents = null;
let analysisController = null;
let analysisSequence = 0;
let jobRequestSequence = 0;
let scrollIntentVersion = 0;
let workerReady = false;
let workerReason = "Media worker is disabled";
let availableOutputFormats = new Set();
let recentJobs = [];
restrictedDialog.addEventListener("close", () => createJobButton.focus());
try {
  const savedJobs = JSON.parse(localStorage.getItem("ytconvert-jobs") || "[]");
  if (Array.isArray(savedJobs)) recentJobs = savedJobs;
} catch {
  recentJobs = [];
}

function renderRecentJobs() {
  const list = document.querySelector("#history-list");
  list.replaceChildren();
  document.querySelector("#history-empty").hidden = recentJobs.length > 0;
  list.hidden = recentJobs.length === 0;
  for (const job of recentJobs) {
    const item = document.createElement("li");
    const format = document.createElement("span");
    format.className = "history-format";
    format.textContent = String(job.format || "media").toUpperCase();
    const state = document.createElement("span");
    state.className = "history-state";
    state.dataset.state = String(job.state || "");
    const labels = { queued: "Queued", downloading: "Downloading", processing: "Processing", completed: "Completed", failed: "Failed", cancelled: "Cancelled" };
    state.textContent = labels[job.state] || "Unknown";
    const identifier = document.createElement("span");
    identifier.className = "history-id";
    identifier.textContent = `ID ${String(job.id || "")}`;
    item.append(format, state, identifier);
    list.append(item);
  }
}

function saveRecentJob(id, format, state) {
  const previous = recentJobs.find((job) => job.id === id);
  if (previous) previous.state = state;
  else recentJobs.unshift({ id, format, state });
  recentJobs.splice(5);
  localStorage.setItem("ytconvert-jobs", JSON.stringify(recentJobs));
  renderRecentJobs();
}

function showNotice(message, isError = false) {
  notice.textContent = message;
  notice.classList.toggle("is-error", isError);
  notice.setAttribute("role", isError ? "alert" : "status");
  notice.setAttribute("aria-live", isError ? "assertive" : "polite");
  notice.hidden = false;
}

function showRestrictedDialog() {
  restrictedDialog.showModal();
  document.querySelector("#restricted-dialog-dismiss").focus();
}

function updateJobButton() {
  createJobButton.disabled = !workerReady || !currentMediaId || outputFields.disabled || !availableOutputFormats.has(formatSelect.value) || !Array.from(qualitySelect.options).some((option) => option.value === qualitySelect.value);
}

function markScrollIntent() {
  scrollIntentVersion += 1;
}

function scrollToCard(selector, intentVersion, isCurrent) {
  const card = document.querySelector(selector);
  if (!card || typeof card.scrollIntoView !== "function") return;
  globalThis.requestAnimationFrame(() => {
    if (intentVersion !== scrollIntentVersion || !isCurrent()) return;
    const rect = card.getBoundingClientRect();
    const viewportHeight = globalThis.innerHeight;
    const topOffset = parseFloat(globalThis.getComputedStyle(card).scrollMarginTop) || 0;
    const visibleHeight = Math.max(0, Math.min(rect.bottom, viewportHeight) - Math.max(rect.top, topOffset));
    const targetHeight = Math.min(rect.height, viewportHeight - topOffset);
    if (rect.top >= topOffset && visibleHeight >= targetHeight * 0.75) return;
    const reducedMotion = globalThis.matchMedia("(prefers-reduced-motion: reduce)").matches;
    card.scrollIntoView({ behavior: reducedMotion ? "instant" : "smooth", block: "start" });
  });
}

for (const eventName of ["wheel", "touchstart", "pointerdown"]) {
  globalThis.addEventListener?.(eventName, markScrollIntent, { passive: true });
}
globalThis.addEventListener?.("keydown", (event) => {
  if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) markScrollIntent();
});

async function apiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) }, ...options });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("Network error. Check your connection and try again.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error?.message || `Request failed with status ${response.status}.`);
    error.status = response.status;
    error.code = data.error?.code;
    throw error;
  }
  return data;
}

async function checkStatus() {
  try {
    const data = await apiRequest("/api/status");
    statusLabel.lastChild.textContent = "Service online";
    statusLabel.classList.add("is-ready");
    if (data.capabilities?.metadata_analysis !== "available") {
      showNotice("Media analysis is not available yet.");
    }
    workerReady = data.capabilities?.conversion_worker === "available";
    workerReason = data.capabilities?.conversion_reason || "Media worker is unavailable";
    document.querySelector("#job-availability").textContent = workerReady ? "Output quality cannot exceed the source. Conversion also requires operator-authorized network access and permission to process the media." : `Conversion unavailable: ${workerReason}.`;
    updateJobButton();
  } catch {
    statusLabel.lastChild.textContent = "Service unavailable";
    statusLabel.classList.add("is-error");
    showNotice("The service is unavailable. Please try again later.", true);
  }
}

function renderMedia(media) {
  currentMediaId = media.media_id;
  document.querySelector("#preview-empty").hidden = true;
  document.querySelector("#media-details").hidden = false;
  document.querySelector("#media-name").textContent = media.title || "Untitled video";
  document.querySelector("#media-channel").textContent = media.channel || "Channel unavailable";
  document.querySelector("#media-duration").textContent = media.duration != null ? `${Math.round(media.duration)} seconds` : media.live_status === "is_live" ? "Live stream" : "Duration unavailable";
  const thumbnail = document.querySelector("#media-thumbnail");
  if (media.thumbnail) {
    thumbnail.src = media.thumbnail;
    thumbnail.hidden = false;
  } else {
    thumbnail.removeAttribute("src");
    thumbnail.hidden = true;
  }
  const previewLink = document.querySelector("#media-preview-link");
  previewLink.href = media.webpage_url;
}

function renderFormats(formats) {
  outputFields.disabled = false;
  const options = formats.options || [];
  const availableOptions = options.filter((item) => item.available);
  availableOutputFormats = new Set(availableOptions.map((item) => item.format));
  for (const option of formatSelect.options) option.disabled = !availableOutputFormats.has(option.value);
  formatSelect.disabled = availableOptions.length === 0;
  const unavailable = [...new Map(options.filter((item) => !item.available).map((item) => [item.format, item.unavailable_reason])).entries()];
  document.querySelector("#format-availability").textContent = unavailable.map(([format, reason]) => `${format.toUpperCase()} unavailable: ${reason || "Required encoder is unavailable"}.`).join(" ");
  formatSelect.value = availableOptions[0]?.format || "";
  const refreshQuality = () => {
    qualitySelect.replaceChildren();
    for (const item of availableOptions.filter((entry) => entry.format === formatSelect.value)) {
      const choice = document.createElement("option");
      choice.value = item.resolution || String(item.audio_bitrate_kbps || "");
      choice.textContent = (item.resolution || (item.audio_bitrate_kbps ? `${item.audio_bitrate_kbps} kbps` : item.quality_note)) + (item.requires_audio_merge ? " · audio merge" : "");
      qualitySelect.append(choice);
    }
    qualitySelect.value = qualitySelect.options[0]?.value || "";
    qualitySelect.disabled = qualitySelect.options.length === 0;
    updateJobButton();
  };
  formatSelect.onchange = refreshQuality;
  refreshQuality();
  const sourceList = document.querySelector("#source-formats-list");
  sourceList.replaceChildren();
  for (const item of formats.formats || []) {
    const row = document.createElement("li");
    const label = document.createElement("strong");
    label.textContent = `${item.format_id} · ${item.extension || "unknown"}`;
    const kind = item.has_video && item.has_audio ? "video + audio" : item.has_video ? "video only" : item.has_audio ? "audio only" : "unknown stream";
    const details = [kind, item.container ? `container ${item.container}` : null, item.width && item.height ? `${item.width}×${item.height}` : null, item.fps ? `${item.fps} fps` : null, item.video_codec, item.audio_codec, item.total_bitrate ? `${item.total_bitrate} kbps` : null, item.filesize ? `${item.filesize} bytes` : item.filesize_approx ? `about ${item.filesize_approx} bytes` : "size unknown"].filter(Boolean);
    row.append(label, document.createTextNode(` · ${details.join(" · ")}`));
    sourceList.append(row);
  }
  document.querySelector("#source-formats").hidden = sourceList.children.length === 0;
}

function resetAnalysis() {
  currentMediaId = null;
  availableOutputFormats = new Set();
  document.querySelector("#preview-empty").hidden = false;
  document.querySelector("#media-details").hidden = true;
  document.querySelector("#source-formats").hidden = true;
  document.querySelector("#source-formats-list").replaceChildren();
  document.querySelector("#media-thumbnail").removeAttribute("src");
  outputFields.disabled = true;
  formatSelect.disabled = true;
  qualitySelect.disabled = true;
  createJobButton.disabled = true;
  qualitySelect.replaceChildren();
  qualitySelect.value = "";
  document.querySelector("#format-availability").textContent = "";
}

analyzeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = document.querySelector("#source-url").value.trim();
  markScrollIntent();
  const intentVersion = scrollIntentVersion;
  analysisSequence += 1;
  const sequence = analysisSequence;
  if (analysisController) analysisController.abort();
  analysisController = new AbortController();
  resetAnalysis();
  if (!url) {
    showNotice("Enter a video URL.", true);
    return;
  }
  analyzeButton.firstChild.textContent = "Analyzing ";
  showNotice("Analyzing source metadata...");
  try {
    const media = await apiRequest("/api/analyze", { method: "POST", body: JSON.stringify({ url }), signal: analysisController.signal });
    if (sequence !== analysisSequence) return;
    renderMedia(media);
    showNotice("Loading source formats...");
    const formats = await apiRequest(`/api/formats/${encodeURIComponent(media.media_id)}`, { signal: analysisController.signal });
    if (sequence !== analysisSequence) return;
    renderFormats(formats);
    if (!workerReady) showNotice(`Media analyzed. Conversion unavailable: ${workerReason}.`);
    else if (!formats.options?.some((item) => item.available)) showNotice("No conversion output is available for this source.", true);
    else {
      const unavailable = formats.options.find((item) => !item.available);
      showNotice(unavailable ? `Media analyzed. ${unavailable.format.toUpperCase()} unavailable: ${unavailable.unavailable_reason}. Choose an available output.` : "Media analyzed. Choose an available output. Conversion requires authorized network access and media-processing permission.");
    }
    scrollToCard(".media-panel", intentVersion, () => sequence === analysisSequence);
  } catch (error) {
    if (sequence === analysisSequence && error.name !== "AbortError") {
      resetAnalysis();
      showNotice(error.message, true);
    }
  } finally {
    if (sequence === analysisSequence) analyzeButton.firstChild.textContent = "Analyze ";
  }
});

jobForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (createJobButton.disabled) return;
  if (!currentMediaId) return;
  markScrollIntent();
  const intentVersion = scrollIntentVersion;
  jobRequestSequence += 1;
  const requestSequence = jobRequestSequence;
  const analysisAtSubmit = analysisSequence;
  const format = formatSelect.value;
  const quality = qualitySelect.value;
  const payload = { media_id: currentMediaId, format, include_subtitles: document.querySelector("#include-subtitles").checked, include_metadata: document.querySelector("#include-metadata").checked, confirm_rights: document.querySelector("#confirm-rights").checked };
  if (["mp4", "webm"].includes(format)) payload.resolution = quality;
  else if (quality) payload.audio_bitrate_kbps = Number(quality);
  try {
    createJobButton.disabled = true;
    const job = await apiRequest("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
    currentJobId = job.job_id;
    saveRecentJob(job.job_id, format, job.state);
    document.querySelector("#progress-empty").hidden = true;
    document.querySelector("#progress-active").hidden = false;
    renderJob(job);
    watchJob(job.job_id);
    scrollToCard(".progress-panel", intentVersion, () => requestSequence === jobRequestSequence && analysisAtSubmit === analysisSequence && currentJobId === job.job_id);
  } catch (error) {
    if (requestSequence !== jobRequestSequence || analysisAtSubmit !== analysisSequence) return;
    if (error.status === 403 && error.code === "network_restricted") showRestrictedDialog();
    else showNotice(error.message, true);
  } finally {
    updateJobButton();
  }
});

function renderJob(job) {
  currentJobId = job.job_id;
  document.querySelector("#progress-empty").hidden = true;
  const active = document.querySelector("#progress-active");
  active.hidden = false;
  active.dataset.state = job.state || "";
  const numericProgress = Number(job.progress);
  const progress = job.state === "completed" ? 100 : Number.isFinite(numericProgress) ? Math.max(0, Math.min(100, numericProgress)) : 0;
  const displayPercent = (value) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric.toFixed(1) : "0.0";
  };
  document.querySelector("#job-state").textContent = job.state || "Processing";
  document.querySelector("#job-progress").value = progress;
  document.querySelector("#job-percent").textContent = `${displayPercent(progress)}% · download ${displayPercent(job.download_progress)}% · conversion ${displayPercent(job.conversion_progress)}%`;
  document.querySelector("#cancel-job").disabled = ["completed", "failed", "cancelled"].includes(job.state);
  if (job.error) showNotice(job.error, true);
  const link = document.querySelector("#download-link");
  link.hidden = !(job.state === "completed" && job.download_url);
  if (!link.hidden) link.href = job.download_url;
}

function watchJob(jobId) {
  if (jobEvents) jobEvents.close();
  jobEvents = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events`);
  jobEvents.addEventListener("job", (event) => {
    let job;
    try { job = JSON.parse(event.data); } catch { showNotice("Invalid job update received.", true); return; }
    renderJob(job);
    const saved = recentJobs.find((entry) => entry.id === jobId);
    if (saved) saveRecentJob(jobId, saved.format, job.state || "processing");
    if (["completed", "failed", "cancelled"].includes(job.state)) {
      jobEvents.close();
    }
  });
  jobEvents.onerror = () => { showNotice("Job updates disconnected. Reconnecting...", true); };
}

document.querySelector("#cancel-job").addEventListener("click", async () => {
  if (!currentJobId) return;
  try {
    const job = await apiRequest(`/api/jobs/${encodeURIComponent(currentJobId)}/cancel`, { method: "POST" });
    if (jobEvents) jobEvents.close();
    renderJob(job);
    const saved = recentJobs.find((entry) => entry.id === currentJobId);
    if (saved) saveRecentJob(currentJobId, saved.format, "cancelled");
    showNotice("Job cancelled.");
  } catch (error) {
    showNotice(error.message, true);
  }
});

const savedTheme = localStorage.getItem("ytconvert-theme");
if (savedTheme === "light") document.documentElement.dataset.theme = "light";
themeToggle.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("ytconvert-theme", next);
});

checkStatus();
renderRecentJobs();
apiRequest("/api/jobs").then((result) => {
  if (!result.jobs?.length) return;
  recentJobs.length = 0;
  for (const job of result.jobs.slice(0, 5)) recentJobs.push({ id: job.job_id, format: job.format, state: job.state });
  renderRecentJobs();
  const active = result.jobs.find((job) => ["queued", "downloading", "processing"].includes(job.state));
  if (active) { renderJob(active); watchJob(active.job_id); }
}).catch(() => {});
