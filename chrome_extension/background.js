importScripts("page_metadata.js");

const DEFAULT_BASE_URL = "http://127.0.0.1:8787";
const JOB_KEY = "loraManagerBridgeJob";
const MODEL_FILE_PATTERN = /\.(safetensors|ckpt|pt|pth|bin|json|zip)$/i;

let activeJobId = "";

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || typeof message !== "object") {
    return false;
  }

  if (message.type === "auto-wait-page") {
    autoWaitForPage(message, _sender)
      .then(sendResponse).catch(() => sendResponse({handled: false}));
    return true;
  }

  if (message.type === "recover-downloads") {
    runDownloadRecovery(message)
      .then(result => sendResponse({ok: true, result}))
      .catch(error => sendResponse({ok: false, error: toErrorMessage(error)}));
    return true;
  }

  if (message.type === "download-and-import") {
    runDownloadAndImport(message)
      .then((result) => sendResponse({ ok: true, result }))
      .catch((error) => sendResponse({ ok: false, error: toErrorMessage(error) }));
    return true;
  }

  if (message.type === "send-browser-import") {
    runSendBrowserImport(message)
      .then((result) => sendResponse({ ok: true, result }))
      .catch((error) => sendResponse({ ok: false, error: toErrorMessage(error) }));
    return true;
  }

  return false;
});

