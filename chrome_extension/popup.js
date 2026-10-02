const DEFAULT_BASE_URL = "http://127.0.0.1:8787";
const JOB_KEY = "loraManagerBridgeJob";
const LAUNCH_PROTOCOL = "lora-manager";
const APP_HEALTHCHECK_TIMEOUT_MS = 1500;
const APP_LAUNCH_WAIT_MS = 15000;
const APP_LAUNCH_POLL_MS = 500;
const WATCH_DIR_FAMILY_ALIASES = {
  workflow: ["workflow", "workflows"],
  lora: ["lora", "loras"],
  lycoris: ["lycoris", "locon", "loha", "lokr"],
  checkpoint: ["checkpoint", "checkpoints", "stablediffusion", "stable-diffusion", "stable_diffusion"],
  embedding: ["embedding", "embeddings", "textualinversion", "textual-inversion"],
};

const state = {
  pageData: null,
  activeTabId: null,
  watchDirs: [],
  lastJob: null,
  busy: false,
  waiting: false,
  pendingLoaded: false,
  pendingRefreshing: false,
  pending: null,
  recoveryRunning: false,
  recoveryRevision: 0,
  recoveryRefreshing: false,
  actionRevision: 0,
};

const elements = {
  baseUrl: document.getElementById("base-url"),
  openApp: document.getElementById("open-app"),
  refreshPage: document.getElementById("refresh-page"),
  sendImport: document.getElementById("send-import"),
  downloadImport: document.getElementById("download-import"),
  cancelPending: document.getElementById("cancel-pending"),
  recoverDownloads: document.getElementById("recover-downloads"),
  recoveryStatus: document.getElementById("recovery-status"),
  recoveryResults: document.getElementById("recovery-results"),
  downloadSelect: document.getElementById("download-select"),
  folderSelect: document.getElementById("folder-select"),
  downloadPill: document.getElementById("download-pill"),
  downloadNote: document.getElementById("download-note"),
  hostPill: document.getElementById("host-pill"),
  pageTitle: document.getElementById("page-title"),
  pageUrl: document.getElementById("page-url"),
  pageDescription: document.getElementById("page-description"),
  badgeRow: document.getElementById("badge-row"),
  jobLine: document.getElementById("job-line"),
  statusLine: document.getElementById("status-line"),
};

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  chrome.storage.onChanged.addListener(handleStorageChange);
  await restoreBaseUrl();
  await Promise.all([refreshPageData(), loadWatchDirs(), loadLastJob(), refreshPendingState()]);
  await refreshRecoveryState();
  window.setInterval(refreshPendingState, 2000);
  window.setInterval(refreshRecoveryState, 2000);
});

