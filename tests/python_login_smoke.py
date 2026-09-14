"""Login lifecycle tests using local fixtures and a disposable browser profile.

Every request is intercepted. No real login, captcha, or SN query is sent to DJI.
"""
import json
import sys
import tempfile
from pathlib import Path
from threading import Event

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dji_checker.browser import BrowserSession, LoginRequired, SEARCH_URL

SN = "1581F4XFC123456"
URL = SEARCH_URL + "&authFixture=1"


def main():
    fixture = (ROOT / "tests/fixtures/device.html").read_text(encoding="utf-8")
    work = ROOT / ".work"
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="python-login-", dir=work) as folder:
        def start():
            session = BrowserSession(Path(folder), Event(), headless=True)
            session.open("about:blank")

            def route(request):
                if request.request.url.startswith("https://repair.dji.com/device/"):
                    request.fulfill(status=200, content_type="text/html; charset=utf-8", body=fixture)
                elif request.request.url.startswith("https://account.dji.com/"):
                    request.fulfill(status=200, content_type="text/html; charset=utf-8", body="<h1>Local login placeholder</h1>")
                else:
                    request.abort()

            session.context.route("**/*", route)
            session.page.goto(URL)
            return session

        session = start()
        try:
            assert session.login_state()["kind"] == "required"
            try:
                session.prepare(SN)
                raise AssertionError("A logged-out session must not fill an SN")
            except LoginRequired:
                pass
            assert session.page.locator("input").input_value() == ""
            assert session.page.evaluate("fixture.queries.length === 0 && fixture.attempts === 0")
            session.page.goto("https://account.dji.com/login")
            assert session.login_state()["kind"] == "required"
            try:
                session.prepare(SN)
                raise AssertionError("Login navigation must be handed back to the user")
            except LoginRequired:
                pass
            assert session.page.url == "https://account.dji.com/login"
            print("PASS logged-out and login-redirect states wait without filling, dragging, or querying")

            session.page.goto(URL)
            session.page.evaluate("fixture.login()")
            assert session.login_state()["kind"] == "authenticated"
            assert not session.form_visible(), "DJI defaults to the bound-device tab"
            session.prepare(SN)
            assert session.form_visible()
            assert session.page.locator("#tab-1").get_attribute("aria-selected") == "true"
            assert session.page.locator("input").input_value() == SN
            session.page.evaluate("fixture.verify()")
            assert session.submit_and_wait(SN)["status"] == "activated"
            assert session.page.evaluate("fixture.queries") == [SN]
            print("PASS signed-in query selects the serial-number tab and records the matching result")
        finally:
            session.close()

        session = start()
        try:
            assert session.login_state()["kind"] == "authenticated", "The browser must retain its profile across restarts"
            session.prepare(SN)
            assert session.form_visible()
            print("PASS browser profile retains the local test login after the browser is closed and reopened")

            session.page.evaluate("fixture.resultStatus = 'login_required'; fixture.verify()")
            result = session.submit_and_wait(SN)
            assert result["status"] == "login_required", result
            assert "activation_time" not in result
            assert session.login_state()["kind"] == "required"
            assert session.page.evaluate("fixture.queries") == [SN]
            print("PASS an expired login with a masked activation date is not reported as inactive")
        finally:
            session.close()

    output = ROOT / "test-results"
    output.mkdir(exist_ok=True)
    (output / "python-login-smoke.json").write_text(json.dumps({
        "passed": True, "cases": 4, "real_login_tested": False, "live_device_queries": 0
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream and not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    main()
