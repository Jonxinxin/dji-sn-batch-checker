import json
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dji_checker.core import Item, QueueStore, csv_text, normalize_settings, parse_result, parse_sns
from dji_checker.native_mouse import MouseUnavailable, ViewportMapping, drag_path
from dji_checker.tracks import MAX_Y_OFFSET, MIN_DYNAMIC_DURATION, MAX_DYNAMIC_DURATION

SN = "1581F4XFC123456"


class CoreTests(unittest.TestCase):
    def test_import_normalizes_deduplicates_and_rejects_invalid_numbers(self):
        valid, invalid, duplicates = parse_sns(f"{SN.lower()}\n{SN},bad-sn;12345678901234", ["12345678901234"])
        self.assertEqual(valid, [SN])
        self.assertEqual(invalid, ["BAD-SN"])
        self.assertEqual(duplicates, [SN, "12345678901234"])

    def test_result_matches_the_entire_serial_number(self):
        text = f"DJI Mini 4 Pro\n序列号：{SN}\n激活时间：2026-06-27"
        result = parse_result(text, SN)
        self.assertEqual(result["status"], "activated")
        self.assertEqual(result["activation_time"], "2026-06-27")
        self.assertEqual(result["product"], "DJI Mini 4 Pro")
        self.assertIsNone(parse_result(text, SN[:-1]))
        self.assertIsNone(parse_result(text, "1581F4XFC654321"))

    def test_inactive_and_unrelated_page_text(self):
        result = parse_result(f"产品名称：DJI Air 3\n序列号：{SN}\n激活时间：--", SN)
        self.assertEqual(result["status"], "inactive")
        self.assertEqual(result["product"], "DJI Air 3")
        self.assertIsNone(parse_result(f"查看设备的激活时间\n{SN}", SN))

    def test_login_gated_activation_is_never_recorded_as_inactive(self):
        for message in ("登录后查看激活信息", "请先登录 DJI 账号", "登录状态已过期", "Sign in to view activation information"):
            result = parse_result(f"序列号：{SN}\n激活时间：--\n{message}", SN)
            self.assertEqual(result["status"], "login_required", message)
            self.assertNotIn("activation_time", result)

    def test_csv_quotes_multiline_fields_and_neutralizes_formulas(self):
        value = csv_text([{"sn": SN, "status": "activated", "product": "=1+1", "details": 'quoted"text\nnext,line'}])
        self.assertTrue(value.startswith("\ufeffSN,"))
        self.assertIn("'=1+1", value)
        self.assertIn('"quoted""text\nnext,line"', value)

    def test_queue_survives_reload_and_keeps_the_current_item(self):
        work = ROOT / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="python-unit-", dir=work) as folder:
            path = Path(folder) / "queue.json"
            store = QueueStore(path)
            self.assertEqual(store.add(SN), (1, 0, 0))
            item = store.next_item()
            item.status = "login_required"
            item.message = "请登录"
            item.slider_attempts = 2
            item.manual_required = True
            item.refresh_verification = True
            store.settings.update(max_slider_attempts=2, mode="browser", continuous=False)
            store.save()
            restored = QueueStore(path)
            self.assertEqual(restored.current.sn, SN)
            self.assertEqual(restored.current.status, "login_required")
            self.assertEqual(restored.current.message, "请登录")
            self.assertEqual(restored.current.slider_attempts, 2)
            self.assertTrue(restored.current.manual_required)
            self.assertTrue(restored.current.refresh_verification)
            self.assertEqual(restored.settings["max_slider_attempts"], 2)
            self.assertEqual(restored.settings["mode"], "browser")
            self.assertFalse(restored.settings["continuous"])
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_saved_settings_have_bounded_retry_limits(self):
        self.assertEqual(normalize_settings(None)["max_slider_attempts"], 3)
        for raw, expected in ((0, 1), (-10, 1), (50, 10), ("5", 5), ("invalid", 3), (None, 3)):
            self.assertEqual(normalize_settings({"max_slider_attempts": raw})["max_slider_attempts"], expected)

    def test_completed_item_is_not_queried_twice(self):
        work = ROOT / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="python-unit-", dir=work) as folder:
            store = QueueStore(Path(folder) / "queue.json")
            first, second = Item(SN, status="activated"), Item("1581F4XFC654321")
            store.items = [first, second]
            store.current_id = first.id
            self.assertEqual(store.next_item().id, second.id)