function bindEvents() {
  elements.recoverDownloads.addEventListener("click", async () => {
    if (state.busy || state.recoveryRunning) return;
    state.recoveryRevision += 1;
    setBusyState(true);
    elements.recoveryStatus.textContent = "Chromeのダウンロード履歴を調査しています…";
    try {
      const baseUrl = await persistBaseUrl();
      const candidate = getSelectedDownloadCandidate();
      const metadata = candidate ? {...state.pageData, download_url: candidate.url} : {};
      const response = await chrome.runtime.sendMessage({type: "recover-downloads", baseUrl, metadata});
      if (!response?.ok) throw new Error(response?.error || "調査を開始できませんでした。LoRA管理を更新して再起動してください。");
      renderRecoveryState(response.result);
    } catch (error) {
      elements.recoveryStatus.textContent = error.message;
    } finally {
      setBusyState(false);
    }
  });

  elements.cancelPending.addEventListener("click", async () => {
    if (state.busy || !state.pending) return;
    const pending = state.pending;
    state.actionRevision += 1;
    setBusyState(true);
    try {
      await fetchLocalJson(normalizeBaseUrl(elements.baseUrl.value), "/api/lora/cancel-pending-download", {
        method: "POST", body: {created_epoch: pending.created_epoch},
      });
      setStatus("取り込み待機を解除しました。");
    } catch (error) { setStatus(error.message, true); }
    finally {setBusyState(false); await refreshPendingState();}
  });
  elements.openApp.addEventListener("click", async () => {
    await openApp();
  });

  elements.refreshPage.addEventListener("click", async () => {
    await refreshPageData();
  });

  elements.baseUrl.addEventListener("change", async () => {
    state.actionRevision += 1;
    state.pendingLoaded = false;
    state.pending = null;
    state.waiting = false;
    updateActionState();
    await persistBaseUrl();
    await Promise.all([loadWatchDirs(), refreshPendingState()]);
  });

  elements.downloadSelect.addEventListener("change", () => {
    renderWatchDirs();
    updateActionState();
  });

  elements.folderSelect.addEventListener("change", () => {
    updateDownloadNote();
  });

  elements.sendImport.addEventListener("click", async () => {
    if (!state.pageData) {
      setStatus("送れるページ情報がありません。", true);
      return;
    }

    const baseUrl = await persistBaseUrl();
    setStatus("LoRA管理へ送信しています。");

    try {
      const payload = await fetchLocalJson(baseUrl, "/api/lora/browser-imports", {
        method: "POST",
        body: state.pageData,
      });
      if (!payload.ok) {
        throw new Error(payload.error || "送信に失敗しました。");
      }

      setStatus("LoRA管理へ送信しました。LoRA管理側で `反映` できます。");
    } catch (error) {
      setStatus(error.message || "送信に失敗しました。", true);
    }
  });

  elements.downloadImport.addEventListener("click", async () => {
    if (state.busy || state.waiting || !state.pendingLoaded) return;
    state.actionRevision += 1;
    const blockedReason = getDownloadActionProblem();
    if (blockedReason) {
      setStatus(blockedReason, true);
      return;
    }

    const selectedCandidate = getSelectedDownloadCandidate();
    const canUseCivitaiPending = canStartPendingImportWithoutCandidate();
    if (!(selectedCandidate || canUseCivitaiPending)) {
      setStatus("ページ上のダウンロード候補が見つかりません。", true);
      return;
    }

    const selectedTarget = String(elements.folderSelect.value || "__auto__").trim();
    const targetDir = selectedTarget === "__auto__" ? "" : selectedTarget;
    const pageData = state.pageData
      ? {
          ...state.pageData,
          model_family_hint: inferModelFamilyFromCurrentContext() || state.pageData.model_family_hint || "",
        }
      : null;

    setBusyState(true);
    setStatus("ダウンロードと反映を開始しています。");

    try {
      const baseUrl = await persistBaseUrl();
      const response = await chrome.runtime.sendMessage({
        type: "download-and-import",
        baseUrl,
        downloadUrl: selectedCandidate?.url || state.pageData?.source_url || "",
        selectedCandidate,
        targetDir,
        tabId: state.activeTabId,
        pageData,
      });

      if (!response?.ok) {
        throw new Error(response?.error || "ダウンロードに失敗しました。");
      }

      if (response.result?.status === "error") throw new Error(response.result.message || "取り込みに失敗しました。");
      if (response.result?.pending) {
        state.waiting = true;
        updateActionState();
        setStatus(response.result.message || "Downloads フォルダの待機を開始しました。");
        await loadLastJob();
        return;
      }

      const fileName = response.result?.filename || "LoRA";
      setStatus(`${fileName} をダウンロードして反映しました。`);
    } catch (error) {
      setStatus(error.message || "ダウンロードに失敗しました。", true);
    } finally {
      setBusyState(false);
      await loadLastJob();
      await refreshPendingState();
    }
  });
}

async function refreshRecoveryState() {
  if (state.recoveryRefreshing || state.busy) return;
  state.recoveryRefreshing = true;
  const revision = state.recoveryRevision;
  const baseUrl = normalizeBaseUrl(elements.baseUrl.value);
  try {
    const response = await fetch(`${baseUrl}/api/lora/recover-downloads`, {signal: AbortSignal.timeout(1500)});
    if (!response.ok) throw new Error("LoRA管理を更新して再起動してください。");
    const payload = await response.json();
    if (revision === state.recoveryRevision && baseUrl === normalizeBaseUrl(elements.baseUrl.value)) renderRecoveryState(payload);
  } catch (error) {
    elements.recoveryStatus.textContent = state.recoveryRunning ? "処理状況に接続できません。再接続すると進捗を表示します。" : error.message;
  } finally {
    state.recoveryRefreshing = false;
  }
}

