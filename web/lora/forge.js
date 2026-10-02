let forgeStatus = { connected: false };
let forgeDialogItem = null;
let forgeSavedSettings = {};
let forgeCatalog = null;
let forgeActionRunning = false;
const forgeElement = id => document.getElementById(id);

async function loadForgeConnection() {
  const config = await fetchJson('/api/lora/forge/connection');
  forgeElement('forge-url').value = config.forge_url;
  forgeElement('forge-open-link').href = config.forge_url + '/';
}

async function saveForgeConnection(event) {
  event.preventDefault();
  try {
    await fetchJson('/api/lora/forge/connection', {method:'POST', body:{forge_url:forgeElement('forge-url').value}});
    await loadForgeConnection();
    await refreshForgeStatus();
    showToast('Forgeの接続先を保存しました。');
  } catch (error) { showToast(error.message, true); }
}

function updateForgeControls(item = getSelectedItem()) {
  const family = item && getItemModelFamily(item);
  forgeElement("forge-send").disabled = forgeActionRunning || !forgeStatus.connected || forgeStatus.busy || !["checkpoint", "lora"].includes(family);
  forgeElement("forge-save-current").disabled = forgeActionRunning || !forgeStatus.connected || family !== "checkpoint";
  const summary = item?.generation_settings_summary;
  const label = forgeElement("forge-preset-summary");
  label.hidden = !summary || !Object.keys(summary).length;
  if (!label.hidden) label.textContent = `生成プリセット保存済み：${summary.width} × ${summary.height} / CFG ${summary.cfg_scale} / Seed ${summary.seed}。設定${summary.count}項目。VAE／エンコーダー：${summary.modules?.join("、") || "追加なし（内蔵を使用）"}`;
}

async function refreshForgeStatus() {
  try {
    forgeStatus = await fetchJson("/api/lora/forge/status");
    forgeElement("forge-connection-status").textContent = forgeStatus.error || (!forgeStatus.connected ? "Forge画面を開いて再読み込みしてください。" : forgeStatus.busy ? "接続中・生成中。設定の保存は利用できます。" : "接続中。送受信できます。");
  } catch (_) {
    forgeStatus = { connected: false };
    forgeElement("forge-connection-status").textContent = "接続できません。ForgeとLibrary Deskの起動を確認してください。";
  }
  updateForgeControls();
  return forgeStatus;
}

function selectForgeChoices(id, choices, selected) {
  const element = forgeElement(id);
  element.replaceChildren(...choices.map(value => new Option(value, value)));
  if (choices.includes(selected)) element.value = selected;
  else { element.prepend(new Option("選択してください", "")); element.value = ""; }
}

async function openForgeSendDialog() {
  const item = getSelectedItem();
  if (!item) return;
  forgeActionRunning = true;
  updateForgeControls();
  try {
    await refreshForgeStatus();
    if (!forgeStatus.connected || forgeStatus.busy) throw new Error("Forge画面を開き、生成が停止していることを確認してください。");
    const [catalog, saved] = await Promise.all([
      fetchJson("/api/lora/forge/catalog"),
      fetchJson("/api/lora/forge/item-settings?path=" + encodeURIComponent(item.path)),
    ]);
    forgeDialogItem = item;
    forgeCatalog = catalog;
    forgeSavedSettings = saved.settings || {};
    const isCheckpoint = getItemModelFamily(item) === "checkpoint";
    forgeElement("forge-dialog-title").textContent = item.display_name + "をForgeへ送る";
    forgeElement("forge-settings-fields").hidden = !isCheckpoint;
    for (const input of forgeElement("forge-settings-fields").querySelectorAll("input, select, textarea")) input.disabled = !isCheckpoint;
    if (isCheckpoint) {
      const defaults = { ...forgeStatus.snapshot.settings, ...forgeSavedSettings };
      const steps = String(item.recommended_steps || defaults.steps || "");
      forgeElement("forge-steps").value = /^\d+$/.test(steps) ? steps : "";
      selectForgeChoices("forge-sampler", catalog.samplers, item.recommended_sampler || defaults.sampler);
      selectForgeChoices("forge-scheduler", catalog.schedulers, item.recommended_scheduler || defaults.scheduler);
      for (const [id, key] of [["forge-width", "width"], ["forge-height", "height"], ["forge-cfg", "cfg_scale"], ["forge-seed", "seed"], ["forge-prompt", "prompt"], ["forge-negative-prompt", "negative_prompt"]]) forgeElement(id).value = defaults[key] ?? "";
      const hasPreset = Boolean(forgeSavedSettings.widgets);
      forgeElement("forge-restore-all").checked = hasPreset;
      forgeElement("forge-restore-all").disabled = !hasPreset;
      forgeElement("forge-restore-modules").checked = "modules" in forgeSavedSettings;
      const selected = new Set((forgeSavedSettings.modules || []).map(m => m.path.toLowerCase()));
      forgeElement("forge-modules").replaceChildren(...catalog.modules.map(m => new Option(m.name, m.path, false, selected.has(m.path.toLowerCase()))));
      forgeElement("forge-modules").disabled = !forgeElement("forge-restore-modules").checked;
      forgeElement("forge-send-description").textContent = (hasPreset ? `保存した${Object.keys(forgeSavedSettings.widgets).length}項目の設定を復元します。` : "送信する生成設定を確認してください。") + (/^\d+$/.test(steps) ? "" : " Stepメモに範囲や補足があるため、送信するStepを1つ指定してください。");
    } else {
      forgeElement("forge-send-description").textContent = "このLoRAとTriggerをForgeのtxt2img Promptへ追加します。同じLoRAの指定は置き換えます。";
    }
    forgeElement("forge-dialog").showModal();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    forgeActionRunning = false;
    updateForgeControls();
  }
}

