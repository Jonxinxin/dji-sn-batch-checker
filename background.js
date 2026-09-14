"use strict";

importScripts("slider.js");

const slider = globalThis.DJISNSlider;
const jobs = new Map();

function withTimeout(promise, milliseconds = 3000) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error("浏览器调试响应超时"), { code: "timeout" })), milliseconds);
    })
  ]).finally(() => clearTimeout(timer));
}

function validSender(sender) {
  return sender.id === chrome.runtime.id && sender.frameId === 0
    && Number.isInteger(sender.tab && sender.tab.id)
    && slider.isAllowedPage(sender.url) && slider.isAllowedPage(sender.tab.url);
}

async function solveInTab(message, sender) {
  const tabId = sender.tab.id;
  if (jobs.size) return { ok: false, code: "busy", message: "已有页面正在自动验证，请稍后重试" };
  const target = { tabId };
  const job = { id: message.requestId, cancelled: false, attached: false, finishing: false };
  jobs.set(tabId, job);
  try {
    return await slider.solve({
      sn: message.sn,
      cancelled: () => job.cancelled,
      attach: async () => {
        const tab = await chrome.tabs.get(tabId);
        if (!slider.isAllowedPage(tab.url)) throw new Error("当前标签页已离开大疆查询页面");
        try {
          const attachment = chrome.debugger.attach(target, "1.3").then(async () => {
            job.attached = true;
            // A timeout only ends our wait; Chrome may still attach later.
            // Release that late connection even after this job has finished.
            if (job.finishing) {
              try { await withTimeout(chrome.debugger.detach(target)); }
              finally { job.attached = false; }
            }
          });
          await withTimeout(attachment);
        } catch (error) {
          if (/another debugger|already attached|devtools/i.test(error.message)) {
            throw new Error("当前页面的开发者工具正在占用调试连接，请关闭后重试，或手动完成滑块");
          }
          throw error;
        }
        await withTimeout(chrome.debugger.sendCommand(target, "Page.enable"));
      },
      detach: async () => {
        job.finishing = true;
        if (job.attached) await withTimeout(chrome.debugger.detach(target));
        job.attached = false;
      },
      send: (method, params) => withTimeout(chrome.debugger.sendCommand(target, method, params)),
      read: async () => {
        const response = await withTimeout(chrome.debugger.sendCommand(target, "Runtime.evaluate", {
          expression: `(${slider.inspect.toString()})()`,
          returnByValue: true
        }));
        if (response.exceptionDetails || !response.result || !response.result.value) {
          throw new Error("无法读取当前页面的滑块状态");
        }
        return response.result.value;
      }
    });
  } finally {
    // Also cover an attach that succeeded before Page.enable failed.
    job.finishing = true;
    if (job.attached) {
      try { await withTimeout(chrome.debugger.detach(target)); } catch (_) { /* Already closed. */ }
    }
    jobs.delete(tabId);
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !["DJI_SLIDER_SOLVE", "DJI_SLIDER_CANCEL"].includes(message.type)) return false;
  if (!validSender(sender)) {
    sendResponse({ ok: false, code: "invalid_sender", message: "滑块请求来源无效" });
    return false;
  }
  if (message.type === "DJI_SLIDER_CANCEL") {
    const job = jobs.get(sender.tab.id);
    if (job && job.id === message.requestId) job.cancelled = true;
    sendResponse({ ok: true });
    return false;
  }
  if (!/^[A-Z0-9]{14,20}$/.test(message.sn || "")
    || typeof message.requestId !== "string" || !/^[a-zA-Z0-9-]{8,80}$/.test(message.requestId)) {
    sendResponse({ ok: false, code: "invalid_request", message: "滑块请求参数无效" });
    return false;
  }
  solveInTab(message, sender).then(sendResponse, (error) => sendResponse({
    ok: false, code: "debugger_error", message: error.message || "浏览器未能启动自动滑块"
  }));
  return true;
});

chrome.debugger.onDetach.addListener((source) => {
  const job = jobs.get(source.tabId);
  if (job) {
    job.attached = false;
    if (!job.finishing) job.cancelled = true;
  }
});

chrome.debugger.onEvent.addListener((source, method, params) => {
  const job = jobs.get(source.tabId);
  if (job && method === "Page.frameNavigated" && params.frame && !params.frame.parentId) job.cancelled = true;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  const job = jobs.get(tabId);
  if (job) job.cancelled = true;
});