// Serialize automatic requests across tabs; the server also guards pending state.
let autoWaitBusy = false;
async function autoWaitForPage(message, sender) {
  if (autoWaitBusy || activeJobId || !sender.tab?.id || sender.frameId !== 0) return {handled: false};
  const url = String(message.url || '');
  if (!/^https:\/\/(?:www\.)?civitai\.(?:com|red)\/models\/\d+(?:[/?#]|$)/i.test(url) || sender.url !== url) return {handled: false};
  autoWaitBusy = true;
  try {
    const baseUrl = await loadStoredBaseUrl();
    const visitKey = `autoWait:${sender.tab.id}`;
    const visit = JSON.stringify([baseUrl, url]);
    const saved = await chrome.storage.session.get(visitKey);
    if (saved[visitKey] === visit) return {handled: true};
    const state = await getJson(`${baseUrl}/api/lora/pending-download`);
    if (state.pending) {
      // Never queue another tab to take over when this download completes.
      await chrome.storage.session.set({[visitKey]: visit});
      return {handled: true};
    }
    const [extracted] = await chrome.scripting.executeScript({target: {tabId: sender.tab.id}, func: extractPageMetadata});
    const pageData = extracted?.result;
    const tab = await chrome.tabs.get(sender.tab.id);
    if (!tab.active || tab.url !== url || pageData?.source_url !== url) return {handled: false};
    const candidates = (pageData.download_candidates || []).filter(c => {
      try { const u = new URL(c.url); return u.protocol === 'https:' && !isCivitaiModelPageUrl(c.url) &&
        (MODEL_FILE_PATTERN.test(c.suggested_filename || '') || /^\/api\/download\/models\/\d+/.test(u.pathname)); }
      catch (_) { return false; }
    });
    const candidate = candidates[0];
    if (!candidate) return {handled: false};
    const result = await runDownloadAndImport({baseUrl, pageData, tabId: sender.tab.id,
      downloadUrl: candidate.url, selectedCandidate: candidate, autoTriggered: true});
    await chrome.storage.session.set({[visitKey]: visit});
    return {handled: true, result};
  } finally { autoWaitBusy = false; }
}

// Registered at worker startup so completion wakes a suspended MV3 worker.
chrome.downloads.onChanged.addListener((delta) => {
  if (["complete", "interrupted"].includes(delta.state?.current)) {
    void reconcileCompletedDownloads("", null, delta.id).catch(error => console.warn("Download reconciliation failed", error));
  }
});
void reconcileCompletedDownloads().catch(() => {});
const RECONCILE_ALARM = "lora-manager-pending-download";
chrome.alarms?.onAlarm.addListener(alarm => {
  if (alarm.name === RECONCILE_ALARM) void reconcileCompletedDownloads().catch(error => console.warn("Download retry failed", error));
});
void chrome.alarms?.create(RECONCILE_ALARM, {periodInMinutes: 1});

async function reconcileCompletedDownloads(baseUrl = "", armedPayload = null, downloadId = null) {
  baseUrl = baseUrl || await loadStoredBaseUrl();
  const state = armedPayload || await getJson(`${baseUrl}/api/lora/pending-download`);
  const pending = state?.pending;
  if (!pending) return null;
  // Automatic waits accept only downloads started after arming.
  // Manual waits retain recovery of the same asset from recent history.
  const armedAt = Number(pending.created_epoch || Date.now() / 1000) * 1000;
  const since = pending.auto_triggered ? armedAt : Math.min(Date.now() - 24 * 60 * 60 * 1000, armedAt - 2000);
  const items = await chrome.downloads.search(downloadId !== null ? {id: downloadId} : {
    state: "complete", exists: true, startedAfter: new Date(since).toISOString(),
    orderBy: ["-startTime"], limit: 0,
  });
  const eligible = pending.auto_triggered ? items.filter(item => Date.parse(item.startTime) >= armedAt) : items;
  const downloads = eligible.filter(item =>
    (item.state === "complete" && item.exists !== false && MODEL_FILE_PATTERN.test(item.filename || "")) ||
    (downloadId !== null && item.state === "interrupted"))
    .map(item => ({source_path: item.filename, download_state: item.state,
      download_urls: [item.url, item.finalUrl].filter(Boolean)}));
  let result = {matched: false};
  for (let offset = 0; offset < downloads.length; offset += 100) {
    result = await postJson(`${baseUrl}/api/lora/complete-pending-download`, {
      created_epoch: pending.created_epoch, downloads: downloads.slice(offset, offset + 100),
    });
    if (result.matched) break;
  }
  if (!result.matched) return null;
  const failed = result.status === "error";
  await setJob({id: createJobId(), status: failed ? "error" : "complete", message: failed ? result.message : `${result.filename} を保存して反映しました。`,
    pageTitle: pending.metadata?.title || "", sourceUrl: pending.source_url,
    targetDir: result.target_dir, fileName: result.filename, importedPath: result.path});
  return result;
}

async function runDownloadRecovery(request) {
  if (activeJobId) throw new Error("別の取り込み処理が動いています。完了後に再実行してください。");
  const jobId = createJobId();
  activeJobId = jobId;
  try {
    const baseUrl = normalizeBaseUrl(request.baseUrl);
    const items = await chrome.downloads.search({exists: true, orderBy: ["-startTime"], limit: 0});
    const downloads = items.filter(item => MODEL_FILE_PATTERN.test(item.filename || ""))
      .map(item => ({source_path: item.filename, download_state: item.state,
        referrer: item.referrer || "", download_urls: [item.url, item.finalUrl].filter(Boolean)}));
    if (downloads.length > 5000) throw new Error("ダウンロード履歴の対象が5000件を超えています。履歴を整理して再実行してください。");
    return await postJson(`${baseUrl}/api/lora/recover-downloads`, {downloads, metadata: request.metadata || {}});
  } finally {
    if (activeJobId === jobId) activeJobId = "";
  }
}

async function runDownloadAndImport(request) {
  if (activeJobId) {
    throw new Error("別のダウンロード処理が動いています。完了してからもう一度試してください。");
  }

  const jobId = createJobId();
  activeJobId = jobId;

  const baseUrl = normalizeBaseUrl(request.baseUrl);
  const pageData = normalizePageData(request.pageData);
  const targetDir = String(request.targetDir || "").trim();
  const activeTabId = Number(request.tabId || 0);
  const downloadUrl = normalizeDownloadUrl(request.downloadUrl, pageData.source_url);
  const selectedCandidate = request.selectedCandidate || {};
  const suggestedFilename = normalizeSuggestedFilename(selectedCandidate.suggested_filename || selectedCandidate.filename);
  const isCivitaiDownload = isCivitaiDownloadSource(downloadUrl, pageData.source_url, pageData.source_host);

  if (!downloadUrl) {
    throw new Error("ダウンロードURLが見つかりません。");
  }

  try {
    await setJob({
      id: jobId,
      status: "starting",
      message: "ダウンロードを開始しています。",
      pageTitle: pageData.title,
      sourceUrl: pageData.source_url,
      targetDir: targetDir || "__auto__",
      downloadUrl,
    });

    let payload;
    let filename = suggestedFilename || "";
    let downloadId = 0;

    if (isCivitaiDownload) {
      if (isCivitaiModelPageUrl(downloadUrl)) {
        throw new Error("取得URLが確定していません。ページ情報を再取得してダウンロード候補を選んでください。");
      }
      return await armPendingDownloadImport({
        jobId,
        baseUrl,
        downloadUrl,
        targetDir,
        pageData,
        suggestedFilename,
        autoTriggered: request.autoTriggered === true,
      });
    } else {
      try {
        const browserResult = await downloadWithBrowserAndImport({
          jobId,
          baseUrl,
          downloadUrl,
          targetDir,
          pageData,
          suggestedFilename,
        });
        payload = browserResult.payload;
        filename = browserResult.filename;
        downloadId = browserResult.downloadId;
      } catch (error) {
        if (!shouldFallbackToRemoteCivitaiImport(error, { downloadUrl, pageData })) {
          throw error;
        }

        payload = await importRemoteModelFromCivitai({
          jobId,
          baseUrl,
          activeTabId,
          downloadUrl,
          targetDir,
          pageData,
          suggestedFilename,
          filename,
          message: "Chrome ダウンロードが失敗したため、Civitai から直接取得し直しています。",
        });
        filename = String(payload.filename || filename || "");
        downloadId = 0;
      }
    }

    const result = {
      path: String(payload.path || ""),
      filename: String(payload.filename || filename || ""),
      target_dir: String(payload.target_dir || targetDir),
      metadata: payload.metadata || {},
    };

    await setJob({
      id: jobId,
      status: "complete",
      message: `${result.filename || filename || "LoRA"} を保存して反映しました。`,
      pageTitle: pageData.title,
      sourceUrl: pageData.source_url,
      targetDir: result.target_dir,
      downloadUrl,
      downloadId,
      fileName: result.filename || filename,
      importedPath: result.path,
    });

    return result;
  } catch (error) {
    const errorMessage = normalizeDownloadError(error, { isCivitai: isCivitaiDownload });
    await setJob({
      id: jobId,
      status: "error",
      message: errorMessage,
      pageTitle: pageData.title,
      sourceUrl: pageData.source_url,
      targetDir: targetDir || "__auto__",
      downloadUrl,
    });
    throw new Error(errorMessage);
  } finally {
    if (activeJobId === jobId) {
      activeJobId = "";
    }
  }
}

async function startBrowserDownload(downloadUrl, suggestedFilename = "") {
  const downloadOptions = {
    url: downloadUrl,
    saveAs: false,
    conflictAction: "uniquify",
  };
  if (suggestedFilename) {
    downloadOptions.filename = suggestedFilename;
  }
  return chrome.downloads.download(downloadOptions);
}

async function armPendingDownloadImport({
  jobId,
  baseUrl,
  downloadUrl,
  targetDir,
  pageData,
  suggestedFilename = "",
  autoTriggered = false,
}) {
  const metadata = {
    ...pageData,
    download_url: downloadUrl,
  };
  const payload = await postJson(`${baseUrl}/api/lora/await-download-import`, {
    target_dir: targetDir,
    metadata,
    expected_filename: suggestedFilename,
    auto_triggered: autoTriggered,
  });

  const waitingMessage = suggestedFilename
    ? `${suggestedFilename} の待機を開始しました。Civitai 側でダウンロードすると、完了後に自動で反映します。`
    : "選択した取得URLの待機を開始しました。Civitai 側でダウンロードしてください。";

  await setJob({
    id: jobId,
    status: "waiting",
    message: waitingMessage,
    pageTitle: pageData.title,
    sourceUrl: pageData.source_url,
    targetDir: targetDir || "__auto__",
    downloadUrl,
    fileName: suggestedFilename,
    downloadsDir: payload?.pending?.downloads_dir || payload?.last_result?.downloads_dir || "",
  });

  try {
    const completed = await reconcileCompletedDownloads(baseUrl, payload);
    if (completed) return completed;
  } catch (error) {
    console.warn("Completed download lookup failed; pending watch remains active.", error);
  }

  return {
    pending: true,
    message: waitingMessage,
    downloads_dir: payload?.pending?.downloads_dir || payload?.last_result?.downloads_dir || "",
  };
}

async function triggerTabDownloadAndImport({
  jobId,
  tabId,
  baseUrl,
  downloadUrl,
  targetDir,
  pageData,
  suggestedFilename = "",
  startMessage = "ページからダウンロードしています。",
}) {
  const expectedFilename = normalizeSuggestedFilename(suggestedFilename);
  const downloadItem = await triggerTabDownloadAndWait({
    tabId,
    downloadUrl,
    expectedFilename,
    startMessage,
    jobId,
    pageData,
    targetDir,
  });
  const filename = fileNameFromPath(downloadItem.filename);
  await setJob({
    id: jobId,
    status: "importing",
    message: "LoRA管理へ取り込んでいます。",
    pageTitle: pageData.title,
    sourceUrl: pageData.source_url,
    targetDir: targetDir || "__auto__",
    downloadUrl,
    downloadId: downloadItem.id,
    fileName: filename,
    sourcePath: downloadItem.filename,
  });

  const payload = await postJson(`${baseUrl}/api/lora/import-download`, {
    source_path: downloadItem.filename,
    target_dir: targetDir,
    metadata: pageData,
  });

  return {
    payload,
    filename,
    downloadId: downloadItem.id,
  };
}

async function triggerTabDownloadAndWait({
  tabId,
  downloadUrl,
  expectedFilename = "",
  startMessage,
  jobId,
  pageData,
  targetDir,
  timeoutMs = 30 * 60 * 1000,
}) {
  const startedAt = Date.now();
  let matchedDownloadId = 0;

  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timer);
      clearInterval(pollTimer);
      chrome.downloads.onCreated.removeListener(handleCreated);
      chrome.downloads.onChanged.removeListener(handleChanged);
    };

    const rejectWith = (error) => {
      cleanup();
      reject(error instanceof Error ? error : new Error(String(error || "ダウンロードに失敗しました。")));
    };

    const resolveWith = (item) => {
      cleanup();
      resolve(item);
    };

    const matchesItem = (item) => {
      if (!item) {
        return false;
      }

      const startedMs = Date.parse(item.startTime || "");
      if (Number.isFinite(startedMs) && startedMs + 1500 < startedAt) {
        return false;
      }

      const itemFilename = fileNameFromPath(item.filename || "");
      if (expectedFilename && itemFilename && itemFilename.toLowerCase() === expectedFilename.toLowerCase()) {
        return true;
      }

      const itemUrl = normalizeDownloadUrl(item.finalUrl || item.url || "", downloadUrl) || String(item.finalUrl || item.url || "");
      if (itemUrl && urlsLookRelated(itemUrl, downloadUrl)) {
        return true;
      }

      return isCivitaiOrigin(itemUrl || downloadUrl) && MODEL_FILE_PATTERN.test(itemFilename);
    };

    const inspectItem = async (id) => {
      const items = await chrome.downloads.search({ id });
      const item = items[0];
      if (!matchesItem(item)) {
        return false;
      }

      matchedDownloadId = item.id;
      if (item.state === "complete" && item.filename) {
        resolveWith(item);
        return true;
      }
      if (item.state === "interrupted") {
        rejectWith(new Error(item.error || "ダウンロードが中断されました。"));
        return true;
      }
      return false;
    };

    const handleCreated = async (item) => {
      try {
        if (!matchesItem(item)) {
          return;
        }
        matchedDownloadId = item.id;
        if (item.state === "complete" && item.filename) {
          resolveWith(item);
        }
      } catch (_error) {
        // keep waiting
      }
    };

    const handleChanged = async (delta) => {
      try {
        if (matchedDownloadId) {
          if (delta.id !== matchedDownloadId) {
            return;
          }
          if (!delta.state && !delta.filename) {
            return;
          }
          await inspectItem(delta.id);
          return;
        }
        await inspectItem(delta.id);
      } catch (_error) {
        // keep waiting
      }
    };

    const timer = setTimeout(() => {
      rejectWith(new Error("Civitai のダウンロード完了を待っている間にタイムアウトしました。"));
    }, timeoutMs);

    const pollTimer = setInterval(async () => {
      try {
        const items = await chrome.downloads.search({});
        const candidate = items
          .filter((item) => matchesItem(item))
          .sort((left, right) => Date.parse(right.startTime || "") - Date.parse(left.startTime || ""))[0];
        if (!candidate) {
          return;
        }
        await inspectItem(candidate.id);
      } catch (_error) {
        // keep waiting
      }
    }, 1000);

    chrome.downloads.onCreated.addListener(handleCreated);
    chrome.downloads.onChanged.addListener(handleChanged);

    (async () => {
      await setJob({
        id: jobId,
        status: "downloading",
        message: startMessage,
        pageTitle: pageData.title,
        sourceUrl: pageData.source_url,
        targetDir: targetDir || "__auto__",
        downloadUrl,
      });

      const result = await triggerTabDownload(tabId, downloadUrl);
      if (!result?.clicked) {
        rejectWith(new Error("Civitai ページからダウンロードを開始できませんでした。"));
      }
    })().catch(rejectWith);
  });
}