function renderRecoveryState(job) {
  state.recoveryRunning = job?.status === "running";
  updateActionState();
  if (!job || job.status === "idle") return;
  elements.recoveryStatus.textContent = `${job.message || ""} 登録・移動 ${job.imported || 0}件 / 対象外 ${job.skipped || 0}件 / 失敗 ${job.failed || 0}件`;
  elements.recoveryResults.replaceChildren();
  for (const result of job.results || []) {
    const row = document.createElement("li");
    row.textContent = `${result.filename}: ${result.reason}${result.path ? ` → ${result.path}` : ""}`;
    row.title = result.source_path || "";
    elements.recoveryResults.append(row);
  }
}

async function refreshPendingState() {
  if (state.busy || state.pendingRefreshing) return;
  state.pendingRefreshing = true;
  const revision = state.actionRevision;
  const baseUrl = normalizeBaseUrl(elements.baseUrl.value);
  try {
    const response = await fetch(`${baseUrl}/api/lora/pending-download`, {signal: AbortSignal.timeout(1500)});
    if (!response.ok) throw new Error("Waiting state unavailable");
    const payload = await response.json();
    if (revision !== state.actionRevision || baseUrl !== normalizeBaseUrl(elements.baseUrl.value)) return;
    state.pending = payload.pending || null;
    state.waiting = Boolean(state.pending);
    state.pendingLoaded = true;
    const result = payload.last_result;
    if (!state.waiting && ["complete", "error", "cancelled"].includes(result?.status)) {
      state.lastJob = {status: result.status, message: result.message, fileName: result.filename, importedPath: result.path};
      renderLastJob();
    }
  } catch (_error) {
    if (revision === state.actionRevision) setStatus("LoRA管理の待機状態を確認できません。接続を確認してください。", true);
  } finally {
    state.pendingRefreshing = false;
    updateActionState();
  }
}

async function restoreBaseUrl() {
  const stored = await chrome.storage.sync.get({ loraManagerBaseUrl: DEFAULT_BASE_URL });
  elements.baseUrl.value = normalizeBaseUrl(stored.loraManagerBaseUrl);
}

async function persistBaseUrl() {
  const normalized = normalizeBaseUrl(elements.baseUrl.value);
  elements.baseUrl.value = normalized;
  await chrome.storage.sync.set({ loraManagerBaseUrl: normalized });
  return normalized;
}

async function openApp() {
  const baseUrl = await persistBaseUrl();
  setBusyState(true);
  setStatus("LoRA管理の状態を確認しています。");

  try {
    const isReadyNow = canReachLocalAppImmediately(baseUrl);
    if (!isReadyNow) {
      setStatus("LoRA管理を起動しています。");
      triggerLocalAppLaunch(baseUrl);
    }
    const isReady = isReadyNow || (await waitForLocalApp(baseUrl));

    if (!isReady) {
      throw new Error(
        "LoRA管理を起動できませんでした。最初の1回だけ register_lora_manager_protocol.bat を実行してください。"
      );
    }

    await chrome.tabs.create({ url: `${baseUrl}/lora` });
    setStatus("LoRA管理を開きました。");
  } catch (error) {
    setStatus(error.message || "LoRA管理を開けませんでした。", true);
  } finally {
    setBusyState(false);
    await loadWatchDirs();
  }
}

async function loadWatchDirs() {
  const baseUrl = normalizeBaseUrl(elements.baseUrl.value);
  try {
    const payload = await fetchLocalJson(baseUrl, "/api/lora/config");
    state.watchDirs = Array.isArray(payload.watch_dirs) ? payload.watch_dirs : [];
  } catch (_error) {
    state.watchDirs = [];
  }

  renderWatchDirs();
  updateActionState();
}

async function loadLastJob() {
  const stored = await chrome.storage.local.get({ [JOB_KEY]: null });
  state.lastJob = stored[JOB_KEY] || null;
  renderLastJob();
}

function handleStorageChange(changes, areaName) {
  if (areaName !== "local" || !changes[JOB_KEY]) {
    return;
  }
  state.lastJob = changes[JOB_KEY].newValue || null;
  renderLastJob();
  void refreshPendingState();
}

function normalizeBaseUrl(value) {
  const text = String(value || "").trim() || DEFAULT_BASE_URL;
  try {
    const url = new URL(text);
    return `${url.protocol}//${url.host}`;
  } catch (_error) {
    return DEFAULT_BASE_URL;
  }
}