class MouseTests(unittest.TestCase):
    def test_native_mapping_handles_dpi_zoom_and_negative_monitor_coordinates(self):
        mapping = ViewportMapping(-1920, 120, 1500, 1000, 1200, 800)
        self.assertEqual(mapping.point(600, 400), (-1170, 620))
        self.assertEqual(mapping.point(0, 0), (-1920, 120))
        with self.assertRaises(MouseUnavailable):
            mapping.point(1200, 0)

    def test_path_stays_in_bounds_and_reaches_the_rail_end(self):
        for distance in (8, 40, 342, 2000):
            for seed in range(20):
                path = list(drag_path(distance, duration=1.8, rng=random.Random(seed)))
                self.assertEqual(path[-1][0], distance)
                self.assertEqual(path[-1][2], 1.8)
                previous_time = 0
                for x, y, timestamp in path:
                    self.assertTrue(all(math.isfinite(value) for value in (x, y, timestamp)))
                    self.assertTrue(0 <= x <= distance)
                    self.assertTrue(abs(y) <= MAX_Y_OFFSET)
                    self.assertGreater(timestamp, previous_time)
                    previous_time = timestamp

    def test_incremental_tracks_are_converted_and_terminal_pullback_is_removed(self):
        # Upstream returns per-event dx/dy and milliseconds, with overshoot.
        # Earlier corrections should survive while the rail endpoint remains exact.
        raw = [(6, 1, 100), (-1, 1, 200), (7, 1, 300), (-2, 1, 400)]
        with patch("dji_checker.tracks.generate_drag", return_value=raw):
            path = drag_path(10, duration=1.2)
        self.assertEqual(len(path), 3)
        self.assertAlmostEqual(path[0][0], 5.0)
        self.assertLess(path[1][0], path[0][0])
        self.assertEqual(path[-1], (10, 3, 1.2))
        self.assertAlmostEqual(path[1][2], 0.6)

    def test_default_drag_duration_varies_within_a_bounded_range(self):
        durations = [list(drag_path(342, rng=random.Random(seed)))[-1][2] for seed in range(30)]
        self.assertTrue(all(MIN_DYNAMIC_DURATION <= duration <= MAX_DYNAMIC_DURATION for duration in durations))
        self.assertGreater(max(durations) - min(durations), 0.5)
        self.assertGreater(len({round(duration, 2) for duration in durations}), 10)

    def test_speed_profile_varies_even_when_total_duration_is_fixed(self):
        def position_at(path, time_point):
            previous_x, previous_time = 0.0, 0.0
            for x, _, timestamp in path:
                if timestamp >= time_point:
                    return previous_x + (x - previous_x) * (time_point - previous_time) / (timestamp - previous_time)
                previous_x, previous_time = x, timestamp
            return path[-1][0]

        midpoints = []
        for seed in range(30):
            path = list(drag_path(342, duration=2.0, rng=random.Random(seed)))
            midpoints.append(position_at(path, 1.0))
            timestamps = [0.0] + [point[2] for point in path]
            intervals = [round(end - start, 5) for start, end in zip(timestamps, timestamps[1:])]
            self.assertGreater(len(set(intervals)), 5)
        # Changing only the sampling times would not substantially change the
        # distance covered halfway through the same two-second movement.
        self.assertGreater(max(midpoints) - min(midpoints), 342 * 0.05)

    def test_invalid_upstream_data_is_rejected_before_input(self):
        for raw in ([], [(1, 0, 0)], [(1, float("nan"), 10)], [(1, 0, float("inf"))]):
            with self.subTest(raw=raw), patch("dji_checker.tracks.generate_drag", return_value=raw):
                with self.assertRaises(ValueError):
                    drag_path(342)

    def test_invalid_distances_and_durations_never_generate_input(self):
        for distance in (0, -1, 2001, float("nan")):
            with self.assertRaises(ValueError):
                list(drag_path(distance))
        for duration in (0, -1, 10, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                list(drag_path(342, duration=duration))


if __name__ == "__main__":
    unittest.main()
