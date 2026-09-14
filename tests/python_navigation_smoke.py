"""Exercise DJI's new-tab return-button behavior using only local fixtures."""
import json
import sys
import tempfile
from pathlib import Path
from threading import Event
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dji_checker.browser import BrowserSession, QUERY_URL

SNS = ("1581F4XFC123456", "1581F4XFC654321", "1581F4XFC987654")


def main():
    fixture = (ROOT / "tests/fixtures/device.html").read_text(encoding="utf-8")
    fixture = fixture.replace("</head>", """<script>
      window.fixtureOptions = {authFixture: true, returnNewTab: true, formDelay: 180};
    </script></head>""")
    work = ROOT / ".work"
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="python-navigation-", dir=work) as folder:
        session = BrowserSession(Path(folder), Event(), headless=True)
        try:
            session.open("about:blank")
            session.context.route("**/*", lambda route: route.fulfill(
                status=200, content_type="text/html; charset=utf-8", body=fixture
            ) if route.request.url.startswith("https://repair.dji.com/device/") else route.abort())
            session.page.goto(QUERY_URL)
            session.page.locator(".device-login").wait_for()
            session.page.evaluate("fixture.login(); window.name = 'query-tab-under-test'")
            assert session.login_state()["kind"] == "authenticated"
            assert not session.form_visible(), "The signed-in page starts on bound devices"
            original_page = session.page
            new_pages = []
            session.context.on("page", lambda page: new_pages.append(page))
            recorded = []

            for index, sn in enumerate(SNS):
                session.prepare(sn)
                assert session.page is original_page
                assert len(session.context.pages) == 1 and not new_pages
                assert session.page.evaluate("window.name") == "query-tab-under-test"
                assert urlparse(session.page.url).path == "/device/search"
                assert session.page.locator("input").input_value() == sn
                assert session.page.locator("#tab-1").get_attribute("aria-selected") == "true"
                if index:
                    assert session.last_prepare["navigated"]
                    assert session.last_prepare["from_path"] == "/device/detail"
                session.page.evaluate("status => { fixture.resultStatus = status; fixture.verify(); }",
                                      "inactive" if index == 1 else "activated")
                result = session.submit_and_wait(sn)
                assert result["status"] == ("inactive" if index == 1 else "activated"), result
                assert session.page.evaluate("fixture.queries") == [sn]
                assert urlparse(session.page.url).path == "/device/detail"
                recorded.append(sn)
            assert recorded == list(SNS)
            print("PASS three matching results reuse one tab despite DJI's new-tab return button")

            session.prepare(SNS[-1], refresh=True)
            assert session.page is original_page and len(session.context.pages) == 1
            assert session.inspect()["kind"] == "slider"
            assert session.page.evaluate("fixture.queries") == []
            assert session.page.locator("input").input_value() == SNS[-1]
            print("PASS retry reloads the existing query tab and waits for the signed-in SN form")

            session.prepare("", refresh=True)
            assert session.page is original_page and not new_pages
            assert session.page.locator("input").input_value() == ""
            assert session.page.evaluate("fixture.queries.length === 0 && fixture.attempts === 0")
            assert session.login_state()["kind"] == "authenticated"
            print("PASS returning with an empty SN preserves login without dragging or submitting")
        finally:
            session.close()

    output = ROOT / "test-results"
    output.mkdir(exist_ok=True)
    (output / "python-navigation-smoke.json").write_text(json.dumps({
        "passed": True, "cases": 3, "fixture_queries": len(recorded),
        "new_query_tabs": len(new_pages), "live_device_queries": 0
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream and not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    main()
