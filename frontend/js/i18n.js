(() => {
  const supported = ["ko", "en", "ja", "zh-CN"];
  const fallbackLocale = "en";
  const defaultLocale = "ko";
  const storageKey = "ytconvert.locale";
  const cache = new Map();
  const missing = new Set();
  let locale = defaultLocale;
  let resources = {};
  let sequence = 0;
  const english = {
    "meta.title": "YTConvert · Media workspace", "header.language": "Language", "header.theme": "Theme", "header.service_online": "Service online", "header.service_offline": "Service unavailable", "header.theme_switch": "Switch color theme", "language.ko": "한국어", "language.en": "English", "language.ja": "日本語", "language.zh-CN": "简体中文", "source.analyze": "Analyze", "source.analyzing": "Analyzing", "source.url_label": "Video URL", "source.url_placeholder": "https://www.youtube.com/watch?v=...", "source.hint": "Metadata analysis requests source information without downloading a media file.", "preview.untitled": "Untitled video", "preview.channel_unavailable": "Channel unavailable", "preview.duration": "{value} seconds", "preview.live": "Live stream", "preview.duration_unavailable": "Duration unavailable", "output.required_encoder": "Required encoder is unavailable", "output.merge": "audio merge", "output.worker_notice": "Conversion unavailable: {reason}.", "output.worker_ready": "Output quality cannot exceed the source. Conversion also requires operator-authorized network access and permission to process the media.", "progress.download": "download {value}%", "progress.conversion": "conversion {value}%", "progress.merging": "Merging audio", "progress.probing": "Inspecting media", "progress.processing": "Processing", "progress.validating": "Validating output", "status.unknown": "Unknown", "history.id": "ID {value}", "messages.network_error": "Network error. Check your connection and try again.", "messages.request_failed": "Request failed with status {status}.", "messages.analysis_unavailable": "Media analysis is not available yet.", "messages.service_unavailable": "The service is unavailable. Please try again later.", "messages.enter_url": "Enter a video URL.", "messages.analyzing_metadata": "Analyzing source metadata...", "messages.loading_formats": "Loading source formats...", "messages.analyzed_unavailable": "Media analyzed. Conversion unavailable: {reason}.", "messages.no_output": "No conversion output is available for this source.", "messages.choose_output": "Media analyzed. Choose an available output. Conversion requires authorized network access and media-processing permission.", "messages.format_unavailable": "Media analyzed. {format} unavailable: {reason}. Choose an available output.", "messages.invalid_update": "Invalid job update received.", "messages.disconnected": "Job updates disconnected. Reconnecting...", "messages.cancelled": "Job cancelled.", "errors.default": "The request could not be completed.", "footer.description": "Built for clear, controlled media workflows.", "accessibility.workspace": "Conversion workspace", "accessibility.progress": "Job progress", "accessibility.dismiss": "Understood", "restricted.title": "Restricted Service Access", "restricted.description": "This installation permits conversion only from hosts and networks authorized by its operator. Metadata analysis does not grant permission to download or convert media.", "legal.notice": "Before converting, review copyright permissions, platform terms, and applicable law. Analysis and available formats do not grant permission."
  };
  const readStored = () => { try { const value = localStorage.getItem(storageKey); return supported.includes(value) ? value : null; } catch { return null; } };
  const get = (object, key) => key.split(".").reduce((value, part) => value && typeof value === "object" ? value[part] : undefined, object);
  const interpolate = (value, params = {}) => String(value).replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (match, name) => Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match);
  const t = (key, params) => {
    const value = get(resources[locale], key) ?? get(resources[fallbackLocale], key) ?? english[key];
    if (value === undefined) { if (!missing.has(key)) { missing.add(key); console.debug(`[i18n] Missing translation: ${key}`); } return key; }
    return interpolate(value, params);
  };
  const apply = (root = document) => {
    root.querySelectorAll("[data-i18n]").forEach((element) => { element.textContent = t(element.dataset.i18n); });
    root.querySelectorAll("[data-i18n-placeholder]").forEach((element) => { element.placeholder = t(element.dataset.i18nPlaceholder); });
    root.querySelectorAll("[data-i18n-title]").forEach((element) => { element.title = t(element.dataset.i18nTitle); });
    root.querySelectorAll("[data-i18n-aria-label]").forEach((element) => { element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel)); });
    root.querySelectorAll("[data-i18n-alt]").forEach((element) => { element.alt = t(element.dataset.i18nAlt); });
    document.documentElement.lang = locale;
    document.title = t("meta.title");
  };
  const applyKnown = () => {
    const selectors = {".eyebrow":"hero.eyebrow", ".hero-copy":"hero.description", ".source-panel .section-kicker":"source.eyebrow", "#source-title":"source.title", ".source-panel .panel-description":"source.description", ".source-panel label":"source.url_label", ".field-hint":"source.hint", ".media-panel .section-kicker":"preview.eyebrow", "#media-title":"preview.title", ".source-formats h3":"preview.streams", ".source-formats p":"preview.streams_description", ".media-panel #media-preview-link":"preview.open_source", ".options-panel .section-kicker":"output.eyebrow", "#options-title":"output.title", "#job-availability":"output.settings_notice", ".progress-panel .section-kicker":"progress.eyebrow", "#progress-title":"progress.title", "#cancel-job":"progress.cancel", "#download-link":"progress.download_file", ".history-panel .section-kicker":"history.eyebrow", "#history-title":"history.title", ".rights-note p":"legal.notice", ".footer span:last-child":"footer.description", "#restricted-dialog-title":"restricted.title", "#restricted-dialog-description":"restricted.description", "#restricted-dialog-dismiss":"accessibility.dismiss"};
    Object.entries(selectors).forEach(([selector, key]) => { const element = document.querySelector(selector); if (element) { element.dataset.i18n = key; element.textContent = t(key); } });
    const h1 = document.querySelector("#hero-title"); if (h1) { h1.firstChild.textContent = `${t("hero.title_start")} `; h1.querySelector("em").textContent = t("hero.title_emphasis"); }
    const analyze = document.querySelector("#analyze-button"); if (analyze) { const textNode = [...analyze.childNodes].find((node) => node.nodeType === Node.TEXT_NODE); if (textNode) textNode.textContent = ` ${t("source.analyze")} `; }
    const meta = document.querySelectorAll(".hero-meta span"); ["steps.analyze", "steps.choose", "steps.convert"].forEach((key, index) => { if (meta[index]) meta[index].textContent = `${String(index + 1).padStart(2, "0")}   ${t(key)}`; });
    const sourceInput = document.querySelector("#source-url"); if (sourceInput) sourceInput.placeholder = t("source.url_placeholder");
    const empty = document.querySelector("#preview-empty"); if (empty) { empty.querySelector("strong").textContent = t("preview.empty"); empty.querySelector("p").textContent = t("preview.empty_description"); }
    const progressEmpty = document.querySelector("#progress-empty"); if (progressEmpty) { progressEmpty.querySelector("strong").textContent = t("progress.empty"); progressEmpty.querySelector("p").textContent = t("progress.empty_description"); }
    const historyEmpty = document.querySelector("#history-empty"); if (historyEmpty) { historyEmpty.querySelector("strong").textContent = t("history.empty"); historyEmpty.querySelector("p").textContent = t("history.empty_description"); }
    const labels = {"#cancel-job":"progress.cancel", "#download-link":"progress.download_file", "#create-job-button":"output.create"}; Object.entries(labels).forEach(([selector, key]) => { const element = document.querySelector(selector); if (element) { const textNode = [...element.childNodes].find((node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim()); if (textNode) textNode.textContent = ` ${t(key)} `; else element.textContent = t(key); } });
    const themeToggle = document.querySelector("#theme-toggle"); if (themeToggle) { const themeLabel = themeToggle.querySelector("span"); if (themeLabel) { themeLabel.dataset.i18n = "header.theme"; themeLabel.textContent = t("header.theme"); } const themeDescription = t("header.theme_switch"); themeToggle.setAttribute("aria-label", themeDescription); themeToggle.title = themeDescription; }
    const fields = {"#include-metadata":"output.metadata", "#include-subtitles":"output.subtitles", "#confirm-rights":"output.rights"}; Object.entries(fields).forEach(([selector, key]) => { const input = document.querySelector(selector); if (input?.parentElement) { const textNode = [...input.parentElement.childNodes].find((node) => node.nodeType === Node.TEXT_NODE); if (textNode) textNode.textContent = ` ${t(key)}`; } });
    const formatLabel = document.querySelector("label[for=format]"); const qualityLabel = document.querySelector("label[for=quality]"); if (formatLabel) formatLabel.textContent = t("output.format"); if (qualityLabel) qualityLabel.textContent = t("output.quality");
    const formatOptions = { mp4: "MP4 · "+t("output.video"), webm: "WEBM · "+t("output.video"), mp3: "MP3 · "+t("output.audio"), m4a: "M4A · "+t("output.audio"), opus: "Opus · "+t("output.audio") }; document.querySelectorAll("#format option").forEach((option) => { if (formatOptions[option.value]) option.textContent = formatOptions[option.value]; });
    const quality = document.querySelector("#quality"); if (quality && !quality.options.length) { const option = document.createElement("option"); option.value = ""; option.textContent = t("output.select_source"); quality.append(option); }
    const progress = document.querySelector("#job-progress"); if (progress) progress.setAttribute("aria-label", t("accessibility.progress")); const workspace = document.querySelector(".workspace"); if (workspace) workspace.setAttribute("aria-label", t("accessibility.workspace")); const legend = document.querySelector("#output-fields legend"); if (legend) legend.textContent = t("output.title"); const thumbnail = document.querySelector("#media-thumbnail"); if (thumbnail) thumbnail.alt = t("preview.title");
  };
  const load = async (requested) => {
    if (cache.has(requested)) return cache.get(requested);
    try { const response = await fetch(`/lang/${encodeURIComponent(requested)}.json`, { headers: { Accept: "application/json" } }); if (!response.ok) throw new Error("translation unavailable"); const data = await response.json(); cache.set(requested, data); return data; } catch { return null; }
  };
  const applyLanguageControl = () => {
    const select = document.querySelector("#language-select"); if (select) { select.value = locale; select.setAttribute("aria-label", t("header.language")); }
    const label = document.querySelector("#language-label"); if (label) label.textContent = t("header.language");
    if (select) select.querySelectorAll("option").forEach((option) => { option.textContent = t(`language.${option.value}`); });
  };
  const setLocale = async (requested) => {
    const next = supported.includes(requested) ? requested : defaultLocale;
    const token = ++sequence;
    const fallbackData = await load(fallbackLocale);
    if (fallbackData) resources[fallbackLocale] = fallbackData;
    let loadedLocale = next;
    let data = await load(loadedLocale);
    if (!data && loadedLocale !== fallbackLocale) { loadedLocale = fallbackLocale; data = await load(loadedLocale); }
    if (token !== sequence) return locale;
    if (data) resources[loadedLocale] = data;
    locale = data ? loadedLocale : fallbackLocale;
    try { localStorage.setItem(storageKey, locale); } catch {}
    applyKnown(); apply(document); applyLanguageControl();
    document.dispatchEvent(new CustomEvent("ytconvert:locale-changed", { detail: { locale } }));
    return locale;
  };
  const formatNumber = (value, options) => new Intl.NumberFormat(locale, options).format(value);
  const formatDate = (value, options) => new Intl.DateTimeFormat(locale, options).format(new Date(value));
  const formatDuration = (value) => t("preview.duration", { value: Math.round(value) });
  const formatBytes = (value) => { if (!Number.isFinite(value)) return "—"; const units = ["B", "KB", "MB", "GB"]; let index = 0; let amount = value; while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; } return `${formatNumber(amount, { maximumFractionDigits: 1 })} ${units[index]}`; };
  const init = async () => { locale = readStored() || defaultLocale; const select = document.createElement("select"); select.id = "language-select"; select.className = "language-select"; select.setAttribute("aria-label", t("header.language")); for (const code of supported) { const option = document.createElement("option"); option.value = code; option.textContent = t(`language.${code}`); select.append(option); } const wrapper = document.createElement("label"); wrapper.className = "theme-toggle language-control"; const icon = document.createElement("span"); icon.className = "language-icon"; icon.setAttribute("aria-hidden", "true"); icon.textContent = "🌐"; wrapper.append(icon, select); const actions = document.querySelector(".topbar-actions"); if (actions) actions.insertBefore(wrapper, document.querySelector("#theme-toggle")); select.addEventListener("change", () => setLocale(select.value)); await setLocale(locale); document.dispatchEvent(new Event("ytconvert:i18n-ready")); };
  globalThis.ytI18n = { supported, defaultLocale, fallbackLocale, init, getLocale: () => locale, setLocale, t, applyTranslations: apply, formatNumber, formatDate, formatDuration, formatBytes };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true }); else init();
})();
