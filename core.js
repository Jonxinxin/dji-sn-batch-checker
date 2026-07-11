(function initCore(root, factory) {
  const api = factory();
  root.DJISNCore = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createCore() {
  "use strict";

  const FINAL_STATUSES = new Set(["activated", "inactive", "unknown", "error", "skipped"]);

  function normalizeSn(value) {
    return String(value || "").trim().toUpperCase();
  }

  function parseSnList(text, existingSns = []) {
    const existing = new Set(existingSns.map(normalizeSn));
    const seen = new Set();
    const valid = [];
    const invalid = [];
    const duplicate = [];
    const tokens = String(text || "")
      .split(/[\s,，;；]+/)
      .map(normalizeSn)
      .filter(Boolean);

    for (const sn of tokens) {
      if (!/^[A-Z0-9]{14,20}$/.test(sn)) {
        invalid.push(sn);
      } else if (existing.has(sn) || seen.has(sn)) {
        duplicate.push(sn);
      } else {
        seen.add(sn);
        valid.push(sn);
      }
    }

    return { valid, invalid, duplicate };
  }

  function normalizeResultText(text) {
    return String(text || "")
      .replace(/\u00a0/g, " ")
      .replace(/[ \t]+/g, " ")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function classifyResult(text) {
    const details = normalizeResultText(text);
    const activationMatch = details.match(
      /激活时间\s*[:：]?\s*((?:19|20)\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)/
    );
    let productMatch = details.match(
      /(?:产品名称|设备名称|产品型号|设备型号)\s*[:：]?\s*([^\n]{1,80})/
    );
    if (!productMatch) {
      productMatch = details.match(/(?:^|\n)\s*([^\n:：]{2,80})\s*\n\s*序列号\s*[:：]/);
    }
    const explicitInactive = /未激活|尚未激活|未查询到激活|暂无激活时间|激活时间\s*[:：]?\s*(?:--|—|无|暂无|未激活)/.test(details);
    const explicitActivated = /已激活|激活成功/.test(details);

    let status = "unknown";
    if (explicitInactive) status = "inactive";
    else if (activationMatch || explicitActivated) status = "activated";

    return {
      status,
      activationTime: activationMatch ? activationMatch[1].trim() : "",
      product: productMatch ? productMatch[1].trim() : "",
      details
    };
  }

  function hasConcreteResult(text, sn = "") {
    const details = normalizeResultText(text);
    const normalizedSn = normalizeSn(sn);
    if (normalizedSn && !details.toUpperCase().includes(normalizedSn)) return false;
    return /激活时间\s*[:：]?\s*(?:(?:19|20)\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?|未激活|--|—|无|暂无)/.test(details)
      || /(?:设备|产品)?\s*(?:未激活|尚未激活)/.test(details);
  }

  function isFinalStatus(status) {
    return FINAL_STATUSES.has(status);
  }

  function summarize(items) {
    return items.reduce(
      (sum, item) => {
        sum.total += 1;
        if (item.status === "activated") sum.activated += 1;
        else if (item.status === "inactive") sum.inactive += 1;
        else if (item.status === "error") sum.error += 1;
        else if (item.status === "unknown") sum.unknown += 1;
        else if (item.status === "skipped") sum.skipped += 1;
        else sum.pending += 1;
        return sum;
      },
      { total: 0, activated: 0, inactive: 0, pending: 0, unknown: 0, error: 0, skipped: 0 }
    );
  }

  function csvEscape(value) {
    const text = String(value == null ? "" : value);
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  function toCsv(items) {
    const labels = {
      pending: "待查询",
      ready: "待验证",
      querying: "查询中",
      activated: "已激活",
      inactive: "未激活",
      unknown: "待确认",
      error: "查询失败",
      skipped: "已跳过"
    };
    const rows = [
      ["SN", "查询状态", "产品", "激活时间", "查询时间", "官网结果原文"],
      ...items.map((item) => [
        item.sn,
        labels[item.status] || item.status,
        item.product || "",
        item.activationTime || "",
        item.checkedAt || "",
        item.details || item.message || ""
      ])
    ];
    return `\ufeff${rows.map((row) => row.map(csvEscape).join(",")).join("\r\n")}`;
  }

  return { normalizeSn, parseSnList, normalizeResultText, classifyResult, hasConcreteResult, isFinalStatus, summarize, toCsv };
});