async function sendForgeSettings(event) {
  event.preventDefault();
  if (!forgeDialogItem || forgeActionRunning) return;
  forgeActionRunning = true;
  forgeElement("forge-confirm-send").disabled = true;
  try {
    let settings = {};
    if (getItemModelFamily(forgeDialogItem) === "checkpoint") {
      if (forgeElement("forge-restore-all").checked) settings = { ...forgeSavedSettings };
      Object.assign(settings, {
        steps: Number(forgeElement("forge-steps").value), sampler: forgeElement("forge-sampler").value,
        scheduler: forgeElement("forge-scheduler").value, width: Number(forgeElement("forge-width").value),
        height: Number(forgeElement("forge-height").value), cfg_scale: Number(forgeElement("forge-cfg").value),
        seed: Number(forgeElement("forge-seed").value), prompt: forgeElement("forge-prompt").value,
        negative_prompt: forgeElement("forge-negative-prompt").value,
      });
      if (forgeElement("forge-restore-modules").checked) {
        const selected = new Set(Array.from(forgeElement("forge-modules").selectedOptions, option => option.value));
        settings.modules = forgeCatalog.modules.filter(m => selected.has(m.path));
      } else delete settings.modules;
    }
    const queued = await fetchJson("/api/lora/forge/send", { method: "POST", body: { path: forgeDialogItem.path, settings } });
    const deadline = Date.now() + 15000;
    let applied = false;
    while (Date.now() < deadline) {
      await sleep(600);
      const status = await refreshForgeStatus();
      if (status.last_result?.id === queued.id) {
        if (!status.last_result.ok) throw new Error(status.last_result.message);
        applied = true;
        showToast("Forgeへ反映しました。");
        forgeElement("forge-dialog").close();
        break;
      }
    }
    if (!applied) throw new Error("Forge画面からの反映完了を待っています。ForgeのLibrary Deskタブで受け取り状態を確認してください。");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    forgeActionRunning = false;
    forgeElement("forge-confirm-send").disabled = false;
    updateForgeControls();
  }
}

async function saveForgeCurrentSettings() {
  const item = getSelectedItem();
  if (!item || forgeActionRunning) return;
  forgeActionRunning = true;
  updateForgeControls();
  try {
    const result = await fetchJson("/api/lora/forge/save-current", { method: "POST", body: { path: item.path } });
    applyItemSnapshot(result.item);
    showToast(result.message);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    forgeActionRunning = false;
    updateForgeControls();
  }
}

async function sendReferenceToForge(item, referenceItem) {
  if (forgeActionRunning) return;
  forgeActionRunning = true;
  const button = forgeElement("reference-send-forge");
  button.disabled = true;
  button.textContent = "送信中…";
  updateForgeControls();
  try {
    const queued = await fetchJson("/api/lora/forge/send-reference", {
      method: "POST", body: { path: item.path, file: referenceItem.path },
    });
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      await sleep(600);
      const status = await refreshForgeStatus();
      if (status.last_result?.id === queued.id) {
        if (!status.last_result.ok) throw new Error(status.last_result.message);
        const warnings = (queued.warnings || []).join(" ");
        showToast("SDへ反映しました。" + (warnings ? " " + warnings : ""), Boolean(warnings));
        return;
      }
    }
    throw new Error("SDへの反映完了を待っています。Forge画面の連携状態を確認してください。");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    forgeActionRunning = false;
    button.textContent = "SDへ送る";
    renderReferenceViewer();
    updateForgeControls();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  forgeElement('forge-connection-form').addEventListener('submit', saveForgeConnection);
  loadForgeConnection().catch(error => showToast(error.message, true));
  forgeElement("forge-send").addEventListener("click", openForgeSendDialog);
  forgeElement("forge-save-current").addEventListener("click", saveForgeCurrentSettings);
  forgeElement("forge-send-form").addEventListener("submit", sendForgeSettings);
  forgeElement("forge-dialog-cancel").addEventListener("click", () => forgeElement("forge-dialog").close());
  forgeElement("forge-restore-modules").addEventListener("change", () => { forgeElement("forge-modules").disabled = !forgeElement("forge-restore-modules").checked; });
  forgeElement("forge-clear-modules").addEventListener("click", () => {
    forgeElement("forge-restore-modules").checked = true;
    forgeElement("forge-modules").disabled = false;
    for (const option of forgeElement("forge-modules").options) option.selected = false;
  });
  refreshForgeStatus();
  setInterval(() => { if (document.visibilityState === "visible") refreshForgeStatus(); }, 5000);
});
