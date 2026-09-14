/*
 * The trajectory generator adapts generate_drag from
 * kangleyao/slider-captcha-lab (MIT), including its feedback trajectory.
 * Attribution, license, and adaptation notes: third_party/slider-captcha-lab/.
 */
(function initSlider(root, factory) {
  const api = factory();
  root.DJISNSlider = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createSlider() {
  "use strict";

  function sliderError(code, message) {
    return Object.assign(new Error(message), { code });
  }

  function isAllowedPage(value) {
    try {
      const url = new URL(value);
      return url.origin === "https://repair.dji.com" && url.pathname.startsWith("/device/");
    } catch (_) {
      return false;
    }
  }

  function generateDrag(distance, { rng = Math.random, totalSeconds, events, variant = "feedback" } = {}) {
    if (!Number.isFinite(distance) || distance < 8 || distance > 2000) {
      throw sliderError("invalid_distance", "滑块距离不在支持范围内");
    }
    const uniform = (min, max) => min + rng() * (max - min);
    const integer = (min, max) => Math.floor(uniform(min, max + 1));
    if (!["feedback", "baseline_current"].includes(variant)) {
      throw sliderError("invalid_variant", "滑块轨迹类型无效");
    }
    const feedback = variant === "feedback";
    const count = events == null ? integer(130, 200) : events;
    if (!Number.isInteger(count) || count < 20 || count > 300) {
      throw sliderError("invalid_events", "滑块轨迹采样数无效");
    }
    const decay = uniform(4, 12);
    const frontRamp = feedback ? uniform(0.08, 0.25) : uniform(0.02, 0.12);
    const yAmplitude = feedback ? uniform(8, 18) : uniform(2, 6.5);
    const yDrift = feedback ? (rng() < 0.75 ? -uniform(25, 90) : uniform(10, 35)) : 0;
    const yCycles = uniform(0.5, 1.1);
    if (totalSeconds == null) {
      const choice = rng();
      totalSeconds = feedback
        ? (choice < 0.2 ? uniform(0.9, 1.25) : choice < 0.65 ? uniform(1.25, 1.9) : uniform(1.9, 3.1))
        : (choice < 0.55 ? uniform(0.55, 0.85) : choice < 0.85 ? uniform(0.9, 1.4) : uniform(1.4, 1.9));
    }
    if (!Number.isFinite(totalSeconds) || totalSeconds < 0.3 || totalSeconds > 4) {
      throw sliderError("invalid_duration", "滑块轨迹时长无效");
    }

    const samples = 400;
    const integrated = [0];
    let previousVelocity = 0;
    for (let i = 1; i < samples; i += 1) {
      const u = i / (samples - 1);
      const velocity = u < frontRamp ? u / frontRamp
        : Math.pow(1 - (u - frontRamp) / (1 - frontRamp), decay);
      integrated.push(integrated[i - 1] + (previousVelocity + velocity) * 0.5 / (samples - 1));
      previousVelocity = velocity;
    }
    const integral = integrated[samples - 1];

    // Upstream time grids: AR(1) baseline and long-tail feedback sampling.
    const weights = [];
    let lastWeight = 1;
    for (let i = 1; i < count; i += 1) {
      if (feedback) {
        const choice = rng();
        lastWeight = choice < 0.62 ? uniform(0.0035, 0.0065)
          : choice < 0.78 ? uniform(0.008, 0.018)
            : choice < 0.9 ? uniform(0.025, 0.06) : uniform(0.07, 0.22);
      } else {
        lastWeight = 0.82 * lastWeight + 0.18 * uniform(0.88, 1.12);
      }
      weights.push(lastWeight);
    }
    const weightSum = weights.reduce((sum, value) => sum + value, 0);
    const times = [0];
    for (const weight of weights) times.push(times[times.length - 1] + totalSeconds * weight / weightSum);
    times[times.length - 1] = totalSeconds;
    if (!feedback && rng() < 0.15) {
      const pauseIndex = integer(2, times.length - 3);
      const pause = uniform(0.03, 0.09);
      for (let i = 1; i < times.length; i += 1) {
        times[i] += (i >= pauseIndex ? pause : 0) - pause * i / (times.length - 1);
      }
    }

    let previousX = 0;
    let previousY = 0;
    let track = [];
    for (let i = 1; i < times.length; i += 1) {
      const u = Math.max(0, Math.min(1, times[i] / totalSeconds));
      const sample = u * (samples - 1);
      const left = Math.min(samples - 2, Math.floor(sample));
      const progress = integrated[left] + (integrated[left + 1] - integrated[left]) * (sample - left);
      const x = distance * progress / integral;
      const y = yDrift * u + yAmplitude * Math.pow(Math.max(0, Math.sin(Math.PI * u)), 1.7) * Math.sin(2 * Math.PI * yCycles * u);
      track.push([Math.max(0.01, x - previousX), y - previousY,
        Math.max(feedback ? 1.2 : 0.1, (times[i] - times[i - 1]) * 1000)]);
      previousX = x;
      previousY = y;
    }

    // Preserve the endpoint after the upstream minimum displacement clamp.
    const mainCount = Math.max(2, Math.floor(track.length * 0.8));
    const sum = track.reduce((total, point) => total + point[0], 0);
    const mainSum = track.slice(0, mainCount).reduce((total, point) => total + point[0], 0);
    const scale = (mainSum + distance - sum) / mainSum;
    for (let i = 0; i < mainCount; i += 1) track[i][0] *= scale;
    if (!feedback) return track;

    // Port of the upstream feedback stages. A bar slider has an end stop,
    // so the puzzle-specific final overshoot/back-pull is deliberately omitted.
    let live = track.length;
    while (live > 10 && track[live - 1][0] <= 0.05) live -= 1;
    const dead = track.slice(live).reduce((total, point) => total.map((v, i) => v + point[i]), [0, 0, 0]);
    track = track.slice(0, live);
    track[track.length - 1][1] += dead[1];
    track[track.length - 1][2] += dead[2];
    const liveDistance = track.reduce((total, point) => total + point[0], 0);
    for (const point of track) point[0] *= distance / liveDistance;

    if (track.length >= 10 && distance > 10) {
      const split = Math.floor(track.length * uniform(0.45, 0.6));
      const frontBudget = distance * uniform(0.15, 0.3);
      const frontSum = track.slice(0, split).reduce((total, point) => total + point[0], 0);
      for (let i = 0; i < split; i += 1) track[i][0] *= frontBudget / frontSum;
      const backY = track.slice(split).reduce((total, point) => total + point[1], 0);
      const remaining = distance - frontBudget;
      const valleyCount = integer(3, 6);
      const flickCount = integer(16, 28);
      const coastCount = integer(5, 10);
      const valleyDistance = remaining * uniform(0.02, 0.05);
      const flickDistance = remaining * uniform(0.86, 0.94);
      const coastDistance = remaining - valleyDistance - flickDistance;
      const peak = uniform(0.35, 0.6);
      const sigma = uniform(0.07, 0.11);
      const shape = Array.from({ length: flickCount }, (_, i) =>
        0.05 + Math.exp(-Math.pow(i / (flickCount - 1) - peak, 2) / (2 * sigma * sigma)));
      const shapeSum = shape.reduce((total, value) => total + value, 0);
      const back = [
        ...Array.from({ length: valleyCount }, () => [valleyDistance / valleyCount, 0, uniform(40, 110)]),
        ...shape.map((value) => [flickDistance * value / shapeSum, 0, uniform(2.5, 6)]),
        ...Array.from({ length: coastCount }, () => [coastDistance / coastCount, 0, uniform(6, 16)])
      ];
      for (const point of back) point[1] = backY * point[0] / remaining;
      track.splice(split, track.length - split, ...back);

      const grid = uniform(0.42, 0.58);
      for (const point of track) {
        let quantized = Math.round(point[0] / grid) * grid;
        if (quantized > 0.5 && rng() < 0.35) quantized += (rng() < 0.5 ? -1 : 1) * grid;
        point[0] = Math.max(0.01, quantized);
      }
      const quantizedSum = track.reduce((total, point) => total + point[0], 0);
      for (const point of track) point[0] *= distance / quantizedSum;
      const zeroCount = Math.max(1, Math.floor(track.length * uniform(0.03, 0.07)));
      for (let i = 0; i < zeroCount; i += 1) {
        const index = integer(2, track.length - 3);
        track[index + 1][0] += track[index][0];
        track[index][0] = 0;
      }
      const corrections = integer(1, 3);
      for (let i = 0; i < corrections; i += 1) {
        const index = integer(Math.floor(track.length * 0.25), Math.floor(track.length * 0.8));
        const pull = uniform(0.8, 2.2);
        track[index][0] -= pull * 0.6;
        track[index + 1][0] -= pull * 0.4;
        track[index + 2][0] += pull;
      }
      const pauseChoice = rng();
      const pauses = pauseChoice < 0.4 ? 0 : pauseChoice < 0.8 ? 1 : 2;
      for (let i = 0; i < pauses; i += 1) {
        track[integer(Math.floor(track.length * 0.4), Math.floor(track.length * 0.85))][2] += uniform(60, 160);
      }
    }

    // Remove subpixel end movements and preserve the requested total distance.
    let tailMs = 0;
    for (let i = track.length - 1; i >= 0 && tailMs < 200; i -= 1) {
      tailMs += track[i][2];
      if (Math.abs(track[i][0]) > 0 && Math.abs(track[i][0]) < 1) {
        track[i][0] = rng() < 0.6 ? 0 : Math.sign(track[i][0]) * 1.15;
      }
    }
    const positive = track.reduce((total, point) => total + Math.max(0, point[0]), 0);
    const negative = track.reduce((total, point) => total + Math.min(0, point[0]), 0);
    for (const point of track) if (point[0] > 0) point[0] *= (distance - negative) / positive;
    let cumulative = 0;
    let boundedPrevious = 0;
    for (let i = 0; i < track.length; i += 1) {
      cumulative += track[i][0];
      const bounded = i === track.length - 1 ? distance : Math.max(0, Math.min(distance, cumulative));
      track[i][0] = bounded - boundedPrevious;
      boundedPrevious = bounded;
    }
    // Resampling can lengthen a short requested duration. Keep it below the
    // solver's deadline without changing the relative sampling intervals.
    const duration = track.reduce((total, point) => total + point[2], 0);
    if (duration > 4000) for (const point of track) point[2] *= 4000 / duration;
    return track;
  }

  // This function must be self-contained: the background worker also executes
  // its source in the target page through CDP. It never changes page state.
  function inspect() {
    const visible = (node) => {
      if (!node) return false;
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
    };
    const rectOf = (node) => {
      const rect = node.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    };
    const input = document.querySelector('input[placeholder*="序列号"]');
    const snapshot = {
      kind: "missing",
      url: location.href,
      sn: input ? input.value.trim().toUpperCase() : "",
      viewport: { width: innerWidth, height: innerHeight },
      message: "等待官网加载滑块"
    };
    const wrapper = document.getElementById("aliyunCaptcha-sliding-wrapper")
      || document.getElementById("aliyunCaptcha-window-embed");
    const picture = document.getElementById("aliyunCaptcha-img");
    const puzzle = document.getElementById("aliyunCaptcha-puzzle");
    if (visible(picture) || visible(puzzle)) {
      return { ...snapshot, kind: "unsupported", message: "官网出现拼图验证，请手动完成后继续" };
    }
    if (!visible(wrapper)) return snapshot;
    const text = (wrapper.innerText || "").replace(/\s+/g, " ").trim();
    if (/验证通过|验证成功|校验通过|校验成功|verification\s+(?:passed|successful)|verified\s+successfully/i.test(text)) {
      return { ...snapshot, kind: "passed", message: "滑块验证已通过" };
    }
    const code = (text.match(/\b[FE]\d{3,5}\b/) || [""])[0];
    if (code || /验证失败|校验失败|点击重试|网络异常|稍后再试|verification\s+failed|try\s+again/i.test(text)) {
      return { ...snapshot, kind: "failed", code, message: text.slice(0, 180) || "官网未通过滑块验证" };
    }
    const handle = document.getElementById("aliyunCaptcha-sliding-slider");
    const rail = document.getElementById("aliyunCaptcha-sliding-body") || wrapper;
    if (!visible(handle) || !visible(rail)) return { ...snapshot, kind: "loading" };
    const handleRect = rectOf(handle);
    const railRect = rectOf(rail);
    const x = handleRect.x + handleRect.width / 2;
    const y = handleRect.y + handleRect.height / 2;
    const endX = railRect.x + railRect.width - handleRect.width / 2;
    const hit = document.elementFromPoint(x, y);
    const endHit = document.elementFromPoint(endX, y);
    if (!hit || !handle.contains(hit) || !endHit || !rail.contains(endHit)) {
      return { ...snapshot, kind: "covered",
        coveredByPanel: [hit, endHit].some((node) => node && node.id === "dji-sn-batch-checker-host"),
        message: "滑块被遮挡，请将滑块完整显示后重试" };
    }
    return { ...snapshot, kind: "slider", handle: handleRect, rail: railRect, message: text };
  }

  function getGeometry(snapshot) {
    const { handle, rail, viewport } = snapshot;
    for (const rect of [handle, rail]) {
      if (!rect || ![rect.x, rect.y, rect.width, rect.height].every(Number.isFinite)
        || rect.width <= 0 || rect.height <= 0) {
        throw sliderError("invalid_geometry", "无法读取滑块的位置");
      }
    }
    if (!viewport || ![viewport.width, viewport.height].every(Number.isFinite)) {
      throw sliderError("invalid_geometry", "无法读取网页显示范围");
    }
    const x = handle.x + handle.width / 2;
    const y = handle.y + handle.height / 2;
    const endX = rail.x + rail.width - handle.width / 2;
    const distance = endX - x;
    if (handle.width < 12 || handle.width > 160 || handle.height < 12 || handle.height > 160
      || rail.width > 2200 || distance < 8 || distance > 2000
      || x < 0 || endX >= viewport.width || y < 8 || y >= viewport.height - 8
      || handle.x < rail.x - 2 || handle.y < rail.y - 2
      || handle.y + handle.height > rail.y + rail.height + 2) {
      throw sliderError("invalid_geometry", "请将滑块完整显示在页面内后重试");
    }
    return { x, y, endX, distance, viewport };
  }

  async function solve(options) {
    const { attach, detach, send, read, sn } = options;
    const rng = options.rng || Math.random;
    const sleep = options.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    const now = options.now || (() => performance.now());
    const cancelled = options.cancelled || (() => false);
    const uniform = (min, max) => min + rng() * (max - min);
    let attached = false;
    let pressed = false;
    let mouseX = 0;
    let mouseY = 0;
    const started = now();
    let result;

    function check() {
      if (cancelled()) throw sliderError("cancelled", "自动滑块已停止");
      if (now() - started > 18000) throw sliderError("timeout", "自动滑块超时，请手动完成或重试");
    }

    async function snapshot() {
      check();
      const state = await read();
      check();
      if (!state || !isAllowedPage(state.url) || state.sn !== sn) {
        throw sliderError("page_changed", "页面或当前 SN 已改变，自动滑块已停止");
      }
      return state;
    }

    async function move(type, x, y, buttons) {
      check();
      mouseX = x;
      mouseY = y;
      const params = { type, x, y, buttons, button: buttons || type === "mouseReleased" ? "left" : "none", pointerType: "mouse" };
      if (type === "mousePressed" || type === "mouseReleased") params.clickCount = 1;
      // Set before sending: a rejected/timed-out command might still reach Chrome.
      if (type === "mousePressed") pressed = true;
      await send("Input.dispatchMouseEvent", params);
      if (type === "mouseReleased") pressed = false;
    }

    function requireSlider(state) {
      if (state.kind !== "slider") {
        throw sliderError(state.kind || "missing", state.message || "没有找到可自动操作的滑块");
      }
      return getGeometry(state);
    }

    try {
      check();
      await attach();
      attached = true;
      const initial = await snapshot();
      if (initial.kind === "passed") {
        result = { ok: true, passed: true };
      } else {
        let geometry = requireSlider(initial);
        const approachX = Math.max(4, geometry.x - uniform(60, 120));
        const approachY = Math.max(8, Math.min(geometry.viewport.height - 8, geometry.y + uniform(-20, 20)));
        await move("mouseMoved", approachX, approachY, 0);
        await sleep(uniform(200, 400));
        for (let i = 1; i <= 8; i += 1) {
          await move("mouseMoved", approachX + (geometry.x - approachX) * i / 8,
            approachY + (geometry.y - approachY) * i / 8, 0);
          await sleep(uniform(10, 20));
        }
        await sleep(uniform(250, 500));

        // Recheck after approaching so a scroll, reflow, or changed SN cannot
        // turn the press into a click on a different element.
        const beforePress = await snapshot();
        if (beforePress.kind === "passed") {
          result = { ok: true, passed: true };
        } else {
          const currentGeometry = requireSlider(beforePress);
          if (Math.abs(currentGeometry.x - geometry.x) > 2 || Math.abs(currentGeometry.y - geometry.y) > 2
            || Math.abs(currentGeometry.endX - geometry.endX) > 2) {
            throw sliderError("layout_changed", "滑块位置发生变化，请保持页面稳定后重试");
          }
          geometry = currentGeometry;
          const track = generateDrag(geometry.distance, { rng });
          let pathY = 0;
          let minY = 0;
          let maxY = 0;
          for (const point of track) {
            pathY += point[1];
            minY = Math.min(minY, pathY);
            maxY = Math.max(maxY, pathY);
          }
          const yScale = Math.min(1, minY < 0 ? (geometry.y - 8) / -minY : 1,
            maxY > 0 ? (geometry.viewport.height - 8 - geometry.y) / maxY : 1);
          await move("mousePressed", geometry.x, geometry.y, 1);
          await sleep(uniform(50, 110));
          const dragStart = now();
          let scheduled = 0;
          let dx = 0;
          let dy = 0;
          for (const [stepX, stepY, delay] of track) {
            scheduled += delay;
            dx += stepX;
            dy += stepY;
            await move("mouseMoved", geometry.x + dx, geometry.y + dy * yScale, 1);
            const remaining = scheduled - (now() - dragStart);
            if (remaining > 0) await sleep(remaining);
          }
          await sleep(uniform(10, 40));
          await move("mouseReleased", geometry.endX, mouseY, 0);

          const verdictStart = now();
          while (now() - verdictStart < 8000) {
            await sleep(200);
            const verdict = await snapshot();
            if (verdict.kind === "passed") {
              result = { ok: true, passed: true };
              break;
            }
            if (["failed", "unsupported"].includes(verdict.kind)) {
              throw sliderError(verdict.kind, verdict.message);
            }
          }
          if (!result) throw sliderError("unconfirmed", "官网尚未确认验证通过，请手动完成或重试");
        }
      }
    } catch (error) {
      result = { ok: false, passed: false, code: error.code || "debugger_error", message: error.message || "自动滑块未完成" };
    } finally {
      // Cancellation and errors must release a pressed mouse before detaching.
      if (attached && pressed) {
        try {
          await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: mouseX, y: mouseY,
            button: "left", buttons: 0, clickCount: 1, pointerType: "mouse" });
        } catch (_) { /* The tab may have closed or detached already. */ }
      }
      if (attached) {
        try { await detach(); } catch (_) { /* Chrome may have detached on navigation. */ }
      }
    }
    return result;
  }

  return { isAllowedPage, generateDrag, inspect, getGeometry, solve };
});