async function triggerTabDownload(tabId, downloadUrl) {
  if (!(Number.isFinite(tabId) && tabId > 0)) {
    return { clicked: false };
  }

  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      func: (url) => {
        try {
          const anchor = document.createElement("a");
          anchor.href = url;
          anchor.target = "_self";
          anchor.rel = "noopener noreferrer";
          anchor.style.display = "none";
          document.body.appendChild(anchor);
          anchor.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
          window.setTimeout(() => anchor.remove(), 0);
          return { clicked: true };
        } catch (error) {
          return { clicked: false, error: String(error || "") };
        }
      },
      args: [downloadUrl],
    });
    return result?.result || { clicked: false };
  } catch (_error) {
    return { clicked: false };
  }
}

async function downloadWithBrowserAndImport({
  jobId,
  baseUrl,
  downloadUrl,
  targetDir,
  pageData,
  suggestedFilename = "",
  startMessage = "Chrome でダウンロードしています。",
}) {
  const downloadId = await startBrowserDownload(downloadUrl, suggestedFilename);
  await setJob({
    id: jobId,
    status: "downloading",
    message: startMessage,
    pageTitle: pageData.title,
    sourceUrl: pageData.source_url,
    targetDir: targetDir || "__auto__",
    downloadUrl,
    downloadId,
  });

  const downloadItem = await waitForDownloadCompletion(downloadId);
  const filename = fileNameFromPath(downloadItem.filename);
  await setJob({
    id: jobId,
    status: "importing",
    message: "LoRA管理へ取り込んでいます。",
    pageTitle: pageData.title,
    sourceUrl: pageData.source_url,
    targetDir: targetDir || "__auto__",
    downloadUrl,
    downloadId,
    fileName: filename,
    sourcePath: downloadItem.filename,
  });

  const payload = await postJson(`${baseUrl}/api/lora/import-download`, {
    source_path: downloadItem.filename,
    target_dir: targetDir,
    metadata: pageData,
  });

  return {
    payload,
    filename,
    downloadId,
  };
}

