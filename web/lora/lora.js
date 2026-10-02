const CATEGORY_LABELS = {
  all: "すべて",
  uncategorized: "未分類",
  character: "Character",
  style: "Style",
  pose: "Pose",
  clothes: "Clothes",
  concept: "Concept",
  nsfw: "NSFW",
  other: "Other",
};

const BUILTIN_CATEGORIES = ["character", "style", "pose", "clothes", "concept", "nsfw", "other"];
const MODEL_FAMILY_LABELS = {
  all: "すべて",
  lora: "LoRA",
  checkpoint: "Checkpoint",
  embedding: "Embedding",
  workflow: "Workflow",
  other: "Other",
};
const BUILTIN_MODEL_FAMILIES = ["lora", "checkpoint", "embedding", "workflow", "other"];
const PAGE_SIZE = 80;
const AUTO_LOAD_MORE_VIEWPORT_THRESHOLD = 180;
const LIBRARY_POLL_MS = 600;
const FILTER_STORAGE_KEY = "lora-manager:filters:v1";
const FONT_SCALE_STORAGE_KEY = "lora-manager:font-scale:v1";
const FONT_SCALE_MIN = 0.95;
const FONT_SCALE_MAX = 1.45;
const FONT_SCALE_STEP = 0.1;
const DETAIL_WIDTH_STORAGE_KEY = "lora-manager:detail-width:v1";
const DETAIL_WIDTH_MIN = 420;
const DETAIL_WIDTH_MAX = 760;
const DETAIL_WIDTH_RATIO_MAX = 0.58;
const WINDOWS_DRIVE_PATH_RE = /^[A-Za-z]:[\\/]/;
const UNC_PATH_RE = /^\\\\[^\\]+\\[^\\]+/;
const FILE_URL_RE = /^file:/i;
const IMAGE_FILE_EXTENSION_RE = /\.(png|jpe?g|webp|bmp|gif|avif)$/i;
const REFERENCE_MEDIA_FILE_EXTENSION_RE = /\.(png|jpe?g|webp|bmp|gif|avif|mp4|webm|mov|m4v)$/i;
const REFERENCE_VIDEO_FILE_EXTENSION_RE = /\.(mp4|webm|mov|m4v)$/i;

const state = {
  items: [],
  browserImports: [],
  browserImportKnownIds: new Set(),
  browserImportLoading: false,
  warnings: [],
  stats: null,
  scanRevision: 0,
  scannedAt: "",
  scanDurationMs: 0,
  scanStatus: "idle",
  scanError: "",
  pendingDownloadUpdatedAt: null,
  pendingDownloadStatus: "idle",
  watchDirs: [],
  selectedPath: "",
  bulkSelectedPaths: new Set(),
  selectionAnchorPath: "",
  deletingItems: false,
  savingRatings: new Set(),
  visibleCount: PAGE_SIZE,
  referencePath: "",
  referenceItems: [],
  referenceLoading: false,
  referenceError: "",
  referenceViewerPath: "",
  referenceImportQueue: [],
  referenceImportQueuedIds: new Set(),
  referenceImportActiveJob: null,
  referenceImportProcessing: false,
  referenceUploadQueue: [],
  referenceUploadActiveJob: null,
  referenceUploadProcessing: false,
  referenceUploadJobSerial: 0,
  filters: {
    query: "",
    favoriteOnly: false,
    rating: "all",
    category: "all",
    family: "all",
    sort: "favorite",
  },
};

const elements = {
  appShell: document.getElementById("app-shell"),
  folderInput: document.getElementById("folder-input"),
  pickFolder: document.getElementById("pick-folder"),
  addFolder: document.getElementById("add-folder"),
  clearFolders: document.getElementById("clear-folders"),
  folderCount: document.getElementById("folder-count"),
  folderDropZone: document.getElementById("folder-drop-zone"),
  folderDropHint: document.getElementById("folder-drop-hint"),
  watchDirList: document.getElementById("watch-dir-list"),
  searchInput: document.getElementById("search-input"),
  favoriteFilter: document.getElementById("favorite-filter"),
  ratingFilter: document.getElementById("rating-filter"),
  categoryFilter: document.getElementById("category-filter"),
  familyFilter: document.getElementById("family-filter"),
  sortSelect: document.getElementById("sort-select"),
  bulkCount: document.getElementById("bulk-count"),
  deleteBulk: document.getElementById("delete-bulk"),
  selectionCount: document.getElementById("selection-count"),
  selectVisible: document.getElementById("select-visible"),
  clearBulk: document.getElementById("clear-bulk"),
  bulkForm: document.getElementById("bulk-form"),
  bulkCategory: document.getElementById("bulk-category"),
  bulkCategoryCustom: document.getElementById("bulk-category-custom"),
  bulkFavorite: document.getElementById("bulk-favorite"),
  refreshLibrary: document.getElementById("refresh-library"),
  clearSelection: document.getElementById("clear-selection"),
  lastScan: document.getElementById("last-scan"),
  statsGrid: document.getElementById("stats-grid"),
  warningList: document.getElementById("warning-list"),
  browserImportCount: document.getElementById("browser-import-count"),
  browserImportTarget: document.getElementById("browser-import-target"),
  refreshBrowserImports: document.getElementById("refresh-browser-imports"),
  browserImportList: document.getElementById("browser-import-list"),
  resultCount: document.getElementById("result-count"),
  statusLine: document.getElementById("status-line"),
  visibleCount: document.getElementById("visible-count"),
  fontScaleDecrease: document.getElementById("font-scale-decrease"),
  fontScaleIncrease: document.getElementById("font-scale-increase"),
  fontScaleReset: document.getElementById("font-scale-reset"),
  fontScaleValue: document.getElementById("font-scale-value"),
  detailResizer: document.getElementById("detail-resizer"),
  detailColumn: document.querySelector(".detail-column"),
  referencePanel: document.getElementById("reference-panel"),
  referenceHeading: document.getElementById("reference-heading"),
  referenceCount: document.getElementById("reference-count"),
  referenceStatus: document.getElementById("reference-status"),
  referenceDropZone: document.getElementById("reference-drop-zone"),
  referenceDropHint: document.getElementById("reference-drop-hint"),
  referenceGrid: document.getElementById("reference-grid"),
  addReferenceImages: document.getElementById("add-reference-images"),
  referenceUploadInput: document.getElementById("reference-upload-input"),
  referenceViewer: document.getElementById("reference-viewer"),
  referenceViewerBackdrop: document.getElementById("reference-viewer-backdrop"),
  referenceViewerClose: document.getElementById("reference-viewer-close"),
  referenceViewerImage: document.getElementById("reference-viewer-image"),
  referenceViewerVideo: document.getElementById("reference-viewer-video"),
  referenceViewerTitle: document.getElementById("reference-viewer-title"),
  referenceViewerSubtitle: document.getElementById("reference-viewer-subtitle"),
  referenceViewerBadges: document.getElementById("reference-viewer-badges"),
  referenceViewerMetaGrid: document.getElementById("reference-viewer-meta-grid"),
  referenceViewerDescription: document.getElementById("reference-viewer-description"),
  referenceViewerSections: document.getElementById("reference-viewer-sections"),
  referenceCopyPrompt: document.getElementById("reference-copy-prompt"),
  referenceCopyNegative: document.getElementById("reference-copy-negative"),
  referenceCopyAll: document.getElementById("reference-copy-all"),
  referenceSendEncyclopedia: document.getElementById("reference-send-encyclopedia"),
  referenceOpenImage: document.getElementById("reference-open-image"),
  referenceOpenSource: document.getElementById("reference-open-source"),
  emptyState: document.getElementById("empty-state"),
  libraryGrid: document.getElementById("library-grid"),
  loadMoreWrap: document.getElementById("load-more-wrap"),
  loadMore: document.getElementById("load-more"),
  libraryColumn: document.querySelector(".library-column"),
  detailEmpty: document.getElementById("detail-empty"),
  detailPanel: document.getElementById("detail-panel"),
  detailForm: document.getElementById("detail-form"),
  detailColumn: document.querySelector(".detail-column"),
  detailPreviewShell: document.getElementById("detail-preview-shell"),
  detailPreview: document.getElementById("detail-preview"),
  detailPreviewVideo: document.getElementById("detail-preview-video"),
  detailPreviewName: document.getElementById("detail-preview-name"),
  replacePreview: document.getElementById("replace-preview"),
  deletePreview: document.getElementById("delete-preview"),
  previewUploadInput: document.getElementById("preview-upload-input"),
  detailFileName: document.getElementById("detail-file-name"),
  detailHeading: document.getElementById("detail-heading"),
  detailRelativePath: document.getElementById("detail-relative-path"),
  detailBadges: document.getElementById("detail-badges"),
  detailFavorite: document.getElementById("detail-favorite"),
  detailDisplayName: document.getElementById("detail-display-name"),
  detailCategory: document.getElementById("detail-category"),
  detailCategoryCustom: document.getElementById("detail-category-custom"),
  detailGenerationSettings: document.getElementById("detail-generation-settings"),
  detailRecommendedSteps: document.getElementById("detail-recommended-steps"),
  detailRecommendedSampler: document.getElementById("detail-recommended-sampler"),
  detailRecommendedScheduler: document.getElementById("detail-recommended-scheduler"),
  detailTriggers: document.getElementById("detail-triggers"),
  detailTags: document.getElementById("detail-tags"),
  detailSourceUrl: document.getElementById("detail-source-url"),
  detailSourceLink: document.getElementById("detail-source-link"),
  detailSourceLinkEmpty: document.getElementById("detail-source-link-empty"),
  detailNotes: document.getElementById("detail-notes"),
  detailPromptPreview: document.getElementById("detail-prompt-preview"),
  detailMetaLine: document.getElementById("detail-meta-line"),
  detailSourceDescription: document.getElementById("detail-source-description"),
  copyPrompt: document.getElementById("copy-prompt"),
  openFolder: document.getElementById("open-folder"),
  deleteItem: document.getElementById("delete-item"),
  categorySuggestions: document.getElementById("category-suggestions"),
  toast: document.getElementById("toast"),
};

let toastTimer = null;
let loadingHintTimer = null;
let renderToken = 0;
let scanRequestToken = 0;
let libraryPollTimer = null;
let referenceRequestToken = 0;
let autoLoadMoreFrame = 0;

document.addEventListener("DOMContentLoaded", () => {
  restoreFilterState();
  restoreFontScale();
  restoreDetailPanelWidth();
  // Apply URL search parameter if present (e.g., ?search=ModelName)
  const urlParams = new URLSearchParams(window.location.search);
  const urlSearch = urlParams.get("search");
  if (urlSearch) {
    state.filters.query = urlSearch;
    state.filters.category = "all";
    state.filters.family = "all";
    state.filters.favoriteOnly = false;
    state.filters.rating = "all";
    syncFilterControls();
  }
  bindEvents();
  renderBulkState();
  loadConfigAndLibrary({ preserveSelection: false, force: false }).finally(() => {
    void refreshPendingDownloadState({ silent: true });
  });
  loadBrowserImports({ primeKnown: true });
  startLibraryAutoRefresh();
});

