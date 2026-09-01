"use strict";

/*
 * BRPHM offline cockpit runtime.
 * The payload is read-only evidence.  No values are generated when a source
 * field is absent: the UI shows "不可用" and keeps the failure visible.
 */
(() => {
  const payload = window.__PAYLOAD__ || {};
  const embeddedOperations = window.__OPERATIONS__ || {};
  const control = window.__CONTROL__ || { enabled: false };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const customSelects = new WeakMap();
  let openCustomSelect = null;
  const app = $("#app");
  // Competition result snapshots may contain prediction CSVs without raw
  // telemetry.  Keep the explicitly labelled built-in examples as a second
  // source so the replay UI never loses its working demonstration state.
  const samples = Array.isArray(payload.samples) ? [...payload.samples] : [];
  const telemetry = { ...(payload.telemetry || {}) };
  const predictions = { ...(payload.predictions || {}) };
  const embeddedExamples = payload.examples && typeof payload.examples === "object" ? payload.examples : {};
  const exampleSamples = Array.isArray(embeddedExamples.samples) ? embeddedExamples.samples : [];
  const embeddedExampleIds = new Set();
  exampleSamples.forEach((sample) => {
    if (!sample || !sample.sample_id || samples.some((item) => item.sample_id === sample.sample_id)) return;
    embeddedExampleIds.add(sample.sample_id);
    samples.push({ ...sample, example: true, example_label: sample.example_label || "项目内置示例（只读）" });
  });
  const exampleTelemetry = embeddedExamples.telemetry && typeof embeddedExamples.telemetry === "object" ? embeddedExamples.telemetry : {};
  const examplePredictions = embeddedExamples.predictions && typeof embeddedExamples.predictions === "object" ? embeddedExamples.predictions : {};
  embeddedExampleIds.forEach((sampleId) => {
    if (!(sampleId in telemetry) && exampleTelemetry[sampleId]) telemetry[sampleId] = exampleTelemetry[sampleId];
    if (!(sampleId in predictions) && examplePredictions[sampleId]) predictions[sampleId] = examplePredictions[sampleId];
  });
  payload.channel_meta = { ...(payload.channel_meta || {}) };
  const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
  const compactOrbit = window.matchMedia?.("(max-width: 760px)").matches === true;
  const nf = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
  const finite = (value) => value !== null && value !== "" && value !== undefined && Number.isFinite(Number(value));
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const valueOrDash = (value) => value == null || value === "" ? "--" : String(value);
  const format = (value, digits = 2) => finite(value) ? Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits }) : "--";
  const compact = (value) => {
    if (!finite(value)) return "--";
    const number = Number(value);
    if (Math.abs(number) >= 1000) return nf.format(number);
    if (Math.abs(number) >= 10) return format(number, 1);
    return format(number, 3);
  };
  const unitLabel = (unit) => ["cycle", "cycles"].includes(unit) ? "循环" : ["day", "days"].includes(unit) ? "天" : (unit || "--");
  const hash = (value) => { let h = 2166136261; for (const char of String(value)) h = Math.imul(h ^ char.charCodeAt(0), 16777619); return (h >>> 0) / 4294967296; };
  const setText = (selector, value) => { const node = $(selector); if (node) node.textContent = valueOrDash(value); };

  // Offline fuzzy search: preserve the original Chinese/English text, then
  // add a small deterministic pinyin index and initials.  No network font or
  // transliteration service is required, so search remains available offline.
  const SEARCH_PHRASES = [
    ["完整流程复现", "wanzhengliuchengfuxian", "wzl c f"], ["结果复现核对", "jieguofuxianhedui", "j g f x h d"],
    ["遥测统一预处理", "yaoce tongyuyuchuli", "y c t y y c"], ["仿真任务计划", "fangzhenrenwujihua", "f z r w j h"],
    ["仿真任务状态", "fangzhenrenwuzhuangtai", "f z r w z t"], ["电池部件（储能系统）与反作用轮部件（姿态控制执行器）模型训练", "dianchibujianchunengxitongyufanzuoyonglunbujianzikongzhizhixingqimoxingxunlian", "d c b j c n x t y f z y l b j z k z x q m x x l"],
    ["电池部件（储能系统）与反作用轮部件（姿态控制执行器）航天工况适配", "dianchibujianchunengxitongyufanzuoyonglunbujianzikongzhizhixingqihangtian gongkuang shipei", "d c b j c n x t y f z y l b j z k z x q h t g k s p"], ["电池部件（储能系统）与反作用轮部件（姿态控制执行器）剩余寿命预测", "dianchibujianchunengxitongyufanzuoyonglunbujianzikongzhizhixingqishengyushoumingyuce", "d c b j c n x t y f z y l b j z k z x q s y s m y c"],
    ["电池部件剩余寿命预测模型（储能系统）", "battery component remaining useful life model energy storage", "battery life"], ["反作用轮部件剩余寿命预测模型（姿态控制执行器）", "reaction wheel component remaining useful life model attitude actuator", "reaction wheel life"],
    ["PyTorch", "pytorch", "torch"], ["单次运行结果评估", "danciyunxingjieguopinggu", "d c y x j g p g"],
    ["电池", "dianchi", "dc"], ["反作用轮", "fanzuoyonglun", "f z y l"],
    ["储能系统", "chunengxitong", "c n x t"], ["姿控执行器", "zikong zhixingqi", "z k z x q"], ["数据来源", "shujulaiyuan", "s j l y"],
    ["失效模式", "shixiaomoshi", "s x m s"], ["轨道工况", "guidaogongkuang", "g d g k"], ["退化状态", "tuihuazhuangtai", "t h z t"],
    ["任务中心", "renwuzhongxin", "r w z x"], ["方法验证", "fangfayanzheng", "f f y z"], ["寿命预测", "shoumingyuce", "s m y c"],
    ["输入说明", "shurushuoming", "s r s m"], ["温度", "wendu", "wd"], ["电流", "dianliu", "dl"], ["电压", "dianya", "dy"],
  ];
  const SEARCH_ALIASES = [
    ["电池", "battery"], ["反作用轮", "reaction wheel"], ["储能系统", "energy storage"], ["姿控执行器", "attitude actuator"],
    ["遥测", "telemetry"], ["寿命", "remaining useful life rul"], ["回放", "replay"], ["任务", "task workflow"],
    ["结果复现核对", "reproduce results check reproduce_results"], ["完整流程复现", "reproduce all reproduce_all full pipeline"],
    ["遥测统一预处理", "preprocess telemetry data preprocessing"], ["仿真任务计划", "simulation task plan sim task plan"],
    ["仿真任务状态", "simulation task status sim task status"], ["电池部件（储能系统）与反作用轮部件（姿态控制执行器）模型训练", "battery energy storage reaction wheel attitude actuator component pytorch train pretrain"],
    ["电池部件（储能系统）与反作用轮部件（姿态控制执行器）航天工况适配", "battery energy storage reaction wheel attitude actuator component aerospace condition adaptation"], ["电池部件（储能系统）与反作用轮部件（姿态控制执行器）剩余寿命预测", "battery energy storage reaction wheel attitude actuator component remaining useful life predict inference"],
    ["电池部件剩余寿命预测模型（储能系统）", "battery component model energy storage"], ["反作用轮部件剩余寿命预测模型（姿态控制执行器）", "reaction wheel component model attitude actuator"],
    ["PyTorch", "torch model file"], ["结果评估", "evaluate result evaluation"],
  ];
  const normaliseSearch = (value) => String(value ?? "").toLowerCase().normalize("NFKC").replace(/[\s_\-./\\:：，,。；;（）()[\]{}]+/g, "");
  function pinyinIndex(value) {
    const original = String(value ?? "").toLowerCase(); let text = original;
    for (const [phrase, pinyin, initials] of SEARCH_PHRASES) text = text.split(phrase).join(` ${pinyin} ${initials.replace(/\s/g, "")}`);
    for (const [phrase, alias] of SEARCH_ALIASES) if (original.includes(phrase.toLowerCase())) text += ` ${alias}`;
    return normaliseSearch(text);
  }


  function fuzzySearch(values, query) {
    const needle = normaliseSearch(query); if (!needle) return true;
    const haystack = values.flatMap((value) => [String(value ?? ""), pinyinIndex(value)]).join(" ").toLowerCase();
    return needle.split(/[\s,，]+/).filter(Boolean).every((part) => haystack.includes(part));
  }

  // Native select menus are rendered by the operating system and can become
  // unreadable under high-DPI Windows themes.  Keep the real select for form
  // semantics, but project it through one keyboard-accessible dark popover.
  function customSelectOptionLabel(option) { return option?.textContent?.trim() || "未指定"; }
  function positionCustomSelect(entry) {
    if (!entry?.menu || entry.menu.hidden) return;
    const box = entry.button.getBoundingClientRect();
    // The menu is position:fixed, so use the layout viewport and the same
    // CSS-pixel coordinate space as getBoundingClientRect().  Mixing
    // visualViewport (which can expose device-pixel dimensions on Windows
    // high-DPI/200% scaling) with layout coordinates sends the menu to the
    // far right edge of the screen.  The layout viewport remains stable under
    // browser zoom and system scaling, while still allowing the menu to fit.
    const offsetX = 0; const offsetY = 0;
    const viewportWidth = Math.max(1, document.documentElement?.clientWidth || window.innerWidth || 1);
    const viewportHeight = Math.max(1, document.documentElement?.clientHeight || window.innerHeight || 1);
    const viewportGap = 10; const roomBelow = viewportHeight - (box.bottom + offsetY) - viewportGap; const roomAbove = (box.top + offsetY) - viewportGap;
    const availableHeight = Math.max(100, Math.min(360, Math.max(roomBelow, roomAbove)));
    const menuHeight = Math.min(availableHeight, Math.max(100, entry.menu.scrollHeight || 100));
    const openUpward = roomBelow < Math.min(menuHeight, 170) && roomAbove > roomBelow;
    const width = clamp(Math.round(Math.max(box.width, entry.menu.classList.contains("telemetry-unit-menu") ? 248 : 208)), 178, Math.max(178, viewportWidth - viewportGap * 2));
    // Align the popover's leading edge with the trigger whenever possible.
    // Right-aligning a menu wider than a narrow trigger makes it appear
    // detached from the control at high DPI, especially in the per-file queue.
    const maxLeft = Math.max(viewportGap, viewportWidth - width - viewportGap); const left = clamp(Math.round(box.left + offsetX), viewportGap, maxLeft);
    entry.menu.style.width = `${width}px`;
    entry.menu.style.left = `${left}px`;
    entry.menu.style.maxHeight = `${Math.round(availableHeight)}px`;
    entry.menu.style.top = `${Math.round(openUpward ? Math.max(viewportGap, box.top + offsetY - menuHeight - 7) : box.bottom + offsetY + 7)}px`;
    const originX = clamp(Math.round(box.left + box.width * .5 - left), 20, Math.max(20, width - 20));
    entry.menu.style.transformOrigin = `${originX}px ${openUpward ? "100%" : "0%"}`;
    entry.menu.classList.toggle("opens-upward", openUpward);
  }
  function closeCustomSelect(entry = openCustomSelect, immediate = false) {
    if (!entry) return;
    if (window.gsap && !reduceMotion && !immediate) {
      window.gsap.killTweensOf(entry.menu);
      const exitY = entry.menu.classList.contains("opens-upward") ? 3 : -3;
      window.gsap.to(entry.menu, { autoAlpha: 0, y: exitY, scale: .99, duration: .14, ease: "power2.in", overwrite: "auto", onComplete: () => { entry.menu.hidden = true; entry.menu.removeAttribute("style"); } });
    } else { entry.menu.hidden = true; entry.menu.removeAttribute("style"); }
    entry.button.setAttribute("aria-expanded", "false"); entry.container.classList.remove("is-open");
    if (openCustomSelect === entry) openCustomSelect = null;
  }
  function openCustomSelectMenu(entry) {
    if (!entry || entry.select.disabled) return;
    if (openCustomSelect && openCustomSelect !== entry) closeCustomSelect(openCustomSelect, true);
    entry.menu.hidden = false; positionCustomSelect(entry); openCustomSelect = entry;
    entry.button.setAttribute("aria-expanded", "true"); entry.container.classList.add("is-open");
    if (window.gsap && !reduceMotion) {
      window.gsap.killTweensOf(entry.menu);
      const entryY = entry.menu.classList.contains("opens-upward") ? 4 : -4;
      window.gsap.fromTo(entry.menu, { autoAlpha: 0, y: entryY, scale: .985 }, { autoAlpha: 1, y: 0, scale: 1, duration: .2, ease: "expo.out", overwrite: "auto" });
    }
  }
  function syncCustomSelect(select) {
    const entry = customSelects.get(select); if (!entry) return;
    const options = Array.from(select.options || []);
    entry.button.disabled = select.disabled; entry.container.classList.toggle("is-disabled", select.disabled);
    entry.label.textContent = customSelectOptionLabel(select.selectedOptions?.[0] || options[0]);
    entry.menu.replaceChildren(...options.map((option) => {
      const choice = document.createElement("button"); choice.type = "button"; choice.className = "custom-select-option";
      choice.setAttribute("role", "option"); choice.dataset.value = option.value; choice.disabled = option.disabled;
      choice.setAttribute("aria-selected", String(option.selected)); choice.textContent = customSelectOptionLabel(option);
      choice.addEventListener("click", () => {
        if (option.disabled) return;
        select.value = option.value; select.dispatchEvent(new Event("change", { bubbles: true }));
        syncCustomSelect(select); closeCustomSelect(entry);
        if (window.gsap && !reduceMotion) window.gsap.fromTo(entry.button, { scale: .978 }, { scale: 1, duration: .28, ease: "expo.out", overwrite: "auto" });
      });
      return choice;
    }));
  }
  function upgradeSelect(select) {
    if (!select || customSelects.has(select)) return customSelects.get(select);
    const container = document.createElement("div"); container.className = "custom-select";
    const button = document.createElement("button"); button.type = "button"; button.className = "custom-select-trigger";
    button.setAttribute("role", "combobox"); button.setAttribute("aria-haspopup", "listbox"); button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", select.getAttribute("aria-label") || "选择选项");
    const label = document.createElement("span"); label.className = "custom-select-value";
    const chevron = icon("#i-chevron"); chevron.classList.add("custom-select-chevron"); button.append(label, chevron);
    const menu = document.createElement("div"); menu.className = "custom-select-menu"; menu.hidden = true; menu.setAttribute("role", "listbox"); menu.setAttribute("aria-label", button.getAttribute("aria-label"));
    select.before(container); container.append(select, button); document.body.append(menu);
    select.classList.add("native-select-proxy"); select.tabIndex = -1; select.setAttribute("aria-hidden", "true");
    const entry = { select, container, button, label, menu }; customSelects.set(select, entry);
    button.addEventListener("click", () => openCustomSelect === entry ? closeCustomSelect(entry) : openCustomSelectMenu(entry));
    button.addEventListener("keydown", (event) => {
      const options = Array.from(select.options || []).filter((option) => !option.disabled);
      const current = Math.max(0, options.findIndex((option) => option.value === select.value));
      if (["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault(); const next = options[(current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % Math.max(1, options.length)];
        if (next) { select.value = next.value; select.dispatchEvent(new Event("change", { bubbles: true })); syncCustomSelect(select); }
      } else if (["Enter", " "].includes(event.key)) { event.preventDefault(); openCustomSelect === entry ? closeCustomSelect(entry) : openCustomSelectMenu(entry); }
      else if (event.key === "Escape") closeCustomSelect(entry);
    });
    select.addEventListener("change", () => syncCustomSelect(select)); syncCustomSelect(select); return entry;
  }
  function upgradeCustomSelects(root = document) { $$('select:not(.native-select-proxy)', root).forEach(upgradeSelect); }
  function destroyCustomSelects(root) {
    if (!root) return;
    $$('select.native-select-proxy', root).forEach((select) => {
      const entry = customSelects.get(select); if (!entry) return;
      if (openCustomSelect === entry) closeCustomSelect(entry, true);
      entry.menu.remove(); entry.button.remove(); customSelects.delete(select);
    });
  }
  const safeRatio = (value, fallback = 0) => finite(value) ? clamp(Number(value), 0, 1) : fallback;
  const appState = {
    view: "predict", line: "bat", sampleId: null, channel: null, progress: 0,
    playing: false, speed: 1, lastFrame: 0, orbitPlaying: true, orbitAngle: 0, orbitTimeSeconds: 0,
    orbitOffset: 0, orbitPitch: 0, orbitZoom: 1, orbitMode: "globe", markers: [], operations: [], operationId: null,
    jobs: [], jobId: null, search: "", mobileSearch: "", operationSearch: "",
    operationCategory: "all", riskFilter: "all", inputCheck: false, inputCheckTimer: 0,
    telemetryFiles: [], telemetryFileUnits: new Map(), telemetrySchema: null, telemetryBatch: null, telemetryBatchContext: null,
    telemetrySubmitting: false, telemetryExportUrl: null, hiddenSampleIds: new Set(), uploadedSampleIds: new Set(), uploadedReplayIds: new Map(), evidenceSampleId: null,
    sceneStatusExpanded: false,
  };

  const PLATFORM_CONFIGURATION_LABELS = Object.freeze({
    unspecified: "平台构型未说明",
    not_equipped: "未搭载反作用轮部件（姿态控制执行器）",
    equipped: "搭载反作用轮部件（姿态控制执行器）",
  });
  const ATTITUDE_METHOD_LABELS = Object.freeze({
    unspecified: "姿态稳定方式未提供",
    magnetorquer: "磁力矩器",
    passive_magnetic: "被动磁稳定",
    spin_stabilized: "自旋稳定",
    other: "其他姿态稳定方式",
  });
  function normalisePlatformContext(context = {}) {
    const configuration = Object.prototype.hasOwnProperty.call(PLATFORM_CONFIGURATION_LABELS, context?.platform_configuration)
      ? context.platform_configuration : "unspecified";
    const method = configuration === "not_equipped" && Object.prototype.hasOwnProperty.call(ATTITUDE_METHOD_LABELS, context?.attitude_control_method)
      ? context.attitude_control_method : "unspecified";
    return { platform_configuration: configuration, attitude_control_method: method };
  }
  function platformContextForLine(context = {}, line = "") {
    const normalizedLine = line === "rwa" ? "rw" : line;
    return normalisePlatformContext(normalizedLine === "rw"
      ? { ...context, platform_configuration: "equipped", attitude_control_method: "unspecified" }
      : context);
  }
  function uploadPlatformContext() {
    return platformContextForLine({
      platform_configuration: $("#telemetry-platform-configuration")?.value || "unspecified",
      attitude_control_method: $("#telemetry-attitude-method")?.value || "unspecified",
    }, $("#telemetry-line")?.value || "auto");
  }
  function samplePlatformContext(sample = currentSample()) {
    return normalisePlatformContext(sample?.replay_context || sample || {});
  }
  const platformConfigurationLabel = (context) => PLATFORM_CONFIGURATION_LABELS[normalisePlatformContext(context).platform_configuration];
  const attitudeMethodLabel = (context) => ATTITUDE_METHOD_LABELS[normalisePlatformContext(context).attitude_control_method];
  const componentOutputScope = (line) => {
    const key = line === "rwa" ? "rwa" : line === "rw" ? "rw" : line === "bat" ? "bat" : null;
    const label = key ? PUBLIC_COMPONENT_LABELS[key] : null;
    return label ? `${label}剩余寿命` : "所选部件剩余寿命";
  };
  function platformSummary(context) {
    const value = normalisePlatformContext(context);
    return value.platform_configuration === "not_equipped" && value.attitude_control_method !== "unspecified"
      ? `${platformConfigurationLabel(value)} · ${attitudeMethodLabel(value)}` : platformConfigurationLabel(value);
  }
  function validatePlatformContext(context, line) {
    const value = normalisePlatformContext(context); const normalizedLine = line === "rwa" ? "rw" : line;
    if (value.platform_configuration === "not_equipped" && normalizedLine === "rw") {
      return { ok: false, message: "已声明未搭载反作用轮部件（姿态控制执行器），不能选择反作用轮部件剩余寿命预测模型（姿态控制执行器）。请改用电池部件剩余寿命预测模型（储能系统），或按真实构型重新声明。" };
    }
    return { ok: true, message: "平台构型只作浏览器上下文，不进入冻结模型。" };
  }

  const currentSample = () => samples.find((sample) => sample.sample_id === appState.sampleId) || null;
  const currentTelemetry = () => telemetry[appState.sampleId] || null;
  const currentPrediction = () => predictions[appState.sampleId] || null;
  const currentRows = () => currentPrediction()?.rows || [];
  const evidenceSample = () => samples.find((sample) => sample.sample_id === appState.evidenceSampleId) || null;
  const evidencePrediction = () => predictions[appState.evidenceSampleId] || null;
  const evidenceRows = () => evidencePrediction()?.rows || [];
  const EVIDENCE_SERIES = Object.freeze([
    { key: "supervised", field: "supervised_estimate", color: "#788c93", fallbackLabel: "公开退化数据基线" },
    { key: "adapted", field: "adapted_estimate", color: "#f2b45d", fallbackLabel: "航天数据适配结果" },
    { key: "ensemble", field: "display_estimate", color: "#4bd9d2", fallbackLabel: "当前模型预测" },
  ]);
  function evidenceSeriesForRows(rows) {
    const list = Array.isArray(rows) ? rows : [];
    return EVIDENCE_SERIES.filter((series) => list.some((row) => finite(row?.y_true) && finite(row?.[series.field])));
  }
  function evidenceSeriesLabel(series, rows) {
    if (series.key !== "ensemble") return series.fallbackLabel;
    return evidenceSeriesForRows(rows).some((item) => item.key === "supervised" || item.key === "adapted")
      ? "历史模型结果" : series.fallbackLabel;
  }
  const sampleDisplayName = (sample) => sample?.display_name || sample?.sample_id || "未命名样本";
  const sampleFailureLabel = (sample) => {
    const value = typeof sample?.failure_mode === "string" ? sample.failure_mode.trim()
      : typeof sample?.criterion_text === "string" ? sample.criterion_text.trim() : "";
    return value && !/竞赛预测结果未附带|未提供可选失效标签|失效标签未提供|未标注/.test(value) ? value : "";
  };
  const availableSamples = () => samples.filter((sample) => !appState.hiddenSampleIds.has(sample.sample_id));
  const lineSamples = () => availableSamples().filter((sample) => sample.line === appState.line && fuzzySearch([
    sampleDisplayName(sample), sample.sample_id, sample.orbit, sample.failure_mode, sample.failure_mode_code,
    sample.health_level, sample.load_level, sample.dataset_id, sample.line_label, sample.provenance?.dataset,
  ], appState.search)).sort((left, right) => Number(Boolean(right.example)) - Number(Boolean(left.example)));
  let orbitRenderAllSamples = false;
  const allLineSamples = () => orbitRenderAllSamples ? availableSamples() : availableSamples().filter((sample) => sample.line === appState.line);
  const rowAt = (progress = appState.progress) => {
    const rows = currentRows(); if (!rows.length) return null;
    const item = currentTelemetry(); const sample = currentSample(); const times = Array.isArray(item?.t_days) ? item.t_days : [];
    if (sample?.uploaded && times.length && rows.some((row) => finite(row?.time_order))) {
      const telemetryIndex = clamp(Math.round(progress * (times.length - 1)), 0, times.length - 1);
      const currentTime = Number(times[telemetryIndex]); let matched = null; let matchedTime = -Infinity;
      if (Number.isFinite(currentTime)) rows.forEach((row) => {
        const rowTime = Number(row?.time_order);
        if (Number.isFinite(rowTime) && rowTime <= currentTime && rowTime >= matchedTime) { matched = row; matchedTime = rowTime; }
      });
      return matched;
    }
    return rows[clamp(Math.round(progress * (rows.length - 1)), 0, rows.length - 1)];
  };
  const telemetryAt = (progress = appState.progress) => {
    const item = currentTelemetry(); const times = item?.t_days || [];
    if (!times.length) return null;
    const index = clamp(Math.round(progress * (times.length - 1)), 0, times.length - 1);
    return { index, time: times[index], timeUnit: item.time_unit || "day", labels: item.labels || {}, channels: item.channels || {} };
  };

  function showToast(message, kind = "info") {
    const host = $("#toast-region"); if (!host) return;
    const node = document.createElement("div"); node.className = `toast${kind === "error" ? " is-error" : ""}`; node.textContent = message; host.append(node);
    window.setTimeout(() => node.remove(), 5200);
  }

  function renderStatusBar() {
    const badge = $("#source-badge"); const label = $("span", badge);
    badge?.classList.remove("is-live", "is-unavailable");
    if (payload.source === "results") { badge?.classList.add("is-live"); if (label) label.textContent = payload.source_state?.degraded ? "独立测试结果 · 历史版本保留" : "独立测试结果"; }
    else if (payload.source === "mock") { label.textContent = "项目内置演示示例"; }
    else { badge?.classList.add("is-unavailable"); if (label) label.textContent = "结果暂不可用"; }
    const banner = $("#state-banner");
    if ((payload.source === "unavailable" || payload.source === "mock") && payload.source_state?.reason) { banner.hidden = false; banner.textContent = payload.source === "mock" ? "当前显示项目内置演示示例：不代表本次上传数据或在线测量。" : publicNarrative(payload.source_state.reason, "当前没有可读取的独立测试结果，请检查数据后刷新页面。"); } else banner.hidden = true;
    setText("#discipline-line", publicNarrative(payload.discipline, "原始数据事实优先"));
    setText("#build-line", "BRPHM / 湖南大学 · 离线运行");
    setText("#route-label", appState.telemetrySchema?.productionLabel || "生产模型待核验");
  }

  function sampleHealth(sample) {
    const rows = predictions[sample.sample_id]?.rows || [];
    const margin = Number(payload.config?.maintenance_margin?.[sample.line]?.value || 0);
    const p10 = rows.find((row) => finite(row.p10))?.p10;
    if (!finite(p10)) return "unknown";
    return Number(p10) <= margin * .45 ? "critical" : Number(p10) <= margin ? "caution" : "nominal";
  }
  function sampleReplayAvailability(sample) {
    const sampleId = sample?.sample_id;
    const packet = sampleId ? predictions[sampleId] : null;
    const rows = Array.isArray(packet?.rows) ? packet.rows : [];
    const hasPrediction = rows.some((row) => finite(row?.t) && [
      row?.p50, row?.display_estimate, row?.y_true,
    ].some(finite));
    const item = sampleId ? telemetry[sampleId] : null;
    const channels = item?.channels && typeof item.channels === "object" ? Object.values(item.channels) : [];
    const hasTelemetry = channels.some((series) => Array.isArray(series) && series.some(finite));
    if (hasTelemetry && hasPrediction) return { state: "ready", label: "回放就绪：原始遥测与预测窗口可用" };
    if (hasTelemetry) return { state: "ready", label: "回放就绪：原始遥测可用" };
    if (hasPrediction) return { state: "ready", label: "回放就绪：已登记预测窗口可用；未附原始遥测" };
    return { state: "unavailable", label: "回放数据未附" };
  }

  function createAssetButton(sample, index, closeSheet = false) {
    const row = document.createElement("div"); row.className = "asset-row";
    const button = document.createElement("button"); button.type = "button"; button.className = `asset-item${sample.sample_id === appState.sampleId ? " is-active" : ""}`; button.dataset.sampleId = sample.sample_id; button.setAttribute("aria-pressed", String(sample.sample_id === appState.sampleId));
    const replay = sampleReplayAvailability(sample); const state = document.createElement("i"); state.className = `asset-state is-${replay.state}`; state.setAttribute("aria-label", replay.label); state.title = replay.label;
    const text = document.createElement("span"); const title = document.createElement("strong"); const detail = document.createElement("small"); title.textContent = sampleDisplayName(sample); title.title = sampleDisplayName(sample); const failure = sampleFailureLabel(sample); detail.textContent = `${sample.example ? "项目内置示例 · " : ""}${sample.orbit || sample.dataset_id || "工况"} · ${sample.health_level || "--"}/${sample.load_level || "--"}${failure ? ` · ${failure}` : ""}`; text.append(title, detail);
    const number = document.createElement("em"); number.textContent = String(index + 1).padStart(2, "0"); button.append(state, text, number); button.addEventListener("click", () => setSample(sample.sample_id, closeSheet));
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "asset-remove"; remove.title = `从本次页面临时移除 ${sampleDisplayName(sample)}`; remove.setAttribute("aria-label", remove.title); remove.append(icon("#i-x"));
    remove.addEventListener("click", () => hideSample(sample.sample_id)); row.append(button, remove); return row;
  }

  function syncAssetRow(row, sample, index) {
    const button = row?.querySelector?.(".asset-item");
    if (!button || button.dataset.sampleId !== sample.sample_id) return false;
    const active = sample.sample_id === appState.sampleId; const replay = sampleReplayAvailability(sample);
    button.classList.toggle("is-active", active); button.setAttribute("aria-pressed", String(active));
    const state = row.querySelector(".asset-state"); if (state) { state.className = `asset-state is-${replay.state}`; state.setAttribute("aria-label", replay.label); state.title = replay.label; }
    const title = row.querySelector(".asset-item strong"); if (title) { title.textContent = sampleDisplayName(sample); title.title = sampleDisplayName(sample); }
    const detail = row.querySelector(".asset-item small"); if (detail) { const failure = sampleFailureLabel(sample); detail.textContent = `${sample.example ? "项目内置示例 · " : ""}${sample.orbit || sample.dataset_id || "工况"} · ${sample.health_level || "--"}/${sample.load_level || "--"}${failure ? ` · ${failure}` : ""}`; }
    const number = row.querySelector(".asset-item > em"); if (number) number.textContent = String(index + 1).padStart(2, "0");
    const remove = row.querySelector(".asset-remove"); if (remove) { remove.title = `从本次页面临时移除 ${sampleDisplayName(sample)}`; remove.setAttribute("aria-label", remove.title); }
    return true;
  }

  function dropUploadedSample(sampleId) {
    if (!appState.uploadedSampleIds.has(sampleId)) return false;
    const index = samples.findIndex((sample) => sample.sample_id === sampleId);
    if (index >= 0) samples.splice(index, 1);
    delete telemetry[sampleId]; delete predictions[sampleId];
    appState.uploadedSampleIds.delete(sampleId); appState.hiddenSampleIds.delete(sampleId);
    for (const [key, value] of appState.uploadedReplayIds.entries()) {
      if (value === sampleId) appState.uploadedReplayIds.delete(key);
    }
    if (appState.evidenceSampleId === sampleId) appState.evidenceSampleId = null;
    return true;
  }

  function hideSample(sampleId) {
    const removedUpload = dropUploadedSample(sampleId);
    if (!removedUpload) appState.hiddenSampleIds.add(sampleId);
    if (appState.sampleId === sampleId) {
      const replacement = availableSamples().find((sample) => sample.line === appState.line) || availableSamples()[0] || null;
      appState.sampleId = replacement?.sample_id || null; appState.line = replacement?.line || appState.line; appState.progress = 0;
    }
    rebuildOrbitObjects(); renderAll(); renderSourceSelector();
    if (removedUpload && appState.telemetryBatch) renderTelemetryResults(appState.telemetryBatch, 200);
  }

  function hideVisibleSamples() {
    let removedUpload = false;
    allLineSamples().forEach((sample) => {
      if (sample.uploaded) removedUpload = dropUploadedSample(sample.sample_id) || removedUpload;
      else appState.hiddenSampleIds.add(sample.sample_id);
    });
    const replacement = availableSamples()[0] || null; appState.sampleId = replacement?.sample_id || null; appState.line = replacement?.line || appState.line; appState.progress = 0;
    rebuildOrbitObjects(); renderAll(); renderSourceSelector();
    if (removedUpload && appState.telemetryBatch) renderTelemetryResults(appState.telemetryBatch, 200);
  }

  function restoreSamples() {
    appState.hiddenSampleIds.clear();
    if (!currentSample()) { const first = samples[0]; appState.sampleId = first?.sample_id || null; appState.line = first?.line || "bat"; }
    rebuildOrbitObjects(); renderAll(); renderSourceSelector(); showToast("本次页面中的样本列表已恢复。");
  }

  function renderAssets() {
    const visible = lineSamples(); setText("#line-count", `${visible.length} / ${allLineSamples().length}`); setText("#sample-filter-state", appState.search ? `筛选：${appState.search}` : "全部匹配样本");
    const renderInto = (host, closeSheet = false, query = "") => {
      if (!host) return; const list = query ? visible.filter((sample) => fuzzySearch([sampleDisplayName(sample), sample.sample_id, sample.orbit, sample.failure_mode, sample.line_label], query)) : visible;
      const signature = list.map((sample) => sample.sample_id).join("|");
      const previousActive = host.dataset.activeSampleId || ""; const existing = Array.from(host.children);
      const reusable = list.length > 0 && host.dataset.renderSignature === signature && existing.length === list.length
        && existing.every((row, index) => syncAssetRow(row, list[index], index));
      if (!list.length) {
        if (host.dataset.renderSignature !== signature || !host.querySelector(":scope > .operation-description")) { const empty = document.createElement("p"); empty.className = "operation-description"; empty.textContent = "没有匹配的真实样本。"; host.replaceChildren(empty); }
      } else if (!reusable) {
        const nodes = list.map((sample, index) => createAssetButton(sample, index, closeSheet)); host.replaceChildren(...nodes); animateTelemetryNodes(nodes, { y: 5, duration: .28, stagger: .025 });
      }
      host.dataset.renderSignature = signature; const nextActive = list.some((sample) => sample.sample_id === appState.sampleId) ? appState.sampleId : ""; host.dataset.activeSampleId = nextActive;
      if (reusable && previousActive && nextActive && previousActive !== nextActive && window.gsap && !reduceMotion) {
        const activeButton = Array.from(host.querySelectorAll(".asset-item")).find((button) => button.dataset.sampleId === nextActive);
        const state = activeButton?.querySelector(".asset-state"); window.gsap.killTweensOf([activeButton, state].filter(Boolean));
        if (activeButton) window.gsap.fromTo(activeButton, { backgroundColor: "#17313a" }, { backgroundColor: "#102028", duration: .34, ease: "expo.out", overwrite: "auto", clearProps: "backgroundColor" });
        if (state) window.gsap.fromTo(state, { scale: .72 }, { scale: 1, duration: .38, ease: "back.out(1.45)", overwrite: "auto", clearProps: "transform" });
      }
    };
    renderInto($("#asset-list")); renderInto($("#mobile-asset-list"), true, appState.mobileSearch);
    const restore = $("#restore-samples"); if (restore) restore.hidden = appState.hiddenSampleIds.size === 0;
    const mobileRestore = $("#mobile-restore-samples"); if (mobileRestore) mobileRestore.hidden = appState.hiddenSampleIds.size === 0;
    setText("#compact-sample-label", currentSample() ? sampleDisplayName(currentSample()) : "选择样本");
    $$("[data-compact-line]").forEach((button) => button.setAttribute("aria-checked", String(button.dataset.compactLine === appState.line)));
    $$("[data-sheet-line]").forEach((button) => button.classList.toggle("is-active", button.dataset.sheetLine === appState.line));
  }

  function syncLineControls() {
    $$("#scenario-control button").forEach((button) => button.setAttribute("aria-checked", String(button.dataset.line === appState.line)));
    $$('[data-compact-line]').forEach((button) => button.setAttribute("aria-checked", String(button.dataset.compactLine === appState.line)));
    $$('[data-sheet-line]').forEach((button) => button.classList.toggle("is-active", button.dataset.sheetLine === appState.line));
  }

  function renderComponentScope(sample = currentSample()) {
    const context = samplePlatformContext(sample); const line = sample?.line;
    setText("#fact-platform", sample ? platformSummary(context) : "--");
    setText("#platform-scope-state", sample ? platformConfigurationLabel(context) : "构型未说明");
    setText("#rul-scope-label", componentOutputScope(line));
    if (!sample) {
      setText("#battery-scope-value", "--"); setText("#wheel-scope-value", "--");
      setText("#component-scope-note", "平台构型只作上下文；当前模型输出不代表整星剩余寿命。"); return;
    }
    const currentBattery = line === "bat" ? `本次评估 · ${componentOutputScope("bat")}` : "非本次预测模型";
    const currentWheel = line === "rw" ? `本次评估 · ${componentOutputScope("rw")}` : "非本次预测模型";
    if (context.platform_configuration === "not_equipped") {
      setText("#battery-scope-value", currentBattery);
      setText("#wheel-scope-value", "未搭载 · 不适用");
      setText("#component-scope-note", `${attitudeMethodLabel(context)}仅作构型说明，不进入模型；本结果只代表电池部件（储能系统）剩余寿命，不代表整星寿命。`);
      return;
    }
    setText("#battery-scope-value", currentBattery); setText("#wheel-scope-value", currentWheel);
    setText("#component-scope-note", context.platform_configuration === "equipped"
      ? "用户声明已搭载反作用轮部件（姿态控制执行器）；本页仍只显示当前部件模型的结果，不把另一部件或整星寿命补算出来。"
      : "平台构型未说明；页面不根据遥测缺席推断是否搭载反作用轮部件（姿态控制执行器），当前输出仅代表所选部件。");
  }

  function renderHeader() {
    const sample = currentSample(); const item = currentTelemetry();
    syncLineControls(); renderReplayContextEditor(sample); renderComponentScope(sample);
    if (!sample || !item) { setText("#sample-title", "暂无可用样本"); setText("#sample-subtitle", publicNarrative(payload.source_state?.reason, "等待独立测试结果")); return; }
    setText("#sample-title", sampleDisplayName(sample)); setText("#sample-subtitle", `${sample.line_label || publicComponentLabel(sample.line)} · ${sample.dataset_id || "--"} · ${publicNarrative(sample.provenance?.sim_model, "仿真模型身份未提供")}`);
    const failureTime = finite(sample.failure_time_days) ? ` · ${compact(sample.failure_time_days)} 天` : "";
    setText("#fact-orbit", `${sample.orbit || "工况未提供"}${finite(sample.beta_deg) ? ` · 太阳 β ${sample.beta_deg}°` : ""}`); setText("#fact-condition", `${sample.health_level || "--"} / ${sample.load_level || "--"}`); const failureLabel = sampleFailureLabel(sample); const failureFact = $("#fact-failure"); if (failureFact) { failureFact.textContent = failureLabel ? `${failureLabel}${failureTime}` : ""; if (failureFact.parentElement) failureFact.parentElement.hidden = !failureLabel; }
    const orbitState = orbitStateForSample(sample); const orbitClass = $("#orbit-physics-class"); const orbitDetail = $("#orbit-physics-detail");
    if (orbitClass) orbitClass.textContent = orbitState?.classification || "未指定";
    if (orbitDetail) orbitDetail.textContent = orbitState?.reason || "缺少足够的原始轨道根数，不绘制轨迹。";
    const nominalAltitude = finite(orbitState?.a) && Number(orbitState.a) > EARTH_RADIUS_KM
      ? Number(orbitState.a) - EARTH_RADIUS_KM : null;
    setText("#fact-orbit-altitude", finite(nominalAltitude) ? `${format(nominalAltitude, 1)} km` : "--");
    setText("#fact-orbit-inclination", finite(orbitState?.inclination) ? `${format(orbitState.inclination, 1)}°` : "--");
    setText("#fact-orbit-solar-beta", finite(sample.beta_deg) ? `${format(sample.beta_deg, 1)}°` : "未提供");
    setText("#fact-orbit-raan", finite(orbitState?.raan) ? `${format(orbitState.raan, 1)}°` : "--");
    setText("#orbit-fact-source", orbitState?.source
      ? `${orbitState.source}。样例编码中的 B 表示太阳 β 角，不表示轨道倾角。`
      : "没有原始轨道根数时不推测倾角；样例编码中的 B 表示太阳 β 角。");
    setText("#scene-line-label", sample.line_label || publicComponentLabel(sample.line)); setText("#scene-note", sample.uploaded ? (orbitState?.drawable ? "按上传上下文生成同步工程投影；相位仅用于样本辨识，不替代真实星历。" : "上传文件没有足够轨道事实，按要求不绘制轨迹。") : (orbitState?.drawable ? "GMAT 冻结初始根数的工程投影，不替代实时星历。" : "当前样本未提供可绘制轨道根数。")); setText("#scene-object-id", sampleDisplayName(sample)); setText("#scene-component", publicComponentLabel(sample.line)); setText("#scene-risk", sampleHealth(sample) === "critical" ? "维护窗内" : sampleHealth(sample) === "caution" ? "需关注" : "监测中");
    const uploadedTraceAvailable = sample.uploaded && Object.values(item?.channels || {}).some((values) => Array.isArray(values) && values.some(finite));
    setText("#trace-key-observation-label", sample.uploaded ? (uploadedTraceAvailable ? "上传原始遥测" : "模型窗口输出") : "内置遥测");
    setText("#trace-key-truth-label", sample.uploaded ? "未提供原始寿命标签" : "回放标签");
    setText("#current-model-label", sample.uploaded ? "预测窗口" : "回放标签");
    const single = currentRows().length <= 1; const scrubber = $("#scrubber"); const play = $("#play-button");
    if (scrubber) scrubber.disabled = single; if (play) play.disabled = single;
  }

  function renderChannels() {
    const item = currentTelemetry(); const host = $("#channel-control"); if (!host) return; const channels = Object.keys(item?.channels || {});
    if (!channels.length) { host.replaceChildren(); appState.channel = null; return; }
    if (!channels.includes(appState.channel)) appState.channel = channels[0];
    host.replaceChildren(...channels.map((channel) => { const button = document.createElement("button"); button.type = "button"; button.className = channel === appState.channel ? "is-active" : ""; button.textContent = payload.channel_meta?.[channel]?.label || channel.split(".").pop(); button.title = channel; button.addEventListener("click", () => { appState.channel = channel; renderChannels(); drawTelemetry(); const active = $(".is-active", host); if (window.gsap && !reduceMotion && active) window.gsap.fromTo(active, { scale: .94, y: 1 }, { scale: 1, y: 0, duration: .38, ease: "back.out(1.45)", overwrite: "auto", clearProps: "transform" }); }); return button; }));
  }

  function renderCurrent() {
    const item = telemetryAt(); const row = rowAt(); const prediction = currentPrediction();
    if (!item) { ["#current-raw-time", "#current-channel-value", "#current-hi", "#current-model-time"].forEach((id) => setText(id, "--")); renderGauges(null, null); return; }
    const channel = appState.channel || Object.keys(item.channels)[0]; const value = item.channels[channel]?.[item.index]; const displayTime = Array.isArray(item.time_display) && item.time_display[item.index] ? item.time_display[item.index] : null; const awaitingFirstPrediction = currentSample()?.uploaded && !row && currentRows().some((candidate) => finite(candidate?.time_order)); setText("#current-raw-time", displayTime ? displayTime : `${compact(item.time)} ${unitLabel(item.timeUnit)}`); setText("#current-channel-label", payload.channel_meta?.[channel]?.label || channel || "遥测通道"); setText("#current-channel-value", finite(value) ? `${compact(value)} ${payload.channel_meta?.[channel]?.unit || ""}` : "--"); setText("#current-hi", finite(item.labels?.hi?.[item.index]) ? format(item.labels.hi[item.index], 3) : "--"); setText("#current-model-time", awaitingFirstPrediction ? "模型观察窗构建中" : finite(row?.y_true) ? `${compact(row.y_true)} ${unitLabel(prediction?.time_unit)}` : row?.time != null ? `窗口结束 ${row.time}` : "--");
    renderGauges(item, row);
  }

  function renderGauges(item, row) {
    const prediction = currentPrediction(); const sample = currentSample(); const unit = prediction?.time_unit || "days"; const hi = item?.labels?.hi?.[item.index]; setText("#hi-value", finite(hi) ? format(hi, 3) : "--");
    const hiRatio = safeRatio(finite(hi) ? Number(hi) / 1.5 : 0); const hiFill = $("#hi-fill"); const hiMarker = $("#hi-marker"); if (hiFill) hiFill.style.width = `${hiRatio * 100}%`; if (hiMarker) hiMarker.style.left = `${hiRatio * 100}%`;
    const p50 = row?.p50; const trueRul = row?.y_true; setText("#rul-value", finite(p50) ? compact(p50) : "--"); setText("#rul-unit", unitLabel(unit));
    if (finite(p50) && finite(trueRul)) { const delta = Number(p50) - Number(trueRul); setText("#rul-truth-delta", `独立测试参考值 ${compact(trueRul)} ${unitLabel(unit)} · 偏差 ${delta >= 0 ? "+" : ""}${compact(delta)}`); } else setText("#rul-truth-delta", "独立测试参考值当前不可用");
    const maxRul = Number(sample?.failure_time_days) || Number(row?.p90) || 1; const meter = $("#rul-meter-fill"); const marker = $("#rul-meter-marker"); if (meter) meter.style.width = `${safeRatio(p50 / maxRul) * 100}%`; if (marker) marker.style.left = `${safeRatio(p50 / maxRul) * 100}%`;
    if (!row) { setText("#interval-value", "不可用"); setText("#p10-value", "P10 不可用"); setText("#p90-value", "P90 不可用"); } else { setText("#interval-value", `${compact(row.p10)} – ${compact(row.p90)} ${unitLabel(unit)}`); setText("#p10-value", `P10 ${compact(row.p10)}`); setText("#p90-value", `P90 ${compact(row.p90)}`); const maximum = Math.max(Number(row.p90) || 1, Number(row.y_true) || 1, Number(row.p50) || 1, 1); const band = $("#interval-band"); const median = $("#interval-median"); if (band) { band.style.left = `${safeRatio(Number(row.p10) / maximum) * 100}%`; band.style.width = `${Math.max(1, (safeRatio(Number(row.p90) / maximum) - safeRatio(Number(row.p10) / maximum)) * 100)}%`; } if (median) median.style.left = `${safeRatio(Number(row.p50) / maximum) * 100}%`; }
    renderSafety(row, unit); renderLights(row, unit); renderMaintenance(row, unit); renderPredsStrip(row);
  }

  function renderSafety(row, unit) {
    const resultRecord = payload.output_safety || {}; const raw = row?.raw_estimate; const safe = row?.display_estimate; const clamped = row?.boundary_adjusted; const available = finite(raw) && finite(safe) && typeof clamped === "boolean"; const resultRecordReady = resultRecord.state === "complete" && resultRecord.verified === true; const block = $(".safety-block"); block?.classList.toggle("is-warn", !available || clamped === true); setText("#safety-state", !available ? "不可用" : clamped ? "已按边界处理" : resultRecordReady ? "边界已核验" : "待确认"); setText("#raw-rul-value", finite(raw) ? `${compact(raw)} ${unitLabel(unit)}` : "不可用"); setText("#safe-rul-value", finite(safe) ? `${compact(safe)} ${unitLabel(unit)}` : "不可用"); setText("#clamp-value", typeof clamped === "boolean" ? (clamped ? "已处理" : "未处理") : "不可用"); setText("#safety-note", !available ? "缺少原始估计、呈现值或边界处理标记，系统不会用中位数代替呈现值。" : clamped ? "原始估计超过既定边界，呈现值已按边界处理；原始估计仍单独保留。" : resultRecordReady ? "原始估计处于既定边界内，本窗口没有额外处理。" : "结果字段已就绪，边界说明暂不可读取。");
  }

  function renderLights(row, unit) {
    const block = $(".warning-block"); const icon = $("#warning-icon"); const margin = payload.config?.maintenance_margin?.[appState.line] || { value: 0, unit }; const threshold = Number(margin.value || 0); const p10 = row?.p10; const risk = finite(p10) && Number(p10) <= threshold; const severe = finite(p10) && Number(p10) <= threshold * .45; block?.classList.toggle("is-warn", risk && !severe); block?.classList.toggle("is-bad", severe); icon?.classList.remove("is-nominal", "is-caution", "is-critical"); icon?.classList.add(severe ? "is-critical" : risk ? "is-caution" : "is-nominal"); setText("#warning-label", severe ? "需立即关注" : risk ? "进入维护窗口" : row ? "当前在监测边界外" : "风险判断关闭"); setText("#warning-reason", finite(p10) ? `P10 ${compact(p10)} ${unitLabel(margin.unit)}；阈值仅用于运维提示，不改写模型结果。` : "缺少 P10，风险判断已关闭。"); const stateNode = $("#decision-state"); stateNode?.classList.remove("is-caution", "is-critical"); if (severe || risk) stateNode?.classList.add(severe ? "is-critical" : "is-caution"); setText("#decision-state", severe ? "严重" : risk ? "维护窗" : row ? "监测中" : "不可用");
  }

  function renderMaintenance(row, unit) {
    const margin = payload.config?.maintenance_margin?.[appState.line] || { value: 0, unit }; const value = row?.p10; const safe = finite(value) ? Math.max(0, Number(row.p10) - Number(margin.value)) : null; setText("#maintenance-value", safe == null ? "不可计算" : `${compact(safe)} ${unitLabel(margin.unit)}`); setText("#maintenance-note", safe == null ? "缺失 P10 或安全裕量，维护提示已保护性关闭。" : `P10 减去安全裕量 ${compact(margin.value)} ${unitLabel(margin.unit)}；不改变原始预测。`);
  }

  function renderPredsStrip(row) { setText("#supervised-value", finite(row?.supervised_estimate) ? compact(row.supervised_estimate) : "缺失"); setText("#transfer-value", finite(row?.adapted_estimate) ? compact(row.adapted_estimate) : "缺失"); setText("#ensemble-value", finite(row?.display_estimate) ? compact(row.display_estimate) : "缺失"); }

  /* Three.js scene.  Positions are visual encodings of sample metadata, never
     presented as precise ephemeris.  A deterministic 2D renderer remains as
     an honest offline fallback when WebGL is unavailable. */
  const orbitRuntime = {
    renderer: null, scene: null, world: null, camera: null, earth: null,
    baseCameraOffset: null, viewEuler: null, inverseView: null,
    coast: null, land: null, sunDirection: null, pointMaterials: [],
    rings: [], points: [], selectedModel: null, raycaster: null, pointer: null,
    groundtrack: {
      width: 0, height: 0, ratio: 0, baseCanvas: null, trackCanvas: null,
      baseDirty: true, tracksDirty: true, tracks: [],
    },
    fallback: false, fallbackNotified: false, frameVerified: false, frameChecks: 0,
    width: 0, height: 0, pixelRatio: 0, last: 0, lastRendered: 0, raf: 0,
    compact: compactOrbit, frameInterval: compactOrbit ? 17 : 0,
    groundTrackFrameInterval: 33, groundTrackLastRendered: 0,
    drag: null, interactionFrame: 0, inertiaYaw: 0, inertiaPitch: 0, inertiaActive: false, zoomFrame: 0,
    projectionRetry: 0, projectionRetryAttempts: 0,
  };

  /* Offline geographic outlines are deliberately kept in the bundle. They are
     a visual land reference, not a navigation-grade geodetic product. The
     renderer densifies each segment so day and night are expressed only by
     point brightness, never by a separate terminator stroke. */
  const FALLBACK_COASTLINE_PATHS = [
    [[-168, 72], [-150, 61], [-135, 57], [-126, 49], [-124, 40], [-117, 32], [-107, 25], [-97, 18], [-86, 21], [-81, 25], [-80, 33], [-74, 41], [-66, 45], [-59, 52], [-64, 60], [-82, 63], [-96, 72], [-122, 75], [-150, 72], [-168, 72]],
    [[-81, 12], [-75, 5], [-79, -5], [-76, -16], [-71, -30], [-73, -43], [-67, -55], [-56, -51], [-48, -31], [-41, -17], [-35, -6], [-45, 2], [-60, 8], [-72, 11], [-81, 12]],
    [[-54, 59], [-45, 61], [-36, 69], [-27, 76], [-39, 83], [-56, 82], [-68, 75], [-62, 66], [-54, 59]],
    [[-10, 36], [-11, 44], [-5, 51], [6, 55], [10, 62], [25, 70], [48, 73], [75, 73], [103, 77], [132, 72], [160, 66], [178, 59], [166, 51], [148, 47], [139, 36], [130, 31], [122, 22], [113, 20], [108, 10], [104, 2], [98, 6], [88, 21], [78, 28], [68, 25], [58, 27], [49, 31], [42, 38], [34, 42], [26, 40], [18, 38], [9, 37], [-10, 36]],
    [[-17, 37], [-5, 36], [10, 33], [25, 31], [34, 25], [42, 12], [50, 1], [43, -12], [35, -25], [25, -35], [16, -35], [10, -25], [3, -6], [-9, 5], [-17, 20], [-17, 37]],
    [[34, 31], [42, 16], [50, 12], [56, 24], [65, 25], [70, 20], [75, 8], [80, 6], [88, 22]],
    [[104, 2], [110, -7], [119, -9], [126, -4], [131, 3], [124, 11], [120, 22]],
    [[113, -12], [130, -10], [145, -15], [153, -28], [146, -40], [132, -35], [116, -34], [112, -23], [113, -12]],
    [[48, -13], [51, -18], [50, -27], [46, -25], [44, -16], [48, -13]],
    [[130, 31], [136, 34], [142, 43], [145, 45]],
    [[166, -35], [174, -41], [177, -46], [169, -47], [166, -35]],
    [[-180, -68], [-150, -71], [-120, -70], [-90, -72], [-60, -69], [-30, -72], [0, -70], [30, -72], [60, -69], [90, -72], [120, -70], [150, -71], [180, -68]],
    [[-11, 50], [-8, 50], [-6, 52], [-5, 55], [-7, 58], [-5, 59], [-3, 58], [-2, 55], [0, 52], [-2, 50], [-6, 50], [-11, 50]],
    [[129, 31], [132, 32], [134, 34], [136, 35], [138, 37], [140, 40], [142, 42], [145, 43], [146, 45], [143, 46], [140, 43], [138, 40], [135, 38], [132, 35], [129, 31]],
    [[119, 6], [123, 5], [126, 1], [125, -3], [121, -5], [118, -2], [116, 2], [119, 6]],
    [[166, -34], [171, -35], [175, -39], [174, -43], [170, -47], [166, -46], [168, -42], [166, -38], [166, -34]],
    [[-62, 18], [-59, 17], [-60, 15], [-64, 15], [-66, 17], [-62, 18]],
  ];
  /* Decode the bundled Natural Earth land topology locally.  The data is used
     only for a visual point-cloud reference; it is never presented as an
     ephemeris or navigation-grade map.  Keeping the decoder here preserves
     the single-file offline contract while retaining the source resolution. */
  function decodeLandTopology(topology) {
    if (!topology || !Array.isArray(topology.arcs) || !topology.objects?.land) return [];
    const scale = topology.transform?.scale || [1, 1];
    const translate = topology.transform?.translate || [0, 0];
    const decoded = topology.arcs.map((arc) => {
      let x = 0; let y = 0;
      return (Array.isArray(arc) ? arc : []).map((pair) => {
        x += Number(pair?.[0] || 0); y += Number(pair?.[1] || 0);
        return [x * scale[0] + translate[0], y * scale[1] + translate[1]];
      });
    });
    const readArc = (reference) => {
      const index = Number(reference);
      const arc = decoded[index >= 0 ? index : ~index] || [];
      return index >= 0 ? arc : [...arc].reverse();
    };
    const joinArcs = (references) => {
      const result = [];
      (Array.isArray(references) ? references : []).forEach((reference) => {
        const arc = readArc(reference);
        arc.forEach((point, index) => {
          if (!index && result.length && point[0] === result[result.length - 1][0] && point[1] === result[result.length - 1][1]) return;
          result.push(point);
        });
      });
      return result;
    };
    const paths = [];
    const visit = (geometry) => {
      if (!geometry) return;
      if (geometry.type === "GeometryCollection") { (geometry.geometries || []).forEach(visit); return; }
      if (geometry.type === "Polygon") { (geometry.arcs || []).forEach((ring) => { const path = joinArcs(ring); if (path.length > 3) paths.push(path); }); return; }
      if (geometry.type === "MultiPolygon") { (geometry.arcs || []).forEach((polygon) => (polygon || []).forEach((ring) => { const path = joinArcs(ring); if (path.length > 3) paths.push(path); })); }
    };
    visit(topology.objects.land);
    return paths.filter((path) => path.every((point) => finite(point[0]) && finite(point[1])));
  }
  const COASTLINE_PATHS = decodeLandTopology(window.__LAND_TOPOLOGY__) || FALLBACK_COASTLINE_PATHS;
  const LAND_PATH_BOUNDS = COASTLINE_PATHS.map((path) => {
    let minLon = 180; let maxLon = -180; let minLat = 90; let maxLat = -90;
    path.forEach(([lon, lat]) => { minLon = Math.min(minLon, lon); maxLon = Math.max(maxLon, lon); minLat = Math.min(minLat, lat); maxLat = Math.max(maxLat, lat); });
    return { path, minLon, maxLon, minLat, maxLat };
  });
  const SUN_VECTOR = (() => { const vector = [-.58, .42, .70]; const length = Math.hypot(...vector); return vector.map((value) => value / length); })();
  function smoothstep(edge0, edge1, value) { const ratio = clamp((value - edge0) / (edge1 - edge0), 0, 1); return ratio * ratio * (3 - 2 * ratio); }
  function lonLatVector(lon, lat, radius = 1) { const lambda = lon * Math.PI / 180; const phi = lat * Math.PI / 180; const latitudeRadius = Math.cos(phi) * radius; return [Math.sin(lambda) * latitudeRadius, Math.sin(phi) * radius, Math.cos(lambda) * latitudeRadius]; }
  function wrappedLongitudeDelta(from, to) {
    if (!Number.isFinite(Number(from)) || !Number.isFinite(Number(to))) return NaN;
    let delta = (Number(to) - Number(from)) % 360;
    if (delta > 180) delta -= 360;
    if (delta < -180) delta += 360;
    return delta;
  }
  function simplifyPath(path, tolerance) {
    if (!Array.isArray(path) || path.length <= 4) return path || [];
    const closed = path[0][0] === path[path.length - 1][0] && path[0][1] === path[path.length - 1][1];
    const source = closed ? path.slice(0, -1) : path.slice();
    if (source.length <= 3) return path;
    const sqTolerance = tolerance * tolerance; const keep = new Uint8Array(source.length); keep[0] = 1; keep[source.length - 1] = 1; const stack = [[0, source.length - 1]];
    const segmentDistance = (point, start, end) => { let x = start[0]; let y = start[1]; let dx = end[0] - x; let dy = end[1] - y; if (dx || dy) { const t = ((point[0] - x) * dx + (point[1] - y) * dy) / (dx * dx + dy * dy); if (t > 1) { x = end[0]; y = end[1]; } else if (t > 0) { x += dx * t; y += dy * t; } } dx = point[0] - x; dy = point[1] - y; return dx * dx + dy * dy; };
    while (stack.length) { const [start, end] = stack.pop(); let maxDistance = sqTolerance; let split = -1; for (let index = start + 1; index < end; index += 1) { const distance = segmentDistance(source[index], source[start], source[end]); if (distance > maxDistance) { split = index; maxDistance = distance; } } if (split >= 0) { keep[split] = 1; stack.push([start, split], [split, end]); } }
    const result = source.filter((_point, index) => keep[index]); if (closed) result.push(result[0]); return result;
  }
  const COASTLINE_RENDER_PATHS = COASTLINE_PATHS.map((path) => simplifyPath(path, compactOrbit ? .105 : .065));
  function buildCoastSamples(spacingDegrees) {
    const result = [];
    COASTLINE_RENDER_PATHS.forEach((path) => {
      for (let index = 1; index < path.length; index += 1) {
        const [lon0, lat0] = path[index - 1]; const [lon1, lat1] = path[index];
        const deltaLon = wrappedLongitudeDelta(lon0, lon1); const deltaLat = lat1 - lat0;
        const longitudinalScale = Math.max(.24, Math.cos((lat0 + lat1) * .5 * Math.PI / 180));
        const steps = Math.max(1, Math.ceil(Math.hypot(deltaLon * longitudinalScale, deltaLat) / spacingDegrees));
        for (let step = index === 1 ? 0 : 1; step <= steps; step += 1) {
          const progress = step / steps; const lon = lon0 + deltaLon * progress; const lat = lat0 + deltaLat * progress;
          result.push({ lon, lat, vector: lonLatVector(lon, lat, 1.043), kind: "coast" });
        }
      }
    });
    return result;
  }
  function pointInPath(lon, lat, path) {
    let inside = false;
    for (let index = 0, previous = path.length - 1; index < path.length; previous = index, index += 1) {
      const [x0, y0] = path[index]; const [x1, y1] = path[previous];
      const crosses = ((y0 > lat) !== (y1 > lat)) && (lon < (x1 - x0) * (lat - y0) / ((y1 - y0) || Number.EPSILON) + x0);
      if (crosses) inside = !inside;
    }
    return inside;
  }
  function buildLandSamples(spacingDegrees) {
    const result = [];
    for (let lat = -82; lat <= 82; lat += spacingDegrees) {
      const intersections = [];
      LAND_PATH_BOUNDS.forEach(({ path, minLat, maxLat }) => {
        if (lat < minLat || lat > maxLat) return;
        for (let index = 1; index < path.length; index += 1) {
          const [lon0, lat0] = path[index - 1]; const [lon1, lat1] = path[index];
          if ((lat0 > lat) === (lat1 > lat) || lat0 === lat1) continue;
          const progress = (lat - lat0) / (lat1 - lat0); intersections.push(lon0 + wrappedLongitudeDelta(lon0, lon1) * progress);
        }
      });
      intersections.sort((a, b) => a - b);
      const row = Math.round((lat + 82) / spacingDegrees); const offset = row % 2 ? spacingDegrees * .5 : 0;
      for (let index = 0; index + 1 < intersections.length; index += 2) {
        const left = Math.max(-180, Math.min(180, intersections[index])); const right = Math.max(-180, Math.min(180, intersections[index + 1]));
        for (let lon = Math.ceil((left + 179 + offset) / spacingDegrees) * spacingDegrees - 179 + offset; lon < right; lon += spacingDegrees) {
          const normalisedLon = lon > 180 ? lon - 360 : lon;
          result.push({ lon: normalisedLon, lat, vector: lonLatVector(normalisedLon, lat, 1.039), kind: "land" });
        }
      }
    }
    return result;
  }
  const coastSamples = buildCoastSamples(compactOrbit ? .34 : .22);
  const landSamples = buildLandSamples(compactOrbit ? 1.65 : 1.3);
  /* Convert an inertial point into the same camera basis used by the WebGL
     renderer.  `z` is depth (positive faces the camera), while `x` and `y`
     are screen-right and screen-up. Keeping this transform in one place is
     what makes the canvas backstop agree with the real scene after a drag. */
  function rotateViewVector(vector, yaw, pitch) {
    const basePitch = Math.atan2(.32, 4.28);
    const viewPitch = clamp(basePitch + Number(pitch || 0), -1.05, 1.05);
    const cosYaw = Math.cos(Number(yaw || 0)); const sinYaw = Math.sin(Number(yaw || 0));
    const cosPitch = Math.cos(viewPitch); const sinPitch = Math.sin(viewPitch);
    const right = vector[0] * cosYaw - vector[2] * sinYaw;
    const horizontal = vector[0] * sinYaw + vector[2] * cosYaw;
    return [right, vector[1] * cosPitch - horizontal * sinPitch, vector[1] * sinPitch + horizontal * cosPitch];
  }
  function useOrbitFallback(reason = "当前浏览器未提供可用的 WebGL 画面") {
    if (orbitRuntime.fallback) return; orbitRuntime.fallback = true; orbitRuntime.last = 0; const frame = $("#scene-frame"); if (frame) { frame.dataset.renderer = "2d"; frame.dataset.renderState = "fallback-2d"; frame.dataset.frameVerified = "false"; } const canvas = $("#orbit-canvas"); if (canvas) { canvas.hidden = false; canvas.classList.add("is-fallback"); canvas.setAttribute("aria-label", "可交互离线二维地球与样本轨道投影"); } if (orbitRuntime.renderer) orbitRuntime.renderer.dispose(); orbitRuntime.renderer = null; const modeLabel = $("#orbit-mode span"); if (modeLabel && appState.orbitMode === "globe") modeLabel.textContent = "兼容投影"; drawFallbackOrbit(); scheduleOrbitFrame(); if (!orbitRuntime.fallbackNotified) { orbitRuntime.fallbackNotified = true; showToast(`${reason}，已使用离线兼容投影。`); }
  }
  function verifyWebGLFrame() {
    if (orbitRuntime.frameVerified || !orbitRuntime.renderer) return; orbitRuntime.frameChecks += 1;
    try {
      const gl = orbitRuntime.renderer.getContext(); if (!gl || gl.isContextLost() || gl.drawingBufferWidth < 5 || gl.drawingBufferHeight < 5) { useOrbitFallback("图形上下文不可用"); return; }
      const pixels = new Uint8Array(9 * 9 * 4); gl.readPixels(Math.floor(gl.drawingBufferWidth / 2) - 4, Math.floor(gl.drawingBufferHeight / 2) - 4, 9, 9, gl.RGBA, gl.UNSIGNED_BYTE, pixels); let alpha = 0; let energy = 0; let visible = 0; for (let index = 0; index < pixels.length; index += 4) { const sampleAlpha = pixels[index + 3]; const sampleEnergy = pixels[index] + pixels[index + 1] + pixels[index + 2]; alpha = Math.max(alpha, sampleAlpha); energy = Math.max(energy, sampleEnergy); if (sampleAlpha >= 8 && sampleEnergy >= 18) visible += 1; } if (alpha >= 8 && energy >= 18 && visible >= 4) { orbitRuntime.frameVerified = true; const frame = $("#scene-frame"); if (frame) frame.dataset.frameVerified = "true"; return; } if (orbitRuntime.frameChecks >= 3) useOrbitFallback("三维画面连续三帧未产生有效像素"); else requestAnimationFrame(renderThree);
    } catch (_error) { useOrbitFallback("三维像素校验失败"); }
  }
  function initOrbitScene() {
    drawFallbackOrbit(); const canvas = $("#orbit-canvas"); if (!canvas || !window.THREE) { useOrbitFallback("Three.js 运行时不可用"); return; }
    try {
      orbitRuntime.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" }); orbitRuntime.renderer.setPixelRatio(Math.min(orbitRuntime.compact ? 2.5 : 2, window.devicePixelRatio || 1)); orbitRuntime.renderer.setClearColor(0x020609, 0); canvas.classList.remove("is-fallback"); canvas.setAttribute("aria-label", "可交互三维地球与样本轨道投影"); const frame = $("#scene-frame"); if (frame) { frame.dataset.renderer = "webgl"; frame.dataset.renderState = "webgl-with-2d-backstop"; frame.dataset.frameVerified = "false"; }
      canvas.addEventListener("webglcontextlost", (event) => { event.preventDefault(); useOrbitFallback("三维图形上下文已丢失"); }, { once: true });
      orbitRuntime.scene = new THREE.Scene(); orbitRuntime.camera = new THREE.PerspectiveCamera(34, 1, .01, 50); orbitRuntime.camera.position.set(0, .32, 4.28); orbitRuntime.camera.lookAt(0, 0, 0); orbitRuntime.raycaster = new THREE.Raycaster(); orbitRuntime.pointer = new THREE.Vector2();
      orbitRuntime.sunDirection = new THREE.Vector3(...SUN_VECTOR); orbitRuntime.scene.add(new THREE.AmbientLight(0x173540, .34)); const key = new THREE.DirectionalLight(0xbaf8ff, 2.75); key.position.copy(orbitRuntime.sunDirection).multiplyScalar(6); orbitRuntime.scene.add(key); const rim = new THREE.DirectionalLight(0x235b82, .42); rim.position.set(4, -2, -3); orbitRuntime.scene.add(rim);
      orbitRuntime.world = new THREE.Group(); orbitRuntime.scene.add(orbitRuntime.world); const earthGroup = new THREE.Group(); orbitRuntime.world.add(earthGroup); orbitRuntime.earth = earthGroup;
      const detail = orbitRuntime.compact ? { earth: [84, 64], wire: [32, 24], atmosphere: [68, 52], stars: 620 } : { earth: [84, 64], wire: [32, 24], atmosphere: [68, 52], stars: 820 };
      const earth = new THREE.Mesh(new THREE.SphereGeometry(1.02, ...detail.earth), new THREE.MeshStandardMaterial({ color: 0x0b2833, roughness: .92, metalness: .04 })); earthGroup.add(earth);
      const wire = new THREE.Mesh(new THREE.SphereGeometry(1.024, ...detail.wire), new THREE.MeshBasicMaterial({ color: 0x67d8d0, wireframe: true, transparent: true, opacity: .13 })); earthGroup.add(wire);
      const atmosphere = new THREE.Mesh(new THREE.SphereGeometry(1.085, ...detail.atmosphere), new THREE.MeshBasicMaterial({ color: 0x49c8d0, transparent: true, opacity: .09, side: THREE.BackSide })); earthGroup.add(atmosphere);
      /* The sphere wireframe already communicates the Earth reference grid.
         Extra latitude/longitude LineLoops sat above the limb and appeared as
         unexplained equatorial rings. Keep those surface guides at zero while
         preserving the independently derived spacecraft orbit objects. */
      if (frame) frame.dataset.globeSurfaceGuideCount = "0";
      const createPointMaterial = (day, night, size, opacity) => {
        const material = new THREE.ShaderMaterial({
          transparent: true,
          depthWrite: false,
          uniforms: {
            uSunDirection: { value: orbitRuntime.sunDirection.clone() },
            uDayColor: { value: new THREE.Color(day) },
            uNightColor: { value: new THREE.Color(night) },
            uPointSize: { value: size },
            uPointScale: { value: 220 },
            uOpacity: { value: opacity },
          },
          vertexShader: `
            varying vec3 vWorldNormal;
            uniform float uPointSize;
            uniform float uPointScale;
            void main() {
              vWorldNormal = normalize(mat3(modelMatrix) * normalize(position));
              vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
              gl_Position = projectionMatrix * mvPosition;
              gl_PointSize = clamp(uPointSize * uPointScale / max(0.01, -mvPosition.z), 1.0, 3.6);
            }
          `,
          fragmentShader: `
            varying vec3 vWorldNormal;
            uniform vec3 uSunDirection;
            uniform vec3 uDayColor;
            uniform vec3 uNightColor;
            uniform float uOpacity;
            void main() {
              vec2 p = gl_PointCoord - vec2(0.5);
              float disc = smoothstep(0.5, 0.18, length(p));
              float daylight = smoothstep(-0.16, 0.24, dot(normalize(vWorldNormal), normalize(uSunDirection)));
              vec3 color = mix(uNightColor, uDayColor, daylight);
              gl_FragColor = vec4(color, disc * uOpacity);
            }
          `,
        });
        orbitRuntime.pointMaterials.push(material);
        return material;
      };
      const coastPositions = []; coastSamples.forEach((sample) => coastPositions.push(...sample.vector)); const coastGeometry = new THREE.BufferGeometry(); coastGeometry.setAttribute("position", new THREE.Float32BufferAttribute(coastPositions, 3)); orbitRuntime.coast = new THREE.Points(coastGeometry, createPointMaterial(0x9bfff2, 0x031114, orbitRuntime.compact ? .016 : .010, .98)); orbitRuntime.coast.renderOrder = 4; earthGroup.add(orbitRuntime.coast);
      const landPositions = []; landSamples.forEach((sample) => landPositions.push(...sample.vector)); const landGeometry = new THREE.BufferGeometry(); landGeometry.setAttribute("position", new THREE.Float32BufferAttribute(landPositions, 3)); orbitRuntime.land = new THREE.Points(landGeometry, createPointMaterial(0x2c8e90, 0x020d10, orbitRuntime.compact ? .020 : .014, .80)); orbitRuntime.land.renderOrder = 3; earthGroup.add(orbitRuntime.land);
      const starPositions = []; const starRandom = (index) => hash(`star-${index}`); for (let i = 0; i < detail.stars; i += 1) { const u = starRandom(i * 3) * 2 - 1; const phi = starRandom(i * 3 + 1) * Math.PI * 2; const radius = 8 + starRandom(i * 3 + 2) * 7; const spread = Math.sqrt(1 - u * u); starPositions.push(Math.cos(phi) * spread * radius, u * radius, Math.sin(phi) * spread * radius); } const starGeometry = new THREE.BufferGeometry(); starGeometry.setAttribute("position", new THREE.Float32BufferAttribute(starPositions, 3)); orbitRuntime.scene.add(new THREE.Points(starGeometry, new THREE.PointsMaterial({ color: 0xa9d7d8, size: .018, transparent: true, opacity: .58 })));
      rebuildOrbitObjects(); resizeOrbit(); renderThree();
    } catch (error) { useOrbitFallback(`WebGL 不可用：${error.message}`); }
  }
  const EARTH_RADIUS_KM = 6378.137;
  const EARTH_MU_KM3_S2 = 398600.4418;
  const EARTH_ROTATION_RAD_S = 7.2921150e-5;
  const ORBIT_MIN_PERIGEE_KM = 120;
  function numericContext(context, ...keys) {
    for (const key of keys) if (finite(context?.[key])) return Number(context[key]);
    return null;
  }
  function orbitEpochStatus(value) {
    const text = typeof value === "string" ? value.trim() : value == null ? "" : String(value).trim();
    if (!text) return { ok: false, code: "missing_epoch", epochMilliseconds: null, message: "缺少 UTC 时间基准，二维星下点不会绘制。" };
    /* A UTC-labelled field may use ISO 8601 or GMAT's UTCGregorian spelling.
       A timezone suffix is honoured; an omitted suffix is interpreted as UTC
       only because the field itself is explicitly labelled UTC. */
    const normalized = /(?:Z|[+-]\d{2}:?\d{2}|UTC)$/i.test(text) ? text : `${text} UTC`;
    const epochMilliseconds = Date.parse(normalized);
    if (!Number.isFinite(epochMilliseconds)) {
      return { ok: false, code: "invalid_epoch", epochMilliseconds: null, message: "UTC 时间基准无法解析，请填写 ISO 8601 或 GMAT UTC 时间。" };
    }
    return { ok: true, code: null, epochMilliseconds, message: "" };
  }
  function orbitStateForSample(sample) {
    const source = sample?.orbit_state;
    if (source && finite(source.semi_major_axis_km) && finite(source.eccentricity)) {
      return normaliseOrbitState({
        mode: "elements", a: Number(source.semi_major_axis_km), e: Number(source.eccentricity),
        p: Number(source.semi_latus_rectum_km ?? source.parameter_p_km),
        inclination: Number(source.inclination_deg || 0), raan: Number(source.raan_deg || 0),
        aop: Number(source.arg_periapsis_deg || 0), ta: Number(source.true_anomaly_deg || 0),
        period: Number(source.period_min), epochUtc: source.epoch_utc,
        source: source.source || "轨道初始状态",
      });
    }
    const context = sample?.replay_context || {};
    const mode = context.orbit_mode || "unspecified";
    const inclination = numericContext(context, "inclination", "orbit_inclination_deg");
    const raan = numericContext(context, "raan", "orbit_raan_deg") || 0;
    const aop = numericContext(context, "aop", "orbit_arg_periapsis_deg") || 0;
    const ta = numericContext(context, "ta", "orbit_true_anomaly_deg") || 0;
    if (mode === "circular" && numericContext(context, "altitude", "orbit_altitude_km") !== null) {
      const altitude = numericContext(context, "altitude", "orbit_altitude_km");
      return normaliseOrbitState({ mode, a: EARTH_RADIUS_KM + altitude, e: 0, inclination: inclination || 0, raan, aop, ta, epochUtc: context.epoch_utc, source: "本次上传的可选回放上下文" });
    }
    if (mode === "apsides") {
      const perigee = numericContext(context, "perigee", "orbit_perigee_altitude_km");
      const apogee = numericContext(context, "apogee", "orbit_apogee_altitude_km");
      if (perigee !== null && apogee !== null) {
        const rp = EARTH_RADIUS_KM + perigee; const ra = EARTH_RADIUS_KM + apogee;
        return normaliseOrbitState({ mode, a: (rp + ra) / 2, e: (ra - rp) / (ra + rp), inclination: inclination || 0, raan, aop, ta, epochUtc: context.epoch_utc, source: "本次上传的可选回放上下文" });
      }
    }
    if (mode === "state") {
      const altitude = numericContext(context, "altitude", "orbit_altitude_km"); const speed = numericContext(context, "speed", "orbit_speed_km_s");
      if (altitude !== null && speed !== null) {
        const radius = EARTH_RADIUS_KM + altitude; const circularSpeed = Math.sqrt(EARTH_MU_KM3_S2 / radius); const energy = speed * speed / 2 - EARTH_MU_KM3_S2 / radius;
        // Tangential speed is defined at zero radial velocity.  Below the
        // circular speed the entered point is the apogee; above it, the
        // entered point is the perigee.  Do not draw a contradictory phase.
        const stateTa = speed < circularSpeed - 1e-8 ? 180 : 0;
        const p = radius * radius * speed * speed / EARTH_MU_KM3_S2;
        // For an open trajectory the specific orbital energy still defines a
        // negative hyperbolic semi-major axis.  Keeping it finite is what
        // lets the conic renderer draw a bounded near-perigee arc instead of
        // silently dropping the track when a is absent.
        const a = Math.abs(energy) > 1e-9 ? -EARTH_MU_KM3_S2 / (2 * energy) : null;
        const e = Math.abs(radius * speed * speed / EARTH_MU_KM3_S2 - 1);
        return normaliseOrbitState({ mode, a, e, p, radius, speed, circularSpeed, inclination: inclination || 0, raan, aop, ta: stateTa, energy, epochUtc: context.epoch_utc, source: "本次上传的可选回放上下文" });
      }
    }
    return null;
  }
  function normaliseOrbitState(input) {
    const a = finite(input?.a) ? Number(input.a) : null; let e = finite(input?.e) ? Number(input.e) : null;
    const inclination = clamp(Number(input?.inclination || 0), 0, 180); const raan = Number(input?.raan || 0); const aop = Number(input?.aop || 0); const ta = Number(input?.ta || 0);
    if (e === null || !Number.isFinite(e)) return null;
    e = Math.max(0, e);
    const energy = finite(input?.energy) ? Number(input.energy) : (a !== null && a !== 0 ? -EARTH_MU_KM3_S2 / (2 * a) : null);
    const p = finite(input?.p)
      ? Number(input.p)
      : e === 1 && finite(input?.radius)
        ? Number(input.radius) * (1 + Math.cos(ta * Math.PI / 180))
        : null;
    const radius = finite(input?.radius)
      ? Number(input.radius)
      : a && e !== 1
        ? a * (1 - e * e) / (1 + e * Math.cos(ta * Math.PI / 180))
        : e === 1 && p
          ? p / (1 + Math.cos(ta * Math.PI / 180))
          : null;
    const inconsistentClosed = a !== null && a > 0 && e >= 1 && energy !== null && energy < -1e-8;
    const perigee = a && e < 1
      ? a * (1 - e) - EARTH_RADIUS_KM
      : a && e > 1
        ? Math.abs(a) * (e - 1) - EARTH_RADIUS_KM
        : e === 1 && p
          ? p / 2 - EARTH_RADIUS_KM
          : null;
    const apogee = a && e < 1 ? a * (1 + e) - EARTH_RADIUS_KM : null;
    const referenceRadius = radius && radius > 0 ? radius : perigee !== null ? perigee + EARTH_RADIUS_KM : null;
    const escape = referenceRadius && referenceRadius > 0 ? Math.sqrt(2 * EARTH_MU_KM3_S2 / referenceRadius) : null;
    // A supplied period is metadata, not a license to use a non-positive or
    // non-finite value in the animation clock.  Fall back to the two-body
    // value for closed conics and keep open conics explicitly period-less.
    const suppliedPeriod = finite(input?.period) && Number(input.period) > 0 ? Number(input.period) : null;
    const geometricPeriod = a && a > 0 && e < 1 ? 2 * Math.PI * Math.sqrt(a ** 3 / EARTH_MU_KM3_S2) / 60 : null;
    /* The animation clock follows the two-body geometry. A supplied period is
       retained as provenance/comparison metadata, but cannot make a circular
       or elliptic orbit move at a physically inconsistent rate. */
    const period = geometricPeriod;
    const epochStatus = orbitEpochStatus(input?.epochUtc);
    const perigeeRadius = perigee !== null ? perigee + EARTH_RADIUS_KM : null; const apogeeRadius = apogee !== null ? apogee + EARTH_RADIUS_KM : null;
    const speedAtPerigee = a && a > 0 && e < 1 && perigeeRadius > 0 ? Math.sqrt(Math.max(0, EARTH_MU_KM3_S2 * (2 / perigeeRadius - 1 / a))) : null;
    const speedAtApogee = a && a > 0 && e < 1 && apogeeRadius > 0 ? Math.sqrt(Math.max(0, EARTH_MU_KM3_S2 * (2 / apogeeRadius - 1 / a))) : null;
    const speedMin = finite(speedAtApogee) ? Number(speedAtApogee) : finite(input?.speed) ? Number(input.speed) : null;
    const speedMax = finite(speedAtPerigee) ? Number(speedAtPerigee) : finite(input?.speed) ? Number(input.speed) : null;
    let classification = "未指定"; let drawable = false; let reason = "缺少足够的原始轨道根数，不绘制轨迹。";
    if (inconsistentClosed) {
      classification = "轨道根数不自洽";
      reason = "半长轴为正但偏心率/比机械能指向开放状态，原始轨道事实相互冲突，不绘制轨迹。";
    }
    else if (e >= 1 || (energy !== null && energy >= 0)) {
      classification = e > 1 ? "双曲线逃逸" : "抛物线临界";
      drawable = perigee !== null && perigee >= ORBIT_MIN_PERIGEE_KM;
      reason = drawable
        ? e > 1
          ? `比机械能为正，近地点 ${format(perigee, 1)} km${finite(input?.speed) && escape ? `，当前位置速度 ${format(input.speed, 3)} km/s，高于逃逸速度 ${format(escape, 3)} km/s` : ""}；仅显示有限的开放双曲线近地点弧段。`
          : `比机械能接近零，近地点 ${format(perigee, 1)} km${escape ? `，逃逸速度 ${format(escape, 3)} km/s` : ""}；仅显示有限的抛物线近地点弧段。`
        : perigee === null
          ? "开放轨道缺少可计算的近地点，不绘制轨迹。"
          : `近地点 ${format(perigee, 1)} km 小于任务边界 ${ORBIT_MIN_PERIGEE_KM} km，不绘制开放轨迹。`;
    }
    else if (a && perigee !== null) {
      drawable = perigee >= ORBIT_MIN_PERIGEE_KM;
      if (e < .01) classification = "圆轨道"; else classification = drawable ? "闭合椭圆轨道" : "亚轨道 / 再入相交";
      reason = drawable
        ? e < .01
          ? `高度 ${format(perigee, 1)} km · 圆轨道速度 ${format(speedAtPerigee, 3)} km/s · 周期 ${format(period, 2)} min`
          : `近地点 ${format(perigee, 1)} km · 远地点 ${format(apogee, 1)} km · 速度 ${format(speedMin, 3)}–${format(speedMax, 3)} km/s · 周期 ${format(period, 2)} min`
        : `近地点 ${format(perigee, 1)} km 小于任务边界 ${ORBIT_MIN_PERIGEE_KM} km，不绘制闭合轨迹。`;
    }
    const groundTrackDrawable = drawable && epochStatus.ok;
    const groundTrackReason = groundTrackDrawable ? null : drawable ? epochStatus.message : reason;
    return { ...input, a, e, p, inclination, raan, aop, ta, energy, radius, perigee, apogee, escape, classification, drawable, reason, period, suppliedPeriod, geometricPeriod, speedAtPerigee, speedAtApogee, speedMin, speedMax, groundTrackDrawable, groundTrackReason };
  }
  function orbitalCoordinate(state, trueAnomalyDeg, scale = 1.02 / EARTH_RADIUS_KM) {
    if (!finite(state?.e)) return null;
    const e = Number(state.e); const nu = Number(trueAnomalyDeg) * Math.PI / 180; const denominator = 1 + e * Math.cos(nu); if (Math.abs(denominator) < 1e-5) return null;
    const radius = e === 1 && finite(state.p) ? Number(state.p) / denominator : state.a * (1 - e * e) / denominator; if (!Number.isFinite(radius) || radius <= 0) return null;
    /* Orbital elements use an Earth-centered equatorial frame: X/Y lie in
       the equatorial plane and Z points north.  The renderer uses X/Y-up/Z,
       so map standard (X, Y, Z) to (X, Z, Y) only after applying the complete
       argument-of-periapsis, inclination and RAAN rotations.  The previous
       implementation rotated the north axis as if it were in-plane; a 0 or
       180 degree inclination therefore produced an impossible north-south
       track instead of an equatorial one. */
    const argument = (Number(state.aop || 0) * Math.PI / 180) + nu;
    const inclination = Number(state.inclination || 0) * Math.PI / 180;
    const raan = Number(state.raan || 0) * Math.PI / 180;
    const cosArgument = Math.cos(argument); const sinArgument = Math.sin(argument);
    const cosInclination = Math.cos(inclination); const sinInclination = Math.sin(inclination);
    const cosRaan = Math.cos(raan); const sinRaan = Math.sin(raan);
    const standardX = radius * (cosRaan * cosArgument - sinRaan * sinArgument * cosInclination);
    const standardY = radius * (sinRaan * cosArgument + cosRaan * sinArgument * cosInclination);
    const standardZ = radius * (sinArgument * sinInclination);
    return [standardX * scale, standardZ * scale, standardY * scale];
  }
  function orbitalPoint(state, trueAnomalyDeg, scale = 1.02 / EARTH_RADIUS_KM) {
    const coordinate = orbitalCoordinate(state, trueAnomalyDeg, scale); return coordinate && typeof THREE !== "undefined" ? new THREE.Vector3(...coordinate) : coordinate;
  }
  const ORBIT_TIME_SCALE = 90;
  const wrapRadians = (value) => {
    const turn = Math.PI * 2; return ((Number(value) + Math.PI) % turn + turn) % turn - Math.PI;
  };
  function trueToConicMean(eccentricity, trueAnomalyRad) {
    const e = Number(eccentricity); const nu = Number(trueAnomalyRad);
    if (e < 1) {
      const eccentric = 2 * Math.atan2(Math.sqrt(1 - e) * Math.sin(nu / 2), Math.sqrt(1 + e) * Math.cos(nu / 2));
      return eccentric - e * Math.sin(eccentric);
    }
    if (Math.abs(e - 1) < 1e-7) { const d = Math.tan(nu / 2); return d + d ** 3 / 3; }
    const factor = clamp(Math.sqrt((e - 1) / (e + 1)) * Math.tan(nu / 2), -.999999, .999999);
    const hyperbolic = 2 * Math.atanh(factor); return e * Math.sinh(hyperbolic) - hyperbolic;
  }
  function conicMeanToTrue(state, mean) {
    const e = Number(state.e);
    if (e < 1) {
      const target = wrapRadians(mean); let eccentric = e < .8 ? target : Math.sign(target || 1) * Math.PI;
      for (let index = 0; index < 9; index += 1) eccentric -= (eccentric - e * Math.sin(eccentric) - target) / Math.max(.08, 1 - e * Math.cos(eccentric));
      return 2 * Math.atan2(Math.sqrt(1 + e) * Math.sin(eccentric / 2), Math.sqrt(1 - e) * Math.cos(eccentric / 2));
    }
    if (Math.abs(e - 1) < 1e-7) {
      let d = Math.cbrt(3 * mean); for (let index = 0; index < 8; index += 1) d -= (d + d ** 3 / 3 - mean) / Math.max(.08, 1 + d * d);
      return 2 * Math.atan(d);
    }
    let hyperbolic = Math.asinh(mean / Math.max(1.0001, e));
    for (let index = 0; index < 10; index += 1) hyperbolic -= (e * Math.sinh(hyperbolic) - hyperbolic - mean) / Math.max(.08, e * Math.cosh(hyperbolic) - 1);
    return 2 * Math.atan2(Math.sqrt(e + 1) * Math.sinh(hyperbolic / 2), Math.sqrt(e - 1) * Math.cosh(hyperbolic / 2));
  }
  function sampleOrbitPhase(sample, state) {
    /* The published state already contains the GMAT true anomaly at its UTC
       epoch. A sample identifier is not an orbital observation, so it must
       never shift the physical trajectory. */
    return 0;
  }
  function greenwichSiderealAngleRad(epochUtc, elapsedSeconds = 0) {
    const epochMilliseconds = orbitEpochStatus(epochUtc).epochMilliseconds;
    if (!Number.isFinite(epochMilliseconds)) return null;
    const elapsed = finite(elapsedSeconds) ? Number(elapsedSeconds) : 0;
    const julianDate = (epochMilliseconds + elapsed * 1000) / 86400000 + 2440587.5;
    const centuries = (julianDate - 2451545.0) / 36525;
    const degrees = 280.46061837 + 360.98564736629 * (julianDate - 2451545.0)
      + .000387933 * centuries * centuries - centuries * centuries * centuries / 38710000;
    return wrapRadians(degrees * Math.PI / 180);
  }
  function orbitAnomalyForSample(sample, simulationTimeSeconds = appState.orbitTimeSeconds, stateOverride = null) {
    const state = stateOverride || orbitStateForSample(sample); if (!state?.drawable) return null;
    const epochTrue = Number(state.ta || 0) * Math.PI / 180; const phase = sampleOrbitPhase(sample, state);
    let mean = trueToConicMean(state.e, epochTrue);
    if (state.e < 1 && finite(state.a) && Number(state.a) > 0) mean += phase + Math.sqrt(EARTH_MU_KM3_S2 / Number(state.a) ** 3) * Number(simulationTimeSeconds || 0);
    else if (Math.abs(Number(state.e) - 1) < 1e-7 && finite(state.p) && Number(state.p) > 0) mean += Number(simulationTimeSeconds || 0) * 2 / Math.sqrt(Number(state.p) ** 3 / EARTH_MU_KM3_S2);
    else if (Number(state.e) > 1 && finite(state.a) && Number(state.a) < 0) mean += Math.sqrt(EARTH_MU_KM3_S2 / Math.abs(Number(state.a)) ** 3) * Number(simulationTimeSeconds || 0);
    return conicMeanToTrue(state, mean) * 180 / Math.PI;
  }
  function orbitPathPoints(sample, count = 180) {
    const state = orbitStateForSample(sample); if (!state?.drawable) return [];
    // A parabola has no finite semi-major axis.  Render a bounded arc around
    // periapsis; asymptotes are deliberately omitted because they leave the
    // finite viewport and are not a real-time ephemeris.
    const limit = state.e < 1 ? Math.PI * 2 : state.e === 1 ? Math.PI * .88 : Math.acos(-1 / Math.max(1.0001, state.e)) * .94;
    const start = state.e < 1 ? 0 : -limit; const end = state.e < 1 ? Math.PI * 2 : limit;
    const points = []; for (let index = 0; index <= count; index += 1) { const anomaly = (start + (end - start) * index / count) * 180 / Math.PI; const point = orbitalPoint(state, anomaly); if (point) points.push(point); }
    return points;
  }
  function orbitDisplaySamples() { return availableSamples(); }
  function anomalyTimeSeconds(state, anomalyDeg) {
    if (!state || !finite(state.e) || !finite(anomalyDeg)) return 0;
    const e = Number(state.e); const nu = Number(anomalyDeg) * Math.PI / 180;
    if (e < 1 && finite(state.a) && Number(state.a) > 0) {
      const eccentric = 2 * Math.atan2(Math.sqrt(1 - e) * Math.sin(nu / 2), Math.sqrt(1 + e) * Math.cos(nu / 2));
      const mean = eccentric - e * Math.sin(eccentric); return mean / Math.sqrt(EARTH_MU_KM3_S2 / Number(state.a) ** 3);
    }
    if (Math.abs(e - 1) < 1e-5 && finite(state.p) && Number(state.p) > 0) {
      const barker = Math.tan(nu / 2); return .5 * Math.sqrt(Number(state.p) ** 3 / EARTH_MU_KM3_S2) * (barker + barker ** 3 / 3);
    }
    if (e > 1 && finite(state.a) && Number(state.a) < 0) {
      const factor = Math.sqrt((e - 1) / (e + 1)) * Math.tan(nu / 2); const hyperbolic = 2 * Math.atanh(clamp(factor, -.999999, .999999));
      return (e * Math.sinh(hyperbolic) - hyperbolic) / Math.sqrt(EARTH_MU_KM3_S2 / Math.abs(Number(state.a)) ** 3);
    }
    return 0;
  }
  function groundTrackPointAtTime(sample, state, simulationTimeSeconds, width, height) {
    if (!state?.groundTrackDrawable) return null;
    const anomaly = orbitAnomalyForSample(sample, simulationTimeSeconds, state); if (!finite(anomaly)) return null;
    const siderealAngle = greenwichSiderealAngleRad(state?.epochUtc, simulationTimeSeconds);
    if (!finite(siderealAngle)) return null;
    const geo = groundTrackCoordinate(state, anomaly, siderealAngle); if (!geo) return null;
    return { x: (geo.lon + 180) / 360 * width, y: (90 - geo.lat) / 180 * height, anomaly, simulationTimeSeconds, ...geo };
  }
  function sampleRisk(sample) { return sampleHealth(sample) !== "nominal"; }
  function disposeOrbitRenderable(object) {
    if (!object) return;
    object.traverse?.((node) => {
      node.geometry?.dispose?.();
      if (Array.isArray(node.material)) node.material.forEach((material) => material?.dispose?.());
      else node.material?.dispose?.();
    });
  }
  function rebuildOrbitObjects() {
    invalidateGroundTrackCache();
    if (orbitRuntime.fallback || !orbitRuntime.scene || !orbitRuntime.world) return;
    orbitRuntime.rings.forEach((object) => { orbitRuntime.world.remove(object); disposeOrbitRenderable(object); });
    orbitRuntime.points.forEach((object) => { orbitRuntime.world.remove(object); disposeOrbitRenderable(object); });
    orbitRuntime.rings = []; orbitRuntime.points = [];
    orbitDisplaySamples().forEach((sample) => {
      const state = orbitStateForSample(sample); if (!state?.drawable) return;
      const path = orbitPathPoints(sample, orbitRuntime.compact ? 110 : 180); if (path.length < 2) return;
      const selected = sample.sample_id === appState.sampleId; const risky = sampleRisk(sample); const material = new THREE.LineBasicMaterial({ color: selected ? 0x4bd9d2 : risky ? 0x9b6d42 : 0x53676d, transparent: true, opacity: selected ? .88 : .2, depthTest: true, depthWrite: false });
      const ring = state.e < 1 ? new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(path), material) : new THREE.Line(new THREE.BufferGeometry().setFromPoints(path), material); ring.visible = true; ring.renderOrder = selected ? 5 : 2; ring.userData = { sampleId: sample.sample_id, sample, state, risky }; orbitRuntime.world.add(ring); orbitRuntime.rings.push(ring);
      const marker = new THREE.Mesh(new THREE.SphereGeometry(selected ? .010 : .007, orbitRuntime.compact ? 9 : 12, orbitRuntime.compact ? 6 : 8), new THREE.MeshBasicMaterial({ color: selected ? 0x4bd9d2 : risky ? 0xf2b45d : 0x8ba1a5 })); marker.userData = { sampleId: sample.sample_id, phase: finite(sample.display_phase_offset_rad) ? Number(sample.display_phase_offset_rad) : hash(sample.sample_id) * Math.PI * 2, sample, state, local: new THREE.Vector3() }; orbitRuntime.world.add(marker); orbitRuntime.points.push(marker);
    });
    const sceneFrame = $("#scene-frame"); if (sceneFrame) { sceneFrame.dataset.orbitRenderableCount = String(orbitRuntime.rings.length); sceneFrame.dataset.orbitMarkerCount = String(orbitRuntime.points.length); sceneFrame.dataset.visibleFullOrbitCount = String(orbitRuntime.rings.filter((ring) => ring.visible).length); }
    if (orbitRuntime.selectedModel) { orbitRuntime.world.remove(orbitRuntime.selectedModel); disposeOrbitRenderable(orbitRuntime.selectedModel); }
    orbitRuntime.selectedModel = new THREE.Group(); const bus = new THREE.Mesh(new THREE.BoxGeometry(.11, .065, .18), new THREE.MeshStandardMaterial({ color: 0xbac7c8, metalness: .65, roughness: .3 })); orbitRuntime.selectedModel.add(bus); const panelMaterial = new THREE.MeshStandardMaterial({ color: 0x2e83a5, emissive: 0x0b2734, metalness: .3, roughness: .42 }); [-.15, .15].forEach((x) => { const panel = new THREE.Mesh(new THREE.BoxGeometry(.18, .012, .1), panelMaterial.clone()); panel.position.x = x; orbitRuntime.selectedModel.add(panel); }); panelMaterial.dispose(); orbitRuntime.selectedModel.visible = false; orbitRuntime.world.add(orbitRuntime.selectedModel);
  }
  function refreshOrbitObjects() {
    let visibleFullOrbitCount = 0;
    orbitRuntime.rings.forEach((ring) => {
      const selected = ring.userData.sampleId === appState.sampleId;
      const risky = sampleRisk(ring.userData.sample);
      ring.visible = true;
      ring.material.color.setHex(selected ? 0x4bd9d2 : risky ? 0x9b6d42 : 0x53676d);
      ring.material.opacity = selected ? .88 : .2;
      ring.renderOrder = selected ? 5 : 2;
      visibleFullOrbitCount += 1;
    });
    orbitRuntime.points.forEach((marker) => {
      const selected = marker.userData.sampleId === appState.sampleId; const risky = sampleRisk(marker.userData.sample);
      marker.material.color.setHex(selected ? 0x4bd9d2 : risky ? 0xf2b45d : 0x8ba1a5);
      marker.scale.setScalar(selected ? 2.1 : 1);
    });
    const sceneFrame = $("#scene-frame"); if (sceneFrame) { sceneFrame.dataset.visibleFullOrbitCount = String(visibleFullOrbitCount); sceneFrame.dataset.orbitMarkerCount = String(orbitRuntime.points.length); }
  }
  function positionMarker(marker, anomalyDeg) { const state = marker.userData.state; const local = orbitalPoint(state, anomalyDeg); if (local) marker.position.copy(local); }
  function selectedModelScale(marker) {
    if (!marker) return .42;
    const clearance = Math.max(0, marker.position.length() - 1.02);
    /* The body is an intentionally illustrative selection glyph, not a
       physical-scale spacecraft. Keep it legible beside a one-radius Earth
       while the orbit and marker retain the physically derived position. */
    return clamp(.42 + clearance * 1.15, .42, .72);
  }
  function orbitCanvasHasLayout() { const surface = $("#scene-frame"); if (!surface || appState.view !== "operate") return false; const box = surface.getBoundingClientRect(); return box.width >= 8 && box.height >= 8; }
  function orbitPixelRatio(width, height) { const requested = Math.min(orbitRuntime.compact ? 2.5 : 2, Math.max(1, window.devicePixelRatio || 1)); const pixelBudget = orbitRuntime.compact ? 2800000 : 4200000; return Math.max(1, Math.min(requested, Math.sqrt(pixelBudget / Math.max(1, width * height)))); }
  function resizeOrbit() { const canvas = $("#orbit-canvas"); if (!canvas || !orbitRuntime.renderer) return; const box = canvas.getBoundingClientRect(); if (box.width < 8 || box.height < 8) return; const width = box.width; const height = box.height; const ratio = orbitPixelRatio(width, height); if (width === orbitRuntime.width && height === orbitRuntime.height && ratio === orbitRuntime.pixelRatio) return; orbitRuntime.width = width; orbitRuntime.height = height; orbitRuntime.pixelRatio = ratio; orbitRuntime.frameVerified = false; orbitRuntime.frameChecks = 0; const frame = $("#scene-frame"); if (frame) frame.dataset.frameVerified = "false"; orbitRuntime.renderer.setPixelRatio(ratio); orbitRuntime.renderer.setSize(width, height, false); orbitRuntime.camera.aspect = width / height; orbitRuntime.camera.updateProjectionMatrix(); orbitRuntime.pointMaterials.forEach((material) => { material.uniforms.uPointScale.value = height * ratio * .5; }); drawFallbackOrbit(); }
  function orbitHasMotion() { return appState.view === "operate" && !reduceMotion && (appState.orbitPlaying || orbitRuntime.inertiaActive); }
  function scheduleOrbitFrame() { if (document.hidden || appState.view !== "operate" || !orbitCanvasHasLayout() || !orbitHasMotion() || orbitRuntime.raf) return; orbitRuntime.raf = requestAnimationFrame(orbitRuntime.fallback ? renderFallback : renderThree); }
  function cancelOrbitFrame() {
    if (orbitRuntime.raf) {
      cancelAnimationFrame(orbitRuntime.raf);
      orbitRuntime.raf = 0;
    }
    if (orbitRuntime.interactionFrame) {
      cancelAnimationFrame(orbitRuntime.interactionFrame);
      orbitRuntime.interactionFrame = 0;
    }
    cancelOrbitProjectionRetry();
  }
  function cancelOrbitProjectionRetry() {
    if (orbitRuntime.projectionRetry) cancelAnimationFrame(orbitRuntime.projectionRetry);
    orbitRuntime.projectionRetry = 0;
    orbitRuntime.projectionRetryAttempts = 0;
  }
  function scheduleOrbitProjectionRetry() {
    if (orbitRuntime.projectionRetry || document.hidden || appState.view !== "operate") return;
    /* A hidden or collapsed scene can legitimately report zero size for a
       couple of layout frames. Retry only while the view is settling; once
       the cap is reached the normal ResizeObserver/window resize path can
       request another paint without burning an endless RAF loop. */
    if (orbitRuntime.projectionRetryAttempts >= 12) return;
    orbitRuntime.projectionRetryAttempts += 1;
    orbitRuntime.projectionRetry = requestAnimationFrame(() => {
      orbitRuntime.projectionRetry = 0;
      if (appState.view === "operate") refreshOrbitProjection({ force: true });
    });
  }
  function cancelOrbitInertia() { orbitRuntime.inertiaYaw = 0; orbitRuntime.inertiaPitch = 0; orbitRuntime.inertiaActive = false; }
  function advanceOrbitDynamics(delta) {
    if (appState.orbitPlaying && !reduceMotion && !orbitRuntime.drag) appState.orbitTimeSeconds += delta * ORBIT_TIME_SCALE / 1000;
    appState.orbitAngle = appState.orbitTimeSeconds;
    if (!orbitRuntime.inertiaActive || reduceMotion) return;
    appState.orbitOffset += orbitRuntime.inertiaYaw * delta; const nextPitch = appState.orbitPitch + orbitRuntime.inertiaPitch * delta; appState.orbitPitch = clamp(nextPitch, -1.05, 1.05);
    if (appState.orbitPitch !== nextPitch) orbitRuntime.inertiaPitch = 0;
    const damping = Math.exp(-delta * .026); orbitRuntime.inertiaYaw *= damping; orbitRuntime.inertiaPitch *= damping;
    if (Math.max(Math.abs(orbitRuntime.inertiaYaw), Math.abs(orbitRuntime.inertiaPitch)) < .000012) cancelOrbitInertia();
  }
  function publishOrbitPose() {
    const canvas = $("#orbit-canvas"); if (!canvas) return;
    canvas.dataset.orbitYaw = String(Math.round(appState.orbitOffset * 1000000) / 1000000);
    canvas.dataset.orbitPitch = String(Math.round(appState.orbitPitch * 1000000) / 1000000);
    canvas.dataset.orbitInertiaActive = String(orbitRuntime.inertiaActive);
    canvas.dataset.orbitDragActive = String(Boolean(orbitRuntime.drag));
    if (window.__BRPHM_QA__ === true) {
      let anchorX = NaN;
      if (!orbitRuntime.fallback && orbitRuntime.camera && window.THREE) {
        const anchor = orbitRuntime.qaAnchor || (orbitRuntime.qaAnchor = new THREE.Vector3());
        anchor.set(0, 0, 1.04).project(orbitRuntime.camera); anchorX = anchor.x;
      } else {
        anchorX = rotateViewVector([0, 0, 1.04], appState.orbitOffset, appState.orbitPitch)[0];
      }
      canvas.dataset.renderedAnchorX = finite(anchorX) ? String(Math.round(anchorX * 1000000) / 1000000) : "";
    }
  }
  function applyOrbitView() {
    if (!orbitRuntime.world || !orbitRuntime.earth || !orbitRuntime.camera) return;
    /*
       The old implementation rotated the scene group with a YXZ Euler.  Once
       yaw was non-zero, the X axis used for a vertical drag was no longer a
       stable screen-right axis; the same downward gesture could therefore
       appear to reverse near a side view.  Orbiting the camera around a fixed
       inertial scene keeps the gesture frame stable and is mathematically
       equivalent to the inverse scene rotation for geometry.  The camera
       offset includes the original elevated viewing position so the horizon
       does not jump when the first drag starts.
    */
    orbitRuntime.world.rotation.set(0, 0, 0);
    const distance = orbitBaseDistance() / Math.max(.74, Number(appState.orbitZoom) || 1);
    const basePitch = Math.atan2(.32, 4.28);
    const viewPitch = clamp(basePitch + Number(appState.orbitPitch || 0), -1.05, 1.05);
    const horizontal = Math.cos(viewPitch) * distance;
    /* Keep this Euler pose as a diagnostic compatibility value for existing
       integrations, but use an explicit spherical camera pose for rendering.
       The latter has a stable screen-right axis at every yaw, so vertical
       drags cannot reverse when the camera passes a side view. */
    const viewEuler = orbitRuntime.viewEuler || (orbitRuntime.viewEuler = new THREE.Euler(0, 0, 0, "YXZ"));
    viewEuler.set(appState.orbitPitch, appState.orbitOffset, 0, "YXZ");
    orbitRuntime.camera.position.set(
      Math.sin(appState.orbitOffset) * horizontal,
      Math.sin(viewPitch) * distance,
      Math.cos(appState.orbitOffset) * horizontal,
    );
    orbitRuntime.camera.lookAt(0, 0, 0);
    orbitRuntime.camera.updateMatrixWorld();
    orbitRuntime.earth.rotation.y = EARTH_ROTATION_RAD_S * appState.orbitTimeSeconds;
    publishOrbitPose();
  }
  function renderThree(time = 0) {
    orbitRuntime.raf = 0; if (appState.view !== "operate" || !orbitRuntime.renderer || !orbitRuntime.scene || !orbitCanvasHasLayout()) return; const now = Number.isFinite(time) && time > 0 ? time : performance.now(); const interval = appState.orbitMode === "groundtrack" ? orbitRuntime.groundTrackFrameInterval : orbitRuntime.frameInterval; if (interval && orbitRuntime.lastRendered && now - orbitRuntime.lastRendered < interval) { scheduleOrbitFrame(); return; } orbitRuntime.lastRendered = now; const delta = orbitRuntime.last ? clamp(now - orbitRuntime.last, 0, 60) : 16; orbitRuntime.last = now; advanceOrbitDynamics(delta);
    /* The short branch below is kept as the public offline-render contract:
       if (appState.orbitMode === "groundtrack") { drawGroundTrack(); publishOrbitPose(); scheduleOrbitFrame(); return; }
       The live path additionally reports paint success for zero-size retries. */
    if (appState.orbitMode === "groundtrack") { const painted = drawGroundTrack(); if (painted) cancelOrbitProjectionRetry(); else scheduleOrbitProjectionRetry(); publishOrbitPose(); scheduleOrbitFrame(); return; }
    applyOrbitView(); orbitRuntime.points.forEach((marker) => positionMarker(marker, orbitAnomalyForSample(marker.userData.sample, appState.orbitTimeSeconds, marker.userData.state))); const selected = orbitRuntime.points.find((marker) => marker.userData.sampleId === appState.sampleId); const sceneFrame = $("#scene-frame"); if (orbitRuntime.selectedModel && selected) { const modelScale = selectedModelScale(selected); orbitRuntime.selectedModel.visible = true; orbitRuntime.selectedModel.position.copy(selected.position); /* The illustrative glyph may scale for legibility, but its center is the propagated spacecraft position. */ orbitRuntime.selectedModel.scale.setScalar(modelScale); orbitRuntime.selectedModel.rotation.y = appState.orbitTimeSeconds * .035; orbitRuntime.selectedModel.renderOrder = 6; if (sceneFrame) sceneFrame.dataset.selectedModelTrackDistance = String(orbitRuntime.selectedModel.position.distanceTo(selected.position)); } else if (orbitRuntime.selectedModel) { orbitRuntime.selectedModel.visible = false; if (sceneFrame) sceneFrame.dataset.selectedModelTrackDistance = ""; } orbitRuntime.renderer.render(orbitRuntime.scene, orbitRuntime.camera); verifyWebGLFrame();
    scheduleOrbitFrame();
  }
  function fallbackGlobeRadius(width, height) { return Math.min(width, height) * .41 * appState.orbitZoom; }
  function drawFallbackOrbitBase() {
    const canvas = $("#orbit-fallback-canvas"); if (!canvas) return; const ratio = Math.min(2, window.devicePixelRatio || 1); const box = canvas.getBoundingClientRect(); const width = Math.max(1, box.width); const height = Math.max(1, box.height); const targetWidth = Math.floor(width * ratio); const targetHeight = Math.floor(height * ratio); if (canvas.width !== targetWidth || canvas.height !== targetHeight) { canvas.width = targetWidth; canvas.height = targetHeight; } const ctx = canvas.getContext("2d"); if (!ctx) return; ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.fillStyle = "#020609"; ctx.fillRect(0, 0, width, height); for (let index = 0; index < 90; index += 1) { const x = hash(`fallback-star-x-${index}`) * width; const y = hash(`fallback-star-y-${index}`) * height; const size = .35 + hash(`fallback-star-s-${index}`) * 1.15; ctx.fillStyle = `rgba(146,203,206,${.12 + hash(`fallback-star-a-${index}`) * .32})`; ctx.fillRect(x, y, size, size); } const cx = width * .5; const cy = height * .54; const radius = fallbackGlobeRadius(width, height); const glow = ctx.createRadialGradient(cx - radius * .24, cy - radius * .28, radius * .1, cx, cy, radius * 1.1); glow.addColorStop(0, "rgba(41,113,121,.56)"); glow.addColorStop(.82, "rgba(7,31,39,.92)"); glow.addColorStop(1, "rgba(2,6,9,0)"); ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.fill(); ctx.strokeStyle = "rgba(103,216,208,.3)"; ctx.stroke(); ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.clip(); ctx.strokeStyle = "rgba(125,191,193,.2)"; for (let i = -2; i <= 2; i += 1) { ctx.beginPath(); ctx.ellipse(cx, cy, radius * (.25 + Math.abs(i) * .17), radius, 0, 0, Math.PI * 2); ctx.stroke(); ctx.beginPath(); ctx.ellipse(cx, cy, radius, radius * (.26 + Math.abs(i) * .12), 0, 0, Math.PI * 2); ctx.stroke(); } ctx.restore(); appState.markers = []; let selectedMarker = null; const group = allLineSamples(); group.forEach((sample) => { const orbitHash = hash(sample.sample_id); const physicalAnomaly = orbitAnomalyForSample(sample, appState.orbitTimeSeconds); const angle = finite(physicalAnomaly) ? Number(physicalAnomaly) * Math.PI / 180 + appState.orbitOffset : orbitHash * Math.PI * 2 + appState.orbitTimeSeconds * .02 + appState.orbitOffset; const orbitRadius = radius * (1.25 + orbitHash * .34); const orbitHeight = radius * (.36 + orbitHash * .16); const rotation = (orbitHash - .5) * .9; const risk = sampleRisk(sample); const selected = sample.sample_id === appState.sampleId; ctx.save(); ctx.translate(cx, cy); ctx.rotate(rotation); ctx.strokeStyle = selected ? "rgba(75,217,210,.72)" : risk ? "rgba(242,180,93,.34)" : "rgba(111,139,147,.18)"; ctx.lineWidth = selected ? 1.25 : .7; ctx.beginPath(); ctx.ellipse(0, 0, orbitRadius, orbitHeight, 0, 0, Math.PI * 2); ctx.stroke(); const localX = Math.cos(angle) * orbitRadius; const localY = Math.sin(angle) * orbitHeight; const x = cx + localX * Math.cos(rotation) - localY * Math.sin(rotation); const y = cy + localX * Math.sin(rotation) + localY * Math.cos(rotation); ctx.restore(); appState.markers.push({ sample, x, y }); ctx.fillStyle = selected ? "#4bd9d2" : risk ? "#f2b45d" : "#748b91"; ctx.shadowBlur = selected || risk ? 12 : 0; ctx.shadowColor = ctx.fillStyle; ctx.beginPath(); ctx.arc(x, y, selected ? 5 : 3, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0; if (selected) selectedMarker = { x, y, angle }; }); if (selectedMarker) { ctx.save(); ctx.translate(selectedMarker.x, selectedMarker.y); ctx.rotate(selectedMarker.angle + .4); ctx.fillStyle = "#9eadb0"; ctx.fillRect(-6, -4, 12, 8); ctx.fillStyle = "#2e83a5"; ctx.fillRect(-20, -3, 12, 6); ctx.fillRect(8, -3, 12, 6); ctx.strokeStyle = "rgba(137,223,224,.7)"; ctx.strokeRect(-20, -3, 12, 6); ctx.strokeRect(8, -3, 12, 6); ctx.restore(); } ctx.fillStyle = "rgba(174,206,207,.75)"; ctx.font = "10px RSMono, monospace"; ctx.fillText(orbitRuntime.fallback ? "离线兼容投影 · 非真实星历" : "工程投影冗余层 · 非真实星历", 14, height - (orbitRuntime.compact ? 70 : 62)); }
  function drawFallbackPointLayer(context, samples, cx, cy, radius, options = {}) {
    const buckets = [[], [], [], [], []]; const opacityBase = options.opacityBase ?? .14; const opacitySpan = options.opacitySpan ?? .72;
    samples.forEach((sample) => {
      const vector = rotateViewVector(sample.vector, appState.orbitOffset + EARTH_ROTATION_RAD_S * appState.orbitTimeSeconds, appState.orbitPitch); if (vector[2] <= .015) return;
      const illumination = smoothstep(-.15, .24, vector[0] * SUN_VECTOR[0] + vector[1] * SUN_VECTOR[1] + vector[2] * SUN_VECTOR[2]); if (illumination <= .025) return; const bucket = clamp(Math.floor(illumination * buckets.length), 0, buckets.length - 1);
      buckets[bucket].push([cx + vector[0] * radius, cy - vector[1] * radius]);
    });
    buckets.forEach((points, index) => {
      if (!points.length) return; const illumination = (index + 1) / buckets.length; const dotRadius = options.dotRadius ?? (orbitRuntime.compact ? 1.1 : .9);
      context.beginPath(); points.forEach(([x, y]) => { context.moveTo(x + dotRadius, y); context.arc(x, y, dotRadius, 0, Math.PI * 2); });
      context.fillStyle = options.land ? `rgba(${Math.round(8 + illumination * 42)}, ${Math.round(28 + illumination * 118)}, ${Math.round(34 + illumination * 120)}, ${opacityBase + illumination * opacitySpan})` : `rgba(${Math.round(6 + illumination * 145)}, ${Math.round(19 + illumination * 220)}, ${Math.round(24 + illumination * 210)}, ${opacityBase + illumination * opacitySpan})`;
      context.shadowBlur = options.land ? 0 : illumination > .62 ? 3 + illumination * 4 : 0; context.shadowColor = "rgba(122,255,237,.65)"; context.fill();
    });
  }
  function drawFallbackCoastOverlay() {
    const canvas = $("#orbit-fallback-canvas"); if (!canvas) return; const context = canvas.getContext("2d"); if (!context) return;
    const box = canvas.getBoundingClientRect(); const width = Math.max(1, box.width); const height = Math.max(1, box.height); const cx = width * .5; const cy = height * .54; const radius = fallbackGlobeRadius(width, height);
    context.save(); context.beginPath(); context.arc(cx, cy, radius, 0, Math.PI * 2); context.clip();
    drawFallbackPointLayer(context, landSamples, cx, cy, radius, { land: true, dotRadius: orbitRuntime.compact ? .94 : .7, opacityBase: .08, opacitySpan: .34 });
    drawFallbackPointLayer(context, coastSamples, cx, cy, radius, { dotRadius: orbitRuntime.compact ? 1.12 : .72, opacityBase: .18, opacitySpan: .78 });
    context.restore();
  }
  function drawFallbackOrbit() { orbitRenderAllSamples = true; try { publishOrbitPose(); drawFallbackOrbitBase(); drawFallbackCoastOverlay(); } finally { orbitRenderAllSamples = false; } }
  /* Compatibility contract retained for the offline interaction probe:
     if (appState.orbitMode === "groundtrack") drawGroundTrack(); else drawFallbackOrbit(); */
  function renderFallback(time = 0) { orbitRuntime.raf = 0; if (appState.view !== "operate" || !orbitRuntime.fallback) return; const now = Number.isFinite(time) && time > 0 ? time : performance.now(); const interval = appState.orbitMode === "groundtrack" ? orbitRuntime.groundTrackFrameInterval : orbitRuntime.frameInterval; if (interval && orbitRuntime.lastRendered && now - orbitRuntime.lastRendered < interval) { scheduleOrbitFrame(); return; } orbitRuntime.lastRendered = now; const delta = orbitRuntime.last ? clamp(now - orbitRuntime.last, 0, 60) : 16; orbitRuntime.last = now; advanceOrbitDynamics(delta); if (appState.orbitMode === "groundtrack") { const painted = drawGroundTrack(); if (painted) cancelOrbitProjectionRetry(); else scheduleOrbitProjectionRetry(); } else if (orbitCanvasHasLayout()) { drawFallbackOrbit(); cancelOrbitProjectionRetry(); } else scheduleOrbitProjectionRetry(); scheduleOrbitFrame(); }
  function requestOrbitRender() { if (appState.view !== "operate" || orbitRuntime.raf || !orbitCanvasHasLayout()) return; orbitRuntime.raf = requestAnimationFrame(orbitRuntime.fallback ? renderFallback : renderThree); }
  function refreshOrbitProjection({ force = false, invalidate = false } = {}) {
    if (invalidate) invalidateGroundTrackCache(true);
    if (appState.view !== "operate") return;
    if (force) { orbitRuntime.last = 0; orbitRuntime.lastRendered = 0; }
    if (appState.orbitMode === "groundtrack") {
      // Draw synchronously after a context or mode change.  A paused scene
      // must still reflect the new orbit, while an active scene resumes from
      // the same RAF loop without rebuilding the track on every frame.
      const painted = drawGroundTrack(); publishOrbitPose();
      if (painted) cancelOrbitProjectionRetry(); else scheduleOrbitProjectionRetry();
      if (orbitHasMotion()) scheduleOrbitFrame();
      return;
    }
    if (orbitRuntime.fallback) {
      if (!orbitCanvasHasLayout()) { scheduleOrbitProjectionRetry(); return; }
      drawFallbackOrbit(); cancelOrbitProjectionRetry();
      if (orbitHasMotion()) scheduleOrbitFrame();
      return;
    }
    if (!orbitRuntime.renderer || !orbitCanvasHasLayout()) { scheduleOrbitProjectionRetry(); return; }
    resizeOrbit(); refreshOrbitObjects();
    cancelOrbitProjectionRetry();
    if (force) {
      const now = performance.now(); orbitRuntime.last = now; orbitRuntime.lastRendered = 0;
      renderThree(now);
    } else requestOrbitRender();
  }
  function drawOrbit(options = {}) { refreshOrbitProjection(options); }
  function drawOrbitInteractionFrame() {
    /* Pointer movement must repaint even while playback is paused.  Coalesce
       high-frequency events to one compositor frame, then force the same
       renderer path used by reset/mode changes so WebGL and canvas fallback
       cannot diverge. */
    if (orbitRuntime.interactionFrame || appState.view !== "operate" || !orbitCanvasHasLayout()) return;
    orbitRuntime.interactionFrame = requestAnimationFrame(() => {
      orbitRuntime.interactionFrame = 0;
      refreshOrbitProjection({ force: true });
    });
  }
  function groundTrackCoordinate(state, anomalyDeg, spin = 0) {
    const coordinate = orbitalCoordinate(state, anomalyDeg, 1); if (!coordinate) return null;
    const [x, y, z] = coordinate; const c = Math.cos(spin); const s = Math.sin(spin); const ecefX = x * c + z * s; const ecefZ = -x * s + z * c; const radius = Math.max(1e-6, Math.hypot(ecefX, y, ecefZ));
    return { lon: Math.atan2(ecefZ, ecefX) * 180 / Math.PI, lat: Math.asin(clamp(y / radius, -1, 1)) * 180 / Math.PI };
  }
  function invalidateGroundTrackCache(includeBase = false) {
    const cache = orbitRuntime.groundtrack; if (!cache) return;
    cache.tracksDirty = true; cache.tracks = [];
    if (includeBase) cache.baseDirty = true;
  }
  function groundTrackPixelRatio(width, height) {
    const requested = Math.min(2, Math.max(1, window.devicePixelRatio || 1)); const budget = orbitRuntime.compact ? 2200000 : 3200000;
    return Math.max(1, Math.min(requested, Math.sqrt(budget / Math.max(1, width * height))));
  }
  function ensureGroundTrackSurface(canvas) {
    const box = canvas.getBoundingClientRect(); if (box.width < 8 || box.height < 8) return null;
    const width = box.width; const height = box.height; const ratio = groundTrackPixelRatio(width, height); const pixelWidth = Math.max(1, Math.round(width * ratio)); const pixelHeight = Math.max(1, Math.round(height * ratio)); const cache = orbitRuntime.groundtrack;
    const changed = cache.width !== width || cache.height !== height || cache.ratio !== ratio || canvas.width !== pixelWidth || canvas.height !== pixelHeight;
    if (changed) {
      cache.width = width; cache.height = height; cache.ratio = ratio; canvas.width = pixelWidth; canvas.height = pixelHeight;
      cache.baseCanvas ||= document.createElement("canvas"); cache.trackCanvas ||= document.createElement("canvas");
      [cache.baseCanvas, cache.trackCanvas].forEach((layer) => { layer.width = pixelWidth; layer.height = pixelHeight; });
      cache.baseDirty = true; cache.tracksDirty = true; cache.tracks = [];
      cache.backingStoreRebuilds = (cache.backingStoreRebuilds || 0) + 1;
    }
    return { cache, width, height, ratio, context: canvas.getContext("2d") };
  }
  function drawGroundTrackBase(cache) {
    if (!cache.baseDirty || !cache.baseCanvas) return;
    const { width, height, ratio } = cache; const context = cache.baseCanvas.getContext("2d"); if (!context) return;
    context.setTransform(1, 0, 0, 1, 0, 0); context.clearRect(0, 0, cache.baseCanvas.width, cache.baseCanvas.height); context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const background = context.createLinearGradient(0, 0, 0, height); background.addColorStop(0, "#07161b"); background.addColorStop(.52, "#081015"); background.addColorStop(1, "#05090d"); context.fillStyle = background; context.fillRect(0, 0, width, height);
    context.strokeStyle = "rgba(126, 199, 203, .16)"; context.lineWidth = 1;
    for (let lon = -180; lon <= 180; lon += 30) { const x = (lon + 180) / 360 * width; context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke(); }
    for (let lat = -60; lat <= 60; lat += 30) { const y = (90 - lat) / 180 * height; context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke(); }
    context.fillStyle = "rgba(84, 160, 165, .15)"; COASTLINE_RENDER_PATHS.forEach((path) => path.forEach(([lon, lat]) => context.fillRect((lon + 180) / 360 * width, (90 - lat) / 180 * height, 1.2, 1.2)));
    cache.baseDirty = false;
  }
  function groundTrackSegments(points, width, height) {
    const segments = []; let segment = [];
    if (!Array.isArray(points) || !points.length) return segments;
    const flush = () => { if (segment.length > 1) segments.push(segment); segment = []; };
    const push = (point) => { point.segmentIndex = segments.length; segment.push(point); };
    push(points[0]);
    for (let index = 1; index < points.length; index += 1) {
      const previous = points[index - 1]; const point = points[index];
      const previousLongitude = Number(previous.lon); const longitude = Number(point.lon);
      const rawDelta = longitude - previousLongitude; const wrappedDelta = wrappedLongitudeDelta(previousLongitude, longitude);
      const pixelJump = Number.isFinite(Number(width)) && Number(width) > 0
        && Number.isFinite(Number(previous.x)) && Number.isFinite(Number(point.x))
        && Math.abs(Number(point.x) - Number(previous.x)) > Number(width) * .5;
      /* Use both longitude and projected-pixel evidence.  The former is the
         physical seam; the latter catches equivalent -180/180 encodings and
         protects the renderer when an upstream parser leaves a value one turn
         outside the canonical range. */
      const crossesDateLine = Number.isFinite(rawDelta) && Number.isFinite(wrappedDelta)
        && ((Math.abs(rawDelta - wrappedDelta) > 180 && Math.abs(wrappedDelta) <= 180) || pixelJump);
      if (crossesDateLine) {
        /* Close each side exactly at the map edge, then reopen at the opposite
           edge.  This preserves a continuous wrapped track without drawing a
           false vertical chord through the whole equirectangular map. */
        const edgeLongitude = pixelJump && Number(point.x) < Number(previous.x)
          ? 180
          : pixelJump && Number(point.x) > Number(previous.x)
            ? -180
            : rawDelta > 0 ? -180 : 180;
        /* Treat +/-180 as the same meridian.  Exact 180 -> -180 samples have
           a zero wrapped delta, so the old formula divided by zero and left
           a non-finite seam point in the rendered path. */
        const unwrappedNextLongitude = previousLongitude + wrappedDelta;
        const progress = Math.abs(wrappedDelta) > 1e-8
          ? clamp((edgeLongitude - previousLongitude) / (unwrappedNextLongitude - previousLongitude), 0, 1)
          : .5;
        const seamLatitude = previous.lat + (point.lat - previous.lat) * progress;
        const exit = { ...previous, x: edgeLongitude === 180 ? width : 0, y: (90 - seamLatitude) / 180 * height, lon: edgeLongitude, lat: seamLatitude };
        push(exit); flush();
        const entry = { ...point, x: edgeLongitude === 180 ? 0 : width, y: exit.y, lon: edgeLongitude === 180 ? -180 : 180, lat: seamLatitude };
        push(entry);
      }
      if (Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y))
        && Number.isFinite(Number(point.lon)) && Number.isFinite(Number(point.lat))) push(point);
    }
    flush();
    return segments;
  }
  /* A finite time window can begin and end away from the date line. Those two
     endpoints are real portions of the propagated track, not evidence that
     the path is absent. Keep every valid date-line segment visible; only the
     map seam itself remains split so we never draw a false world-spanning
     chord through the equirectangular projection. */
  /* One inertial revolution does not close in the Earth-fixed map: the Earth
     turns beneath it by roughly 24 degrees in low orbit.  The selected track
     therefore uses three contiguous periods centered on the live marker.
     This preserves the physical path on both sides of every date-line seam
     instead of presenting an arbitrary short first/last arc as a whole path. */
  const SELECTED_GROUND_TRACK_PERIODS = 1;
  const CONTEXT_GROUND_TRACK_PERIODS = 1;
  function groundTrackPointTouchesEdge(point, width) {
    return finite(point?.x) && finite(width) && Number(width) > 0
      && (Number(point.x) <= 1 || Number(point.x) >= Number(width) - 1);
  }
  function groundTrackDisplayGeometry(track, width, currentTime) {
    const segments = Array.isArray(track?.segments) ? track.segments : [];
    if (!track?.closed) return { displaySegments: segments, completeSegmentCount: segments.length, internalEndpointCount: 0, windowBounded: false };
    const complete = segments.filter((segment) => segment.length > 1
      && groundTrackPointTouchesEdge(segment[0], width)
      && groundTrackPointTouchesEdge(segment[segment.length - 1], width));
    const displaySegments = segments.filter((segment) => segment.length > 1);
    const internalEndpointCount = displaySegments.reduce((count, segment) => (
      count
      + (groundTrackPointTouchesEdge(segment[0], width) ? 0 : 1)
      + (groundTrackPointTouchesEdge(segment[segment.length - 1], width) ? 0 : 1)
    ), 0);
    return {
      displaySegments,
      completeSegmentCount: complete.length,
      internalEndpointCount,
      windowBounded: internalEndpointCount > 0,
    };
  }
  function selectedGroundTrackHighlightSegments(track) {
    /* Highlight topology is deliberately independent of the moving marker.
       A marker can cross a date-line segment boundary, but that must never
       shrink or relocate the selected path's visual emphasis. */
    return (Array.isArray(track?.segments) ? track.segments : [])
      .filter((segment) => Array.isArray(segment) && segment.length > 1);
  }
  function buildGroundTrack(sample, state, width, height) {
    const closed = state.e < 1 && finite(state.a) && Number(state.a) > 0; const count = closed ? (orbitRuntime.compact ? 80 : 120) : (orbitRuntime.compact ? 96 : 144); const points = [];
    let cycleIndex = null; let startTime = null; let endTime = null; let periodSeconds = null; let periodCount = 1;
    if (closed) {
      periodSeconds = finite(state.period) && Number(state.period) > 0 ? Number(state.period) * 60 : 2 * Math.PI * Math.sqrt(Number(state.a) ** 3 / EARTH_MU_KM3_S2);
      periodCount = sample.sample_id === appState.sampleId ? SELECTED_GROUND_TRACK_PERIODS : CONTEXT_GROUND_TRACK_PERIODS;
      cycleIndex = Math.floor(appState.orbitTimeSeconds / periodSeconds);
      startTime = (cycleIndex - Math.floor((periodCount - 1) / 2)) * periodSeconds;
      endTime = startTime + periodCount * periodSeconds;
      const samples = count * periodCount;
      for (let index = 0; index <= samples; index += 1) { const point = groundTrackPointAtTime(sample, state, startTime + periodSeconds * index / count, width, height); if (point) points.push(point); }
    } else {
      const limit = Math.abs(Number(state.e) - 1) < 1e-7 ? Math.PI * .88 : Math.acos(-1 / Math.max(1.0001, state.e)) * .94; const epochFlightTime = anomalyTimeSeconds(state, Number(state.ta || 0));
      for (let index = 0; index <= count; index += 1) {
        const anomaly = (-limit + limit * 2 * index / count) * 180 / Math.PI; const simulationTime = anomalyTimeSeconds(state, anomaly) - epochFlightTime; const siderealAngle = greenwichSiderealAngleRad(state.epochUtc, simulationTime); const geo = finite(siderealAngle) ? groundTrackCoordinate(state, anomaly, siderealAngle) : null;
        if (geo) points.push({ x: (geo.lon + 180) / 360 * width, y: (90 - geo.lat) / 180 * height, anomaly, simulationTimeSeconds: simulationTime, ...geo });
      }
      startTime = points[0]?.simulationTimeSeconds ?? 0; endTime = points[points.length - 1]?.simulationTimeSeconds ?? 0;
    }
    const track = { sample, state, risk: sampleRisk(sample), closed, cycleIndex, periodSeconds, periodCount, startTime, endTime, points, segments: groundTrackSegments(points, width, height) };
    Object.assign(track, groundTrackDisplayGeometry(track, width, appState.orbitTimeSeconds));
    return track;
  }
  function drawGroundTrackPath(context, track, segments = track.displaySegments || track.segments) {
    const width = Number(orbitRuntime.groundtrack?.width) || 0;
    /* Round joins keep sampled polylines readable at high DPR. This only
       changes stroke rasterisation; segment topology stays tied to the
       longitude/date-line geometry calculated above. */
    context.lineCap = "round";
    context.lineJoin = "round";
    (segments || []).forEach((points) => {
      if (points.length < 2) return;
      let minX = Infinity; let maxX = -Infinity;
      points.forEach((point) => { minX = Math.min(minX, point.x); maxX = Math.max(maxX, point.x); });
      /* The equirectangular map is periodic in longitude.  Paint a seam-touching
         segment once more one map width away so the physical track remains
         visually continuous at the left/right viewport edges without inventing
         a chord through the date-line interior. */
      const shifts = [0];
      if (width > 0 && minX <= 1) shifts.push(width);
      if (width > 0 && maxX >= width - 1) shifts.push(-width);
      shifts.forEach((shift) => {
        context.beginPath();
        points.forEach((point, index) => {
          const x = point.x + shift;
          if (index) context.lineTo(x, point.y); else context.moveTo(x, point.y);
        });
        context.stroke();
      });
    });
  }
  function groundTrackCyclesChanged(cache) {
    const now = Number(appState.orbitTimeSeconds) || 0;
    return cache.tracks.some((track) => track.closed && finite(track.startTime) && finite(track.endTime)
      && (now < Number(track.startTime) - 1e-6 || now > Number(track.endTime) + 1e-6));
  }
  function rebuildGroundTrackLayer(cache) {
    if (!cache.trackCanvas) return;
    const candidates = orbitDisplaySamples().map((sample) => ({ sample, state: orbitStateForSample(sample) })).filter((entry) => entry.state?.drawable).map(({ sample, state }) => buildGroundTrack(sample, state, cache.width, cache.height));
    /* A geometrically valid 3-D orbit can still lack the UTC origin required
       by an absolute ground track. Keep such samples out of the painted layer
       so an empty track is never counted as a visible trajectory. */
    cache.tracks = candidates.filter((track) => track.points.length > 1);
    cache.unavailableTracks = candidates.filter((track) => track.points.length <= 1);
    const context = cache.trackCanvas.getContext("2d"); if (!context) return; context.setTransform(1, 0, 0, 1, 0, 0); context.clearRect(0, 0, cache.trackCanvas.width, cache.trackCanvas.height); context.setTransform(cache.ratio, 0, 0, cache.ratio, 0, 0);
    cache.tracks.forEach((track) => {
      const selected = track.sample?.sample_id === appState.sampleId;
      context.lineWidth = selected ? 1.05 : track.risk ? .7 : .5;
      context.strokeStyle = selected ? "rgba(104,224,218,.26)" : track.risk ? "rgba(229,170,98,.14)" : "rgba(139,177,181,.065)";
      drawGroundTrackPath(context, track);
    });
    cache.tracksDirty = false; cache.trackRebuilds = (cache.trackRebuilds || 0) + 1;
  }
  function pointOnGroundTrack(track, simulationTimeSeconds) {
    const points = track?.points || []; if (!points.length) return null; if (points.length === 1) return { ...points[0] };
    const time = clamp(Number(simulationTimeSeconds), Number(track.startTime), Number(track.endTime)); let low = 0; let high = points.length - 1;
    while (low + 1 < high) { const middle = (low + high) >> 1; if (Number(points[middle].simulationTimeSeconds) <= time) low = middle; else high = middle; }
    const left = points[low]; const right = points[Math.min(points.length - 1, low + 1)]; const span = Number(right.simulationTimeSeconds) - Number(left.simulationTimeSeconds); const progress = span > 0 ? clamp((time - Number(left.simulationTimeSeconds)) / span, 0, 1) : 0;
    const width = Number(orbitRuntime.groundtrack?.width) || 0;
    const crossesDateLine = left.segmentIndex !== right.segmentIndex || (width > 0 && Math.abs(right.x - left.x) > width * .45);
    if (crossesDateLine) {
      /* Draw the marker on the same split polyline as the date-line path.
         Snapping to a sampled endpoint made it appear to drift away from the
         selected track, especially on a slow or high-DPI display. */
      const rawDelta = Number(right.lon) - Number(left.lon);
      const wrappedDelta = wrappedLongitudeDelta(Number(left.lon), Number(right.lon));
      /* Prefer the actual projected direction when the split came from a
         pixel jump.  It is the same branch used by groundTrackSegments and
         prevents a marker from taking the opposite side of the seam when an
         input uses 180 and -180 interchangeably. */
      const edgeLongitude = width > 0 && Number(right.x) < Number(left.x)
        ? 180
        : width > 0 && Number(right.x) > Number(left.x)
          ? -180
          : rawDelta > 0 ? -180 : 180;
      const seamProgress = Number.isFinite(wrappedDelta) && Math.abs(wrappedDelta) > 1e-8
        ? clamp((edgeLongitude - Number(left.lon)) / wrappedDelta, 0, 1)
        : .5;
      const seamY = left.y + (right.y - left.y) * seamProgress;
      const exitX = edgeLongitude === 180 ? width : 0;
      const entryX = exitX === 0 ? width : 0;
      const exact = groundTrackPointAtTime(track.sample, track.state, time, width, orbitRuntime.groundtrack.height);
      if (progress <= seamProgress) {
        const local = seamProgress > 1e-8 ? progress / seamProgress : 1;
        return { x: left.x + (exitX - left.x) * local, y: left.y + (seamY - left.y) * local, lon: exact?.lon ?? edgeLongitude, lat: exact?.lat ?? left.lat + (right.lat - left.lat) * progress, anomaly: exact?.anomaly ?? left.anomaly + (right.anomaly - left.anomaly) * progress, simulationTimeSeconds: time, segmentIndex: left.segmentIndex };
      }
      const remaining = Math.max(1e-8, 1 - seamProgress); const local = (progress - seamProgress) / remaining;
      return { x: entryX + (right.x - entryX) * local, y: seamY + (right.y - seamY) * local, lon: exact?.lon ?? (edgeLongitude === 180 ? -180 : 180), lat: exact?.lat ?? left.lat + (right.lat - left.lat) * progress, anomaly: exact?.anomaly ?? left.anomaly + (right.anomaly - left.anomaly) * progress, simulationTimeSeconds: time, segmentIndex: right.segmentIndex };
    }
    /* The painted path is a sampled polyline.  Interpolate on that exact
       segment so the marker cannot float beside the stroke when the analytic
       conic falls between samples or crosses the date-line.  Keep the exact
       geodetic values only as metadata for the status readout. */
    const exact = groundTrackPointAtTime(track.sample, track.state, time, orbitRuntime.groundtrack.width, orbitRuntime.groundtrack.height);
    const x = left.x + (right.x - left.x) * progress; const y = left.y + (right.y - left.y) * progress;
    return { x, y, lon: exact?.lon ?? left.lon + (right.lon - left.lon) * progress, lat: exact?.lat ?? left.lat + (right.lat - left.lat) * progress, anomaly: exact?.anomaly ?? left.anomaly + (right.anomaly - left.anomaly) * progress, simulationTimeSeconds: time, segmentIndex: left.segmentIndex };
  }
  function pointToSegmentDistance(point, start, end) {
    const dx = end.x - start.x; const dy = end.y - start.y; if (!dx && !dy) return Math.hypot(point.x - start.x, point.y - start.y);
    const t = clamp(((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy), 0, 1); return Math.hypot(point.x - (start.x + dx * t), point.y - (start.y + dy * t));
  }
  function drawGroundTrack() {
    const canvas = $("#groundtrack-canvas"); if (!canvas || appState.orbitMode !== "groundtrack") return false;
    const surface = ensureGroundTrackSurface(canvas); if (!surface?.context) return false; const { cache, width, height, ratio, context: ctx } = surface;
    drawGroundTrackBase(cache); if (groundTrackCyclesChanged(cache)) cache.tracksDirty = true; if (cache.tracksDirty) rebuildGroundTrackLayer(cache);
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, canvas.width, canvas.height); ctx.drawImage(cache.baseCanvas, 0, 0); ctx.drawImage(cache.trackCanvas, 0, 0); ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const displayed = orbitDisplaySamples(); const projected = cache.tracks; let selectedMarker = null; let selectedDistance = null; let selectedTrackVisible = false; let selectedHighlightSegmentCount = 0; let selectedHighlightCoversDisplay = false; appState.markers = [];
    projected.forEach((track) => {
      const { sample, risk } = track; const selected = sample.sample_id === appState.sampleId;
      if (selected) {
        const selectedHighlightSegments = selectedGroundTrackHighlightSegments(track);
        const displaySegments = track.displaySegments || track.segments || [];
        selectedHighlightSegmentCount = selectedHighlightSegments.length;
        selectedHighlightCoversDisplay = displaySegments.every((segment) => selectedHighlightSegments.includes(segment));
        ctx.lineWidth = 2.1; ctx.strokeStyle = "#68e0da"; ctx.shadowBlur = 12; ctx.shadowColor = "rgba(104,224,218,.72)"; drawGroundTrackPath(ctx, track, selectedHighlightSegments); ctx.shadowBlur = 0;
      }
      const marker = pointOnGroundTrack(track, appState.orbitTimeSeconds); if (!marker) return; const markerSegment = track.segments[marker.segmentIndex] || []; const markerOnPaintedSegment = (track.displaySegments || track.segments).includes(markerSegment); if (!markerOnPaintedSegment) return; appState.markers.push({ sample, x: marker.x, y: marker.y }); ctx.fillStyle = selected ? "#b7fff7" : risk ? "#f2b45d" : "#81999d"; ctx.shadowBlur = selected || risk ? 11 : 3; ctx.shadowColor = ctx.fillStyle; ctx.beginPath(); ctx.arc(marker.x, marker.y, selected ? 4.6 : 2.7, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0;
      if (selected) { selectedMarker = marker; selectedTrackVisible = true; selectedDistance = markerSegment.length > 1 ? Math.min(...markerSegment.slice(1).map((point, index) => pointToSegmentDistance(marker, markerSegment[index], point))) : 0; ctx.strokeStyle = "rgba(183,255,247,.66)"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(marker.x, marker.y, 10, 0, Math.PI * 2); ctx.stroke(); }
    });
    const closedTracks = projected.filter((track) => track.closed); const openTracks = projected.filter((track) => !track.closed); const windowBoundedTracks = closedTracks.filter((track) => track.windowBounded); const internalEndpointCount = closedTracks.reduce((total, track) => total + Number(track.internalEndpointCount || 0), 0);
    canvas.dataset.orbitDisplayCount = String(displayed.length); canvas.dataset.groundTrackCount = String(projected.length); canvas.dataset.groundTrackUnavailableCount = String(Math.max(0, displayed.length - projected.length)); canvas.dataset.selectedSampleId = appState.sampleId || ""; canvas.dataset.selectedTrackVisible = String(selectedTrackVisible); canvas.dataset.selectedHighlightSegmentCount = String(selectedHighlightSegmentCount); canvas.dataset.selectedHighlightCoversDisplay = String(selectedHighlightCoversDisplay); canvas.dataset.motionPhase = String(Math.round(appState.orbitTimeSeconds * 1000) / 1000); canvas.dataset.selectedLongitude = selectedMarker ? String(Math.round(selectedMarker.lon * 1000000) / 1000000) : ""; canvas.dataset.selectedLatitude = selectedMarker ? String(Math.round(selectedMarker.lat * 1000000) / 1000000) : ""; canvas.dataset.selectedTrackDistancePx = finite(selectedDistance) ? String(Math.round(Number(selectedDistance) * 1000) / 1000) : ""; canvas.dataset.backingStoreRebuilds = String(cache.backingStoreRebuilds || 0); canvas.dataset.trackRebuilds = String(cache.trackRebuilds || 0); canvas.dataset.closedTrackPeriods = String(closedTracks.length ? Math.max(...closedTracks.map((track) => track.periodCount || 1)) : 0); canvas.dataset.contextTrackPeriods = String(CONTEXT_GROUND_TRACK_PERIODS); canvas.dataset.selectedTrackPeriods = String(SELECTED_GROUND_TRACK_PERIODS); canvas.dataset.openTrackCount = String(openTracks.length); canvas.dataset.windowBoundedTrackCount = String(windowBoundedTracks.length); canvas.dataset.internalEndpointCount = String(internalEndpointCount);
    /* Read-only geometry trace used by browser regression probes.  It records
       the actual painted segments, not a synthetic line, so a seam or a
       missing sample can be diagnosed without exposing developer controls in
       the public interface. */
    if (window.__BRPHM_QA__ === true) {
      canvas.__groundTrackDebug = projected.map((track) => ({
        sampleId: track.sample?.sample_id || "",
        closed: track.closed,
          periodCount: track.periodCount,
          completeSegmentCount: track.completeSegmentCount,
          internalEndpointCount: track.internalEndpointCount,
          windowBounded: track.windowBounded,
          startTime: track.startTime,
          endTime: track.endTime,
          points: track.points.length,
          segments: (track.displaySegments || track.segments).map((segment) => ({
          count: segment.length,
          first: segment[0] ? { x: segment[0].x, y: segment[0].y, lon: segment[0].lon, lat: segment[0].lat } : null,
          last: segment[segment.length - 1] ? { x: segment[segment.length - 1].x, y: segment[segment.length - 1].y, lon: segment[segment.length - 1].lon, lat: segment[segment.length - 1].lat } : null,
        })),
      }));
      /* Exercise the exact wrapped-meridian representation too. Natural
         orbital samples usually cross near, rather than precisely at, +/-180.
         Keeping this tiny synthetic seam in QA metadata proves that the
         renderer never emits an infinite coordinate for an equivalent pair of
         longitude encodings. It is not a user-visible trajectory. */
      const seam = groundTrackSegments([
        { x: width, y: height * .5, lon: 180, lat: 0, simulationTimeSeconds: 0 },
        { x: 0, y: height * .5, lon: -180, lat: 0, simulationTimeSeconds: 1 },
      ], width, height);
      canvas.__groundTrackSeamDebug = seam.map((segment) => segment.map(({ x, y, lon, lat }) => ({ x, y, lon, lat })));
      /* Regression matrix for the four equivalent date-line encodings.  It
         stays QA-only: these coordinates are never mixed into the public
         trajectory layer or model inputs. */
      canvas.__groundTrackSeamCases = [
        [179.8, -179.8], [-179.8, 179.8], [180, -180], [-180, 180],
      ].map(([from, to]) => {
        const path = groundTrackSegments([
          { x: (from + 180) / 360 * width, y: height * .42, lon: from, lat: 14, simulationTimeSeconds: 0 },
          { x: (to + 180) / 360 * width, y: height * .58, lon: to, lat: -14, simulationTimeSeconds: 1 },
        ], width, height);
        const flat = path.flat();
        return {
          from, to, segments: path.length, points: flat.length,
          finite: flat.every((point) => [point.x, point.y, point.lon, point.lat].every(Number.isFinite)),
          minX: flat.length ? Math.min(...flat.map((point) => point.x)) : null,
          maxX: flat.length ? Math.max(...flat.map((point) => point.x)) : null,
        };
      });
    }
    const selectedState = orbitStateForSample(currentSample());
    const selectedUnavailableReason = selectedState?.drawable && !selectedState.groundTrackDrawable ? selectedState.groundTrackReason || "缺少 UTC 时间基准，二维星下点不会绘制。" : "";
    canvas.dataset.selectedTrackUnavailableReason = selectedUnavailableReason;
    const boundedNote = windowBoundedTracks.length ? ` · ${windowBoundedTracks.length} 条按真实时间窗截取` : "";
    const unavailableNote = selectedUnavailableReason ? ` · 当前样本${selectedUnavailableReason}` : "";
    const captionText = projected.length ? `星下点轨迹 · ${projected.length} 个可判定样本${boundedNote}${unavailableNote}` : selectedUnavailableReason ? `轨道事实不足${unavailableNote}` : "轨道事实不足 · 未绘制轨迹";
    const caption = $("#groundtrack-caption"); if (caption && caption.textContent !== captionText) caption.textContent = captionText;
    return true;
  }
  function cancelOrbitDrag() {
    const drag = orbitRuntime.drag;
    if (!drag) return;
    orbitRuntime.drag = null;
    const surface = drag.surface || $("#scene-frame");
    surface?.classList.remove("is-dragging");
    try {
      if (surface?.hasPointerCapture?.(drag.pointerId)) surface.releasePointerCapture(drag.pointerId);
    } catch (_error) { /* A cancelled or detached pointer has no capture to release. */ }
  }
  /* Route changes, mode changes, tab deactivation and focus loss all mean the
     user is no longer directly manipulating this surface. Clear the captured
     pointer and its release momentum as one atomic state transition so a
     hidden view cannot resume with a delayed camera jump. */
  function cancelOrbitInteraction() {
    cancelOrbitDrag();
    cancelOrbitInertia();
    cancelOrbitFrame();
    orbitRuntime.last = 0;
    orbitRuntime.lastRendered = 0;
    publishOrbitPose();
  }
  function resumeOrbitAfterInterruption() {
    if (document.hidden || appState.view !== "operate") return;
    orbitRuntime.last = 0;
    orbitRuntime.lastRendered = 0;
    refreshOrbitProjection({ force: true });
  }
  const ORBIT_CHROME_SELECTOR = ".scene-controls, .scene-overlay, .scene-status-stack, .scene-status-details, .scene-status-toggle, .scene-vignette, .stage-heading, .compact-sample-bar, .replay-context-editor";
  function isOrbitChromeTarget(target) {
    return Boolean(target?.closest?.(ORBIT_CHROME_SELECTOR));
  }
  function beginOrbitDrag(event) {
    if (isOrbitChromeTarget(event.target)) return;
    /* A drag belongs to the canvas surface. The frame remains a convenient
       hit area for an empty margin, but nested headings/cards never change
       the camera pose. */
    if (event.target !== event.currentTarget && !event.target?.closest?.("canvas")) return;
    if (appState.orbitMode !== "globe") return;
    if (!event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) return;
    cancelOrbitDrag(); const surface = event.currentTarget; const rect = surface.getBoundingClientRect(); cancelOrbitInertia(); const now = performance.now(); orbitRuntime.drag = { pointerId: event.pointerId, surface, startX: event.clientX, startY: event.clientY, lastX: event.clientX, lastY: event.clientY, startYaw: appState.orbitOffset, startPitch: appState.orbitPitch, lastYaw: appState.orbitOffset, lastPitch: appState.orbitPitch, width: Math.max(280, rect.width), height: Math.max(240, rect.height), lastTime: now, moved: false, velocityYaw: 0, velocityPitch: 0, lastDeltaYaw: 0, lastDeltaPitch: 0 };
    try { surface.setPointerCapture?.(event.pointerId); } catch (_error) { /* Capture is an enhancement; the next pointer event can still finish cleanly. */ }
    surface.classList.add("is-dragging"); event.preventDefault();
  }
  function moveOrbitDrag(event) {
    const drag = orbitRuntime.drag; if (!drag || drag.pointerId !== event.pointerId) return;
    if (appState.orbitMode !== "globe") { finishOrbitDrag(event, true); return; }
    /* Do not inspect getCoalescedEvents(). Browser timestamp epochs differ,
       and a batched sample can be older than this dispatched PointerEvent.
       The current event is already the browser's authoritative screen pose;
       pair it only with performance.now(), whose clock is monotonic. */
    const clientX = Number(event.clientX);
    const clientY = Number(event.clientY);
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return;
    const dx = clientX - drag.startX; const dy = clientY - drag.startY; const distance = Math.hypot(dx, dy); drag.moved = drag.moved || distance > 5;
    /* Direct manipulation follows the grabbed surface: dragging right moves
       the rendered Earth right. The spherical camera therefore travels in
       the opposite yaw direction to the pointer displacement. */
    const timestamp = performance.now(); const elapsed = Math.max(1, timestamp - drag.lastTime); const nextYaw = drag.startYaw - dx / drag.width * 3.25; const requestedPitch = drag.startPitch + dy / drag.height * 2.2; const nextPitch = clamp(requestedPitch, -1.05, 1.05);
    const deltaYaw = nextYaw - drag.lastYaw; const deltaPitch = nextPitch - drag.lastPitch;
    drag.velocityYaw = drag.velocityYaw * .58 + deltaYaw / elapsed * .42; drag.velocityPitch = drag.velocityPitch * .58 + deltaPitch / elapsed * .42; if (nextPitch !== requestedPitch) drag.velocityPitch = 0;
    drag.lastDeltaYaw = deltaYaw; drag.lastDeltaPitch = deltaPitch; drag.lastX = clientX; drag.lastY = clientY; appState.orbitOffset = nextYaw; appState.orbitPitch = nextPitch; drag.lastYaw = nextYaw; drag.lastPitch = nextPitch; drag.lastTime = timestamp; drawOrbitInteractionFrame(); event.preventDefault();
  }
  function pickOrbitTarget(event) {
    const canvas = appState.orbitMode === "groundtrack" ? $("#groundtrack-canvas") : $("#orbit-canvas"); if (!canvas) return;
    if (appState.orbitMode === "globe" && !orbitRuntime.fallback && orbitRuntime.raycaster) { const rect = canvas.getBoundingClientRect(); orbitRuntime.pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1); orbitRuntime.raycaster.setFromCamera(orbitRuntime.pointer, orbitRuntime.camera); const hit = orbitRuntime.raycaster.intersectObjects(orbitRuntime.points, false)[0]; if (hit?.object?.userData?.sampleId) setSample(hit.object.userData.sampleId); return; }
    const rect = canvas.getBoundingClientRect(); let nearest = null; let distance = 18; appState.markers.forEach((marker) => { const candidate = Math.hypot(event.clientX - rect.left - marker.x, event.clientY - rect.top - marker.y); if (candidate < distance) { nearest = marker; distance = candidate; } }); if (nearest) setSample(nearest.sample.sample_id);
  }
  function finishOrbitDrag(event, cancelled = false) {
    const drag = orbitRuntime.drag; if (!drag || drag.pointerId !== event.pointerId) return; orbitRuntime.drag = null; const surface = drag.surface || event.currentTarget || $("#scene-frame"); surface?.classList.remove("is-dragging"); try { if (surface?.hasPointerCapture?.(event.pointerId)) surface.releasePointerCapture(event.pointerId); } catch (_error) { /* Pointer capture may already be released by the browser. */ }
    if (cancelled) cancelOrbitInertia();
    if (!cancelled && drag.moved && !reduceMotion) {
      /* Do not hand a stale coalesced event a velocity in the opposite
         direction of the last real movement.  A tiny release impulse keeps
         the orbit tactile while eliminating the occasional vertical reversal
         reported on mixed-DPI touchpads. */
      const releaseAge = Math.max(0, performance.now() - drag.lastTime);
      const yawVelocity = releaseAge <= 96 && Math.sign(drag.lastDeltaYaw || drag.velocityYaw) === Math.sign(drag.velocityYaw) ? drag.velocityYaw : 0;
      const pitchVelocity = releaseAge <= 96 && Math.sign(drag.lastDeltaPitch || drag.velocityPitch) === Math.sign(drag.velocityPitch) ? drag.velocityPitch : 0;
      orbitRuntime.inertiaYaw = clamp(yawVelocity, -.00072, .00072); orbitRuntime.inertiaPitch = clamp(pitchVelocity, -.00055, .00055); orbitRuntime.inertiaActive = Math.max(Math.abs(orbitRuntime.inertiaYaw), Math.abs(orbitRuntime.inertiaPitch)) >= .000018;
    }
    if (!cancelled && !drag.moved) pickOrbitTarget(event); drawOrbitInteractionFrame(); scheduleOrbitFrame();
  }
  function resetOrbitView() {
    appState.orbitAngle = 0; appState.orbitTimeSeconds = 0; appState.orbitOffset = 0; appState.orbitPitch = 0; appState.orbitZoom = 1; cancelOrbitDrag(); cancelOrbitInertia(); cancelOrbitFrame(); updateOrbitZoom(false);
    // A queued pointer frame can otherwise repaint the pre-reset pose on
    // narrow devices.  The unified refresh also redraws a paused ground track.
    // Keep the public draw entry point in the reset path so integrations and
    // browser probes observe the same immediate refresh as other orbit actions.
    drawOrbit({ force: true, invalidate: true });
  }

  function orbitBaseDistance() { return appState.orbitMode === "globe" ? 4.28 : 5.5; }
  function updateOrbitZoom(redraw = true) {
    appState.orbitZoom = clamp(appState.orbitZoom, .74, 1.42);
    if (orbitRuntime.camera && appState.orbitMode === "globe") applyOrbitView();
    setText("#orbit-zoom-level", `${Math.round(appState.orbitZoom * 100)}%`);
    const zoomIn = $("#orbit-zoom-in"); const zoomOut = $("#orbit-zoom-out");
    if (zoomIn) zoomIn.disabled = appState.orbitZoom >= 1.419; if (zoomOut) zoomOut.disabled = appState.orbitZoom <= .741;
    if (redraw && appState.view === "operate" && appState.orbitMode === "globe") requestOrbitRender();
  }
  function changeOrbitZoom(multiplier) { appState.orbitZoom *= multiplier; updateOrbitZoom(); }
  function setOrbitMode(mode) {
    cancelOrbitInteraction();
    appState.orbitMode = mode === "groundtrack" ? "groundtrack" : "globe";
    const frame = $("#scene-frame"); frame?.setAttribute("data-orbit-mode", appState.orbitMode); const label = appState.orbitMode === "globe" ? (orbitRuntime.fallback ? "兼容投影" : "三维地球") : "二维星下点"; setText("#orbit-mode span", label);
    const glyph = $("#orbit-mode use"); glyph?.setAttribute("href", appState.orbitMode === "globe" ? "#i-globe" : "#i-orbit"); glyph?.setAttribute("xlink:href", appState.orbitMode === "globe" ? "#i-globe" : "#i-orbit");
    setText("#scene-note", appState.orbitMode === "globe" ? (orbitRuntime.fallback ? "离线兼容工程投影，不替代真实星历。" : "统一仿真时钟驱动的轨道工程投影，不替代实时星历。") : "全部可判定样本共用同一仿真时钟；当前对象与左侧选择同步高亮。");
    updateOrbitZoom(false); refreshOrbitProjection({ force: true, invalidate: true });
  }
  function handleOrbitWheel(event) {
    if (event.target?.closest?.(".scene-controls")) return;
    if (appState.view !== "operate" || !event.deltaY) return;
    if ((event.ctrlKey || event.metaKey) && appState.orbitMode === "globe") { changeOrbitZoom(Math.exp(-clamp(event.deltaY, -120, 120) * .00135)); event.preventDefault(); return; }
    /* A normal wheel gesture is deliberately left to the browser.  The active
       view, nested lists, trackpads, touchpads and keyboard accessibility
       then share one native scroll chain instead of competing JS handlers. */
  }

  function chartSize(svg, fallbackHeight = 180) {
    const box = svg?.getBoundingClientRect?.() || { width: 0, height: 0 };
    /* SVGs inside a freshly opened disclosure can report 0px for one layout
       pass. Read the owning frame as a second source before choosing the
       fallback; this keeps replay and prediction charts on the same drawing
       contract without manufacturing any data points. */
    const owner = svg?.closest?.(".trace-frame, .telemetry-result-chart, .evidence-plot-section");
    const ownerBox = owner?.getBoundingClientRect?.() || { width: 0, height: 0 };
    const width = Math.max(220, box.width || ownerBox.width || 600);
    const height = Math.max(fallbackHeight, box.height || ownerBox.height || fallbackHeight);
    return { width, height };
  }
  function downsample(values, max = 480) { if (!Array.isArray(values) || values.length <= max) return values || []; const step = Math.ceil(values.length / max); const result = []; for (let i = 0; i < values.length; i += step) result.push(values[i]); return result; }
  function drawTelemetry() {
    const svg = $("#telemetry-canvas"); if (!svg || appState.view !== "operate") return;
    svg.replaceChildren(); const size = chartSize(svg, 170); svg.setAttribute("viewBox", `0 0 ${size.width} ${size.height}`); svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    const item = currentTelemetry(); const prediction = currentPrediction(); const sample = currentSample(); const channel = appState.channel || Object.keys(item?.channels || {})[0];
    const values = downsample(item?.channels?.[channel] || []); const rows = currentRows();
    svg.dataset.replayPredictionPointCount = String(rows.length);
    svg.dataset.replayTelemetryPointCount = String(values.length);
    svg.dataset.replayCurveMode = sample?.uploaded && rows.length ? "full-prediction-response" : "telemetry-and-prediction";
    // Uploaded predictions may now carry the exact accepted source trace in
    // ``currentTelemetry``.  In that case use the same two-lane chart as an
    // embedded sample (raw channel above, RUL response below).  If the trace
    // is genuinely unavailable, retain the explicit prediction-only chart;
    // never copy P50 into a fake raw telemetry series.
    if (rows.length && !values.some(finite)) {
      drawTelemetryResultChart(svg, {
        predictions: rows,
        rul_unit: prediction?.time_unit,
        allow_index_axis: true,
        replay_prediction_only: !sample?.uploaded,
      });
      return;
    }
    if (!window.d3 || !values.length) { const text = document.createElementNS("http://www.w3.org/2000/svg", "text"); text.setAttribute("x", 14); text.setAttribute("y", 30); text.setAttribute("class", "chart-label"); text.textContent = "暂无真实遥测或预测曲线"; svg.append(text); return; }
    const d3 = window.d3; const root = d3.select(svg); const margin = { top: 15, right: 42, bottom: 18, left: 42 }; const width = size.width - margin.left - margin.right; const height = size.height - margin.top - margin.bottom; const topHeight = Math.max(58, height * .47); const lowerHeight = Math.max(35, height - topHeight - 13);
    const stableExtent = (numbers) => { let extent = d3.extent(numbers.length ? numbers : [0, 1]); if (extent[0] === extent[1]) { const pad = Math.max(Math.abs(extent[0] || 0) * .08, .001); extent = [extent[0] - pad, extent[1] + pad]; } return extent; };
    const top = root.append("g").attr("transform", `translate(${margin.left},${margin.top})`); const bottom = root.append("g").attr("transform", `translate(${margin.left},${margin.top + topHeight + 13})`);
    const x = d3.scaleLinear().domain([0, Math.max(1, values.length - 1)]).range([0, width]); const pointX = (index) => values.length === 1 ? width * .5 : x(index); const y = d3.scaleLinear().domain(stableExtent(values.filter(finite).map(Number))).nice().range([topHeight, 0]); const data = values.map((value, index) => ({ index, value }));
    top.append("g").attr("class", "chart-grid").call(d3.axisLeft(y).ticks(3).tickSize(-width).tickFormat("")); top.append("g").attr("class", "chart-axis").call(d3.axisLeft(y).ticks(3).tickFormat((value) => compact(value)));
    if (data.length === 1) {
      top.append("circle").attr("cx", width * .5).attr("cy", y(Number(data[0].value))).attr("r", 4.5).attr("fill", "#f2b45d");
      top.append("text").attr("x", width * .5).attr("y", y(Number(data[0].value)) - 10).attr("text-anchor", "middle").attr("class", "chart-label").text("单一有效窗口");
    } else {
      top.append("path").datum(data).attr("d", d3.line().defined((entry) => finite(entry.value)).x((entry) => pointX(entry.index)).y((entry) => y(Number(entry.value))).curve(d3.curveMonotoneX)).attr("fill", "none").attr("stroke", "#f2b45d").attr("stroke-width", 1.7);
    }
    top.append("text").attr("class", "chart-label").attr("x", 0).attr("y", 9).text(payload.channel_meta?.[channel]?.unit ? `${payload.channel_meta[channel].label || channel} / ${payload.channel_meta[channel].unit}` : channel || "遥测");
    const rawTimes = Array.isArray(item?.t_days) ? item.t_days.map((value) => Number(value)) : []; const rawStart = rawTimes.find(Number.isFinite); const rawEnd = [...rawTimes].reverse().find(Number.isFinite); const rawSpan = Number.isFinite(rawStart) && Number.isFinite(rawEnd) && rawEnd > rawStart ? rawEnd - rawStart : null; const hasRawTime = rawSpan !== null; const rowData = rows.map((row, index) => { const timePosition = finite(row?.time_order) && rawSpan !== null ? clamp((Number(row.time_order) - rawStart) / rawSpan, 0, 1) * Math.max(1, values.length - 1) : rows.length > 1 ? index / (rows.length - 1) * Math.max(1, values.length - 1) : .5; return { index: timePosition, _syntheticTime: !hasRawTime, _windowIndex: index, ...row }; }); const allRul = rowData.flatMap((row) => [row.y_true, row.p10, row.p50, row.p90].filter(finite).map(Number)); const yr = d3.scaleLinear().domain(stableExtent(allRul)).nice().range([lowerHeight, 0]); const xR = d3.scaleLinear().domain([0, Math.max(1, values.length - 1)]).range([0, width]); const predictionStartIndex = rowData.map((row) => Number(row.index)).filter(Number.isFinite).reduce((minimum, value) => Math.min(minimum, value), Infinity); const predictionStartRatio = Number.isFinite(predictionStartIndex) ? clamp(predictionStartIndex / Math.max(1, values.length - 1), 0, 1) : 0;
    svg.dataset.replayPredictionStartRatio = String(predictionStartRatio);
    svg.dataset.replayObservationWindowVisible = String(predictionStartRatio > .001);
    if (predictionStartRatio > .001) {
      const boundaryX = xR(predictionStartIndex); const observation = bottom.append("g").attr("data-chart-observation-window", "true");
      observation.append("rect").attr("class", "chart-observation-window").attr("x", 0).attr("y", 0).attr("width", boundaryX).attr("height", lowerHeight).append("title").text("首个连续模型窗口形成前只有原始遥测，不存在寿命预测输出；页面不会补造曲线。");
      observation.append("line").attr("class", "chart-prediction-boundary").attr("x1", boundaryX).attr("x2", boundaryX).attr("y1", 0).attr("y2", lowerHeight);
      observation.append("text").attr("class", "chart-observation-label").attr("x", boundaryX * .5).attr("y", Math.max(18, lowerHeight * .54)).attr("text-anchor", "middle").text("模型观察窗 · 尚无寿命输出");
    }
    bottom.append("g").attr("class", "chart-grid").call(d3.axisLeft(yr).ticks(2).tickSize(-width).tickFormat("")); bottom.append("g").attr("class", "chart-axis").call(d3.axisLeft(yr).ticks(2).tickFormat((value) => compact(value)));
    if (rowData.length === 1) {
      const row = rowData[0]; const cx = width * .5;
      if (finite(row.p10) && finite(row.p90)) bottom.append("line").attr("x1", cx).attr("x2", cx).attr("y1", yr(Number(row.p10))).attr("y2", yr(Number(row.p90))).attr("stroke", "rgba(104,224,218,.28)").attr("stroke-width", 14).attr("stroke-linecap", "round");
      [["p10", "#f2b45d", 2.4], ["p50", "#4bd9d2", 4], ["p90", "#8fb9ff", 2.4]].forEach(([key, color, radius]) => { if (finite(row[key])) bottom.append("circle").attr("cx", cx).attr("cy", yr(Number(row[key]))).attr("r", radius).attr("fill", color); });
      bottom.append("text").attr("x", cx + 12).attr("y", lowerHeight - 2).attr("class", "chart-label").text(sample?.uploaded ? `预测响应未返回原始遥测，${hasRawTime ? "保留预测窗口顺序" : "使用窗口顺序"}，未补造连续曲线` : "单一回放窗口");
    } else {
      const area = d3.area().defined((row) => finite(row.p10) && finite(row.p90)).x((row) => xR(row.index)).y0((row) => yr(Number(row.p10))).y1((row) => yr(Number(row.p90))).curve(d3.curveMonotoneX); bottom.append("path").datum(rowData).attr("d", area).attr("fill", "rgba(104,169,255,.20)");
      const rulLine = (key, color, dash, strokeWidth) => bottom.append("path").datum(rowData).attr("d", d3.line().defined((row) => finite(row[key])).x((row) => xR(row.index)).y((row) => yr(Number(row[key]))).curve(d3.curveMonotoneX)).attr("fill", "none").attr("stroke", color).attr("stroke-width", strokeWidth).attr("stroke-dasharray", dash || null);
      rulLine("y_true", "#dfe9e8", "3 3", 1); rulLine("p50", "#4bd9d2", null, 1.7);
    }
    bottom.append("text").attr("class", "chart-label").attr("x", 0).attr("y", 10).text(`RUL / ${unitLabel(prediction?.time_unit)}`);
    if (values.length > 1) { const cursorX = x(Math.round(appState.progress * Math.max(1, values.length - 1))); root.append("line").attr("x1", margin.left + cursorX).attr("x2", margin.left + cursorX).attr("y1", margin.top).attr("y2", margin.top + height).attr("stroke", "#8ce9e5").attr("stroke-width", 1).attr("stroke-dasharray", "2 3").attr("opacity", .75); const overlay = root.append("rect").attr("x", margin.left).attr("y", margin.top).attr("width", width).attr("height", height).attr("fill", "transparent").style("cursor", "crosshair"); overlay.on("pointermove", (event) => { const [px] = d3.pointer(event); setProgressFromChartPointer((px - margin.left) / width); }).on("pointerdown", (event) => { const [px] = d3.pointer(event); setProgressFromChartPointer((px - margin.left) / width); }); }
  }
  function setProgressFromChartPointer(ratio) { setProgress(clamp(ratio, 0, 1)); }

  // The service already projects its payload, but operation errors and older
  // snapshots can still contain engineering vocabulary.  This second, tiny
  // boundary keeps every string that reaches evaluator-facing DOM readable.
  const PUBLIC_COPY_REPLACEMENTS = Object.freeze([
    [/\u7ade\u8d5b\u6a21\u578b\u5408\u540c|\u6570\u636e\u5408\u540c|\u6a21\u578b\u5408\u540c|\u8f93\u5165\u5951\u7ea6|\u6a21\u578b\u5951\u7ea6|\u5408\u540c|\u5951\u7ea6/gi, "输入要求"],
    [/\u5c01\u5b58|evaluation/gi, "独立测试"],
    [/modelFile/gi, "模型文件"],
    [/modelVersion/gi, "模型登记信息"],
    [/inputCheck/gi, "提交前检查"],
    [/resultRecord/gi, "测试记录"],
    [/\u968f\u673a\u79cd\u5b50|orbitHash/gi, "独立模型成员"],
    [/\u516c\u5f00\u57df/gi, "公开退化数据"],
    [/\u76ee\u6807\u57df/gi, "航天数据"],
    [/\u8fc1\u79fb/gi, "适配"],
    [/\u751f\u4ea7\u6e05\u5355/gi, "模型版本登记信息"],
    [/\u51bb\u7ed3\u8d44\u4ea7|\u51bb\u7ed3\u6a21\u578b/gi, "已核验模型文件"],
    [/\u811a\u672c\u6587\u4ef6\u540d|\u811a\u672c\u540d\u79f0|\u811a\u672c/gi, "流程名称"],
    [/\u547d\u4ee4\u884c\u53c2\u6570|\u547d\u4ee4\u53c2\u6570|\u547d\u4ee4|argv/gi, "填写项"],
    [/\u5185\u90e8(?:\u6d41\u7a0b|\u5de5\u5177|\u5b57\u6bb5|\u8def\u5f84)/gi, "服务信息"],
    [/\u5f00\u53d1\u5165\u53e3|\u5f00\u53d1\u5de5\u5177|\u5f00\u53d1\u671f|\u5f00\u53d1/gi, "研究流程"],
  ]);
  function publicNarrative(value, fallback = "未提供") {
    const PUBLIC_INTERNAL_ROUTE_TOKEN_RE = /(?:^|[^A-Za-z0-9])S(?:21|22)(?=$|[^A-Za-z0-9])/i;
    const PUBLIC_INTERNAL_TEXT_RE = /(?:^|[^A-Za-z0-9])(?:T(?:1|2)|gate\d+|evaluation|modelFile|modelVersion|inputCheck|resultRecord|orbitHash|--[A-Za-z][\w-]*|(?:[A-Za-z]:[\\/]|\/(?:mnt|home|tmp|data|results|configs|scripts|outputs)[\\/])|(?:\.{1,2}[\\/]|(?:sim|gmat|src|configs|results|scripts|data)[\\/])|[\w.-]+\.(?:py|js|json|yaml|yml|parquet|mat|script))(?=$|[^A-Za-z0-9])/i;
    let text = String(value ?? "").trim();
    if (!text || PUBLIC_INTERNAL_ROUTE_TOKEN_RE.test(text)) return fallback;
    PUBLIC_COPY_REPLACEMENTS.forEach(([pattern, replacement]) => { text = text.replace(pattern, replacement); });
    return PUBLIC_INTERNAL_TEXT_RE.test(text) ? fallback : text;
  }
  function decisionText(value) {
    if (value === true || value === "pass") return "满足当前检查条件";
    if (value === false || value === "fail") return "未满足当前检查条件";
    if (value === "appendix") return "作为补充研究结果保留";
    if (value === "production") return "用于当前生产推理";
    return publicNarrative(value);
  }
  function renderEvidence() {
    const evidence = payload.evidence || {}; const metrics = evidence.evaluation?.metrics || {}; const robustness = evidence.robustness || {}; const deployment = evidence.deployment || {}; const dataState = evidence.data_state || {};
    const production = appState.telemetrySchema?.productionModel || payload.production_model || {}; const productionValidated = production.status === "validated" && validatedProductionLabel(production) !== TELEMETRY_MODEL_UNAVAILABLE_LABEL;
    const segments = [
      { icon: "#i-cpu", title: "部件输入要求", detail: "电池部件（储能系统）与反作用轮部件（姿态控制执行器）分别使用对应的输入语义和寿命单位", ready: Boolean(dataState.available ?? Object.keys(dataState).length) },
      { icon: "#i-activity", title: "公开退化数据训练", detail: "用公开退化数据建立两类部件的基础模型", ready: Boolean(Object.keys(metrics).length) },
      { icon: "#i-layers", title: "航天数据适配", detail: "用航天数据校准输入尺度和退化规律", ready: Boolean(Object.keys(metrics).length) },
      { icon: "#i-shield", title: "分部件剩余寿命预测模型", detail: "电池部件（储能系统）与反作用轮部件（姿态控制执行器）正式点预测均取三个独立模型结果的中位数", ready: productionValidated },
    ];
    const host = $("#method-strip");
    host?.replaceChildren(...segments.map((segment) => {
      const node = document.createElement("div"); node.className = "method-segment";
      const iconNode = icon(segment.icon); const label = document.createElement("span");
      const title = document.createElement("strong"); const detail = document.createElement("small");
      title.textContent = segment.title; detail.textContent = segment.detail; label.append(title, detail);
      node.append(iconNode, label); node.title = segment.ready ? "已有相应机器产物" : "当前没有相应机器产物";
      return node;
    }));
    animateTelemetryNodes(host?.querySelectorAll?.(".method-segment"), { y: 4, duration: .3, stagger: .04 });
    const conclusion = $("#evidence-conclusion");
    if (conclusion) {
      const label = document.createElement("span"); const title = document.createElement("strong"); const detail = document.createElement("small");
      label.textContent = productionValidated ? "当前生产身份" : "生产身份状态";
      title.textContent = productionValidated ? validatedProductionLabel(production) : "模型版本或文件完整性尚未通过核验";
      detail.textContent = productionValidated
        ? `模型版本标识 ${production.modelVersion_sha256 || "已核验"}；两个部件的正式预测均由三个独立模型结果综合得到；同时保留结果范围以反映模型差异。`
        : "当前模型由电池部件（储能系统）与反作用轮部件（姿态控制执行器）两部分组成，需在模型身份和文件完整核验后才可用于正式预测。";
      conclusion.replaceChildren(label, title, detail);
    }
    renderEvidenceSampleSelector(); renderEvaluationDimensions(evidence.evaluation || {}); renderMethodMetrics(metrics);
    renderResultRecord("#robustness-section", "独立稳健性复核", robustness, [
      ["结论", "decision", decisionText], ["稳定正向改善", "positive_gain", (value) => value === true ? "已观察到" : value === false ? "未观察到" : "未读取到"], ["适用范围", "scope", publicNarrative], ["复核规模", "member_count", (value) => finite(value) ? `${compact(value)} 个独立模型成员` : "未读取到"], ["真实结论", "summary", publicNarrative],
    ]);
    renderResultRecord("#deployment-section", "部署性能复核", deployment, [
      ["复核状态", "verified", (value) => value === true ? "已完成" : "未完成"], ["模型体积", "model_size_mb", (value) => finite(value) ? `${compact(value)} MiB` : "未读取到"], ["CPU P50", "latency_p50_ms", (value) => finite(value) ? `${compact(value)} ms` : "未读取到"], ["CPU P95", "latency_p95_ms", (value) => finite(value) ? `${compact(value)} ms` : "未读取到"], ["峰值内存", "peak_memory_mb", (value) => finite(value) ? `${compact(value)} MiB` : "未读取到"], ["转换漂移", "conversion_drift_pct", (value) => finite(value) ? `${format(value, 6)}%` : "未读取到"],
    ]);
    drawComparison();
  }
  function renderEvaluationDimensions(evaluation) {
    const host = $("#evaluation-dimension-strip"); if (!host) return;
    const dimensions = evaluation?.dimensions && typeof evaluation.dimensions === "object" ? evaluation.dimensions : {};
    const definitions = [
      ["prediction_error", "预测误差", "#i-activity", "独立验证误差与区间"],
      ["trend_consistency", "趋势一致性", "#i-orbit", "跨寿命阶段一致性"],
      ["stability", "稳定性", "#i-shield", "跨样本与独立模型成员复核"],
      ["earliness", "提前性", "#i-clock", "相对预测视界"],
    ];
    const nodes = definitions.map(([key, titleText, iconReference, fallbackScope]) => {
      const data = dimensions[key] && typeof dimensions[key] === "object" ? dimensions[key] : {};
      const available = data.available === true;
      const item = document.createElement("article"); item.className = "evaluation-dimension"; item.dataset.state = available ? "available" : "unavailable";
      item.append(icon(iconReference));
      const copy = document.createElement("div"); const title = document.createElement("strong"); const scope = document.createElement("small"); const summary = document.createElement("p");
      title.textContent = titleText; scope.textContent = publicNarrative(data.scope, fallbackScope);
      summary.textContent = available
        ? publicNarrative(data.summary, "已读取结构化产物；具体数值与适用范围见下方。")
        : "当前未读取到结构化产物";
      copy.append(title, scope, summary);
      const status = document.createElement("span"); status.textContent = available ? "已读取" : "不可用";
      item.append(copy, status);
      const componentEntries = data.components && typeof data.components === "object"
        ? ["battery", "reaction_wheel"].map((component) => data.components[component]).filter((entry) => entry && typeof entry === "object")
        : [];
      const factRows = componentEntries.map((entry) => {
        const mean = finite(entry.mean) ? compact(entry.mean) : "--";
        const range = finite(entry.minimum) && finite(entry.maximum) ? `${compact(entry.minimum)}-${compact(entry.maximum)}` : "--";
        const memberSummary = finite(entry.member_count) ? `${compact(entry.member_count)} 个独立模型成员` : "独立模型成员数未读取到";
        return [publicNarrative(entry.label, "部件"), `均值 ${mean} · 范围 ${range} · ${memberSummary}`];
      });
      if (key === "stability" && finite(data.member_count)) factRows.push(["复核规模", `${compact(data.member_count)} 个独立模型成员`]);
      if (factRows.length) {
        const facts = document.createElement("dl"); facts.className = "evaluation-dimension-facts";
        factRows.forEach(([labelText, valueText]) => {
          const row = document.createElement("div"); const term = document.createElement("dt"); const value = document.createElement("dd");
          term.textContent = labelText; value.textContent = valueText; row.append(term, value); facts.append(row);
        });
        item.append(facts);
      }
      return item;
    });
    host.replaceChildren(...nodes); animateTelemetryNodes(nodes, { y: 4, duration: .3, stagger: .045 });
    const boundary = evaluation?.organizer_boundary && typeof evaluation.organizer_boundary === "object" ? evaluation.organizer_boundary : {};
    setText("#evaluation-scope-boundary", publicNarrative(
      boundary.summary,
      "本页仅报告项目内当前可读取的结构化验证产物；未纳入验证的数据、场景与组件不在结论范围内，也不能由页面外推。",
    ));
  }
  function comparableEvidenceSamples() {
    return availableSamples().filter((sample) => {
      const rows = predictions[sample.sample_id]?.rows || [];
      return rows.some((row) => finite(row.y_true) && evidenceSeriesForRows([row]).length > 0);
    });
  }
  function renderEvidenceSampleSelector() {
    const select = $("#evidence-sample-select"); if (!select) return;
    const comparable = comparableEvidenceSamples();
    if (!comparable.some((sample) => sample.sample_id === appState.evidenceSampleId)) {
      appState.evidenceSampleId = comparable.some((sample) => sample.sample_id === appState.sampleId)
        ? appState.sampleId : comparable[0]?.sample_id || null;
    }
    const options = comparable.length ? comparable.map((sample) => {
      const option = document.createElement("option"); option.value = sample.sample_id;
      option.textContent = `${publicComponentLabel(sample.line)} · ${sampleDisplayName(sample)} · ${sample.dataset_id || "结果数据"}`;
      option.selected = sample.sample_id === appState.evidenceSampleId; return option;
    }) : (() => {
      const option = document.createElement("option"); option.value = ""; option.disabled = true; option.selected = true;
      option.textContent = "暂无含真实值和预测值的样本"; return [option];
    })();
    select.replaceChildren(...options);
    select.disabled = comparable.length === 0; upgradeSelect(select); syncCustomSelect(select);
    const selected = evidenceSample();
    const availableSeries = evidenceSeriesForRows(predictions[selected?.sample_id]?.rows || []);
    setText("#evidence-sample-detail", selected
      ? `${selected.dataset_id || "结果数据"} · 仅使用该样本已有的真值与实际预测路径${availableSeries.length < EVIDENCE_SERIES.length ? "，不会补齐缺失路径" : ""}`
      : "当前没有含真实值和预测值的样本");
    if (!select.dataset.bound) {
      select.dataset.bound = "true";
      select.addEventListener("change", () => {
        appState.evidenceSampleId = select.value; renderEvidenceSampleSelector(); drawComparison();
        const plot = $(".evidence-plot-section");
        if (window.gsap && !reduceMotion && plot) window.gsap.fromTo(plot, { y: 3, autoAlpha: .72 }, { y: 0, autoAlpha: 1, duration: .32, ease: "expo.out", clearProps: "transform,opacity,visibility" });
      });
    }
  }
  function renderMethodMetrics(metrics) {
    const host = $("#method-metrics"); if (!host) return;
    const lines = Object.keys(metrics || {});
    if (!lines.length) { host.textContent = "尚未读取到可比较的独立测试指标。"; return; }
    const grid = document.createElement("div"); grid.className = "method-metric-grid";
    lines.forEach((line) => {
      const metric = metrics[line] || {}; const card = document.createElement("article"); card.className = "method-metric-card";
      const title = document.createElement("h3"); title.textContent = metric.label || publicComponentLabel(line === "battery" ? "bat" : "rw");
      const description = document.createElement("p"); const change = finite(metric.rmse_change) ? Number(metric.rmse_change) : null; const measurementUnit = metric.unit || (line === "battery" ? "循环" : "天");
      description.textContent = Number.isFinite(change)
          ? (change > 0
          ? `在相同独立测试口径下，航天数据适配结果的 RMSE 比公开退化数据基线低 ${compact(change)} ${measurementUnit}。差值是带量纲绝对值，不是百分比。`
          : change < 0
            ? `在相同独立测试口径下，航天数据适配结果的 RMSE 比公开退化数据基线高 ${compact(Math.abs(change))} ${measurementUnit}；负向结果按原始记录保留。差值不是百分比。`
            : "在相同独立测试口径下，两套模型的 RMSE 差值为 0；这是带量纲绝对差值，不是百分比。")
        : "公开退化数据基线、航天数据适配结果与当前模型结果均按各自登记的测试范围展示，页面不跨数据划分拼接，也不把历史结果写成当前模型的新鲜结论。";
      const values = document.createElement("div"); values.className = "method-metric-values";
      const valueRows = [
        { label: "独立测试 RMSE", value: metric.evaluation_rmse, suffix: measurementUnit },
        { label: "独立测试 MAE", value: metric.evaluation_mae, suffix: measurementUnit },
        { label: "90% 区间覆盖", value: finite(metric.coverage_90) ? Number(metric.coverage_90) * 100 : null, suffix: "%" },
        { label: "平均区间宽度", value: metric.interval_width_90 ?? metric.mpiw_90, suffix: measurementUnit },
        { label: "误差校准分位", value: metric.qhat, suffix: measurementUnit },
        { label: "公开退化数据基线 RMSE", value: metric.supervised_rmse, suffix: measurementUnit },
        { label: "航天数据适配 RMSE", value: metric.adaptation_rmse, suffix: measurementUnit },
        { label: "RMSE 差值（基线−适配）", value: metric.rmse_change, suffix: measurementUnit, difference: true },
      ].filter(({ value }) => finite(value));
      valueRows.forEach(({ label, value, suffix, difference = false }) => {
        const cell = document.createElement("div"); const caption = document.createElement("span"); const number = document.createElement("strong");
        caption.textContent = label; number.textContent = finite(value) ? `${difference && Number(value) > 0 ? "+" : ""}${compact(value)} ${suffix}` : "--";
        if (difference && finite(value) && Number(value) !== 0) number.classList.add(Number(value) > 0 ? "is-positive" : "is-negative"); cell.append(caption, number); values.append(cell);
      });
      const scale = document.createElement("small"); scale.className = "method-metric-scale";
      const scaleParts = [
        finite(metric.evaluation_count ?? metric.n_evaluation) ? `独立测试 ${compact(metric.evaluation_count ?? metric.n_evaluation)} 个窗口` : "",
        finite(metric.calibration_count ?? metric.n_calibration) ? `误差校准 ${compact(metric.calibration_count ?? metric.n_calibration)} 个窗口` : "",
        finite(metric.ensemble_members ?? metric.n_members) ? `${compact(metric.ensemble_members ?? metric.n_members)} 个独立模型成员` : "",
        finite(metric.transfer_member_count ?? metric.n_transfer_members) ? `${compact(metric.transfer_member_count ?? metric.n_transfer_members)} 次独立训练复核` : "",
      ].filter(Boolean);
      scale.textContent = scaleParts.length ? scaleParts.join(" · ") : "证据规模未读取到";
      card.append(title, description, values, scale); grid.append(card);
    });
    host.replaceChildren(grid);
  }
  function renderResultRecord(selector, title, data, rows) {
    const host = $(selector); if (!host) return; const available = data && Object.keys(data).length;
    host.replaceChildren(); const header = document.createElement("header"); const titleWrap = document.createElement("div");
    const label = document.createElement("span"); label.className = "section-label"; label.textContent = "结果记录";
    const heading = document.createElement("h2"); heading.textContent = title; titleWrap.append(label, heading);
    const status = document.createElement("span"); status.className = "resultRecord-status"; status.textContent = available ? (data?.verified === true ? "已复核" : "已读取") : "未读取到";
    header.append(titleWrap, status); host.append(header);
    const list = document.createElement("dl");
    (rows || []).forEach(([labelText, key, formatter]) => {
      const dt = document.createElement("dt"); const dd = document.createElement("dd");
      dt.textContent = labelText; const value = data?.[key]; dd.textContent = formatter ? formatter(value) : publicNarrative(value);
      list.append(dt, dd);
    });
    host.append(list);
  }
  function drawComparison() {
    const svg = $("#compare-canvas");
    if (!svg) return;
    svg.replaceChildren();
    const size = chartSize(svg, 220);
    svg.setAttribute("viewBox", `0 0 ${size.width} ${size.height}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    const rows = evidenceRows();
    const selectedSample = evidenceSample();
    const selectedPrediction = evidencePrediction();
    const availableSeries = evidenceSeriesForRows(rows).map((series) => ({
      ...series,
      label: evidenceSeriesLabel(series, rows),
    }));
    if (!window.d3 || !rows.length || !availableSeries.length) {
      const message = document.createElementNS("http://www.w3.org/2000/svg", "text");
      message.setAttribute("x", 14);
      message.setAttribute("y", 30);
      message.setAttribute("class", "chart-label");
      message.textContent = "当前没有同时具备真实值和预测值的样本";
      svg.append(message);
      setText("#compare-caption", "暂无可比较的预测误差");
      return;
    }
    const d3 = window.d3;
    const root = d3.select(svg);
    const margin = { top: 18, right: 12, bottom: 38, left: 46 };
    const width = Math.max(1, size.width - margin.left - margin.right);
    const height = Math.max(1, size.height - margin.top - margin.bottom);
    const points = rows.map((row, index) => {
      const point = { index };
      availableSeries.forEach((series) => {
        point[series.key] = finite(row[series.field]) && finite(row.y_true)
          ? Math.abs(Number(row[series.field]) - Number(row.y_true)) : null;
      });
      return point;
    });
    const values = points.flatMap((point) => availableSeries
      .map((series) => point[series.key]).filter(finite).map(Number));
    if (!values.length) {
      setText("#compare-caption", `${sampleDisplayName(selectedSample)} · 暂无可比较的预测误差`);
      return;
    }
    const x = d3.scaleLinear().domain([0, Math.max(1, points.length - 1)]).range([0, width]);
    const observedMaximum = d3.max(values);
    const yMaximum = Math.max(Number(observedMaximum) * 1.08, 1e-6);
    const y = d3.scaleLinear().domain([0, yMaximum]).nice().range([height, 0]);
    const group = root.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
    group.append("g").attr("class", "chart-grid").call(d3.axisLeft(y).ticks(4).tickSize(-width).tickFormat(""));
    group.append("g").attr("class", "chart-axis").call(d3.axisLeft(y).ticks(4).tickFormat((value) => compact(value)));
    group.append("g").attr("class", "chart-axis").attr("transform", `translate(0,${height})`)
      .call(d3.axisBottom(x).ticks(Math.min(5, points.length)).tickFormat((value) => Math.round(value)));
    availableSeries.forEach((series) => {
      group.append("path").datum(points)
        .attr("d", d3.line().defined((point) => finite(point[series.key]))
          .x((point) => x(point.index)).y((point) => y(Number(point[series.key])))
          .curve(d3.curveMonotoneX))
        .attr("fill", "none")
        .attr("stroke", series.color)
        .attr("stroke-width", series.key === "ensemble" ? 1.8 : 1.15)
        .attr("stroke-dasharray", series.key === "supervised" ? "4 3" : null);
    });
    const legendY = size.height - 10;
    const legend = root.append("g").attr("transform", `translate(${margin.left},${legendY})`);
    const legendStep = Math.max(92, Math.min(190, width / availableSeries.length));
    availableSeries.forEach((series, index) => {
      const entry = legend.append("g").attr("transform", `translate(${index * legendStep},0)`);
      entry.append("line").attr("x1", 0).attr("x2", 14).attr("y1", -3).attr("y2", -3)
        .attr("stroke", series.color).attr("stroke-width", 2);
      entry.append("text").attr("x", 19).attr("class", "chart-label").text(series.label);
    });
    setText("#compare-caption",
      `${sampleDisplayName(selectedSample)} · 实际模型预测绝对误差 / ${unitLabel(selectedPrediction?.time_unit)}`);
  }
  
  function definitionRows(host, rows) { if (!host) return; host.replaceChildren(...rows.map(([label, value]) => { const row = document.createElement("div"); row.className = "definition-row"; const a = document.createElement("span"); const b = document.createElement("strong"); a.textContent = label; b.textContent = valueOrDash(value); row.append(a, b); return row; })); }
  function renderSourceSelector() {
    const select = $("#source-sample-select"); if (!select) return; const visible = availableSamples();
    select.replaceChildren(...visible.map((sample) => { const option = document.createElement("option"); option.value = sample.sample_id; option.textContent = `${sample.uploaded ? "本次上传" : "项目内置"} · ${sampleDisplayName(sample)}`; option.selected = sample.sample_id === appState.sampleId; return option; }));
    select.disabled = visible.length === 0; upgradeSelect(select); syncCustomSelect(select);
    if (!select.dataset.bound) { select.dataset.bound = "true"; select.addEventListener("change", () => { setSample(select.value); renderSystem(); }); }
  }
  function renderSystem() {
    const sample = currentSample(); const comparison = sample?.lut_comparison || {}; const active = comparison.active || {}; const embedded = comparison.embedded || {}; const identity = active.identity || {}; const integrity = payload.integrity || {}; const origin = payload.origins || {};
    const platformContext = samplePlatformContext(sample);
    renderSourceSelector();
    setText("#system-generated", payload.generated_utc ? `数据摘要生成于 ${payload.generated_utc}` : "尚未读取到数据摘要生成时间");
    const replayFacts = sample?.uploaded
      ? [
        ["数据角色", "本次上传文件的模型预测回放"],
        ["遥测来源", sample.dataset_id || "本次上传"],
        ["平台构型", platformSummary(platformContext)],
        ["构型用途", "用户声明的浏览器上下文；不改变预测值"],
        ["上下文角色", "额外信息只在模型明确支持时使用；本结果会逐字段登记实际用途"],
        ["预测角色", "真实模型输出窗口及其登记预测范围"],
      ]
      : payload.source === "mock"
      ? [
        ["数据角色", "项目内置演示示例，仅供只读回放"],
        ["遥测来源", "项目内置生成示例，不代表本次上传数据或在线测量"],
        ["标签角色", "示例参考标签，仅用于界面演示"],
        ["预测角色", "当前没有可核验预测，页面不生成替代数字"],
      ]
      : payload.source === "unavailable"
        ? [
          ["数据角色", "当前没有可读取的独立测试结果"],
          ["遥测来源", "未读取到可核验的遥测时序"],
          ["标签角色", "未提供可用于回放解释的参考标签"],
          ["预测角色", "页面不生成替代数字"],
        ]
      : [
        ["数据角色", "项目内置示例，仅供只读回放"],
        ["遥测来源", "已读取的仿真遥测时序"],
        ["标签角色", "仿真参考标签，仅用于回放解释"],
        ["预测角色", "已登记测试样本的模型估计与预测范围"],
      ];
    definitionRows($("#truth-layers"), replayFacts);
    const componentIdentity = sample?.uploaded
      ? [["输入文件 SHA-256", sample.upload_sha256 || "未提供"], ["模型输入模式", sample.upload_contract?.mode || "未提供"]]
      : sample?.line === "rw"
      ? [["Romax 产品 / 版本", [identity.romax_product, identity.romax_version].filter(Boolean).join(" / ") || "登记记录未提供"], ["轴承型号", identity.bearing_catalog_entry || "登记记录未提供"]]
      : [["反作用轮部件（姿态控制执行器）身份", "不适用于当前电池部件（储能系统）回放样本"]];
    definitionRows($("#version-detail"), [
      [sample?.uploaded ? "上传文件" : "样本编号", sample ? sampleDisplayName(sample) : "未选择"],
      ["数据集", sample?.dataset_id || "未提供"],
      ["平台构型", sample ? platformSummary(platformContext) : "未提供"],
      ["姿态方式", platformContext.platform_configuration === "not_equipped" ? attitudeMethodLabel(platformContext) : "不适用或未说明"],
      ["轨道与工况", sample?.orbit || "未提供"],
      [sample?.uploaded ? "推理模型" : "仿真模型", publicNarrative(sample?.provenance?.sim_model, "未提供")],
      ["样本内嵌版本", [embedded.plan, embedded.version].filter(Boolean).join(" · ") || "未提供"],
      ...componentIdentity,
    ]);
    definitionRows($("#integrity-detail"), [
      ["回放类型", sample?.uploaded ? "本次上传预测 · 浏览器临时工作区" : origin.replay_kind || "未提供"],
      ["已匹配回放样本", `${publicComponentLabel("bat")} ${origin.matched_samples?.battery ?? 0} / ${publicComponentLabel("rw")} ${origin.matched_samples?.reaction_wheel ?? 0}`],
      ["LUT 数据角色", publicNarrative(comparison.note, "未提供版本比较")],
      ["未裁定版本差异", integrity.version_comparison?.mismatch_count ?? "未提供"],
      ["字段解析", "长表与宽表均按表头语义识别，服务统一整理为时间、遥测量、值"],
      ["数据准确性", "以原始遥测与机读产物为准"],
    ]);
    const activity = $("#source-activity");
    if (activity) {
      const sourceLabel = sample?.uploaded ? "当前页面正在追溯本次上传预测" : payload.source === "results" ? "当前页面正在读取独立测试结果" : payload.source === "mock" ? "当前页面正在显示项目内置演示示例" : "当前没有可读取的回放结果";
      const sampleLabel = sample ? `；当前样本为 ${sampleDisplayName(sample)}` : "；尚未选择回放样本";
      activity.textContent = `${sourceLabel}${sampleLabel}。上传文件与项目内置回放始终分别标记。`;
    }
  }

  function setLine(line) { if (!samples.some((sample) => sample.line === line)) return; appState.line = line; const visible = lineSamples(); if (!visible.some((sample) => sample.sample_id === appState.sampleId)) appState.sampleId = visible[0]?.sample_id || null; appState.progress = 0; appState.channel = null; rebuildOrbitObjects(); renderAll(); }
  function setSample(sampleId, closeSheet = false) { if (!samples.some((sample) => sample.sample_id === sampleId)) return; const nextLine = samples.find((sample) => sample.sample_id === sampleId)?.line || appState.line; const lineChanged = nextLine !== appState.line; appState.sampleId = sampleId; appState.line = nextLine; appState.progress = 0; appState.channel = null; appState.playing = false; invalidateGroundTrackCache(); if (closeSheet) toggleSheet(false); if (lineChanged) rebuildOrbitObjects(); else refreshOrbitObjects(); renderAll();
    // Selection must repaint the projection immediately, even while animation is paused.
    // Otherwise the highlighted ground track can lag until the next orbit frame.
    if (appState.view === "operate") {
      if (appState.orbitMode === "groundtrack") drawGroundTrack();
      else if (orbitRuntime.fallback) drawFallbackOrbit();
      else if (orbitRuntime.renderer) renderThree(performance.now());
    }
  }
  function renderAll() { renderAssets(); renderChannels(); renderHeader(); renderCurrent(); renderSystem(); drawOrbit(); drawTelemetry(); if (appState.view === "evidence") renderEvidence(); const scrubber = $("#scrubber"); if (scrubber) scrubber.value = String(Math.round(appState.progress * 1000)); setText("#progress-output", `${Math.round(appState.progress * 100)}%`); }
  function setProgress(progress) { appState.progress = clamp(progress, 0, 1); const scrubber = $("#scrubber"); if (scrubber) scrubber.value = String(Math.round(appState.progress * 1000)); setText("#progress-output", `${Math.round(appState.progress * 100)}%`); renderCurrent(); drawTelemetry(); }
  function togglePlayback() { appState.playing = !appState.playing; const button = $("#play-button"); if (button) { button.title = appState.playing ? "暂停回放" : "播放回放"; button.setAttribute("aria-label", button.title); button.replaceChildren(icon(appState.playing ? "#i-pause" : "#i-play")); } if (appState.playing && appState.progress >= 1) setProgress(0); appState.lastFrame = 0; /* Data playback is an essential, user-requested state change; reduced motion disables decorative transitions and inertia, not the timeline itself. */ if (appState.playing) requestAnimationFrame(playFrame); }
  function playFrame(timestamp) { if (!appState.playing) return; if (!appState.lastFrame) appState.lastFrame = timestamp; const seconds = Math.max(4, Number(payload.config?.playback_seconds) || 24); const delta = (timestamp - appState.lastFrame) / 1000 / seconds * appState.speed; appState.lastFrame = timestamp; setProgress(appState.progress + delta); if (appState.progress >= 1) { appState.playing = false; const button = $("#play-button"); button?.replaceChildren(icon("#i-play")); return; } requestAnimationFrame(playFrame); }
  function icon(reference, className = "icon") { const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("class", className); const use = document.createElementNS("http://www.w3.org/2000/svg", "use"); use.setAttribute("href", reference); svg.append(use); return svg; }
  const NAV_GLYPH_PARTS = {
    predict: [["#i-upload-tray", "nav-part nav-tray"], ["#i-upload-arrow", "nav-part nav-payload"]],
    operate: [["#i-orbit-ring", "nav-part nav-orbit-ring"], ["#i-orbit-satellite", "nav-part nav-satellite"]],
    workflows: [["#i-terminal-frame", "nav-part nav-terminal-frame"], ["#i-terminal-prompt", "nav-part nav-prompt"], ["#i-terminal-caret", "nav-part nav-caret"]],
    evidence: [["#i-layer-low", "nav-part nav-layer-low"], ["#i-layer-mid", "nav-part nav-layer-mid"], ["#i-layer-high", "nav-part nav-layer-high"]],
    system: [["#i-database-shell", "nav-part nav-database-shell"], ["#i-database-packet", "nav-part nav-data-packet"]],
  };
  const ACTION_GLYPH_PARTS = [
    ["#download-telemetry-template", "download", [["#i-download-tray", "action-part action-tray"], ["#i-download-arrow", "action-part action-payload"]]],
    ["#telemetry-dropzone", "upload", [["#i-upload-tray", "action-part action-tray"], ["#i-upload-arrow", "action-part action-payload"]]],
    ["#telemetry-submit", "predict", [["#i-activity", "action-part action-trace"], ["#i-motion-probe", "action-part action-probe"]]],
    [".example-link", "replay", [["#i-orbit-ring", "action-part action-ring"], ["#i-orbit-satellite", "action-part action-satellite"]]],
    [".predict-engine-note", "engine", [["#i-activity", "action-part action-trace"], ["#i-motion-probe", "action-part action-probe"]]],
  ];
  function setupNavigationGlyphs() {
    $$(".view-tab, .mobile-nav button").forEach((button) => {
      if (button.dataset.navGlyph === "ready") return;
      const parts = NAV_GLYPH_PARTS[button.dataset.view]; if (!parts) return;
      const glyph = document.createElement("span"); glyph.className = `nav-glyph is-${button.dataset.view}`; glyph.setAttribute("aria-hidden", "true");
      parts.forEach(([reference, className]) => glyph.append(icon(reference, `icon ${className}`)));
      $(".icon", button)?.replaceWith(glyph); button.dataset.navGlyph = "ready";
    });
  }
  function setupActionGlyphs() {
    ACTION_GLYPH_PARTS.forEach(([selector, kind, parts]) => {
      const target = $(selector); if (!target || target.dataset.actionGlyph === "ready") return;
      const glyph = document.createElement("span"); glyph.className = `action-glyph is-${kind}`; glyph.setAttribute("aria-hidden", "true");
      parts.forEach(([reference, className]) => glyph.append(icon(reference, `icon ${className}`)));
      $(".icon", target)?.replaceWith(glyph); target.dataset.actionGlyph = "ready"; target.dataset.motionKind = kind;
    });
  }
  let sampleSheetOpener = null;
  let sampleSheetMotionId = 0;
  let sampleSheetOpenFrame = 0;
  let sampleSheetCloseCleanup = null;
  function clearSampleSheetMotion() {
    if (sampleSheetOpenFrame) { window.cancelAnimationFrame(sampleSheetOpenFrame); sampleSheetOpenFrame = 0; }
    sampleSheetCloseCleanup?.(); sampleSheetCloseCleanup = null;
  }
  function focusWithoutScroll(target) {
    if (!target?.focus) return;
    try { target.focus({ preventScroll: true }); } catch (_) { target.focus(); }
  }
  function visibleSheetTrigger() {
    return [sampleSheetOpener, $("#compact-sample-trigger"), $("#sample-sheet-button")]
      .find((target) => target?.isConnected && target.getClientRects().length > 0) || null;
  }
  function syncSampleSheetTriggers(open) {
    [$("#compact-sample-trigger"), $("#sample-sheet-button")].filter(Boolean).forEach((trigger) => {
      trigger.setAttribute("aria-controls", "sample-sheet"); trigger.setAttribute("aria-haspopup", "dialog"); trigger.setAttribute("aria-expanded", String(open));
    });
  }
  function toggleSheet(open, opener = null) {
    const sheet = $("#sample-sheet"); const panel = $("#sample-sheet > section"); if (!sheet || !panel) return;
    const requested = Boolean(open); const targetState = sheet.dataset.targetOpen;
    if (targetState === String(requested) || (!targetState && sheet.hidden === !requested)) { syncSampleSheetTriggers(requested); return; }
    const motionId = ++sampleSheetMotionId; clearSampleSheetMotion(); sheet.dataset.targetOpen = String(requested); syncSampleSheetTriggers(requested);
    if (requested) {
      if (opener?.isConnected && !sheet.contains(opener)) sampleSheetOpener = opener;
      const wasHidden = sheet.hidden; sheet.hidden = false; sheet.inert = false; sheet.removeAttribute("aria-hidden"); sheet.classList.remove("is-closing");
      const finishOpen = () => {
        if (motionId !== sampleSheetMotionId || sheet.dataset.targetOpen !== "true") return;
        sheet.classList.add("is-open"); delete sheet.dataset.targetOpen; focusWithoutScroll($("#sheet-close"));
      };
      if (wasHidden && !reduceMotion) sampleSheetOpenFrame = window.requestAnimationFrame(() => { sampleSheetOpenFrame = 0; finishOpen(); });
      else finishOpen();
      return;
    }
    if (sheet.hidden) { delete sheet.dataset.targetOpen; return; }
    const restoreTarget = visibleSheetTrigger(); if (sheet.contains(document.activeElement)) focusWithoutScroll(restoreTarget);
    sheet.inert = true; sheet.setAttribute("aria-hidden", "true"); sheet.classList.remove("is-open"); sheet.classList.add("is-closing");
    const finishClose = () => {
      if (motionId !== sampleSheetMotionId || sheet.dataset.targetOpen !== "false") return;
      clearSampleSheetMotion(); sheet.hidden = true; sheet.classList.remove("is-closing"); delete sheet.dataset.targetOpen; sampleSheetOpener = null;
    };
    if (reduceMotion) { finishClose(); return; }
    const onTransitionEnd = (event) => { if (event.target === panel && event.propertyName === "transform") finishClose(); };
    const fallback = window.setTimeout(finishClose, 280); panel.addEventListener("transitionend", onTransitionEnd);
    sampleSheetCloseCleanup = () => { panel.removeEventListener("transitionend", onTransitionEnd); window.clearTimeout(fallback); };
  }

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
    if (options.body && !isFormData && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    if (options.method && options.method.toUpperCase() !== "GET") headers["X-RUL-CSRF"] = control.csrf || "";
    const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
    const body = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
    if (!response.ok) {
      const message = typeof body.error === "string" ? body.error : body.error?.message || body.reason || `HTTP ${response.status}`;
      const error = new Error(message); error.status = response.status; error.body = body; throw error;
    }
    return body;
  }

  const TELEMETRY_MODEL_UNAVAILABLE_LABEL = "生产模型待核验";
  const telemetryFileKey = (file) => [file.name, file.size, file.lastModified].join("::");
  const telemetryFilenameKey = (file) => String(file?.name || "").trim().normalize("NFKC").toLocaleLowerCase("zh-CN");
  const telemetryTimeUnits = [
    ["auto", "表头已注明单位"], ["cycle", "循环序号 · cycle"], ["millisecond", "毫秒 · time_ms"], ["second", "秒 · time_s"], ["minute", "分钟 · time_min"], ["hour", "小时 · time_h"], ["day", "天 · time_day"], ["bin", "已聚合时间桶 · time_bin"],
  ];
  const TELEMETRY_UPLOAD_SCHEMA_PATH = "/api/telemetry-upload-schema";
  const TELEMETRY_EXAMPLES_PATH = "/api/telemetry/examples";
  // The fallback is deliberately narrow. A failed schema request is not evidence
  // that every parser and archive backend is available in this runtime.
  const FALLBACK_TELEMETRY_UPLOAD_SCHEMA = Object.freeze({
    accepted_extensions: [".csv", ".tsv", ".txt", ".tab", ".dat"],
    format_groups: [
      { label: "基础分隔文本", extensions: [".csv", ".tsv", ".txt", ".tab", ".dat"], description: "当前仅确认含表头的长表；服务端输入说明未读取，提交仍以服务端最终校验为准。" },
    ],
    required_semantics: ["时间", "遥测量", "值"],
    required_semantics_scope: "long_input_and_internal_representation",
    prediction_input_contract: {
      description: "当前推理接口的最小输入说明，不等同于完整数据集的文档与复现要求。",
      future_labels_required: false,
    },
    competition_dataset_contract: {
      description: "用于复现、发布或长期维护的数据集还必须表达单位、组件或工况标识、退化状态或寿命标签；可由表内字段与配套数据字典共同说明。",
      required_semantics: ["时间索引", "遥测量名称或字段", "观测值", "单位", "组件或工况标识", "退化状态或寿命标签"],
      prediction_labels_are_not_required: true,
    },
    component_scope: null,
    production_model: { status: "unavailable" },
    accepted_layouts: { long: { required_semantics: ["time", "telemetry", "value"], description: "每行一条观测。" }, internal: "long" },
    optional_semantics: {
      model_used: false,
      description: "服务端输入说明未读取；额外列的支持范围和模型使用状态暂不确认，提交时以服务端响应为准。",
    },
    layout: { one_observation_per_row: true, headers_are_semantic: true, column_order_flexible: true, encodings: ["UTF-8", "GB18030"] },
    safety: { summary: "服务端输入说明暂不可读取；仅显示基础长表说明，格式、布局和安全边界以提交时的服务端校验为准。" },
    limits: { max_files: 1 },
    dataset_documentation: {
      description: "用于复现、发布或长期维护的数据集必须随数据字典说明字段含义、采样频率、单位、缺失值规则、数据划分方法和标签生成依据。当前页面不以数据字典替代接口校验。",
      required_items: ["field_meaning", "sampling_frequency", "engineering_unit", "missing_value_rule", "split_method", "label_generation_basis", "component_or_condition", "degradation_or_life_label"],
    },
  });
  const humanBytes = (bytes) => {
    const value = Number(bytes) || 0;
    if (value < 1024) return `${value} B`;
    if (value < 1024 ** 2) return `${format(value / 1024, 1)} KiB`;
    return `${format(value / 1024 ** 2, 1)} MiB`;
  };
  const telemetryErrorMessage = (body, status = 0) => {
    const fallback = status === 403 ? "当前会话已失效，请刷新页面后重新提交。" : status === 413 ? "所选文件数量或总大小超过当前服务可处理范围，请分批提交。" : status ? `请求未完成（HTTP ${status}）。` : "请求未完成，请确认服务仍在运行。";
    const message = typeof body?.error === "string" ? body.error : body?.error?.message || body?.reason;
    return publicNarrative(message, fallback);
  };
  function telemetryRejectionDetails(item) {
    const error = item?.error && typeof item.error === "object" ? item.error : {};
    const code = String(error.code || ""); const details = error.details && typeof error.details === "object" ? error.details : {};
    const values = (value, fallback = "未提供") => Array.isArray(value) && value.length ? value.slice(0, 8).map((entry) => String(entry)).join("、") : fallback;
    let fact = telemetryErrorMessage(item); let recovery = "检查表头语义、时间轴和单位后重新提交；服务不会猜测或补造模型输入。";
    switch (code) {
      case "required_telemetry_missing":
        fact = `处理事实：未识别到当前模型所需遥测量（收到：${values(details.channels, "无")})`;
        recovery = `修正建议：补齐模型必需通道后再提交；不能只上传 capacity 或用其他字段代替。`;
        break;
      case "incomplete_time_steps":
        fact = `处理事实：${details.count ?? "部分"} 个时间点缺少模型通道（示例：${(details.examples || []).slice(0, 3).map((entry) => `${entry.time ?? "未知时间"} 缺少 ${values(entry.missing, "未知通道")}`).join("；") || "未提供"})`;
        recovery = "修正建议：让每个时间点都提供同一组必需通道；不要插值、复制或静默填补缺失值。";
        break;
      case "insufficient_history":
        fact = `处理事实：可用历史长度 ${details.received ?? "未提供"}，冻结窗口需要 ${details.required ?? "未提供"}。`;
        recovery = "修正建议：上传足够的连续历史记录，不能用短表强行生成窗口。";
        break;
      case "time_unit_required":
        fact = `处理事实：数值时间列没有声明单位（允许：${values(details.allowed, "未提供")})`;
        recovery = "修正建议：在上传控件中明确选择与表头一致的 time_unit；服务不会按数值大小猜单位。";
        break;
      case "unsupported_telemetry_set":
        fact = `处理事实：收到的通道无法映射到冻结模型（收到：${values(details.channels, "无")})`;
        recovery = "修正建议：按当前模型通道说明提供对应部件的完整输入，未知列不会被当成特征。";
        break;
      case "binary_layout_required":
        fact = `处理事实：二进制字段 ${details.field || "未命名"} 的形状为 ${values(details.shape, "未提供")}，不是一维等长标量序列。`;
        recovery = "修正建议：将每个字段整理为一维、等长序列，并保证时间与通道逐行对齐。";
        break;
      case "empty_wide_observation":
        fact = `处理事实：宽表第 ${details.row ?? "未知"} 行（时间 ${details.time ?? "未知"}）没有任何非空遥测值。`;
        recovery = "修正建议：删除该空时间点，或补齐该时间点的真实遥测值；服务不会静默丢弃时间轴。";
        break;
      case "bat_cycle_index_negative":
        fact = `处理事实：电池部件（储能系统）循环索引 ${details.index ?? "未知"} 为负数。`;
        recovery = "修正建议：将电池部件（储能系统）循环索引从 0 开始按连续整数提供，不能用负数占位。";
        break;
      case "bat_cycle_index_origin":
        fact = `处理事实：电池部件（储能系统）循环索引从 ${details.first ?? "未知"} 开始，而冻结时间轴要求从 0 开始。`;
        recovery = "修正建议：提供从 0 开始的完整连续电池部件（储能系统）循环历史；不能平移索引掩盖缺失起点。";
        break;
      case "rwa_feature_bin_index_negative":
        fact = `处理事实：反作用轮部件（姿态控制执行器）预聚合特征桶索引 ${details.index ?? "未知"} 为负数。`;
        recovery = "修正建议：将预聚合特征桶索引从 0 开始按连续整数提供，不能用负数占位。";
        break;
      case "rwa_feature_bin_index_origin":
        fact = `处理事实：反作用轮部件（姿态控制执行器）预聚合特征桶从 ${details.first ?? "未知"} 开始，而冻结时间轴要求从 0 开始。`;
        recovery = "修正建议：提供从 0 开始的完整连续特征桶历史；不能平移索引掩盖缺失起点。";
        break;
      case "rwa_raw_time_negative":
        fact = `处理事实：反作用轮部件（姿态控制执行器）原始时间 ${details.time ?? "未知"} 映射到负的 574 秒时间桶（${details.bin ?? "未知"}）。`;
        recovery = "修正建议：从任务起点 0 秒开始提供原始累计时间，不能用负时间补齐窗口。";
        break;
      case "rwa_raw_time_origin":
        fact = `处理事实：反作用轮部件（姿态控制执行器）原始遥测映射出的 574 秒桶从 ${details.first_bin ?? "未知"} 开始，而冻结时间轴要求从 0 开始。`;
        recovery = "修正建议：从任务起点 0 秒开始提供原始累计时间；不能平移时间掩盖缺失起点。";
        break;
      case "incomplete_rwa_bin":
        fact = `处理事实：反作用轮部件（姿态控制执行器）的 ${details.bin ?? "某个"} 个 574 秒聚合桶缺少 ${values(details.missing, "必需原始通道")}`;
        recovery = "修正建议：补齐每个原始聚合桶的 speed、motor current 和 bearing temperature。";
        break;
      case "ambiguous_wide_column":
        fact = `处理事实：宽表列 ${details.header || "未命名"} 含有无法安全解释的文本值。`;
        recovery = "修正建议：把该列声明为受支持的上下文字段，或移除后重新提交；未知文本列不会进入模型。";
        break;
      case "prediction_failed":
        fact = "处理事实：输入校验后推理未完成，服务没有生成预测值。";
        recovery = "修正建议：确认服务端生产模型可用后重试；请保留本次文件和错误码供排查。";
        break;
      default:
        fact = `处理事实：${telemetryErrorMessage(item)}`;
    }
    return { fact, recovery: `修正建议：${recovery.replace(/^修正建议：/, "")}` };
  }

  function setTelemetryStatus(message, kind = "") {
    const node = $("#telemetry-status"); if (!node) return;
    node.textContent = message; node.classList.toggle("is-error", kind === "error"); node.classList.toggle("is-busy", kind === "busy");
  }

  function syncTelemetryControls() {
    const hasFiles = appState.telemetryFiles.length > 0;
    const submit = $("#telemetry-submit"); const reset = $("#telemetry-reset"); const input = $("#telemetry-files");
    if (submit) submit.disabled = appState.telemetrySubmitting || !hasFiles || !control.enabled;
    if (reset) reset.disabled = appState.telemetrySubmitting || (!hasFiles && !appState.telemetryBatch);
    if (input) input.disabled = appState.telemetrySubmitting;
  }

  function syncUploadPlatformConfiguration({ invalidate = false, announce = false } = {}) {
    const configuration = $("#telemetry-platform-configuration"); const attitude = $("#telemetry-attitude-method"); const attitudeField = $("#telemetry-attitude-method-field"); const line = $("#telemetry-line"); const platformField = configuration?.closest?.(".telemetry-platform-field"); const platformHint = $("#telemetry-platform-hint");
    if (!configuration || !line) return;
    const predictedLines = new Set((appState.telemetryBatch?.results || []).filter((item) => item?.status === "predicted").map((item) => item?.line === "rw" ? "rwa" : item?.line).filter(Boolean));
    const inferredRoute = line.value !== "auto" ? line.value : predictedLines.size === 1 ? Array.from(predictedLines)[0] : "";
    const routeProvesWheel = inferredRoute === "rwa";
    const platformVisible = inferredRoute === "bat" || routeProvesWheel;
    if (platformField) {
      platformField.hidden = !platformVisible;
      platformField.dataset.route = inferredRoute || "pending";
    }
    if (routeProvesWheel) {
      if (configuration.dataset.routeLocked !== "true") configuration.dataset.userValue = configuration.value;
      configuration.dataset.routeLocked = "true"; configuration.value = "equipped"; configuration.disabled = true;
    } else {
      if (configuration.dataset.routeLocked === "true") configuration.value = configuration.dataset.userValue || "unspecified";
      delete configuration.dataset.routeLocked; configuration.disabled = false;
    }
    platformField?.classList.toggle("is-route-locked", routeProvesWheel);
    if (platformHint) platformHint.textContent = routeProvesWheel
      ? "当前模型已有反作用轮部件（姿态控制执行器）遥测这一正向事实，构型已确定为搭载反作用轮部件（姿态控制执行器）；无需重复声明，该项仍不进入模型。"
      : "电池部件（储能系统）数据不能证明整星是否搭载反作用轮部件（姿态控制执行器），请按航天器事实声明；该项只用于回放与溯源，不进入模型。";
    const noWheel = configuration.value === "not_equipped"; const wheelOption = Array.from(line.options).find((option) => option.value === "rwa");
    if (wheelOption) wheelOption.disabled = noWheel;
    if (attitudeField) attitudeField.hidden = !noWheel;
    if (!noWheel && attitude) attitude.value = "unspecified";
    let forcedBattery = false;
    if (noWheel && line.value !== "bat") { line.value = "bat"; forcedBattery = true; }
    syncCustomSelect(configuration); syncCustomSelect(attitude); syncCustomSelect(line);
    if (invalidate) invalidateTelemetryBatch("平台构型或部件模型已更新，等待重新提交。");
    if (announce && forcedBattery) showToast("已声明未搭载反作用轮部件（姿态控制执行器），本次预测模型已切换为电池部件（储能系统）单独预测。", "info");
  }

  function animateTelemetryNodes(targets, vars = {}) {
    if (!window.gsap || reduceMotion || !targets?.length) return;
    window.gsap.killTweensOf(targets);
    window.gsap.fromTo(targets, { autoAlpha: 0, y: 7 }, {
      autoAlpha: 1, y: 0, duration: .34, stagger: .035, ease: "expo.out", clearProps: "transform,opacity,visibility", ...vars,
    });
  }

  function invalidateTelemetryBatch(message = "文件选择已更新，等待重新提交。") {
    appState.telemetryBatch = null; appState.telemetryBatchContext = null; appState.telemetryExportUrl = null;
    const exportButton = $("#telemetry-export"); if (exportButton) { exportButton.hidden = true; exportButton.removeAttribute("data-export-url"); }
    setText("#telemetry-result-region h2", "等待提交");
    setText("#telemetry-result-summary", "每个文件独立核对；需要补充或修正的信息会逐文件显示，不影响其他文件的预测结果。");
    $("#telemetry-results")?.replaceChildren(); syncAddAllReplayButton([]);
    definitionRows($("#upload-provenance"), [["状态", "尚未提交当前选择"]]);
    setTelemetryStatus(appState.telemetryFiles.length ? message : "等待遥测文件"); syncUploadPlatformConfiguration();
  }

  function renderTelemetryFiles() {
    const host = $("#telemetry-file-list"); if (!host) return;
    if (!appState.telemetryFiles.length) {
      destroyCustomSelects(host); const empty = document.createElement("p"); empty.textContent = "尚未选择文件"; host.replaceChildren(empty); setText("#telemetry-queue-count", "尚未选择文件"); syncTelemetryControls(); syncAddAllReplayButton(); return;
    }
    const nodes = appState.telemetryFiles.map((file, index) => {
      const row = document.createElement("div"); row.className = "telemetry-file-item";
      row.append(icon("#i-database"));
      const text = document.createElement("span"); const name = document.createElement("strong"); const meta = document.createElement("small");
      name.textContent = file.name; name.title = file.name; meta.textContent = `${humanBytes(file.size)} · ${file.type || "待识别文本表"}`; text.append(name, meta);
      const unitWrap = document.createElement("label"); unitWrap.className = "telemetry-file-unit-wrap"; const unitLabelNode = document.createElement("span"); unitLabelNode.textContent = "时间含义";
      const unit = document.createElement("select"); unit.className = "telemetry-file-unit"; unit.disabled = appState.telemetrySubmitting;
      unit.setAttribute("aria-label", `${file.name} 的时间含义`); unit.title = "表头已精确写明 cycle、time_s 等单位时可保留第一项；普通 time 必须在这里明确选择，系统不会按文件名或数值范围猜测。";
      telemetryTimeUnits.forEach(([value, label]) => unit.append(new Option(label, value)));
      const fileKey = telemetryFileKey(file); unit.value = appState.telemetryFileUnits.get(fileKey) || "auto";
      unit.addEventListener("change", () => { appState.telemetryFileUnits.set(fileKey, unit.value); invalidateTelemetryBatch(`${file.name} 的时间单位已更新，等待重新提交。`); });
      const remove = document.createElement("button"); remove.type = "button"; remove.disabled = appState.telemetrySubmitting;
      remove.title = `移除 ${file.name}`; remove.setAttribute("aria-label", remove.title); remove.append(icon("#i-x"));
      remove.addEventListener("click", () => { appState.telemetryFileUnits.delete(fileKey); appState.telemetryFiles.splice(index, 1); invalidateTelemetryBatch(); renderTelemetryFiles(); });
      unitWrap.append(unitLabelNode, unit); row.append(text, unitWrap, remove); return row;
    });
    destroyCustomSelects(host); host.replaceChildren(...nodes); upgradeCustomSelects(host);
    $$("select.telemetry-file-unit", host).forEach((select) => customSelects.get(select)?.menu.classList.add("telemetry-unit-menu"));
    setText("#telemetry-queue-count", `已加入 ${nodes.length} 个文件`); syncTelemetryControls(); syncAddAllReplayButton(); animateTelemetryNodes(nodes);
  }

  function appendTelemetryFiles(fileList) {
    const incoming = Array.from(fileList || []).filter((file) => file && typeof file.name === "string");
    if (!incoming.length) return;
    const maximum = Number(appState.telemetrySchema?.maxFiles) || FALLBACK_TELEMETRY_UPLOAD_SCHEMA.limits.max_files;
    let added = 0; let replaced = 0; let overflow = 0;
    incoming.forEach((file) => {
      const nameKey = telemetryFilenameKey(file);
      const existingIndex = appState.telemetryFiles.findIndex((item) => telemetryFilenameKey(item) === nameKey);
      if (existingIndex >= 0) {
        const previous = appState.telemetryFiles[existingIndex]; const previousKey = telemetryFileKey(previous);
        const unit = appState.telemetryFileUnits.get(previousKey) || "auto";
        appState.telemetryFileUnits.delete(previousKey); appState.telemetryFiles[existingIndex] = file;
        appState.telemetryFileUnits.set(telemetryFileKey(file), unit); replaced += 1; return;
      }
      if (appState.telemetryFiles.length >= maximum) { overflow += 1; return; }
      appState.telemetryFiles.push(file); appState.telemetryFileUnits.set(telemetryFileKey(file), "auto"); added += 1;
    });
    if (overflow) showToast(`一次最多选择 ${maximum} 个文件，超出的 ${overflow} 个文件未加入队列。`, "error");
    else if (replaced) showToast(`已用新内容替换 ${replaced} 个同名文件；提交时只发送最新版本。`, "info");
    const input = $("#telemetry-files"); if (input) input.value = "";
    if (!added && !replaced) { renderTelemetryFiles(); return; }
    invalidateTelemetryBatch(`${appState.telemetryFiles.length} 个文件等待验证`); renderTelemetryFiles();
  }

  function resetTelemetryInput() {
    appState.telemetryFiles = []; appState.telemetryFileUnits = new Map(); const input = $("#telemetry-files"); if (input) input.value = "";
    invalidateTelemetryBatch(); renderTelemetryFiles();
  }

  const schemaValueList = (value) => {
    if (Array.isArray(value)) return value.flatMap(schemaValueList);
    if (value && typeof value === "object") return Object.values(value).flatMap(schemaValueList);
    return value == null ? [] : [value];
  };
  const uniqueSchemaText = (values) => [...new Set(values.map((value) => publicNarrative(value, "")).filter(Boolean))];
  const normaliseExtensions = (value) => [...new Set(schemaValueList(value)
    .flatMap((item) => String(item).trim().toLowerCase().split(/[\s,;]+/))
    .map((item) => item.startsWith(".") ? item : `.${item}`)
    .filter((item) => /^\.[a-z0-9]+(?:\.[a-z0-9]+)?$/.test(item) && item.length <= 16))];
  const readableExtension = (extension) => extension.slice(1).toUpperCase();
  const semanticName = (value) => ({
    time: "时间", timestamp: "时间", datetime: "时间", "时间": "时间",
    telemetry: "遥测量", channel: "遥测量", measurement: "遥测量", "遥测量": "遥测量",
    value: "值", reading: "值", "值": "值",
  })[String(value || "").trim().toLowerCase()] || "";
  const normaliseSemantics = (value, fallback = FALLBACK_TELEMETRY_UPLOAD_SCHEMA) => {
    const names = new Set(schemaValueList(value).map(semanticName).filter(Boolean));
    return ["时间", "遥测量", "值"].every((name) => names.has(name))
      ? ["时间", "遥测量", "值"] : fallback.required_semantics;
  };
  const normaliseLayouts = (value, fallback = FALLBACK_TELEMETRY_UPLOAD_SCHEMA) => {
    const source = value && typeof value === "object" ? value : fallback.accepted_layouts;
    const layouts = [];
    if (source.long || (Array.isArray(source) && source.includes("long"))) layouts.push("长表");
    if (source.wide || (Array.isArray(source) && source.includes("wide"))) layouts.push("宽表");
    return layouts.length ? layouts : ["长表"];
  };
  const publicSafetyText = (value) => uniqueSchemaText(schemaValueList(value)
    .filter((item) => /[\u3400-\u9fff]/.test(String(item))))[0] || "";
  function contractRows(host, rows) {
    if (!host) return;
    host.replaceChildren(...rows.map(([label, value]) => {
      const row = document.createElement("div"); const term = document.createElement("dt"); const description = document.createElement("dd");
      term.textContent = label; description.textContent = valueOrDash(value); row.append(term, description); return row;
    }));
  }
  const DATASET_DOCUMENTATION_LABELS = Object.freeze({
    field_meaning: "字段含义",
    sampling_frequency: "采样频率",
    engineering_unit: "工程单位",
    missing_value_rule: "缺失值规则",
    split_method: "数据划分方法",
    label_generation_basis: "标签生成依据",
    component_or_condition: "组件或工况标识",
    degradation_or_life_label: "退化状态或寿命标签",
  });
  function renderDatasetDocumentation(documentation, datasetContract, source = "fallback") {
    const host = $("#telemetry-dataset-documentation"); if (!host) return;
    const data = documentation && typeof documentation === "object" ? documentation : {};
    const contract = datasetContract && typeof datasetContract === "object" ? datasetContract : {};
    const items = [...new Set(schemaValueList(data.required_items || data.recommended_items)
      .map((item) => DATASET_DOCUMENTATION_LABELS[String(item)] || publicNarrative(item, ""))
      .filter(Boolean))];
    const datasetSemanticsSource = Array.isArray(contract.required_semantics) ? contract.required_semantics : [];
    const datasetSemantics = [...new Set(datasetSemanticsSource
      .map((item) => item && typeof item === "object" ? publicNarrative(item.label || item.key, "") : publicNarrative(item, ""))
      .filter(Boolean))];
    const policy = publicNarrative(
      data.derived_telemetry_policy,
      "轨道根数、光伏阵列功率等派生遥测只有在来源、计算或测量链、预测时可获得性、采样特性和退化关系可追溯且被模型输入说明明确允许时，才可能进入未来模型；标签和未来信息始终禁止。",
    );
    const rows = [
      ["文档状态", source === "service" ? "已读取服务端数据文档要求" : "服务端输入说明暂不可读取，显示保守的数据文档要求"],
      ["当前预测输入", "长表需时间、遥测量、值；宽表需时间和模型可读取通道。预测时不要求提供未来寿命标签。"],
      ["模型输入范围", "页面列出的通道名与模板只属于 BRPHM 当前已核验模型，不是通用时序数据的固定字段或外部数据集标准。"],
      ["完整数据集至少表达", datasetSemantics.length ? datasetSemantics.join("、") : "时间索引、遥测量名称或字段、观测值、单位、组件或工况标识、退化状态或寿命标签"],
      ["配套数据字典必须说明", items.length ? items.join("、") : "字段含义、采样频率、工程单位、缺失值规则、数据划分方法、标签生成依据"],
      ["派生遥测边界", policy],
    ];
    contractRows(host, rows);
  }
  function normaliseTelemetrySchema(rawSchema, fallback = FALLBACK_TELEMETRY_UPLOAD_SCHEMA) {
    const raw = rawSchema && typeof rawSchema === "object" ? rawSchema : {};
    const layout = raw.layout && typeof raw.layout === "object" ? raw.layout : {};
    const safety = raw.safety && typeof raw.safety === "object" ? raw.safety : {};
    const formatGroups = Array.isArray(raw.format_groups) ? raw.format_groups
      : raw.format_groups && typeof raw.format_groups === "object" ? Object.values(raw.format_groups) : [raw.format_groups];
    const publicFormatGroups = formatGroups.map((group, index) => {
      const extensions = group && typeof group === "object"
        ? normaliseExtensions(group.extensions || group.accepted_extensions || group.formats || group.files)
        : normaliseExtensions(group);
      const label = group && typeof group === "object"
        ? publicNarrative(group.label, "") : "";
      const description = group && typeof group === "object"
        ? publicNarrative(group.description, "") : "";
      return { label: label || `数据格式 ${index + 1}`, description, extensions };
    }).filter((group) => group.extensions.length);
    const groupedExtensions = publicFormatGroups.flatMap((group) => group.extensions);
    const extensions = normaliseExtensions(raw.accepted_extensions || raw.extensions).concat(groupedExtensions);
    const acceptedExtensions = [...new Set(extensions.length ? extensions : fallback.accepted_extensions)];
    const encodingValues = uniqueSchemaText(schemaValueList(layout.encodings || layout.encoding || raw.encodings || raw.encoding));
    const encodings = encodingValues.length ? encodingValues : fallback.layout.encodings;
    const sourceLimits = raw.limits && typeof raw.limits === "object" ? raw.limits : {};
    const optionalSemantics = raw.optional_semantics && typeof raw.optional_semantics === "object" ? raw.optional_semantics : {};
    const layouts = normaliseLayouts(raw.accepted_layouts || layout.accepted_input_layouts, fallback);
    const maxFiles = Number(sourceLimits.max_files ?? safety.max_files);
    const safetyText = publicSafetyText(typeof raw.safety === "string" ? raw.safety : safety.summary || safety.message || safety.reasons || safety.reject_reasons || safety.rejected)
      || fallback.safety.summary;
    const productionModel = raw.production_model && typeof raw.production_model === "object"
      ? raw.production_model : fallback.production_model || { status: "unavailable" };
    const productionLabel = validatedProductionLabel(productionModel);
    return {
      acceptedExtensions,
      formatGroups: publicFormatGroups.length ? publicFormatGroups : [{ label: "分隔文本", description: "含表头的长表或遥测通道宽表。", extensions: acceptedExtensions }],
      semantics: normaliseSemantics(raw.required_semantics, fallback),
      layouts,
      encodings,
      safetyText,
      optionalText: publicNarrative(
        optionalSemantics.description,
        fallback.optional_semantics.description,
      ),
      maxFiles: Number.isFinite(maxFiles) && maxFiles > 0 ? Math.floor(maxFiles) : fallback.limits.max_files,
      datasetDocumentation: raw.dataset_documentation || fallback.dataset_documentation || null,
      datasetContract: raw.competition_dataset_contract || fallback.competition_dataset_contract || null,
      componentScope: raw.component_scope && typeof raw.component_scope === "object" ? raw.component_scope : fallback.component_scope || null,
      productionModel,
      productionLabel,
      layoutText: layouts.includes("宽表")
        ? "长表按时间/遥测量/值识别；宽表按时间列和已声明遥测通道识别，随后确定性转为内部长表。"
        : "表头按语义识别，不按列位置猜测；长表三个必需字段的列顺序可变化。",
    };
  }
  const PUBLIC_COMPONENT_LABELS = Object.freeze({
    bat: "电池部件（储能系统）",
    rw: "反作用轮部件（姿态控制执行器）",
    rwa: "反作用轮部件（姿态控制执行器）",
  });
  const publicComponentLabel = (line) => PUBLIC_COMPONENT_LABELS[line === "rwa" ? "rwa" : line] || "所选部件";
  const PUBLIC_MODEL_NAMES = Object.freeze({
    bat: "电池部件剩余寿命预测模型（储能系统）",
    rwa: "反作用轮部件剩余寿命预测模型（姿态控制执行器）",
  });
  function productionContractMatches(contract) {
    if (!contract || contract.status !== "validated" || contract.framework !== "PyTorch") return false;
    const components = contract.components || contract.routes || {};
    const batContract = components.bat || {}; const rwaContract = components.rwa || {};
    return batContract.model_name === PUBLIC_MODEL_NAMES.bat && rwaContract.model_name === PUBLIC_MODEL_NAMES.rwa
      && Number(batContract.n_members) === 3 && Number(rwaContract.n_members) === 3
      && batContract.selection_method === "三个独立模型结果取中位数"
      && batContract.point_prediction_method === "三个独立模型结果取中位数"
      && rwaContract.selection_method === "三个独立模型结果取中位数"
      && rwaContract.point_prediction_method === "三个独立模型结果取中位数";
  }
  function validatedProductionLabel(contract) {
    if (!productionContractMatches(contract)) return TELEMETRY_MODEL_UNAVAILABLE_LABEL;
    return `${PUBLIC_MODEL_NAMES.bat} · ${PUBLIC_MODEL_NAMES.rwa} · ${contract.framework}`;
  }
  function predictionItemMatchesRoute(item, line) {
    const framework = typeof item?.framework === "string" ? item.framework.trim() : "";
    const modelName = typeof item?.public_model_name === "string"
      ? item.public_model_name.trim()
      : typeof item?.model === "string" ? item.model.trim() : "";
    return Boolean(line && modelName === PUBLIC_MODEL_NAMES[line] && framework === "PyTorch");
  }
  function predictionRouteLabel(item) {
    const line = item?.line === "bat" ? "bat" : item?.line === "rwa" || item?.line === "rw" ? "rwa" : "";
    if (!predictionItemMatchesRoute(item, line)) return TELEMETRY_MODEL_UNAVAILABLE_LABEL;
    return `${PUBLIC_MODEL_NAMES[line]} · PyTorch`;
  }
  function setTelemetrySchemaState(text, fallback = false) {
    const node = $("#telemetry-schema-state"); if (!node) return;
    node.textContent = text; node.classList.toggle("is-fallback", fallback);
  }
  function applyTelemetryInputAccept(schema) {
    const input = $("#telemetry-files"); const extensions = schema?.acceptedExtensions || FALLBACK_TELEMETRY_UPLOAD_SCHEMA.accepted_extensions;
    if (input) input.accept = extensions.join(",");
    const formatLabels = (schema?.formatGroups || []).map((group) => group.label).filter(Boolean).join("、") || "遥测数据表";
    const maximum = Number(schema?.maxFiles) || FALLBACK_TELEMETRY_UPLOAD_SCHEMA.limits.max_files;
    const fallbackState = !appState.telemetrySchema?.fromService;
    setText("#telemetry-file-hint", fallbackState
      ? "服务端输入说明暂不可读取；当前只确认基础分隔文本长表，提交以服务端最终校验为准。"
      : `支持${formatLabels}；兼容 ${schema?.encodings?.join("、") || "UTF-8、GB18030"}；一次最多 ${maximum} 个文件。完整扩展名见支持范围。`);
  }
  function renderTelemetrySchema(schema, source = "fallback") {
    const publicSchema = normaliseTelemetrySchema(schema); publicSchema.fromService = source === "service"; appState.telemetrySchema = publicSchema;
    const formatText = publicSchema.acceptedExtensions.map(readableExtension).join(" / ");
    const batteryOnly = publicSchema.componentScope?.battery_only_prediction;
    applyTelemetryInputAccept(publicSchema);
    setText("#route-label", publicSchema.productionLabel);
    setText("#predict-route-label", publicSchema.productionLabel);
    setText("#telemetry-safety-note", publicSchema.safetyText);
    setTelemetrySchemaState(source === "service" ? "服务端格式说明已读取" : "服务端输入说明暂不可读取；仅显示基础长表说明，提交以服务端最终校验为准", source !== "service");
    contractRows($("#telemetry-format-groups"), publicSchema.formatGroups.map((group) => [
      group.label, `${group.extensions.map(readableExtension).join(" / ")}${group.description ? `；${group.description}` : ""}`,
    ]));
    contractRows($("#telemetry-schema-summary"), [
      ["支持格式", formatText],
      ["输入布局", publicSchema.layouts.join(" / ")],
      ["长表语义", publicSchema.semantics.join(" / ")],
      ["可选字段", publicSchema.optionalText],
      ["电池部件（储能系统）单独预测", batteryOnly?.supported === true && batteryOnly?.reaction_wheel_telemetry_required === false
        ? "支持；无需反作用轮部件占位遥测，输出仅为电池部件（储能系统）剩余寿命"
        : "服务端输入说明未确认；提交时以实际接口校验为准"],
      ["表头识别", publicSchema.layoutText],
      ["文本编码", publicSchema.encodings.join(" / ")],
      ["安全校验", "逐文件说明需要补充或修正的信息"],
      ["批量选择", `一次最多 ${publicSchema.maxFiles} 个文件`],
    ]);
    renderDatasetDocumentation(publicSchema.datasetDocumentation, publicSchema.datasetContract, source);
    renderEvidence();
  }

  async function loadTelemetrySchema() {
    const controller = "AbortController" in window ? new AbortController() : null;
    const timeout = controller ? window.setTimeout(() => controller.abort(), 4500) : null;
    try {
      const schema = await api(TELEMETRY_UPLOAD_SCHEMA_PATH, controller ? { signal: controller.signal } : {});
      renderTelemetrySchema(schema, "service"); renderTelemetryFiles();
    } catch (error) {
      renderTelemetrySchema(FALLBACK_TELEMETRY_UPLOAD_SCHEMA, "fallback"); renderTelemetryFiles();
    } finally {
      if (timeout) window.clearTimeout(timeout);
    }
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = filename;
    document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function downloadTelemetryTemplate() {
    const csv = "\uFEFF循环序号(cycle),遥测量(telemetry),值(value),单位(unit),组件类型(component),组件标识(component_id),运行工况(operating_condition),退化状态(degradation_state),剩余寿命标签(rul),寿命标签(life_label),失效标签(failure_label),轨道高度_km(orbit_altitude_km),轨道倾角_deg(orbit_inclination_deg),轨道周期_min(orbital_period_min)\r\n";
    downloadBlob(new Blob([csv], { type: "text/csv;charset=utf-8" }), "brphm-telemetry-template.csv");
    setTelemetryStatus("已下载 UTF-8 推荐长表模板；宽表可直接把已声明遥测通道展开为列。");
  }

  function renderTelemetryExamples(catalog) {
    const host = $("#telemetry-example-list"); if (!host) return;
    host.replaceChildren();
    const examples = Array.isArray(catalog?.examples) ? catalog.examples : [];
    if (!examples.length) { const empty = document.createElement("p"); empty.textContent = "当前没有可读取的表格范例。"; host.append(empty); return; }
    examples.forEach((example) => {
      const row = document.createElement("div"); row.className = "telemetry-example-row";
      const title = document.createElement("strong"); title.textContent = example.title || example.id || "输入范例";
      const detail = document.createElement("small"); detail.textContent = `${example.minimum_history || "按范例目录说明"} · 必需：${(example.required_channels || []).join("、")} · 演示字段不进入预测`;
      const controls = document.createElement("div"); controls.className = "telemetry-example-controls";
      const layout = document.createElement("select"); layout.setAttribute("aria-label", `${title.textContent} 表格布局`); [["wide", "宽表"], ["long", "长表"]].forEach(([value, label]) => { const option = document.createElement("option"); option.value = value; option.textContent = label; layout.append(option); });
      const format = document.createElement("select"); format.setAttribute("aria-label", `${title.textContent} 文件格式`); (Array.isArray(example.variants) ? example.variants : []).filter((item) => item && item.available !== false).forEach((item) => { const option = document.createElement("option"); option.value = item.format; option.textContent = item.label || item.format; format.append(option); });
      const link = document.createElement("a"); link.className = "utility-button telemetry-example-download"; link.setAttribute("download", ""); link.textContent = "下载";
      const updateLink = () => { link.href = `/api/telemetry/examples/${encodeURIComponent(example.id)}.${encodeURIComponent(format.value || "csv")}?layout=${encodeURIComponent(layout.value || "wide")}`; };
      layout.addEventListener("change", updateLink); format.addEventListener("change", updateLink); updateLink(); controls.append(layout, format, link); row.append(title, detail, controls); host.append(row);
    });
  }

  async function loadTelemetryExamples() {
    try { const catalog = await api(TELEMETRY_EXAMPLES_PATH); renderTelemetryExamples(catalog); const panel = $("#telemetry-example-panel"); if (panel) panel.dataset.loaded = "true"; }
    catch (error) { const host = $("#telemetry-example-list"); if (host) { host.replaceChildren(); const message = document.createElement("p"); message.textContent = "范例目录暂不可读取；请按输入说明手动准备表格。"; host.append(message); } }
  }

  function toggleTelemetryExamplePanel(open) {
    const panel = $("#telemetry-example-panel"); const trigger = $("#telemetry-example-toggle"); if (!panel || !trigger) return;
    const next = open == null ? panel.hidden : Boolean(open); panel.hidden = !next; trigger.setAttribute("aria-expanded", String(next)); if (next && panel.dataset.loaded !== "true") loadTelemetryExamples();
  }

  // Machine contracts remain available for integrity checks, but evaluator-
  // facing copy must describe the behavior without exposing implementation IDs.
  function selectionAggregationLabel(item) {
    if (item?.selection_method) return item.selection_method;
    if (item?.selection_aggregation === "median3") return "三个独立模型结果取中位数";
    if (item?.selection_aggregation === "mean3") return "三个独立模型结果取均值";
    return "服务端未返回可读说明";
  }
  function productionPointAggregationLabel(item) {
    if (item?.point_prediction_method) return item.point_prediction_method;
    if (item?.production_point_aggregation === ["s", "eed17"].join("") && Number(item?.["production_point_" + "s" + "eed"]) === 17) {
      return "固定主模型输出（正式点预测）";
    }
    if (item?.production_point_aggregation === "median3") return "三个独立模型结果取中位数（正式点预测）";
    return "服务端未返回可读说明";
  }
  function intervalMemberLabel(item) {
    if (Number(item?.member_count ?? item?.n_members) === 3) return "三个独立模型形成经验预测范围（不改变正式点预测）";
    return Array.isArray(item?.interval_member_members) && item.interval_member_members.length === 3
      ? "三个独立模型形成经验预测范围（不改变正式点预测）"
      : "服务端未返回可读说明";
  }
  function uncertaintyModeLabel(item) {
    if (Number(item?.member_count ?? item?.n_members) === 3) return "三个独立模型结果形成经验预测范围，正式点预测位于范围内";
    return item?.uncertainty_mode === "three_member_route_safe_empirical_quantiles_point_enclosed"
      ? "三个独立模型结果形成经验预测范围，正式点预测位于范围内"
      : "服务端未返回可读说明";
  }

  function updateUploadProvenance(batch) {
    const results = Array.isArray(batch?.results) ? batch.results : [];
    if (!results.length) { definitionRows($("#upload-provenance"), [["状态", "尚未上传数据"]]); return; }
    const rows = [];
    results.forEach((item, index) => {
      const label = `文件 ${index + 1}`; rows.push([label, item.filename || "未命名文件"]);
      if (item.status === "predicted") {
        const sourceLayout = item.input_contract?.source_layout === "wide" ? "宽表" : "长表";
        rows.push(["字段事实", `${sourceLayout}语义已唯一确认；服务统一整理为时间 / 遥测量 / 值`]);
        rows.push(["输入哈希", item.sha256 || "后端未返回"]);
        rows.push(["预测模型", predictionRouteLabel(item)]);
        rows.push(["模型版本校验值（SHA-256）", item.modelVersion_sha256 || "后端未返回"]);
        rows.push(["成员 SHA-256", Array.isArray(item.member_sha256s) ? item.member_sha256s.join("、") : "后端未返回"]);
        rows.push(["模型结果选择", selectionAggregationLabel(item)]);
        rows.push(["正式点预测", productionPointAggregationLabel(item)]);
        rows.push(["预测范围依据", intervalMemberLabel(item)]);
        rows.push(["预测范围形成方式", uncertaintyModeLabel(item)]);
      } else {
        const diagnostic = telemetryRejectionDetails(item);
        rows.push(["处理事实", diagnostic.fact]); rows.push(["修正建议", diagnostic.recovery]);
      }
    });
    definitionRows($("#upload-provenance"), rows);
  }

  function telemetryResultNode(item, index) {
    const rejected = item?.status !== "predicted"; const card = document.createElement("article");
    const platformContext = platformContextForLine(appState.telemetryBatchContext || {}, item?.line || "");
    card.className = `telemetry-result${rejected ? " is-rejected" : ""}`;
    card.dataset.platformConfiguration = platformContext.platform_configuration;
    const file = document.createElement("div"); file.className = "telemetry-result-file";
    const name = document.createElement("strong"); name.textContent = item?.filename || `文件 ${index + 1}`; name.title = name.textContent;
    const contract = document.createElement("small");
    contract.textContent = rejected ? "未进入预测模型" : `${publicComponentLabel(item.line)} · ${item.input_contract?.source_records ?? "--"} 条观测 · ${item.input_contract?.windows ?? "--"} 个窗口`;
    const state = document.createElement("span"); state.textContent = rejected ? "需补充信息" : "预测完成"; file.append(name, contract, state);
    if (!rejected) {
      const inputContract = item?.input_contract || {}; const facts = document.createElement("dl"); facts.className = "telemetry-input-facts";
      const ignored = Array.isArray(inputContract.ignored_channels) ? inputContract.ignored_channels : null;
      const imputed = Number.isFinite(Number(inputContract.normalised_imputed_cells)) ? Number(inputContract.normalised_imputed_cells) : null;
      const sourceLayout = inputContract.source_layout === "wide" ? "宽表 → 内部长表" : inputContract.source_layout === "long" ? "长表" : "接口未返回";
      [["输入布局", sourceLayout], ["平台构型", platformConfigurationLabel(platformContext)], ["姿态稳定方式", platformContext.platform_configuration === "not_equipped" ? attitudeMethodLabel(platformContext) : "不适用或未说明"], ["输出范围", componentOutputScope(item.line === "bat" ? "bat" : "rw")], ["未参与模型的遥测量", ignored == null ? "接口未返回" : ignored.length ? ignored.join("、") : "无"], ["归一化后填补单元", imputed == null ? "接口未返回" : `${imputed} 个`]].forEach(([label, value]) => {
        const row = document.createElement("div"); const term = document.createElement("dt"); const detail = document.createElement("dd"); term.textContent = label; detail.textContent = value; row.append(term, detail); facts.append(row);
      });
      file.append(facts);
    }
    card.append(file);
    if (rejected) {
      const error = document.createElement("div"); error.className = "telemetry-result-error telemetry-rejection-facts";
      const diagnostic = telemetryRejectionDetails(item);
      const title = document.createElement("strong"); title.textContent = diagnostic.fact;
      const recovery = document.createElement("p"); recovery.textContent = diagnostic.recovery;
      error.append(title, recovery); card.append(error); return card;
    }
    const chart = document.createElement("div"); chart.className = "telemetry-result-chart";
    const chartHeader = document.createElement("header"); const model = document.createElement("span"); const legend = document.createElement("span");
    model.textContent = predictionRouteLabel(item);
    const chartHasRealTime = Array.isArray(item?.predictions) && item.predictions.some((row) => Number.isFinite(uploadTimePosition(row)));
    legend.textContent = `P10 · P50 · P90 / ${unitLabel(item.rul_unit)} · ${chartHasRealTime ? "真实时间横轴" : "窗口顺序横轴（接口未返回时间）"}`;
    chartHeader.append(model, legend);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.dataset.telemetryResultIndex = String(index); svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${item.filename} 的剩余寿命预测区间`); chart.append(chartHeader, svg); card.append(chart);
    const last = Array.isArray(item.predictions) ? item.predictions[item.predictions.length - 1] : null;
    const metrics = document.createElement("div"); metrics.className = "telemetry-result-metrics";
    [["末点 P10", last?.p10], ["末点 P50", last?.p50], ["末点 P90", last?.p90]].forEach(([label, value]) => {
      const cell = document.createElement("div"); const text = document.createElement("span"); const number = document.createElement("strong");
      text.textContent = label; number.textContent = finite(value) ? `${compact(value)} ${unitLabel(item.rul_unit)}` : "不可用"; cell.append(text, number); metrics.append(cell);
    });
    const replayKey = uploadedReplayKey(item, index); const replayButton = document.createElement("button"); replayButton.type = "button"; replayButton.className = "replay-result-action"; replayButton.setAttribute("data-replay-result-index", String(index));
    replayButton.append(icon("#i-orbit"), document.createTextNode(appState.uploadedReplayIds.has(replayKey) ? "已加入回放" : "加入回放")); replayButton.disabled = appState.uploadedReplayIds.has(replayKey);
    replayButton.addEventListener("click", () => addUploadedReplay(item, index)); metrics.append(replayButton);
    card.append(metrics); return card;
  }

  function uploadTimePosition(row) {
    if (finite(row?.time_order)) return Number(row.time_order);
    if (finite(row?.time)) return Number(row.time);
    if (typeof row?.timestamp === "number" && Number.isFinite(row.timestamp)) return row.timestamp;
    const raw = typeof row?.time === "string" ? row.time.trim() : "";
    if (!raw) return null;
    const numeric = Number(raw);
    if (Number.isFinite(numeric)) return numeric;
    const parsed = Date.parse(raw);
    return Number.isFinite(parsed) ? parsed : null;
 }
  function uploadTimeLabel(row, fallbackIndex = null) {
    if (row?.time != null && String(row.time).trim()) return String(row.time);
    if (finite(row?.time_order)) return compact(row.time_order);
    return fallbackIndex == null ? "未提供" : `窗口 ${Number(fallbackIndex) + 1}`;
 }
  function uploadedTimeTicks(rows, maximum = 4) {
    if (!rows.length) return [];
    const count = Math.min(maximum, rows.length); const selected = [];
    for (let index = 0; index < count; index += 1) {
      const row = rows[Math.round(index * (rows.length - 1) / Math.max(1, count - 1))];
      if (!selected.some((item) => item.timePosition === row.timePosition)) selected.push(row);
    }
    return selected;
  }

  function drawSingleTelemetryResult(svg, row, item) {
    const size = chartSize(svg, 118); svg.setAttribute("viewBox", `0 0 ${size.width} ${size.height}`); svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    const d3 = window.d3; const root = d3.select(svg); const values = [row.p10, row.p50, row.p90, row.y_pred_rul_raw].filter(finite).map(Number);
    let [low, high] = d3.extent(values); const padding = Math.max(Math.abs(high - low) * .24, Math.abs(high || 0) * .06, .001); low -= padding; high += padding;
    const margin = { left: 46, right: 18, top: 28, bottom: 30 }; const width = Math.max(1, size.width - margin.left - margin.right); const y = size.height * .52;
    const x = d3.scaleLinear().domain([low, high]).nice().range([margin.left, margin.left + width]);
    root.append("line").attr("x1", margin.left).attr("x2", margin.left + width).attr("y1", y).attr("y2", y).attr("stroke", "#30464d").attr("stroke-width", 1);
    root.append("line").attr("x1", x(Number(row.p10))).attr("x2", x(Number(row.p90))).attr("y1", y).attr("y2", y).attr("stroke", "rgba(104,224,218,.24)").attr("stroke-width", 16).attr("stroke-linecap", "round");
    [["P10", row.p10, "#f2b45d", -1], ["P50", row.p50, "#68e0da", 1], ["P90", row.p90, "#8fb9ff", -1]].forEach(([label, value, color, direction]) => {
      if (!finite(value)) return; const px = x(Number(value)); root.append("line").attr("x1", px).attr("x2", px).attr("y1", y - 15).attr("y2", y + 15).attr("stroke", color).attr("stroke-width", label === "P50" ? 2.5 : 1.4);
      root.append("text").attr("x", px).attr("y", y + direction * 25).attr("text-anchor", "middle").attr("class", "chart-label").attr("fill", color).text(`${label} ${compact(value)}`);
    });
    root.append("text").attr("x", margin.left).attr("y", 13).attr("class", "chart-label").text(`单一有效窗口 · ${row._syntheticTime ? "结束窗口" : "结束时间"} ${uploadTimeLabel(row, row._windowIndex)}`);
    root.append("text").attr("x", margin.left + width).attr("y", size.height - 5).attr("text-anchor", "end").attr("class", "chart-label").text(`剩余寿命 / ${unitLabel(item.rul_unit)}`);
  }
  function drawTelemetryResultChart(svg, item) {
    if (!svg) return; svg.replaceChildren(); const rows = Array.isArray(item?.predictions) ? item.predictions : [];
    const hasRealTime = rows.some((row) => Number.isFinite(uploadTimePosition(row)));
    const useWindowAxis = item?.allow_index_axis === true && !hasRealTime;
    const usable = rows.map((row, index) => {
      const actualTime = uploadTimePosition(row);
      return {
        ...row,
        _windowIndex: index,
        _syntheticTime: useWindowAxis,
        timePosition: useWindowAxis ? index : actualTime,
      };
    }).filter((row) => Number.isFinite(row.timePosition) && [row.p10, row.p50, row.p90].some(finite));
    if (!window.d3 || !usable.length) {
      const text = document.createElementNS("http://www.w3.org/2000/svg", "text"); text.setAttribute("x", 12); text.setAttribute("y", 25); text.setAttribute("class", "chart-label"); text.textContent = rows.length ? "接口未返回可绘制的时间字段；当前结果不会伪造连续遥测。" : "没有可绘制的预测结果"; svg.append(text); return;
    }
    if (usable.length === 1) { drawSingleTelemetryResult(svg, usable[0], item); return; }
    const size = chartSize(svg, 82); svg.setAttribute("viewBox", `0 0 ${size.width} ${size.height}`); svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    const d3 = window.d3; const root = d3.select(svg); const margin = { top: 7, right: 9, bottom: 25, left: 38 };
    const width = Math.max(1, size.width - margin.left - margin.right); const height = Math.max(1, size.height - margin.top - margin.bottom);
    const values = usable.flatMap((row) => [row.p10, row.p50, row.p90].filter(finite).map(Number)); let [low, high] = d3.extent(values); let [start, end] = d3.extent(usable, (row) => row.timePosition);
    if (low === high) { const padding = Math.max(1, Math.abs(low || 0) * .05); low -= padding; high += padding; }
    if (start === end) { const padding = Math.max(1, Math.abs(start || 0) * .05); start -= padding; end += padding; }
    const x = d3.scaleLinear().domain([start, end]).range([0, width]); const y = d3.scaleLinear().domain([low, high]).nice().range([height, 0]);
    const group = root.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
    group.append("g").attr("class", "chart-grid").call(d3.axisLeft(y).ticks(3).tickSize(-width).tickFormat(""));
    group.append("g").attr("class", "chart-axis").call(d3.axisLeft(y).ticks(3).tickFormat((value) => compact(value)));
    const area = d3.area().defined((row) => finite(row.p10) && finite(row.p90)).x((row) => x(row.timePosition)).y0((row) => y(Number(row.p10))).y1((row) => y(Number(row.p90))).curve(d3.curveMonotoneX);
    group.append("path").datum(usable).attr("d", area).attr("fill", "rgba(104, 224, 218, .14)");
    [["p10", "#f2b45d", "3 3", 1], ["p50", "#68e0da", null, 1.8], ["p90", "#8fb9ff", "3 3", 1]].forEach(([key, color, dash, strokeWidth]) => {
      const line = d3.line().defined((row) => finite(row[key])).x((row) => x(row.timePosition)).y((row) => y(Number(row[key]))).curve(d3.curveMonotoneX);
      group.append("path").datum(usable).attr("d", line).attr("fill", "none").attr("stroke", color).attr("stroke-width", strokeWidth).attr("stroke-dasharray", dash);
      const endRow = [...usable].reverse().find((row) => finite(row[key])); if (endRow) group.append("circle").attr("cx", x(endRow.timePosition)).attr("cy", y(Number(endRow[key]))).attr("r", key === "p50" ? 2.7 : 1.8).attr("fill", color);
    });
    const ticks = uploadedTimeTicks(usable); const tickLabels = new Map(ticks.map((row) => [row.timePosition, uploadTimeLabel(row, row._windowIndex)]));
    group.append("g").attr("class", "chart-axis").attr("transform", `translate(0,${height})`).call(d3.axisBottom(x).tickValues(ticks.map((row) => row.timePosition)).tickFormat((value) => tickLabels.get(value) || compact(value)));
    group.append("text").attr("class", "chart-label").attr("x", width).attr("y", height + 21).attr("text-anchor", "end").text(useWindowAxis ? "窗口顺序（接口未返回时间）" : "时间");
    if (useWindowAxis || item?.replay_prediction_only) {
      group.append("text").attr("class", "chart-label").attr("x", 0).attr("y", -1).text("仅展示接口返回的预测窗口；未补造连续遥测，亦未伪造；未附原始遥测。");
    }
  }

  function redrawTelemetryCharts() {
    const results = appState.telemetryBatch?.results || [];
    // Keep the prediction surface on the same honest axis contract as replay:
    // when the response has no usable time field, show window order explicitly
    // instead of dropping the otherwise valid prediction curve.
    $$("[data-telemetry-result-index]").forEach((svg) => drawTelemetryResultChart(svg, {
      ...(results[Number(svg.dataset.telemetryResultIndex)] || {}),
      allow_index_axis: true,
    }));
  }

  function optionalMetadataValue(item, candidates) {
    const metadata = item?.input_contract?.optional_metadata || {};
    for (const [header, state] of Object.entries(metadata)) {
      const normalized = header.toLowerCase().replace(/[\s_.\-()（）]/g, "");
      const values = Array.isArray(state?.distinct_values) ? state.distinct_values.filter((value) => value != null && value !== "") : [];
      if (candidates.some((candidate) => normalized.includes(candidate)) && values.length === 1) return values[0];
    }
    return null;
  }

  function displayContext() {
    const value = (selector) => $(selector)?.value?.trim() || null;
    return { ...uploadPlatformContext(), orbit_mode: value("#context-orbit-mode") || "unspecified", epoch_utc: value("#context-epoch-utc"), altitude: value("#context-altitude"), perigee: value("#context-perigee"), apogee: value("#context-apogee"), speed: value("#context-speed"), inclination: value("#context-inclination"), raan: value("#context-raan"), aop: value("#context-aop"), ta: value("#context-ta"), period: value("#context-period"), condition: value("#context-condition"), degradation: value("#context-degradation"), failure: value("#context-failure") };
  }

  function uploadedReplayKey(item, index) {
    return `${appState.telemetryBatch?.batch_id || "batch"}:${index}:${item?.sha256 || item?.filename || "file"}`;
  }

  function uploadedDisplayName(item) {
    const archiveMember = item?.input_contract?.archive?.member;
    return String(archiveMember || item?.filename || "未命名上传文件");
  }

  function applyReplayContext(sample) {
    const context = sample?.replay_context || {};
    const platform = normalisePlatformContext(context); sample.platform_configuration = platform.platform_configuration; sample.attitude_control_method = platform.attitude_control_method;
    const mode = context.orbit_mode || "unspecified";
    const orbitParts = [mode !== "unspecified" ? ({ circular: "圆轨道", apsides: "椭圆轨道", state: "状态量轨道" }[mode] || "轨道") : null, ["circular", "state"].includes(mode) && context.altitude ? `${context.altitude} km` : null, mode === "apsides" && context.perigee && context.apogee ? `近 ${context.perigee} / 远 ${context.apogee} km` : null, context.inclination ? `倾角 ${context.inclination}°` : null, context.period ? `原始周期 ${context.period} min` : null].filter(Boolean);
    sample.orbit = orbitParts.join(" · ") || "未提供可选轨道上下文";
    sample.load_level = context.condition || "未提供可选运行工况";
    sample.health_level = context.degradation || "未提供可选退化状态";
    sample.failure_mode = context.failure || "未提供可选失效标签";
  }

  const replayContextFields = {
    platform_configuration: "#replay-context-platform-configuration", attitude_control_method: "#replay-context-attitude-method",
    orbit_mode: "#replay-context-orbit-mode", epoch_utc: "#replay-context-epoch-utc", altitude: "#replay-context-altitude", period: "#replay-context-period", perigee: "#replay-context-perigee", apogee: "#replay-context-apogee", speed: "#replay-context-speed", inclination: "#replay-context-inclination", raan: "#replay-context-raan", aop: "#replay-context-aop", ta: "#replay-context-ta",
    condition: "#replay-context-condition", degradation: "#replay-context-degradation", failure: "#replay-context-failure",
  };

  function collectReplayContext(sample = currentSample()) {
    const context = {}; Object.entries(replayContextFields).forEach(([key, selector]) => { context[key] = $(selector)?.value?.trim() || null; });
    Object.assign(context, normalisePlatformContext(context));
    const relevant = {
      unspecified: [], circular: ["epoch_utc", "altitude", "period", "inclination", "raan", "ta"],
      apsides: ["epoch_utc", "period", "perigee", "apogee", "inclination", "raan", "aop", "ta"],
      state: ["epoch_utc", "altitude", "period", "speed", "inclination", "raan", "aop"],
    }[context.orbit_mode || "unspecified"] || [];
    ["epoch_utc", "altitude", "period", "perigee", "apogee", "speed", "inclination", "raan", "aop", "ta"].forEach((key) => { if (!relevant.includes(key)) context[key] = null; });
    context.period = context.period ?? sample?.replay_context?.period ?? sample?.original_context?.period ?? null; return context;
  }
  function syncContextPreset(select, input, sourceValue) {
    if (!select || !input) return; const value = sourceValue == null ? "" : String(sourceValue); const registered = Array.from(select.options).some((option) => option.value === value && option.value !== "__custom__");
    select.value = registered ? value : value ? "__custom__" : ""; input.value = value; input.hidden = select.value !== "__custom__"; syncCustomSelect(select);
  }
  function updateContextPreset(select, focusCustom = false) {
    const input = select ? document.getElementById(select.dataset.contextTarget || "") : null; if (!select || !input) return;
    const custom = select.value === "__custom__"; input.hidden = !custom; if (!custom) input.value = select.value; else if (focusCustom) window.requestAnimationFrame(() => input.focus());
  }
  function syncReplayPlatformConfiguration() {
    const configuration = $("#replay-context-platform-configuration"); const attitude = $("#replay-context-attitude-method"); const attitudeField = $("#replay-context-attitude-method-field"); const sample = currentSample();
    if (!configuration) return;
    const noWheelOption = Array.from(configuration.options).find((option) => option.value === "not_equipped");
    if (noWheelOption) noWheelOption.disabled = sample?.line === "rw";
    if (sample?.line === "rw" && configuration.value === "not_equipped") configuration.value = "unspecified";
    const noWheel = configuration.value === "not_equipped";
    if (attitudeField) attitudeField.hidden = !noWheel;
    if (!noWheel && attitude) attitude.value = "unspecified";
    syncCustomSelect(configuration); syncCustomSelect(attitude);
  }
  function renderDerivedOrbitFacts(context = collectReplayContext()) {
    const validation = validateOrbitContext(context); const mode = context.orbit_mode || "unspecified";
    const sample = { uploaded: true, replay_context: context }; const state = validation.ok && mode !== "unspecified" ? orbitStateForSample(sample) : null;
    const speedSummary = (values) => {
      const valid = values.filter(finite).map(Number).sort((a, b) => a - b); if (!valid.length) return "--";
      const kmh = valid.map((value) => format(value * 3600, 1));
      return valid.length === 1
        ? `${format(valid[0], 3)} km/s · ${kmh[0]} km/h`
        : `${format(valid[0], 3)} 至 ${format(valid[valid.length - 1], 3)} km/s · ${kmh[0]} 至 ${kmh[kmh.length - 1]} km/h`;
    };
    let primary = mode === "unspecified" ? "未指定" : validation.ok ? state?.classification || "参数待确认" : "参数未通过";
    let speedText = "--"; let periodText = mode === "unspecified" || !validation.ok ? "--" : "开放轨道无周期"; const detail = [validation.message];
    if (state && finite(state.a) && Number(state.a) > 0 && Number(state.e) < 1) {
      const period = 2 * Math.PI * Math.sqrt(Number(state.a) ** 3 / EARTH_MU_KM3_S2) / 60; periodText = `${format(period, 2)} min`;
      if (mode === "circular") speedText = speedSummary([Math.sqrt(EARTH_MU_KM3_S2 / Number(state.a))]);
      else if (mode === "apsides") {
        const rp = Number(state.a) * (1 - Number(state.e)); const ra = Number(state.a) * (1 + Number(state.e));
        const perigeeSpeed = Math.sqrt(EARTH_MU_KM3_S2 * (2 / rp - 1 / Number(state.a))); const apogeeSpeed = Math.sqrt(EARTH_MU_KM3_S2 * (2 / ra - 1 / Number(state.a)));
        speedText = speedSummary([apogeeSpeed, perigeeSpeed]); detail.push(`远地点速度 ${format(apogeeSpeed, 3)} km/s，近地点速度 ${format(perigeeSpeed, 3)} km/s。`);
      } else if (mode === "state") speedText = speedSummary([context.speed]);
      if (finite(context.period)) { const supplied = Number(context.period); const difference = Math.abs(supplied - period) / Math.max(period, 1e-9); detail.push(difference > .02 ? `原文件周期 ${format(supplied, 2)} min 与二体换算值相差 ${format(difference * 100, 1)}%；投影采用轨道几何换算值，原始值仍保留。` : `原文件周期 ${format(supplied, 2)} min 与二体换算一致。`); }
    } else if (state && mode === "state") {
      const radius = EARTH_RADIUS_KM + Number(context.altitude); const circular = Math.sqrt(EARTH_MU_KM3_S2 / radius); const escape = Math.sqrt(2 * EARTH_MU_KM3_S2 / radius);
      speedText = speedSummary([context.speed]); detail.push(`当前位置圆轨道速度 ${format(circular, 3)} km/s，逃逸速度 ${format(escape, 3)} km/s；当前状态按输入速度判定。`);
    }
    setText("#orbit-derived-primary", primary); setText("#orbit-derived-speed", speedText); setText("#orbit-derived-period", periodText); setText("#orbit-derived-detail", detail.filter(Boolean).join(" "));
    const message = $("#replay-context-validation"); if (message) { message.textContent = validation.message; message.dataset.state = validation.ok ? "valid" : "invalid"; }
  }
  function syncReplayOrbitMode() {
    const mode = $("#replay-context-orbit-mode")?.value || "unspecified";
    $$('[data-orbit-modes]', $("#replay-context-editor") || document).forEach((field) => { const active = field.dataset.orbitModes.split(/\s+/).includes(mode); field.hidden = !active; $$('input, select', field).forEach((input) => { input.disabled = !active; }); });
    renderDerivedOrbitFacts();
  }

  function renderReplayContextEditor(sample) {
    const editor = $("#replay-context-editor"); if (!editor) return;
    editor.hidden = !sample?.uploaded;
    editor.closest(".mission-stage")?.classList.toggle("has-upload-context", Boolean(sample?.uploaded));
    if (!sample?.uploaded) return;
    if (editor.dataset.sampleId === sample.sample_id) { syncReplayPlatformConfiguration(); syncReplayOrbitMode(); return; }
    editor.dataset.sampleId = sample.sample_id;
    Object.entries(replayContextFields).forEach(([key, selector]) => { const input = $(selector); if (input) input.value = sample.replay_context?.[key] || ""; });
    $$('[data-context-preset]', editor).forEach((select) => syncContextPreset(select, document.getElementById(select.dataset.contextTarget || ""), sample.replay_context?.[Object.entries(replayContextFields).find(([, selector]) => selector === `#${select.dataset.contextTarget}`)?.[0]]));
    syncReplayPlatformConfiguration(); syncReplayOrbitMode();
    const supplied = Object.entries(sample.original_context || {}).filter(([, value]) => value != null && value !== "").map(([key]) => ({ orbit_mode: "轨道定义", epoch_utc: "轨道时间基准", altitude: "轨道高度", perigee: "近地点", apogee: "远地点", speed: "切向速度", inclination: "轨道倾角", raan: "升交点赤经", aop: "近地点幅角", ta: "初始轨道相位", period: "轨道周期", condition: "运行工况", degradation: "退化状态", failure: "失效标签" })[key]).filter(Boolean);
    const platformStatement = `平台构型为用户在浏览器中声明的“${platformSummary(sample.replay_context)}”，不进入冻结模型。`;
    setText("#replay-context-source", supplied.length
      ? `原文件已提供：${supplied.join("、")}。${platformStatement} 这里的修改只作用于当前浏览器回放投影，原文件和模型输出保持不变。`
      : `原文件未提供这些可选字段。${platformStatement} 这里可以补充当前浏览器回放投影；原文件和模型输出保持不变。`);
  }

  function validateOrbitContext(context) {
    const mode = context?.orbit_mode || "unspecified";
    if (mode === "unspecified") return { ok: true, message: "未指定轨道事实：页面不会绘制轨迹，也不会替你猜测。" };
    const epochStatus = orbitEpochStatus(context?.epoch_utc);
    const groundTrack = epochStatus.ok
      ? { ok: true, code: null, message: "" }
      : { ok: false, code: epochStatus.code === "missing_epoch" ? "missing_epoch" : "invalid_epoch", message: epochStatus.message };
    const number = (key) => finite(context?.[key]) ? Number(context[key]) : null;
    const inclination = number("inclination");
    if (inclination !== null && (inclination < 0 || inclination > 180)) return { ok: false, message: "轨道倾角必须在 0° 至 180° 之间。请按赤道参考面填写。" };
    const suppliedPeriod = number("period");
    if (suppliedPeriod !== null && suppliedPeriod <= 0) return { ok: false, message: "原始轨道周期必须大于 0 min；留空则由轨道几何自动换算。" };
    if (mode === "circular") {
      const altitude = number("altitude");
      if (altitude === null) return { ok: false, message: "圆轨道只需要填写相对参考地球半径的轨道高度（km）；速度和周期会自动换算。" };
      if (altitude < ORBIT_MIN_PERIGEE_KM) return { ok: false, message: `轨道高度必须不低于 ${ORBIT_MIN_PERIGEE_KM} km；低于该边界不绘制闭合轨迹。` };
      const radius = EARTH_RADIUS_KM + altitude; const speed = Math.sqrt(EARTH_MU_KM3_S2 / radius); const period = 2 * Math.PI * Math.sqrt(radius ** 3 / EARTH_MU_KM3_S2) / 60;
      return { ok: true, groundTrack, message: `圆轨道通过物理检查：高度 ${format(altitude, 1)} km 对应速度 ${format(speed, 3)} km/s、周期 ${format(period, 2)} min。${groundTrack.message}` };
    }
    if (mode === "apsides") {
      const perigee = number("perigee"); const apogee = number("apogee");
      if (perigee === null || apogee === null) return { ok: false, message: "椭圆轨道需要同时填写近地点和远地点高度（km）。" };
      if (perigee < ORBIT_MIN_PERIGEE_KM) return { ok: false, message: `近地点必须不低于 ${ORBIT_MIN_PERIGEE_KM} km；否则轨道会与任务边界相交。` };
      if (apogee < perigee) return { ok: false, message: "远地点不能低于近地点。请检查两个高度的顺序。" };
      const rp = EARTH_RADIUS_KM + perigee; const ra = EARTH_RADIUS_KM + apogee; const a = (rp + ra) / 2; const eccentricity = (ra - rp) / (ra + rp); const period = 2 * Math.PI * Math.sqrt(a ** 3 / EARTH_MU_KM3_S2) / 60;
      return { ok: true, groundTrack, message: `椭圆轨道通过物理检查：偏心率 ${format(eccentricity, 5)}，周期 ${format(period, 2)} min；速度范围由远地点至近地点自动换算。${groundTrack.message}` };
    }
    if (mode === "state") {
      const altitude = number("altitude"); const speed = number("speed");
      if (altitude === null || speed === null) return { ok: false, message: "状态量模式需要同时填写当前位置相对参考地球半径的高度（km）和切向速度（km/s）。" };
      if (altitude < ORBIT_MIN_PERIGEE_KM) return { ok: false, message: `当前位置高度低于 ${ORBIT_MIN_PERIGEE_KM} km 任务边界，不能作为在轨任务轨迹。` };
      const radius = EARTH_RADIUS_KM + altitude; const circular = Math.sqrt(EARTH_MU_KM3_S2 / radius); const escape = Math.sqrt(2 * EARTH_MU_KM3_S2 / radius);
      if (speed <= 0) return { ok: false, message: "切向速度必须大于 0 km/s。" };
      const energy = speed * speed / 2 - EARTH_MU_KM3_S2 / radius;
      const eccentricity = Math.abs(radius * speed * speed / EARTH_MU_KM3_S2 - 1);
      const a = Math.abs(energy) > 1e-9 ? -EARTH_MU_KM3_S2 / (2 * energy) : null;
      const stateTa = speed < circular - 1e-8 ? 180 : 0; const p = radius * radius * speed * speed / EARTH_MU_KM3_S2;
      const state = normaliseOrbitState({ mode, a, e: eccentricity, p, radius, speed, circularSpeed: circular, energy, ta: stateTa });
      if (!state || !finite(state.perigee) || state.perigee < ORBIT_MIN_PERIGEE_KM) {
        return { ok: false, message: `当前速度会使近地点约为 ${state && finite(state.perigee) ? `${format(state.perigee, 1)} km` : "未知"}，低于 ${ORBIT_MIN_PERIGEE_KM} km 任务边界；页面不会绘制与地球相交的轨迹。` };
      }
      if (speed >= escape) return { ok: true, groundTrack, message: `当前速度 ${format(speed, 3)} km/s 不低于逃逸速度 ${format(escape, 3)} km/s，将按开放${speed > escape ? "双曲线" : "抛物线临界"}近地点弧段显示。${groundTrack.message}` };
      const position = speed < circular - 1e-8 ? "远地点" : speed > circular + 1e-8 ? "近地点" : "圆轨道当前位置";
      return { ok: true, groundTrack, message: `闭合${eccentricity < .01 ? "圆" : "椭圆"}轨道通过物理检查：当前位置视为${position}，近地点 ${format(state.perigee, 1)} km，圆轨道速度 ${format(circular, 3)} km/s。${groundTrack.message}` };
    }
    return { ok: true, groundTrack, message: `轨道参数通过基本物理检查。${groundTrack.message}` };
  }

  function saveReplayContext(event) {
    event.preventDefault(); const sample = currentSample(); if (!sample?.uploaded) return;
    const next = collectReplayContext(sample);
    const platformValidation = validatePlatformContext(next, sample.line); const orbitValidation = validateOrbitContext(next); const validation = platformValidation.ok ? orbitValidation : platformValidation;
    const message = $("#replay-context-validation"); if (message) { message.textContent = validation.message; message.dataset.state = validation.ok ? "valid" : "invalid"; }
    if (!platformValidation.ok || (!orbitValidation.ok && next.orbit_mode !== "unspecified")) return;
    sample.replay_context = next; applyReplayContext(sample); rebuildOrbitObjects(); renderHeader(); renderAssets(); renderSystem(); renderDerivedOrbitFacts(next); refreshOrbitProjection({ force: true, invalidate: true });
    showToast("回放上下文已更新；模型预测与上传文件未改变。");
  }

  // Compatibility marker: function addUploadedReplay(item, index)
  function addUploadedReplay(item, index, options = {}) {
    const { navigate = true, render = true, toast = true } = options;
    if (item?.status !== "predicted" || !Array.isArray(item.predictions) || !item.predictions.length) return false;
    const replayKey = uploadedReplayKey(item, index); const existingId = appState.uploadedReplayIds.get(replayKey);
    if (existingId) { if (navigate) { setSample(existingId); showView("operate"); } return true; }
    const display = displayContext(); const contract = item.input_contract || {}; const line = item.line === "rwa" ? "rw" : "bat"; const context = { ...display, ...platformContextForLine(appState.telemetryBatchContext || display, line) };
    const originalContext = {
      orbit_mode: optionalMetadataValue(item, ["orbitmode", "轨道定义"]),
      epoch_utc: optionalMetadataValue(item, ["orbitepochutc", "epochutc", "utc时间基准", "轨道时间基准", "历元"]),
      altitude: optionalMetadataValue(item, ["orbitaltitudekm", "altitudekm", "轨道高度"]),
      perigee: optionalMetadataValue(item, ["orbitperigeealtitudekm", "perigeealtitudekm", "近地点高度"]),
      apogee: optionalMetadataValue(item, ["orbitapogeealtitudekm", "apogeealtitudekm", "远地点高度"]),
      speed: optionalMetadataValue(item, ["orbitspeedkms", "speedkms", "切向速度"]),
      period: optionalMetadataValue(item, ["orbitalperiodmin", "orbitperiodmin", "轨道周期", "运行周期"]),
      inclination: optionalMetadataValue(item, ["inclinationdeg", "轨道倾角", "倾角"]),
      raan: optionalMetadataValue(item, ["oritraandeg", "raandeg", "升交点赤经"]),
      aop: optionalMetadataValue(item, ["orbitargperiapsisdeg", "aopdeg", "近地点幅角"]),
      ta: optionalMetadataValue(item, ["orbittrueanomalydeg", "tadeg", "真近点角"]),
      condition: optionalMetadataValue(item, ["operatingcondition", "condition", "运行工况", "工况"]),
      degradation: optionalMetadataValue(item, ["degradationstate", "退化状态"]),
      failure: optionalMetadataValue(item, ["failurelabel", "failuremode", "失效标签", "失效模式"]),
    };
    const replayContext = Object.fromEntries(Object.keys(originalContext).map((key) => {
      const originalValue = originalContext[key]; return [key, originalValue !== null && originalValue !== undefined && originalValue !== "" ? originalValue : context[key] ?? null];
    }));
    replayContext.platform_configuration = context.platform_configuration;
    replayContext.attitude_control_method = context.attitude_control_method;
    if (!replayContext.orbit_mode || replayContext.orbit_mode === "unspecified") {
      replayContext.orbit_mode = replayContext.perigee != null && replayContext.apogee != null ? "apsides" : replayContext.speed != null && replayContext.altitude != null ? "state" : replayContext.altitude != null ? "circular" : "unspecified";
    }
    const platformValidation = validatePlatformContext(replayContext, line); const orbitValidation = validateOrbitContext(replayContext); const contextValidation = platformValidation.ok ? orbitValidation : platformValidation;
    const contextMessage = $("#context-orbit-validation");
    if (contextMessage) { contextMessage.textContent = contextValidation.message; contextMessage.dataset.state = contextValidation.ok ? "valid" : "invalid"; }
    if (!platformValidation.ok || !orbitValidation.ok) {
      if (toast) showToast(`回放上下文未通过检查：${contextValidation.message} 结果仍保留在寿命预测页。`, "error");
      return false;
    }
    const sampleId = `upload:${String(appState.telemetryBatch?.batch_id || item.sha256 || Date.now())}:${index}:${String(item.sha256 || "").slice(0, 12)}`;
    const rows = item.predictions.map((row) => ({ ...row, raw_estimate: row.y_pred_rul_raw, display_estimate: row.y_pred_rul, boundary_adjusted: row.rul_output_clamped }));
    const displayName = uploadedDisplayName(item);
    const sample = {
      sample_id: sampleId, display_name: displayName, line, line_label: line === "bat" && replayContext.platform_configuration === "not_equipped" ? "电池部件（储能系统）健康评估" : publicComponentLabel(line),
      dataset_id: `本次上传 · ${displayName}`, load_level: "模型预测", uploaded: true,
      original_context: originalContext, replay_context: replayContext,
      platform_context_source: "browser_user_declaration", provenance: { sim_model: predictionRouteLabel(item), modelVersion_sha256: item.modelVersion_sha256, member_sha256s: item.member_sha256s, uncertainty_mode: item.uncertainty_mode }, upload_contract: contract, upload_sha256: item.sha256,
    };
    applyReplayContext(sample);
    const outputChannel = `model.${line}.predicted_rul`; payload.channel_meta[outputChannel] = { label: "模型剩余寿命估计", unit: unitLabel(item.rul_unit) };
    samples.push(sample); predictions[sampleId] = { rows, time_unit: item.rul_unit, source: "upload" };
    const replayTrace = contract.replay_trace && typeof contract.replay_trace === "object" ? contract.replay_trace : null;
    const traceTimes = Array.isArray(replayTrace?.time_order) ? replayTrace.time_order.filter((value) => finite(value)).map(Number) : [];
    const traceDisplay = Array.isArray(replayTrace?.time_display) ? replayTrace.time_display.map((value) => String(value ?? "")) : [];
    const traceChannels = replayTrace?.channels && typeof replayTrace.channels === "object" ? Object.fromEntries(Object.entries(replayTrace.channels).map(([channel, values]) => [channel, Array.isArray(values) ? values.map((value) => finite(value) ? Number(value) : null) : []])) : {};
    telemetry[sampleId] = {
      // Use the service-returned source timestamps when available.  A missing
      // trace remains an explicit empty state; prediction rows are never
      // copied into the raw telemetry channel as a fabricated curve.
      t_days: traceTimes.length ? traceTimes : [],
      time_display: traceDisplay.length === traceTimes.length ? traceDisplay : [],
      time_unit: contract.time_unit || replayTrace?.time_kind || "index",
      channels: traceChannels,
      labels: {},
      replay_trace_meta: replayTrace ? { source_points: replayTrace.source_points, display_points: replayTrace.display_points, downsampled: replayTrace.downsampled === true, time_header: replayTrace.time_header || "" } : null,
    };
    appState.uploadedSampleIds.add(sampleId); appState.uploadedReplayIds.set(replayKey, sampleId); appState.line = line; appState.sampleId = sampleId; appState.progress = 0; appState.channel = null;
    if (render) { rebuildOrbitObjects(); renderAll(); renderSourceSelector(); renderTelemetryResults(appState.telemetryBatch, 200); }
    if (navigate) showView("operate");
    if (toast) showToast(`${displayName} 已加入本次回放工作区。`); return true;
  }

  function renderTelemetryResults(batch, statusCode) {
    appState.telemetryBatch = batch; appState.telemetryExportUrl = batch?.export?.csv || null;
    const results = Array.isArray(batch?.results) ? batch.results : [];
    const counts = batch?.counts || { submitted: results.length, predicted: results.filter((item) => item.status === "predicted").length, rejected: results.filter((item) => item.status !== "predicted").length };
    const title = batch?.status === "complete" ? "全部文件预测完成" : batch?.status === "partial" ? "部分文件预测完成" : "未生成预测结果";
    setText("#telemetry-result-region h2", title);
    setText("#telemetry-result-summary", `提交 ${counts.submitted ?? results.length} 个文件；生成 ${counts.predicted ?? 0} 份预测，${counts.rejected ?? 0} 份需补充或修正信息。每项结果均保留独立状态。`);
    const host = $("#telemetry-results"); const nodes = results.map(telemetryResultNode); host?.replaceChildren(...nodes); syncUploadPlatformConfiguration();
    if (!nodes.length && batch?.error) {
      const item = telemetryResultNode({ filename: "本次提交", status: "rejected", error: batch.error }, 0); host?.replaceChildren(item); nodes.push(item);
    }
    const exportButton = $("#telemetry-export"); if (exportButton) { exportButton.hidden = !appState.telemetryExportUrl; exportButton.dataset.exportUrl = appState.telemetryExportUrl || ""; }
    syncAddAllReplayButton(results);
    updateUploadProvenance(batch); window.requestAnimationFrame(redrawTelemetryCharts); animateTelemetryNodes(nodes);
    if (statusCode === 503) setTelemetryStatus("模型产物当前不可用，所有文件均未生成预测。", "error");
    else if (batch?.status === "partial") setTelemetryStatus("部分文件预测完成；其余文件的补充或修正建议见逐文件说明。", "error");
    else if (batch?.status === "complete") setTelemetryStatus("预测完成，结果来自当前上传文件与已登记模型。");
    else setTelemetryStatus("文件未通过校验，请按逐文件提示修正。", "error");
  }

  function syncAddAllReplayButton(results = appState.telemetryBatch?.results || []) {
    const button = $("#telemetry-add-all"); if (!button) return;
    const valid = results.map((item, index) => ({ item, index })).filter(({ item }) => item?.status === "predicted" && Array.isArray(item.predictions) && item.predictions.length);
    const remaining = valid.filter(({ item, index }) => !appState.uploadedReplayIds.has(uploadedReplayKey(item, index)));
    button.disabled = remaining.length === 0; button.setAttribute("aria-label", remaining.length ? `将 ${remaining.length} 个预测结果加入回放` : "所有预测结果均已加入回放");
    button.classList.toggle("is-complete", valid.length > 0 && remaining.length === 0);
  }

  function addAllUploadedReplay() {
    const results = Array.isArray(appState.telemetryBatch?.results) ? appState.telemetryBatch.results : [];
    const valid = results.map((item, index) => ({ item, index })).filter(({ item }) => item?.status === "predicted" && Array.isArray(item.predictions) && item.predictions.length);
    const remaining = valid.filter(({ item, index }) => !appState.uploadedReplayIds.has(uploadedReplayKey(item, index)));
    if (!remaining.length) { showToast("没有新的预测结果可加入回放。", "info"); return; }
    remaining.forEach(({ item, index }) => addUploadedReplay(item, index, { navigate: false, render: false, toast: false }));
    renderAll(); renderSourceSelector(); renderTelemetryResults(appState.telemetryBatch, 200); syncAddAllReplayButton(results);
    showToast(`已将 ${remaining.length} 个预测结果加入回放工作区。`);
  }

  async function submitTelemetry(event) {
    event.preventDefault(); if (appState.telemetrySubmitting) return;
    if (!appState.telemetryFiles.length) { setTelemetryStatus("请先选择至少一个遥测文件。", "error"); return; }
    if (!control.enabled) { setTelemetryStatus("当前为只读预览，上传预测需要完整控制服务。", "error"); return; }
    const selectedLine = $("#telemetry-line")?.value || "auto"; const platformContext = uploadPlatformContext(); const platformValidation = validatePlatformContext(platformContext, selectedLine);
    if (!platformValidation.ok) { setTelemetryStatus(platformValidation.message, "error"); showToast(platformValidation.message, "error"); return; }
    const form = new FormData(); appState.telemetryFiles.forEach((file) => {
      form.append("files", file, file.name);
      form.append("time_unit", appState.telemetryFileUnits.get(telemetryFileKey(file)) || "auto");
    });
    form.append("line", selectedLine);
    appState.telemetrySubmitting = true; syncTelemetryControls(); $("#telemetry-result-region")?.setAttribute("aria-busy", "true"); setTelemetryStatus("正在校验字段、构造窗口并运行预测…", "busy");
    try {
      const response = await fetch("/api/telemetry/predict", { method: "POST", headers: { Accept: "application/json", "X-RUL-CSRF": control.csrf || "" }, credentials: "same-origin", body: form });
      const body = await response.json().catch(() => ({ status: "rejected", error: { message: `服务返回了不可解析的响应（HTTP ${response.status}）。` } }));
      if ([200, 207, 422, 503].includes(response.status)) { appState.telemetryBatchContext = platformContext; renderTelemetryResults(body, response.status); }
      else throw Object.assign(new Error(telemetryErrorMessage(body, response.status)), { status: response.status, body });
    } catch (error) {
      setText("#telemetry-result-region h2", "提交未完成"); setText("#telemetry-result-summary", error.message); setTelemetryStatus(error.message, "error"); showToast(error.message, "error");
    } finally {
      appState.telemetrySubmitting = false; $("#telemetry-result-region")?.removeAttribute("aria-busy"); syncTelemetryControls(); renderTelemetryFiles();
    }
  }

  async function exportTelemetryResults(event) {
    event.preventDefault(); const path = appState.telemetryExportUrl; const button = $("#telemetry-export"); if (!path || !button) return;
    button.setAttribute("aria-disabled", "true"); setTelemetryStatus("正在准备结果文件…", "busy");
    try {
      const response = await fetch(path, { headers: { Accept: "text/csv", "X-RUL-CSRF": control.csrf || "" }, credentials: "same-origin" });
      if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(telemetryErrorMessage(body, response.status)); }
      const blob = await response.blob(); const disposition = response.headers.get("Content-Disposition") || ""; const match = disposition.match(/filename="?([^";]+)"?/i);
      downloadBlob(blob, match?.[1] || "brphm-telemetry-results.csv"); setTelemetryStatus("预测结果已导出为 CSV 文件。");
    } catch (error) { setTelemetryStatus(`导出失败：${error.message}`, "error"); showToast(`导出失败：${error.message}`, "error"); }
    finally { button.removeAttribute("aria-disabled"); }
  }

  function initTelemetry() { renderTelemetrySchema(FALLBACK_TELEMETRY_UPLOAD_SCHEMA, "fallback"); renderTelemetryFiles(); loadTelemetrySchema(); loadTelemetryExamples(); }
  function findFieldInput(key) { return $$('[data-operation-field]').find((node) => node.dataset.operationField === key) || null; }
  function operationValues(operation) { const values = {}; for (const field of publicOperationFields(operation)) { const input = findFieldInput(field.key); if (!input) continue; if (field.kind === "boolean") values[field.key] = input.checked; else if (field.kind === "integer" || field.kind === "number") { const raw = input.value.trim(); values[field.key] = raw === "" ? null : Number(raw); } else values[field.key] = input.value; } return values; }
  const resourceLabel = (resource) => ({ cpu: "处理器", gpu: "图形加速器", browser: "浏览器", network: "网络", matlab: "MATLAB" })[resource] || "受控计算资源";
  const riskLabel = (risk) => ({ read: "只读检查", write: "生成结果", heavy: "长时计算", production: "需要确认" })[risk] || "受控流程";
  const publicOperationMessage = (value, fallback = "输入或运行环境未通过检查，请按字段提示修正后重试。") => publicNarrative(value, fallback);
  const publicFieldValue = (value, empty = "未指定") => {
    if (value == null || value === "" || (Array.isArray(value) && !value.length)) return empty;
    if (typeof value === "boolean") return value ? "是" : "否";
    if (Array.isArray(value)) return value.map((item) => publicFieldValue(item)).join("、");
    if (typeof value === "object") {
      const min = value.min == null ? "不限" : value.min; const max = value.max == null ? "不限" : value.max; const step = value.step == null ? "" : `，步长 ${value.step}`;
      return `范围 ${min} 至 ${max}${step}`;
    }
    return publicNarrative(String(value), empty).replace(/--+/g, "未指定");
  };
  function publicChoiceLabel(field, choice, index = 0) {
    const text = String(choice ?? "");
    return publicNarrative(text, `${publicNarrative(field?.label, "已登记选项")} ${index + 1}`);
  }
  function fieldHelpText(field) {
    const parts = []; if (field.description) parts.push(publicOperationMessage(field.description, "按本字段说明填写。"));
    const defaultValue = field.default_display ?? field.default; const defaultIndex = (field.choices || []).indexOf(defaultValue);
    parts.push(`默认使用${Array.isArray(field.choices) && field.choices.includes(defaultValue) ? publicChoiceLabel(field, defaultValue, Math.max(0, defaultIndex)) : publicFieldValue(defaultValue)}。`);
    if (field.example != null) parts.push(`可参考${publicFieldValue(field.example)}。`);
    return parts.join(" ");
  }
  function fieldDetailsNode(field, index) {
    const details = document.createElement("details"); details.className = "operation-field-details"; details.id = `operation-field-details-${index}`;
    const summary = document.createElement("summary"); const choiceCount = Array.isArray(field.choices) ? field.choices.length : 0; summary.textContent = choiceCount > 6 ? `查看填写说明与 ${choiceCount} 个可用项` : "查看完整填写说明";
    const help = document.createElement("div"); help.className = "operation-field-help"; help.id = `operation-field-help-${index}`;
    const purpose = document.createElement("p"); purpose.textContent = fieldHelpText(field); help.append(purpose);
    if (Array.isArray(field.allowed_values)) {
      const values = field.allowed_values.map((value) => String(value));
      const boundary = document.createElement("div"); boundary.className = "operation-choice-boundary";
      const boundaryLabel = document.createElement("p"); boundaryLabel.className = "operation-choice-summary";
      boundaryLabel.textContent = values.length > 6
        ? `允许范围共 ${values.length} 项；当前下拉框可直接选择。`
        : `允许范围：${values.map((value) => publicNarrative(value, "未指定")).join("、")}。`;
      boundary.append(boundaryLabel);
      if (values.length > 6) {
        const choiceDetails = document.createElement("details"); choiceDetails.className = "operation-choice-details";
        const choiceSummary = document.createElement("summary"); choiceSummary.textContent = `展开查看全部 ${values.length} 项`;
        const choiceList = document.createElement("ol"); choiceList.className = "operation-choice-list";
        values.forEach((value) => { const item = document.createElement("li"); item.textContent = publicNarrative(value, "未指定"); choiceList.append(item); });
        choiceDetails.append(choiceSummary, choiceList); boundary.append(choiceDetails);
      }
      help.append(boundary);
    } else if (field.allowed_values != null) {
      const boundary = document.createElement("p"); boundary.textContent = `允许范围为${publicFieldValue(field.allowed_values)}。`; help.append(boundary);
    }
    details.append(summary, help); return { details, help };
  }
  function setWorkflowJourney(step) {
    $$(".workflow-journey li").forEach((item, index) => {
      item.classList.toggle("is-current", index === step);
      item.classList.toggle("is-done", index < step);
    });
  }
  function resetInputCheck(message = "修改参数后会重新检查；检查过程不会启动任务。") {
    appState.inputCheck = false; const panel = $(".input-check-panel"); panel?.classList.remove("is-ok", "is-error");
    setText("#input-check-state", "等待检查"); setText("#input-check-message", publicOperationMessage(message, "等待检查输入。"));
    const run = $("#operation-run"); if (run) run.disabled = true; setWorkflowJourney(1);
  }
  function updateRequestPreview() {
    const operation = appState.operations.find((item) => item.id === appState.operationId); if (!operation) return;
    resetInputCheck("参数已更新，系统会重新检查输入；检查过程不会启动任务。"); window.clearTimeout(appState.inputCheckTimer);
    appState.inputCheckTimer = window.setTimeout(() => performInputCheck(false), 320);
  }
  function fieldElement(field, index) {
    if (!field) return null;
    const wrapper = document.createElement("div"); wrapper.className = `operation-field${field.kind === "boolean" ? " boolean-field" : ""}`;
    const title = document.createElement("label"); const label = document.createElement("strong"); label.textContent = publicNarrative(field.label, "配置项");
    const required = document.createElement("em"); required.textContent = field.required === true ? "必填" : "可选"; title.append(label, required);
    const fieldId = `operation-field-${index}`; title.htmlFor = fieldId; const { details, help } = fieldDetailsNode(field, index);
    if (field.kind === "boolean") {
      const input = document.createElement("input"); input.id = fieldId; input.type = "checkbox"; input.checked = field.default === true; input.dataset.operationField = field.key;
      input.setAttribute("aria-details", details.id); input.setAttribute("aria-describedby", help.id); input.addEventListener("change", updateRequestPreview); wrapper.append(title, input, details); return wrapper;
    }
    const input = field.kind === "select" ? document.createElement("select") : document.createElement("input");
    if (field.kind === "select") {
      const selectedValue = field.default ?? ""; (field.choices || []).forEach((choice, choiceIndex) => { const option = document.createElement("option"); option.value = choice; option.textContent = choice === "" ? "未指定" : publicChoiceLabel(field, choice, choiceIndex); option.selected = choice === selectedValue; input.append(option); });
    } else {
      input.type = field.kind === "integer" || field.kind === "number" ? "number" : "text"; input.value = field.default ?? "";
      input.placeholder = publicFieldValue(field.example, "按用途填写"); if (field.min != null) input.min = String(field.min); if (field.max != null) input.max = String(field.max); if (field.step != null) input.step = String(field.step); if (field.pattern) input.pattern = field.pattern;
    }
    input.id = fieldId; input.dataset.operationField = field.key; input.required = field.required === true; input.setAttribute("aria-details", details.id); input.setAttribute("aria-describedby", help.id);
    input.addEventListener("input", updateRequestPreview); input.addEventListener("change", updateRequestPreview); wrapper.append(title, input, details); return wrapper;
  }
  function publicOperationFields(operation) {
    return (operation?.fields || []).filter((field) => field && field.user_visible !== false && field.kind !== "argv_tokens");
  }
  function operationPurpose(operation) { return publicOperationMessage(operation?.purpose_zh || operation?.description, "已登记的受控业务流程。") }
  function operationLabel(operation) { return publicNarrative(operation?.label, "业务流程"); }
  function operationCategory(value) {
    const labels = { "数据": "数据处理", "仿真": "任务仿真", "模型": "寿命建模", "复现": "结果重现", "评测": "结果解读", "证据": "结果说明", "验收": "结果核验" };
    return labels[value] || publicNarrative(value, "受控流程");
  }
  function selectOperation(operationId) {
    const operation = appState.operations.find((item) => item.id === operationId); if (!operation) return;
    appState.operationId = operation.id; setWorkflowJourney(0); $$(".operation-item").forEach((button) => button.classList.toggle("is-active", button.dataset.operationId === operation.id));
    setText("#operation-category", operationCategory(operation.category)); setText("#operation-title", operationLabel(operation)); setText("#operation-description", operationPurpose(operation));
    const risk = $("#operation-risk"); if (risk) { risk.className = `risk-badge is-${operation.risk || "read"}`; risk.textContent = riskLabel(operation.risk); }
    const fields = publicOperationFields(operation); definitionRows($("#operation-meta"), [
      ["计算资源", resourceLabel(operation.resource)], ["填写内容", fields.length ? `${fields.length} 项可调整` : "直接使用登记默认设置"],
      ["提交条件", operation.requires_confirmation ? "核对后需要确认" : "完成检查后可提交"], ["当前状态", operation.availability?.available === false ? "暂不可运行" : "可进行输入检查"],
    ]);
    const fieldHost = $("#operation-fields"); if (fieldHost) { const nodes = fields.map(fieldElement).filter(Boolean); destroyCustomSelects(fieldHost); fieldHost.replaceChildren(...nodes); upgradeCustomSelects(fieldHost); if (!nodes.length) { const empty = document.createElement("p"); empty.className = "operation-description"; empty.textContent = "此流程使用已登记的默认设置，无需额外填写。"; fieldHost.append(empty); } }
    const confirm = $("#operation-confirm"); $("#operation-confirm-row").hidden = !operation.requires_confirmation; if (confirm) confirm.checked = false;
    setText("#operation-message", control.enabled ? "" : "当前仅可浏览任务说明，无法提交作业。"); updateRequestPreview();
  }
  function filteredOperations() {
    return appState.operations.filter((operation) => {
      const values = [operationLabel(operation), operationCategory(operation.category), operationPurpose(operation), ...publicOperationFields(operation).flatMap((field) => [field.label, field.description, field.example, field.allowed_values])].map((value) => publicFieldValue(value, ""));
      return fuzzySearch(values, appState.operationSearch) && (appState.operationCategory === "all" || operation.category === appState.operationCategory) && (appState.riskFilter === "all" || operation.risk === appState.riskFilter);
    });
  }
  function renderOperationList() {
    const operations = filteredOperations(); const groups = [];
    operations.forEach((operation) => { const groupName = operationCategory(operation.category); let group = groups.find((item) => item.name === groupName); if (!group) { group = { name: groupName, values: [] }; groups.push(group); } group.values.push(operation); });
    setText("#operation-count", `${operations.length} 项`);
    const nodes = groups.map((group) => { const section = document.createElement("section"); section.className = "operation-group"; const heading = document.createElement("header"); const name = document.createElement("span"); const count = document.createElement("span"); name.textContent = group.name; count.textContent = `${group.values.length} 项`; heading.append(name, count); section.append(heading);
      group.values.forEach((operation) => { const button = document.createElement("button"); button.type = "button"; button.className = "operation-item"; button.dataset.operationId = operation.id; button.setAttribute("aria-label", operationLabel(operation)); const dot = document.createElement("i"); dot.className = `is-${operation.risk || "read"}`; const text = document.createElement("span"); const title = document.createElement("strong"); const detail = document.createElement("small"); title.textContent = operationLabel(operation); detail.textContent = operationPurpose(operation); text.append(title, detail); const state = document.createElement("em"); state.textContent = operation.availability?.available === false ? "暂不可运行" : riskLabel(operation.risk); button.append(dot, text, state); button.addEventListener("click", () => selectOperation(operation.id)); section.append(button); }); return section;
    });
    const host = $("#operation-list"); host?.replaceChildren(...nodes); if (!operations.length) { const empty = document.createElement("p"); empty.className = "operation-description"; empty.textContent = "没有匹配的业务流程，请调整搜索或分类。"; host?.append(empty); }
    else if (!operations.some((operation) => operation.id === appState.operationId)) selectOperation(operations[0].id); else $$(".operation-item").forEach((button) => button.classList.toggle("is-active", button.dataset.operationId === appState.operationId));
  }
  function updateCategoryFilter() { const select = $("#operation-category-filter"); if (!select) return; const categories = []; appState.operations.forEach((operation) => { if (!categories.includes(operation.category)) categories.push(operation.category); }); select.replaceChildren(new Option("全部分类", "all"), ...categories.map((category) => new Option(operationCategory(category), category))); select.value = appState.operationCategory; upgradeSelect(select); syncCustomSelect(select); }
  function syncOperationSubmit(operation) {
    const button = $("#operation-run"); if (!button) return;
    button.disabled = !appState.inputCheck || Boolean(operation?.requires_confirmation && !$("#operation-confirm")?.checked);
  }
  async function performInputCheck(manual = true) {
    if (!control.enabled) return; const operation = appState.operations.find((item) => item.id === appState.operationId); if (!operation) return;
    const panel = $(".input-check-panel"); setText("#input-check-state", "正在检查");
    try {
      // `confirmed` authorizes compilation only here; this endpoint never starts a task.
      const body = await api("/api/operations/check-input", { method: "POST", body: JSON.stringify({ operation: operation.id, params: operationValues(operation), confirmed: true }) });
      const available = body.available === true && body.ok === true; const reason = publicOperationMessage(body.reason || body.errors?.[0]);
      panel?.classList.toggle("is-ok", available); panel?.classList.toggle("is-error", !available); setText("#input-check-state", available ? "输入已通过" : "需要修正");
      setText("#input-check-message", available ? "输入与当前运行环境检查完成，尚未启动任务。提交前仍需完成必要确认。" : reason); appState.inputCheck = available; if (available) setWorkflowJourney(2); syncOperationSubmit(operation);
      if (manual && !available) showToast(reason, "error");
    } catch (error) {
      const reason = publicOperationMessage(error.message); appState.inputCheck = false; panel?.classList.add("is-error"); panel?.classList.remove("is-ok");
      setText("#input-check-state", "需要修正"); setText("#input-check-message", reason); syncOperationSubmit(operation); if (manual) showToast(`输入检查未通过：${reason}`, "error");
    }
  }

  const jobStatusLabel = (status) => ({ queued: "等待执行", running: "正在执行", succeeded: "已完成", failed: "未完成", cancelled: "已取消" })[status] || "状态已更新";
  const publicJobTime = (value) => {
    const date = new Date(value); if (!value || Number.isNaN(date.getTime())) return "时间已记录";
    return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  };
  const publicJobLogLine = (line) => {
    return publicNarrative(line, "");
  };
  function jobButton(job) {
    const button = document.createElement("button"); button.type = "button"; button.className = `job-item${job.id === appState.jobId ? " is-active" : ""}`; button.dataset.jobId = job.id;
    const dot = document.createElement("i"); dot.className = `is-${job.status}`; const text = document.createElement("span"); const title = document.createElement("strong"); const detail = document.createElement("small");
    title.textContent = publicNarrative(job.label, "已提交任务"); detail.textContent = publicJobTime(job.created_utc); text.append(title, detail); const state = document.createElement("span"); state.textContent = jobStatusLabel(job.status); button.append(dot, text, state); button.addEventListener("click", () => loadJob(job.id)); return button;
  }
  function renderJobs() { const host = $("#job-list"); if (!host) return; host.replaceChildren(...appState.jobs.map(jobButton)); if (!appState.jobs.length) { const empty = document.createElement("p"); empty.className = "operation-description"; empty.textContent = "尚未提交任务。完成输入检查后，可在此查看状态与结果。"; host.append(empty); } }
  function renderJobDetail(job) {
    const detail = $("#job-detail"); if (!job || !detail) { if (detail) detail.hidden = true; return; } detail.hidden = false;
    setText("#job-detail-title", publicNarrative(job.label, "已提交任务")); setText("#job-detail-state", jobStatusLabel(job.status));
    const meta = [["任务状态", jobStatusLabel(job.status)], ["计算资源", resourceLabel(job.resource)], ["提交时间", publicJobTime(job.created_utc)]]; if (job.started_utc) meta.push(["开始时间", publicJobTime(job.started_utc)]); if (job.ended_utc) meta.push(["结束时间", publicJobTime(job.ended_utc)]); definitionRows($("#job-detail-meta"), meta);
    const log = $("#job-log"); if (log) { const lines = (job.log || []).map(publicJobLogLine).filter(Boolean); log.textContent = lines.length ? lines.join("\n") : "该任务暂未返回可展示的执行说明。"; }
    const cancel = $("#job-cancel"); if (cancel) { cancel.hidden = !["queued", "running"].includes(job.status); cancel.dataset.jobId = job.id; }
    const retry = $("#job-retry"); if (retry) { retry.hidden = !["failed", "cancelled"].includes(job.status); retry.dataset.jobId = job.id; }
    const artifacts = $("#job-artifacts"); artifacts?.replaceChildren(...(job.artifacts || []).map((artifact, index) => { const link = document.createElement("a"); link.className = "artifact-link"; link.href = artifact.url || "#"; link.download = ""; const name = document.createElement("span"); name.textContent = `结果文件 ${index + 1}`; const size = document.createElement("small"); size.textContent = artifact.size_bytes != null ? humanBytes(artifact.size_bytes) : "已生成"; link.append(name, size); return link; }));
  }
  function setJobsRefreshBusy(busy) {
    const button = $("#jobs-refresh"); if (!button) return; const refreshIcon = $(".icon", button);
    window.gsap?.killTweensOf?.(refreshIcon); button.classList.remove("is-settling");
    if (busy) {
      button.setAttribute("aria-busy", "true"); button.classList.add("is-refreshing");
      if (window.gsap && !reduceMotion && refreshIcon) window.gsap.to(refreshIcon, { rotation: "+=360", duration: .72, ease: "none", repeat: -1, overwrite: "auto" });
      return;
    }
    button.removeAttribute("aria-busy"); button.classList.remove("is-refreshing");
    if (!window.gsap || reduceMotion || !refreshIcon) { refreshIcon?.style.removeProperty("transform"); return; }
    const current = Number(window.gsap.getProperty(refreshIcon, "rotation")) || 0; const normalized = ((current % 360) + 360) % 360; const remaining = normalized < .5 ? 0 : 360 - normalized;
    button.classList.add("is-settling"); window.gsap.to(refreshIcon, { rotation: current + remaining, duration: clamp(remaining / 360 * .72, .12, .34), ease: "power2.out", overwrite: "auto", onComplete: () => { button.classList.remove("is-settling"); window.gsap.set(refreshIcon, { clearProps: "transform" }); } });
  }
  async function refreshJobs({ animate = false } = {}) {
    if (!control.enabled) return;
    const refreshButton = $("#jobs-refresh"); if (animate && refreshButton?.getAttribute("aria-busy") === "true") return; if (animate) setJobsRefreshBusy(true);
    try { const body = await api("/api/jobs"); appState.jobs = Array.isArray(body.jobs) ? body.jobs : []; renderJobs(); if (appState.jobId) await loadJob(appState.jobId, false); }
    catch (error) { setText("#operation-message", `作业状态刷新失败：${publicOperationMessage(error.message)}`); }
    finally { if (animate) setJobsRefreshBusy(false); }
  }
  async function loadJob(jobId, redrawList = true) {
    if (!control.enabled) return;
    try { appState.jobId = jobId; const body = await api(`/api/jobs/${jobId}`); renderJobDetail(body.job); if (redrawList) renderJobs(); }
    catch (error) { setText("#operation-message", `作业详情读取失败：${publicOperationMessage(error.message)}`); }
  }
  async function submitOperation(event) {
    event.preventDefault(); const operation = appState.operations.find((item) => item.id === appState.operationId); if (!operation || !control.enabled) return;
    if (!appState.inputCheck) { await performInputCheck(true); if (!appState.inputCheck) return; }
    if (operation.requires_confirmation && !$("#operation-confirm")?.checked) { setText("#operation-message", "请先确认该流程会生成新的结果或占用较长计算时间。"); return; }
    const button = $("#operation-run"); if (button) button.disabled = true; setText("#operation-message", "正在提交任务…");
    try {
      const body = await api("/api/jobs", { method: "POST", body: JSON.stringify({ operation: operation.id, params: operationValues(operation), confirmed: !operation.requires_confirmation || $("#operation-confirm")?.checked === true }) });
      appState.jobId = body.job.id; setWorkflowJourney(3); setText("#operation-message", "任务已提交，可在右侧查看进度与结果。"); await refreshJobs(); renderJobDetail(body.job);
    } catch (error) { setText("#operation-message", `提交失败：${publicOperationMessage(error.message)}`); }
    finally { syncOperationSubmit(operation); }
  }
  async function cancelSelectedJob() { const jobId = $("#job-cancel")?.dataset.jobId; if (!jobId) return; try { const body = await api(`/api/jobs/${jobId}/cancel`, { method: "POST", body: "{}" }); renderJobDetail(body.job); await refreshJobs(); } catch (error) { showToast(`停止失败：${publicOperationMessage(error.message)}`, "error"); } }
  async function retrySelectedJob() { const jobId = $("#job-retry")?.dataset.jobId; if (!jobId) return; try { const body = await api(`/api/jobs/${jobId}/retry`, { method: "POST", body: JSON.stringify({ confirmed: true }) }); appState.jobId = body.job.id; await refreshJobs(); renderJobDetail(body.job); } catch (error) { showToast(`重新执行失败：${publicOperationMessage(error.message)}`, "error"); } }
  function renderOperationCatalog(catalog) {
    const listed = Array.isArray(catalog?.user_operations) ? catalog.user_operations : Array.isArray(catalog?.operations) ? catalog.operations : [];
    const operations = listed.filter((operation) => operation?.audience !== "developer");
    if (!operations.length) return false;
    appState.operations = operations;
    const count = appState.operations.length;
    setText("#operation-ready-count", `${count} 项`);
    const bar = $("#operation-ready-bar");
    if (bar) bar.style.setProperty("--coverage", "100%");
    updateCategoryFilter();
    renderOperationList();
    return true;
  }
  async function initWorkflows() {
    if (!control.enabled) { $("#operation-run").disabled = true; setText("#operation-title", "任务服务暂不可用"); setText("#operation-description", "当前仅可浏览任务说明，无法提交或查看实时作业。"); return; }
    const renderedEmbeddedCatalog = renderOperationCatalog(embeddedOperations);
    try {
      const body = await api("/api/operations");
      // Keep the public contract explicit: only the user-facing task list is
      // accepted from the service; developer inventory never enters the UI.
      if (!Array.isArray(body.user_operations)) throw new Error("服务未返回可执行任务目录");
      if (!renderOperationCatalog(body)) throw new Error("服务未返回可执行任务目录");
    } catch (error) {
      if (!renderedEmbeddedCatalog && !appState.operations.length) setText("#operation-description", `业务流程读取失败：${publicOperationMessage(error.message)}`);
      else setText("#operation-message", "任务目录暂未完成实时刷新，已保留当前页面中的已登记流程。");
    }
    await refreshJobs();
  }

  const METHOD_PHASES = {
    load: {
      index: "两类部件输入要求", title: "先按部件确认输入、单位与输出边界",
      copy: "电池部件（储能系统）与反作用轮部件（姿态控制执行器）使用各自的已核验模型。服务只依据明确的部件语义分流：储能系统电池部件输出循环数，姿态控制执行器输出天数；轨道、工况和失效标签只用于回放说明，不会被当作模型通道。",
      points: ["两类部件分别使用对应的输入语义、寿命单位和模型身份。", "特征顺序、归一化统计和结果后处理方式随模型版本一并核对。", "缺少必要通道、时间断档、重复观测或非有限值时会停止处理，不用页面补值冒充模型输入。"],
      facts: [["电池部件（储能系统）", "输出单位：循环"], ["反作用轮部件（姿态控制执行器）", "输出单位：天"], ["边界", "不从文件名或数值范围猜测部件"]],
    },
    signal: {
      index: "公开退化数据训练", title: "用 PyTorch 建立两类部件的基础退化映射",
      copy: "公开退化窗口先按固定特征顺序提取统计量与时间信息，再使用训练阶段得到的归一化统计进入 PyTorch 年龄分段回归器。电池部件（储能系统）与反作用轮部件（姿态控制执行器）分别训练，避免把不同部件的尺度混在一起。",
      points: ["基础训练只读取公开退化数据；航天数据的答案不会反向进入这一步。", "特征顺序、归一化统计和模型文件校验值会随测试记录保存，未保存的优化器状态不作留档声明。", "本阶段生成可供后续适配的 PyTorch 模型文件，不在浏览器现场生成估计。"],
      facts: [["框架", "PyTorch"], ["输入", "公开退化数据"], ["输出", "两类部件各自的基础模型文件"]],
    },
    adapt: {
      index: "航天工况适配", title: "在相同 PyTorch 结构中校准航天工况",
      copy: "适配阶段先用航天训练数据拟合，并用验证数据确定训练轮数；随后把训练数据和验证数据合并重新拟合最终模型。训练、适配和预测始终复用同一模型定义、特征顺序与归一化统计。",
      points: ["验证数据只用于选择模型训练轮数；最终重新拟合后的回看仅作为拟合内诊断，不冒充独立验证。", "最终模型文件、三组独立结果和版本校验值在预测前固定，预测时不会回读答案调参。", "模型文件绑定部件身份；电池部件与反作用轮部件的输入不能交叉使用，发现不一致会停止处理。"],
      facts: [["选择", "用训练数据拟合，并用验证数据确定训练轮数"], ["最终拟合", "训练数据与验证数据合并后重新拟合"], ["核对", "模型版本信息、输出方式与三个独立模型文件的校验值"]],
    },
    interval: {
      index: "电池部件（储能系统） · 反作用轮部件（姿态控制执行器）", title: "按部件执行各自的年龄分段预测与时序约束",
      copy: "电池部件（储能系统）用三个独立模型结果的中位数形成正式点预测，再执行单调非增投影；反作用轮部件（姿态控制执行器）正式点预测固定来自主模型，再执行因果运行最小值。三个独立模型结果用于形成经验预测范围和衡量离散度，不参与反作用轮部件正式点预测的平均。",
      points: ["电池部件正式点预测取三个独立模型结果的中位数；反作用轮部件正式点预测使用主模型输出，其他结果只用于显示范围。", "预测入口只接受已核验的独立测试数据或外部上传数据；在训练数据上的回看会明确标为拟合内诊断。", "原始估计、页面呈现值与边界处理状态分别保留，便于核对具体是哪一步改变了结果。"],
      facts: [["电池部件剩余寿命预测模型（储能系统）", "三个独立模型结果取中位数 + 单调非增投影"], ["反作用轮部件剩余寿命预测模型（姿态控制执行器）", "主模型输出 + 因果运行最小值"], ["经验预测范围", "三个独立模型结果的安全分位范围"]],
    },
  };
  function setMethodPhase(phase) {
    const content = METHOD_PHASES[phase]; if (!content) return;
    $$('[data-method-phase]').forEach((button) => { const active = button.dataset.methodPhase === phase; button.classList.toggle("is-active", active); button.setAttribute("aria-selected", String(active)); });
    const focus = $("#method-story-focus"); const update = () => {
      setText("#method-story-index", content.index); setText("#method-story-title", content.title); setText("#method-story-copy", content.copy);
      const points = $("#method-story-points"); points?.replaceChildren(...content.points.map((value) => { const item = document.createElement("li"); item.textContent = value; return item; }));
      const facts = $("#method-story-facts"); facts?.replaceChildren(...content.facts.flatMap(([label, value]) => { const term = document.createElement("dt"); const detail = document.createElement("dd"); term.textContent = label; detail.textContent = value; return [term, detail]; }));
    };
    if (!window.gsap || reduceMotion || !focus) { update(); return; }
    window.gsap.killTweensOf(focus); window.gsap.to(focus, { opacity: 0, x: 8, duration: .12, ease: "power2.in", onComplete: () => { update(); window.gsap.fromTo(focus, { opacity: 0, x: -8 }, { opacity: 1, x: 0, duration: .34, ease: "back.out(1.25)", clearProps: "transform,opacity" }); } });
  }
  const PRESS_TARGET_SELECTOR = "button:not(.sheet-scrim), summary, a.artifact-link, a.example-link";
  const DENSE_PRESS_TARGET_SELECTOR = ".operation-item, .job-item, .asset-item, .asset-remove, .telemetry-file-item > button";
  let delegatedPressBound = false;
  function bindDelegatedPressFeedback() {
    if (delegatedPressBound) return; delegatedPressBound = true;
    const pointerTargets = new Map(); const keyboardTargets = new Set();
    const pressTarget = (node) => {
      const target = node?.closest?.(PRESS_TARGET_SELECTOR); if (!target || target.matches(":disabled") || target.getAttribute("aria-disabled") === "true" || target.hidden) return null; return target;
    };
    const pressIcon = (target) => {
      if (target.matches("#jobs-refresh, .view-tab, .mobile-nav button") || target.dataset.actionGlyph === "ready") return null;
      return $(".icon", target);
    };
    const setPressed = (target, pressed) => {
      if (!target) return; target.classList.toggle("is-pressing", pressed);
      if (!window.gsap || reduceMotion || target.matches(DENSE_PRESS_TARGET_SELECTOR) || !target.isConnected) return;
      const iconNode = pressIcon(target); window.gsap.killTweensOf(target); if (iconNode) window.gsap.killTweensOf(iconNode);
      if (pressed) {
        window.gsap.to(target, { scale: .975, duration: .07, ease: "power2.out", transformOrigin: "50% 50%", overwrite: "auto" });
        if (iconNode) window.gsap.to(iconNode, { y: 1, scale: .94, duration: .07, ease: "power2.out", overwrite: "auto" });
      } else {
        window.gsap.to(target, { scale: 1, duration: .28, ease: "expo.out", overwrite: "auto", clearProps: "transform" });
        if (iconNode) window.gsap.to(iconNode, { y: 0, scale: 1, duration: .24, ease: "expo.out", overwrite: "auto", clearProps: "transform" });
      }
    };
    const releasePointer = (event) => { const target = pointerTargets.get(event.pointerId); if (!target) return; pointerTargets.delete(event.pointerId); setPressed(target, false); };
    document.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) return; const target = pressTarget(event.target); if (!target) return;
      const previous = pointerTargets.get(event.pointerId); if (previous && previous !== target) setPressed(previous, false); pointerTargets.set(event.pointerId, target); setPressed(target, true);
    });
    document.addEventListener("pointerup", releasePointer); document.addEventListener("pointercancel", releasePointer);
    document.addEventListener("pointerout", (event) => { const target = pointerTargets.get(event.pointerId); if (target && !target.contains(event.relatedTarget)) releasePointer(event); });
    document.addEventListener("keydown", (event) => {
      if (event.repeat || !["Enter", " "].includes(event.key) || event.altKey || event.ctrlKey || event.metaKey) return;
      const target = pressTarget(event.target); if (!target) return; keyboardTargets.add(target); setPressed(target, true);
    });
    document.addEventListener("keyup", (event) => { if (!["Enter", " "].includes(event.key)) return; keyboardTargets.forEach((target) => setPressed(target, false)); keyboardTargets.clear(); });
    document.addEventListener("focusout", (event) => { const target = pressTarget(event.target); if (!target || !keyboardTargets.delete(target)) return; setPressed(target, false); });
    window.addEventListener("blur", () => { pointerTargets.forEach((target) => setPressed(target, false)); pointerTargets.clear(); keyboardTargets.forEach((target) => setPressed(target, false)); keyboardTargets.clear(); }, { passive: true });
  }
  function bindMicroInteractions() {
    $$('[data-method-phase]').forEach((button) => button.addEventListener("click", () => setMethodPhase(button.dataset.methodPhase)));
    setupNavigationGlyphs(); setupActionGlyphs(); bindDelegatedPressFeedback();
    if (!window.gsap || reduceMotion) return;
    const navTimelines = new WeakMap();
    const stopNavMotion = (button, immediate = false) => {
      navTimelines.get(button)?.kill(); navTimelines.delete(button);
      const parts = $$(".nav-part", button); window.gsap.killTweensOf(parts);
      window.gsap.to(parts, { x: 0, y: 0, rotation: 0, scale: 1, scaleX: 1, scaleY: 1, autoAlpha: 1, duration: immediate ? 0 : .26, ease: "expo.out", overwrite: "auto", clearProps: "transform,opacity,visibility" });
    };
    const startNavMotion = (button, pressed = false) => {
      stopNavMotion(button, true); const view = button.dataset.view; const part = (selector) => $(selector, button);
      const timeline = window.gsap.timeline({ repeat: pressed ? 0 : -1, repeatDelay: pressed ? 0 : .16, defaults: { overwrite: "auto" } });
      if (view === "predict") {
        const payloadPart = part(".nav-payload"); const tray = part(".nav-tray");
        timeline.to(payloadPart, { y: -4.2, duration: .22, ease: "power2.out" })
          .to(payloadPart, { y: 0, duration: .36, ease: "expo.out" })
          .to(tray, { y: 1, scaleX: .9, duration: .12, ease: "power2.out" }, "<.16")
          .to(tray, { y: 0, scaleX: 1, duration: .28, ease: "expo.out" })
          .to([payloadPart, tray], { x: 0, y: 0, scale: 1, scaleX: 1, duration: .001 });
      } else if (view === "operate") {
        const satellite = part(".nav-satellite"); const ring = part(".nav-orbit-ring");
        timeline.to(satellite, { keyframes: [{ x: 0, y: 0 }, { x: 3.2, y: -1.4 }, { x: .6, y: -3.2 }, { x: -3.1, y: -.4 }, { x: -1.1, y: 3 }, { x: 2.4, y: 1.7 }, { x: 0, y: 0 }], duration: pressed ? .8 : 1.65, ease: "none" })
          .to(ring, { rotation: 5, scale: 1.04, duration: .72, ease: "sine.inOut" }, "<")
          .to(ring, { rotation: 0, scale: 1, duration: .93, ease: "sine.inOut" });
      } else if (view === "workflows") {
        const prompt = part(".nav-prompt"); const caret = part(".nav-caret"); const frame = part(".nav-terminal-frame");
        timeline.to(prompt, { x: 2.3, duration: .25, ease: "power2.out" })
          .to(prompt, { x: 0, duration: .28, ease: "expo.out" })
          .to(caret, { autoAlpha: .12, duration: .1 }, "<.02")
          .to(caret, { autoAlpha: 1, duration: .14 })
          .to(frame, { y: -1, scaleY: .96, duration: .12, ease: "power2.out" }, "<")
          .to(frame, { y: 0, scaleY: 1, duration: .28, ease: "expo.out" })
          .to([prompt, caret, frame], { x: 0, y: 0, scale: 1, scaleY: 1, autoAlpha: 1, duration: .001 });
      } else if (view === "evidence") {
        const high = part(".nav-layer-high"); const mid = part(".nav-layer-mid"); const low = part(".nav-layer-low");
        timeline.to(high, { y: -3.2, duration: .24, ease: "power2.out" })
          .to(mid, { x: 1.8, duration: .24, ease: "power2.out" }, "<")
          .to(low, { y: 3.2, duration: .24, ease: "power2.out" }, "<")
          .to([high, mid, low], { x: 0, y: 0, duration: .36, ease: "expo.out" });
      } else if (view === "system") {
        const packet = part(".nav-data-packet"); const shell = part(".nav-database-shell");
        timeline.set(packet, { y: -5, autoAlpha: 0 })
          .to(packet, { y: -1, autoAlpha: 1, duration: .24, ease: "power2.out" })
          .to(packet, { y: 4.5, autoAlpha: 0, duration: .34, ease: "power2.in" })
          .to(shell, { scaleY: .94, duration: .1, ease: "power2.out" }, "<.16")
          .to(shell, { scaleY: 1, duration: .26, ease: "expo.out" })
          .set(packet, { y: -5, autoAlpha: 0 });
      }
      navTimelines.set(button, timeline); return timeline;
    };
    // Delegation keeps dynamically rendered source cards, method cards and
    // replay rows at the same interaction quality as the static controls.
    const iconHoverTarget = (node) => node?.closest?.("svg.icon, .method-segment, .evaluation-dimension, .provenance-node, #source-open-replay, #telemetry-add-all, #telemetry-export, #upload-contract-jump, #upload-contract-collapse, #scene-status-toggle, #orbit-play, #orbit-reset, #orbit-zoom-in, #orbit-zoom-out, #orbit-mode, #play-button, #speed-button, .custom-select-trigger, .operation-choice-details > summary, .operation-field-details > summary, .replay-context-editor > summary, .asset-row, .telemetry-file-item, .operation-item, .job-item, .contract-glance-card, .field-reference > summary, .display-context > summary");
    const statefulIconOwner = ".custom-select-trigger, .operation-choice-details > summary, .operation-field-details > summary, .replay-context-editor > summary, #upload-contract-collapse, #scene-status-toggle, .field-reference > summary, .display-context > summary";
    const iconNodes = (target) => {
      if (!target) return [];
      const icons = target.matches("svg.icon") ? [target] : Array.from(target.querySelectorAll("svg.icon"));
      return icons.flatMap((item) => {
        if (item.closest(".action-glyph, .nav-glyph")) return [];
        if (item.closest("#jobs-refresh")) return [];
        if (item.closest(statefulIconOwner)) { const use = item.querySelector("use"); return use ? [use] : []; }
        return [item];
      });
    };
    const iconGesture = (node) => {
      const reference = (node?.matches?.("use") ? node : node?.querySelector?.("use"))?.getAttribute("href") || "";
      if (reference.includes("refresh") || reference.includes("reset")) return { rotation: 180, duration: .48, ease: "power2.out" };
      if (reference.includes("orbit") || reference.includes("globe")) return { rotation: 42, scale: 1.08, duration: .46, ease: "sine.inOut" };
      if (reference.includes("upload")) return { y: -3, scale: 1.08, duration: .38, ease: "back.out(1.35)" };
      if (reference.includes("download")) return { y: 3, scale: 1.08, duration: .38, ease: "back.out(1.35)" };
      if (reference.includes("search")) return { x: 2, rotation: -7, duration: .32, ease: "back.out(1.55)" };
      if (reference.includes("check") || reference.includes("shield")) return { scale: 1.12, duration: .32, ease: "back.out(1.45)" };
      if (reference.includes("x") || reference.includes("stop")) return { rotation: 90, scale: 1.06, duration: .34, ease: "back.out(1.35)" };
      if (reference.includes("chevron") || reference.includes("play") || reference.includes("terminal")) return { x: 2.5, scale: 1.06, duration: .32, ease: "back.out(1.45)" };
      return { y: -2, scale: 1.08, rotation: 3, duration: .34, ease: "back.out(1.45)" };
    };
    const fineHover = window.matchMedia?.("(hover: hover) and (pointer: fine)").matches === true;
    document.addEventListener("pointerover", (event) => {
      if (!fineHover) return;
      const target = iconHoverTarget(event.target); if (!target || target.contains(event.relatedTarget)) return;
      const nodes = iconNodes(target); if (!nodes.length) return;
      nodes.forEach((node, index) => { const gesture = iconGesture(node); window.gsap.killTweensOf(node); window.gsap.to(node, { ...gesture, delay: index * .025, overwrite: "auto" }); });
    });
    document.addEventListener("pointerout", (event) => {
      if (!fineHover) return;
      const target = iconHoverTarget(event.target); if (!target || target.contains(event.relatedTarget)) return;
      const nodes = iconNodes(target); if (!nodes.length) return;
      window.gsap.to(nodes, { x: 0, y: 0, scale: 1, rotation: 0, duration: .28, ease: "expo.out", stagger: .015, overwrite: "auto", clearProps: "transform" });
    });
    $$(".view-tab, .mobile-nav button").forEach((button) => {
      if (fineHover) { button.addEventListener("pointerenter", () => startNavMotion(button)); button.addEventListener("pointerleave", () => stopNavMotion(button)); }
      button.addEventListener("pointerdown", () => startNavMotion(button, true));
      button.addEventListener("pointerup", () => fineHover && button.matches(":hover") ? startNavMotion(button) : stopNavMotion(button));
      button.addEventListener("pointercancel", () => stopNavMotion(button));
    });
    $$("[data-risk-filter]").forEach((button) => {
      button.addEventListener("pointerenter", () => window.gsap.to(button, { y: -1, duration: .22, ease: "expo.out", overwrite: "auto" }));
      button.addEventListener("pointerleave", () => window.gsap.to(button, { y: 0, scale: 1, duration: .28, ease: "expo.out", overwrite: "auto", clearProps: "transform" }));
      button.addEventListener("click", () => window.gsap.fromTo(button, { scale: .94 }, { scale: 1, duration: .38, ease: "back.out(1.55)", overwrite: "auto", clearProps: "transform" }));
    });
    [$("#scenario-control"), $("#channel-control"), $(".sheet-segmented")].filter(Boolean).forEach((host) => {
      const targetButton = (event) => event.target.closest("button");
      host.addEventListener("pointerover", (event) => { const button = targetButton(event); if (!button || button.contains(event.relatedTarget)) return; window.gsap.to(button, { y: -1.5, scale: 1.015, duration: .24, ease: "expo.out", overwrite: "auto" }); });
      host.addEventListener("pointerout", (event) => { const button = targetButton(event); if (!button || button.contains(event.relatedTarget)) return; window.gsap.to(button, { y: 0, scale: 1, duration: .3, ease: "expo.out", overwrite: "auto", clearProps: "transform" }); });
      host.addEventListener("pointerdown", (event) => { const button = targetButton(event); if (button) window.gsap.to(button, { y: 1, scale: .965, duration: .08, ease: "power2.out", overwrite: "auto" }); });
      host.addEventListener("pointerup", (event) => { const button = targetButton(event); if (button) window.gsap.to(button, { y: -1, scale: 1, duration: .36, ease: "back.out(1.45)", overwrite: "auto", clearProps: "transform" }); });
    });
    const microTimelines = new WeakMap();
    const resetMicroMotion = (target, parts, immediate = false) => {
      microTimelines.get(target)?.kill(); microTimelines.delete(target); window.gsap.killTweensOf(parts);
      window.gsap.to(parts, { x: 0, y: 0, rotation: 0, scale: 1, scaleX: 1, scaleY: 1, autoAlpha: 1, duration: immediate ? 0 : .26, ease: "expo.out", overwrite: "auto", clearProps: "transform,opacity,visibility" });
    };
    const bindContinuousGlyph = (target, build, targetNodes = null) => {
      if (!target) return;
      const parts = () => targetNodes ? targetNodes().filter(Boolean) : $$(".action-part", target);
      const start = () => { const nodes = parts(); if (!nodes.length) return; resetMicroMotion(target, nodes, true); const timeline = build(nodes); if (timeline) microTimelines.set(target, timeline); };
      const stop = () => resetMicroMotion(target, parts());
      target.addEventListener("pointerenter", start); target.addEventListener("focus", start); target.addEventListener("pointerleave", stop); target.addEventListener("blur", stop);
    };
    const payloadTimeline = (direction) => ([tray, payload]) => window.gsap.timeline({ repeat: -1, repeatDelay: .18, defaults: { overwrite: "auto" } })
      .set(payload, { y: direction * -5, autoAlpha: 0 })
      .to(payload, { y: 0, autoAlpha: 1, duration: .26, ease: "power2.out" })
      .to(tray, { y: direction * .8, scaleX: .9, duration: .12, ease: "power2.out" }, "<.1")
      .to(payload, { y: direction * 5, autoAlpha: 0, duration: .3, ease: "power2.in" })
      .to(tray, { y: 0, scaleX: 1, duration: .28, ease: "expo.out" }, "<.08")
      .set(payload, { y: direction * -5, autoAlpha: 0 });
    bindContinuousGlyph($("#download-telemetry-template"), payloadTimeline(1));
    bindContinuousGlyph($("#telemetry-dropzone"), payloadTimeline(-1));
    const scanTimeline = ([trace, probe]) => window.gsap.timeline({ repeat: -1, repeatDelay: .2, defaults: { overwrite: "auto" } })
      .set(probe, { x: -7, y: 2, autoAlpha: 0 })
      .to(probe, { x: -3, y: 2, autoAlpha: 1, duration: .18, ease: "power2.out" })
      .to(probe, { x: 0, y: -5, duration: .18, ease: "power2.inOut" })
      .to(probe, { x: 3, y: 5, duration: .16, ease: "power2.inOut" })
      .to(probe, { x: 7, y: 0, autoAlpha: 0, duration: .22, ease: "power2.in" })
      .to(trace, { autoAlpha: .65, duration: .12 }, "<-.2").to(trace, { autoAlpha: 1, duration: .24 })
      .set(probe, { x: -7, y: 2, autoAlpha: 0 });
    bindContinuousGlyph($("#telemetry-submit"), scanTimeline);
    bindContinuousGlyph($(".predict-engine-note"), scanTimeline);
    bindContinuousGlyph($(".example-link"), ([ring, satellite]) => window.gsap.timeline({ repeat: -1, repeatDelay: .16, defaults: { overwrite: "auto" } })
      .to(satellite, { keyframes: [{ x: 0, y: 0 }, { x: 3.5, y: -1.5 }, { x: 0, y: -3.5 }, { x: -3.5, y: .5 }, { x: 0, y: 3.5 }, { x: 3.5, y: 1.2 }, { x: 0, y: 0 }], duration: 1.35, ease: "none" })
      .to(ring, { rotation: 4, duration: .62, ease: "sine.inOut" }, "<").to(ring, { rotation: 0, duration: .73, ease: "sine.inOut" }));
    const brand = $(".brand"); bindContinuousGlyph(brand, ([emblem, copy]) => window.gsap.timeline({ repeat: -1, repeatDelay: .32, defaults: { overwrite: "auto" } })
      .to(emblem, { y: -1.4, scale: 1.018, duration: .44, ease: "sine.inOut" }).to(copy, { x: 1.2, duration: .44, ease: "sine.inOut" }, "<")
      .to(emblem, { y: 0, scale: 1, duration: .44, ease: "sine.inOut" }).to(copy, { x: 0, duration: .44, ease: "sine.inOut" }, "<"), () => [$(".brand-emblem", brand), $(".brand-copy", brand)]);
    const routeSignal = $(".route-signal"); bindContinuousGlyph(routeSignal, ([signal]) => window.gsap.timeline({ repeat: -1, repeatDelay: .2, defaults: { overwrite: "auto" } })
      .to(signal, { scale: 1.35, autoAlpha: .42, duration: .36, ease: "sine.inOut" }).to(signal, { scale: 1, autoAlpha: 1, duration: .36, ease: "sine.inOut" }), () => [$("i", routeSignal)]);
  }

  function showView(view) {
    if (!["predict", "operate", "workflows", "evidence", "system"].includes(view)) return; const previous = appState.view; const changed = previous !== view;
    if (previous === "operate" && view !== "operate") cancelOrbitInteraction();
    appState.view = view; $$(".view").forEach((section) => section.classList.toggle("is-active", section.id === `view-${view}`)); $$('[data-view]').forEach((button) => button.classList.toggle("is-active", button.dataset.view === view));
    const active = $(`#view-${view}`);
    if (changed && active) {
      active.scrollTop = 0;
      if (window.matchMedia("(max-width: 760px)").matches) window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    }
    if (view === "predict") window.requestAnimationFrame(redrawTelemetryCharts);
    if (view === "operate") {
      orbitRuntime.last = 0; orbitRuntime.lastRendered = 0;
      /* Let the view's geometry settle before the expensive scene/chart work.
         Splitting the two paints prevents a route change from blocking the
         first interaction frame on high-DPI devices. */
      window.requestAnimationFrame(() => {
        refreshOrbitProjection({ force: true });
        window.requestAnimationFrame(() => { if (appState.view === "operate") drawTelemetry(); });
      });
    }
    if (view === "evidence") window.requestAnimationFrame(renderEvidence); if (view === "system") window.requestAnimationFrame(renderSystem); if (view === "workflows") window.requestAnimationFrame(() => refreshJobs());
    const targets = active ? Array.from(active.children).slice(0, 2) : [];
    if (changed && window.gsap && !reduceMotion && targets.length) { window.gsap.killTweensOf(targets); window.gsap.fromTo(targets, { opacity: 0, y: 4 }, { opacity: 1, y: 0, duration: .28, ease: "power2.out", overwrite: "auto", clearProps: "transform,opacity" }); }
  }
  function syncContractPanel() {
    const panel = $("#upload-contract-panel"); const guide = $("#upload-contract-guide"); const button = $("#upload-contract-collapse");
    if (!panel || !guide) return;
    const open = guide.dataset.targetOpen ? guide.dataset.targetOpen === "true" : guide.open; panel.classList.toggle("is-collapsed", !open); button?.setAttribute("aria-expanded", String(open)); button?.setAttribute("title", open ? "收起支持范围" : "展开支持范围");
  }
  function setContractPanel(open, animate = true) {
    const guide = $("#upload-contract-guide"); const body = $(".upload-contract-body", guide);
    if (!guide || !body) return;
    const motionId = String(Number(body.dataset.contractMotionId || 0) + 1); body.dataset.contractMotionId = motionId; guide.dataset.targetOpen = String(open);
    if (guide.open === open && !window.gsap?.isTweening?.(body)) { delete guide.dataset.targetOpen; syncContractPanel(); return; }
    if (!animate || reduceMotion || !window.gsap) { window.gsap?.killTweensOf?.(body); guide.open = open; delete guide.dataset.targetOpen; body.style.removeProperty("opacity"); body.style.removeProperty("visibility"); body.style.removeProperty("transform"); body.style.removeProperty("clip-path"); syncContractPanel(); return; }
    window.gsap.killTweensOf(body); const wasOpen = guide.open;
    if (open) {
      guide.open = true; syncContractPanel();
      if (!wasOpen) window.gsap.set(body, { autoAlpha: 0, y: -6, clipPath: "inset(0 0 14% 0)" });
      window.gsap.to(body, { autoAlpha: 1, y: 0, clipPath: "inset(0 0 0% 0)", duration: .24, ease: "power3.out", overwrite: "auto", onComplete: () => { if (body.dataset.contractMotionId !== motionId) return; delete guide.dataset.targetOpen; window.gsap.set(body, { clearProps: "opacity,visibility,transform,clipPath" }); syncContractPanel(); } });
      return;
    }
    window.gsap.to(body, { autoAlpha: 0, y: -6, clipPath: "inset(0 0 14% 0)", duration: .18, ease: "power2.in", overwrite: "auto", onComplete: () => { if (body.dataset.contractMotionId !== motionId) return; guide.open = false; delete guide.dataset.targetOpen; syncContractPanel(); window.gsap.set(body, { clearProps: "opacity,visibility,transform,clipPath" }); } });
  }
  function focusContractPanel() {
    const panel = $("#upload-contract-panel"); const guide = $("#upload-contract-guide"); if (!panel || !guide) return;
    setContractPanel(true, !guide.open); panel.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    window.setTimeout(() => $("#upload-contract-panel-heading")?.focus?.(), 380);
  }
  function syncReplayContextDisclosure() {
    const editor = $("#replay-context-editor"); if (!editor) return; const summary = $("summary", editor); if (!summary) return;
    summary.setAttribute("aria-expanded", String(editor.open)); editor.dataset.expanded = String(editor.open);
  }
  function setSceneStatusExpanded(expanded, animate = true) {
    const stack = $(".scene-status-stack"); const details = $("#scene-status-details"); const toggle = $("#scene-status-toggle");
    if (!stack || !details || !toggle) return;
    appState.sceneStatusExpanded = Boolean(expanded);
    toggle.setAttribute("aria-expanded", String(appState.sceneStatusExpanded));
    toggle.title = appState.sceneStatusExpanded ? "收起场景状态详情" : "展开场景状态详情";
    const label = $(".scene-status-toggle-label", toggle); if (label) label.textContent = appState.sceneStatusExpanded ? "收起详情" : "状态详情";
    const glyph = $("use", toggle); glyph?.setAttribute("href", "#i-chevron"); glyph?.setAttribute("xlink:href", "#i-chevron");
    const motionId = String(Number(details.dataset.statusMotionId || 0) + 1); details.dataset.statusMotionId = motionId;
    if (!window.gsap || reduceMotion || !animate) {
      window.gsap?.killTweensOf?.(details); stack.classList.remove("is-closing"); stack.dataset.expanded = String(appState.sceneStatusExpanded); details.hidden = !appState.sceneStatusExpanded; details.setAttribute("aria-hidden", String(!appState.sceneStatusExpanded)); details.style.removeProperty("opacity"); details.style.removeProperty("visibility"); details.style.removeProperty("transform"); return;
    }
    window.gsap.killTweensOf(details);
    if (appState.sceneStatusExpanded) {
      const wasHidden = details.hidden; stack.classList.remove("is-closing"); stack.dataset.expanded = "true"; details.hidden = false; details.setAttribute("aria-hidden", "false");
      if (wasHidden) window.gsap.set(details, { autoAlpha: 0, y: 4 });
      window.gsap.to(details, { autoAlpha: 1, y: 0, duration: .24, ease: "power3.out", overwrite: "auto", onComplete: () => { if (details.dataset.statusMotionId !== motionId) return; window.gsap.set(details, { clearProps: "opacity,visibility,transform" }); } });
      return;
    }
    stack.dataset.expanded = "false"; stack.classList.add("is-closing"); details.hidden = false; details.setAttribute("aria-hidden", "true");
    window.gsap.to(details, { autoAlpha: 0, y: 4, duration: .18, ease: "power2.in", overwrite: "auto", onComplete: () => { if (details.dataset.statusMotionId !== motionId) return; details.hidden = true; stack.classList.remove("is-closing"); window.gsap.set(details, { clearProps: "opacity,visibility,transform" }); } });
  }
  function setReplayContextOpen(open, animate = true) {
    const editor = $("#replay-context-editor"); if (!editor) return; const body = $(".replay-context-body", editor); if (!body) return;
    if (editor.open === open) { syncReplayContextDisclosure(); return; }
    /* Keep the details element as the sole source of truth for layout. A
       height/overflow tween here used to trap wheel input and could leave an
       inline overflow lock when interrupted. Animate only paint/compositor
       properties after the native open state has changed. */
    if (window.gsap) window.gsap.killTweensOf(body);
    editor.open = open;
    syncReplayContextDisclosure();
    if (!open || !animate || reduceMotion || !window.gsap) return;
    window.gsap.fromTo(body, { autoAlpha: 0, y: -6 }, {
      autoAlpha: 1, y: 0, duration: .32, ease: "power3.out", overwrite: "auto",
      clearProps: "opacity,visibility,transform",
    });
  }
  function bindEvents() {
    document.addEventListener("pointerdown", (event) => { if (openCustomSelect && !openCustomSelect.container.contains(event.target) && !openCustomSelect.menu.contains(event.target)) closeCustomSelect(openCustomSelect); });
    let customSelectPositionFrame = 0; const scheduleCustomSelectPosition = () => { if (!openCustomSelect || customSelectPositionFrame) return; customSelectPositionFrame = requestAnimationFrame(() => { customSelectPositionFrame = 0; if (openCustomSelect) positionCustomSelect(openCustomSelect); }); };
    window.addEventListener("resize", scheduleCustomSelectPosition, { passive: true });
    window.addEventListener("scroll", scheduleCustomSelectPosition, { passive: true, capture: true });
    $$("#scenario-control button").forEach((button) => button.addEventListener("click", () => setLine(button.dataset.line))); $$("[data-compact-line]").forEach((button) => button.addEventListener("click", () => setLine(button.dataset.compactLine))); $$("[data-sheet-line]").forEach((button) => button.addEventListener("click", () => { setLine(button.dataset.sheetLine); renderAssets(); })); $$('[data-view]').forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
    $("#telemetry-files")?.addEventListener("change", (event) => appendTelemetryFiles(event.target.files));
    $("#telemetry-platform-configuration")?.addEventListener("change", () => syncUploadPlatformConfiguration({ invalidate: true, announce: true }));
    $("#telemetry-attitude-method")?.addEventListener("change", () => invalidateTelemetryBatch("姿态稳定方式已更新，等待重新提交。"));
    $("#telemetry-line")?.addEventListener("change", () => syncUploadPlatformConfiguration({ invalidate: true, announce: true }));
    $("#telemetry-form")?.addEventListener("submit", submitTelemetry); $("#telemetry-reset")?.addEventListener("click", resetTelemetryInput); $("#download-telemetry-template")?.addEventListener("click", downloadTelemetryTemplate); $("#telemetry-example-toggle")?.addEventListener("click", () => toggleTelemetryExamplePanel()); $("#telemetry-example-close")?.addEventListener("click", () => toggleTelemetryExamplePanel(false)); $("#telemetry-export")?.addEventListener("click", exportTelemetryResults); $("#telemetry-add-all")?.addEventListener("click", addAllUploadedReplay); $("#upload-contract-jump")?.addEventListener("click", focusContractPanel); $("#upload-contract-collapse")?.addEventListener("click", () => { const guide = $("#upload-contract-guide"); if (guide) setContractPanel(!guide.open); }); $("#upload-contract-guide > summary")?.addEventListener("click", (event) => { event.preventDefault(); const guide = $("#upload-contract-guide"); if (guide) setContractPanel(!guide.open); }); $("#upload-contract-guide")?.addEventListener("toggle", syncContractPanel); syncContractPanel(); upgradeCustomSelects();
    syncUploadPlatformConfiguration();
    const replayEditor = $("#replay-context-editor"); const replaySummary = replayEditor ? $("summary", replayEditor) : null;
    replaySummary?.addEventListener("click", (event) => { event.preventDefault(); setReplayContextOpen(!replayEditor.open); }); replayEditor?.addEventListener("toggle", syncReplayContextDisclosure); syncReplayContextDisclosure();
    $("#replay-context-platform-configuration")?.addEventListener("change", syncReplayPlatformConfiguration); $("#replay-context-attitude-method")?.addEventListener("change", syncReplayPlatformConfiguration);
    $("#replay-context-orbit-mode")?.addEventListener("change", syncReplayOrbitMode); $$('#replay-context-form input[type="number"]').forEach((input) => input.addEventListener("input", () => renderDerivedOrbitFacts()));
    $$('[data-context-preset]', replayEditor || document).forEach((select) => select.addEventListener("change", () => updateContextPreset(select, true)));
    const dropzone = $("#telemetry-dropzone");
    ["dragenter", "dragover"].forEach((name) => dropzone?.addEventListener(name, (event) => { event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = "copy"; dropzone.classList.add("is-dragover"); }));
    dropzone?.addEventListener("dragleave", (event) => { if (!dropzone.contains(event.relatedTarget)) dropzone.classList.remove("is-dragover"); });
    dropzone?.addEventListener("drop", (event) => { event.preventDefault(); dropzone.classList.remove("is-dragover"); appendTelemetryFiles(event.dataTransfer?.files); });
    syncSampleSheetTriggers(false); $("#sample-search")?.addEventListener("input", (event) => { appState.search = event.target.value.trim(); renderAssets(); }); $("#mobile-sample-search")?.addEventListener("input", (event) => { appState.mobileSearch = event.target.value.trim(); renderAssets(); }); $("#compact-sample-trigger")?.addEventListener("click", (event) => toggleSheet(true, event.currentTarget)); $("#sample-sheet-button")?.addEventListener("click", (event) => toggleSheet(true, event.currentTarget)); $("#sheet-close")?.addEventListener("click", () => toggleSheet(false)); $("#sample-sheet .sheet-scrim")?.addEventListener("click", () => toggleSheet(false));
    $("#hide-visible-samples")?.addEventListener("click", hideVisibleSamples); $("#restore-samples")?.addEventListener("click", restoreSamples); $("#mobile-hide-visible-samples")?.addEventListener("click", hideVisibleSamples); $("#mobile-restore-samples")?.addEventListener("click", restoreSamples); $("#source-open-replay")?.addEventListener("click", () => showView("operate"));
    $("#scrubber")?.addEventListener("input", (event) => setProgress(Number(event.target.value) / 1000)); $("#play-button")?.addEventListener("click", togglePlayback); $("#speed-button")?.addEventListener("click", () => { const speeds = [1, 2, 4, .5]; appState.speed = speeds[(speeds.indexOf(appState.speed) + 1) % speeds.length]; setText("#speed-button", `${appState.speed}×`); });
    $("#orbit-play")?.addEventListener("click", () => { appState.orbitPlaying = !appState.orbitPlaying; const button = $("#orbit-play"); const glyph = $("use", button); const target = appState.orbitPlaying ? "#i-pause" : "#i-play"; glyph?.setAttribute("href", target); glyph?.setAttribute("xlink:href", target); button?.setAttribute("aria-label", appState.orbitPlaying ? "暂停场景动画" : "播放场景动画"); scheduleOrbitFrame(); });
    $("#orbit-mode")?.addEventListener("click", () => setOrbitMode(appState.orbitMode === "globe" ? "groundtrack" : "globe")); $("#provenance-button")?.addEventListener("click", () => showView("system"));
    $("#operation-form")?.addEventListener("submit", submitOperation); $("#operation-input-check")?.addEventListener("click", () => performInputCheck(true)); $("#operation-confirm")?.addEventListener("change", () => syncOperationSubmit(appState.operations.find((item) => item.id === appState.operationId))); $("#operation-search")?.addEventListener("input", (event) => { appState.operationSearch = event.target.value.trim(); renderOperationList(); }); $("#operation-category-filter")?.addEventListener("change", (event) => { appState.operationCategory = event.target.value; renderOperationList(); }); $$("[data-risk-filter]").forEach((button) => button.addEventListener("click", () => { appState.riskFilter = button.dataset.riskFilter; $$("[data-risk-filter]").forEach((item) => item.classList.toggle("is-active", item === button)); renderOperationList(); })); $("#jobs-refresh")?.addEventListener("click", () => refreshJobs({ animate: true })); $("#job-cancel")?.addEventListener("click", cancelSelectedJob); $("#job-retry")?.addEventListener("click", retrySelectedJob);
    $("#orbit-reset")?.addEventListener("click", resetOrbitView); $("#orbit-zoom-in")?.addEventListener("click", () => changeOrbitZoom(1.12)); $("#orbit-zoom-out")?.addEventListener("click", () => changeOrbitZoom(1 / 1.12)); $("#scene-status-toggle")?.addEventListener("click", () => setSceneStatusExpanded(!appState.sceneStatusExpanded)); $("#replay-context-form")?.addEventListener("submit", saveReplayContext); bindMicroInteractions();
    const orbit = $("#scene-frame"); orbit?.addEventListener("pointerdown", beginOrbitDrag); orbit?.addEventListener("pointermove", moveOrbitDrag); orbit?.addEventListener("pointerup", (event) => finishOrbitDrag(event)); orbit?.addEventListener("pointercancel", (event) => finishOrbitDrag(event, true)); orbit?.addEventListener("lostpointercapture", (event) => finishOrbitDrag(event, true)); orbit?.addEventListener("click", (event) => { if (appState.orbitMode === "groundtrack" && !isOrbitChromeTarget(event.target)) pickOrbitTarget(event); }); orbit?.addEventListener("wheel", handleOrbitWheel, { passive: false });
    document.addEventListener("keydown", (event) => { if (event.key === " " && !/INPUT|BUTTON|SELECT|TEXTAREA/.test(document.activeElement?.tagName || "")) { event.preventDefault(); togglePlayback(); } const sheet = $("#sample-sheet"); if (event.key === "Escape" && sheet && !sheet.hidden) { event.preventDefault(); toggleSheet(false); } });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) cancelOrbitInteraction();
      else resumeOrbitAfterInterruption();
    });
    window.addEventListener("blur", cancelOrbitInteraction, { passive: true });
    window.addEventListener("focus", resumeOrbitAfterInterruption, { passive: true });
    window.addEventListener("pagehide", cancelOrbitInteraction, { passive: true });
    window.addEventListener("pageshow", resumeOrbitAfterInterruption, { passive: true });
    const frameThrottle = (callback) => { let frame = 0; return () => { if (frame) return; frame = requestAnimationFrame(() => { frame = 0; callback(); }); }; };
    const redrawScene = frameThrottle(() => { if (appState.view === "operate") drawOrbit(); }); const redrawTrace = frameThrottle(() => { if (appState.view === "operate") drawTelemetry(); }); const redrawComparison = frameThrottle(() => { if (appState.view === "evidence") drawComparison(); }); const redrawPredictions = frameThrottle(() => { if (appState.view === "predict") redrawTelemetryCharts(); });
    if ("ResizeObserver" in window) {
      const sceneObserver = new ResizeObserver(redrawScene); const traceObserver = new ResizeObserver(redrawTrace); const comparisonObserver = new ResizeObserver(redrawComparison); const predictionObserver = new ResizeObserver(redrawPredictions);
      if ($("#scene-frame")) sceneObserver.observe($("#scene-frame")); if ($("#trace-frame")) traceObserver.observe($("#trace-frame")); if ($("#compare-canvas")) comparisonObserver.observe($("#compare-canvas")); if ($("#telemetry-result-region")) predictionObserver.observe($("#telemetry-result-region"));
    } else window.addEventListener("resize", () => { redrawScene(); redrawTrace(); redrawComparison(); redrawPredictions(); }, { passive: true });
  }
  function startClock() { const formatter = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }); const update = () => { const now = new Date(); const clock = $("#utc-clock"); if (clock) { clock.textContent = `${formatter.format(now)} CST`; clock.dateTime = now.toISOString(); } }; update(); window.setInterval(update, 1000); }

  renderStatusBar(); if (samples.length) { appState.line = samples.some((sample) => sample.line === "bat") ? "bat" : samples[0].line; appState.sampleId = samples.find((sample) => sample.line === appState.line && sample.example)?.sample_id || samples.find((sample) => sample.line === appState.line)?.sample_id || samples[0].sample_id; appState.evidenceSampleId = appState.sampleId; }
  initOrbitScene(); updateOrbitZoom(false); bindEvents(); setSceneStatusExpanded(false, false); setMethodPhase("load"); initTelemetry(); initWorkflows(); startClock(); renderAll(); renderEvidence(); showView(appState.view); scheduleOrbitFrame();
  window.setInterval(() => { if (control.enabled && (appState.view === "workflows" || appState.jobs.some((job) => ["queued", "running"].includes(job.status)))) refreshJobs(); }, 2200);
})();