async function importRemoteModelFromCivitai({
  jobId,
  baseUrl,
  activeTabId,
  downloadUrl,
  targetDir,
  pageData,
  suggestedFilename,
  filename = "",
  message = "Civitai から取得して LoRA管理へ取り込んでいます。",
}) {
  const resolved = await resolveAuthenticatedDownloadUrl(activeTabId, downloadUrl, suggestedFilename);
  const resolvedDownloadUrl = normalizeDownloadUrl(resolved.downloadUrl, downloadUrl) || downloadUrl;
  const resolvedFilename = normalizeSuggestedFilename(resolved.suggestedFilename || suggestedFilename) || suggestedFilename;

  await setJob({
    id: jobId,
    status: "importing",
    message,
    pageTitle: pageData.title,
    sourceUrl: pageData.source_url,
    targetDir: targetDir || "__auto__",
    downloadUrl: resolvedDownloadUrl,
    fileName: resolvedFilename || filename,
  });

  return postJson(`${baseUrl}/api/lora/import-download-remote`, {
    download_url: resolvedDownloadUrl,
    target_dir: targetDir,
    metadata: pageData,
    suggested_filename: resolvedFilename,
    cookie_header: await buildCookieHeaderForUrls([
      resolvedDownloadUrl,
      downloadUrl,
      pageData.source_url,
      buildUrlFromHost(pageData.source_host),
    ]),
    referer_url: pageData.source_url || downloadUrl,
  });
}