async function refreshPageData() {
  setStatus("ページ情報を取得しています。");

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id || !/^https?:/i.test(tab.url || "")) {
      state.pageData = null;
      state.activeTabId = null;
      renderPageData();
      renderDownloadCandidates();
      setStatus("このタブではページ情報を取得できません。", true);
      return;
    }

    state.activeTabId = tab.id;
    const [result] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractPageMetadata,
    });

    state.pageData = normalizePageData(result?.result || null);
    renderPageData();
    renderDownloadCandidates();
    updateActionState();
    if (state.pageData) {
      const downloadCount = state.pageData.download_candidates.length;
      const familyText = state.pageData.model_family_hint ? `モデル種別: ${labelModelFamily(state.pageData.model_family_hint)}。` : "";
      if (downloadCount) {
        setStatus(`ページ情報を取得しました。${familyText}ダウンロード候補は ${downloadCount} 件あります。`);
      } else {
        setStatus(`ページ情報を取得しました。${familyText}必要ならページ情報だけ送信できます。`);
      }
    } else {
      setStatus("ページ情報を取得できませんでした。", true);
    }
  } catch (error) {
    state.pageData = null;
    renderPageData();
    renderDownloadCandidates();
    updateActionState();
    setStatus(error.message || "ページ取得に失敗しました。", true);
  }
}

function normalizePageData(value) {
  if (!value || typeof value !== "object") {
    return null;
  }

  return {
    source_url: String(value.source_url || "").trim(),
    source_host: String(value.source_host || "").trim(),
    title: String(value.title || "").trim(),
    description: truncateText(String(value.description || "").replace(/\s+/g, " ").trim(), 420),
    prompt: String(value.prompt || "").trim(),
    negative_prompt: String(value.negative_prompt || "").trim(),
    prompt_preview: String(value.prompt_preview || "").trim(),
    author: String(value.author || "").trim(),
    base_model: String(value.base_model || "").trim(),
    steps: String(value.steps || "").trim(),
    sampler: String(value.sampler || "").trim(),
    cfg_scale: String(value.cfg_scale || value.cfgScale || value.cfg || "").trim(),
    seed: String(value.seed || "").trim(),
    model_family_hint: normalizeModelFamily(value.model_family || value.model_family_hint || ""),
    preview_media_kind: String(value.preview_media_kind || value.media_kind || "").trim().toLowerCase(),
    preview_image_url: normalizeUrl(value.preview_image_url, value.source_url),
    preview_media_url: normalizeUrl(value.preview_media_url || value.preview_video_url || value.preview_image_url, value.source_url),
    preview_video_url: normalizeUrl(value.preview_video_url, value.source_url),
    triggers: normalizeStringList(value.triggers),
    tags: normalizeStringList(value.tags),
    resources_used: normalizeResourcesUsed(value.resources_used || value.resources, value.source_url),
    download_candidates: normalizeDownloadCandidates(value.download_candidates, value.source_url),
  };
}

function normalizeDownloadCandidates(candidates, baseUrl) {
  const seen = new Set();
  const normalized = [];

  (Array.isArray(candidates) ? candidates : []).forEach((candidate) => {
    if (!candidate || typeof candidate !== "object") {
      return;
    }
    const url = normalizeUrl(candidate.url, baseUrl);
    if (!url || seen.has(url)) {
      return;
    }
    seen.add(url);
    normalized.push({
      url,
      label: truncateText(String(candidate.label || candidate.url || "").trim() || url, 96),
      suggested_filename: fileNameFromPath(String(candidate.suggested_filename || candidate.filename || "").trim()),
    });
  });

  return normalized.slice(0, 12);
}

function normalizeUrl(value, baseUrl = "") {
  const text = String(value || "").replace(/&amp;/g, "&").trim();
  if (!text) {
    return "";
  }
  try {
    const url = new URL(text, baseUrl || undefined);
    return /^https?:$/i.test(url.protocol) ? url.href : "";
  } catch (_error) {
    return "";
  }
}

