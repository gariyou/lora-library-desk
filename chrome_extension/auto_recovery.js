// Download completion, not the single page-wait slot, drives automatic recovery.
// Persist before contacting the app so MV3 suspension/offline periods are safe.
const AUTO_RECOVERY_KEY = "loraManagerAutomaticRecovery";
const AUTO_RECOVERY_LOOKBACK_MS = 24 * 60 * 60 * 1000;
let automaticRecoveryChain = Promise.resolve();

function automaticDownloadKey(item) {
  return JSON.stringify([item.id, item.startTime, item.filename]);
}

function isAutomaticRecoveryDownload(item) {
  if (item.state !== "complete" || item.exists === false || !MODEL_FILE_PATTERN.test(item.filename || "")) return false;
  return [item.url, item.finalUrl, item.referrer].some(value => {
    try {
      const url = new URL(value);
      return url.protocol === "https:" && isCivitaiHost(url.hostname);
    } catch (_) { return false; }
  });
}

function automaticRecoveryPath(value) {
  return String(value || "").replace(/\\/g, "/").toLowerCase();
}

function finishAutomaticRecovery(state, job) {
  if (!state.active || job.status === "running") return;
  if (job.id === state.active.id && job.status === "complete") {
    const results = new Map((job.results || []).map(result => [automaticRecoveryPath(result.source_path), result]));
    for (const key of state.active.keys) {
      const entry = state.queue[key];
      if (!entry) continue;
      const result = results.get(automaticRecoveryPath(entry.item.filename));
      // No result means the source was moved/removed before the job began.
      if (!result || result.status === "imported") {
        state.finished[key] = Date.now();
        delete state.queue[key];
      } else {
        // Keep failures, but avoid hashing a large unidentified file every minute.
        entry.attempts = (entry.attempts || 0) + 1;
        entry.retryAt = Date.now() + Math.min(60, 2 ** Math.min(entry.attempts - 1, 6)) * 60000;
      }
    }
  }
  // Server restart or manual job replacement: retry without overwriting files.
  state.active = null;
}

function scheduleAutomaticRecovery() {
  automaticRecoveryChain = automaticRecoveryChain.then(runAutomaticRecovery).catch(error => {
    console.warn("Automatic download recovery will retry", toErrorMessage(error));
  });
  return automaticRecoveryChain;
}

async function runAutomaticRecovery() {
  const stored = (await chrome.storage.local.get(AUTO_RECOVERY_KEY))[AUTO_RECOVERY_KEY];
  const state = stored || {queue: {}, finished: {}, active: null};
  const save = () => chrome.storage.local.set({[AUTO_RECOVERY_KEY]: state});
  const now = Date.now();
  for (const [key, at] of Object.entries(state.finished)) {
    if (now - at > AUTO_RECOVERY_LOOKBACK_MS * 2) delete state.finished[key];
  }
  // Use end time: a multi-GB model may have started more than a day ago.
  const recent = await chrome.downloads.search({state: "complete", exists: true,
    endedAfter: new Date(now - AUTO_RECOVERY_LOOKBACK_MS).toISOString(), orderBy: ["endTime"], limit: 0});
  for (const item of recent) {
    if (!isAutomaticRecoveryDownload(item)) continue;
    const key = automaticDownloadKey(item);
    if (!state.finished[key] && !state.queue[key]) state.queue[key] = {item, attempts: 0, retryAt: 0};
  }
  await save();
  if (!state.active && !Object.keys(state.queue).length) return;
  const baseUrl = await loadStoredBaseUrl();
  // Honor the existing selected target/metadata before attempting recovery.
  await reconcileCompletedDownloads(baseUrl);
  let job = await getJson(`${baseUrl}/api/lora/recover-downloads`);
  finishAutomaticRecovery(state, job);
  await save();
  if (job.status === "running" || activeJobId) return;

  const ready = [];
  for (const [key, entry] of Object.entries(state.queue)) {
    if (entry.retryAt > now) continue;
    const [current] = await chrome.downloads.search({id: entry.item.id});
    if (!current || automaticDownloadKey(current) !== key || !isAutomaticRecoveryDownload(current)) {
      state.finished[key] = now;
      delete state.queue[key];
      continue;
    }
    entry.item = current;
    ready.push(key);
    if (ready.length === 100) break;
  }
  await save();
  if (!ready.length) return;
  const downloads = ready.map(key => {
    const item = state.queue[key].item;
    return {source_path: item.filename, download_state: item.state, referrer: item.referrer || "",
      download_urls: [item.url, item.finalUrl].filter(Boolean)};
  });
  job = await postJson(`${baseUrl}/api/lora/recover-downloads`, {downloads, metadata: {}, scan_downloads_dir: false});
  if (job.already_running) return;
  state.active = {id: job.id, keys: ready};
  await save();
  // Long hashes continue in the app; the one-minute alarm resumes the queue.
  for (let poll = 0; job.status === "running" && poll < 8; poll++) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    job = await getJson(`${baseUrl}/api/lora/recover-downloads`);
  }
  finishAutomaticRecovery(state, job);
  await save();
}

chrome.downloads.onChanged.addListener(delta => {
  if (delta.state?.current === "complete") void scheduleAutomaticRecovery();
});
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === RECONCILE_ALARM) void scheduleAutomaticRecovery();
});
void scheduleAutomaticRecovery();