async function buildCookieHeaderForUrls(urls) {
  const cookieMap = new Map();

  for (const url of Array.isArray(urls) ? urls : [urls]) {
    const normalizedUrl = normalizeDownloadUrl(url);
    if (!normalizedUrl) {
      continue;
    }

    const { hostname } = new URL(normalizedUrl);
    const domain = hostname.replace(/^www\./i, "");
    const [cookiesByUrl, cookiesByDomain] = await Promise.all([
      chrome.cookies.getAll({ url: normalizedUrl }).catch(() => []),
      chrome.cookies.getAll({ domain }).catch(() => []),
    ]);
    [...cookiesByUrl, ...cookiesByDomain]
      .filter((entry) => entry && entry.name)
      .forEach((entry) => {
        cookieMap.set(entry.name, `${entry.name}=${entry.value}`);
      });
  }

  return Array.from(cookieMap.values()).join("; ");
}

async function resolveAuthenticatedDownloadUrl(tabId, downloadUrl, suggestedFilename = "") {
  if (!(Number.isFinite(tabId) && tabId > 0)) {
    return { downloadUrl, suggestedFilename };
  }

  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      func: (url, fileNameHint) =>
        new Promise((resolve) => {
          const xhr = new XMLHttpRequest();
          let settled = false;

          const finish = (payload) => {
            if (settled) {
              return;
            }
            settled = true;
            resolve(payload);
          };

          const parseFilename = (contentDisposition) => {
            const text = String(contentDisposition || "");
            const utf8 = text.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
            if (utf8) {
              try {
                return decodeURIComponent(utf8[1].trim().replace(/^\"|\"$/g, ""));
              } catch (_error) {
                return utf8[1].trim().replace(/^\"|\"$/g, "");
              }
            }
            const quoted = text.match(/filename\s*=\s*\"([^\"]+)\"/i);
            if (quoted) {
              return quoted[1].trim();
            }
            const plain = text.match(/filename\s*=\s*([^;]+)/i);
            if (plain) {
              return plain[1].trim().replace(/^\"|\"$/g, "");
            }
            return "";
          };

          xhr.open("HEAD", url, true);
          xhr.withCredentials = true;

          xhr.onreadystatechange = () => {
            if (xhr.readyState !== XMLHttpRequest.HEADERS_RECEIVED) {
              return;
            }

            const responseUrl = String(xhr.responseURL || url || "");
            const contentDisposition = xhr.getResponseHeader("Content-Disposition") || "";
            const parsedFilename = parseFilename(contentDisposition) || fileNameHint || "";
            try {
              xhr.abort();
            } catch (_error) {
              // ignore
            }

            finish({
              ok: xhr.status >= 200 && xhr.status < 400,
              status: xhr.status,
              downloadUrl: responseUrl,
              suggestedFilename: parsedFilename,
            });
          };

          xhr.onerror = () => finish({ ok: false, status: xhr.status || 0, downloadUrl: url, suggestedFilename: fileNameHint || "" });
          xhr.onabort = () => {
            if (!settled) {
              finish({
                ok: xhr.status >= 200 && xhr.status < 400,
                status: xhr.status,
                downloadUrl: String(xhr.responseURL || url || ""),
                suggestedFilename: fileNameHint || "",
              });
            }
          };

          try {
            xhr.send();
          } catch (_error) {
            finish({ ok: false, status: 0, downloadUrl: url, suggestedFilename: fileNameHint || "" });
          }
        }),
      args: [downloadUrl, suggestedFilename],
    });

    const payload = result?.result;
    if (payload?.ok) {
      return {
        downloadUrl: normalizeDownloadUrl(payload.downloadUrl, downloadUrl) || downloadUrl,
        suggestedFilename: normalizeSuggestedFilename(payload.suggestedFilename || suggestedFilename),
      };
    }
  } catch (_error) {
    // fall through to original URL
  }

  return { downloadUrl, suggestedFilename };
}

async function runSendBrowserImport(request) {
  const baseUrl = normalizeBaseUrl(request.baseUrl);
  const pageData = normalizePageData(request.pageData);
  if (!pageData.source_url) {
    throw new Error("送信するページ情報が見つかりません。");
  }

  const payload = await postJson(`${baseUrl}/api/lora/browser-imports`, pageData);
  return {
    import_id: Number(payload.import_id || 0),
  };
}

async function waitForDownloadCompletion(downloadId, timeoutMs = 30 * 60 * 1000) {
  const initial = await chrome.downloads.search({ id: downloadId });
  if (initial[0]?.state === "complete" && initial[0]?.filename) {
    return initial[0];
  }

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error("ダウンロード完了を待っている間にタイムアウトしました。"));
    }, timeoutMs);

    const cleanup = () => {
      clearTimeout(timer);
      chrome.downloads.onChanged.removeListener(handleChanged);
    };

    const handleChanged = async (delta) => {
      if (delta.id !== downloadId || !delta.state) {
        return;
      }

      if (delta.state.current === "complete") {
        cleanup();
        try {
          const items = await chrome.downloads.search({ id: downloadId });
          const item = items[0];
          if (!item?.filename) {
            reject(new Error("ダウンロードファイルの保存先を取得できませんでした。"));
            return;
          }
          resolve(item);
        } catch (error) {
          reject(error);
        }
      }

      if (delta.state.current === "interrupted") {
        cleanup();
        const code = String(delta.error?.current || "").trim();
        if (code === "SERVER_UNAUTHORIZED") {
          reject(
            new Error(
              "Civitai 側で未認証扱いになりました。chrome://extensions で拡張を再読み込みしてから、Civitai にログイン済みのページで試してください。"
            )
          );
          return;
        }
        reject(new Error(code || "ダウンロードが中断されました。"));
      }
    };

    chrome.downloads.onChanged.addListener(handleChanged);
  });
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      Accept: "application/json",
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(120000),
  });

  const payload = await readJsonResponse(response);
  if (!response.ok) {
    throw new Error(payload.error || "通信に失敗しました。");
  }
  return payload;
}