function bindEvents() {
  bindGlobalFileDropGuards();
  bindFontScaleControls();
  bindDetailResizer();
  bindWatchFolderDropZone();
  bindReferenceDropZone();
  bindPreviewDropZone();

  elements.refreshLibrary.addEventListener("click", () => {
    loadConfigAndLibrary({ preserveSelection: true, force: true });
  });

  if (elements.refreshBrowserImports) {
    elements.refreshBrowserImports.addEventListener("click", () => {
      loadBrowserImports({ autoApply: true });
    });
  }

  elements.pickFolder.addEventListener("click", async () => {
    await browseAndAddFolder();
  });

  elements.addFolder.addEventListener("click", async () => {
    const nextPath = elements.folderInput.value.trim();
    if (!nextPath) {
      showToast("追加したいフォルダパスを入れてください。", true);
      return;
    }

    await addWatchDir(nextPath);
  });

  elements.folderInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      const nextPath = elements.folderInput.value.trim();
      if (!nextPath) {
        browseAndAddFolder();
        return;
      }
      addWatchDir(nextPath);
    }
  });

  elements.clearFolders.addEventListener("click", async () => {
    if (!state.watchDirs.length) {
      return;
    }
    if (!window.confirm("登録済みフォルダをすべて外しますか？")) {
      return;
    }
    await saveWatchDirs([]);
  });

  elements.watchDirList.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-index]");
    if (!button) {
      return;
    }

    const index = Number(button.dataset.index);
    const nextDirs = state.watchDirs.filter((_, position) => position !== index);
    await saveWatchDirs(nextDirs);
  });

  if (elements.browserImportList) {
    elements.browserImportList.addEventListener("click", async (event) => {
      const referenceButton = event.target.closest("button[data-import-reference]");
      if (referenceButton) {
        const item = getSelectedItem();
        if (!item) {
          showToast("先に追加先のモデルを選んでください。", true);
          return;
        }

        const importId = Number(referenceButton.dataset.importReference);
        if (!queueBrowserImportReference(importId, item.path)) {
          showToast("この参考メディアはすでに順番待ちです。");
          return;
        }
        showToast("参考メディアを順番待ちに追加しました。");
        return;
      }

      const applyButton = event.target.closest("button[data-import-apply]");
      if (applyButton) {
        const item = getSelectedItem();
        if (!item) {
          showToast("先に反映先のモデルを選んでください。", true);
          return;
        }

        try {
          await applyBrowserImportMetadata(Number(applyButton.dataset.importApply), item.path, {
            consume: false,
            silent: false,
          });
        } catch (error) {
          showToast(error.message, true);
        }
        return;
      }

      const deleteButton = event.target.closest("button[data-import-delete]");
      if (deleteButton) {
        try {
          await fetchJson("/api/lora/browser-imports/delete", {
            method: "POST",
            body: {
              import_id: Number(deleteButton.dataset.importDelete),
            },
          });
          state.browserImports = state.browserImports.filter((item) => item.id !== Number(deleteButton.dataset.importDelete));
          renderBrowserImports();
          showToast("取り込み候補を削除しました。");
        } catch (error) {
          showToast(error.message, true);
        }
      }
    });
  }

  elements.searchInput.addEventListener("input", (event) => {
    state.filters.query = event.target.value.trim();
    resetVisibleCount();
    persistFilterState();
    renderLibrary();
  });

  elements.favoriteFilter.addEventListener("change", (event) => {
    state.filters.favoriteOnly = event.target.checked;
    resetVisibleCount();
    persistFilterState();
    renderLibrary();
  });

  elements.ratingFilter.addEventListener("change", (event) => {
    state.filters.rating = normalizeRatingFilter(event.target.value);
    resetVisibleCount();
    persistFilterState();
    renderLibrary();
  });

  elements.categoryFilter.addEventListener("change", (event) => {
    state.filters.category = event.target.value;
    resetVisibleCount();
    persistFilterState();
    renderLibrary();
  });

  elements.familyFilter.addEventListener("change", (event) => {
    state.filters.family = event.target.value;
    resetVisibleCount();
    persistFilterState();
    renderLibrary();
  });

  elements.detailCategory.addEventListener("change", () => {
    elements.detailCategoryCustom.value = "";
  });

  elements.bulkCategory.addEventListener("change", () => {
    elements.bulkCategoryCustom.value = "";
  });

  elements.sortSelect.addEventListener("change", (event) => {
    state.filters.sort = event.target.value;
    resetVisibleCount();
    persistFilterState();
    renderLibrary();
  });

  elements.libraryGrid.addEventListener("click", (event) => {
    const star = event.target.closest(".rating-star");
    if (star) {
      event.preventDefault();
      saveCardRating(star.closest(".card-rating").dataset.ratingPath, Number(star.dataset.rating));
      return;
    }
    const card = event.target.closest("button[data-path]");
    if (!card) {
      return;
    }

    if (state.deletingItems) return;
    const path = card.dataset.path || "";
    if (event.shiftKey || event.ctrlKey || event.metaKey) {
      event.preventDefault();
      selectCardRangeOrToggle(path, event);
      return;
    }

    state.bulkSelectedPaths = new Set([path]);
    state.selectionAnchorPath = path;
    renderBulkState();
    updateDetailPanelAnchor(card);
    state.selectedPath = card.dataset.path || "";
    closeReferenceViewer();
    renderBrowserImports();
    renderLibrary();
    renderDetail();
    window.requestAnimationFrame(() => {
      elements.libraryGrid.querySelector(`button[data-path="${cssEscape(path)}"]`)?.focus({ preventScroll: true });
    });
  });

  elements.libraryGrid.addEventListener("keydown", (event) => {
    if (!event.target.matches("button[data-path]")) return;
    if (event.key === "Delete" && state.bulkSelectedPaths.size) {
      event.preventDefault();
      deleteSelectedItems();
    } else if (event.key === "Escape") {
      event.preventDefault();
      clearAllSelections();
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
      event.preventDefault();
      elements.selectVisible.click();
    }
  });
  elements.deleteBulk.addEventListener("click", deleteSelectedItems);

  elements.selectVisible.addEventListener("click", () => {
    getVisibleItems().forEach((item) => {
      state.bulkSelectedPaths.add(item.path);
    });
    syncCardSelection();
  });

  elements.clearBulk.addEventListener("click", () => {
    state.bulkSelectedPaths.clear();
    state.selectionAnchorPath = "";
    syncCardSelection();
  });

  elements.bulkForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.deletingItems) return;
    const changes = readBulkForm();
    if (!Object.keys(changes).length) {
      showToast("一括編集する項目を入れてください。", true);
      return;
    }

    const paths = Array.from(state.bulkSelectedPaths);
    if (!paths.length) {
      showToast("先に一括編集する LoRA を選んでください。", true);
      return;
    }

    try {
      await fetchJson("/api/lora/items/bulk", {
        method: "POST",
        body: {
          paths,
          changes,
        },
      });
      applyBulkMetadata(paths, changes);
      showToast(`${paths.length}件に適用しました。`);
    } catch (error) {
      showToast(error.message, true);
    }
  });

  elements.clearSelection.addEventListener("click", () => {
    clearAllSelections();
  });

  [
    elements.libraryColumn,
    elements.libraryGrid,
    elements.emptyState,
    elements.detailColumn,
    elements.detailPanel,
    elements.detailEmpty,
  ]
    .filter(Boolean)
    .forEach((surface) => {
      surface.addEventListener("click", handleBlankSelectionClear);
    });

  elements.addReferenceImages.addEventListener("click", () => {
    if (!getSelectedItem()) {
      showToast("先に LoRA を選んでください。", true);
      return;
    }
    elements.referenceUploadInput.click();
  });

  elements.referenceUploadInput.addEventListener("change", async (event) => {
    const item = getSelectedItem();
    const files = Array.from(event.target.files || []);
    event.target.value = "";

    if (!item || !files.length) {
      return;
    }

    await uploadReferenceImages(item, files);
  });

  elements.referenceGrid.addEventListener("click", async (event) => {
    const deleteButton = event.target.closest("button[data-reference-delete]");
    if (deleteButton) {
      const item = getSelectedItem();
      const filePath = String(deleteButton.dataset.referenceDelete || "").trim();
      if (!item || !filePath) {
        return;
      }

      const referenceItem = state.referenceItems.find((entry) => entry.path === filePath);
      const fileName = referenceItem?.name || filePath.split(/[\\/]/).pop() || "参考画像";
      if (!window.confirm(`この参考画像を削除しますか？\n${fileName}`)) {
        return;
      }

      try {
        state.referenceLoading = true;
        state.referenceError = "";
        renderReferencePanel();

        const payload = await fetchJson("/api/lora/references/delete", {
          method: "POST",
          body: {
            path: item.path,
            file: filePath,
          },
        });

        state.referencePath = item.path;
        state.referenceItems = payload.items || [];
        state.referenceLoading = false;
        state.referenceError = "";
        item.reference_count = payload.count || state.referenceItems.length;
        renderLibrary();
        renderReferencePanel();
        renderReferenceViewer();
        showToast("参考メディアを削除しました。");
      } catch (error) {
        state.referenceLoading = false;
        state.referenceError = error.message;
        renderReferencePanel();
        showToast(error.message, true);
      }
      return;
    }

    const openTrigger = event.target.closest("[data-reference-open]");
    if (!openTrigger) {
      return;
    }
    event.preventDefault();
    openReferenceViewer(String(openTrigger.dataset.referenceOpen || ""));
  });

  elements.loadMore.addEventListener("click", () => {
    revealNextLibraryPage();
  });

  window.addEventListener("resize", () => {
    const selectedCard = elements.libraryGrid.querySelector(`button[data-path="${cssEscape(state.selectedPath)}"]`);
    if (selectedCard) {
      updateDetailPanelAnchor(selectedCard);
    } else {
      resetDetailPanelAnchor();
    }
  });
  window.addEventListener("scroll", scheduleAutoLoadMoreCheck, { passive: true });
  window.addEventListener("resize", scheduleAutoLoadMoreCheck, { passive: true });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refreshLibraryState({ silent: true });
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.referenceViewerPath) {
      closeReferenceViewer();
    }
    if ((event.key === "e" || event.key === "E") && state.referenceViewerPath && !event.ctrlKey && !event.altKey && !event.metaKey) {
      const target = event.target;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
      event.preventDefault();
      elements.referenceSendEncyclopedia.click();
    }
  });

  [elements.referenceViewerBackdrop, elements.referenceViewerClose].forEach((element) => {
    element.addEventListener("click", () => {
      closeReferenceViewer();
    });
  });

  elements.referenceCopyPrompt.addEventListener("click", async () => {
    const referenceItem = getSelectedReferenceItem();
    if (!referenceItem?.prompt) {
      showToast("Prompt はまだありません。", true);
      return;
    }
    await copyReferenceText(referenceItem.prompt, "Prompt をコピーしました。");
  });

  elements.referenceCopyNegative.addEventListener("click", async () => {
    const referenceItem = getSelectedReferenceItem();
    if (!referenceItem?.negative_prompt) {
      showToast("Negative prompt はまだありません。", true);
      return;
    }
    await copyReferenceText(referenceItem.negative_prompt, "Negative prompt をコピーしました。");
  });

  elements.referenceCopyAll.addEventListener("click", async () => {
    const referenceItem = getSelectedReferenceItem();
    if (!referenceItem) {
      return;
    }
    const bundle = buildReferenceCopyBundle(referenceItem);
    if (!bundle) {
      showToast("コピーできる prompt 情報がありません。", true);
      return;
    }
    await copyReferenceText(bundle, "Prompt 情報をまとめてコピーしました。");
  });

  elements.referenceSendEncyclopedia.addEventListener("click", async () => {
    const referenceItem = getSelectedReferenceItem();
    if (!referenceItem || !referenceItem.path) {
      showToast("参考画像が選択されていません。", true);
      return;
    }
    try {
      const payload = {
        path: referenceItem.path,
        prompt: referenceItem.prompt || "",
        negative_prompt: referenceItem.negative_prompt || "",
        raw_parameters: referenceItem.raw_parameters || "",
        resources_used: referenceItem.resources_used || [],
      };
      const resp = await fetch("/api/lora/encyclopedia-queue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (data.ok) {
        showToast("百科事典に送信しました。");
      } else {
        showToast("送信に失敗しました。", true);
      }
    } catch (e) {
      showToast("送信エラー: " + e.message, true);
    }
  });

  elements.detailForm.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing) {
      return;
    }
    if (event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) {
      return;
    }

    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.tagName === "TEXTAREA" || target.closest("textarea")) {
      return;
    }

    event.preventDefault();
    elements.detailForm.requestSubmit();
  });

  elements.detailForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const item = getSelectedItem();
    if (!item) {
      return;
    }

    try {
      const metadata = readDetailForm();
      await fetchJson("/api/lora/item", {
        method: "POST",
        body: {
          path: item.path,
          metadata,
        },
      });
      applySavedMetadata(item.path, metadata);
      showToast("保存しました。");
    } catch (error) {
      showToast(error.message, true);
    }
  });

  elements.copyPrompt.addEventListener("click", async () => {
    const item = getSelectedItem();
    if (!item) {
      return;
    }

    try {
      await navigator.clipboard.writeText(buildPromptSnippet(item));
      showToast("プロンプトをコピーしました。");
    } catch (error) {
      showToast("クリップボードへのコピーに失敗しました。", true);
    }
  });

  elements.replacePreview.addEventListener("click", () => {
    if (!getSelectedItem()) {
      return;
    }
    elements.previewUploadInput.value = "";
    elements.previewUploadInput.click();
  });

  elements.previewUploadInput.addEventListener("change", async (event) => {
    const item = getSelectedItem();
    const [file] = Array.from(event.target.files || []);
    event.target.value = "";

    if (!item || !file) {
      return;
    }

    await uploadPreviewImage(item, file);
  });

  elements.deletePreview.addEventListener("click", async () => {
    const item = getSelectedItem();
    if (!item) {
      return;
    }
    if (!item.preview_available) {
      showToast("削除できるサムネがまだありません。", true);
      return;
    }
    if (!window.confirm(`このサムネイルを削除しますか？\n${item.filename}`)) {
      return;
    }

    try {
      const payload = await fetchJson("/api/lora/preview/delete", {
        method: "POST",
        body: { path: item.path },
      });
      applyItemSnapshot(payload.item);
      showToast("サムネイルを削除しました。");
    } catch (error) {
      showToast(error.message, true);
    }
  });

  elements.openFolder.addEventListener("click", async () => {
    const item = getSelectedItem();
    if (!item) {
      return;
    }

    try {
      await fetchJson("/api/lora/reveal", {
        method: "POST",
        body: { path: item.path },
      });
      showToast("Explorer を開きました。");
    } catch (error) {
      showToast(error.message, true);
    }
  });

  elements.deleteItem.addEventListener("click", async () => {
    if (state.deletingItems) return;
    const item = getSelectedItem();
    if (!item) {
      return;
    }

    if (!window.confirm(`この LoRA を削除しますか？\n${item.filename}`)) {
      return;
    }

    state.deletingItems = true;
    renderBulkState();
    try {
      await fetchJson("/api/lora/delete", {
        method: "POST",
        body: { path: item.path },
      });
      applyDeletedItem(item.path);
      showToast("削除しました。");
    } catch (error) {
      showToast(error.message, true);
    } finally {
      state.deletingItems = false;
      renderBulkState();
    }
  });

  [
    elements.detailDisplayName,
    elements.detailFavorite,
    elements.detailCategory,
    elements.detailTriggers,
    elements.detailTags,
    elements.detailSourceUrl,
    elements.detailNotes,
  ].forEach((field) => {
    const eventName = field.tagName === "SELECT" || field.type === "checkbox" ? "change" : "input";
    field.addEventListener(eventName, updatePromptPreview);
  });
}

async function fetchJson(url, options = {}) {
  const request = {
    method: options.method || "GET",
    headers: {
      Accept: "application/json",
    },
  };

  if (options.body !== undefined) {
    request.headers["Content-Type"] = "application/json; charset=utf-8";
    request.body = JSON.stringify(options.body);
  }

  const response = await fetch(url, request);
  const text = await response.text();
  let payload = {};

  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      payload = { error: text };
    }
  }

  if (!response.ok) {
    throw new Error(payload.error || "通信に失敗しました。");
  }

  return payload;
}

function normalizeFontScale(value) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return 1;
  }
  return Math.min(FONT_SCALE_MAX, Math.max(FONT_SCALE_MIN, Math.round(numeric * 100) / 100));
}

function updateFontScaleControls(value = 1) {
  const normalized = normalizeFontScale(value);
  if (elements.fontScaleValue) {
    elements.fontScaleValue.textContent = `${Math.round(normalized * 100)}%`;
  }
  if (elements.fontScaleDecrease) {
    elements.fontScaleDecrease.disabled = normalized <= FONT_SCALE_MIN + 0.001;
  }
  if (elements.fontScaleIncrease) {
    elements.fontScaleIncrease.disabled = normalized >= FONT_SCALE_MAX - 0.001;
  }
  if (elements.fontScaleReset) {
    elements.fontScaleReset.disabled = Math.abs(normalized - 1) < 0.001;
  }
}

function applyFontScale(value) {
  const normalized = normalizeFontScale(value);
  document.documentElement.style.setProperty("--font-scale", String(normalized));
  updateFontScaleControls(normalized);
  return normalized;
}

function persistFontScale(value) {
  const normalized = applyFontScale(value);
  if (Math.abs(normalized - 1) < 0.001) {
    window.localStorage.removeItem(FONT_SCALE_STORAGE_KEY);
  } else {
    window.localStorage.setItem(FONT_SCALE_STORAGE_KEY, String(normalized));
  }
  return normalized;
}

function restoreFontScale() {
  const stored = window.localStorage.getItem(FONT_SCALE_STORAGE_KEY);
  applyFontScale(stored || 1);
}

function bindFontScaleControls() {
  elements.fontScaleDecrease?.addEventListener("click", () => {
    const current = normalizeFontScale(
      document.documentElement.style.getPropertyValue("--font-scale") || window.localStorage.getItem(FONT_SCALE_STORAGE_KEY) || 1
    );
    persistFontScale(current - FONT_SCALE_STEP);
  });
  elements.fontScaleIncrease?.addEventListener("click", () => {
    const current = normalizeFontScale(
      document.documentElement.style.getPropertyValue("--font-scale") || window.localStorage.getItem(FONT_SCALE_STORAGE_KEY) || 1
    );
    persistFontScale(current + FONT_SCALE_STEP);
  });
  elements.fontScaleReset?.addEventListener("click", () => {
    persistFontScale(1);
  });
}

function getDetailWidthUpperBound() {
  const viewportWidth = Math.max(window.innerWidth || 0, document.documentElement?.clientWidth || 0, DETAIL_WIDTH_MIN);
  return Math.max(DETAIL_WIDTH_MIN, Math.min(DETAIL_WIDTH_MAX, Math.floor(viewportWidth * DETAIL_WIDTH_RATIO_MAX)));
}

function normalizeDetailPanelWidth(value) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return 0;
  }
  return Math.min(getDetailWidthUpperBound(), Math.max(DETAIL_WIDTH_MIN, Math.round(numeric)));
}

function getCurrentDetailPanelWidth() {
  if (elements.detailColumn) {
    const width = Math.round(elements.detailColumn.getBoundingClientRect().width || 0);
    if (width > 0) {
      return width;
    }
  }
  const inlineWidth = parseFloat(elements.appShell?.style.getPropertyValue("--detail-column-width") || "");
  return normalizeDetailPanelWidth(inlineWidth);
}

function applyDetailPanelWidth(value) {
  if (!elements.appShell) {
    return 0;
  }

  const normalized = normalizeDetailPanelWidth(value);
  if (!normalized) {
    elements.appShell.style.removeProperty("--detail-column-width");
    if (elements.detailResizer) {
      elements.detailResizer.removeAttribute("aria-valuenow");
    }
    return 0;
  }

  elements.appShell.style.setProperty("--detail-column-width", `${normalized}px`);
  if (elements.detailResizer) {
    elements.detailResizer.setAttribute("aria-valuemin", String(DETAIL_WIDTH_MIN));
    elements.detailResizer.setAttribute("aria-valuemax", String(getDetailWidthUpperBound()));
    elements.detailResizer.setAttribute("aria-valuenow", String(normalized));
  }
  return normalized;
}

function persistDetailPanelWidth(value) {
  const normalized = applyDetailPanelWidth(value);
  if (normalized) {
    window.localStorage.setItem(DETAIL_WIDTH_STORAGE_KEY, String(normalized));
  } else {
    window.localStorage.removeItem(DETAIL_WIDTH_STORAGE_KEY);
  }
  return normalized;
}

function restoreDetailPanelWidth() {
  const stored = normalizeDetailPanelWidth(window.localStorage.getItem(DETAIL_WIDTH_STORAGE_KEY));
  if (stored) {
    applyDetailPanelWidth(stored);
  } else {
    window.localStorage.removeItem(DETAIL_WIDTH_STORAGE_KEY);
    applyDetailPanelWidth(0);
  }
}

function bindDetailResizer() {
  const handle = elements.detailResizer;
  if (!handle || !elements.appShell) {
    return;
  }

  let activePointerId = null;

  const finishResize = () => {
    if (activePointerId == null) {
      return;
    }
    if (handle.hasPointerCapture?.(activePointerId)) {
      handle.releasePointerCapture(activePointerId);
    }
    activePointerId = null;
    document.body.classList.remove("is-resizing-detail");
    persistDetailPanelWidth(getCurrentDetailPanelWidth());
  };

  const updateFromPointer = (event) => {
    if (activePointerId == null || event.pointerId !== activePointerId) {
      return;
    }
    const shellRect = elements.appShell.getBoundingClientRect();
    const nextWidth = shellRect.right - event.clientX;
    applyDetailPanelWidth(nextWidth);
    event.preventDefault();
  };

  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || window.matchMedia("(max-width: 960px)").matches) {
      return;
    }
    activePointerId = event.pointerId;
    handle.setPointerCapture?.(activePointerId);
    document.body.classList.add("is-resizing-detail");
    updateFromPointer(event);
    event.preventDefault();
  });

  handle.addEventListener("pointermove", updateFromPointer);
  handle.addEventListener("pointerup", finishResize);
  handle.addEventListener("pointercancel", finishResize);
  handle.addEventListener("lostpointercapture", finishResize);
  handle.addEventListener("dblclick", () => {
    persistDetailPanelWidth(0);
  });
  handle.addEventListener("keydown", (event) => {
    if (window.matchMedia("(max-width: 960px)").matches) {
      return;
    }
    let nextWidth = 0;
    if (event.key === "ArrowLeft") {
      nextWidth = getCurrentDetailPanelWidth() + 24;
    } else if (event.key === "ArrowRight") {
      nextWidth = getCurrentDetailPanelWidth() - 24;
    } else if (event.key === "Home") {
      nextWidth = DETAIL_WIDTH_MIN;
    } else if (event.key === "End") {
      nextWidth = getDetailWidthUpperBound();
    } else if (event.key === "Escape") {
      persistDetailPanelWidth(0);
      event.preventDefault();
      return;
    } else {
      return;
    }
    persistDetailPanelWidth(nextWidth);
    event.preventDefault();
  });

  window.addEventListener("resize", () => {
    const stored = normalizeDetailPanelWidth(window.localStorage.getItem(DETAIL_WIDTH_STORAGE_KEY));
    if (stored) {
      persistDetailPanelWidth(stored);
    } else {
      applyDetailPanelWidth(0);
    }
  });
}

function getReferenceItemByPath(path) {
  const targetPath = String(path || "").trim();
  if (!targetPath) {
    return null;
  }
  return state.referenceItems.find((item) => item.path === targetPath) || null;
}

function getSelectedReferenceItem() {
  return getReferenceItemByPath(state.referenceViewerPath);
}

function buildReferenceGenerationText(referenceItem) {
  if (!referenceItem) {
    return "";
  }

  const lines = [];
  if (referenceItem.steps) {
    lines.push(`Steps: ${referenceItem.steps}`);
  }
  if (referenceItem.sampler) {
    lines.push(`Sampler: ${referenceItem.sampler}`);
  }
  if (referenceItem.cfg_scale) {
    lines.push(`CFG: ${referenceItem.cfg_scale}`);
  }
  if (referenceItem.seed) {
    lines.push(`Seed: ${referenceItem.seed}`);
  }
  return lines.join("\n");
}

