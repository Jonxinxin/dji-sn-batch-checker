"""Adapt slider-captcha-lab's incremental puzzle tracks to DJI's end-stop rail."""
from __future__ import annotations

import math
import random

from ._vendor.slider_captcha_lab.human_track import generate_drag

TRACK_SOURCE = "slider-captcha-lab/human_track.py"
TRACK_VARIANT = "feedback"
MAX_Y_OFFSET = 4.0
MIN_DYNAMIC_DURATION = 0.9
MAX_DYNAMIC_DURATION = 3.1


def drag_path(distance: float, duration: float | None = None, rng=None):
    """Return absolute CSS offsets and elapsed seconds, validated before input.

    Use the upstream feedback generator, including its variable sampling,
    pauses and acceleration phases. A bar slider has no puzzle overshoot:
    finish at the furthest point, scale it to the rail end and remove the
    terminal pullback. Small corrections earlier in the track are preserved.
    """
    if not math.isfinite(distance) or not 8 <= distance <= 2000:
        raise ValueError("滑块距离超出支持范围")
    if duration is not None and (not math.isfinite(duration) or not 0.5 <= duration <= 4):
        raise ValueError("拖动时长超出支持范围")
    rng = rng if rng is not None else random.Random()
    total = duration if duration is not None else rng.triangular(MIN_DYNAMIC_DURATION, MAX_DYNAMIC_DURATION, 1.65)
    increments = generate_drag(distance, total_s=total, rng=rng, variant=TRACK_VARIANT)
    x = y = elapsed = 0.0
    points = []
    for dx, dy, dt_ms in increments:
        if not all(math.isfinite(value) for value in (dx, dy, dt_ms)) or dt_ms <= 0:
            raise ValueError("轨迹生成器返回了无效坐标或时间")
        x, y, elapsed = x + dx, y + dy, elapsed + dt_ms / 1000
        if not all(math.isfinite(value) for value in (x, y, elapsed)):
            raise ValueError("轨迹累计坐标或时间无效")
        points.append((x, y, elapsed))
    if not points or max(point[0] for point in points) <= 0:
        raise ValueError("轨迹生成器没有返回有效拖动距离")
    peak_index = max(range(len(points)), key=lambda index: points[index][0])
    points = points[:peak_index + 1]
    peak_x, _, end_time = points[-1]
    y_scale = min(1.0, MAX_Y_OFFSET / max(MAX_Y_OFFSET, max(abs(point[1]) for point in points)))
    # feedback rewrites its tail, changing the raw duration. Restore the
    # chosen total while retaining the relative durations of individual events.
    path = [(max(0.0, min(distance, px * distance / peak_x)), py * y_scale, timestamp * total / end_time)
            for px, py, timestamp in points]
    # Exact endpoint and duration, without accumulating per-frame rounding.
    path[-1] = (distance, path[-1][1], total)
    return tuple(path)
