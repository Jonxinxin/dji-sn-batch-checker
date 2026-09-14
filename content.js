(function startDJISNBatchChecker() {
  "use strict";

  if (document.getElementById("dji-sn-batch-checker-host")) return;

  const core = globalThis.DJISNCore;
  const slider = globalThis.DJISNSlider;
  const STORAGE_KEY = "djiSnBatchCheckerStateV1";
  const QUERY_URL = "https://repair.dji.com/device/detail?re=cn&lang=zh-CN";
  const STATUS_LABELS = {
    pending: "待查询",
    ready: "待验证",
    querying: "查询中",
    activated: "已激活",
    inactive: "未激活",
    unknown: "待确认",
    error: "查询失败",
    skipped: "已跳过"
  };

  const host = document.createElement("div");
  host.id = "dji-sn-batch-checker-host";
  document.documentElement.appendChild(host);
  const shadow = host.attachShadow({ mode: "open" });
  const stylesheet = document.createElement("link");
  stylesheet.rel = "stylesheet";
  stylesheet.href = chrome.runtime.getURL("panel.css");
  shadow.appendChild(stylesheet);

  const shell = document.createElement("div");
  shadow.appendChild(shell);

  let state = {
    items: [],
    currentId: null,
    running: false,
    collapsed: false,
    importText: "",
    autoAdvance: true,
    autoSlider: true
  };
  let observerTimer = 0;
  let toastTimer = 0;
  let navigationTimer = 0;
  let autoAdvanceTimer = 0;
  let lastAutoSubmittedId = "";
  let sliderTimer = 0;
  let activeSliderAttempt = null;
  let sliderHandledItemId = "";
  let sliderWaitStartedAt = 0;
  let lastFilledItemId = "";
  let stateLoaded = false;
  let restoringItemId = "";

  const icons = {
    minimize: '<svg class="sn-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg>',
    list: '<svg class="sn-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/></svg>',
    plus: '<svg class="sn-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    play: '<svg class="sn-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 4 13 8-13 8z"/></svg>',
    pause: '<svg class="sn-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14M16 5v14"/></svg>',
    next: '<svg class="sn-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>',
    capture: '<svg class="sn-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7V4h3M17 4h3v3M20 17v3h-3M7 20H4v-3"/><path d="M8 12h8M12 8v8"/></svg>',
    download: '<svg class="sn-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M5 21h14"/></svg>',
    retry: '<svg class="sn-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6v5h-5"/><path d="M19 11a8 8 0 1 0 1 5"/></svg>',
    trash: '<svg class="sn-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v5M14 11v5"/></svg>'
  };

  function uid() {
    return globalThis.crypto && crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>'"]/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
    })[char]);
  }

  function getCurrent() {
    return state.items.find((item) => item.id === state.currentId) || null;
  }

  function findNextPending() {
    return state.items.find((item) => item.status === "pending") || null;
  }

  function loadState() {
    return new Promise((resolve) => {
      chrome.storage.local.get(STORAGE_KEY, (result) => {
        if (result && result[STORAGE_KEY]) state = { ...state, ...result[STORAGE_KEY] };
        resolve();
      });
    });
  }

  function saveState() {
    chrome.storage.local.set({ [STORAGE_KEY]: state });
  }

  function patchItem(id, patch) {
    state.items = state.items.map((item) => item.id === id ? { ...item, ...patch } : item);
    saveState();
    render();
  }

  function showToast(message, tone = "") {
    clearTimeout(toastTimer);
    const old = shell.querySelector(".sn-toast");
    if (old) old.remove();
    const toast = document.createElement("div");
    toast.className = `sn-toast ${tone}`.trim();
    toast.setAttribute("role", "status");
    toast.textContent = message;
    const panel = shell.querySelector(".sn-panel");
    if (panel) panel.appendChild(toast);
    toastTimer = window.setTimeout(() => toast.remove(), 3200);
  }

  function officialInput() {
    return document.querySelector('input[placeholder*="序列号"]');
  }

  function officialQueryButton() {
    const scoped = document.querySelector(".btn-step button");
    if (scoped && /查询/.test(scoped.textContent || "")) return scoped;
    return Array.from(document.querySelectorAll("button")).find((button) =>
      /^\s*查询\s*$/.test(button.textContent || "")
    ) || null;
  }

  function setNativeInputValue(input, value) {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.dispatchEvent(new Event("blur", { bubbles: true }));
  }

  function fillCurrentSn() {
    const item = getCurrent();
    const input = officialInput();
    if (!item || !input) return false;
    if (lastFilledItemId !== item.id || core.normalizeSn(input.value) !== item.sn) {
      cancelAutoSlider(true);
      lastAutoSubmittedId = "";
      sliderWaitStartedAt = Date.now();
      lastFilledItemId = item.id;
    }
    if (input.value !== item.sn) setNativeInputValue(input, item.sn);
    if (item.status === "pending") patchItem(item.id, {
      status: "ready", message: state.autoSlider ? "等待自动完成滑块验证" : "等待手动完成滑块验证"
    });
    input.scrollIntoView({ block: "center", behavior: "auto" });
    input.focus({ preventScroll: true });
    scheduleAutoSlider();
    return true;
  }

  function sendSliderCancel(attempt) {
    try {
      chrome.runtime.sendMessage({ type: "DJI_SLIDER_CANCEL", requestId: attempt.requestId }, () => {
        void chrome.runtime.lastError;
      });
    } catch (_) { /* The extension may have been reloaded. */ }
  }

  function cancelAutoSlider(resetHandled = false) {
    clearTimeout(sliderTimer);
    const attempt = activeSliderAttempt;
    activeSliderAttempt = null;
    if (attempt) sendSliderCancel(attempt);
    if (resetHandled) sliderHandledItemId = "";
  }

  function stopPendingAutomation() {
    restoringItemId = "";
    clearTimeout(autoAdvanceTimer);
    clearInterval(navigationTimer);
    cancelAutoSlider(true);
  }

  function scheduleAutoSlider(delay = 650) {
    clearTimeout(sliderTimer);
    sliderTimer = window.setTimeout(tryAutoSlider, delay);
  }

  function requestSlider(attempt) {
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        sendSliderCancel(attempt);
        reject(new Error("自动滑块响应超时，请手动完成或重试"));
      }, 26000);
      try {
        chrome.runtime.sendMessage({ type: "DJI_SLIDER_SOLVE", requestId: attempt.requestId, sn: attempt.sn }, (response) => {
          clearTimeout(timer);
          const error = chrome.runtime.lastError;
          if (error) reject(new Error("自动滑块连接不可用，请重新加载扩展并刷新官网页面"));
          else resolve(response || { ok: false, message: "自动滑块未返回结果" });
        });
      } catch (error) {
        clearTimeout(timer);
        reject(error);
      }
    });
  }

  async function tryAutoSlider() {
    const item = getCurrent();
    if (!stateLoaded || !state.running || !state.autoSlider || !item || item.status !== "ready"
      || activeSliderAttempt || sliderHandledItemId === item.id || lastFilledItemId !== item.id) return;
    const input = officialInput();
    if (!input || core.normalizeSn(input.value) !== item.sn) return;
    const snapshot = slider.inspect();
    if (snapshot.kind === "passed") {
      autoSubmitAfterVerification();
      return;
    }
    if (["missing", "loading"].includes(snapshot.kind) && Date.now() - sliderWaitStartedAt < 15000) return;
    sliderHandledItemId = item.id;
    if (snapshot.kind !== "slider") {
      patchItem(item.id, { message: snapshot.message || "未找到可自动操作的滑块，请手动完成" });
      return;
    }

    const attempt = { requestId: uid(), itemId: item.id, sn: item.sn };
    activeSliderAttempt = attempt;
    patchItem(item.id, { message: "正在自动完成滑块验证…" });
    let response;
    try {
      response = await requestSlider(attempt);
    } catch (error) {
      sendSliderCancel(attempt);
      response = { ok: false, message: error.message };
    }
    // A pause, removal, manual query, or a new SN invalidates this result.
    if (activeSliderAttempt !== attempt) return;
    activeSliderAttempt = null;
    const current = getCurrent();
    if (!current || current.id !== item.id || current.status !== "ready" || !state.running) return;
    if (response.ok && verificationPassed()) {
      patchItem(item.id, { message: state.autoAdvance ? "滑块已通过，等待官网查询" : "滑块已通过，请点击官网“查询”" });
      autoSubmitAfterVerification();
    } else {
      const reason = response.message || "官网尚未确认验证通过";
      patchItem(item.id, { message: `${reason}；可手动完成后继续` });
      showToast("自动滑块未完成，可重试或手动拖动", "error");
    }
  }

  function retrySlider() {
    const item = getCurrent();
    if (!item || item.status !== "ready" || activeSliderAttempt) return;
    const snapshot = slider.inspect();
    if (snapshot.kind === "failed") {
      // The Aliyun failure text is also its official refresh control.
      const refresh = document.getElementById("aliyunCaptcha-sliding-text");
      if (refresh) refresh.click();
    }
    sliderHandledItemId = "";
    sliderWaitStartedAt = Date.now();
    scheduleAutoSlider();
  }

  function pageResultText() {
    const item = getCurrent();
    const sn = item ? item.sn : "";
    const selectors = [
      ".device-page .active-wrapper",
      ".device-page .detail-wrapper",
      ".device-detail",
      ".device-info",
      "main"
    ];
    const candidates = [];
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      const text = core.normalizeResultText(element && element.innerText);
      if (text && core.hasConcreteResult(text, sn)) candidates.push(text);
    }

    if (sn) {
      const leafNodes = Array.from(document.querySelectorAll("span, p, div"))
        .filter((element) => element.childElementCount === 0 && (element.textContent || "").toUpperCase().includes(sn));
      for (const leaf of leafNodes.slice(0, 8)) {
        let element = leaf;
        for (let depth = 0; element && depth < 8; depth += 1, element = element.parentElement) {
          const text = core.normalizeResultText(element.innerText);
          if (text && text.length <= 10000 && core.hasConcreteResult(text, sn)) candidates.push(text);
        }
      }
    }

    const bodyText = core.normalizeResultText(document.body && document.body.innerText);
    if (bodyText && core.hasConcreteResult(bodyText, sn)) candidates.push(bodyText.slice(0, 10000));
    candidates.sort((left, right) => left.length - right.length);
    return candidates[0] || "";
  }

  function captureResult(force = false) {
    const item = getCurrent();
    if (!item) return false;
    const text = pageResultText();
    if (!force && !core.hasConcreteResult(text, item.sn)) return false;
    if (!text) {
      showToast("当前页面没有可记录的结果", "error");
      return false;
    }
    const parsed = core.classifyResult(text);
    const shouldAdvance = state.running && state.autoAdvance && parsed.status !== "unknown";
    cancelAutoSlider();
    patchItem(item.id, {
      ...parsed,
      checkedAt: new Date().toLocaleString("zh-CN", { hour12: false }),
      message: parsed.status === "unknown" ? "结果已记录，请人工确认状态" : "结果已记录"
    });
    state.running = shouldAdvance;
    saveState();
    render();
    showToast(parsed.status === "unknown"
      ? "已保存官网结果，请确认激活状态"
      : shouldAdvance ? "结果已记录，正在进入下一条" : "查询结果已记录");
    if (shouldAdvance) {
      clearTimeout(autoAdvanceTimer);
      autoAdvanceTimer = window.setTimeout(() => {
        if (state.running && state.autoAdvance && state.currentId === item.id) moveNext(false);
      }, 900);
    }
    return true;
  }

  function verificationPassed() {
    return slider.inspect().kind === "passed";
  }

  function autoSubmitAfterVerification() {
    const item = getCurrent();
    if (!state.running || !state.autoAdvance || !item || item.status !== "ready") return false;
    if (activeSliderAttempt) return false;
    const input = officialInput();
    if (!input || core.normalizeSn(input.value) !== item.sn) return false;
    if (lastAutoSubmittedId === item.id || !verificationPassed()) return false;
    const button = officialQueryButton();
    if (!button || button.disabled) return false;
    lastAutoSubmittedId = item.id;
    button.click();
    return true;
  }

  function detectError() {
    const item = getCurrent();
    if (!item || item.status !== "querying") return false;
    const errorNodes = Array.from(document.querySelectorAll(".el-message--error, .el-message--warning"));
    const visibleError = errorNodes.find((node) => node.offsetParent !== null && (node.textContent || "").trim());
    if (!visibleError) return false;
    const message = core.normalizeResultText(visibleError.textContent).slice(0, 500);
    if (/滑块|验证|请输入/.test(message)) {
      lastAutoSubmittedId = "";
      patchItem(item.id, { status: "ready", message });
      showToast(message, "error");
      return true;
    }
    patchItem(item.id, {
      status: "error",
      message,
      details: message,
      checkedAt: new Date().toLocaleString("zh-CN", { hour12: false })
    });
    stopPendingAutomation();
    state.running = false;
    saveState();
    render();
    showToast(`官网返回：${message}`, "error");
    return true;
  }

  function restoreQueuedItem() {
    const item = getCurrent();
    if (!state.running || !item || item.id !== restoringItemId) {
      restoringItemId = "";
      return;
    }
    if (item.status === "querying" && captureResult(false)) {
      restoringItemId = "";
      return;
    }
    if (core.isFinalStatus(item.status)) {
      // A reload may happen between saving the result and advancing. Wait
      // for the site to render before trying to return to its query form.
      if (!officialInput() && !pageResultText()) return;
      restoringItemId = "";
      if (state.autoAdvance) moveNext(false);
      else pauseQueue();
    } else if (officialInput()) {
      restoringItemId = "";
      // A full reload loses an in-flight query. The visible form needs a
      // fresh verification, even if the saved item still says querying.
      if (item.status === "querying") patchItem(item.id, { status: "pending" });
      fillCurrentSn();
    }
  }

  function checkPage() {
    if (!stateLoaded) return;
    if (restoringItemId) {
      restoreQueuedItem();
      if (restoringItemId) return;
    }
    if (detectError()) return;
    const item = getCurrent();
    if (item && item.status === "querying" && captureResult(false)) return;
    if (!autoSubmitAfterVerification()) tryAutoSlider();
  }

  function observePage() {
    const observer = new MutationObserver(() => {
      clearTimeout(observerTimer);
      observerTimer = window.setTimeout(checkPage, 450);
    });
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ["class", "style", "disabled", "aria-disabled", "aria-hidden"] });
    // Covers SDK transitions that do not add DOM nodes, and delayed page loads.
    window.setInterval(checkPage, 1000);
  }

  function handleOfficialQueryClick(event) {
    const button = event.target.closest && event.target.closest("button");
    if (!button || button !== officialQueryButton() || button.disabled) return;
    const item = getCurrent();
    if (!item) return;
    const input = officialInput();
    if (!input || core.normalizeSn(input.value) !== item.sn) {
      pauseQueue();
      showToast("官网输入的 SN 与当前队列不一致，队列已暂停", "error");
      return;
    }
    cancelAutoSlider();
    lastAutoSubmittedId = item.id;
    patchItem(item.id, { status: "querying", message: "正在等待官网返回结果" });
  }

  function addItems() {
    const textarea = shell.querySelector("[data-import]");
    const text = textarea ? textarea.value : state.importText;
    const parsed = core.parseSnList(text, state.items.map((item) => item.sn));
    if (parsed.valid.length) {
      state.items.push(...parsed.valid.map((sn) => ({ id: uid(), sn, status: "pending", addedAt: Date.now() })));
      state.importText = "";
      saveState();
      render();
    }
    const notes = [];
    if (parsed.valid.length) notes.push(`已加入 ${parsed.valid.length} 条`);
    if (parsed.duplicate.length) notes.push(`忽略 ${parsed.duplicate.length} 条重复`);
    if (parsed.invalid.length) notes.push(`${parsed.invalid.length} 条格式无效（需 14–20 位字母或数字）`);
    showToast(notes.join("；") || "没有识别到 SN", parsed.invalid.length ? "error" : "");
  }

  function beginQueue() {
    let item = getCurrent();
    if (!item || core.isFinalStatus(item.status)) item = findNextPending();
    if (!item) {
      showToast("没有待查询的 SN");
      return;
    }
    stopPendingAutomation();
    state.currentId = item.id;
    state.running = true;
    saveState();
    render();
    if (!fillCurrentSn()) navigateToFormAndFill();
  }

  function pauseQueue() {
    stopPendingAutomation();
    state.running = false;
    const item = getCurrent();
    if (item && item.status === "ready") item.message = "已暂停，继续后可重新尝试滑块验证";
    saveState();
    render();
  }

  function navigateToFormAndFill() {
    clearInterval(navigationTimer);
    if (!officialInput()) {
      const queryAnother = Array.from(document.querySelectorAll("button"))
        .find((button) => /^\s*查询其他设备\s*$/.test(button.textContent || ""));
      if (queryAnother) queryAnother.click();
      else history.back();
    }
    let attempts = 0;
    const navigatingId = state.currentId;
    navigationTimer = window.setInterval(() => {
      if (!state.running || state.currentId !== navigatingId) {
        clearInterval(navigationTimer);
        return;
      }
      attempts += 1;
      if (fillCurrentSn()) {
        clearInterval(navigationTimer);
      } else if (attempts >= 20) {
        clearInterval(navigationTimer);
        location.assign(QUERY_URL);
      }
    }, 350);
  }

  function moveNext(skipCurrent = false) {
    stopPendingAutomation();
    const current = getCurrent();
    if (current && skipCurrent && !core.isFinalStatus(current.status)) {
      patchItem(current.id, { status: "skipped", message: "用户跳过" });
    }
    const next = findNextPending();
    if (!next) {
      state.running = false;
      saveState();
      render();
      showToast("队列已处理完毕");
      return;
    }
    state.currentId = next.id;
    state.running = true;
    saveState();
    render();
    if (!fillCurrentSn()) navigateToFormAndFill();
  }

  function retryItem(id) {
    stopPendingAutomation();
    state.items = state.items.map((item) => item.id === id ? {
      ...item,
      status: "pending",
      activationTime: "",
      product: "",
      details: "",
      message: "",
      checkedAt: ""
    } : item);
    state.currentId = id;
    state.running = true;
    saveState();
    render();
    if (!fillCurrentSn()) navigateToFormAndFill();
  }

  function removeItem(id) {
    if (state.currentId === id) {
      stopPendingAutomation();
      state.running = false;
    }
    state.items = state.items.filter((item) => item.id !== id);
    if (state.currentId === id) state.currentId = null;
    saveState();
    render();
  }

  function clearCompleted() {
    state.items = state.items.filter((item) => !core.isFinalStatus(item.status));
    if (!state.items.some((item) => item.id === state.currentId)) {
      stopPendingAutomation();
      state.currentId = null;
      state.running = false;
    }
    saveState();
    render();
  }

  function setManualStatus(status) {
    const item = getCurrent();
    if (!item) return;
    stopPendingAutomation();
    state.running = false;
    const details = pageResultText() || item.details || "人工确认";
    patchItem(item.id, {
      status,
      details,
      checkedAt: item.checkedAt || new Date().toLocaleString("zh-CN", { hour12: false }),
      message: "状态由用户人工确认"
    });
  }

  function exportCsv() {
    if (!state.items.length) {
      showToast("当前没有可导出的数据");
      return;
    }
    const blob = new Blob([core.toCsv(state.items)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `DJI-SN-查询结果-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function statusClass(status) {
    return ["activated", "inactive", "ready", "querying", "error"].includes(status) ? status : "";
  }

  function renderItem(item) {
    const isCurrent = item.id === state.currentId;
    const meta = item.activationTime || item.product || item.message || "尚无结果";
    return `
      <div class="sn-item ${isCurrent ? "is-current" : ""}" data-item-id="${escapeHtml(item.id)}">
        <div class="sn-item-main">
          <div class="sn-item-code" title="${escapeHtml(item.sn)}">${escapeHtml(item.sn)}</div>
          <div class="sn-item-meta" title="${escapeHtml(meta)}">${escapeHtml(meta)}</div>
        </div>
        <span class="sn-status ${statusClass(item.status)}">${escapeHtml(STATUS_LABELS[item.status] || item.status)}</span>
        <div class="sn-item-actions">
          <button class="sn-icon-btn" type="button" data-action="retry" data-id="${escapeHtml(item.id)}" title="重新查询" aria-label="重新查询 ${escapeHtml(item.sn)}">${icons.retry}</button>
          <button class="sn-icon-btn" type="button" data-action="remove" data-id="${escapeHtml(item.id)}" title="删除" aria-label="删除 ${escapeHtml(item.sn)}">${icons.trash}</button>
        </div>
      </div>`;
  }

  function render() {
    const summary = core.summarize(state.items);
    const current = getCurrent();
    const finalCurrent = current && core.isFinalStatus(current.status);
    const unresolved = summary.pending + summary.unknown + summary.error;
    const currentResult = current && (current.product || current.activationTime || current.checkedAt || current.details)
      ? `<div class="sn-result">
          <dl class="sn-result-grid">
            <dt>产品</dt><dd>${escapeHtml(current.product || "未识别")}</dd>
            <dt>激活时间</dt><dd>${escapeHtml(current.activationTime || (current.status === "inactive" ? "未激活" : "未识别"))}</dd>
            <dt>记录时间</dt><dd>${escapeHtml(current.checkedAt || "—")}</dd>
          </dl>
          <div class="sn-manual" aria-label="人工修正状态">
            <button class="sn-button sn-choice" type="button" data-action="manual-status" data-status="activated">标记已激活</button>
            <button class="sn-button sn-choice" type="button" data-action="manual-status" data-status="inactive">标记未激活</button>
            <button class="sn-button sn-choice" type="button" data-action="manual-status" data-status="unknown">待确认</button>
          </div>
        </div>`
      : "";

    shell.innerHTML = `
      <button class="sn-launcher" type="button" data-action="expand" ${state.collapsed ? "" : "hidden"} title="打开 SN 批量查询助手" aria-label="打开 SN 批量查询助手">
        ${icons.list}<span class="sn-launcher-count">${unresolved}</span>
      </button>
      <section class="sn-panel" aria-label="SN 批量激活查询助手" ${state.collapsed ? "hidden" : ""}>
        <header class="sn-header">
          <div class="sn-title"><h2>SN 批量激活查询</h2><p>${state.autoSlider ? "自动滑块验证" : "人工滑块验证"} · 本地保存结果</p></div>
          <button class="sn-icon-btn" type="button" data-action="collapse" title="收起面板" aria-label="收起面板">${icons.minimize}</button>
        </header>
        <div class="sn-summary" aria-label="查询汇总">
          <div class="sn-metric"><strong>${summary.total}</strong><span>总数</span></div>
          <div class="sn-metric" data-tone="success"><strong>${summary.activated}</strong><span>已激活</span></div>
          <div class="sn-metric"><strong>${summary.inactive}</strong><span>未激活</span></div>
          <div class="sn-metric" data-tone="warning"><strong>${summary.pending}</strong><span>待处理</span></div>
        </div>
        <section class="sn-section sn-import">
          <div class="sn-section-title"><h3>导入序列号</h3><span class="sn-help">14–20 位，一行一条</span></div>
          <textarea data-import aria-label="待导入的序列号" placeholder="粘贴 SN，支持换行、空格、逗号分隔">${escapeHtml(state.importText || "")}</textarea>
          <div class="sn-row">
            <span class="sn-hint sn-grow">重复项会自动忽略</span>
            <button class="sn-button" type="button" data-action="add">${icons.plus}加入队列</button>
          </div>
        </section>
        <section class="sn-section sn-current">
          <div class="sn-current-head">
            <div class="sn-current-main">
              <div class="sn-current-label">当前 SN</div>
              <div class="sn-code" title="${escapeHtml(current ? current.sn : "")}">${escapeHtml(current ? current.sn : "尚未开始")}</div>
            </div>
            <span class="sn-status ${current ? statusClass(current.status) : ""}">${escapeHtml(current ? (STATUS_LABELS[current.status] || current.status) : "空闲")}</span>
          </div>
          <div class="sn-steps">
            <div class="sn-step"><span class="sn-step-index">1</span><span>点击开始后，SN 会自动填入大疆输入框</span></div>
            <div class="sn-step"><span class="sn-step-index">2</span><span>${state.autoSlider
              ? (state.autoAdvance ? "自动完成滑块，验证通过后自动查询" : "自动完成滑块，再点击官网“查询”")
              : (state.autoAdvance ? "手动拖动滑块，验证通过后自动查询" : "手动拖动滑块，再点击官网“查询”")}</span></div>
          </div>
          <label class="sn-auto-option">
            <input type="checkbox" data-auto-slider ${state.autoSlider ? "checked" : ""}>
            <span>自动完成滑块验证</span>
          </label>
          <label class="sn-auto-option">
            <input type="checkbox" data-auto-advance ${state.autoAdvance ? "checked" : ""}>
            <span>验证后自动查询、记录并进入下一条</span>
          </label>
          ${current && current.status === "ready" ? `<div class="sn-verification" role="status" aria-live="polite">
            <span>${escapeHtml(current.message || "等待滑块验证")}</span>
            ${state.autoSlider && state.running && !activeSliderAttempt && sliderHandledItemId === current.id && !verificationPassed()
              ? '<button class="sn-link-button" type="button" data-action="retry-slider">重试滑块</button>' : ""}
          </div>` : ""}
          <div class="sn-row">
            <button class="sn-button primary" type="button" data-action="${state.running ? "pause" : "start"}" ${state.items.length ? "" : "disabled"}>${state.running ? icons.pause + "暂停" : icons.play + "开始查询"}</button>
            <button class="sn-button" type="button" data-action="capture" ${current ? "" : "disabled"}>${icons.capture}记录当前页</button>
            <button class="sn-button" type="button" data-action="next" ${current ? "" : "disabled"}>${icons.next}${finalCurrent ? "下一条" : "跳过"}</button>
          </div>
          ${currentResult}
        </section>
        <section class="sn-list-section">
          <div class="sn-list-head"><h3>查询队列</h3><button class="sn-link-button" type="button" data-action="clear-completed">清除已完成</button></div>
          <div class="sn-list">${state.items.length ? state.items.map(renderItem).join("") : '<div class="sn-empty">先粘贴一批 SN 加入队列。<br>查询结果会保存在这里。</div>'}</div>
        </section>
        <footer class="sn-footer">
          <div class="sn-footer-note">${state.autoSlider ? "自动验证失败时，可手动拖动后继续" : state.autoAdvance ? "滑块需人工完成；其余步骤自动执行" : "当前为逐条人工确认模式"}</div>
          <button class="sn-button" type="button" data-action="export" ${state.items.length ? "" : "disabled"}>${icons.download}导出 CSV</button>
        </footer>
      </section>`;
  }

  shell.addEventListener("input", (event) => {
    if (event.target.matches("[data-import]")) {
      state.importText = event.target.value;
      saveState();
    }
  });

  shell.addEventListener("change", (event) => {
    if (event.target.matches("[data-auto-advance]")) {
      state.autoAdvance = event.target.checked;
      if (!state.autoAdvance) clearTimeout(autoAdvanceTimer);
      saveState();
      render();
      if (state.autoAdvance) autoSubmitAfterVerification();
    }
    if (event.target.matches("[data-auto-slider]")) {
      state.autoSlider = event.target.checked;
      cancelAutoSlider(true);
      sliderWaitStartedAt = Date.now();
      const item = getCurrent();
      if (item && item.status === "ready") item.message = state.autoSlider ? "等待自动完成滑块验证" : "等待手动完成滑块验证";
      saveState();
      render();
      if (state.autoSlider) scheduleAutoSlider();
    }
  });

  shell.addEventListener("keydown", (event) => {
    if (event.target.matches("[data-import]") && (event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      addItems();
    }
  });

  shell.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    if (action === "add") addItems();
    else if (action === "start") beginQueue();
    else if (action === "pause") pauseQueue();
    else if (action === "capture") captureResult(true);
    else if (action === "next") moveNext(!getCurrent() || !core.isFinalStatus(getCurrent().status));
    else if (action === "retry") retryItem(button.dataset.id);
    else if (action === "retry-slider") retrySlider();
    else if (action === "remove") removeItem(button.dataset.id);
    else if (action === "clear-completed") clearCompleted();
    else if (action === "manual-status") setManualStatus(button.dataset.status);
    else if (action === "export") exportCsv();
    else if (action === "collapse" || action === "expand") {
      state.collapsed = action === "collapse";
      saveState();
      render();
    }
  });

  document.addEventListener("click", handleOfficialQueryClick, true);
  window.addEventListener("pagehide", () => cancelAutoSlider());
  window.addEventListener("resize", () => cancelAutoSlider());
  observePage();

  loadState().then(() => {
    restoringItemId = state.running && getCurrent() ? state.currentId : "";
    stateLoaded = true;
    render();
    if (restoringItemId) window.setTimeout(checkPage, 500);
  });
})();
