const assert = require("node:assert/strict");
const core = require("../core.js");

const parsed = core.parseSnList(
  "1581F4XFC123456\n1581f4xfc123456, 12345678901234;BAD-SN",
  []
);
assert.deepEqual(parsed.valid, ["1581F4XFC123456", "12345678901234"]);
assert.deepEqual(parsed.duplicate, ["1581F4XFC123456"]);
assert.deepEqual(parsed.invalid, ["BAD-SN"]);

const activated = core.classifyResult("产品名称：DJI Mini 4 Pro\n激活时间：2025-06-18 09:30:12");
assert.equal(activated.status, "activated");
assert.equal(activated.product, "DJI Mini 4 Pro");
assert.equal(activated.activationTime, "2025-06-18 09:30:12");

const screenshotResult = core.classifyResult(
  "设备信息查询\n查看设备的激活时间和增值服务信息，并了解购买资格。\nOsmo Pocket 3\n序列号：5WTXP4N002J472\n激活时间：2026-06-27"
);
assert.equal(screenshotResult.status, "activated");
assert.equal(screenshotResult.product, "Osmo Pocket 3");
assert.equal(screenshotResult.activationTime, "2026-06-27");
assert.equal(core.hasConcreteResult(screenshotResult.details, "5WTXP4N002J472"), true);
assert.equal(core.hasConcreteResult(screenshotResult.details, "DIFFERENT-SN"), false);
assert.equal(core.hasConcreteResult("查看设备的激活时间和增值服务信息", ""), false);

const inactive = core.classifyResult("设备型号：DJI Air 3\n激活时间：--\n当前设备未激活");
assert.equal(inactive.status, "inactive");
assert.equal(inactive.product, "DJI Air 3");

const unknown = core.classifyResult("已查询到设备信息，但官网字段发生变化");
assert.equal(unknown.status, "unknown");

const csv = core.toCsv([{ sn: "ABC", status: "activated", details: '包含"引号",以及逗号' }]);
assert.match(csv, /^\ufeffSN,查询状态/);
assert.match(csv, /"包含""引号"",以及逗号"/);

const summary = core.summarize([
  { status: "activated" },
  { status: "inactive" },
  { status: "pending" },
  { status: "error" }
]);
assert.deepEqual(summary, { total: 4, activated: 1, inactive: 1, pending: 1, unknown: 0, error: 1, skipped: 0 });

console.log("core tests passed");