function normalizeResourcesUsed(resources, baseUrl = "") {
  const seen = new Set();
  const normalized = [];

  (Array.isArray(resources) ? resources : []).forEach((resource) => {
    if (!resource || typeof resource !== "object") {
      return;
    }

    const modelId = String(resource.model_id || resource.modelId || "").trim();
    const modelVersionId = String(
      resource.model_version_id || resource.modelVersionId || resource.version_id || resource.versionId || ""
    ).trim();
    const url =
      normalizeUrl(resource.url || resource.source_url, baseUrl) ||
      (modelId
        ? `https://civitai.red/models/${encodeURIComponent(modelId)}${
            modelVersionId ? `?modelVersionId=${encodeURIComponent(modelVersionId)}` : ""
          }`
        : "");
    const entry = {
      name: String(resource.name || resource.model_name || resource.modelName || resource.title || "").trim(),
      type: String(resource.type || resource.model_type || resource.modelType || "").trim(),
      strength: String(resource.strength ?? resource.weight ?? "").trim(),
      base_model: String(resource.base_model || resource.baseModel || "").trim(),
      version_name: String(resource.version_name || resource.versionName || "").trim(),
      model_id: modelId,
      model_version_id: modelVersionId,
      trained_words: normalizeStringList(
        resource.trained_words || resource.trainedWords || resource.trigger_words || resource.triggerWords
      ),
      url,
    };

    if (
      !entry.name &&
      !entry.type &&
      !entry.strength &&
      !entry.base_model &&
      !entry.version_name &&
      !entry.model_id &&
      !entry.model_version_id &&
      !entry.url &&
      !entry.trained_words.length
    ) {
      return;
    }

    const key = JSON.stringify([
      entry.url.toLowerCase(),
      entry.name.toLowerCase(),
      entry.type.toLowerCase(),
      entry.version_name.toLowerCase(),
      entry.strength.toLowerCase(),
    ]);
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    normalized.push(entry);
  });

  return normalized.slice(0, 40);
}

function normalizeModelFamily(value) {
  const text = String(value || "").trim().toLowerCase();
  if (!text) {
    return "";
  }

  const collapsed = text.replace(/[^a-z0-9]+/g, "");
  if (!collapsed) {
    return "";
  }
  if (collapsed.includes("workflow")) { return "workflow"; }
  if (/(lycoris|locon|loha|lokr|ia3|dylora)/.test(collapsed)) {
    return "lycoris";
  }
  if (/(textualinversion|embedding|embeddings)/.test(collapsed)) {
    return "embedding";
  }
  if (/(checkpoint|checkpoints|ckpt)/.test(collapsed)) {
    return "checkpoint";
  }
  if (collapsed.includes("lora")) {
    return "lora";
  }
  return "";
}

function labelModelFamily(value) {
  const family = normalizeModelFamily(value);
  if (family === "workflow") { return "Workflow"; }
  if (family === "lycoris") {
    return "LyCORIS";
  }
  if (family === "checkpoint") {
    return "Checkpoint";
  }
  if (family === "embedding") {
    return "Embedding";
  }
  if (family === "lora") {
    return "LoRA";
  }
  return "自動判定";
}