function formatReferenceResourceType(value) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }

  const normalized = text.toLowerCase().replace(/[^a-z0-9]+/g, "");
  if (normalized === "textualinversion") {
    return "Embedding";
  }
  if (normalized === "lora") {
    return "LoRA";
  }
  if (normalized === "checkpoint") {
    return "Checkpoint";
  }
  return text;
}

function buildReferenceResourcesText(referenceItem) {
  const resources = Array.isArray(referenceItem?.resources_used) ? referenceItem.resources_used : [];
  if (!resources.length) {
    return "";
  }

  return resources
    .map((resource) => {
      const title = String(resource.name || resource.version_name || resource.type || "Resource").trim();
      const details = [
        formatReferenceResourceType(resource.type),
        String(resource.version_name || "").trim(),
        String(resource.base_model || "").trim(),
        String(resource.strength || "").trim() ? `strength ${String(resource.strength).trim()}` : "",
      ].filter(Boolean);
      const lines = [details.length ? `- ${title} (${details.join(" / ")})` : `- ${title}`];
      const trainedWords = Array.isArray(resource.trained_words) ? resource.trained_words.filter(Boolean) : [];
      if (resource.url) {
        lines.push(`  ${resource.url}`);
      }
      if (trainedWords.length) {
        lines.push(`  Trained words: ${trainedWords.join(", ")}`);
      }
      return lines.join("\n");
    })
    .join("\n");
}

function buildReferenceCopyBundle(referenceItem) {
  if (!referenceItem) {
    return "";
  }

  const sections = [];
  const generationText = buildReferenceGenerationText(referenceItem);
  if (generationText) {
    sections.push(`Generation:\n${generationText}`);
  }
  const resourcesText = buildReferenceResourcesText(referenceItem);
  if (resourcesText) {
    sections.push(`Resources used:\n${resourcesText}`);
  }
  if (referenceItem.prompt) {
    sections.push(`Prompt:\n${referenceItem.prompt}`);
  }
  if (referenceItem.negative_prompt) {
    sections.push(`Negative prompt:\n${referenceItem.negative_prompt}`);
  }
  if (referenceItem.raw_parameters) {
    sections.push(`Parameters:\n${referenceItem.raw_parameters}`);
  }
  if (referenceItem.prompt_json) {
    sections.push(`Prompt JSON:\n${referenceItem.prompt_json}`);
  }
  if (referenceItem.workflow_json) {
    sections.push(`Workflow JSON:\n${referenceItem.workflow_json}`);
  }
  return sections.join("\n\n").trim();
}

async function copyReferenceText(text, successMessage) {
  try {
    await navigator.clipboard.writeText(String(text || ""));
    showToast(successMessage);
  } catch (error) {
    showToast("クリップボードへのコピーに失敗しました。", true);
  }
}

function isReferenceVideoItem(referenceItem) {
  if (!referenceItem || typeof referenceItem !== "object") {
    return false;
  }

  const mediaKind = String(referenceItem.media_kind || "").trim().toLowerCase();
  if (mediaKind) {
    return mediaKind === "video";
  }
  return REFERENCE_VIDEO_FILE_EXTENSION_RE.test(String(referenceItem.name || ""));
}

function setReferenceViewerMedia(referenceItem) {
  const isVideo = isReferenceVideoItem(referenceItem);

  if (elements.referenceViewerImage) {
    if (isVideo) {
      elements.referenceViewerImage.hidden = true;
      elements.referenceViewerImage.removeAttribute("src");
      elements.referenceViewerImage.alt = "参考画像プレビュー";
    } else {
      elements.referenceViewerImage.hidden = false;
      elements.referenceViewerImage.src = referenceItem.url;
      elements.referenceViewerImage.alt = referenceItem.name || "reference image";
    }
  }

  if (elements.referenceViewerVideo) {
    if (isVideo) {
      elements.referenceViewerVideo.hidden = false;
      elements.referenceViewerVideo.src = referenceItem.url;
      elements.referenceViewerVideo.preload = "metadata";
      elements.referenceViewerVideo.muted = true;
      void elements.referenceViewerVideo.play().catch(() => {});
    } else {
      elements.referenceViewerVideo.pause();
      elements.referenceViewerVideo.hidden = true;
      elements.referenceViewerVideo.removeAttribute("src");
      elements.referenceViewerVideo.load();
    }
  }
}

function resetReferenceViewerMedia() {
  if (elements.referenceViewerImage) {
    elements.referenceViewerImage.hidden = true;
    elements.referenceViewerImage.removeAttribute("src");
    elements.referenceViewerImage.alt = "参考画像プレビュー";
  }

  if (elements.referenceViewerVideo) {
    elements.referenceViewerVideo.pause();
    elements.referenceViewerVideo.hidden = true;
    elements.referenceViewerVideo.removeAttribute("src");
    elements.referenceViewerVideo.load();
  }
}

function setReferenceViewerVisibility(isVisible) {
  elements.referenceViewer.hidden = !isVisible;
  elements.referenceViewer.setAttribute("aria-hidden", isVisible ? "false" : "true");
  document.body.classList.toggle("reference-viewer-open", isVisible);
  if (!isVisible) {
    resetReferenceViewerMedia();
  }
}

function buildReferenceMetaCard(label, value) {
  const card = document.createElement("article");
  card.className = "reference-viewer-meta-card";

  const kicker = document.createElement("span");
  kicker.className = "reference-viewer-meta-label";
  kicker.textContent = label;

  const body = document.createElement("strong");
  body.className = "reference-viewer-meta-value";
  body.textContent = value;

  card.append(kicker, body);
  return card;
}

function buildReferenceViewerSection(title, value) {
  const text = String(value || "").trim();
  if (!text) {
    return null;
  }

  const section = document.createElement("section");
  section.className = "reference-viewer-section";

  const heading = document.createElement("h3");
  heading.className = "reference-viewer-section-title";
  heading.textContent = title;

  const code = document.createElement("pre");
  code.className = "reference-viewer-code mono";
  code.textContent = text;

  section.append(heading, code);
  return section;
}

function buildReferenceResourcesSection(resources) {
  const entries = Array.isArray(resources) ? resources.filter((resource) => resource && typeof resource === "object") : [];
  if (!entries.length) {
    return null;
  }

  const section = document.createElement("section");
  section.className = "reference-viewer-section";

  const heading = document.createElement("h3");
  heading.className = "reference-viewer-section-title";
  heading.textContent = "Resources used";

  const list = document.createElement("div");
  list.className = "reference-viewer-resource-list";

  entries.forEach((resource) => {
    const card = document.createElement("article");
    card.className = "reference-viewer-resource-card";

    const title = document.createElement(resource.url ? "a" : "strong");
    title.className = "reference-viewer-resource-title";
    title.textContent = resource.name || resource.version_name || resource.type || "Resource";
    if (resource.url) {
      title.href = resource.url;
      title.target = "_blank";
      title.rel = "noreferrer";
    }
    card.append(title);

    const versionName = String(resource.version_name || "").trim();
    if (versionName) {
      const subtitle = document.createElement("p");
      subtitle.className = "reference-viewer-resource-subtitle";
      subtitle.textContent = versionName;
      card.append(subtitle);
    }

    const meta = document.createElement("div");
    meta.className = "reference-viewer-resource-meta";
    const metaValues = [
      formatReferenceResourceType(resource.type),
      String(resource.base_model || "").trim(),
      String(resource.strength || "").trim() ? `Strength ${String(resource.strength).trim()}` : "",
    ].filter(Boolean);
    metaValues.forEach((value) => {
      meta.append(createBadge(value, "plain"));
    });
    if (meta.childElementCount) {
      card.append(meta);
    }

    const trainedWords = Array.isArray(resource.trained_words) ? resource.trained_words.filter(Boolean) : [];
    if (trainedWords.length) {
      const triggerLine = document.createElement("p");
      triggerLine.className = "reference-viewer-resource-note";
      triggerLine.textContent = `Trained words: ${trainedWords.join(", ")}`;
      card.append(triggerLine);
    }

    if (resource.url) {
      const linkLine = document.createElement("p");
      linkLine.className = "reference-viewer-resource-linkline";

      const link = document.createElement("a");
      link.className = "reference-viewer-resource-link";
      link.href = resource.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = resource.url;

      linkLine.append(link);
      card.append(linkLine);
    }

    list.append(card);
  });

  section.append(heading, list);
  return section;
}

function setViewerActionLink(element, href) {
  const url = String(href || "").trim();
  element.hidden = !url;
  if (url) {
    element.href = url;
  } else {
    element.removeAttribute("href");
  }
}

function buildSourceLinkLabel(href) {
  const url = String(href || "").trim();
  if (!url) {
    return "元ページを開く";
  }
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./i, "");
    return host ? `${host} を開く` : "元ページを開く";
  } catch (error) {
    return "元ページを開く";
  }
}

function renderReferenceViewer() {
  const referenceItem = getSelectedReferenceItem();
  if (!referenceItem) {
    state.referenceViewerPath = "";
    setReferenceViewerVisibility(false);
    return;
  }

  setReferenceViewerVisibility(true);
  setReferenceViewerMedia(referenceItem);
  elements.referenceViewerTitle.textContent = referenceItem.source_name || referenceItem.name || "参考画像";
  elements.referenceViewerSubtitle.textContent = [
    referenceItem.name || "",
    formatDate(referenceItem.modified_at),
    formatFileSize(referenceItem.size_bytes),
  ]
    .filter(Boolean)
    .join(" / ");

  elements.referenceViewerBadges.innerHTML = "";
  elements.referenceViewerBadges.append(createBadge(isReferenceVideoItem(referenceItem) ? "Video" : "Image", "plain"));
  if (referenceItem.metadata_source) {
    elements.referenceViewerBadges.append(createBadge(referenceItem.metadata_source, "plain"));
  }
  if (referenceItem.source_host) {
    elements.referenceViewerBadges.append(createBadge(referenceItem.source_host, "sage"));
  }
  if (referenceItem.base_model) {
    elements.referenceViewerBadges.append(createBadge(referenceItem.base_model, "gold"));
  }
  if (referenceItem.has_prompt) {
    elements.referenceViewerBadges.append(createBadge("Prompt", "accent"));
  }

  elements.referenceViewerMetaGrid.innerHTML = "";
  [
    ["Type", isReferenceVideoItem(referenceItem) ? "Video" : "Image"],
    ["Steps", referenceItem.steps],
    ["Sampler", referenceItem.sampler],
    ["CFG", referenceItem.cfg_scale],
    ["Seed", referenceItem.seed],
    ["Resources", Array.isArray(referenceItem.resources_used) && referenceItem.resources_used.length ? String(referenceItem.resources_used.length) : ""],
    ["Source", referenceItem.source_name || referenceItem.source_host],
    ["Author", referenceItem.author],
    ["Base Model", referenceItem.base_model],
    ["Metadata", referenceItem.metadata_source],
    ["Updated", formatDate(referenceItem.modified_at)],
    ["Size", formatFileSize(referenceItem.size_bytes)],
  ].forEach(([label, value]) => {
    const text = String(value || "").trim();
    if (text) {
      elements.referenceViewerMetaGrid.append(buildReferenceMetaCard(label, text));
    }
  });

  elements.referenceViewerDescription.hidden = !referenceItem.source_description;
  elements.referenceViewerDescription.textContent = referenceItem.source_description || "";

  setViewerActionLink(elements.referenceOpenImage, referenceItem.url);
  setViewerActionLink(elements.referenceOpenSource, referenceItem.source_url);
  elements.referenceCopyPrompt.disabled = !referenceItem.prompt;
  elements.referenceCopyNegative.disabled = !referenceItem.negative_prompt;
  elements.referenceCopyAll.disabled = !buildReferenceCopyBundle(referenceItem);

  elements.referenceViewerSections.innerHTML = "";
  const generationText = buildReferenceGenerationText(referenceItem);
  const sections = [
    buildReferenceViewerSection("Generation", generationText),
    buildReferenceResourcesSection(referenceItem.resources_used),
    buildReferenceViewerSection("Prompt", referenceItem.prompt),
    buildReferenceViewerSection("Negative Prompt", referenceItem.negative_prompt),
    buildReferenceViewerSection("Parameters", referenceItem.raw_parameters),
    buildReferenceViewerSection("Prompt JSON", referenceItem.prompt_json),
    buildReferenceViewerSection("Workflow JSON", referenceItem.workflow_json),
  ].filter(Boolean);

  if (!sections.length) {
    elements.referenceViewerSections.append(buildEmptyCard("この参考画像には表示できる prompt メタ情報がまだありません。"));
    return;
  }

  sections.forEach((section) => {
    elements.referenceViewerSections.append(section);
  });
}

function openReferenceViewer(path) {
  const referenceItem = getReferenceItemByPath(path);
  if (!referenceItem) {
    showToast("参考メディアが見つかりません。", true);
    return;
  }

  state.referenceViewerPath = referenceItem.path;
  renderReferenceViewer();
}

function closeReferenceViewer() {
  if (!state.referenceViewerPath && elements.referenceViewer.hidden) {
    return;
  }

  state.referenceViewerPath = "";
  setReferenceViewerVisibility(false);
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error(`${file.name || "ファイル"} の読み込みに失敗しました。`));
    reader.readAsDataURL(file);
  });
}

function isReferenceMediaFileLike(file) {
  if (!file) {
    return false;
  }
  const fileType = String(file.type || "").toLowerCase();
  if (fileType.startsWith("image/") || fileType.startsWith("video/")) {
    return true;
  }
  return REFERENCE_MEDIA_FILE_EXTENSION_RE.test(String(file.name || ""));
}

function isImageFileLike(file) {
  if (!file) {
    return false;
  }
  if (String(file.type || "").toLowerCase().startsWith("image/")) {
    return true;
  }
  return IMAGE_FILE_EXTENSION_RE.test(String(file.name || ""));
}

function isPreviewMediaFileLike(file) {
  if (!file) {
    return false;
  }
  const fileType = String(file.type || "").toLowerCase();
  if (fileType.startsWith("image/") || fileType.startsWith("video/")) {
    return true;
  }
  return REFERENCE_MEDIA_FILE_EXTENSION_RE.test(String(file.name || ""));
}

function collectReferenceMediaFilesFromDataTransfer(dataTransfer) {
  const files = [];
  Array.from(dataTransfer?.files || []).forEach((file) => {
    if (isReferenceMediaFileLike(file)) {
      files.push(file);
    }
  });
  return files;
}

function collectImageFilesFromDataTransfer(dataTransfer) {
  const files = [];
  Array.from(dataTransfer?.files || []).forEach((file) => {
    if (isImageFileLike(file)) {
      files.push(file);
    }
  });
  return files;
}

function collectPreviewMediaFilesFromDataTransfer(dataTransfer) {
  const files = [];
  Array.from(dataTransfer?.files || []).forEach((file) => {
    if (isPreviewMediaFileLike(file)) {
      files.push(file);
    }
  });
  return files;
}

function isPreviewVideoItem(item) {
  if (!item || typeof item !== "object") {
    return false;
  }

  const mediaKind = String(item.preview_media_kind || "").trim().toLowerCase();
  if (mediaKind) {
    return mediaKind === "video";
  }
  return REFERENCE_VIDEO_FILE_EXTENSION_RE.test(String(item.preview_url || ""));
}

async function uploadReferenceImages(item, files) {
  const imageFiles = Array.from(files || []).filter((file) => isReferenceMediaFileLike(file));
  if (!item || !imageFiles.length) {
    if (item) {
      showToast("画像または動画ファイルを追加してください。", true);
    }
    return;
  }

  state.referencePath = item.path;
  state.referenceError = "";
  renderReferencePanel();
  imageFiles.forEach((file) => {
    state.referenceUploadJobSerial += 1;
    state.referenceUploadQueue.push({
      id: state.referenceUploadJobSerial,
      targetPath: item.path,
      file,
    });
  });
  renderReferencePanel();
  void processReferenceUploadQueue();

  const queuedCount = countQueuedReferenceJobs();
  showToast(
    imageFiles.length === 1
      ? `参考メディアをキューに追加しました。現在 ${queuedCount} 件待ちです。`
      : `${imageFiles.length}件の参考メディアをキューに追加しました。現在 ${queuedCount} 件待ちです。`
  );
}

async function uploadPreviewImage(item, file) {
  if (!item) {
    return;
  }
  if (!isPreviewMediaFileLike(file)) {
    showToast("画像または動画ファイルを選んでください。", true);
    return;
  }

  try {
    const payload = await fetchJson("/api/lora/preview/upload", {
      method: "POST",
      body: {
        path: item.path,
        file: {
          name: file.name,
          data_url: await readFileAsDataUrl(file),
        },
      },
    });
    applyItemSnapshot(payload.item);
    showToast("サムネイルを更新しました。");
  } catch (error) {
    showToast(error.message, true);
  }
}

function normalizeHttpUrl(value) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }
  try {
    const url = new URL(text);
    if (!/^https?:$/i.test(url.protocol)) {
      return "";
    }
    return url.toString();
  } catch (error) {
    return "";
  }
}

function extractDroppedMediaUrl(dataTransfer) {
  const uriList = String(dataTransfer?.getData("text/uri-list") || "");
  const uriCandidate = uriList
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line && !line.startsWith("#"));
  const normalizedUri = normalizeHttpUrl(uriCandidate);
  if (normalizedUri) {
    return normalizedUri;
  }
  return normalizeHttpUrl(dataTransfer?.getData("text/plain") || "");
}

