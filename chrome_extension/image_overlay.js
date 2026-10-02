const DEFAULT_BASE_URL = "http://127.0.0.1:8787";
const ROOT_ID = "lora-manager-image-send-root";
const STYLE_ID = "lora-manager-image-send-style";
const BASE_URL_KEY = "loraManagerBaseUrl";

const overlayState = {
  busy: false,
  mountedUrl: "",
  statusTimer: 0,
};

function isCivitaiImagePage(urlText = window.location.href) {
  try {
    const url = new URL(urlText);
    return /^(?:www\.)?civitai\.(?:com|red)$/i.test(url.hostname) && /^\/images\/\d+(?:\/|$)/.test(url.pathname);
  } catch (_error) {
    return false;
  }
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

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) {
    return;
  }

  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    #${ROOT_ID} {
      position: fixed;
      top: 102px;
      right: 18px;
      z-index: 2147483647;
      display: grid;
      gap: 10px;
      pointer-events: none;
    }

    #${ROOT_ID}[hidden] {
      display: none !important;
    }

    #${ROOT_ID} .lmb-send-button,
    #${ROOT_ID} .lmb-send-status {
      pointer-events: auto;
    }

    #${ROOT_ID} .lmb-send-button {
      min-width: 132px;
      min-height: 92px;
      padding: 14px 16px 12px;
      border: 1px solid rgba(255, 255, 255, 0.26);
      border-radius: 24px;
      background: linear-gradient(180deg, rgba(255, 248, 241, 0.76), rgba(237, 225, 214, 0.56));
      backdrop-filter: blur(18px);
      box-shadow: 0 20px 44px rgba(0, 0, 0, 0.18);
      color: #231811;
      font-family: "Yu Gothic UI", "Hiragino Sans", sans-serif;
      cursor: pointer;
      display: grid;
      justify-items: center;
      gap: 6px;
      transition: transform 0.16s ease, box-shadow 0.16s ease, background 0.16s ease, opacity 0.16s ease;
    }

    #${ROOT_ID} .lmb-send-button:hover {
      transform: translateY(-1px);
      box-shadow: 0 24px 54px rgba(0, 0, 0, 0.22);
      background: linear-gradient(180deg, rgba(255, 252, 247, 0.84), rgba(242, 231, 221, 0.68));
    }

    #${ROOT_ID} .lmb-send-button:disabled {
      cursor: wait;
      opacity: 0.76;
      transform: none;
    }

    #${ROOT_ID} .lmb-send-label {
      font-size: 14px;
      line-height: 1.35;
      font-weight: 700;
      text-align: center;
      letter-spacing: 0.02em;
    }

    #${ROOT_ID} .lmb-send-arrow {
      font-size: 28px;
      line-height: 1;
      font-weight: 700;
      opacity: 0.86;
      transform: translateY(1px);
    }

    #${ROOT_ID} .lmb-send-status {
      max-width: 220px;
      padding: 10px 12px;
      border-radius: 18px;
      background: rgba(18, 16, 15, 0.74);
      backdrop-filter: blur(12px);
      color: #fff8f1;
      font-size: 12px;
      line-height: 1.5;
      box-shadow: 0 14px 34px rgba(0, 0, 0, 0.22);
      word-break: break-word;
    }

    #${ROOT_ID} .lmb-send-status.is-error {
      background: rgba(128, 32, 32, 0.82);
    }

    @media (max-width: 900px) {
      #${ROOT_ID} {
        top: auto;
        right: 14px;
        bottom: 18px;
      }

      #${ROOT_ID} .lmb-send-button {
        min-width: 120px;
        min-height: 82px;
        padding: 12px 14px 10px;
      }
    }
  `;
  document.documentElement.append(style);
}

function ensureOverlay() {
  let root = document.getElementById(ROOT_ID);
  if (root) {
    return root;
  }

  root = document.createElement("div");
  root.id = ROOT_ID;
  root.hidden = true;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "lmb-send-button";
  button.setAttribute("aria-label", "LoRA管理へページ情報を送る");

  const label = document.createElement("span");
  label.className = "lmb-send-label";
  label.textContent = "ページ情報を送る";

  const arrow = document.createElement("span");
  arrow.className = "lmb-send-arrow";
  arrow.textContent = "↓";

  const status = document.createElement("div");
  status.className = "lmb-send-status";
  status.hidden = true;

  button.append(label, arrow);
  root.append(button, status);
  document.documentElement.append(root);

  button.addEventListener("click", () => {
    sendCurrentImagePage();
  });

  return root;
}

function getOverlayParts() {
  const root = ensureOverlay();
  return {
    root,
    button: root.querySelector(".lmb-send-button"),
    label: root.querySelector(".lmb-send-label"),
    status: root.querySelector(".lmb-send-status"),
  };
}

function setOverlayVisible(visible) {
  const { root } = getOverlayParts();
  root.hidden = !visible;
}

function setBusyState(busy, labelText = "") {
  overlayState.busy = Boolean(busy);
  const { button, label } = getOverlayParts();
  button.disabled = overlayState.busy;
  label.textContent = labelText || (overlayState.busy ? "送信しています" : "ページ情報を送る");
}

function showStatus(message, isError = false) {
  const { status } = getOverlayParts();
  window.clearTimeout(overlayState.statusTimer);
  const text = String(message || "").trim();
  if (!text) {
    status.hidden = true;
    status.textContent = "";
    status.classList.remove("is-error");
    return;
  }

  status.hidden = false;
  status.textContent = text;
  status.classList.toggle("is-error", Boolean(isError));
  overlayState.statusTimer = window.setTimeout(() => {
    status.hidden = true;
    status.textContent = "";
    status.classList.remove("is-error");
  }, isError ? 4800 : 2600);
}

async function sendCurrentImagePage() {
  if (overlayState.busy || !isCivitaiImagePage()) {
    return;
  }

  setBusyState(true, "送信しています");
  showStatus("");

  try {
    const pageData = await extractCivitaiImagePageData();
    if (!pageData || !pageData.source_url) {
      throw new Error("ページ情報を取得できませんでした。");
    }

    const stored = await chrome.storage.sync.get({ [BASE_URL_KEY]: DEFAULT_BASE_URL });
    const baseUrl = normalizeBaseUrl(stored[BASE_URL_KEY]);
    const response = await chrome.runtime.sendMessage({
      type: "send-browser-import",
      baseUrl,
      pageData,
    });

    if (!response?.ok) {
      throw new Error(response?.error || "LoRA管理への送信に失敗しました。");
    }

    showStatus("LoRA管理へ送信しました。");
  } catch (error) {
    showStatus(error instanceof Error ? error.message : "送信に失敗しました。", true);
  } finally {
    setBusyState(false);
  }
}

async function extractCivitaiImagePageData() {
  const firstText = (...values) => {
    for (const value of values) {
      const text = String(value || "").replace(/\s+/g, " ").trim();
      if (text) {
        return text;
      }
    }
    return "";
  };

  const splitList = (value) =>
    String(value || "")
      .split(/[\n,;|/]+/)
      .map((entry) => entry.trim())
      .filter(Boolean)
      .filter((entry, index, array) => array.findIndex((candidate) => candidate.toLowerCase() === entry.toLowerCase()) === index);

  const addMany = (target, values) => {
    values.forEach((value) => {
      if (!value) {
        return;
      }
      if (!target.some((entry) => entry.toLowerCase() === value.toLowerCase())) {
        target.push(value);
      }
    });
  };

  const normalizeUrl = (value, baseUrl = "") => {
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
  };

  const buildPromptPreview = (promptText, negativePromptText = "") => {
    const prompt = firstText(promptText).replace(/\s+/g, " ").trim();
    if (prompt) {
      return prompt.slice(0, 220);
    }

    const negativePrompt = firstText(negativePromptText).replace(/\s+/g, " ").trim();
    if (negativePrompt) {
      return `Negative: ${negativePrompt}`.slice(0, 220);
    }

    return "";
  };

  const meta = (selector) => document.querySelector(selector)?.getAttribute("content") || "";

  const fetchCivitaiImageGenerationData = async () => {
    const match = location.pathname.match(/^\/images\/(\d+)(?:\/|$)/);
    if (!match) {
      return {};
    }

    const imageId = Number(match[1]);
    if (!Number.isFinite(imageId) || imageId <= 0) {
      return {};
    }

    const input = encodeURIComponent(JSON.stringify({ json: { id: imageId } }));

    try {
      const response = await fetch(`/api/trpc/image.getGenerationData?input=${input}`, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        return {};
      }

      const payload = await response.json();
      const result = payload?.result?.data?.json;
      if (!result || typeof result !== "object") {
        return {};
      }

      const metaPayload = result.meta && typeof result.meta === "object" ? result.meta : {};
      const prompt = firstText(metaPayload.prompt, result.prompt);
      const negativePrompt = firstText(
        metaPayload.negativePrompt,
        metaPayload.negative_prompt,
        result.negativePrompt,
        result.negative_prompt
      );
      const steps = firstText(metaPayload.steps, result.steps);
      const sampler = firstText(metaPayload.sampler, metaPayload.samplerName, result.sampler, result.samplerName);
      const cfgScale = firstText(
        metaPayload.cfgScale,
        metaPayload.cfg_scale,
        metaPayload.cfg,
        result.cfgScale,
        result.cfg_scale,
        result.cfg
      );
      const seed = firstText(metaPayload.seed, result.seed);

      const resourceTriggers = [];
      const resources = Array.isArray(metaPayload.resources)
        ? metaPayload.resources
        : Array.isArray(result.resources)
          ? result.resources
          : [];
      const resourcesUsed = [];
      const seenResourceKeys = new Set();

      resources.forEach((resource) => {
        if (!resource || typeof resource !== "object") {
          return;
        }
        const trainedWords = splitList(resource.trainedWords || resource.trained_words || resource.triggerWords || "");
        addMany(resourceTriggers, trainedWords);

        const modelId = firstText(resource.modelId, resource.model_id);
        const modelVersionId = firstText(
          resource.modelVersionId,
          resource.model_version_id,
          resource.versionId,
          resource.version_id
        );
        const entry = {
          name: firstText(resource.modelName, resource.model_name, resource.name),
          type: firstText(resource.modelType, resource.model_type, resource.type),
          strength: firstText(resource.strength, resource.weight),
          base_model: firstText(resource.baseModel, resource.base_model),
          version_name: firstText(resource.versionName, resource.version_name),
          model_id: modelId,
          model_version_id: modelVersionId,
          trained_words: trainedWords,
          url: modelId
            ? `https://civitai.red/models/${encodeURIComponent(modelId)}${
                modelVersionId ? `?modelVersionId=${encodeURIComponent(modelVersionId)}` : ""
              }`
            : "",
        };
        const key = JSON.stringify([
          String(entry.url || "").toLowerCase(),
          String(entry.name || "").toLowerCase(),
          String(entry.type || "").toLowerCase(),
          String(entry.version_name || "").toLowerCase(),
          String(entry.strength || "").toLowerCase(),
        ]);
        if (seenResourceKeys.has(key)) {
          return;
        }
        seenResourceKeys.add(key);
        resourcesUsed.push(entry);
      });

      return {
        prompt,
        negative_prompt: negativePrompt,
        prompt_preview: buildPromptPreview(prompt, negativePrompt),
        steps,
        sampler,
        cfg_scale: cfgScale,
        seed,
        triggers: resourceTriggers.slice(0, 24),
        resources_used: resourcesUsed.slice(0, 40),
      };
    } catch (_error) {
      return {};
    }
  };

  const normalizeMediaType = (value) => String(value || "").trim().toLowerCase();

  const inferCivitaiAssetExtension = (mimeType, fileName = "") => {
    const explicitName = firstText(fileName);
    const explicitExtension = explicitName.match(/\.[A-Za-z0-9]+$/)?.[0]?.toLowerCase() || "";
    if (explicitExtension) {
      return explicitExtension;
    }

    const normalizedMimeType = normalizeMediaType(mimeType);
    if (normalizedMimeType === "video/mp4") {
      return ".mp4";
    }
    if (normalizedMimeType === "video/webm") {
      return ".webm";
    }
    if (normalizedMimeType === "video/quicktime") {
      return ".mov";
    }
    if (normalizedMimeType === "image/png") {
      return ".png";
    }
    if (normalizedMimeType === "image/webp") {
      return ".webp";
    }
    if (normalizedMimeType === "image/avif") {
      return ".avif";
    }
    if (normalizedMimeType === "image/gif") {
      return ".gif";
    }
    return ".jpeg";
  };

  const buildCivitaiAssetBaseUrl = (...values) => {
    for (const value of values) {
      const normalized = String(value || "").replace(/&amp;/g, "&").trim();
      if (!normalized) {
        continue;
      }
      const match = normalized.match(/^(https?:\/\/image\.civitai\.com\/[^/]+)/i);
      if (match) {
        return match[1];
      }
    }
    return "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA";
  };

  const buildCivitaiAssetUrl = (asset, fallbackUrl = "") => {
    if (!asset || typeof asset !== "object") {
      return "";
    }

    const assetToken = firstText(asset.url);
    if (!assetToken) {
      return "";
    }

    const mimeType = firstText(asset.mimeType, asset.mime_type);
    const type = firstText(asset.type);
    const extension = inferCivitaiAssetExtension(mimeType, asset.name);
    const fileName = firstText(asset.name) || `civitai-asset${extension}`;
    const baseUrl = buildCivitaiAssetBaseUrl(
      fallbackUrl,
      meta('meta[property="og:image"]'),
      meta('meta[property="og:image:secure_url"]'),
      meta('meta[name="twitter:image"]'),
      meta('meta[property="og:video"]'),
      meta('meta[property="og:video:url"]'),
      meta('meta[property="og:video:secure_url"]')
    );
    const transform = /video/i.test(type) || normalizeMediaType(mimeType).startsWith("video/")
      ? "original=true,quality=90"
      : "width=450";

    return `${baseUrl}/${encodeURIComponent(assetToken)}/${transform}/${encodeURIComponent(fileName)}`;
  };

  const fetchCivitaiImageAssetData = async () => {
    if (!/^(?:www\.)?civitai\.(?:com|red)$/i.test(location.hostname)) {
      return {};
    }

    const match = location.pathname.match(/^\/images\/(\d+)(?:\/|$)/);
    if (!match) {
      return {};
    }

    const imageId = Number(match[1]);
    if (!Number.isFinite(imageId) || imageId <= 0) {
      return {};
    }

    const input = encodeURIComponent(JSON.stringify({ json: { id: imageId } }));

    try {
      const response = await fetch(`/api/trpc/image.get?input=${input}`, {
        credentials: "include",
        headers: {
          Accept: "application/json",
        },
      });
      if (!response.ok) {
        return {};
      }

      const payload = await response.json();
      const result = payload?.result?.data?.json;
      if (!result || typeof result !== "object") {
        return {};
      }

      const mimeType = firstText(result.mimeType, result.mime_type);
      const type = firstText(result.type);
      const mediaUrl = buildCivitaiAssetUrl(result);
      const isVideo = /video/i.test(type) || normalizeMediaType(mimeType).startsWith("video/");

      return {
        type,
        media_kind: isVideo ? "video" : "image",
        mime_type: mimeType,
        preview_media_url: mediaUrl,
        preview_video_url: isVideo ? mediaUrl : "",
        preview_image_url: !isVideo ? mediaUrl : "",
      };
    } catch (_error) {
      return {};
    }
  };

  const collectPreviewVideoUrl = () => {
    const candidates = [];
    const seen = new Set();

    const addCandidate = (urlLike, score = 0) => {
      const url = normalizeUrl(urlLike, location.href);
      if (!url || seen.has(url)) {
        return;
      }
      seen.add(url);
      candidates.push({ url, score });
    };

    addCandidate(meta('meta[property="og:video"]'), 180);
    addCandidate(meta('meta[property="og:video:url"]'), 175);
    addCandidate(meta('meta[property="og:video:secure_url"]'), 170);
    addCandidate(meta('meta[name="twitter:player:stream"]'), 165);

    Array.from(document.querySelectorAll("video"))
      .slice(0, 60)
      .forEach((video) => {
        const width = Number(video.videoWidth || video.clientWidth || video.offsetWidth || 0);
        const height = Number(video.videoHeight || video.clientHeight || video.offsetHeight || 0);
        const score = Math.max(80, width * Math.max(height, 1));
        addCandidate(video.currentSrc || video.src || video.getAttribute("src"), score);
        Array.from(video.querySelectorAll("source")).forEach((source) => {
          addCandidate(source.src || source.getAttribute("src"), score - 5);
        });
      });

    candidates.sort((left, right) => right.score - left.score);
    return candidates[0]?.url || "";
  };

  const collectPreviewImageUrl = () => {
    const direct = normalizeUrl(
      meta('meta[property="og:image"]') ||
        meta('meta[name="twitter:image"]') ||
        meta('meta[property="twitter:image"]'),
      location.href
    );
    if (direct) {
      return direct;
    }

    const candidates = [];
    Array.from(document.images)
      .slice(0, 120)
      .forEach((image) => {
        const url = normalizeUrl(image.currentSrc || image.src || image.getAttribute("src"), location.href);
        if (!url) {
          return;
        }
        const width = Number(image.naturalWidth || image.width || 0);
        const height = Number(image.naturalHeight || image.height || 0);
        if (width < 240 || height < 240) {
          return;
        }
        candidates.push({ url, score: width * height });
      });

    candidates.sort((left, right) => right.score - left.score);
    return candidates[0]?.url || "";
  };

  const [civitaiImageData, civitaiImageAsset] = await Promise.all([
    fetchCivitaiImageGenerationData(),
    fetchCivitaiImageAssetData(),
  ]);
  const civitaiAssetKind = firstText(civitaiImageAsset.media_kind, civitaiImageAsset.type)
    .trim()
    .toLowerCase();
  const hasCivitaiAsset = Boolean(civitaiImageAsset.preview_media_url || civitaiImageAsset.preview_image_url || civitaiImageAsset.preview_video_url);
  const previewVideoUrl = hasCivitaiAsset
    ? firstText(civitaiImageAsset.preview_video_url)
    : firstText(civitaiImageAsset.preview_video_url, collectPreviewVideoUrl());
  const previewImageUrl = hasCivitaiAsset
    ? firstText(civitaiImageAsset.preview_image_url, collectPreviewImageUrl())
    : firstText(civitaiImageAsset.preview_image_url, collectPreviewImageUrl());
  const baseModel =
    civitaiImageData.resources_used?.find((resource) => /checkpoint/i.test(String(resource.type || "")))?.base_model ||
    civitaiImageData.resources_used?.[0]?.base_model ||
    "";

  return {
    source_url: location.href,
    source_host: location.hostname,
    title: firstText(meta('meta[property="og:title"]'), document.querySelector("h1")?.textContent, document.title),
    description: firstText(meta('meta[name="description"]'), meta('meta[property="og:description"]')),
    author: firstText(meta('meta[name="author"]')),
    base_model: baseModel,
    prompt: firstText(civitaiImageData.prompt),
    negative_prompt: firstText(civitaiImageData.negative_prompt),
    prompt_preview: firstText(civitaiImageData.prompt_preview),
    preview_media_kind: civitaiAssetKind || (previewVideoUrl ? "video" : previewImageUrl ? "image" : ""),
    preview_image_url: previewImageUrl,
    preview_media_url: firstText(civitaiImageAsset.preview_media_url, previewVideoUrl, previewImageUrl),
    preview_video_url: previewVideoUrl,
    triggers: Array.isArray(civitaiImageData.triggers) ? civitaiImageData.triggers : [],
    tags: [],
    resources_used: Array.isArray(civitaiImageData.resources_used) ? civitaiImageData.resources_used : [],
  };
}

function syncOverlayVisibility() {
  const visible = isCivitaiImagePage();
  setOverlayVisible(visible);
  overlayState.mountedUrl = window.location.href;
  if (!visible) {
    showStatus("");
    setBusyState(false);
  }
}

function scheduleOverlaySync() {
  window.requestAnimationFrame(() => {
    syncOverlayVisibility();
  });
}

function installNavigationHooks() {
  const wrapHistoryMethod = (methodName) => {
    const original = history[methodName];
    if (typeof original !== "function") {
      return;
    }
    history[methodName] = function wrappedHistoryMethod(...args) {
      const result = original.apply(this, args);
      window.setTimeout(scheduleOverlaySync, 0);
      return result;
    };
  };

  wrapHistoryMethod("pushState");
  wrapHistoryMethod("replaceState");
  window.addEventListener("popstate", scheduleOverlaySync);
  window.addEventListener("hashchange", scheduleOverlaySync);

  const observer = new MutationObserver(() => {
    if (overlayState.mountedUrl !== window.location.href) {
      scheduleOverlaySync();
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
}

function initImageOverlay() {
  ensureStyle();
  ensureOverlay();
  syncOverlayVisibility();
  installNavigationHooks();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initImageOverlay, { once: true });
} else {
  initImageOverlay();
}