async function getJson(url) {
  const response = await fetch(url, {
    method: "GET",
    signal: AbortSignal.timeout(5000),
    headers: {
      Accept: "application/json",
    },
  });

  const payload = await readJsonResponse(response);
  if (!response.ok) {
    throw new Error(payload.error || "通信に失敗しました。");
  }
  return payload;
}

async function loadStoredBaseUrl() {
  const stored = await chrome.storage.sync.get({ loraManagerBaseUrl: DEFAULT_BASE_URL });
  return normalizeBaseUrl(stored.loraManagerBaseUrl);
}

async function readJsonResponse(response) {
  const text = await response.text();
  if (!text) {
    return {};
  }

  try {
    return JSON.parse(text);
  } catch (_error) {
    return { error: text };
  }
}

async function setJob(job) {
  const nextJob = {
    ...(job || {}),
    updatedAt: new Date().toISOString(),
  };
  await chrome.storage.local.set({ [JOB_KEY]: nextJob });
  return nextJob;
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

function normalizeDownloadUrl(value, baseUrl = "") {
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

function isCivitaiOrigin(value) {
  const url = normalizeDownloadUrl(value);
  if (!url) {
    return false;
  }

  try {
    const parsed = new URL(url);
    return /(?:^|\.)civitai\.(?:com|red)$/i.test(parsed.hostname);
  } catch (_error) {
    return false;
  }
}

function isCivitaiHost(value) {
  const host = String(value || "").trim().replace(/^https?:\/\//i, "").replace(/\/.*$/, "");
  return /(?:^|\.)civitai\.(?:com|red)$/i.test(host);
}

function isCivitaiModelPageUrl(value) {
  const url = normalizeDownloadUrl(value);
  if (!url) {
    return false;
  }

  try {
    const parsed = new URL(url);
    return /(?:^|\.)civitai\.(?:com|red)$/i.test(parsed.hostname) && /^\/models\/\d+(?:\/|$)/i.test(parsed.pathname);
  } catch (_error) {
    return false;
  }
}

function isCivitaiDownloadSource(downloadUrl, sourceUrl = "", sourceHost = "") {
  if (isCivitaiOrigin(sourceUrl) || isCivitaiHost(sourceHost)) {
    return true;
  }
  return isCivitaiOrigin(downloadUrl);
}

function buildUrlFromHost(host) {
  const text = String(host || "").trim();
  return text ? `https://${text.replace(/^https?:\/\//i, "").replace(/\/+$/, "")}/` : "";
}

function urlsLookRelated(left, right) {
  const normalizedLeft = normalizeDownloadUrl(left);
  const normalizedRight = normalizeDownloadUrl(right);
  if (!(normalizedLeft && normalizedRight)) {
    return false;
  }

  try {
    const leftUrl = new URL(normalizedLeft);
    const rightUrl = new URL(normalizedRight);
    if (leftUrl.href === rightUrl.href) {
      return true;
    }
    if (leftUrl.pathname === rightUrl.pathname) {
      return true;
    }
    const leftTail = leftUrl.pathname.split("/").filter(Boolean).slice(-2).join("/");
    const rightTail = rightUrl.pathname.split("/").filter(Boolean).slice(-2).join("/");
    return Boolean(leftTail && rightTail && leftTail === rightTail);
  } catch (_error) {
    return false;
  }
}

function shouldFallbackToRemoteCivitaiImport(error, context = {}) {
  const rawMessage = toErrorMessage(error).trim();
  const message = rawMessage.toUpperCase();
  const isBrowserFetchFailure =
    message === "SERVER_FAILED" ||
    message === "SERVER_UNAUTHORIZED" ||
    /サイトでファイルを取得できませんでした/.test(rawMessage);
  if (!isBrowserFetchFailure) {
    return false;
  }
  if (isCivitaiDownloadSource(context.downloadUrl, context.pageData?.source_url, context.pageData?.source_host)) {
    return true;
  }
  return Boolean(context.pageData?.source_url);
}

function normalizeSuggestedFilename(value) {
  const text = fileNameFromPath(String(value || "").trim());
  return /\.(safetensors|ckpt|pt|pth|bin|json|zip)$/i.test(text) ? text : "";
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

function inferModelFamilyFromPageData(pageData) {
  const explicitHint = normalizeModelFamily(pageData?.model_family_hint);
  if (explicitHint) {
    return explicitHint;
  }

  const firstCandidate = Array.isArray(pageData?.download_candidates) ? pageData.download_candidates[0] : null;
  if (/\.(json|zip)$/i.test(firstCandidate?.suggested_filename || "")) { return "workflow"; }

  const hints = [
    firstCandidate?.suggested_filename,
    firstCandidate?.filename,
    firstCandidate?.label,
    firstCandidate?.url,
    pageData?.title,
    pageData?.description,
    pageData?.source_url,
    ...(Array.isArray(pageData?.tags) ? pageData.tags : []),
    ...(Array.isArray(pageData?.triggers) ? pageData.triggers : []),
  ];

  for (const hint of hints) {
    const family = normalizeModelFamily(hint);
    if (family) {
      return family;
    }
  }
  return "";
}

function fileNameFromPath(value) {
  return String(value || "").split(/[\\/]/).pop() || "";
}

function normalizePageData(pageData) {
  const payload = pageData && typeof pageData === "object" ? pageData : {};
  return {
    source_url: String(payload.source_url || "").trim(),
    source_host: String(payload.source_host || "").trim(),
    title: String(payload.title || "").trim(),
    description: String(payload.description || "").trim(),
    prompt: String(payload.prompt || "").trim(),
    negative_prompt: String(payload.negative_prompt || "").trim(),
    prompt_preview: String(payload.prompt_preview || "").trim(),
    author: String(payload.author || "").trim(),
    base_model: String(payload.base_model || "").trim(),
    steps: String(payload.steps || "").trim(),
    sampler: String(payload.sampler || "").trim(),
    cfg_scale: String(payload.cfg_scale || payload.cfgScale || payload.cfg || "").trim(),
    seed: String(payload.seed || "").trim(),
    model_family_hint: String(payload.model_family || payload.model_family_hint || "").trim(),
    preview_media_kind: String(payload.preview_media_kind || payload.media_kind || "").trim().toLowerCase(),
    preview_image_url: normalizeDownloadUrl(payload.preview_image_url, payload.source_url),
    preview_media_url: normalizeDownloadUrl(payload.preview_media_url || payload.preview_video_url || payload.preview_image_url, payload.source_url),
    preview_video_url: normalizeDownloadUrl(payload.preview_video_url, payload.source_url),
    triggers: normalizeStringList(payload.triggers),
    tags: normalizeStringList(payload.tags),
    resources_used: normalizeResourcesUsed(payload.resources_used || payload.resources),
  };
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

function normalizeResourcesUsed(values) {
  const normalized = [];
  const seen = new Set();
  const entries = Array.isArray(values) ? values : [];

  entries.forEach((value) => {
    if (!value || typeof value !== "object") {
      return;
    }

    const entry = {
      name: String(value.name || value.model_name || value.modelName || "").trim(),
      type: String(value.type || value.model_type || value.modelType || "").trim(),
      strength: String(value.strength ?? value.weight ?? "").trim(),
      base_model: String(value.base_model || value.baseModel || "").trim(),
      version_name: String(value.version_name || value.versionName || "").trim(),
      model_id: String(value.model_id || value.modelId || "").trim(),
      model_version_id: String(
        value.model_version_id || value.modelVersionId || value.version_id || value.versionId || ""
      ).trim(),
      url: normalizeDownloadUrl(value.url || value.source_url),
      trained_words: normalizeStringList(
        value.trained_words || value.trainedWords || value.trigger_words || value.triggerWords || []
      ),
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

function createJobId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function normalizeDownloadError(error, options = {}) {
  const message = toErrorMessage(error);
  if ((options.isCivitai || /civitai download was unauthorized/i.test(message)) && /civitai download was unauthorized/i.test(message)) {
    return "Civitai のログインが切れているか、ダウンロード権限が足りません。chrome://extensions で拡張を再読み込みしてから、Civitai にログイン済みのモデルページで試してください。";
  }
  return message;
}

function toErrorMessage(error) {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return String(error || "処理に失敗しました。");
}

importScripts("auto_recovery.js");