async function uploadPreviewUrl(item, url) {
  if (!item) {
    return;
  }
  const normalizedUrl = normalizeHttpUrl(url);
  if (!normalizedUrl) {
    showToast("有効な画像または動画の URL をドロップしてください。", true);
    return;
  }

  try {
    const payload = await fetchJson("/api/lora/preview/upload", {
      method: "POST",
      body: {
        path: item.path,
        url: normalizedUrl,
      },
    });
    applyItemSnapshot(payload.item);
    showToast("サムネイルを更新しました。");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function saveWatchDirs(nextDirs) {
  try {
    const payload = await fetchJson("/api/lora/config", {
      method: "POST",
      body: { watch_dirs: nextDirs },
    });
    state.watchDirs = payload.watch_dirs || [];
    state.items = [];
    state.stats = null;
    state.scannedAt = "";
    state.scanDurationMs = 0;
    state.scanStatus = state.watchDirs.length ? "scanning" : "idle";
    state.scanError = "";
    state.selectedPath = "";
    state.bulkSelectedPaths.clear();
    resetVisibleCount();
    renderWatchDirs();
    renderBrowserImports();
    renderBulkState();
    renderStats();
    renderLibrary();
    renderDetail();
    showToast("登録フォルダを更新しました。");
    await startLibraryScan({ preserveSelection: false, force: true });
  } catch (error) {
    showToast(error.message, true);
  }
}

async function addWatchDir(path) {
  await addWatchDirs([path]);
}

async function addWatchDirs(paths) {
  const nextPaths = Array.from(
    new Set(
      (Array.isArray(paths) ? paths : [paths])
        .map((entry) => normalizeWatchDirInput(entry))
        .filter(Boolean)
    )
  );
  if (!nextPaths.length) {
    showToast("フォルダパスを入れてください。", true);
    return;
  }

  await saveWatchDirs([...state.watchDirs, ...nextPaths]);
  elements.folderInput.value = "";
}

function bindGlobalFileDropGuards() {
  window.addEventListener("dragover", (event) => {
    if (!eventHasProtectedDropPayload(event)) {
      return;
    }
    event.preventDefault();
  });

  window.addEventListener("drop", (event) => {
    if (!eventHasProtectedDropPayload(event)) {
      return;
    }
    event.preventDefault();
  });
}

function bindWatchFolderDropZone() {
  const zone = elements.folderDropZone;
  if (!zone) {
    return;
  }

  zone.addEventListener("click", async () => {
    await browseAndAddFolder();
  });

  const activate = (event) => {
    if (!eventHasExternalFiles(event)) {
      return;
    }
    event.preventDefault();
    setWatchFolderDropActive(true, "ドロップすると登録フォルダに追加します。");
  };

  zone.addEventListener("dragenter", activate);
  zone.addEventListener("dragover", (event) => {
    if (!eventHasExternalFiles(event)) {
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "copy";
    }
    setWatchFolderDropActive(true, "ドロップすると登録フォルダに追加します。");
  });

  zone.addEventListener("dragleave", (event) => {
    if (!zone.contains(event.relatedTarget)) {
      setWatchFolderDropActive(false);
    }
  });

  zone.addEventListener("drop", async (event) => {
    event.preventDefault();
    setWatchFolderDropActive(false);

    const droppedPaths = await extractWatchDirPathsFromDrop(event.dataTransfer);
    if (!droppedPaths.length) {
      showToast("フォルダの場所を読み取れませんでした。難しいときは「参照して追加」を使ってください。", true);
      return;
    }

    elements.folderInput.value = droppedPaths[0];
    await addWatchDirs(droppedPaths);
  });

  zone.addEventListener("keydown", async (event) => {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    await browseAndAddFolder();
  });
}

function bindReferenceDropZone() {
  const zone = elements.referenceDropZone;
  if (!zone) {
    return;
  }

  zone.addEventListener("click", () => {
    const item = getSelectedItem();
    if (!item) {
      showToast("先に LoRA を選んでください。", true);
      return;
    }
    elements.referenceUploadInput.click();
  });

  const activate = (event) => {
    if (!eventHasExternalFiles(event)) {
      return;
    }
    event.preventDefault();
    setReferenceDropActive(true, "ドロップすると参考画像や動画に追加します。");
  };

  zone.addEventListener("dragenter", activate);
  zone.addEventListener("dragover", (event) => {
    if (!eventHasExternalFiles(event)) {
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "copy";
    }
    setReferenceDropActive(true, "ドロップすると参考画像や動画に追加します。");
  });

  zone.addEventListener("dragleave", (event) => {
    if (!zone.contains(event.relatedTarget)) {
      setReferenceDropActive(false);
    }
  });

  zone.addEventListener("drop", async (event) => {
    event.preventDefault();
    setReferenceDropActive(false);

    const item = getSelectedItem();
    if (!item) {
      showToast("先に LoRA を選んでください。", true);
      return;
    }

    const files = collectReferenceMediaFilesFromDataTransfer(event.dataTransfer);
    if (!files.length) {
      showToast("画像または動画ファイルをドロップしてください。", true);
      return;
    }

    await uploadReferenceImages(item, files);
  });

  zone.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    const item = getSelectedItem();
    if (!item) {
      showToast("先に LoRA を選んでください。", true);
      return;
    }
    elements.referenceUploadInput.click();
  });
}

function bindPreviewDropZone() {
  const zone = elements.detailPreviewShell;
  if (!zone) {
    return;
  }

  const requestUpload = () => {
    const item = getSelectedItem();
    if (!item) {
      showToast("先に LoRA を選んでください。", true);
      return false;
    }
    elements.previewUploadInput.value = "";
    elements.previewUploadInput.click();
    return true;
  };

  zone.addEventListener("click", () => {
    requestUpload();
  });

  const activate = (event) => {
    if (!eventHasExternalFiles(event)) {
      return;
    }
    event.preventDefault();
    setPreviewDropActive(true);
  };

  zone.addEventListener("dragenter", activate);
  zone.addEventListener("dragover", (event) => {
    if (!eventHasExternalFiles(event)) {
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "copy";
    }
    setPreviewDropActive(true);
  });

  zone.addEventListener("dragleave", (event) => {
    if (!zone.contains(event.relatedTarget)) {
      setPreviewDropActive(false);
    }
  });

  zone.addEventListener("drop", async (event) => {
    event.preventDefault();
    setPreviewDropActive(false);

    const item = getSelectedItem();
    if (!item) {
      showToast("先に LoRA を選んでください。", true);
      return;
    }

    const files = collectPreviewMediaFilesFromDataTransfer(event.dataTransfer);
    if (files.length) {
      await uploadPreviewImage(item, files[0]);
      return;
    }

    const mediaUrl = extractDroppedMediaUrl(event.dataTransfer);
    if (!mediaUrl) {
      showToast("画像または動画ファイル、または直接 URL をドロップしてください。", true);
      return;
    }

    await uploadPreviewUrl(item, mediaUrl);
  });

  zone.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    requestUpload();
  });
}

function setWatchFolderDropActive(active, message = "") {
  if (!elements.folderDropZone || !elements.folderDropHint) {
    return;
  }
  elements.folderDropZone.classList.toggle("is-active", Boolean(active));
  elements.folderDropHint.textContent = active
    ? message || "ドロップすると登録フォルダに追加します。"
    : "Explorer からフォルダを落とすと、そのまま登録に追加します。";
}

function setReferenceDropActive(active, message = "") {
  if (!elements.referenceDropZone || !elements.referenceDropHint) {
    return;
  }
  elements.referenceDropZone.classList.toggle("is-active", Boolean(active));
  elements.referenceDropHint.textContent = active
    ? message || "ドロップすると参考画像や動画に追加します。"
    : "フォルダから画像や動画を落とすか、クリックして追加できます。";
}

function setPreviewDropActive(active) {
  if (!elements.detailPreviewShell) {
    return;
  }
  elements.detailPreviewShell.classList.toggle("is-active", Boolean(active));
}

function eventHasExternalFiles(event) {
  const types = Array.from(event?.dataTransfer?.types || []);
  return types.includes("Files") || types.includes("text/uri-list") || types.includes("text/plain");
}

function eventHasProtectedDropPayload(event) {
  const types = Array.from(event?.dataTransfer?.types || []);
  return types.includes("Files") || types.includes("text/uri-list");
}

async function extractWatchDirPathsFromDrop(dataTransfer) {
  if (!dataTransfer) {
    return [];
  }

  const filePaths = collectDroppedFilePaths(dataTransfer);
  const droppedFileKeys = new Set(filePaths.map((entry) => entry.toLowerCase()));
  const uriPaths = [
    ...extractTransferPathsFromText(dataTransfer.getData("text/uri-list")),
    ...extractTransferPathsFromText(dataTransfer.getData("text/plain")),
  ];

  const directPaths = uriPaths.map((entry) => {
    if (droppedFileKeys.has(entry.toLowerCase())) {
      return getParentDirectoryPath(entry);
    }
    return stripTrailingSeparators(entry);
  });

  if (directPaths.length) {
    return Array.from(new Set(directPaths.filter(Boolean)));
  }

  const entryPaths = await collectDroppedDirectoryPaths(dataTransfer);
  if (entryPaths.length) {
    return Array.from(new Set(entryPaths.filter(Boolean)));
  }

  const parentDirs = filePaths.map((entry) => getParentDirectoryPath(entry)).filter(Boolean);
  return Array.from(new Set(parentDirs));
}

function collectDroppedFilePaths(dataTransfer) {
  const filePaths = [];
  const pushFilePath = (rawPath) => {
    const normalized = normalizeDroppedLocalPath(rawPath);
    if (normalized) {
      filePaths.push(normalized);
    }
  };

  Array.from(dataTransfer.files || []).forEach((file) => {
    pushFilePath(file?.path);
  });

  Array.from(dataTransfer.items || []).forEach((item) => {
    if (item.kind !== "file") {
      return;
    }
    const file = item.getAsFile();
    pushFilePath(file?.path);
  });

  return Array.from(new Set(filePaths));
}

async function collectDroppedDirectoryPaths(dataTransfer) {
  const paths = [];
  for (const item of Array.from(dataTransfer?.items || [])) {
    if (item.kind !== "file") {
      continue;
    }

    const entry = getDropItemEntry(item);
    if (!entry?.isDirectory) {
      continue;
    }

    const resolved = await resolveDirectoryEntryPath(entry);
    if (resolved) {
      paths.push(resolved);
    }
  }
  return Array.from(new Set(paths));
}

function getDropItemEntry(item) {
  if (typeof item?.webkitGetAsEntry === "function") {
    try {
      return item.webkitGetAsEntry();
    } catch (_error) {
      return null;
    }
  }
  return null;
}

function fileEntryToFile(entry) {
  return new Promise((resolve) => {
    if (!entry?.isFile || typeof entry.file !== "function") {
      resolve(null);
      return;
    }
    try {
      entry.file(
        (file) => resolve(file || null),
        () => resolve(null),
      );
    } catch (_error) {
      resolve(null);
    }
  });
}

function readDirectoryEntries(entry) {
  return new Promise((resolve) => {
    if (!entry?.isDirectory || typeof entry.createReader !== "function") {
      resolve([]);
      return;
    }

    const reader = entry.createReader();
    const items = [];
    const pump = () => {
      try {
        reader.readEntries(
          (entries) => {
            if (!entries?.length) {
              resolve(items);
              return;
            }
            items.push(...entries);
            pump();
          },
          () => resolve(items),
        );
      } catch (_error) {
        resolve(items);
      }
    };
    pump();
  });
}

async function findSampleFileForEntry(entry) {
  if (!entry) {
    return null;
  }

  if (entry.isFile) {
    const file = await fileEntryToFile(entry);
    const path = normalizeDroppedLocalPath(file?.path);
    return path ? { entry, path } : null;
  }

  if (!entry.isDirectory) {
    return null;
  }

  const children = await readDirectoryEntries(entry);
  children.sort((left, right) => String(left?.name || "").localeCompare(String(right?.name || "")));
  for (const child of children) {
    const sample = await findSampleFileForEntry(child);
    if (sample?.path) {
      return sample;
    }
  }

  return null;
}

