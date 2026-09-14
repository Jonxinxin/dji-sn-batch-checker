"""Launch the real Tk interface with a disposable local queue, without querying."""
import sys
import json
import tempfile
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import python_app


def main():
    original_tk = tk.Tk
    failures = []
    expected_limit = 3

    def children(widget):
        for child in widget.winfo_children():
            yield child
            yield from children(child)

    def factory(*args, **kwargs):
        app = original_tk(*args, **kwargs)

        def finish():
            app.eval(app.protocol("WM_DELETE_WINDOW"))

        def check():
            try:
                widgets = list(children(app))
                buttons = [widget for widget in widgets if widget.winfo_class() == "TButton"]
                spinners = [widget for widget in widgets if widget.winfo_class() == "TSpinbox"]
                assert len(spinners) == 1
                spinner = spinners[0]
                assert int(spinner.get()) == expected_limit
                spinner.set("4")
                spinner.tk.call(spinner.cget("command"))
                for button in buttons + spinners:
                    assert button.winfo_ismapped(), button.cget("text")
                    x = button.winfo_rootx() - app.winfo_rootx()
                    y = button.winfo_rooty() - app.winfo_rooty()
                    assert 0 <= x and x + button.winfo_width() <= app.winfo_width(), button.cget("text")
                    assert 0 <= y and y + button.winfo_height() <= app.winfo_height(), button.cget("text")
                importer = next(widget for widget in widgets if widget.winfo_class() == "Text" and str(widget.cget("state")) == "normal")
                importer.insert("1.0", "1581F4XFC123456")
                next(button for button in buttons if button.cget("text") == "加入队列").invoke()
            except Exception as error:
                failures.append(error)
                finish()

        def verify_import():
            try:
                table = next(widget for widget in children(app) if widget.winfo_class() == "Treeview")
                assert len(table.get_children()) == 1
                assert table.item(table.get_children()[0], "values")[0] == "1581F4XFC123456"
                print("PASS real desktop UI starts, retry limit loads, controls fit, and importing updates the queue")
            except Exception as error:
                failures.append(error)
            finally:
                finish()

        app.after(400, check)
        app.after(1000, verify_import)
        return app

    work = ROOT / ".work"
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="python-gui-", dir=work) as folder, patch.object(tk, "Tk", factory):
        python_app.desktop(Path(folder))
        saved = json.loads((Path(folder) / "queue.json").read_text(encoding="utf-8"))
        assert saved["settings"]["max_slider_attempts"] == 4
        expected_limit = 4
        python_app.desktop(Path(folder))
    if failures:
        raise failures[0]


def completion_smoke():
    from threading import current_thread, main_thread
    from tkinter import messagebox
    from dji_checker.runner import Runner
    from test_python_runner import FakeBrowser

    original_tk = tk.Tk
    failures, notifications, workers = [], [], []

    def make_worker(folder, events):
        worker = Runner(folder, events, browser_factory=FakeBrowser)
        workers.append(worker)
        return worker

    def info(title, message, *, parent):
        notifications.append((title, message, parent.state(), current_thread() is main_thread()))
        return "ok"

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    def factory(*args, **kwargs):
        app = original_tk(*args, **kwargs)

        def start_batch():
            try:
                widgets = list(descendants(app))
                importer = next(widget for widget in widgets if widget.winfo_class() == "Text" and str(widget.cget("state")) == "normal")
                importer.insert("1.0", "1581F4XFC123456")
                buttons = {widget.cget("text"): widget for widget in widgets if widget.winfo_class() == "TButton"}
                buttons["加入队列"].invoke()
                app.iconify()
                buttons["开始 / 继续"].invoke()
            except Exception as error:
                failures.append(error)

        def verify_completion():
            try:
                assert len(notifications) == 1, notifications
                title, message, window_state, in_main_thread = notifications[0]
                assert title == "查询完成"
                assert "已激活 1 条" in message and "浏览器已自动关闭" in message
                assert window_state == "normal" and in_main_thread
                assert app.winfo_exists() and workers[0].is_alive()
                assert workers[0].browser is None and not workers[0].running
                table = next(widget for widget in descendants(app) if widget.winfo_class() == "Treeview")
                row = table.item(table.get_children()[0], "values")
                assert row[0] == "1581F4XFC123456" and row[1] == "已激活"
                print("PASS completion GUI: main window restored, one dialog on the UI thread, results remain visible")
            except Exception as error:
                failures.append(error)
            finally:
                app.eval(app.protocol("WM_DELETE_WINDOW"))

        app.after(250, start_batch)
        app.after(1500, verify_completion)
        return app

    work = ROOT / ".work"
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="python-gui-completion-", dir=work) as folder:
        with patch.object(tk, "Tk", factory), patch("dji_checker.runner.Runner", make_worker), patch.object(messagebox, "showinfo", info):
            python_app.desktop(Path(folder))
    if failures:
        raise failures[0]


if __name__ == "__main__":
    main()
    completion_smoke()
