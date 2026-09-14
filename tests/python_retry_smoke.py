"""Exercise Runner with real Playwright input; all requests use a local fixture."""
import json
import queue
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dji_checker.browser import BrowserSession, INPUT, QUERY_URL
from dji_checker.runner import Runner

SN = "1581F4XFC123456"
FIXTURE = (ROOT / "tests/fixtures/device.html").read_text(encoding="utf-8")


def run_case(folder, *, failures, query_failures=0, limit=3):
    options = {"failures": failures, "queryFailures": query_failures}
    fixture = FIXTURE.replace("</head>", '<script>window.fixtureOptions = {authFixture: true, strictEnd: true, returnNewTab: true};</script></head>')
    fixture = fixture.replace("</body>", """<script>
      const retryOptions = OPTIONS;
      const count = key => Number(sessionStorage.getItem(key) || 0);
      document.addEventListener('mousedown', event => {
        if (event.target.id !== 'aliyunCaptcha-sliding-slider') return;
        const attempts = count('retryAttempts') + 1;
        sessionStorage.setItem('retryAttempts', attempts);
        fixture.mode = attempts <= retryOptions.failures ? 'fail' : 'pass';
      }, true);
      document.addEventListener('click', event => {
        if (!event.target.matches('.btn-step button')) return;
        const queries = count('queryAttempts') + 1;
        sessionStorage.setItem('queryAttempts', queries);
        fixture.error = queries <= retryOptions.queryFailures ? '滑块验证已过期' : '';
      }, true);
      window.retryCounts = () => ({attempts: count('retryAttempts'), queries: count('queryAttempts')});
    </script></body>""".replace("OPTIONS", json.dumps(options)))

    class LocalSession(BrowserSession):
        def __init__(self, data_dir, cancel, log):
            super().__init__(data_dir, cancel, log, headless=True)
            self.navigations = []

        def open(self, url=QUERY_URL):
            if self.page and not self.page.is_closed():
                return
            super().open("about:blank")

            def route_request(route):
                if route.request.url.startswith("https://repair.dji.com/device/"):
                    if route.request.is_navigation_request():
                        self.navigations.append(route.request.url)
                    route.fulfill(status=200, content_type="text/html; charset=utf-8", body=fixture)
                else:
                    route.abort()

            self.context.route("**/*", route_request)
            self.page.goto(QUERY_URL)
            self.page.evaluate("fixture.login()")

    runner = Runner(folder, queue.Queue(), browser_factory=LocalSession, retry_delay=0.01)
    try:
        runner.handle("add", SN)
        runner.handle("start", {"mode": "browser", "max_slider_attempts": limit})
        page = runner.browser.page
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            runner.step()
            if runner.store.current.status in ("manual", "activated"):
                break
            time.sleep(0.02)
        item = runner.store.current
        assert runner.browser.page is page and len(runner.browser.context.pages) == 1
        assert runner.browser.login_state()["kind"] == "authenticated"
        counts = page.evaluate("retryCounts()")
        if failures >= limit:
            assert item.status == "manual", item
            assert counts == {"attempts": limit, "queries": 0}, counts
            assert item.slider_attempts == limit and item.manual_required
            assert page.locator(INPUT).input_value() == SN
            assert runner.browser.inspect()["kind"] == "slider"
            before = len(runner.browser.navigations)
            for _ in range(5):
                runner.step()
                time.sleep(0.03)
            assert len(runner.browser.navigations) == before == limit + 1
            assert page.evaluate("retryCounts()") == counts
            page.evaluate("fixture.verify()")
            runner.step()
            assert item.status == "activated", item
            assert page.evaluate("retryCounts()") == {"attempts": limit, "queries": 1}
            print("PASS retry limit: fresh slider, stable manual handoff, manual completion continues the same SN")
        else:
            assert item.status == "activated", item
            assert counts == {"attempts": failures + query_failures + 1, "queries": query_failures + 1}, counts
            assert page.evaluate("fixture.queries") == [SN]
            assert len(runner.browser.navigations) == failures + query_failures + 1
            print("PASS query rejection retry" if query_failures else "PASS failed drag refreshes and succeeds in the same authenticated tab")
        assert runner.browser.last_trace["track_source"] == "slider-captcha-lab/human_track.py"
        assert runner.browser.last_trace["track_variant"] == "feedback"
        process = runner.browser.process
        runner.finish_if_complete()
        assert runner.browser is None and not runner.running
        assert process.poll() is not None
        saved = json.loads((folder / "queue.json").read_text(encoding="utf-8"))
        assert saved["items"][0]["status"] == "activated"
        assert saved["items"][0]["checked_at"]
        notifications = []
        while not runner.events.empty():
            kind, value = runner.events.get_nowait()
            if kind == "complete":
                notifications.append(value)
        assert len(notifications) == 1 and notifications[0]["browser_closed"]
        assert notifications[0]["counts"] == {"activated": 1}
        runner.finish_if_complete()
        assert runner.events.empty()
        print("PASS completion: saved result, browser process exited, one GUI notification")
    finally:
        if runner.browser:
            runner.browser.close()


def main():
    work = ROOT / ".work"
    work.mkdir(exist_ok=True)
    for options in ({"failures": 1}, {"failures": 99}, {"failures": 0, "query_failures": 1}):
        with tempfile.TemporaryDirectory(prefix="python-retry-", dir=work) as folder:
            run_case(Path(folder), **options)
    output = ROOT / "test-results"
    output.mkdir(exist_ok=True)
    (output / "python-retry-smoke.json").write_text(json.dumps({"passed": True, "cases": 3,
        "same_tab": True, "login_preserved": True, "completion_closes_browser": True,
        "live_captcha_tested": False}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