function inferWatchDirModelFamily(path) {
  const parts = String(path || "")
    .split(/[\\/]+/)
    .filter(Boolean)
    .slice(-4)
    .reverse();

  for (const part of parts) {
    const collapsed = String(part || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
    for (const [family, aliases] of Object.entries(WATCH_DIR_FAMILY_ALIASES)) {
      if (collapsed && aliases.some((alias) => alias.toLowerCase().replace(/[^a-z0-9]+/g, "") === collapsed)) {
        return family;
      }
    }
    const family = normalizeModelFamily(part);
    if (family) {
      return family;
    }
  }
  return "";
}

function inferModelFamilyFromCurrentContext(pageData = state.pageData, selectedCandidate = getSelectedDownloadCandidate()) {
  const explicitHint = normalizeModelFamily(pageData?.model_family_hint);
  if (explicitHint) {
    return explicitHint;
  }

  if (/\.(json|zip)$/i.test(selectedCandidate?.suggested_filename || "")) { return "workflow"; }

  const hints = [
    selectedCandidate?.suggested_filename,
    selectedCandidate?.filename,
    selectedCandidate?.label,
    selectedCandidate?.url,
    pageData?.title,
    pageData?.description,
    pageData?.source_url,
    ...(pageData?.tags || []),
    ...(pageData?.triggers || []),
  ];

  for (const hint of hints) {
    const family = normalizeModelFamily(hint);
    if (family) {
      return family;
    }
  }
  return "";
}

function resolveAutoTargetDir() {
  const family = inferModelFamilyFromCurrentContext();
  const explicitMatches = state.watchDirs.filter((path) => inferWatchDirModelFamily(path) === family);
  if (explicitMatches.length) {
    return { family, dir: explicitMatches[0] };
  }

  if (family === "lycoris") {
    const loraFallback = state.watchDirs.find((path) => inferWatchDirModelFamily(path) === "lora");
    if (loraFallback) {
      return { family, dir: loraFallback };
    }
  }

  return { family, dir: family === "workflow" ? "" : state.watchDirs.find(path => inferWatchDirModelFamily(path) !== "workflow") || "" };
}

function buildAutoTargetLabel() {
  const autoTarget = resolveAutoTargetDir();
  if (!autoTarget.dir) {
    return autoTarget.family === "workflow" ? "Workflow: workflows フォルダを登録してください" : "自動振り分け";
  }

  return `自動振り分け (${labelModelFamily(autoTarget.family)} -> ${truncateText(autoTarget.dir, 60)})`;
}

function updateDownloadNote() {
  const hasCandidates = Boolean(state.pageData?.download_candidates?.length);
  if (!state.watchDirs.length) {
    if (hasCandidates) {
      elements.downloadNote.textContent = "LoRA管理に watch フォルダがまだありません。先にフォルダ登録をしてください。";
    }
    return;
  }

  if (!hasCandidates) {
    if (inferModelFamilyFromCurrentContext() === "workflow") {
      elements.downloadNote.textContent = "Workflow の JSON / ZIP を選び、Civitaiでダウンロードしてください。JSONをComfyUIのworkflowsへ取り込みます。";
      return;
    }
    if (canStartPendingImportWithoutCandidate()) {
      elements.downloadNote.textContent =
        "この Civitai ページは候補URLを拾えなくても使えます。押すと待機を開始するので、そのあと Civitai 側で手動ダウンロードしてください。";
      return;
    }
    elements.downloadNote.textContent = "ページ内のダウンロードURLをまだ見つけられていません。Civitai のような配布ページで使うと見つかりやすいです。";
    return;
  }

  if (String(elements.folderSelect.value || "__auto__") === "__auto__") {
    const autoTarget = resolveAutoTargetDir();
    const dirText = autoTarget.dir ? ` -> ${truncateText(autoTarget.dir, 68)}` : "";
    elements.downloadNote.textContent = `${labelModelFamily(autoTarget.family)} として自動振り分けします${dirText}。必要ならプルダウンで手動指定もできます。`;
    return;
  }

  elements.downloadNote.textContent = "候補URLから Chrome でダウンロードして、選んだフォルダへそのまま反映します。";
}

function fileNameFromPath(value) {
  return String(value || "").split(/[\\/]/).pop() || "";
}

function renderPageData() {
  const data = state.pageData;
  elements.badgeRow.innerHTML = "";

  if (!data) {
    elements.hostPill.textContent = "-";
    elements.pageTitle.textContent = "ページ未取得";
    elements.pageUrl.textContent = "-";
    elements.pageDescription.textContent = "description なし";
    return;
  }

  elements.hostPill.textContent = data.source_host || "-";
  elements.pageTitle.textContent = data.title || "タイトルなし";
  elements.pageUrl.textContent = data.source_url || "-";
  elements.pageDescription.textContent = data.description || data.prompt_preview || "description なし";

  [
    data.author ? `author ${data.author}` : "",
    data.base_model ? `base ${data.base_model}` : "",
    data.model_family_hint ? `family ${labelModelFamily(data.model_family_hint)}` : "",
    data.preview_media_url || data.preview_video_url || data.preview_image_url ? "preview yes" : "",
    data.prompt ? "prompt yes" : "",
    data.negative_prompt ? "negative yes" : "",
    data.triggers.length ? `trigger ${data.triggers.length}` : "",
    data.tags.length ? `tags ${data.tags.length}` : "",
    data.download_candidates.length ? `download ${data.download_candidates.length}` : "",
  ]
    .filter(Boolean)
    .forEach((text) => {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = text;
      elements.badgeRow.append(badge);
    });
}

function renderDownloadCandidates() {
  const candidates = state.pageData?.download_candidates || [];
  const currentValue = elements.downloadSelect.value;

  elements.downloadSelect.innerHTML = "";
  elements.downloadPill.textContent = `${candidates.length}件`;

  if (!candidates.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "候補が見つかりません";
    elements.downloadSelect.append(option);
    elements.downloadSelect.disabled = true;
  } else {
    candidates.forEach((candidate) => {
      const option = document.createElement("option");
      option.value = candidate.url;
      option.textContent = candidate.label;
      if (candidate.url === currentValue) {
        option.selected = true;
      }
      elements.downloadSelect.append(option);
    });
    elements.downloadSelect.disabled = false;
  }
  renderWatchDirs();
  updateDownloadNote();
}

function renderWatchDirs() {
  const currentValue = String(elements.folderSelect.value || "__auto__");
  elements.folderSelect.innerHTML = "";

  if (!state.watchDirs.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "LoRA管理で登録フォルダを追加してください";
    elements.folderSelect.append(option);
    elements.folderSelect.disabled = true;
    if (state.pageData?.download_candidates.length) {
      elements.downloadNote.textContent = "LoRA管理に watch フォルダがまだありません。先にフォルダ登録をしてください。";
    }
    return;
  }

  const autoOption = document.createElement("option");
  autoOption.value = "__auto__";
  autoOption.textContent = buildAutoTargetLabel();
  autoOption.selected = currentValue === "__auto__" || !state.watchDirs.includes(currentValue);
  elements.folderSelect.append(autoOption);

  state.watchDirs.forEach((path) => {
    const option = document.createElement("option");
    option.value = path;
    option.textContent = truncateText(path, 88);
    if (path === currentValue) {
      option.selected = true;
    }
    elements.folderSelect.append(option);
  });
  elements.folderSelect.disabled = false;
  updateDownloadNote();
}

function renderLastJob() {
  const job = state.lastJob;
  if (job?.status === "cancelled") {
    elements.jobLine.textContent = job.message || "取り込み待機を解除しました。";
    elements.jobLine.classList.remove("error");
    return;
  }
  if (!job) {
    elements.jobLine.textContent = "自動ダウンロードはまだ実行していません。";
    elements.jobLine.classList.remove("error");
    return;
  }

  if (job.status === "error") {
    elements.jobLine.textContent = `自動反映エラー: ${job.message || "失敗しました。"}`;
    elements.jobLine.classList.add("error");
    return;
  }

  elements.jobLine.classList.remove("error");

  if (job.status === "complete") {
    const targetText = job.importedPath ? ` -> ${job.importedPath}` : "";
    elements.jobLine.textContent = `最後の自動反映: ${job.fileName || "LoRA"}${targetText}`;
    return;
  }

  const fileText = job.fileName ? ` (${job.fileName})` : "";
  elements.jobLine.textContent = `進行中: ${job.message || "処理中です。"}${fileText}`;
}

function getSelectedDownloadCandidate() {
  const selectedUrl = String(elements.downloadSelect.value || "").trim();
  if (!selectedUrl) {
    return null;
  }
  return state.pageData?.download_candidates.find((candidate) => candidate.url === selectedUrl) || null;
}

function isCivitaiSourcePageData(pageData = state.pageData) {
  const sourceUrl = String(pageData?.source_url || "").trim();
  const sourceHost = String(pageData?.source_host || "").trim();
  if (/(?:^|\.)civitai\.(?:com|red)$/i.test(sourceHost)) {
    return true;
  }
  if (!sourceUrl) {
    return false;
  }
  try {
    return /(?:^|\.)civitai\.(?:com|red)$/i.test(new URL(sourceUrl).hostname);
  } catch (_error) {
    return false;
  }
}

function canStartPendingImportWithoutCandidate(pageData = state.pageData) {
  return false;
}

function getDownloadActionProblem() {
  if (inferModelFamilyFromCurrentContext() === "workflow") {
    if (!state.watchDirs.some(path => inferWatchDirModelFamily(path) === "workflow")) {
      return "ComfyUI の workflows フォルダをLoRA管理に登録してください。";
    }
    if (!/\.(json|zip)$/i.test(getSelectedDownloadCandidate()?.suggested_filename || "")) {
      return "Workflow の JSON / ZIP のファイル名が分かる候補を選んでください。";
    }
  }
  if (!state.pageData) {
    return "ページ情報がまだ取れていません。`再取得` を押してください。";
  }
  if (!state.watchDirs.length) {
    return "LoRA管理側の登録フォルダが見つかりません。LoRA管理を開いて登録フォルダを確認してください。";
  }
  if (!(getSelectedDownloadCandidate() || canStartPendingImportWithoutCandidate())) {
    return "ダウンロード候補がまだ選べていません。ページを再取得して候補を確認してください。";
  }
  return "";
}

function updateActionState() {
  elements.downloadImport.textContent = state.waiting ? "待機中" : isCivitaiSourcePageData() ? "取り込み待機を開始（DLはCivitaiで）" : "ダウンロードして反映";
  const hasPageData = Boolean(state.pageData);
  const downloadProblem = getDownloadActionProblem();

  elements.recoverDownloads.disabled = state.busy || state.recoveryRunning;
  elements.recoverDownloads.textContent = state.recoveryRunning ? "調査・登録中…" : "ダウンロードフォルダーを調査・登録";
  elements.openApp.disabled = state.busy;
  elements.sendImport.disabled = state.busy || !hasPageData;
  elements.downloadImport.disabled = state.busy || state.waiting || !state.pendingLoaded;
  elements.cancelPending.hidden = !state.waiting;
  elements.cancelPending.disabled = state.busy || !state.pending;
  elements.downloadImport.title = downloadProblem || "";
  elements.refreshPage.disabled = state.busy;
}

function setBusyState(isBusy) {
  state.busy = Boolean(isBusy);
  updateActionState();
}

function buildLaunchProtocolUrl(baseUrl) {
  return `${LAUNCH_PROTOCOL}://start?base_url=${encodeURIComponent(normalizeBaseUrl(baseUrl))}`;
}

function triggerLocalAppLaunch(baseUrl) {
  const link = document.createElement("a");
  link.href = buildLaunchProtocolUrl(baseUrl);
  link.rel = "noreferrer";
  document.body.append(link);
  link.click();
  link.remove();
}

function canReachLocalAppImmediately(baseUrl) {
  try {
    const request = new XMLHttpRequest();
    request.open("GET", `${baseUrl}/health`, false);
    request.send(null);
    return request.status >= 200 && request.status < 300 && String(request.responseText || "").trim().toLowerCase() === "ok";
  } catch (_error) {
    return false;
  }
}

async function canReachLocalApp(baseUrl) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), APP_HEALTHCHECK_TIMEOUT_MS);
  try {
    const response = await fetch(`${baseUrl}/health`, {
      method: "GET",
      signal: controller.signal,
    });
    if (!response.ok) {
      return false;
    }
    const text = await response.text();
    return text.trim().toLowerCase() === "ok";
  } catch (_error) {
    return false;
  } finally {
    window.clearTimeout(timer);
  }
}

