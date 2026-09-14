import queue
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dji_checker.native_mouse import Cancelled
from dji_checker.browser import LoginRequired
from dji_checker.runner import Runner

SN1, SN2 = "1581F4XFC123456", "1581F4XFC654321"


class FakePage:
    def is_closed(self):
        return False


class FakeBrowser:
    def __init__(self, folder, cancel, log):
        self.folder = Path(folder)
        self.page = FakePage()
        self.cancel = cancel
        self.passed = False
        self.fail_drag = False
        self.fail_attempts = 0
        self.failure_code = "failed"
        self.block_drag = False
        self.reject_query = False
        self.attempts = 0
        self.queries = []
        self.closed = False
        self.close_count = 0
        self.fail_close_once = False
        self.saved_on_close = None
        self.logged_in = True
        self.prepared = []
        self.refreshes = []
        self.expire_on_query = False
        self.expire_on_prepare = False
        self.saved_result = None

    def open(self):
        pass

    def show_login_window(self):
        pass

    def login_state(self):
        return {"kind": "authenticated" if self.logged_in else "required", "message": "signed in" if self.logged_in else "please log in"}

    def result(self, sn):
        return self.saved_result if self.logged_in else None

    def prepare(self, sn, refresh=False):
        if self.expire_on_prepare:
            self.logged_in = False
            raise LoginRequired()
        self.prepared.append(sn)
        if refresh:
            self.refreshes.append(sn)
        self.sn = sn
        self.passed = False

    def attempt_slider(self, sn, mode):
        self.attempts += 1
        if self.block_drag:
            self.cancel.wait(3)
            raise Cancelled("paused during drag")
        self.passed = not self.fail_drag and self.attempts > self.fail_attempts
        return {"ok": self.passed, "code": "passed" if self.passed else self.failure_code,
                "message": "verified" if self.passed else "manual verification required"}

    def _validate_page(self, sn):
        assert self.sn == sn

    def inspect(self):
        return {"kind": "passed" if self.passed else "slider"}

    def submit_and_wait(self, sn):
        self.queries.append(sn)
        if self.expire_on_query:
            self.logged_in = False
            return {"status": "login_required", "message": "please log in"}
        if self.reject_query:
            return {"status": "manual", "message": "verification expired"}
        return {"status": "inactive" if sn == SN2 else "activated", "product": "DJI Mini 4 Pro", "activation_time": "2026-06-27"}

    def diagnostic(self, reason):
        return Path("diagnostic.json")

    def close(self):
        self.close_count += 1
        path = self.folder / "queue.json"
        if path.exists():
            self.saved_on_close = json.loads(path.read_text(encoding="utf-8"))
        if self.fail_close_once:
            self.fail_close_once = False
            raise RuntimeError("test browser close failure")
        self.closed = True
        self.page = None


class RunnerTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / ".work"
        work.mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(prefix="python-runner-", dir=work)
        self.events = queue.Queue()
        self.browser = None
        self.browsers = []
        self.completions = []
        self.runner = Runner(Path(self.folder.name), self.events, browser_factory=self.make_browser, retry_delay=0.05)
        self.runner.start()

    def make_browser(self, folder, cancel, log):
        self.browser = FakeBrowser(folder, cancel, log)
        self.browsers.append(self.browser)
        return self.browser

    def completion_events(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                return self.completions
            if kind == "complete":
                self.completions.append(value)

    def tearDown(self):
        self.runner.command("close")
        self.runner.join(timeout=4)
        self.assertFalse(self.runner.is_alive())
        self.folder.cleanup()

    def wait_for(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.025)
        self.fail("Runner did not reach the expected state")

    def start_queue(self, text=SN1, max_attempts=1, **settings):
        self.runner.command("add", text)
        self.runner.command("start", {"mode": "browser", "max_slider_attempts": max_attempts, **settings})

    def test_batch_records_two_results_and_stops(self):
        self.start_queue(f"{SN1}\n{SN2}")
        self.wait_for(lambda: len(self.runner.store.items) == 2 and self.runner.store.items[-1].status == "inactive" and not self.runner.running)
        self.assertEqual(self.browser.queries, [SN1, SN2])
        self.assertEqual(self.browser.attempts, 2)
        self.assertTrue(all(item.checked_at for item in self.runner.store.items))
        self.wait_for(lambda: len(self.completion_events()) == 1)
        self.assertIsNone(self.runner.browser)
        self.assertTrue(self.browser.closed)
        self.assertEqual(self.browser.close_count, 1)
        self.assertEqual([item["status"] for item in self.browser.saved_on_close["items"]], ["activated", "inactive"])
        self.assertEqual(self.completions[0]["total"], 2)
        self.assertEqual(self.completions[0]["counts"], {"activated": 1, "inactive": 1})
        self.assertTrue(self.completions[0]["browser_closed"])
        self.assertEqual(self.runner.login["kind"], "closed")

    def test_noncontinuous_last_item_closes_browser_and_notifies(self):
        self.start_queue(continuous=False)
        self.wait_for(lambda: len(self.completion_events()) == 1)
        self.assertEqual(self.browser.queries, [SN1])
        self.assertTrue(self.browser.closed)
        self.assertFalse(self.runner.running)

    def test_noncontinuous_intermediate_stop_keeps_browser_until_last_item(self):
        self.start_queue(f"{SN1}\n{SN2}", continuous=False)
        self.wait_for(lambda: len(self.runner.store.items) == 2 and self.runner.store.items[0].status == "activated" and not self.runner.running)
        self.assertEqual(self.runner.store.items[1].status, "pending")
        self.assertFalse(self.browser.closed)
        self.assertEqual(self.completion_events(), [])
        self.runner.command("start")
        self.wait_for(lambda: len(self.completion_events()) == 1)
        self.assertEqual(self.browser.queries, [SN1, SN2])
        self.assertEqual(len(self.browsers), 1)
        self.assertTrue(self.browser.closed)

    def test_empty_and_completed_start_do_not_reopen_or_repeat_completion(self):
        self.runner.command("start")
        time.sleep(0.25)
        self.assertEqual(self.browsers, [])
        self.assertEqual(self.completion_events(), [])
        self.start_queue()
        self.wait_for(lambda: len(self.completion_events()) == 1)
        self.runner.command("start")
        time.sleep(0.25)
        self.assertEqual(len(self.browsers), 1)
        self.assertEqual(len(self.completion_events()), 1)

    def test_new_batch_reopens_browser_and_notifies_once_again(self):
        self.start_queue()
        self.wait_for(lambda: len(self.completion_events()) == 1)
        first = self.browser
        self.start_queue(SN2)
        self.wait_for(lambda: len(self.completion_events()) == 2)
        self.assertEqual(len(self.browsers), 2)
        self.assertIsNot(first, self.browser)
        self.assertEqual(first.queries, [SN1])
        self.assertEqual(self.browser.queries, [SN2])
        self.assertTrue(first.closed and self.browser.closed)

    def test_skipping_last_item_completes_with_an_explicit_skipped_count(self):
        self.start_queue(auto_drag=False)
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "manual")
        self.assertEqual(self.completion_events(), [])
        self.assertFalse(self.browser.closed)
        self.runner.command("skip", self.runner.store.current.id)
        self.wait_for(lambda: len(self.completion_events()) == 1)
        self.assertEqual(self.completions[0]["counts"], {"skipped": 1})
        self.assertIn("已跳过 1 条", self.completions[0]["message"])
        self.assertEqual(self.browser.queries, [])
        self.assertTrue(self.browser.closed)

    def test_close_failure_still_reports_saved_results_without_claiming_browser_closed(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.fail_close_once = True
        self.start_queue()
        self.wait_for(lambda: len(self.completion_events()) == 1)
        self.assertFalse(self.completions[0]["browser_closed"])
        self.assertIn("请手动关闭", self.completions[0]["message"])
        self.assertEqual(self.browser.saved_on_close["items"][0]["status"], "activated")
        self.assertFalse(self.runner.running)

    def test_failed_automatic_attempt_waits_for_manual_completion(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.fail_drag = True
        self.start_queue()
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "manual")
        time.sleep(0.4)
        self.assertEqual(self.browser.attempts, 1)
        self.assertEqual(self.browser.queries, [])
        self.assertFalse(self.browser.closed)
        self.assertEqual(self.completion_events(), [])
        self.browser.passed = True
        self.wait_for(lambda: self.runner.store.current.status == "activated")
        self.assertEqual(self.browser.queries, [SN1])

    def test_query_rejection_does_not_loop_on_a_stale_passed_verification(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.reject_query = True
        self.start_queue(auto_drag=False)
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "manual")
        self.browser.passed = True
        self.wait_for(lambda: bool(self.runner.must_reset_verification))
        time.sleep(0.4)
        self.assertEqual(self.browser.queries, [SN1])
        self.browser.passed = False
        self.wait_for(lambda: not self.runner.must_reset_verification)
        self.browser.reject_query = False
        self.browser.passed = True
        self.wait_for(lambda: self.runner.store.current.status == "activated")

    def test_pause_during_automatic_drag_keeps_the_item_for_resume(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.block_drag = True
        self.start_queue()
        self.wait_for(lambda: self.browser.attempts == 1)
        self.runner.command("pause")
        self.wait_for(lambda: not self.runner.running and self.runner.store.current.status == "pending")
        self.assertEqual(self.browser.queries, [])

    def test_slider_test_does_not_submit_a_device_query(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.fail_drag = True
        self.runner.command("test_slider", {"mode": "browser", "max_slider_attempts": 3})
        self.wait_for(lambda: self.browser and self.browser.attempts == 1)
        time.sleep(0.35)
        self.assertEqual(self.browser.attempts, 1)
        self.assertEqual(self.browser.queries, [])
        self.assertEqual(self.runner.store.items, [])
        self.assertFalse(self.runner.running)

    def test_failure_refreshes_the_same_sn_then_succeeds(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.fail_attempts = 1
        self.start_queue(max_attempts=3)
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "activated")
        self.assertEqual(self.browser.attempts, 2)
        self.assertEqual(self.browser.refreshes, [SN1])
        self.assertEqual(self.browser.prepared, [SN1, SN1])
        self.assertEqual(self.browser.queries, [SN1])

    def test_limit_refreshes_for_manual_and_never_retries_after_handoff(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.fail_drag = True
        self.start_queue(max_attempts=3)
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "manual")
        time.sleep(0.35)
        self.assertEqual(self.browser.attempts, 3)
        self.assertEqual(self.runner.store.current.slider_attempts, 3)
        self.assertEqual(self.browser.refreshes, [SN1, SN1, SN1])
        self.assertEqual(self.browser.queries, [])
        self.browser.passed = True
        self.wait_for(lambda: self.runner.store.current.status == "activated")
        self.assertEqual(self.browser.queries, [SN1])
        self.assertEqual(self.browser.attempts, 3)

    def test_query_rejections_require_fresh_verifications_and_stop_at_limit(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.reject_query = True
        self.start_queue(max_attempts=3)
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "manual")
        time.sleep(0.35)
        self.assertEqual(self.browser.attempts, 3)
        self.assertEqual(self.browser.refreshes, [SN1, SN1, SN1])
        self.assertEqual(self.browser.queries, [SN1, SN1, SN1])
        self.assertFalse(self.browser.passed)

    def test_manual_completion_during_cooldown_prevents_refresh(self):
        self.runner.retry_delay = 0.8
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.fail_drag = True
        self.start_queue(max_attempts=3)
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "retrying")
        self.browser.passed = True
        self.wait_for(lambda: self.runner.store.current.status == "activated")
        self.assertEqual(self.browser.attempts, 1)
        self.assertEqual(self.browser.refreshes, [])
        self.assertEqual(self.browser.queries, [SN1])

    def test_pause_during_cooldown_preserves_remaining_attempts(self):
        self.runner.retry_delay = 0.5
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.fail_attempts = 1
        self.start_queue(max_attempts=2)
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "retrying")
        self.runner.command("pause")
        self.wait_for(lambda: not self.runner.running)
        time.sleep(0.6)
        self.assertEqual(self.browser.attempts, 1)
        self.assertEqual(self.browser.refreshes, [])
        self.runner.command("start")
        self.wait_for(lambda: self.runner.store.current.status == "activated")
        self.assertEqual(self.runner.store.current.slider_attempts, 2)
        self.assertEqual(self.browser.queries, [SN1])

    def test_skip_during_cooldown_does_not_retry_skipped_sn(self):
        self.runner.retry_delay = 0.8
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.fail_attempts = 1
        self.start_queue(f"{SN1}\n{SN2}", max_attempts=3)
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "retrying")
        self.runner.command("skip", self.runner.store.current.id)
        self.wait_for(lambda: self.runner.store.items[-1].status == "inactive")
        self.assertEqual(self.runner.store.items[0].status, "skipped")
        self.assertEqual(self.browser.attempts, 2)
        self.assertEqual(self.browser.queries, [SN2])
        self.assertEqual(self.browser.refreshes, [])

    def test_login_during_retry_does_not_reset_the_limit(self):
        self.runner.retry_delay = 0.5
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.fail_drag = True
        self.start_queue(max_attempts=2)
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "retrying")
        self.browser.logged_in = False
        self.wait_for(lambda: self.runner.store.current.status == "login_required")
        self.assertEqual(self.runner.store.current.slider_attempts, 1)
        self.browser.logged_in = True
        self.wait_for(lambda: self.runner.store.current.status == "manual")
        self.assertEqual(self.browser.attempts, 2)
        self.assertEqual(self.browser.queries, [])

    def test_manual_handoff_survives_login_pause_and_restart(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.fail_drag = True
        self.start_queue(max_attempts=2)
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "manual")
        self.browser.logged_in = False
        self.wait_for(lambda: self.runner.store.current.status == "login_required")
        self.runner.command("pause")
        self.wait_for(lambda: not self.runner.running)
        self.browser.logged_in = True
        self.runner.command("start")
        self.wait_for(lambda: self.runner.store.current.status == "manual")
        self.assertEqual(self.browser.attempts, 2)
        self.runner.command("close")
        self.runner.join(timeout=4)
        self.assertFalse(self.runner.is_alive())
        self.browser = None
        self.runner = Runner(Path(self.folder.name), self.events, browser_factory=self.make_browser, retry_delay=0.05)
        self.runner.start()
        self.runner.command("start")
        self.wait_for(lambda: self.browser is not None and self.runner.store.current.status == "manual")
        time.sleep(0.25)
        self.assertEqual(self.runner.max_slider_attempts, 2)
        self.assertEqual(self.runner.store.current.slider_attempts, 2)
        self.assertEqual(self.browser.attempts, 0)
        self.assertEqual(self.browser.queries, [])

    def test_explicit_retry_starts_a_new_budget_after_manual_handoff(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.fail_drag = True
        self.start_queue()
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "manual")
        self.browser.fail_drag = False
        self.runner.command("retry", self.runner.store.current.id)
        self.wait_for(lambda: self.runner.store.current.status == "activated")
        self.assertEqual(self.browser.attempts, 2)
        self.assertEqual(self.runner.store.current.slider_attempts, 1)
        self.assertEqual(self.browser.queries, [SN1])

    def test_unsupported_verification_and_mouse_errors_go_directly_to_manual(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.fail_drag = True
        for index, code in enumerate(("unsupported", "mouse_unavailable")):
            with self.subTest(code=code):
                self.browser.failure_code = code
                if index == 0:
                    self.start_queue(max_attempts=3)
                else:
                    self.runner.command("retry", self.runner.store.current.id)
                self.wait_for(lambda: self.browser.attempts == index + 1 and self.runner.store.current.status == "manual")
                time.sleep(0.25)
                self.assertEqual(self.browser.attempts, index + 1)
                self.assertEqual(self.browser.queries, [])
                self.assertEqual(len(self.browser.refreshes), index)

    def test_login_wait_does_not_fill_drag_or_submit_and_resumes_same_sn(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.logged_in = False
        self.start_queue()
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "login_required")
        time.sleep(0.35)
        self.assertEqual(self.browser.prepared, [])
        self.assertEqual(self.browser.attempts, 0)
        self.assertEqual(self.browser.queries, [])
        self.assertEqual(self.runner.store.current.checked_at, "")
        self.browser.logged_in = True
        self.wait_for(lambda: self.runner.store.current.status == "activated")
        self.assertEqual(self.browser.queries, [SN1])

    def test_login_expiring_during_prepare_waits_and_does_not_move_mouse(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.expire_on_prepare = True
        self.start_queue()
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "login_required")
        self.assertTrue(self.runner.running)
        self.assertEqual(self.browser.attempts, 0)
        self.browser.expire_on_prepare = False
        self.browser.logged_in = True
        self.wait_for(lambda: self.runner.store.current.status == "activated")
        self.assertEqual(self.browser.queries, [SN1])

    def test_query_login_gate_reads_returned_result_without_resubmitting(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.expire_on_query = True
        self.start_queue()
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "login_required")
        time.sleep(0.35)
        self.assertEqual(self.browser.queries, [SN1])
        self.assertEqual(self.runner.store.current.activation_time, "")
        self.browser.saved_result = {"status": "activated", "activation_time": "2026-09-13"}
        self.browser.logged_in = True
        self.wait_for(lambda: self.runner.store.current.status == "activated")
        self.assertEqual(self.browser.queries, [SN1])
        self.assertEqual(self.browser.attempts, 1)

    def test_pause_during_login_wait_prevents_automatic_resume(self):
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        self.browser.logged_in = False
        self.start_queue()
        self.wait_for(lambda: self.runner.store.current and self.runner.store.current.status == "login_required")
        self.runner.command("pause")
        self.wait_for(lambda: not self.runner.running)
        self.browser.logged_in = True
        time.sleep(0.35)
        self.assertEqual(self.browser.queries, [])
        self.runner.command("start")
        self.wait_for(lambda: self.runner.store.current.status == "activated")

    def test_opening_login_window_does_not_start_saved_queue(self):
        self.runner.command("add", SN1)
        self.runner.command("open")
        self.wait_for(lambda: self.browser is not None)
        time.sleep(0.2)
        self.assertFalse(self.runner.running)
        self.assertEqual(self.browser.prepared, [])
        self.assertEqual(self.browser.queries, [])


if __name__ == "__main__":
    unittest.main()