async function resolveDirectoryEntryPath(directoryEntry) {
  const sample = await findSampleFileForEntry(directoryEntry);
  if (!sample?.path) {
    return "";
  }

  const fullPath = String(sample.entry?.fullPath || "").replace(/\//g, "\\");
  const relativeParts = fullPath.replace(/^\\+/, "").split("\\").filter(Boolean);
  if (relativeParts.length > 1) {
    const suffix = `\\${relativeParts.slice(1).join("\\")}`;
    if (sample.path.toLowerCase().endsWith(suffix.toLowerCase())) {
      return stripTrailingSeparators(sample.path.slice(0, -suffix.length));
    }
  }

  const rootName = String(directoryEntry.name || "").trim();
  if (rootName) {
    const lowerSample = sample.path.toLowerCase();
    const marker = `\\${rootName.toLowerCase()}\\`;
    const markerIndex = lowerSample.lastIndexOf(marker);
    if (markerIndex >= 0) {
      return stripTrailingSeparators(sample.path.slice(0, markerIndex + marker.length - 1));
    }
  }

  return getParentDirectoryPath(sample.path);
}

function extractTransferPathsFromText(rawText) {
  if (!rawText) {
    return [];
  }

  return String(rawText)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .map((line) => normalizeDroppedLocalPath(line))
    .filter(Boolean);
}

function normalizeDroppedLocalPath(rawValue) {
  const normalized = normalizeWatchDirInput(rawValue);
  if (!normalized) {
    return "";
  }

  if (!WINDOWS_DRIVE_PATH_RE.test(normalized) && !UNC_PATH_RE.test(normalized)) {
    return "";
  }

  return stripTrailingSeparators(normalized);
}

function normalizeWatchDirInput(rawValue) {
  let normalized = String(rawValue || "").trim();
  if (!normalized) {
    return "";
  }

  if (normalized.startsWith("\"") && normalized.endsWith("\"")) {
    normalized = normalized.slice(1, -1).trim();
  }

  if (FILE_URL_RE.test(normalized)) {
    normalized = decodeFileUrlToPath(normalized);
  }

  return String(normalized || "").trim().replace(/\//g, "\\");
}

function decodeFileUrlToPath(rawUrl) {
  try {
    const parsed = new URL(rawUrl);
    if (parsed.protocol !== "file:") {
      return "";
    }

    let pathname = decodeURIComponent(parsed.pathname || "");
    if (/^\/[A-Za-z]:\//.test(pathname)) {
      pathname = pathname.slice(1);
    }
    pathname = pathname.replace(/\//g, "\\");

    if (parsed.hostname && parsed.hostname !== "localhost") {
      const suffix = pathname.startsWith("\\") ? pathname : `\\${pathname}`;
      return `\\\\${parsed.hostname}${suffix}`;
    }

    return pathname;
  } catch (error) {
    return "";
  }
}

function stripTrailingSeparators(path) {
  const normalized = String(path || "").trim().replace(/\//g, "\\");
  if (!normalized) {
    return "";
  }
  if (/^[A-Za-z]:\\?$/.test(normalized)) {
    return normalized.length === 2 ? `${normalized}\\` : normalized;
  }
  return normalized.replace(/[\\]+$/, "");
}

function getParentDirectoryPath(path) {
  const normalized = stripTrailingSeparators(path);
  if (!normalized) {
    return "";
  }
  if (/^[A-Za-z]:\\?$/.test(normalized) || /^\\\\[^\\]+\\[^\\]+$/.test(normalized)) {
    return normalized;
  }

  const lastSeparator = normalized.lastIndexOf("\\");
  if (lastSeparator < 0) {
    return normalized;
  }
  if (lastSeparator === 2 && /^[A-Za-z]:\\/.test(normalized)) {
    return normalized.slice(0, 3);
  }
  return normalized.slice(0, lastSeparator);
}

async function browseAndAddFolder() {
  try {
    const payload = await fetchJson("/api/lora/pick-folder", {
      method: "POST",
      body: {},
    });
    if (payload.cancelled || !payload.path) {
      return;
    }

    elements.folderInput.value = payload.path;
    await addWatchDir(payload.path);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadConfigAndLibrary({ preserveSelection, force }) {
  try {
    const payload = await fetchJson("/api/lora/config");
    state.watchDirs = payload.watch_dirs || [];
    state.scanStatus = state.watchDirs.length ? "scanning" : "idle";
    state.scanError = "";
    renderWatchDirs();
    renderStats();
    renderLibrary();
    await startLibraryScan({ preserveSelection, force });
  } catch (error) {
    showToast(error.message, true);
  }
}

function countQueuedReferenceImports(path = "") {
  const targetPath = String(path || "").trim();
  let count = 0;

  if (!targetPath || state.referenceImportActiveJob?.targetPath === targetPath) {
    if (state.referenceImportActiveJob) {
      count += 1;
    }
  }

  state.referenceImportQueue.forEach((job) => {
    if (!targetPath || job.targetPath === targetPath) {
      count += 1;
    }
  });

  return count;
}

function countQueuedReferenceUploads(path = "") {
  const targetPath = String(path || "").trim();
  let count = 0;

  if (!targetPath || state.referenceUploadActiveJob?.targetPath === targetPath) {
    if (state.referenceUploadActiveJob) {
      count += 1;
    }
  }

  state.referenceUploadQueue.forEach((job) => {
    if (!targetPath || job.targetPath === targetPath) {
      count += 1;
    }
  });

  return count;
}

function countQueuedReferenceJobs(path = "") {
  return countQueuedReferenceImports(path) + countQueuedReferenceUploads(path);
}

function hasQueuedReferenceImport(importId) {
  const normalizedId = Number(importId);
  if (!Number.isFinite(normalizedId)) {
    return false;
  }
  if (state.referenceImportActiveJob?.importId === normalizedId) {
    return true;
  }
  return state.referenceImportQueuedIds.has(normalizedId);
}

function queueBrowserImportReference(importId, modelPath) {
  const normalizedId = Number(importId);
  const targetPath = String(modelPath || "").trim();
  if (!Number.isFinite(normalizedId) || normalizedId <= 0 || !targetPath) {
    return false;
  }

  if (hasQueuedReferenceImport(normalizedId)) {
    return false;
  }

  state.referenceImportQueue.push({
    importId: normalizedId,
    targetPath,
  });
  state.referenceImportQueuedIds.add(normalizedId);
  renderBrowserImports();
  renderReferencePanel();
  void processReferenceImportQueue();
  return true;
}

async function requestBrowserImportReference(importId, modelPath, { consume = true, summaryOnly = false } = {}) {
  return fetchJson("/api/lora/browser-imports/reference", {
    method: "POST",
    body: {
      import_id: Number(importId),
      path: modelPath,
      consume: Boolean(consume),
      summary_only: Boolean(summaryOnly),
    },
  });
}

function applyBrowserImportReferencePayload(importId, modelPath, payload, { consume = true, updateReferenceItems = true } = {}) {
  const targetItem = state.items.find((item) => item.path === modelPath);
  const nextItems = Array.isArray(payload?.items) ? payload.items : [];
  const nextCount = Number(payload?.count || (updateReferenceItems ? nextItems.length : targetItem?.reference_count || 0));

  if (targetItem) {
    targetItem.reference_count = nextCount;
  }

  if (consume) {
    state.browserImports = state.browserImports.filter((item) => item.id !== Number(importId));
  }

  if (updateReferenceItems) {
    state.referencePath = modelPath;
    state.referenceItems = nextItems;
    state.referenceLoading = false;
    state.referenceError = "";
  }
}

async function processReferenceImportQueue() {
  if (state.referenceImportProcessing || !state.referenceImportQueue.length) {
    return;
  }

  state.referenceImportProcessing = true;
  const refreshedPaths = new Set();
  let errorMessage = "";

  try {
    while (state.referenceImportQueue.length) {
      const job = state.referenceImportQueue[0];
      state.referenceImportActiveJob = job;
      renderBrowserImports();
      renderReferencePanel();

      try {
        const payload = await requestBrowserImportReference(job.importId, job.targetPath, {
          consume: true,
          summaryOnly: true,
        });
        applyBrowserImportReferencePayload(job.importId, job.targetPath, payload, {
          consume: true,
          updateReferenceItems: false,
        });
        refreshedPaths.add(job.targetPath);
      } catch (error) {
        errorMessage = error.message;
      } finally {
        state.referenceImportQueue.shift();
        state.referenceImportQueuedIds.delete(job.importId);
        state.referenceImportActiveJob = null;
      }
    }
  } finally {
    state.referenceImportProcessing = false;
  }

  const selectedItem = getSelectedItem();
  if (selectedItem && refreshedPaths.has(selectedItem.path)) {
    await loadReferenceImages(selectedItem.path, { force: true });
  } else {
    renderLibrary();
    renderDetail();
    renderReferencePanel();
  }

  if (errorMessage && document.visibilityState === "visible") {
    showToast(errorMessage, true);
  }
}

async function processReferenceUploadQueue() {
  if (state.referenceUploadProcessing || !state.referenceUploadQueue.length) {
    return;
  }

  state.referenceUploadProcessing = true;
  const refreshedPaths = new Set();
  let completedCount = 0;
  let errorMessage = "";

  try {
    while (state.referenceUploadQueue.length) {
      const job = state.referenceUploadQueue[0];
      state.referenceUploadActiveJob = job;
      renderLibrary();
      renderReferencePanel();

      try {
        const payload = await fetchJson("/api/lora/references/upload", {
          method: "POST",
          body: {
            path: job.targetPath,
            summary_only: true,
            files: [
              {
                name: job.file.name,
                data_url: await readFileAsDataUrl(job.file),
              },
            ],
          },
        });
        completedCount += 1;
        refreshedPaths.add(job.targetPath);
        const targetItem = state.items.find((item) => item.path === job.targetPath);
        if (targetItem) {
          targetItem.reference_count = Number(payload.count || targetItem.reference_count || 0);
        }
      } catch (error) {
        errorMessage = error.message;
        if (state.referencePath === job.targetPath) {
          state.referenceError = error.message;
        }
      } finally {
        state.referenceUploadQueue.shift();
        state.referenceUploadActiveJob = null;
        renderLibrary();
        renderReferencePanel();
      }
    }
  } finally {
    state.referenceUploadProcessing = false;
    state.referenceUploadActiveJob = null;
  }

  const selectedItem = getSelectedItem();
  if (selectedItem && refreshedPaths.has(selectedItem.path)) {
    try {
      await loadReferenceImages(selectedItem.path, { force: true });
    } catch (_error) {
      // loadReferenceImages handles reference panel state
    }
  } else {
    renderLibrary();
    renderDetail();
    renderReferencePanel();
  }

  if (errorMessage && document.visibilityState === "visible") {
    showToast(
      completedCount ? `${completedCount}件追加したところでエラー: ${errorMessage}` : errorMessage,
      true
    );
  } else if (completedCount && document.visibilityState === "visible") {
    showToast(
      completedCount === 1 ? "参考メディアを追加しました。" : `${completedCount}件の参考メディアを追加しました。`
    );
  }
}

async function applyBrowserImportReference(importId, modelPath, { consume = true, silent = false } = {}) {
  const targetItem = state.items.find((item) => item.path === modelPath);
  if (!targetItem) {
    throw new Error("先に追加先のモデルを選んでください。");
  }

  state.referencePath = modelPath;
  state.referenceLoading = true;
  state.referenceError = "";
  renderReferencePanel();

  try {
    const payload = await requestBrowserImportReference(importId, modelPath, { consume });
    applyBrowserImportReferencePayload(importId, modelPath, payload, {
      consume,
      updateReferenceItems: true,
    });
    renderBrowserImports();
    renderLibrary();
    renderReferencePanel();
    if (!silent) {
      showToast("参考メディアを追加しました。");
    }
    return payload;
  } catch (error) {
    state.referenceLoading = false;
    state.referenceError = error.message;
    renderReferencePanel();
    throw error;
  }
}

async function applyBrowserImportMetadata(importId, modelPath, { consume = false, silent = false } = {}) {
  const targetItem = state.items.find((item) => item.path === modelPath);
  if (!targetItem) {
    throw new Error("先に反映先のモデルを選んでください。");
  }

  const payload = await fetchJson("/api/lora/browser-imports/apply", {
    method: "POST",
    body: {
      import_id: Number(importId),
      path: modelPath,
      consume: Boolean(consume),
    },
  });

  applySavedMetadata(modelPath, payload.metadata, payload.item);
  if (consume) {
    state.browserImports = state.browserImports.filter((item) => item.id !== Number(importId));
  }
  renderBrowserImports();
  if (!silent) {
    showToast("ページ情報を反映しました。");
  }
  return payload;
}

async function autoApplyBrowserImports(importItems) {
  const targetItem = getActiveBrowserImportTargetItem();
  const targetPath = targetItem?.path || "";
  if (!targetPath) {
    return { referenceCount: 0, metadataCount: 0 };
  }

  const candidates = [...importItems].sort((left, right) => Number(left.id || 0) - Number(right.id || 0));
  if (!candidates.length) {
    return { referenceCount: 0, metadataCount: 0 };
  }

  const metadataCandidates = candidates.filter((item) => !isReferenceBrowserImport(item));
  const referenceCandidates = candidates.filter((item) => isReferenceBrowserImport(item));
  let referenceCount = 0;
  let metadataCount = 0;
  referenceCandidates.forEach((importItem) => {
    if (queueBrowserImportReference(Number(importItem.id), targetPath)) {
      referenceCount += 1;
    }
  });

  for (const importItem of metadataCandidates) {
    try {
      await applyBrowserImportMetadata(Number(importItem.id), targetPath, {
        consume: true,
        silent: true,
      });
      metadataCount += 1;
    } catch (_error) {
      // Keep the import in the queue so it can still be handled manually.
    }
  }

  if (referenceCount && metadataCount) {
    showToast(`参考メディア ${referenceCount} 件を順番待ちに追加し、ページ情報 ${metadataCount} 件を自動で反映しました。`);
  } else if (referenceCount) {
    showToast(referenceCount === 1 ? "参考メディアを順番待ちに追加しました。" : `${referenceCount}件の参考メディアを順番待ちに追加しました。`);
  } else if (metadataCount) {
    showToast(metadataCount === 1 ? "ページ情報を自動で反映しました。" : `${metadataCount}件のページ情報を自動で反映しました。`);
  }
  return { referenceCount, metadataCount };
}

async function loadBrowserImports({ autoApply = false, primeKnown = false, silent = false } = {}) {
  if (state.browserImportLoading) {
    return;
  }

  state.browserImportLoading = true;
  try {
    const payload = await fetchJson("/api/lora/browser-imports");
    const items = Array.isArray(payload.items) ? payload.items : [];
    const newItems = items.filter((item) => !state.browserImportKnownIds.has(Number(item.id)));

    state.browserImports = items;
    items.forEach((item) => state.browserImportKnownIds.add(Number(item.id)));

    if (primeKnown) {
      renderBrowserImports();
      return;
    }

    if (autoApply) {
      await autoApplyBrowserImports(newItems);
    }

    renderBrowserImports();
  } catch (error) {
    if (!silent) {
      showToast(error.message, true);
    }
  } finally {
    state.browserImportLoading = false;
  }
}

function startLibraryAutoRefresh() {
  if (libraryPollTimer !== null) {
    window.clearInterval(libraryPollTimer);
  }

  libraryPollTimer = window.setInterval(() => {
    refreshLibraryState({ silent: true });
    refreshPendingDownloadState({ silent: true });
    loadBrowserImports({ autoApply: true, silent: true });
  }, LIBRARY_POLL_MS);
}

async function refreshPendingDownloadState({ silent = false } = {}) {
  if (!state.watchDirs.length) {
    return;
  }

  try {
    const payload = await fetchJson("/api/lora/pending-download");
    const lastResult = payload?.last_result || {};
    const nextUpdatedAt = lastResult.updated_at || "";
    const nextStatus = lastResult.status || "idle";
    const isFirstSync = state.pendingDownloadUpdatedAt === null;
    const hasChanged =
      nextUpdatedAt !== state.pendingDownloadUpdatedAt ||
      nextStatus !== state.pendingDownloadStatus;

    state.pendingDownloadUpdatedAt = nextUpdatedAt;
    state.pendingDownloadStatus = nextStatus;

    if (isFirstSync || !hasChanged || !nextUpdatedAt) {
      return;
    }

    if (nextStatus === "complete") {
      const importedItem = lastResult.item;
      if (importedItem?.path && !(lastResult.imported_count > 1)) {
        applyItemSnapshot(importedItem);
        return;
      }
      await startLibraryScan({ preserveSelection: true, force: true });
      return;
    }

    if (!silent && nextStatus === "error" && lastResult.message) {
      showToast(lastResult.message, true);
    }
  } catch (error) {
    if (!silent && document.visibilityState === "visible") {
      showToast(error.message, true);
    }
  }
}

async function refreshLibraryState({ silent = false } = {}) {
  if (!state.watchDirs.length) {
    return;
  }

  try {
    const payload = await fetchJson("/api/lora/library");
    const nextRevision = Number(payload.revision || 0);
    const nextStatus = payload.status || "idle";
    const nextScannedAt = payload.scanned_at || "";
    const nextWatchDirs = payload.config?.watch_dirs || state.watchDirs;
    const hasConfigChanged = JSON.stringify(nextWatchDirs) !== JSON.stringify(state.watchDirs);
    const hasStateChanged =
      nextRevision !== state.scanRevision ||
      nextStatus !== state.scanStatus ||
      nextScannedAt !== state.scannedAt ||
      hasConfigChanged;

    if (!hasStateChanged) {
      return;
    }

    applyLibraryPayload(payload, {
      preserveSelection: true,
      preserveItemsDuringScan: true,
    });
  } catch (error) {
    if (!silent && document.visibilityState === "visible") {
      showToast(error.message, true);
    }
  }
}

async function startLibraryScan({ preserveSelection, force }) {
  scanRequestToken += 1;
  const token = scanRequestToken;

  if (!state.watchDirs.length) {
    state.scanStatus = "idle";
    state.scanError = "";
    state.items = [];
    state.stats = null;
    state.scannedAt = "";
    state.scanDurationMs = 0;
    state.bulkSelectedPaths.clear();
    renderStats();
    renderBrowserImports();
    renderBulkState();
    renderLibrary();
    renderDetail();
    return;
  }

  state.scanStatus = "scanning";
  state.scanError = "";
  elements.statusLine.textContent = "ライブラリを読み込み中です...";
  window.clearTimeout(loadingHintTimer);
  loadingHintTimer = window.setTimeout(() => {
    if (token !== scanRequestToken) {
      return;
    }
    elements.statusLine.textContent = "ライブラリを読み込み中です... 登録済みフォルダは保存されています。";
  }, 1200);

  try {
    let payload;
    try {
      payload = await fetchJson("/api/lora/scan", {
        method: "POST",
        body: { force: Boolean(force) },
      });
    } catch (error) {
      await loadLegacyLibrary({ preserveSelection, token });
      return;
    }

    while (token === scanRequestToken) {
      applyLibraryPayload(payload, {
        preserveSelection,
        preserveItemsDuringScan: false,
      });

      if (payload.status === "ready") {
        return;
      }

      if (payload.status === "error") {
        throw new Error(payload.scan_error || "ライブラリの読み込みに失敗しました。");
      }

      await sleep(400);
      payload = await fetchJson("/api/lora/library");
      preserveSelection = true;
    }
  } catch (error) {
    state.scanStatus = "error";
    state.scanError = error.message;
    renderStats();
    renderLibrary();
    renderDetail();
    showToast(error.message, true);
  } finally {
    if (token === scanRequestToken) {
      window.clearTimeout(loadingHintTimer);
    }
  }
}

async function loadLegacyLibrary({ preserveSelection, token }) {
  const payload = await fetchJson("/api/lora/library");
  if (token !== scanRequestToken) {
    return;
  }

  applyLibraryPayload(
    {
      ...payload,
      status: "ready",
      scan_error: "",
    },
    {
      preserveSelection,
      preserveItemsDuringScan: false,
    },
  );
}

function applyLibraryPayload(payload, options = {}) {
  const preserveSelection = Boolean(options.preserveSelection);
  const preserveItemsDuringScan = Boolean(options.preserveItemsDuringScan);
  const nextStatus = payload.status || "idle";
  const nextWatchDirs = payload.config?.watch_dirs || state.watchDirs;
  const previousItems = state.items;
  const previousWatchDirs = state.watchDirs;
  const shouldKeepCurrentItems =
    preserveItemsDuringScan &&
    nextStatus !== "ready" &&
    nextWatchDirs.length > 0 &&
    state.items.length > 0;

  state.scanRevision = Number(payload.revision || state.scanRevision || 0);
  state.scanStatus = nextStatus;
  state.scanError = payload.scan_error || "";
  state.watchDirs = nextWatchDirs;

  if (!(shouldKeepCurrentItems && nextStatus === "scanning")) {
    state.warnings = payload.warnings || [];
    state.stats = payload.stats || null;
    state.scannedAt = payload.scanned_at || "";
    state.scanDurationMs = payload.scan_duration_ms || 0;
  }

  if (state.scanStatus === "ready") {
    state.items = payload.items || [];
    if (!preserveSelection || !state.items.some((item) => item.path === state.selectedPath)) {
      state.selectedPath = "";
    }
    state.bulkSelectedPaths = new Set(
      Array.from(state.bulkSelectedPaths).filter((path) => state.items.some((item) => item.path === path))
    );
  } else if (!shouldKeepCurrentItems) {
    state.items = [];
    state.selectedPath = "";
    state.bulkSelectedPaths.clear();
  }

  const watchDirsUnchanged = JSON.stringify(previousWatchDirs) === JSON.stringify(state.watchDirs);
  const canUseLightRender =
    (shouldKeepCurrentItems && nextStatus === "scanning" && watchDirsUnchanged) ||
    (nextStatus === "ready" &&
      watchDirsUnchanged &&
      areLibraryItemsEquivalent(previousItems, state.items));

  renderFilterOptions();
  renderWatchDirs();
  renderBrowserImports();
  renderBulkState();
  renderStats();
  if (canUseLightRender) {
    updateLibraryStatusOnly();
    return;
  }
  renderLibrary();
  renderDetail();
}

function buildLibraryStatusText(filtered, visibleItems) {
  const favorites = filtered.filter((item) => item.favorite).length;
  const scanLabel = state.scanDurationMs ? `読み込み ${formatDuration(state.scanDurationMs)}` : "";
  const listLabel = filtered.length > visibleItems.length ? `先頭 ${visibleItems.length} 件を表示中` : `${visibleItems.length} 件を表示中`;
  const autoScanLabel = state.scanStatus === "scanning" ? "バックグラウンドで更新中です。" : "";
  return scanLabel
    ? `${favorites}件がお気に入りです。${scanLabel}。${listLabel}。${autoScanLabel}`.trim()
    : `${favorites}件がお気に入りです。${listLabel}。${autoScanLabel}`.trim();
}

function updateLibraryStatusOnly() {
  const filtered = getFilteredItems();
  const visibleItems = getVisibleItems(filtered);
  elements.resultCount.textContent = `${filtered.length}件`;
  elements.visibleCount.textContent = `${visibleItems.length} / ${filtered.length}件表示`;
  elements.loadMoreWrap.hidden = filtered.length <= visibleItems.length;

  if (!state.watchDirs.length) {
    elements.statusLine.textContent = "モデルの親フォルダを登録すると、ここに一覧が出ます。";
    return;
  }

  if (state.scanStatus === "error") {
    elements.statusLine.textContent = "読み込みに失敗しました。";
    return;
  }

  if (!state.items.length) {
    elements.statusLine.textContent = "登録フォルダにモデルが見つかりませんでした。";
    elements.visibleCount.textContent = "0件表示";
    elements.loadMoreWrap.hidden = true;
    return;
  }

  if (!filtered.length) {
    elements.statusLine.textContent = "条件に一致する LoRA がありません。";
    elements.visibleCount.textContent = "0件表示";
    elements.loadMoreWrap.hidden = true;
    return;
  }

  elements.statusLine.textContent = buildLibraryStatusText(filtered, visibleItems);
}

function buildLibraryItemSignature(item) {
  const entry = item && typeof item === "object" ? item : {};
  return [
    entry.path || "",
    entry.filename || "",
    entry.display_name || "",
    entry.preview_url || "",
    entry.preview_media_kind || "",
    entry.preview_available ? "1" : "0",
    String(entry.reference_count || 0),
    entry.favorite ? "1" : "0",
    String(entry.rating || 0),
    entry.category || "",
    entry.author || "",
    entry.base_model || "",
    entry.source_url || "",
    entry.source_name || "",
    entry.strength_label || "",
    entry.recommended_steps || "",
    entry.recommended_sampler || "",
    entry.recommended_scheduler || "",
    JSON.stringify(entry.generation_settings_summary || {}),
    entry.updated_at || "",
    entry.modified_at || "",
    entry.prompt_snippet || "",
    Array.isArray(entry.triggers) ? entry.triggers.join("\u001f") : "",
    Array.isArray(entry.tags) ? entry.tags.join("\u001f") : "",
    Array.isArray(entry.metadata_sources) ? entry.metadata_sources.join("\u001f") : "",
  ].join("\u001e");
}

function areLibraryItemsEquivalent(leftItems, rightItems) {
  const left = Array.isArray(leftItems) ? leftItems : [];
  const right = Array.isArray(rightItems) ? rightItems : [];
  if (left.length !== right.length) {
    return false;
  }

  for (let index = 0; index < left.length; index += 1) {
    if (buildLibraryItemSignature(left[index]) !== buildLibraryItemSignature(right[index])) {
      return false;
    }
  }
  return true;
}

function renderWatchDirs() {
  elements.watchDirList.innerHTML = "";
  elements.folderCount.textContent = `${state.watchDirs.length}件`;

  if (!state.watchDirs.length) {
    const item = document.createElement("li");
    item.className = "watch-dir-item";
    const copy = document.createElement("span");
    copy.textContent = "まだ未登録です。LoRA の親フォルダを追加してください。";
    item.append(copy);
    elements.watchDirList.append(item);
    return;
  }

  state.watchDirs.forEach((watchDir, index) => {
    const item = document.createElement("li");
    item.className = "watch-dir-item";

    const copy = document.createElement("span");
    copy.className = "mono";
    copy.textContent = watchDir;

    const button = document.createElement("button");
    button.type = "button";
    button.dataset.index = String(index);
    button.textContent = "削除";

    item.append(copy, button);
    elements.watchDirList.append(item);
  });
}

function renderBrowserImports() {
  if (!elements.browserImportList || !elements.browserImportCount || !elements.browserImportTarget) {
    return;
  }
  elements.browserImportList.innerHTML = "";
  elements.browserImportCount.textContent = `${state.browserImports.length}件`;
  const targetItem = getActiveBrowserImportTargetItem();
  const queuedCount = targetItem ? countQueuedReferenceJobs(targetItem.path) : countQueuedReferenceJobs();
  elements.browserImportTarget.textContent = targetItem
    ? queuedCount
      ? `反映先: ${targetItem.display_name} / 参考追加待ち ${queuedCount}件`
      : `反映先: ${targetItem.display_name}`
    : queuedCount
      ? `反映先: 未選択 / 参考追加待ち ${queuedCount}件`
      : "反映先: 未選択";

  if (!state.browserImports.length) {
    elements.browserImportList.append(buildEmptyCard("まだ取り込みはありません。Chrome拡張からページ情報を送るとここに表示されます。"));
    return;
  }

  state.browserImports.forEach((item) => {
    const card = document.createElement("article");
    card.className = "browser-import-card";
    const payload = item.payload && typeof item.payload === "object" ? item.payload : {};
    const hasReferenceCandidate = isReferenceBrowserImport(item);
    const isQueuedReference = hasReferenceCandidate && hasQueuedReferenceImport(item.id);

    const title = document.createElement("h3");
    title.className = "browser-import-title";
    title.textContent = item.title || item.source_host || "取り込み候補";

    const meta = document.createElement("p");
    meta.className = "browser-import-meta mono";
    meta.textContent = item.source_url || item.source_host || "URLなし";

    const badges = document.createElement("div");
    badges.className = "badge-row";
    if (item.source_host) {
      badges.append(createBadge(item.source_host, "plain"));
    }
    if (item.triggers?.length) {
      badges.append(createBadge(`trigger ${item.triggers.length}`, "sage"));
    }
    if (item.tags?.length) {
      badges.append(createBadge(`tags ${item.tags.length}`, "sage"));
    }
    if (item.has_prompt) {
      badges.append(createBadge("prompt yes", "plain"));
    }
    if (item.negative_prompt) {
      badges.append(createBadge("negative yes", "plain"));
    }
    if (isQueuedReference) {
      badges.append(createBadge(state.referenceImportActiveJob?.importId === Number(item.id) ? "queue active" : "queue", "gold"));
    }

    const description = document.createElement("p");
    description.className = "browser-import-description";
    description.textContent = item.description || item.prompt_preview || "description なし";

    const footer = document.createElement("div");
    footer.className = "browser-import-footer";

    const created = document.createElement("span");
    created.textContent = formatDate(item.created_at);

    const actions = document.createElement("div");
    actions.className = "detail-actions";

    const applyButton = document.createElement("button");
    applyButton.type = "button";
    applyButton.className = "ghost-button small";
    applyButton.dataset.importApply = String(item.id);
    applyButton.textContent = "反映";

    if (hasReferenceCandidate) {
      const referenceButton = document.createElement("button");
      referenceButton.type = "button";
      referenceButton.className = "ghost-button small";
      referenceButton.dataset.importReference = String(item.id);
      referenceButton.textContent = state.referenceImportActiveJob?.importId === Number(item.id)
        ? "追加中..."
        : isQueuedReference
          ? "順番待ち"
          : "参考画像へ";
      referenceButton.disabled = isQueuedReference;
      actions.append(referenceButton);
    }

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "ghost-button small danger-button";
    deleteButton.dataset.importDelete = String(item.id);
    deleteButton.textContent = "削除";

    actions.append(applyButton, deleteButton);
    footer.append(created, actions);
    card.append(title, meta, badges, description, footer);
    elements.browserImportList.append(card);
  });
}

function renderFilterOptions() {
  const categoryValues = new Set(["all", ...BUILTIN_CATEGORIES]);
  const familyValues = new Set(["all", ...BUILTIN_MODEL_FAMILIES]);
  state.items.forEach((item) => {
    categoryValues.add(item.category || "uncategorized");
    familyValues.add(getItemModelFamily(item));
  });

  const values = Array.from(categoryValues).filter(Boolean);
  const familyOptions = Array.from(familyValues).filter(Boolean);
  if (!values.includes(state.filters.category)) {
    state.filters.category = "all";
  }
  if (!familyOptions.includes(state.filters.family)) {
    state.filters.family = "all";
  }
  persistFilterState();

  elements.categoryFilter.innerHTML = "";
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = CATEGORY_LABELS[value] || value;
    if (value === state.filters.category) {
      option.selected = true;
    }
    elements.categoryFilter.append(option);
  });

  elements.familyFilter.innerHTML = "";
  familyOptions.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = MODEL_FAMILY_LABELS[value] || value;
    if (value === state.filters.family) {
      option.selected = true;
    }
    elements.familyFilter.append(option);
  });

  elements.detailCategory.innerHTML = "";
  const detailValues = Array.from(new Set(["", ...values.filter((value) => value !== "all")]));
  detailValues.forEach((value) => {
    const option = document.createElement("option");
    option.value = value === "uncategorized" ? "" : value;
    option.textContent = value ? (CATEGORY_LABELS[value] || value) : "未分類";
    elements.detailCategory.append(option);
  });

  elements.categorySuggestions.innerHTML = "";
  detailValues
    .filter((value) => value && value !== "uncategorized")
    .forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      elements.categorySuggestions.append(option);
    });

  const currentBulkCategory = elements.bulkCategory.value || "__keep__";
  elements.bulkCategory.innerHTML = "";
  [
    { value: "__keep__", label: "変更しない" },
    { value: "uncategorized", label: "未分類にする" },
    ...detailValues
      .filter((value) => value)
      .map((value) => ({
        value,
        label: CATEGORY_LABELS[value] || value,
      })),
  ].forEach((entry) => {
    const option = document.createElement("option");
    option.value = entry.value;
    option.textContent = entry.label;
    if (entry.value === currentBulkCategory) {
      option.selected = true;
    }
    elements.bulkCategory.append(option);
  });
}

