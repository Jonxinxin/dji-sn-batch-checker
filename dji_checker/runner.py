"""One worker owns Playwright and queue writes; the GUI never blocks on it."""
from __future__ import annotations

import queue
import threading
import time
from collections import Counter
from pathlib import Path

from .browser import BrowserSession, LoginRequired
from .core import FINAL, LOGIN_MESSAGE, QueueStore, normalize_settings, now_text
from .native_mouse import Cancelled


class Runner(threading.Thread):
    def __init__(self, data_dir: Path, events: queue.Queue, browser_factory=BrowserSession, *, retry_delay=2.0):
        super().__init__(name="dji-browser-worker", daemon=True)
        self.data_dir, self.events = Path(data_dir), events
        self.store = QueueStore(self.data_dir / "queue.json")
        self.commands = queue.Queue()
        self.cancel = threading.Event()
        self.closing = threading.Event()
        self.browser_factory, self.browser = browser_factory, None
        self.running = False
        self.completion_armed = False
        self.apply_settings(self.store.settings)
        self.retry_delay = retry_delay
        self.retry_at = 0.0
        self.must_reset_verification = ""
        self.login = {"kind": "unchecked", "message": "请先打开查询浏览器并登录 DJI 账号；登录状态会保留在本机"}
        self.next_login_poll = 0.0

    def log(self, message):
        self.events.put(("log", str(message)))

    def publish(self):
        self.store.save()
        self.events.put(("state", {**self.store.snapshot(), "running": self.running, "login": self.login}))

    def apply_settings(self, values):
        self.store.settings = normalize_settings({**self.store.settings, **(values or {})})
        for key, value in self.store.settings.items():
            setattr(self, key, value)

    def finish_if_complete(self):
        """Save a finished batch, close our browser, then notify the GUI once."""
        if (not self.completion_armed or self.closing.is_set() or not self.store.items
                or any(item.status not in FINAL for item in self.store.items)):
            return
        self.running = False
        self.completion_armed = False
        self.retry_at = 0.0
        self.must_reset_verification = ""
        counts = Counter(item.status for item in self.store.items)
        summary = f"共 {len(self.store.items)} 条：已激活 {counts['activated']} 条，未激活 {counts['inactive']} 条"
        for key, label in (("error", "查询失败"), ("unknown", "待确认"), ("skipped", "已跳过")):
            if counts[key]:
                summary += f"，{label} {counts[key]} 条"
        self.log("队列已处理完毕，正在关闭查询浏览器…")
        self.publish()  # Results must be durable before the browser disappears.
        browser_closed = True
        try:
            if self.browser:
                self.browser.close()
                self.browser = None
        except Exception as error:
            browser_closed = False
            self.log(f"自动关闭查询浏览器未完成：{error}")
        if browser_closed:
            browser_message = "查询浏览器已自动关闭，登录状态保留在本机。"
            self.login = {"kind": "closed", "message": "查询已完成，浏览器已关闭；下次查询会重新打开并复用登录状态"}
        else:
            browser_message = "查询浏览器未能自动关闭，请手动关闭。"
        self.log(summary + "；结果已保存，可导出 CSV")
        self.publish()
        self.events.put(("complete", {"summary": summary, "browser_closed": browser_closed,
                                    "counts": dict(counts), "total": len(self.store.items),
                                    "message": "队列已处理完毕。\n\n" + summary + "。\n\n" + browser_message
                                               + "\n结果已保存，可在主界面查看或导出 CSV。"}))

    def update_login(self):
        state = self.browser.login_state()
        if state != self.login:
            self.login = state
            self.events.put(("login", state))
        return state

    def wait_for_login(self, item, message=LOGIN_MESSAGE):
        changed = item.status != "login_required" or item.message != message
        if item.status == "manual":
            item.manual_required = True
        item.status, item.message = "login_required", message
        item.product = item.activation_time = item.checked_at = item.details = ""
        if changed:
            self.log(message)
            self.publish()

    def command(self, name, value=None):
        if name in ("pause", "open", "retry", "skip", "test_slider", "close"):
            self.cancel.set()
        if name == "close":
            self.closing.set()
        self.commands.put((name, value))

    def ensure_browser(self):
        if self.browser and (not self.browser.page or self.browser.page.is_closed()):
            self.browser.close()
            self.browser = None
        if self.browser is None:
            self.browser = self.browser_factory(self.data_dir, self.cancel, self.log)
            try:
                self.browser.open()
            except Exception:
                self.browser.close()
                self.browser = None
                raise
            item = self.store.current
            if item and item.status not in FINAL:
                # A new browser has a fresh form. Preserve the attempt budget
                # and manual handoff when reopening or restoring the queue.
                item.manual_required = item.manual_required or item.status == "manual"
                item.status = "pending"

    def manual(self, item, message=None):
        item.manual_required = True
        item.status = "manual"
        if message is None:
            prefix = f"已自动尝试 {item.slider_attempts} 次，达到上限；" if item.slider_attempts >= self.max_slider_attempts else ""
            message = prefix + "请手动完成滑块，验证通过后会自动查询"
        item.message = message
        self.log(message)
        self.publish()

    def prepare_item(self, item):
        refreshed = item.refresh_verification
        self.browser.prepare(item.sn, refresh=refreshed)
        item.refresh_verification = False
        if refreshed:
            self.must_reset_verification = ""
        if item.manual_required or not self.auto_drag or item.slider_attempts >= self.max_slider_attempts:
            self.manual(item)
        else:
            item.status, item.message = "ready", f"等待滑块验证（已尝试 {item.slider_attempts}/{self.max_slider_attempts} 次）"
            self.publish()

    def schedule_retry(self, item, reason):
        """Refresh once after a failure; manual mode never schedules more input."""
        item.manual_required = item.manual_required or item.slider_attempts >= self.max_slider_attempts or not self.auto_drag
        item.refresh_verification = True
        item.status = "retrying"
        delay = self.retry_delay * min(2.5, 1 + max(0, item.slider_attempts - 1) * 0.5)
        self.retry_at = time.monotonic() + delay
        next_action = "刷新后转为人工验证" if item.manual_required else "刷新后重新尝试"
        item.message = f"自动验证 {item.slider_attempts}/{self.max_slider_attempts} 次未通过：{reason}；{delay:g} 秒后{next_action}"
        self.log(item.message)
        self.publish()

    def slider_failed(self, item, outcome):
        reason = outcome.get("message", "官网未确认验证通过")
        # Missing/loading widgets can recover on a new page. Unsupported
        # puzzles or a mouse/window problem need the user, not repeated input.
        if outcome.get("code") in ("failed", "unconfirmed", "missing", "loading"):
            self.schedule_retry(item, reason)
        else:
            self.manual(item, reason + "；请手动完成滑块，或调整后点击重试当前")
        try:
            diagnostic = self.browser.diagnostic(item.message)
            self.log(f"诊断记录：{diagnostic.name}")
        except Exception as error:
            self.log(f"诊断保存失败：{error}")

    def submit(self, item):
        item.refresh_verification = False
        item.status, item.message = "querying", "正在等待官网结果"
        self.publish()
        self.record(item, self.browser.submit_and_wait(item.sn))

    def handle(self, name, value):
        if name == "add":
            added, invalid, duplicate = self.store.add(value)
            self.log(f"加入 {added} 条；忽略重复 {duplicate} 条；格式无效 {invalid} 条")
        elif name == "pause":
            self.running = False
            self.log("已暂停，可点击开始/继续")
        elif name == "settings":
            self.apply_settings(value)
        elif name in ("start", "open"):
            self.cancel.clear()
            self.apply_settings(value)
            if name == "start" and self.store.next_item() is None:
                self.running = False
                self.log("没有待查询的 SN；失败的条目可选中后点击重试")
                self.publish()
                return
            self.ensure_browser()
            if name == "start":
                self.running = True
                self.completion_armed = True
            else:
                self.running = False
                self.browser.show_login_window()
            self.update_login()
        elif name == "test_slider":
            self.running = False
            self.cancel.clear()
            self.apply_settings(value)
            self.ensure_browser()
            if self.store.current and self.store.current.status not in FINAL:
                self.store.current.status = "pending"
            self.browser.prepare("", refresh=True)
            outcome = self.browser.attempt_slider("", self.mode)
            self.log("滑块测试：" + outcome["message"])
            report = self.browser.diagnostic("滑块测试：" + outcome["message"])
            self.log("测试诊断：" + report.name + "（未提交设备查询）")
        elif name == "retry":
            item = next((row for row in self.store.items if row.id == value), self.store.current)
            if item:
                previous = self.store.current
                if previous and previous.id != item.id and previous.status not in FINAL:
                    previous.status = "pending"
                self.cancel.clear()
                self.store.current_id = item.id
                item.status = "pending"
                item.product = item.activation_time = item.checked_at = item.details = item.message = ""
                item.slider_attempts = 0
                item.manual_required = False
                item.refresh_verification = True
                self.retry_at = 0.0
                self.must_reset_verification = ""
                self.ensure_browser()
                self.running = True
                self.completion_armed = True
                auth = self.update_login()
                if auth["kind"] == "authenticated":
                    self.prepare_item(item)
                else:
                    self.wait_for_login(item, auth["message"])
        elif name == "skip":
            item = next((row for row in self.store.items if row.id == value), None)
            if item and item.status not in FINAL:
                item.status, item.message = "skipped", "用户跳过"
                self.must_reset_verification = ""
                self.cancel.clear()
                self.running = True
                self.completion_armed = True
        elif name == "clear":
            self.store.items = [item for item in self.store.items if item.status not in FINAL]
        self.publish()

    def record(self, item, result):
        if result.get("status") == "login_required":
            self.login = {"kind": "required", "message": result.get("message", LOGIN_MESSAGE)}
            self.wait_for_login(item, self.login["message"])
            return
        for name in ("status", "product", "activation_time", "details", "message"):
            if name in result:
                setattr(item, name, result[name])
        if item.status in FINAL:
            item.checked_at = now_text()
            item.refresh_verification = False
            self.must_reset_verification = ""
        if item.status in ("activated", "inactive"):
            item.message = "官网结果已记录"
            self.log(f"{item.sn}：{item.message}")
            if not self.continuous:
                self.running = False
        elif item.status == "manual":
            self.must_reset_verification = item.id
            if self.auto_drag and not item.manual_required:
                self.schedule_retry(item, item.message)
            else:
                self.manual(item, item.message + "；请在官网刷新验证后手动完成，或点击重试当前")
        else:
            self.running = False
            self.log(item.message)
            self.browser.diagnostic(item.message)
        self.publish()

    def step(self):
        item = self.store.next_item()
        if not item:
            self.running = False
            self.publish()
            return
        self.ensure_browser()
        auth = self.update_login()
        if auth["kind"] == "away":
            raise Cancelled(auth["message"])
        if auth["kind"] != "authenticated":
            self.wait_for_login(item, auth["message"])
            return
        if item.status == "login_required":
            # A login redirect can return directly to the result already requested.
            # Read it first so a successful query is not submitted a second time.
            result = self.browser.result(item.sn)
            if result:
                self.record(item, result)
                return
            item.status = "pending"
            self.log("已确认 DJI 登录，继续当前 SN")
        if item.status == "pending":
            self.prepare_item(item)
        if item.status == "retrying":
            self.browser._validate_page(item.sn)
            state = self.browser.inspect()
            # A late response or a manual solve during the cooldown wins over
            # refreshing, unless the query explicitly rejected this token.
            if state["kind"] == "passed" and self.must_reset_verification != item.id:
                self.submit(item)
                return
            if time.monotonic() < self.retry_at:
                return
            self.prepare_item(item)
        if item.status == "ready":
            self.browser._validate_page(item.sn)
            if self.browser.inspect()["kind"] == "passed" and self.must_reset_verification != item.id:
                self.submit(item)
                return
            if self.auto_drag and not item.manual_required and item.slider_attempts < self.max_slider_attempts:
                # Persist before moving. A pause, login redirect or restart
                # must not silently reset the limit and retry forever.
                item.slider_attempts += 1
                item.message = f"正在自动验证 {item.slider_attempts}/{self.max_slider_attempts} 次"
                self.publish()
                outcome = self.browser.attempt_slider(item.sn, self.mode)
                if not outcome["ok"]:
                    self.slider_failed(item, outcome)
                    return
            else:
                self.manual(item)
        if item.status in ("ready", "manual"):
            self.browser._validate_page(item.sn)
            state = self.browser.inspect()
            if self.must_reset_verification == item.id:
                if state["kind"] != "passed":
                    self.must_reset_verification = ""
                return
            if state["kind"] == "passed":
                self.submit(item)
        elif item.status == "querying":
            self.record(item, self.browser.wait_result(item.sn))

    def run(self):
        self.publish()
        try:
            while not self.closing.is_set():
                try:
                    try:
                        name, value = self.commands.get(timeout=0.15)
                        if name == "close":
                            break
                        self.handle(name, value)
                    except queue.Empty:
                        pass
                    if self.running:
                        self.step()
                    elif self.browser and not self.cancel.is_set() and time.monotonic() >= self.next_login_poll:
                        self.next_login_poll = time.monotonic() + 0.75
                        self.update_login()
                    self.finish_if_complete()
                except LoginRequired as error:
                    self.login = {"kind": "required", "message": str(error)}
                    if self.running and self.store.current:
                        self.wait_for_login(self.store.current, str(error))
                    else:
                        self.log(str(error))
                        self.publish()
                except Cancelled as error:
                    self.running = False
                    item = self.store.current
                    if item and item.status not in FINAL:
                        item.message = str(error)
                        if item.status != "querying":
                            if item.status == "ready" and item.slider_attempts:
                                item.refresh_verification = True
                            item.status = "pending"
                    self.log(str(error))
                    self.publish()
                except Exception as error:
                    self.running = False
                    item = self.store.current
                    if item and item.status not in FINAL:
                        item.message = str(error)
                    self.log(f"操作未完成：{error}")
                    if self.browser:
                        try:
                            self.browser.diagnostic(str(error))
                        except Exception:
                            pass
                    self.publish()
        finally:
            if self.browser:
                try:
                    self.browser.close()
                except Exception as error:
                    self.log(f"关闭查询浏览器：{error}")
            self.events.put(("closed", None))
