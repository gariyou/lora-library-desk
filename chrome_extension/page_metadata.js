async function extractPageMetadata() {
  const firstText = (...values) => {
    for (const value of values) {
      const text = String(value || "").replace(/\s+/g, " ").trim();
      if (text) {
        return text;
      }
    }
    return "";
  };

  const truncateTextInPage = (value, maxLength) => {
    const text = String(value || "").trim();
    if (text.length <= maxLength) {
      return text;
    }
    return `${text.slice(0, Math.max(0, maxLength - 1))}…`;
  };

  const normalizeUrlInPage = (value, baseUrl = "") => {
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

  const fileNameFromPathInPage = (value) => String(value || "").split(/[\\/]/).pop() || "";

  const stripHtmlToText = (value) => {
    const text = String(value || "").trim();
    if (!text) {
      return "";
    }

    const container = document.createElement("div");
    container.innerHTML = text;
    return String(container.textContent || container.innerText || "")
      .replace(/\s+/g, " ")
      .trim();
  };

  const summarizeDescription = (value) => truncateTextInPage(stripHtmlToText(value), 420);

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

  const meta = (selector) => document.querySelector(selector)?.getAttribute("content") || "";

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

  const normalizeModelFamilyInPage = (value) => {
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

  const fetchCivitaiImageGenerationData = async () => {
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
      const response = await fetch(`/api/trpc/image.getGenerationData?input=${input}`, {
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

  const fetchCivitaiModelPageData = async () => {
    if (!/^(?:www\.)?civitai\.(?:com|red)$/i.test(location.hostname)) {
      return {};
    }

    const match = location.pathname.match(/^\/models\/(\d+)(?:\/|$)/);
    if (!match) {
      return {};
    }

    const modelId = Number(match[1]);
    if (!Number.isFinite(modelId) || modelId <= 0) {
      return {};
    }

    const input = encodeURIComponent(JSON.stringify({ json: { id: modelId, excludeTrainingData: true } }));

    try {
      const response = await fetch(`/api/trpc/model.getById?input=${input}`, {
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

      const selectedVersionId = firstText(new URLSearchParams(location.search).get("modelVersionId"));
      const creator =
        (result.creator && typeof result.creator === "object" ? firstText(result.creator.username, result.creator.name) : "") ||
        (result.user && typeof result.user === "object" ? firstText(result.user.username, result.user.name) : "");
      const modelVersions = Array.isArray(result.modelVersions) ? result.modelVersions : [];
      const latestVersion = modelVersions.find((entry) => entry && typeof entry === "object") || {};
      const selectedVersion =
        modelVersions.find((entry) => String(entry?.id || "") === selectedVersionId) ||
        (selectedVersionId ? null : latestVersion);
      if (!selectedVersion) return {title: firstText(result.name), version_unavailable: true, download_candidates: []};
      const versionFiles = Array.isArray(selectedVersion.files) ? selectedVersion.files : [];
      const downloadCandidates = [];
      const seenDownloadUrls = new Set();

      versionFiles.forEach((file) => {
        if (!file || typeof file !== "object") {
          return;
        }

        const downloadUrl = firstText(file.downloadUrl, file.url);
        const normalizedUrl = normalizeUrlInPage(downloadUrl, location.href);
        if (!normalizedUrl || seenDownloadUrls.has(normalizedUrl)) {
          return;
        }

        const suggestedFilename = fileNameFromPathInPage(firstText(file.name, file.filename));
        const fileType = firstText(file.type, file.modelType).toUpperCase();
        const versionName = firstText(selectedVersion.name);
        const labelParts = [
          suggestedFilename || firstText(result.name),
          versionName,
          fileType,
        ].filter(Boolean);

        seenDownloadUrls.add(normalizedUrl);
        downloadCandidates.push({
          url: normalizedUrl,
          label: labelParts.join(" / "),
          suggested_filename: suggestedFilename,
        });
      });

      return {
        title: firstText(result.name),
        description: summarizeDescription(result.description),
        author: creator,
        base_model: firstText(result.baseModel, selectedVersion.baseModel, selectedVersion.base_model, latestVersion.baseModel, latestVersion.base_model),
        model_family_hint: normalizeModelFamilyInPage(
          firstText(result.type, selectedVersion.modelType, selectedVersion.type, latestVersion.modelType, latestVersion.type)
        ),
        triggers: Array.isArray(selectedVersion.trainedWords) ? selectedVersion.trainedWords : null,
        download_candidates: downloadCandidates,
      };
    } catch (_error) {
      const matchText =
        Array.from(document.scripts)
          .slice(0, 80)
          .map((script) => script.textContent || "")
          .join("\n") || document.documentElement.innerHTML || "";
      const matchedType =
        matchText.match(/"type":"(Checkpoint|LORA|TextualInversion|LoCon|LoHa|LoKr|LyCORIS|Workflows)"/i)?.[1] || "";
      return {
        model_family_hint: normalizeModelFamilyInPage(matchedType),
      };
    }
  };

  const parseJsonScripts = () => {
    const scripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
    const collected = [];
    scripts.forEach((script) => {
      try {
        const parsed = JSON.parse(script.textContent || "");
        collected.push(parsed);
      } catch (_error) {
        return;
      }
    });
    return collected;
  };

  const flattenStructuredData = (value, bucket = []) => {
    if (!value) {
      return bucket;
    }
    if (Array.isArray(value)) {
      value.forEach((entry) => flattenStructuredData(entry, bucket));
      return bucket;
    }
    if (typeof value === "object") {
      bucket.push(value);
      Object.values(value).forEach((entry) => flattenStructuredData(entry, bucket));
    }
    return bucket;
  };

  const collectSectionValues = (labels) => {
    const results = [];
    const candidates = Array.from(document.querySelectorAll("h1,h2,h3,h4,h5,dt,div,span,strong,p"));
    const labelSet = labels.map((label) => label.toLowerCase());

    for (const node of candidates.slice(0, 500)) {
      const text = firstText(node.textContent);
      if (!text || text.length > 80) {
        continue;
      }

      const lower = text.toLowerCase().replace(/[:：]$/, "").trim();
      if (!labelSet.includes(lower)) {
        continue;
      }

      const nearby = [node.nextElementSibling];
      if (firstText(node.parentElement?.textContent) === text) nearby.push(node.parentElement?.nextElementSibling);
      for (const container of nearby) {
        if (!container || /(?:civitai:\d|urn:air:|^AIR\b)/i.test(firstText(container.textContent))) {
          continue;
        }

        const tokens = Array.from(container.querySelectorAll("a,button,li,span,code,div"))
          .map((entry) => firstText(entry.textContent))
          .filter((entry) => entry && entry.length <= 64);

        if (!tokens.length) tokens.push(firstText(container.textContent));
        addMany(results, splitList(tokens.join(",")));
        if (results.length) {
          return results.slice(0, 20);
        }
      }
    }

    return results.slice(0, 20);
  };

  const collectDownloadCandidates = () => {
    const candidates = [];
    const seen = new Set();

    const inferLabelFromUrl = (value) => {
      try {
        const url = new URL(value, location.href);
        const fileName = url.pathname.split("/").pop() || "";
        return fileName || "download";
      } catch (_error) {
        return "download";
      }
    };

    const inferFilename = (url, suggested) => {
      const explicit = firstText(suggested);
      if (explicit) {
        return explicit.split(/[\\/]/).pop() || "";
      }
      try {
        const parsed = new URL(url, location.href);
        return parsed.pathname.split("/").pop() || "";
      } catch (_error) {
        return "";
      }
    };

    const normalizeCandidateUrl = (value) => {
      const text = String(value || "").replace(/&amp;/g, "&").trim();
      if (!text) {
        return "";
      }
      try {
        const parsed = new URL(text, location.href);
        if (!/^https?:$/i.test(parsed.protocol)) {
          return "";
        }
        return parsed.href;
      } catch (_error) {
        return "";
      }
    };

    const scoreCandidate = (url) => {
      const haystack = url.toLowerCase();
      if (haystack.includes("/api/download/models/")) {
        return 100;
      }
      if (/\.(safetensors|ckpt|pt|pth|bin|json|zip)(?:$|[?#])/.test(haystack)) {
        return 90;
      }
      if (haystack.includes("format=safetensor")) {
        return 80;
      }
      if (haystack.includes("type=model")) {
        return 70;
      }
      if (haystack.includes("/download/") || haystack.includes("download?")) {
        return 40;
      }
      return 0;
    };

    const isLikelyDownload = (url) => {
      const haystack = url.toLowerCase();
      if (/\.(safetensors|ckpt|pt|pth|bin|json|zip)(?:$|[?#])/.test(haystack)) {
        return true;
      }
      if (haystack.includes("/api/download/models/")) {
        return true;
      }
      if (haystack.includes("format=safetensor") || haystack.includes("type=model")) {
        return true;
      }
      return haystack.includes("/download/") || haystack.includes("download?");
    };

    const isIgnoredAsset = (url) => /\.(png|jpe?g|webp|gif|svg|mp4|webm)(?:$|[?#])/i.test(url);

    const addCandidate = (urlLike, labelLike = "", suggestedFilename = "") => {
      const url = normalizeCandidateUrl(urlLike);
      if (!url || seen.has(url) || isIgnoredAsset(url)) {
        return;
      }
      const label = firstText(labelLike) || inferLabelFromUrl(url);
      if (!isLikelyDownload(url)) {
        return;
      }
      seen.add(url);
      candidates.push({
        url,
        label,
        suggested_filename: inferFilename(url, suggestedFilename),
        score: scoreCandidate(url),
      });
    };

    Array.from(document.querySelectorAll("a[href], area[href]")).forEach((node) => {
      addCandidate(
        node.getAttribute("href"),
        firstText(node.textContent, node.getAttribute("aria-label"), node.getAttribute("title"), node.getAttribute("download")),
        node.getAttribute("download")
      );
    });

    Array.from(document.querySelectorAll("[data-download-url],[data-href],[data-url]")).forEach((node) => {
      ["data-download-url", "data-href", "data-url"].forEach((attributeName) => {
        addCandidate(
          node.getAttribute(attributeName),
          firstText(node.textContent, node.getAttribute("aria-label"), node.getAttribute("title")),
          node.getAttribute("download")
        );
      });
    });

    const scriptPatterns = [
      /https?:\/\/[^"'\\\s<>]+\/api\/download\/models\/\d+[^"'\\\s<>]*/gi,
      /\/api\/download\/models\/\d+[^"'\\\s<>]*/gi,
    ];

    Array.from(document.scripts).slice(0, 80).forEach((script) => {
      const text = script.textContent || "";
      scriptPatterns.forEach((pattern) => {
        for (const match of text.matchAll(pattern)) {
          addCandidate(match[0], "script download link");
          if (candidates.length >= 12) {
            return;
          }
        }
      });
    });

    return candidates
      .sort((left, right) => (right.score || 0) - (left.score || 0))
      .slice(0, 12)
      .map(({ score, ...candidate }) => candidate);
  };

  const collectPreviewVideoUrl = () => {
    const candidates = [];
    const seen = new Set();

    const addCandidate = (urlLike, score = 0) => {
      const url = normalizeUrlInPage(urlLike, location.href);
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

    structured.forEach((entry) => {
      if (!entry || typeof entry !== "object") {
        return;
      }
      addCandidate(entry.contentUrl, 150);
      addCandidate(entry.embedUrl, 145);
      if (typeof entry.video === "string") {
        addCandidate(entry.video, 140);
      } else if (entry.video && typeof entry.video === "object") {
        addCandidate(entry.video.url, 140);
        addCandidate(entry.video.contentUrl, 140);
      }
    });

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

    candidates.sort((left, right) => (right.score || 0) - (left.score || 0));
    return candidates[0]?.url || "";
  };

  const collectPreviewImageUrl = () => {
    const candidates = [];
    const seen = new Set();

    const addCandidate = (urlLike, score = 0, hint = "") => {
      const url = normalizeUrlInPage(urlLike, location.href);
      if (!url || seen.has(url)) {
        return;
      }

      const haystack = `${url} ${hint}`.toLowerCase();
      if (/(avatar|icon|emoji|favicon|logo|sprite)/.test(haystack)) {
        score -= 120;
      }
      if (/(preview|sample|gallery|image|images|model-version)/.test(haystack)) {
        score += 15;
      }

      seen.add(url);
      candidates.push({ url, score });
    };

    addCandidate(meta('meta[property="og:image"]'), 140, "og:image");
    addCandidate(meta('meta[property="og:image:secure_url"]'), 135, "og:image");
    addCandidate(meta('meta[name="twitter:image"]'), 130, "twitter:image");
    addCandidate(meta('meta[name="twitter:image:src"]'), 125, "twitter:image");
    addCandidate(document.querySelector('link[rel="image_src"]')?.getAttribute("href"), 120, "image_src");

    structured.forEach((entry) => {
      const values = [];
      if (typeof entry.image === "string") {
        values.push(entry.image);
      } else if (Array.isArray(entry.image)) {
        entry.image.forEach((imageEntry) => values.push(imageEntry));
      } else if (entry.image && typeof entry.image === "object") {
        values.push(entry.image.url, entry.image.contentUrl);
      }
      if (typeof entry.thumbnailUrl === "string") {
        values.push(entry.thumbnailUrl);
      }
      values.forEach((imageValue) => {
        if (typeof imageValue === "string") {
          addCandidate(imageValue, 105, "structured");
          return;
        }
        if (imageValue && typeof imageValue === "object") {
          addCandidate(imageValue.url, 105, "structured");
          addCandidate(imageValue.contentUrl, 105, "structured");
        }
      });
    });

    Array.from(document.images)
      .slice(0, 120)
      .forEach((image) => {
        const url = image.currentSrc || image.src || image.getAttribute("src");
        const width = Number(image.naturalWidth || image.width || 0);
        const height = Number(image.naturalHeight || image.height || 0);
        if (width < 240 || height < 240) {
          return;
        }
        const areaScore = Math.min(60, Math.round((width * height) / 20000));
        const hint = firstText(image.alt, image.getAttribute("aria-label"), image.className, image.id);
        addCandidate(url, 60 + areaScore, hint);
      });

    candidates.sort((left, right) => (right.score || 0) - (left.score || 0));
    return candidates[0]?.url || "";
  };

  const structured = flattenStructuredData(parseJsonScripts());
  const keywords = [];
  const structuredNames = [];
  const structuredDescriptions = [];
  const structuredAuthors = [];
  const structuredModels = [];

  structured.forEach((entry) => {
    addMany(keywords, splitList(entry.keywords));
    addMany(keywords, splitList(entry.tags));
    addMany(structuredNames, [firstText(entry.name, entry.headline)]);
    addMany(structuredDescriptions, [firstText(entry.description)]);
    if (typeof entry.author === "string") {
      addMany(structuredAuthors, [firstText(entry.author)]);
    } else if (entry.author && typeof entry.author === "object") {
      addMany(structuredAuthors, [firstText(entry.author.name)]);
    }
      addMany(structuredModels, [firstText(entry.baseModel, entry.model)]);
  });

  const [civitaiImageData, civitaiImageAsset, civitaiModelData] = await Promise.all([
    fetchCivitaiImageGenerationData(),
    fetchCivitaiImageAssetData(),
    fetchCivitaiModelPageData(),
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
  const apiDownloadCandidates = Array.isArray(civitaiModelData.download_candidates) ? civitaiModelData.download_candidates : [];
  const pageDownloadCandidates = collectDownloadCandidates();
  const combinedDownloadCandidates = civitaiModelData.version_unavailable ? [] : apiDownloadCandidates.length ? apiDownloadCandidates : pageDownloadCandidates;
  const triggers = Array.isArray(civitaiModelData.triggers) ? [...civitaiModelData.triggers] : collectSectionValues(["trained words", "trigger words", "activation text", "trigger"]);
  addMany(triggers, civitaiImageData.triggers || []);
  const tags = [];
  addMany(tags, splitList(meta('meta[name="keywords"]')));
  addMany(tags, keywords);
  addMany(tags, collectSectionValues(["tags"]));

  const baseModel = firstText(
    meta('meta[name="base-model"]'),
    structuredModels[0],
    collectSectionValues(["base model", "model"]).find(Boolean)
  );

  return {
    source_url: location.href,
    source_host: location.hostname,
    title: firstText(
      civitaiModelData.title,
      meta('meta[property="og:title"]'),
      document.querySelector("h1")?.textContent,
      structuredNames[0],
      document.title
    ),
    description: firstText(
      civitaiModelData.description,
      summarizeDescription(meta('meta[name="description"]')),
      summarizeDescription(meta('meta[property="og:description"]')),
      summarizeDescription(structuredDescriptions[0])
    ),
    author: firstText(civitaiModelData.author, structuredAuthors[0], meta('meta[name="author"]')),
    base_model: firstText(civitaiModelData.base_model, baseModel),
    model_family_hint: firstText(civitaiModelData.model_family_hint),
    prompt: firstText(civitaiImageData.prompt),
    negative_prompt: firstText(civitaiImageData.negative_prompt),
    prompt_preview: firstText(civitaiImageData.prompt_preview),
    preview_media_kind: civitaiAssetKind || (previewVideoUrl ? "video" : previewImageUrl ? "image" : ""),
    preview_image_url: previewImageUrl,
    preview_media_url: firstText(civitaiImageAsset.preview_media_url, previewVideoUrl, previewImageUrl),
    preview_video_url: previewVideoUrl,
    triggers,
    tags: tags.slice(0, 24),
    resources_used: Array.isArray(civitaiImageData.resources_used) ? civitaiImageData.resources_used : [],
    download_candidates: combinedDownloadCandidates,
  };
}