function renderStats() {
  elements.lastScan.textContent = state.scannedAt ? `最終: ${formatDate(state.scannedAt)}` : "未スキャン";
  elements.statsGrid.innerHTML = "";
  elements.warningList.innerHTML = "";
  const families = state.stats?.families || {};
  const loraCount = (families.lora || 0) + (families.lycoris || 0);

  const statEntries = [
    { label: "Models", value: state.stats?.total || 0 },
    { label: "LoRA", value: loraCount },
    { label: "Checkpoint", value: families.checkpoint || 0 },
    { label: "Favorite", value: state.stats?.favorites || 0 },
    { label: "Preview", value: state.stats?.with_preview || 0 },
    { label: "Folders", value: state.watchDirs.length },
  ];

  statEntries.forEach((entry) => {
    const card = document.createElement("div");
    card.className = "stat-card";

    const label = document.createElement("span");
    label.textContent = entry.label;

    const value = document.createElement("strong");
    value.textContent = String(entry.value);

    card.append(label, value);
    elements.statsGrid.append(card);
  });

  state.warnings.forEach((warning) => {
    const item = document.createElement("div");
    item.className = "warning-item";
    item.textContent = warning;
    elements.warningList.append(item);
  });
}

function resetReferenceState() {
  state.referencePath = "";
  state.referenceItems = [];
  state.referenceLoading = false;
  state.referenceError = "";
  closeReferenceViewer();
}

async function loadReferenceImages(path, { force = false } = {}) {
  const targetPath = String(path || "").trim();
  if (!targetPath) {
    resetReferenceState();
    renderReferencePanel();
    return;
  }

  if (!force && state.referencePath === targetPath && !state.referenceLoading && !state.referenceError) {
    renderReferencePanel();
    return;
  }

  referenceRequestToken += 1;
  const token = referenceRequestToken;
  state.referencePath = targetPath;
  state.referenceLoading = true;
  state.referenceError = "";
  renderReferencePanel();

  try {
    const payload = await fetchJson(`/api/lora/references?path=${encodeURIComponent(targetPath)}`);
    if (token !== referenceRequestToken) {
      return;
    }

    state.referencePath = targetPath;
    state.referenceItems = payload.items || [];
    state.referenceLoading = false;
    state.referenceError = "";

    const item = state.items.find((entry) => entry.path === targetPath);
    if (item) {
      item.reference_count = payload.count || state.referenceItems.length;
    }
  } catch (error) {
    if (token !== referenceRequestToken) {
      return;
    }
    state.referenceItems = [];
    state.referenceLoading = false;
    state.referenceError = error.message;
  }

  renderLibrary();
  renderReferencePanel();
}

function syncReferenceGallery({ force = false } = {}) {
  const item = getSelectedItem();
  if (!item) {
    resetReferenceState();
    renderReferencePanel();
    return;
  }

  if (!force && state.referencePath === item.path && !state.referenceLoading) {
    renderReferencePanel();
    return;
  }

  loadReferenceImages(item.path, { force });
}

function renderReferencePanel() {
  const scrollX = window.scrollX;
  const scrollY = window.scrollY;
  const restoreScroll = () => {
    if (Math.abs(window.scrollX - scrollX) > 1 || Math.abs(window.scrollY - scrollY) > 1) {
      window.scrollTo(scrollX, scrollY);
    }
  };

  try {
    const item = getSelectedItem();
    elements.referencePanel.hidden = !item;
    elements.referenceGrid.innerHTML = "";

    if (!item) {
      mountReferencePanelNearSelection();
      renderReferenceViewer();
      return;
    }

    const shownCount = state.referencePath === item.path ? state.referenceItems.length : item.reference_count || 0;
    const queuedCount = countQueuedReferenceJobs(item.path);
    const totalQueuedCount = countQueuedReferenceJobs();
    elements.referenceHeading.textContent = `${item.display_name} の参考画像`;
    elements.referenceCount.textContent = totalQueuedCount
      ? `${shownCount}件 / キュー${totalQueuedCount}件`
      : `${shownCount}件`;
    elements.addReferenceImages.disabled = false;

    if (state.referenceLoading && state.referencePath === item.path) {
      elements.referenceStatus.textContent = "参考画像を読み込んでいます。";
    } else if (state.referenceError && state.referencePath === item.path) {
      elements.referenceStatus.textContent = state.referenceError;
    } else if (!state.referenceItems.length) {
      elements.referenceStatus.textContent = queuedCount
        ? `参考メディアを ${queuedCount} 件順番に追加しています。追加中でも続けて送れます。`
        : totalQueuedCount
          ? `別の LoRA で参考メディアを ${totalQueuedCount} 件追加中です。ここでも続けて送れます。`
        : "まだ参考メディアはありません。ここから追加できます。";
    } else {
      const promptCount = state.referenceItems.filter((entry) => entry.has_prompt).length;
      const baseStatus = promptCount
        ? `参考メディア ${state.referenceItems.length} 件のうち ${promptCount} 件は prompt を保持しています。`
        : "サムネイルはそのまま、参考メディアだけ中央に並べています。";
      elements.referenceStatus.textContent = queuedCount
        ? `${baseStatus} あと ${queuedCount} 件を順番に追加中です。`
        : totalQueuedCount
          ? `${baseStatus} ほかの LoRA で ${totalQueuedCount} 件を処理中です。`
        : baseStatus;
    }

    if (!state.referenceItems.length) {
      mountReferencePanelNearSelection();
      renderReferenceViewer();
      return;
    }

    state.referenceItems.forEach((entry) => {
      const card = document.createElement("article");
      card.className = "reference-card";
      if (entry.path === state.referenceViewerPath) {
        card.classList.add("selected");
      }

      const link = document.createElement("button");
      link.type = "button";
      link.className = "reference-card-link";
      link.dataset.referenceOpen = entry.path;

      const image = isReferenceVideoItem(entry) ? document.createElement("video") : document.createElement("img");
      image.src = entry.url;
      if (isReferenceVideoItem(entry)) {
        image.preload = "metadata";
        image.muted = true;
        image.loop = true;
        image.autoplay = true;
        image.playsInline = true;
        image.setAttribute("aria-label", entry.name || "reference video");
      } else {
        image.loading = "lazy";
        image.alt = entry.name || "reference image";
      }

      const caption = document.createElement("span");
      caption.className = "reference-caption";
      caption.textContent = entry.name || "reference";

      const meta = document.createElement("span");
      meta.className = "reference-meta";
      meta.textContent = entry.prompt_preview || (isReferenceVideoItem(entry) ? "動画リファレンス" : "Prompt なし");

      link.append(image);
      if (isReferenceVideoItem(entry)) {
        link.append(createBadge("Video", "accent"));
      }
      if (entry.has_prompt) {
        link.append(createBadge("Prompt", "plain"));
      }
      if (entry.source_host) {
        link.append(createBadge(entry.source_host, "sage"));
      }
      link.append(caption, meta);

      const actions = document.createElement("div");
      actions.className = "reference-card-actions";
      actions.style.display = "flex";
      actions.style.justifyContent = "space-between";

      const sendButton = document.createElement("button");
      sendButton.type = "button";
      sendButton.className = "ghost-button small";
      sendButton.title = "百科事典に追加 (E)";
      sendButton.textContent = "📚";
      sendButton.addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          const resp = await fetch("/api/lora/encyclopedia-queue", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              path: entry.path,
              prompt: entry.prompt || "",
              negative_prompt: entry.negative_prompt || "",
              raw_parameters: entry.raw_parameters || "",
              resources_used: entry.resources_used || [],
            }),
          });
          const data = await resp.json();
          if (data.ok) {
            sendButton.textContent = "✓";
            setTimeout(() => { sendButton.textContent = "📚"; }, 1500);
            showToast("百科事典に送信しました。");
          }
        } catch (err) {
          showToast("送信エラー: " + err.message, true);
        }
      });

      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "ghost-button small danger-button";
      deleteButton.dataset.referenceDelete = entry.path;
      deleteButton.textContent = "削除";

      actions.append(sendButton, deleteButton);
      card.append(link, actions);
      elements.referenceGrid.append(card);
    });

    mountReferencePanelNearSelection();
    renderReferenceViewer();
  } finally {
    restoreScroll();
    window.requestAnimationFrame(restoreScroll);
  }
}

