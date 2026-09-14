"""DJI SN checker desktop entry point for source and packaged Windows builds."""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
from pathlib import Path
from threading import Event

from dji_checker.core import LABELS, MAX_SLIDER_ATTEMPTS, csv_text
from dji_checker.native_mouse import enable_dpi_awareness
from dji_checker.paths import default_data_dir

class InstanceLock:
    def __init__(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        self.file = (folder / "app.lock").open("a+b")
        self.file.seek(0)
        self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def close(self):
        self.file.close()


def desktop(data_dir, *, open_browser=False):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from dji_checker.runner import Runner

    enable_dpi_awareness()
    app = tk.Tk()
    edition = "Windows 桌面版" if getattr(sys, "frozen", False) else "Python 本地版"
    app.title("DJI SN 批量查询 · " + edition)
    app.geometry("1060x790")
    app.minsize(900, 690)
    style = ttk.Style(app)
    style.theme_use("clam")
    style.configure(".", font=("Microsoft YaHei UI", 10))
    style.configure("Treeview", rowheight=30)
    style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
    style.configure("Status.TLabel", foreground="#0966a8")
    events = queue.Queue()
    try:
        lock = InstanceLock(data_dir)
    except OSError:
        messagebox.showerror("已在运行", "查询助手已打开，请使用已有窗口。", parent=app)
        app.destroy()
        return
    try:
        worker = Runner(data_dir, events)
    except Exception as error:
        messagebox.showerror("无法读取队列", f"本地队列无法读取，原文件已保留：\n{data_dir / 'queue.json'}\n\n{error}", parent=app)
        lock.close()
        app.destroy()
        return
    snapshot = {"items": [], "current_id": "", "running": False}
    shutting_down = False
    frame = ttk.Frame(app, padding=18)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(5, weight=1)
    ttk.Label(frame, text="DJI SN 批量查询", style="Title.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(frame, text=edition + " · 首次在查询窗口登录 DJI 账号，之后保留登录状态").grid(row=1, column=0, sticky="w", pady=(4, 12))

    importer = ttk.LabelFrame(frame, text="导入 SN", padding=10)
    importer.grid(row=2, column=0, sticky="ew")
    importer.columnconfigure(0, weight=1)
    textbox = tk.Text(importer, height=3, wrap="word", font=("Consolas", 11), undo=True)
    textbox.grid(row=0, column=0, sticky="ew", padx=(0, 10))

    def add():
        text = textbox.get("1.0", "end").strip()
        if text:
            worker.command("add", text)
            textbox.delete("1.0", "end")

    ttk.Button(importer, text="加入队列", command=add).grid(row=0, column=1, sticky="ns")
    ttk.Label(importer, text="14–20 位字母或数字，支持换行、空格、逗号；重复项会忽略").grid(row=1, column=0, sticky="w", pady=(6, 0))
    textbox.bind("<Control-Return>", lambda event: add())

    controls = ttk.Frame(frame)
    controls.grid(row=3, column=0, sticky="ew", pady=12)
    saved_settings = worker.store.settings
    auto = tk.BooleanVar(value=saved_settings["auto_drag"])
    continuous = tk.BooleanVar(value=saved_settings["continuous"])
    mode = tk.StringVar(value="系统鼠标（Windows）" if os.name == "nt" and saved_settings["mode"] == "system" else "浏览器鼠标（兼容模式）")
    max_attempts = tk.IntVar(value=saved_settings["max_slider_attempts"])

    def settings():
        return {"mode": "system" if mode.get().startswith("系统") else "browser", "auto_drag": auto.get(),
                "continuous": continuous.get(), "max_slider_attempts": max_attempts.get()}

    def save_settings():
        worker.command("settings", settings())

    ttk.Label(controls, text="拖动方式").grid(row=0, column=0, padx=(0, 6))
    selector = ttk.Combobox(controls, textvariable=mode, state="readonly", width=24,
                           values=["系统鼠标（Windows）", "浏览器鼠标（兼容模式）"])
    selector.grid(row=0, column=1, sticky="w")
    selector.bind("<<ComboboxSelected>>", lambda event: save_settings())
    ttk.Checkbutton(controls, text="自动尝试滑块", variable=auto, command=save_settings).grid(row=0, column=2, padx=12)
    ttk.Checkbutton(controls, text="连续查询", variable=continuous, command=save_settings).grid(row=0, column=3)
    retry_settings = ttk.Frame(controls)
    retry_settings.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
    ttk.Label(retry_settings, text="自动尝试上限").pack(side="left", padx=(0, 8))
    ttk.Spinbox(retry_settings, from_=1, to=MAX_SLIDER_ATTEMPTS, textvariable=max_attempts, width=3,
                state="readonly", command=save_settings).pack(side="left")
    ttk.Label(retry_settings, text="次（含首次；失败后刷新重试，达到上限转人工）").pack(side="left", padx=(8, 0))
    ttk.Label(controls, text="系统鼠标会移动真实光标；拖动期间保持查询窗口在前台，按 Esc 可停止。").grid(row=2, column=0, columnspan=4, sticky="w", pady=(7, 0))
    login_status = tk.StringVar(value=worker.login["message"])
    ttk.Label(controls, textvariable=login_status, style="Status.TLabel", wraplength=850).grid(row=3, column=0, columnspan=4, sticky="w", pady=(7, 0))

    buttons = ttk.Frame(frame)
    buttons.grid(row=4, column=0, sticky="ew", pady=(0, 10))
    ttk.Button(buttons, text="打开浏览器 / 登录", command=lambda: worker.command("open", settings())).pack(side="left", padx=(0, 7))
    ttk.Button(buttons, text="测试滑块", command=lambda: worker.command("test_slider", settings())).pack(side="left", padx=(0, 7))
    start = ttk.Button(buttons, text="开始 / 继续", command=lambda: worker.command("start", settings()))
    start.pack(side="left", padx=(0, 7))
    pause = ttk.Button(buttons, text="暂停", command=lambda: worker.command("pause"), state="disabled")
    pause.pack(side="left", padx=(0, 7))

    table_frame = ttk.Frame(frame)
    table_frame.grid(row=5, column=0, sticky="nsew")
    table_frame.columnconfigure(0, weight=1)
    table_frame.rowconfigure(0, weight=1)
    columns = ("sn", "status", "product", "activation_time", "message")
    table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
    for name, title, width in [("sn", "SN", 185), ("status", "状态", 90), ("product", "产品", 140),
                               ("activation_time", "激活时间", 145), ("message", "进度 / 结果说明", 310)]:
        table.heading(name, text=title)
        table.column(name, width=width, minwidth=70, stretch=name == "message")
    table.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=table.xview)
    horizontal.grid(row=1, column=0, sticky="ew")
    table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
    table.tag_configure("current", background="#eaf4fc")

    def selected_id():
        return table.selection()[0] if table.selection() else snapshot["current_id"]

    def retry():
        # Settings take effect before the interrupted item is retried.
        worker.command("pause")
        worker.command("open", settings())
        worker.command("retry", selected_id())

    ttk.Button(buttons, text="重试当前 / 选中项", command=retry).pack(side="left", padx=(0, 7))
    ttk.Button(buttons, text="跳过当前", command=lambda: worker.command("skip", snapshot["current_id"])).pack(side="left")

    status = tk.StringVar(value="先点击“打开浏览器 / 登录”，在查询窗口登录 DJI 账号，再加入 SN 并开始。")
    ttk.Label(frame, textvariable=status, style="Status.TLabel", wraplength=980).grid(row=6, column=0, sticky="w", pady=10)
    log = tk.Text(frame, height=5, wrap="word", state="disabled", font=("Microsoft YaHei UI", 9), background="#f5f7fa", relief="flat")
    log.grid(row=7, column=0, sticky="ew")
    footer = ttk.Frame(frame)
    footer.grid(row=8, column=0, sticky="ew", pady=(10, 0))
    summary = tk.StringVar(value="队列为空")
    ttk.Label(footer, textvariable=summary).pack(side="left")

    def export():
        if not snapshot["items"]:
            return
        filename = filedialog.asksaveasfilename(parent=app, title="导出查询结果", defaultextension=".csv",
                                              initialfile="DJI-SN-查询结果.csv", filetypes=[("CSV 文件", "*.csv")])
        if filename:
            try:
                Path(filename).write_text(csv_text(snapshot["items"]), encoding="utf-8", newline="")
                status.set(f"结果已导出：{filename}")
            except OSError as error:
                messagebox.showerror("导出失败", str(error), parent=app)

    ttk.Button(footer, text="导出 CSV", command=export).pack(side="right")
    ttk.Button(footer, text="清除已完成", command=lambda: worker.command("clear")).pack(side="right", padx=8)

    def poll():
        nonlocal snapshot
        while True:
            try:
                kind, value = events.get_nowait()
            except queue.Empty:
                break
            if kind == "state":
                snapshot = value
                login_status.set(value["login"]["message"])
                ids = {item["id"] for item in value["items"]}
                for row in table.get_children():
                    if row not in ids:
                        table.delete(row)
                for item in value["items"]:
                    values = [item["sn"], LABELS.get(item["status"], item["status"]), item["product"], item["activation_time"], item["message"]]
                    tags = ("current",) if item["id"] == value["current_id"] else ()
                    if table.exists(item["id"]):
                        table.item(item["id"], values=values, tags=tags)
                    else:
                        table.insert("", "end", iid=item["id"], values=values, tags=tags)
                activated = sum(item["status"] == "activated" for item in value["items"])
                inactive = sum(item["status"] == "inactive" for item in value["items"])
                summary.set(f"共 {len(ids)} 条 · 已激活 {activated} · 未激活 {inactive}")
                start.configure(state="disabled" if value["running"] else "normal")
                pause.configure(state="normal" if value["running"] else "disabled")
            elif kind == "login":
                login_status.set(value["message"])
            elif kind == "log":
                status.set(value)
                log.configure(state="normal")
                log.insert("end", value + "\n")
                if int(log.index("end-1c").split(".")[0]) > 250:
                    log.delete("1.0", "50.0")
                log.see("end")
                log.configure(state="disabled")
            elif kind == "complete":
                status.set(value["summary"] + "；结果已保存，可导出 CSV")
                if not shutting_down:
                    app.deiconify()
                    app.lift()
                    messagebox.showinfo("查询完成", value["message"], parent=app)
            elif kind == "closed":
                lock.close()
                app.destroy()
                return
        app.after(100, poll)

    def close():
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        status.set("正在保存队列并关闭独立查询浏览器…")
        worker.command("close")

    app.protocol("WM_DELETE_WINDOW", close)
    worker.start()
    if open_browser:
        app.after(200, lambda: worker.command("open", settings()))
    app.after(100, poll)
    app.mainloop()


def main():
    parser = argparse.ArgumentParser(description="DJI SN 批量查询桌面版")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--self-test", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--diagnose", action="store_true", help="只检查官网滑块状态，不拖动、不提交查询")
    parser.add_argument("--test-slider", action="store_true", help="测试一次自动滑块，不提交设备查询")
    parser.add_argument("--mouse", choices=["system", "browser"], default="system", help="滑块测试使用的鼠标方式")
    parser.add_argument("--headless", action="store_true", help="诊断时使用无界面浏览器")
    parser.add_argument("--open-browser", action="store_true", help="打开桌面界面和查询窗口供手动登录，不开始查询")
    args = parser.parse_args()
    if args.self_test:
        from dji_checker.self_test import run_self_test
        raise SystemExit(0 if run_self_test(args.self_test, desktop) else 1)
    if args.diagnose or args.test_slider:
        from dji_checker.browser import BrowserSession
        if args.test_slider and args.mouse == "system" and args.headless:
            parser.error("系统鼠标测试需要可见浏览器，请去掉 --headless")
        enable_dpi_awareness()
        session = BrowserSession(args.data_dir, Event(), print, headless=args.headless)
        try:
            session.open()
            if args.test_slider:
                session.prepare("")
            state = session.wait_slider()
            if args.test_slider:
                state = session.attempt_slider("", args.mouse)
                report = session.diagnostic("单次滑块测试：" + state["message"])
            else:
                report = session.diagnostic("只读诊断：未执行拖动或查询")
            print(json.dumps(state, ensure_ascii=False, indent=2))
            print(f"诊断文件：{report}")
        finally:
            session.close()
    else:
        desktop(args.data_dir, open_browser=args.open_browser)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream and not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    try:
        main()
    except Exception as error:
        if sys.stderr:
            raise
        import tkinter.messagebox
        tkinter.messagebox.showerror("查询助手启动失败", str(error))