async function waitForLocalApp(baseUrl) {
  const deadline = Date.now() + APP_LAUNCH_WAIT_MS;
  while (Date.now() < deadline) {
    if (await canReachLocalApp(baseUrl)) {
      return true;
    }
    await sleep(APP_LAUNCH_POLL_MS);
  }
  return false;
}

async function fetchLocalJson(baseUrl, path, options = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    method: options.method || "GET",
    headers: {
      Accept: "application/json",
      ...(options.body !== undefined ? { "Content-Type": "application/json; charset=utf-8" } : {}),
    },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (_error) {
      payload = { error: text };
    }
  }

  if (!response.ok) {
    throw new Error(payload.error || "通信に失敗しました。");
  }

  return payload;
}

function setStatus(message, isError = false) {
  elements.statusLine.textContent = message;
  elements.statusLine.classList.toggle("error", Boolean(isError));
}

function normalizeStringList(values) {
  const rawValues = Array.isArray(values) ? values : String(values || "").split(/[\n,;|/]+/);
  const seen = new Set();
  const normalized = [];

  rawValues.forEach((value) => {
    const text = String(value || "").trim();
    if (!text) {
      return;
    }
    const key = text.toLowerCase();
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    normalized.push(text);
  });

  return normalized;
}

function sleep(milliseconds) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}

function truncateText(text, maxLength) {
  const value = String(text || "").trim();
  if (value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, Math.max(0, maxLength - 1))}…`;
}

