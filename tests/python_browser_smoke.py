"""Python end-to-end tests. Website requests are served from a local fixture.

--native additionally opens an isolated visible browser and moves the real
Windows pointer over the local test slider. It never contacts DJI.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from threading import Event, Timer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dji_checker.browser import BrowserSession, QUERY_URL
from dji_checker.native_mouse import Cancelled, enable_dpi_awareness
from dji_checker.tracks import MIN_DYNAMIC_DURATION, MAX_DYNAMIC_DURATION, TRACK_SOURCE, TRACK_VARIANT

SN1, SN2 = "1581F4XFC123456", "1581F4XFC654321"


def main(native=False):
    if native:
        enable_dpi_awareness()
    fixture = (ROOT / "tests/fixtures/device.html").read_text(encoding="utf-8")
    fixture = fixture.replace("</head>", '<script>window.fixtureOptions = {strictEnd: true};</script></head>')
    work = ROOT / ".work"
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="python-browser-", dir=work) as folder:
        cancel = Event()
        session = BrowserSession(Path(folder), cancel, headless=not native)
        try:
            session.open("about:blank")
            session.context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=fixture)
                                  if route.request.url.startswith("https://repair.dji.com/device/") else route.abort())
            session.page.goto(QUERY_URL + "&strictEnd=1")
            session.prepare(SN1)
            mode = "system" if native else "browser"
            result = session.attempt_slider(SN1, mode)
            assert result["ok"], (result, session.last_trace, session.page.evaluate("fixture.mouse.filter(e => e.type !== 'move')"))
            assert session.last_trace["mouse_down"] and session.last_trace["mouse_up"]
            assert session.last_trace["moves"] > 20
            assert session.last_trace["duration_mode"] == "dynamic"
            assert MIN_DYNAMIC_DURATION <= session.last_trace["duration_s"] <= MAX_DYNAMIC_DURATION
            assert session.last_trace["track_source"] == TRACK_SOURCE
            assert session.last_trace["track_variant"] == TRACK_VARIANT
            assert session.last_trace["actual_duration_s"] >= session.last_trace["duration_s"] - 0.02
            assert session.page.evaluate("fixture.mouse.every(event => event.trusted)")
            assert session.submit_and_wait(SN1)["status"] == "activated"
            assert session.page.evaluate("fixture.queries") == [SN1]
            query_page = session.page
            session.prepare(SN2)
            session.page.evaluate("fixture.resultStatus = 'inactive'")
            assert session.attempt_slider(SN2, mode)["ok"]
            assert session.last_trace["duration_mode"] == "dynamic"
            assert MIN_DYNAMIC_DURATION <= session.last_trace["duration_s"] <= MAX_DYNAMIC_DURATION
            assert session.submit_and_wait(SN2)["status"] == "inactive"
            assert session.page.evaluate("fixture.queries") == [SN2]
            assert session.page is query_page and len(session.context.pages) == 1
            print(f"PASS {mode}: dynamic speed, real mouse events, two-item queue, result matching")

            session.prepare(SN1)
            session.page.evaluate("fixture.mode = 'fail'")
            result = session.attempt_slider(SN1, mode)
            assert not result["ok"] and result["code"] == "failed", result
            assert session.inspect()["kind"] == "failed"
            print(f"PASS {mode}: a rejected verification is not reported as success")

            session.prepare(SN1, refresh=True)
            timer = Timer(0.8, cancel.set)
            timer.start()
            try:
                session.attempt_slider(SN1, mode, duration=2.5)
                raise AssertionError("Expected cancellation during the drag")
            except Cancelled:
                pass
            finally:
                timer.cancel()
                cancel.clear()
            assert session.last_trace["mouse_down"], session.last_trace
            assert session.last_trace["mouse_up"], session.last_trace
            assert not session.page.evaluate("fixture.pressed")
            print(f"PASS {mode}: cancellation releases the pressed mouse")

            # No new drag should happen if the user changes the SN.
            session.prepare(SN1, refresh=True)
            session.page.locator('input[placeholder*="序列号"]').fill(SN2)
            try:
                session.attempt_slider(SN1, mode)
                raise AssertionError("Expected SN mismatch to abort")
            except Cancelled:
                pass
            assert not session.last_trace["mouse_down"]
            print(f"PASS {mode}: a mismatched SN aborts before mouse-down")
            output = ROOT / "test-results"
            output.mkdir(exist_ok=True)
            (output / f"python-{mode}-smoke.json").write_text(json.dumps({"passed": True, "mode": mode,
                "cases": 4, "live_captcha_tested": False}, indent=2), encoding="utf-8")
        except Exception:
            cancel.clear()
            output = ROOT / "test-results"
            output.mkdir(exist_ok=True)
            session.data_dir = output
            diagnostic = session.diagnostic("Python browser smoke failure")
            print(f"Diagnostic: {diagnostic}")
            raise
        finally:
            cancel.clear()
            session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if stream and not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    main(args.native)
