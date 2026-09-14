"""Offline release check executed by the packaged EXE, without system Python."""
from __future__ import annotations

import json
import os
import queue
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path
from threading import Event

from .browser import BrowserSession, QUERY_URL
from .paths import PROJECT_ROOT, default_data_dir, resource_root
from .runner import Runner

SN1, SN2 = "1581F4XFC123456", "1581F4XFC654321"


def _check_gui(desktop, folder):
    import tkinter as tk
    from unittest.mock import patch

    original_tk = tk.Tk
    failures = []

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    def factory(*args, **kwargs):
        app = original_tk(*args, **kwargs)

        def check():
            try:
                widgets = list(descendants(app))
                assert any(widget.winfo_class() == "Treeview" for widget in widgets)
                assert any(widget.winfo_class() == "TSpinbox" for widget in widgets)
                importer = next(widget for widget in widgets if widget.winfo_class() == "Text" and str(widget.cget("state")) == "normal")
                importer.insert("1.0", SN1)
                next(widget for widget in widgets if widget.winfo_class() == "TButton" and widget.cget("text") == "加入队列").invoke()
            except Exception as error:
                failures.append(error)

        app.after(250, check)
        app.after(750, lambda: app.eval(app.protocol("WM_DELETE_WINDOW")))
        return app

    with patch.object(tk, "Tk", factory):
        desktop(folder)
    if failures:
        raise failures[0]
    saved = json.loads((folder / "queue.json").read_text(encoding="utf-8"))
    assert saved["items"][0]["sn"] == SN1


def _check_browser(folder, fixture, executable=None):
    fixture = fixture.replace("</head>", '<script>window.fixtureOptions = {authFixture: true, strictEnd: true, returnNewTab: true};</script></head>')
    sessions = []

    class LocalSession(BrowserSession):
        def __init__(self, data_dir, cancel, log):
            super().__init__(data_dir, cancel, log, headless=True, executable=executable)
            self.attempts = 0
            self.query_page = None
            self.max_tabs = 0
            sessions.append(self)

        def open(self, url=QUERY_URL):
            if self.page and not self.page.is_closed():
                return
            super().open("about:blank")
            self.context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=fixture)
                               if route.request.url.startswith("https://repair.dji.com/device/") else route.abort())
            self.page.goto(QUERY_URL)
            self.query_page = self.page

        def attempt_slider(self, sn, mode="browser", **kwargs):
            self.attempts += 1
            assert self.page is self.query_page
            self.max_tabs = max(self.max_tabs, len(self.context.pages))
            self.page.evaluate("options => {fixture.mode = options.fail ? 'fail' : 'pass'; fixture.resultStatus = options.inactive ? 'inactive' : 'activated';}",
                               {"fail": self.attempts == 1, "inactive": sn == SN2})
            return super().attempt_slider(sn, mode, **kwargs)

    runner = Runner(folder, queue.Queue(), browser_factory=LocalSession, retry_delay=0.01)
    try:
        runner.handle("open", {"mode": "browser"})
        runner.browser.page.evaluate("fixture.login()")
        runner.handle("add", SN1 + "\n" + SN2)
        runner.handle("start", {"mode": "browser", "max_slider_attempts": 3})
        deadline = time.monotonic() + 45
        while runner.running and time.monotonic() < deadline:
            runner.step()
            runner.finish_if_complete()
            time.sleep(0.02)
        assert not runner.running and runner.browser is None
        assert [item.status for item in runner.store.items] == ["activated", "inactive"]
        assert sessions[0].attempts == 3 and sessions[0].max_tabs == 1
        assert sessions[0].last_trace["track_source"] == "slider-captcha-lab/human_track.py"
        assert sessions[0].process.poll() is not None
        notices = []
        while not runner.events.empty():
            kind, value = runner.events.get_nowait()
            if kind == "complete":
                notices.append(value)
        assert len(notices) == 1 and notices[0]["browser_closed"]
        saved = json.loads((folder / "queue.json").read_text(encoding="utf-8"))
        assert [row["status"] for row in saved["items"]] == ["activated", "inactive"]
        assert all(row["checked_at"] for row in saved["items"])

        # Reopen the same isolated profile; the test login must survive closing.
        reopened = LocalSession(folder, Event(), lambda text: None)
        reopened.open()
        assert reopened.login_state()["kind"] == "authenticated"
        reopened.prepare("")
        return {"attempts": 3, "results": ["activated", "inactive"], "same_tab": True,
                "completion_notice": True, "browser_closed": True, "login_retained": True,
                "browser": str(reopened.executable or sessions[0].process.args[0])}
    finally:
        for session in sessions:
            if session.page is not None or (session.process and session.process.poll() is None):
                session.cancel.clear()
                session.close()


def run_self_test(report_path: Path, desktop) -> bool:
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "frozen": bool(getattr(sys, "frozen", False)),
              "python_version": sys.version.split()[0], "python_on_path": shutil.which("python"),
              "executable": sys.executable, "resource_root": str(resource_root()),
              "default_data_dir": str(default_data_dir()), "live_device_queries": False}
    try:
        fixture_path = resource_root() / "selftest/device.html"
        if not report["frozen"]:
            fixture_path = PROJECT_ROOT / "tests/fixtures/device.html"
        assert (resource_root() / "core.js").is_file()
        assert (resource_root() / "slider.js").is_file()
        if report["frozen"]:
            assert not default_data_dir().resolve().is_relative_to(resource_root().resolve())
            assert (resource_root() / "licenses/slider-captcha-lab/LICENSE").is_file()
        fixture = fixture_path.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="exe-selftest-", dir=report_path.parent) as temporary:
            folder = Path(temporary) / "data"
            _check_gui(desktop, folder)
            report["gui_started"] = True
            report["browser_checks"] = _check_browser(folder, fixture)
            edges = [Path(os.environ.get(key, "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe"
                     for key in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
            edge = next((path for path in edges if path.is_file()), None)
            if edge and str(edge).lower() != report["browser_checks"]["browser"].lower():
                report["edge_checks"] = _check_browser(Path(temporary) / "edge-data", fixture, executable=edge)
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report["passed"]