function renderLibrary() {
  renderBulkState();
  const filtered = getFilteredItems();
  const visibleItems = getVisibleItems(filtered);
  elements.libraryGrid.innerHTML = "";
  elements.emptyState.innerHTML = "";
  elements.resultCount.textContent = `${filtered.length}件`;
  elements.visibleCount.textContent = `${visibleItems.length} / ${filtered.length}件表示`;
  elements.loadMoreWrap.hidden = filtered.length <= visibleItems.length;
  renderToken += 1;
  const currentToken = renderToken;

  if (!state.watchDirs.length) {
    mountReferencePanelNearSelection();
    elements.statusLine.textContent = "モデルの親フォルダを登録すると、ここに一覧が出ます。";
    elements.emptyState.append(buildEmptyCard("まずは管理したいモデルのフォルダを 1 つ追加してください。"));
    elements.visibleCount.textContent = "0件表示";
    elements.loadMoreWrap.hidden = true;
    return;
  }

  if (state.scanStatus === "scanning" && !state.items.length) {
    mountReferencePanelNearSelection();
    elements.statusLine.textContent = "ライブラリを読み込み中です... 登録済みフォルダは保存されています。";
    elements.emptyState.append(buildEmptyCard("フォルダ設定は保存済みです。LoRA 一覧をバックグラウンドで読み込んでいます。"));
    elements.visibleCount.textContent = "読み込み中";
    elements.loadMoreWrap.hidden = true;
    return;
  }

  if (state.scanStatus === "error") {
    mountReferencePanelNearSelection();
    elements.statusLine.textContent = "読み込みに失敗しました。";
    elements.emptyState.append(buildEmptyCard(state.scanError || "ライブラリの読み込み中にエラーが起きました。"));
    elements.visibleCount.textContent = "0件表示";
    elements.loadMoreWrap.hidden = true;
    return;
  }

  if (!state.items.length) {
    mountReferencePanelNearSelection();
    elements.statusLine.textContent = "登録フォルダにモデルが見つかりませんでした。";
    elements.emptyState.append(buildEmptyCard(".safetensors や .ckpt を含むフォルダかどうかを確認してください。"));
    elements.visibleCount.textContent = "0件表示";
    elements.loadMoreWrap.hidden = true;
    return;
  }

  if (!filtered.length) {
    mountReferencePanelNearSelection();
    elements.statusLine.textContent = "条件に一致する LoRA がありません。";
    elements.emptyState.append(buildEmptyCard("検索語や星評価、カテゴリの条件を緩めてください。"));
    elements.visibleCount.textContent = "0件表示";
    elements.loadMoreWrap.hidden = true;
    return;
  }

  elements.statusLine.textContent = buildLibraryStatusText(filtered, visibleItems);

  renderCardsInBatches(visibleItems, currentToken);
}

function renderCardsInBatches(items, token) {
  let index = 0;

  const appendBatch = () => {
    if (token !== renderToken) {
      return;
    }

    const fragment = document.createDocumentFragment();
    for (let count = 0; count < 24 && index < items.length; count += 1, index += 1) {
      try {
        fragment.append(createCard(items[index]));
      } catch (error) {
        console.warn("Failed to render LoRA card.", items[index], error);
      }
    }
    elements.libraryGrid.append(fragment);
    mountReferencePanelNearSelection();

    if (index < items.length) {
      window.requestAnimationFrame(appendBatch);
      return;
    }

    scheduleAutoLoadMoreCheck();
  };

  window.requestAnimationFrame(appendBatch);
}

function createCard(item) {
  const safeItem = item && typeof item === "object" ? item : {};
  const itemPath = String(safeItem.path || "").trim();
  const displayName = String(safeItem.display_name || safeItem.filename || "Unnamed").trim() || "Unnamed";
  const relativePath = String(safeItem.relative_path || safeItem.path || "").trim();
  const triggers = Array.isArray(safeItem.triggers) ? safeItem.triggers.filter(Boolean) : [];
  const metadataSources = Array.isArray(safeItem.metadata_sources) ? safeItem.metadata_sources.filter(Boolean) : [];

  const card = document.createElement("button");
  card.type = "button";
  card.className = "lora-card";
  card.dataset.path = itemPath;
  card.setAttribute("aria-pressed", String(state.bulkSelectedPaths.has(itemPath)));
  if (itemPath === state.selectedPath) {
    card.classList.add("selected");
  }
  if (state.bulkSelectedPaths.has(itemPath)) {
    card.classList.add("bulk-selected");
  }

  const previewShell = document.createElement("div");
  previewShell.className = "preview-shell";

  if (safeItem.preview_available && safeItem.preview_url) {
    previewShell.append(
      createPreviewMediaNode(safeItem, `${displayName} preview`, () => {
        previewShell.classList.add("has-image");
      })
    );
  }

  const fallback = document.createElement("div");
  fallback.className = "preview-fallback";
  const mark = document.createElement("span");
  mark.className = "preview-mark";
  mark.textContent = getItemModelFamilyLabel(safeItem);
  const name = document.createElement("span");
  name.className = "preview-name";
  name.textContent = displayName;
  fallback.append(mark, name);
  previewShell.append(fallback);

  const copy = document.createElement("div");
  copy.className = "card-copy";

  const heading = document.createElement("h3");
  heading.textContent = displayName;

  const file = document.createElement("p");
  file.className = "card-file mono";
  file.textContent = relativePath;

  const badges = document.createElement("div");
  badges.className = "badge-row";
  badges.append(createBadge(getItemModelFamilyLabel(safeItem), "plain"));
  const selectionBadge = createBadge("選択中", "accent");
  selectionBadge.classList.add("selection-badge");
  selectionBadge.hidden = !state.bulkSelectedPaths.has(itemPath);
  badges.append(selectionBadge);
  if (safeItem.favorite) {
    badges.append(createBadge("Favorite", "accent"));
  }
  if (safeItem.category) {
    badges.append(createBadge(CATEGORY_LABELS[safeItem.category] || safeItem.category, "sage"));
  }
  if (safeItem.reference_count) {
    badges.append(createBadge(`Ref ${safeItem.reference_count}`, "plain"));
  }
  metadataSources.slice(0, 2).forEach((source) => {
    badges.append(createBadge(source, "plain"));
  });

  const trigger = document.createElement("p");
  trigger.className = "card-trigger";
  trigger.textContent = triggers.length ? triggers.join(", ") : "Trigger なし";

  const footer = document.createElement("div");
  footer.className = "card-meta";
  const date = document.createElement("span");
  date.textContent = formatDate(safeItem.updated_at || safeItem.modified_at);
  footer.append(date);

  copy.append(heading, file, badges, trigger, footer);
  card.append(previewShell, copy);
  const shell = document.createElement("div");
  shell.className = "lora-card-shell";
  shell.append(card, createCardRating(safeItem));
  return shell;
}

function createCardRating(item) {
  const group = document.createElement("div");
  group.className = "card-rating";
  group.dataset.ratingPath = item.path;
  group.setAttribute("role", "group");
  for (let value = 1; value <= 5; value += 1) {
    const star = document.createElement("button");
    star.type = "button";
    star.className = "rating-star";
    star.dataset.rating = String(value);
    group.append(star);
  }
  updateCardRating(group, item);
  return group;
}

function updateCardRating(group, item) {
  const rating = Number(item.rating || 0);
  const saving = state.savingRatings.has(item.path);
  group.dataset.rating = String(rating);
  group.setAttribute("aria-label", `${item.display_name || item.filename}の評価：${rating ? `${rating} / 5` : "未評価"}`);
  group.setAttribute("aria-busy", String(saving));
  group.querySelectorAll(".rating-star").forEach((star) => {
    const value = Number(star.dataset.rating);
    const label = value === rating ? `${value}つ星（クリックで評価を解除）` : `${value}つ星に評価`;
    star.textContent = value <= rating ? "★" : "☆";
    star.classList.toggle("is-filled", value <= rating);
    star.setAttribute("aria-label", label);
    star.setAttribute("aria-pressed", String(value === rating));
    star.setAttribute("aria-disabled", String(saving));
    star.title = saving ? "評価を保存中…" : label;
  });
}

function syncCardRating(path) {
  const item = state.items.find((entry) => entry.path === path);
  if (!item) return;
  elements.libraryGrid.querySelectorAll(".card-rating").forEach((group) => {
    if (group.dataset.ratingPath === path) updateCardRating(group, item);
  });
}

async function saveCardRating(path, value) {
  const item = state.items.find((entry) => entry.path === path);
  if (!item || state.deletingItems || state.savingRatings.has(path)) return;
  const rating = Number(item.rating || 0) === value ? 0 : value;
  state.savingRatings.add(path);
  syncCardRating(path);
  try {
    const result = await fetchJson("/api/lora/item", {
      method: "POST",
      body: { path, metadata: { rating } },
    });
    // Keep the current form and selection intact; only the rating was edited.
    if (result.metadata?.rating !== rating) throw new Error("アプリを再起動して、もう一度お試しください。");
    const currentItem = state.items.find((entry) => entry.path === path);
    if (currentItem) {
      currentItem.rating = rating;
      currentItem.updated_at = result.metadata.updated_at || currentItem.updated_at;
      if (!matchesRatingFilter(currentItem, state.filters.rating)) {
        renderLibrary();
      }
    }
    showToast(rating ? `${rating}つ星で保存しました。` : "評価を解除しました。");
  } catch (error) {
    showToast(`評価を保存できませんでした：${error.message}`, true);
  } finally {
    state.savingRatings.delete(path);
    syncCardRating(path);
  }
}

function createBadge(label, tone) {
  const badge = document.createElement("span");
  badge.className = `badge ${tone}`;
  badge.textContent = label;
  return badge;
}

function normalizeRatingFilter(value) {
  return typeof value === "string" && /^(all|unrated|eq-[1-5]|lte-[1-5])$/.test(value) ? value : "all";
}

function matchesRatingFilter(item, filter) {
  if (filter === "all") return true;
  const rating = Number(item.rating || 0);
  if (filter === "unrated") return rating === 0;
  const [mode, value] = filter.split("-");
  const stars = Number(value);
  return mode === "eq" ? rating === stars : rating >= 1 && rating <= stars;
}

function restoreFilterState() {
  try {
    const raw = window.localStorage.getItem(FILTER_STORAGE_KEY);
    if (!raw) {
      syncFilterControls();
      return;
    }

    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") {
      syncFilterControls();
      return;
    }

    state.filters.query = String(parsed.query || "").trim();
    state.filters.favoriteOnly = Boolean(parsed.favoriteOnly);
    state.filters.rating = normalizeRatingFilter(parsed.rating);
    state.filters.category = String(parsed.category || "all").trim() || "all";
    state.filters.family = String(parsed.family || "all").trim() || "all";
    state.filters.sort = String(parsed.sort || "favorite").trim() || "favorite";
  } catch (_error) {
    state.filters.query = "";
    state.filters.favoriteOnly = false;
    state.filters.rating = "all";
    state.filters.category = "all";
    state.filters.family = "all";
    state.filters.sort = "favorite";
  }

  syncFilterControls();
}

function persistFilterState() {
  try {
    window.localStorage.setItem(
      FILTER_STORAGE_KEY,
      JSON.stringify({
        query: state.filters.query,
        favoriteOnly: state.filters.favoriteOnly,
        rating: state.filters.rating,
        category: state.filters.category,
        family: state.filters.family,
        sort: state.filters.sort,
      }),
    );
  } catch (_error) {
    // Ignore storage failures and keep the app usable.
  }
}

function syncFilterControls() {
  elements.searchInput.value = state.filters.query;
  elements.favoriteFilter.checked = state.filters.favoriteOnly;
  elements.ratingFilter.value = state.filters.rating;
  elements.sortSelect.value = state.filters.sort;
  if (elements.categoryFilter) elements.categoryFilter.value = state.filters.category;
  if (elements.familyFilter) elements.familyFilter.value = state.filters.family;
}

function normalizeModelFamily(value) {
  const text = String(value || "").trim().toLowerCase();
  const normalized = text === "lycoris" ? "lora" : text;
  return MODEL_FAMILY_LABELS[normalized] ? normalized : "";
}

function getItemModelFamily(item) {
  const direct = normalizeModelFamily(item?.model_family);
  if (direct) {
    return direct;
  }
  if (item?.extension === ".ckpt") {
    return "checkpoint";
  }
  if (item?.extension === ".safetensors") {
    return "lora";
  }
  return "other";
}

function getItemModelFamilyLabel(item) {
  const direct = String(item?.model_family_label || "").trim();
  if (direct) {
    return direct;
  }
  return MODEL_FAMILY_LABELS[getItemModelFamily(item)] || "Other";
}

function supportsPromptTag(item) {
  const family = getItemModelFamily(item);
  return family === "lora";
}

function isCivitaiImagePageUrl(url) {
  return /^https?:\/\/(?:www\.)?civitai\.(?:com|red)\/images\/\d+(?:[/?#]|$)/i.test(String(url || "").trim());
}

function isReferenceBrowserImport(importItem) {
  const payload = importItem?.payload && typeof importItem.payload === "object" ? importItem.payload : {};
  if (!payload.preview_media_url && !payload.preview_image_url && !payload.preview_video_url) {
    return false;
  }

  const sourceUrl = String(importItem?.source_url || payload.source_url || "").trim();
  if (isCivitaiImagePageUrl(sourceUrl)) {
    return true;
  }

  return Boolean(
    payload.prompt ||
      payload.negative_prompt ||
      payload.raw_parameters ||
      payload.prompt_json ||
      payload.workflow_json ||
      payload.steps ||
      payload.sampler ||
      payload.cfg_scale ||
      payload.seed
  );
}

function createPreviewMediaNode(item, labelText, onReady) {
  if (isPreviewVideoItem(item)) {
    const video = document.createElement("video");
    video.preload = "metadata";
    video.muted = true;
    video.defaultMuted = true;
    video.loop = true;
    video.autoplay = true;
    video.playsInline = true;
    video.setAttribute("aria-label", labelText || "LoRA preview video");
    if (typeof onReady === "function") {
      video.addEventListener("loadeddata", () => {
        onReady();
        void video.play().catch(() => {});
      });
    }
    video.addEventListener("error", () => {
      video.pause();
      video.remove();
    });
    video.src = item.preview_url;
    return video;
  }

  const image = document.createElement("img");
  image.loading = "lazy";
  image.alt = labelText || "LoRA preview";
  if (typeof onReady === "function") {
    image.addEventListener("load", () => onReady());
  }
  image.addEventListener("error", () => image.remove());
  image.src = item.preview_url;
  return image;
}

function resetDetailPreviewMedia() {
  elements.detailPreviewShell.classList.remove("has-image");

  if (elements.detailPreview) {
    elements.detailPreview.onload = null;
    elements.detailPreview.onerror = null;
    elements.detailPreview.hidden = true;
    elements.detailPreview.removeAttribute("src");
  }

  if (elements.detailPreviewVideo) {
    elements.detailPreviewVideo.onloadeddata = null;
    elements.detailPreviewVideo.onerror = null;
    elements.detailPreviewVideo.pause();
    elements.detailPreviewVideo.hidden = true;
    elements.detailPreviewVideo.removeAttribute("src");
    elements.detailPreviewVideo.load();
  }
}

function setDetailPreviewMedia(item) {
  resetDetailPreviewMedia();
  if (!item?.preview_available || !item.preview_url) {
    return;
  }

  const handleReady = () => {
    elements.detailPreviewShell.classList.add("has-image");
  };
  const handleError = () => {
    resetDetailPreviewMedia();
  };

  if (isPreviewVideoItem(item) && elements.detailPreviewVideo) {
    elements.detailPreviewVideo.onloadeddata = () => {
      handleReady();
      void elements.detailPreviewVideo.play().catch(() => {});
    };
    elements.detailPreviewVideo.onerror = handleError;
    elements.detailPreviewVideo.hidden = false;
    elements.detailPreviewVideo.preload = "metadata";
    elements.detailPreviewVideo.muted = true;
    elements.detailPreviewVideo.defaultMuted = true;
    elements.detailPreviewVideo.loop = true;
    elements.detailPreviewVideo.autoplay = true;
    elements.detailPreviewVideo.playsInline = true;
    elements.detailPreviewVideo.src = item.preview_url;
    return;
  }

  if (elements.detailPreview) {
    elements.detailPreview.onload = handleReady;
    elements.detailPreview.onerror = handleError;
    elements.detailPreview.hidden = false;
    elements.detailPreview.src = item.preview_url;
  }
}

function renderDetail() {
  const item = getSelectedItem();
  const hasItem = Boolean(item);

  elements.detailEmpty.hidden = hasItem;
  elements.detailForm.hidden = !hasItem;

  if (!item) {
    resetDetailPreviewMedia();
    syncReferenceGallery();
    return;
  }

  populateDetail(item);
  updatePromptPreview();
  syncReferenceGallery();
}

function populateDetail(item) {
  if (typeof updateForgeControls === "function") updateForgeControls(item);
  setDetailPreviewMedia(item);
  elements.detailPreviewName.textContent = item.display_name;
  elements.deletePreview.disabled = !item.preview_available;
  elements.detailFileName.textContent = item.filename;
  elements.detailHeading.textContent = item.display_name;
  elements.detailRelativePath.textContent = item.path;
  elements.detailFavorite.checked = Boolean(item.favorite);
  elements.detailDisplayName.value = item.display_name || "";
  elements.detailCategory.value = item.category || "";
  elements.detailCategoryCustom.value = "";
  elements.detailGenerationSettings.hidden = getItemModelFamily(item) !== "checkpoint";
  elements.detailRecommendedSteps.value = item.recommended_steps || "";
  elements.detailRecommendedSampler.value = item.recommended_sampler || "";
  elements.detailRecommendedScheduler.value = item.recommended_scheduler || "";
  elements.detailTriggers.value = item.triggers.join(", ");
  elements.detailTags.value = item.tags.join(", ");
  elements.detailSourceUrl.value = item.source_url || "";
  if (elements.detailSourceLink) {
    setViewerActionLink(elements.detailSourceLink, item.source_url || "");
    elements.detailSourceLink.textContent = buildSourceLinkLabel(item.source_url || "");
    if (item.source_url) {
      elements.detailSourceLink.title = item.source_url;
    } else {
      elements.detailSourceLink.removeAttribute("title");
    }
  }
  if (elements.detailSourceLinkEmpty) {
    elements.detailSourceLinkEmpty.hidden = Boolean(item.source_url);
  }
  elements.detailNotes.value = item.notes || "";
  elements.detailMetaLine.textContent = `更新: ${formatDate(item.updated_at || item.modified_at)} / サイズ: ${formatFileSize(item.size_bytes)}`;
  elements.detailSourceDescription.textContent = item.source_description || "";

  elements.detailBadges.innerHTML = "";
  elements.detailBadges.append(createBadge(getItemModelFamilyLabel(item), "plain"));
  if (item.source_name) {
    elements.detailBadges.append(createBadge(item.source_name, "plain"));
  }
  if (item.reference_count) {
    elements.detailBadges.append(createBadge(`Ref ${item.reference_count}`, "plain"));
  }
  item.tags.forEach((tag) => {
    elements.detailBadges.append(createBadge(tag, "sage"));
  });
}

function applyItemSnapshot(itemSnapshot) {
  const nextItem = itemSnapshot && typeof itemSnapshot === "object" ? itemSnapshot : null;
  if (!nextItem?.path) {
    return false;
  }

  const index = state.items.findIndex((entry) => entry.path === nextItem.path);
  if (index === -1) {
    state.items = [...state.items, nextItem];
  } else {
    state.items[index] = { ...state.items[index], ...nextItem };
  }

  recalcStatsFromItems();
  renderFilterOptions();
  renderStats();
  renderLibrary();
  renderDetail();
  return true;
}

function applySavedMetadata(path, metadata, itemSnapshot = null) {
  if (applyItemSnapshot(itemSnapshot)) {
    return;
  }

  const item = state.items.find((entry) => entry.path === path);
  if (!item) {
    return;
  }

  item.display_name = metadata.display_name || item.filename.replace(/\.[^.]+$/, "");
  item.favorite = Boolean(metadata.favorite);
  item.category = metadata.category || "";
  item.source_url = metadata.source_url || "";
  item.notes = metadata.notes || "";
  item.triggers = [...metadata.triggers];
  item.tags = [...metadata.tags];
  if ("recommended_steps" in metadata) {
    item.recommended_steps = metadata.recommended_steps;
    item.recommended_sampler = metadata.recommended_sampler;
    item.recommended_scheduler = metadata.recommended_scheduler;
  }
  item.prompt_snippet = buildPromptSnippet(item);
  item.updated_at = new Date().toISOString();

  recalcStatsFromItems();
  renderFilterOptions();
  renderStats();
  renderLibrary();
  renderDetail();
}

function applyBulkMetadata(paths, changes) {
  const selected = new Set(paths);

  state.items.forEach((item) => {
    if (!selected.has(item.path)) {
      return;
    }

    if ("favorite" in changes) {
      item.favorite = Boolean(changes.favorite);
    }
    if ("category" in changes) {
      item.category = changes.category || "";
    }
    item.prompt_snippet = buildPromptSnippet(item);
    item.updated_at = new Date().toISOString();
  });

  recalcStatsFromItems();
  renderFilterOptions();
  renderBulkState();
  renderStats();
  renderLibrary();
  renderDetail();
}

function applyDeletedItem(path) {
  applyDeletedItems([path]);
}

function applyDeletedItems(paths) {
  const deleted = new Set(paths);
  state.items = state.items.filter((item) => !deleted.has(item.path));
  paths.forEach((path) => state.bulkSelectedPaths.delete(path));
  if (deleted.has(state.selectionAnchorPath)) state.selectionAnchorPath = "";
  if (deleted.has(state.selectedPath)) {
    state.selectedPath = "";
    resetDetailPanelAnchor();
    closeReferenceViewer();
  }

  recalcStatsFromItems();
  renderFilterOptions();
  renderBrowserImports();
  renderBulkState();
  renderStats();
  renderLibrary();
  renderDetail();
}

function readBulkForm() {
  const payload = {};
  const customCategory = elements.bulkCategoryCustom.value.trim();
  const category = elements.bulkCategory.value;
  if (customCategory) {
    payload.category = customCategory;
  } else if (category !== "__keep__") {
    payload.category = category === "uncategorized" ? "" : category;
  }

  const favorite = elements.bulkFavorite.value;
  if (favorite === "true") {
    payload.favorite = true;
  } else if (favorite === "false") {
    payload.favorite = false;
  }

  return payload;
}

function readDetailForm() {
  const customCategory = elements.detailCategoryCustom.value.trim();
  const generationSettings = getItemModelFamily(getSelectedItem()) === "checkpoint" ? {
    recommended_steps: elements.detailRecommendedSteps.value.trim(),
    recommended_sampler: elements.detailRecommendedSampler.value.trim(),
    recommended_scheduler: elements.detailRecommendedScheduler.value.trim(),
  } : {};
  return {
    display_name: elements.detailDisplayName.value.trim(),
    favorite: elements.detailFavorite.checked,
    category: customCategory || elements.detailCategory.value.trim(),
    ...generationSettings,
    triggers: splitList(elements.detailTriggers.value),
    tags: splitList(elements.detailTags.value),
    source_url: elements.detailSourceUrl.value.trim(),
    notes: elements.detailNotes.value.trim(),
  };
}

function buildPromptSnippet(item, formOverride = null) {
  const form = formOverride || {
    triggers: item.triggers,
  };
  const modelName = item.filename.replace(/\.[^.]+$/, "");
  const maxWeight = parseNumber(item.strength_max);
  const minWeight = parseNumber(item.strength_min);
  const weight = maxWeight ?? minWeight ?? 0.8;
  const triggerText = form.triggers.join(", ");
  if (!supportsPromptTag(item)) {
    return triggerText;
  }
  return triggerText ? `<lora:${modelName}:${formatWeight(weight)}>, ${triggerText}` : `<lora:${modelName}:${formatWeight(weight)}>`;
}

function updatePromptPreview() {
  const item = getSelectedItem();
  if (!item) {
    elements.detailPromptPreview.textContent = "";
    elements.copyPrompt.disabled = true;
    return;
  }

  const snippet = buildPromptSnippet(item, readDetailForm());
  if (snippet) {
    elements.detailPromptPreview.textContent = snippet;
  } else if (getItemModelFamily(item) === "checkpoint") {
    elements.detailPromptPreview.textContent = "Checkpoint はプロンプトタグを使いません。";
  } else {
    elements.detailPromptPreview.textContent = "Prompt はまだありません。";
  }
  elements.copyPrompt.disabled = !snippet;
}

function getSelectedItem() {
  return state.items.find((item) => item.path === state.selectedPath) || null;
}

function getActiveBrowserImportTargetItem() {
  const selectedItem = getSelectedItem();
  if (selectedItem) {
    return selectedItem;
  }

  const referencePath = String(state.referencePath || "").trim();
  if (!referencePath) {
    return null;
  }

  return state.items.find((item) => item.path === referencePath) || null;
}

function clearCurrentSelection() {
  if (!state.selectedPath) {
    return;
  }

  state.selectedPath = "";
  resetDetailPanelAnchor();
  closeReferenceViewer();
  renderBrowserImports();
  renderLibrary();
  renderDetail();
}

function clearAllSelections() {
  if (state.deletingItems) return;
  state.selectionAnchorPath = "";
  const hadSelectedPath = Boolean(state.selectedPath);
  const hadBulkSelection = state.bulkSelectedPaths.size > 0;
  if (!hadSelectedPath && !hadBulkSelection) {
    return;
  }

  state.selectedPath = "";
  state.bulkSelectedPaths.clear();
  resetDetailPanelAnchor();
  closeReferenceViewer();
  renderBrowserImports();
  renderBulkState();
  renderLibrary();
  renderDetail();
}

function handleBlankSelectionClear(event) {
  if (!state.selectedPath) {
    return;
  }

  if (event.target !== event.currentTarget) {
    return;
  }

  clearCurrentSelection();
}

function getFilteredItems() {
  const query = state.filters.query.toLowerCase();

  const filtered = state.items.filter((item) => {
    if (!matchesRatingFilter(item, state.filters.rating)) return false;
    if (state.filters.favoriteOnly && !item.favorite) {
      return false;
    }

    if (state.filters.category !== "all") {
      const category = item.category || "uncategorized";
      if (category !== state.filters.category) {
        return false;
      }
    }

    if (state.filters.family !== "all" && getItemModelFamily(item) !== state.filters.family) {
      return false;
    }

    if (!query) {
      return true;
    }

    const haystack = [
      item.display_name,
      item.filename,
      item.category,
      item.model_family,
      item.model_family_label,
      getItemModelFamilyLabel(item),
      item.notes,
      item.source_name,
      item.recommended_steps,
      item.recommended_sampler,
      item.recommended_scheduler,
      item.triggers.join(" "),
      item.tags.join(" "),
    ]
      .join(" ")
      .toLowerCase();

    return haystack.includes(query);
  });

  const sorted = [...filtered];
  sorted.sort((left, right) => compareItems(left, right, state.filters.sort));
  return sorted;
}

function getVisibleItems(items = getFilteredItems()) {
  return items.slice(0, state.visibleCount);
}

function renderBulkState() {
  const count = state.bulkSelectedPaths.size;
  const visible = new Set(getVisibleItems().map((item) => item.path));
  const hiddenCount = [...state.bulkSelectedPaths].filter((path) => !visible.has(path)).length;
  const label = `${count}件選択中${hiddenCount ? `（表示外 ${hiddenCount}件）` : ""}`;
  elements.bulkCount.textContent = label;
  elements.selectionCount.textContent = label;
  elements.clearBulk.disabled = count === 0 || state.deletingItems;
  elements.selectVisible.disabled = visible.size === 0 || state.deletingItems;
  elements.deleteBulk.disabled = count === 0 || state.deletingItems;
  elements.deleteBulk.textContent = state.deletingItems ? "削除中…" : "選択したファイルを削除";
  elements.bulkForm.querySelector('button[type="submit"]').disabled = state.deletingItems;
  elements.deleteItem.disabled = state.deletingItems;
  elements.clearSelection.disabled = state.deletingItems;
}

function syncCardSelection() {
  renderBulkState();
  elements.libraryGrid.querySelectorAll("button[data-path]").forEach((card) => {
    const selected = state.bulkSelectedPaths.has(card.dataset.path);
    card.classList.toggle("bulk-selected", selected);
    card.setAttribute("aria-pressed", String(selected));
    const badge = card.querySelector(".selection-badge");
    if (badge) badge.hidden = !selected;
  });
}

function selectCardRangeOrToggle(path, event) {
  const visible = getVisibleItems().map((item) => item.path);
  const end = visible.indexOf(path);
  if (end < 0) return;
  const anchor = visible.indexOf(state.selectionAnchorPath);
  if (event.shiftKey) {
    if (!event.ctrlKey && !event.metaKey) state.bulkSelectedPaths.clear();
    const start = anchor < 0 ? end : anchor;
    visible.slice(Math.min(start, end), Math.max(start, end) + 1)
      .forEach((itemPath) => state.bulkSelectedPaths.add(itemPath));
    if (anchor < 0) state.selectionAnchorPath = path;
  } else {
    if (state.bulkSelectedPaths.has(path)) state.bulkSelectedPaths.delete(path);
    else state.bulkSelectedPaths.add(path);
    state.selectionAnchorPath = path;
  }
  syncCardSelection();
}

async function deleteSelectedItems() {
  if (state.deletingItems) return;
  const items = state.items.filter((item) => state.bulkSelectedPaths.has(item.path));
  if (!items.length) return;
  const visible = new Set(getVisibleItems().map((item) => item.path));
  const hiddenCount = items.filter((item) => !visible.has(item.path)).length;
  const names = items.slice(0, 15).map((item) => `・${item.path}`).join("\n");
  const remaining = items.length > 15 ? `\nほか ${items.length - 15}件` : "";
  const hidden = hiddenCount ? `\n現在の表示外の ${hiddenCount}件も対象です。` : "";
  if (!window.confirm(`選択した ${items.length}件の元ファイルを削除しますか？${hidden}\nごみ箱には移動せず、元に戻せません。\n\n${names}${remaining}`)) return;

  state.deletingItems = true;
  renderBulkState();
  const deleted = [];
  const failed = [];
  try {
    for (const item of items) {
      try {
        await fetchJson("/api/lora/delete", { method: "POST", body: { path: item.path } });
        deleted.push(item.path);
      } catch (error) {
        failed.push(`${item.filename}: ${error.message}`);
      }
      elements.deleteBulk.textContent = `削除中 ${deleted.length + failed.length} / ${items.length}件`;
    }
  } finally {
    state.deletingItems = false;
    applyDeletedItems(deleted);
  }
  if (failed.length) {
    showToast(`${deleted.length}件削除、${failed.length}件失敗。失敗したカードは選択を残しています。`, true);
    window.alert(`削除できなかったファイル\n${failed.join("\n")}`);
  } else {
    showToast(`${deleted.length}件を削除しました。`);
  }
}

function compareItems(left, right, mode) {
  if (mode === "name") {
    return left.display_name.localeCompare(right.display_name, "ja");
  }

  if (mode === "created") {
    return compareDate(right.created_at || right.modified_at, left.created_at || left.modified_at) ||
      compareDate(right.updated_at || right.modified_at, left.updated_at || left.modified_at) ||
      left.display_name.localeCompare(right.display_name, "ja");
  }

  if (mode === "updated") {
    return compareDate(right.updated_at || right.modified_at, left.updated_at || left.modified_at) ||
      left.display_name.localeCompare(right.display_name, "ja");
  }

  if (mode === "modified") {
    return compareDate(right.modified_at, left.modified_at) ||
      left.display_name.localeCompare(right.display_name, "ja");
  }

  return (Number(right.favorite) - Number(left.favorite)) ||
    left.display_name.localeCompare(right.display_name, "ja");
}

function compareDate(left, right) {
  return Date.parse(left || "") - Date.parse(right || "");
}

function splitList(value) {
  return value
    .split(/[\n,;]+/)
    .map((entry) => entry.trim())
    .filter(Boolean)
    .filter((entry, index, array) => array.findIndex((candidate) => candidate.toLowerCase() === entry.toLowerCase()) === index);
}

function buildEmptyCard(message) {
  const wrapper = document.createElement("div");
  wrapper.className = "empty-card";
  wrapper.textContent = message;
  return wrapper;
}

function parseNumber(value) {
  if (value === "" || value === null || value === undefined) {
    return null;
  }

  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatWeight(value) {
  return Number(value).toFixed(2).replace(/\.?0+$/, "");
}

function formatDate(value) {
  const parsed = Date.parse(value || "");
  if (!Number.isFinite(parsed)) {
    return "日時不明";
  }

  return new Intl.DateTimeFormat("ja-JP", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function formatFileSize(bytes) {
  if (!Number.isFinite(bytes)) {
    return "-";
  }

  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;

  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }

  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatDuration(milliseconds) {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) {
    return "";
  }
  if (milliseconds < 1000) {
    return `${milliseconds}ms`;
  }
  return `${(milliseconds / 1000).toFixed(1)}s`;
}

function resetVisibleCount() {
  state.visibleCount = PAGE_SIZE;
}

function revealNextLibraryPage() {
  const filteredCount = getFilteredItems().length;
  if (state.visibleCount >= filteredCount) {
    return false;
  }

  state.visibleCount = Math.min(filteredCount, state.visibleCount + PAGE_SIZE);
  renderLibrary();
  return true;
}

function scheduleAutoLoadMoreCheck() {
  if (autoLoadMoreFrame) {
    return;
  }

  autoLoadMoreFrame = window.requestAnimationFrame(() => {
    autoLoadMoreFrame = 0;
    autoLoadMoreIfNeeded();
  });
}

function autoLoadMoreIfNeeded() {
  if (elements.loadMoreWrap.hidden) {
    return;
  }

  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
  if (!viewportHeight) {
    return;
  }

  const rect = elements.loadMoreWrap.getBoundingClientRect();
  if (rect.top > viewportHeight + AUTO_LOAD_MORE_VIEWPORT_THRESHOLD) {
    return;
  }

  revealNextLibraryPage();
}

function sleep(milliseconds) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}

function updateDetailPanelAnchor(card) {
  if (!elements.detailPanel || window.innerWidth <= 960) {
    resetDetailPanelAnchor();
    return;
  }

  const gridRect = elements.libraryGrid.getBoundingClientRect();
  const cardRect = card.getBoundingClientRect();
  const offset = Math.max(0, Math.round(cardRect.top - gridRect.top));
  elements.detailPanel.style.setProperty("--detail-offset", `${offset}px`);
}

function resetDetailPanelAnchor() {
  if (!elements.detailPanel) {
    return;
  }
  elements.detailPanel.style.removeProperty("--detail-offset");
}

function mountReferencePanelNearSelection() {
  if (!elements.referencePanel || !elements.libraryGrid || !elements.emptyState) {
    return;
  }

  const libraryColumn = elements.emptyState.parentElement;
  const selectedPath = String(state.selectedPath || "").trim();
  const selectedCard = selectedPath
    ? elements.libraryGrid.querySelector(`button[data-path="${cssEscape(selectedPath)}"]`)
    : null;

  if (!selectedCard || elements.referencePanel.hidden) {
    if (libraryColumn && elements.referencePanel.parentElement !== libraryColumn) {
      libraryColumn.insertBefore(elements.referencePanel, elements.emptyState);
    }
    return;
  }

  selectedCard.closest(".lora-card-shell").insertAdjacentElement("afterend", elements.referencePanel);
}

function recalcStatsFromItems() {
  if (!state.stats) {
    return;
  }

  const categories = {};
  const families = {};
  state.items.forEach((item) => {
    const key = item.category || "uncategorized";
    categories[key] = (categories[key] || 0) + 1;
    const family = getItemModelFamily(item);
    families[family] = (families[family] || 0) + 1;
  });

  state.stats = {
    ...state.stats,
    total: state.items.length,
    favorites: state.items.filter((item) => item.favorite).length,
    with_preview: state.items.filter((item) => item.preview_available).length,
    categories,
    families,
  };
}

function cssEscape(value) {
  if (!value) {
    return "";
  }
  if (window.CSS && typeof window.CSS.escape === "function") {
    return window.CSS.escape(value);
  }
  return String(value).replace(/["\\]/g, "\\$&");
}

function showToast(message, isError = false) {
  window.clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  elements.toast.classList.toggle("error", isError);
  const durationMs = isError ? 5200 : 2600;
  toastTimer = window.setTimeout(() => {
    elements.toast.classList.remove("show", "error");
  }, durationMs);
}
