"""Own browser process, read-only DOM inspection, and bounded slider attempts."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
import urllib.request
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from threading import Event
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from .core import LOGIN_MESSAGE, login_required, parse_result
from .native_mouse import Cancelled, MouseUnavailable, SystemMouse, drag_path
from .tracks import TRACK_SOURCE, TRACK_VARIANT
from .paths import external_browser_dlls, resource_root

ROOT = resource_root()
QUERY_URL = "https://repair.dji.com/device/search?re=cn&lang=zh-CN"
SEARCH_URL = QUERY_URL
INPUT = 'input[placeholder*="序列号"]'
SN_TAB = '.device-page .tabs-nav #tab-1'


class LoginRequired(Exception):
    def __init__(self, message=LOGIN_MESSAGE):
        super().__init__(message)


def login_page(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (
        parsed.hostname in {"account.dji.com", "accounts.dji.com"}
        or (parsed.hostname == "repair.dji.com" and parsed.path.lower().startswith(("/login", "/auth")))
    )


def allowed_page(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc == "repair.dji.com" and parsed.path.startswith("/device/")


def find_browser() -> Path:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("没有找到 Chrome 或 Edge，请先安装其中一个浏览器")


class BrowserMouse:
    def __init__(self, page):
        self.page, self.pressed = page, False

    def move(self, x, y):
        self.page.mouse.move(x, y)

    def down(self):
        self.pressed = True
        self.page.mouse.down()

    def up(self):
        if self.pressed:
            try:
                self.page.mouse.up()
            finally:
                self.pressed = False


class BrowserSession:
    def __init__(self, data_dir: Path, cancel: Event, log=lambda text: None, *, headless=False,
                 executable: Path | None = None, page=None, pid=0):
        self.data_dir, self.cancel, self.log = Path(data_dir), cancel, log
        self.headless, self.executable = headless, executable
        self.process = None
        self.playwright = None
        self.browser = None
        self.context = page.context if page else None
        self.page, self.pid = page, pid
        self.last_sn = ""
        self.owns_browser = False
        self.last_trace = {}
        self.last_prepare = {}
        self.scripts = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in ("core.js", "slider.js"))
        if page:
            self.context.add_init_script(script=self.scripts)
            page.evaluate(self.scripts)

    def check(self):
        if self.cancel.is_set():
            raise Cancelled("已暂停")
        if self.page and self.page.is_closed():
            raise RuntimeError("查询浏览器已关闭，请点击开始重新打开")

    def sleep(self, seconds):
        if self.cancel.wait(max(0, seconds)):
            raise Cancelled("已暂停")

    def open(self, url=QUERY_URL):
        self.check()
        if self.page and not self.page.is_closed():
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        profile = self.data_dir / "browser-profile"
        profile.mkdir(exist_ok=True)
        executable = self.executable or find_browser()
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        endpoint = f"http://127.0.0.1:{port}"
        args = [str(executable), f"--user-data-dir={profile.resolve()}", f"--remote-debugging-port={port}",
                "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check",
                "--window-size=1280,900", "--new-window", "about:blank"]
        if self.headless:
            args.insert(-1, "--headless=new")
        self.log("正在打开独立浏览器…")
        with external_browser_dlls():
            self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.pid = self.process.pid
        self.owns_browser = True
        deadline = time.monotonic() + 25
        # Never send loopback DevTools requests through the user's HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline:
            self.check()
            try:
                with opener.open(endpoint + "/json/version", timeout=0.7) as response:
                    json.load(response)
                break
            except (OSError, ValueError):
                if self.process.poll() is not None:
                    raise RuntimeError("独立浏览器启动失败。请关闭上次由 Python 版打开的浏览器窗口后重试")
                self.sleep(0.15)
        else:
            raise RuntimeError("无法连接独立浏览器，请关闭上次的查询窗口后重试")
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.connect_over_cdp(endpoint, timeout=8000)
        self.context = self.browser.contexts[0]
        self.context.add_init_script(script=self.scripts)
        self.context.set_default_timeout(5000)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self.page.bring_to_front()
        self.log("浏览器已连接，等待官网表单加载")

    def _validate_page(self, sn: str | None = None):
        self.check()
        if not allowed_page(self.page.url):
            if login_page(self.page.url):
                raise LoginRequired()
            raise Cancelled("浏览器已离开大疆设备查询页，已暂停")
        if sn is not None:
            input_box = self.page.locator(INPUT)
            if input_box.count() != 1 or input_box.input_value().strip().upper() != sn:
                raise Cancelled("官网输入框中的 SN 已改变，已暂停；继续后会重新填入队列 SN")

    def login_state(self) -> dict:
        """Read only the query page's login indicators, never account form fields."""
        if not self.page or self.page.is_closed():
            return {"kind": "closed", "message": "请打开查询浏览器并登录 DJI 账号"}
        if login_page(self.page.url):
            return {"kind": "required", "message": LOGIN_MESSAGE}
        if not allowed_page(self.page.url):
            return {"kind": "away", "message": "请在查询窗口返回大疆设备查询页后继续"}
        try:
            state = self.page.evaluate(r"""() => {
              const visible = node => !!node && !!node.getClientRects().length &&
                getComputedStyle(node).visibility !== 'hidden';
              const page = document.querySelector('.device-page');
              const loading = [...document.querySelectorAll('.el-loading-mask')].some(visible);
              const prompt = [...document.querySelectorAll('.device-login, #deviceVerifyCodeNoLogin')].some(visible);
              const tabs = !!page?.querySelector('.tabs-nav #tab-1, .tabs-nav [role="tab"]');
              // Element UI keeps the account dropdown hidden until it is opened.
              // Its logout item is rendered only for a signed-in account.
              const logout = [...document.querySelectorAll('.myCenter .el-dropdown-menu__item, .myCenter .mobileShow span')]
                .some(node => /^(退出(?:登录|登陆)?|登出|log\s*out|sign\s*out)$/i.test(node.textContent.trim()));
              const messages = [...document.querySelectorAll('.device-page, .el-message, .el-dialog')]
                .filter(visible).map(node => node.innerText || '').join('\n');
              return {loading, prompt, signedIn: tabs || logout, messages};
            }""")
        except PlaywrightError as error:
            if "Execution context was destroyed" not in str(error) and "Cannot find context" not in str(error):
                raise
            return {"kind": "loading", "message": "正在等待官网完成登录跳转"}
        if state["loading"]:
            return {"kind": "loading", "message": "正在等待官网确认登录状态"}
        if state["prompt"] or login_required(state["messages"]):
            return {"kind": "required", "message": LOGIN_MESSAGE}
        if state["signedIn"]:
            return {"kind": "authenticated", "message": "已登录 DJI 账号；本机查询窗口会保留登录状态"}
        return {"kind": "unknown", "message": "尚未确认登录状态，请在查询窗口登录并返回设备查询页"}

    def require_login(self):
        state = self.login_state()
        if state["kind"] != "authenticated":
            raise LoginRequired(state["message"])

    def show_login_window(self):
        self.open()
        if (allowed_page(self.page.url) and urlparse(self.page.url).path != "/device/search"
                and self.login_state()["kind"] == "required"):
            self.page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
        self.page.bring_to_front()
        self.log("查询窗口已打开；已登录时可直接点击“开始 / 继续”，官网提示登录时再在此窗口登录")

    def inspect(self) -> dict:
        self._validate_page()
        try:
            return self.page.evaluate("""() => typeof DJISNSlider === 'object' ? DJISNSlider.inspect()
                : {kind: 'loading', url: location.href, message: '等待官网完成跳转'}""")
        except PlaywrightError as error:
            if "Execution context was destroyed" not in str(error) and "Cannot find context" not in str(error):
                raise
            return {"kind": "loading", "url": self.page.url, "message": "等待官网完成跳转"}

    def form_visible(self):
        return self.page.locator(INPUT).count() == 1 and self.page.locator(INPUT).is_visible()

    def prepare(self, sn: str, *, refresh=False, timeout=25):
        self.open()
        self._validate_page()
        trace = self.last_prepare = {"from_path": urlparse(self.page.url).path,
                                     "tabs_before": len(self.context.pages), "navigated": False,
                                     "phase": "opening_form"}
        if (refresh or (self.last_sn and self.last_sn != sn)
                or urlparse(self.page.url).path.rstrip("/") != "/device/search"
                or (not self.form_visible() and not self.page.locator(SN_TAB).count())):
            # DJI's "query another device" button calls window.open(..., '_blank').
            # Navigate the page we already own, keeping the same tab and login.
            self.log("正在当前标签页返回设备查询页…")
            self.page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
            trace["navigated"] = True
        trace["phase"] = "waiting_form"
        deadline = time.monotonic() + timeout
        while True:
            self._validate_page()
            auth = self.login_state()
            if sn and auth["kind"] == "required":
                raise LoginRequired(auth["message"])
            # DJI defaults to the bound-device tab after signing in.
            sn_tab = self.page.locator(SN_TAB)
            if (sn_tab.count() == 1 and sn_tab.is_visible()
                    and sn_tab.get_attribute("aria-selected") != "true"
                    and auth["kind"] != "loading"):
                sn_tab.click()
            if (self.form_visible() and self.page.locator(INPUT).is_enabled()
                    and auth["kind"] != "loading"
                    and (not sn or auth["kind"] == "authenticated")):
                break
            if time.monotonic() > deadline:
                if sn and auth["kind"] != "authenticated":
                    raise LoginRequired(auth["message"])
                raise RuntimeError(f"当前标签页的 SN 查询表单在 {timeout:g} 秒内未就绪，请检查页面后重试")
            self.sleep(0.2)
        if sn:
            self.require_login()
        self.page.locator(INPUT).fill(sn)
        self.last_sn = sn
        trace.update(phase="ready", path=urlparse(self.page.url).path, tabs_after=len(self.context.pages))
        self.log(f"已填入 {sn}，等待滑块加载" if sn else "查询表单已就绪，等待滑块加载")

    def wait_slider(self, timeout=20):
        deadline = time.monotonic() + timeout
        state = self.inspect()
        while state["kind"] in ("missing", "loading") and time.monotonic() < deadline:
            self.sleep(0.2)
            state = self.inspect()
        return state

    def attempt_slider(self, sn: str, mode="system", *, duration=None) -> dict:
        trace = self.last_trace = {"mode": mode, "pid": self.pid, "mouse_down": False, "moves": 0, "mouse_up": False}
        mouse = None
        try:
            if sn:
                self.require_login()
            self._validate_page(sn)
            state = self.wait_slider()
            if state["kind"] == "passed":
                return {"ok": True, "message": "官网已确认验证通过"}
            if state["kind"] != "slider":
                return {"ok": False, "code": state["kind"], "message": state.get("message", "请在浏览器中手动完成验证")}
            self.page.locator("#aliyunCaptcha-sliding-body").scroll_into_view_if_needed()
            self.page.bring_to_front()
            state = self.inspect()
            if state["kind"] != "slider":
                return {"ok": False, "code": state["kind"], "message": state.get("message", "滑块状态已改变")}
            geometry = self.page.evaluate("state => DJISNSlider.getGeometry(state)", state)
            trace["before"], trace["geometry"] = state, geometry
            path = tuple(drag_path(geometry["distance"], duration))
            trace["duration_s"] = path[-1][2]
            trace["duration_mode"] = "dynamic" if duration is None else "fixed"
            trace["track_source"] = TRACK_SOURCE
            trace["track_variant"] = TRACK_VARIANT
            trace["track_events"] = len(path)
            mouse = SystemMouse(self.pid, geometry["viewport"]) if mode == "system" else BrowserMouse(self.page)
            if mode == "system":
                trace["screen_mapping"] = asdict(mouse.mapping)
            start_x, start_y, end_x = geometry["x"], geometry["y"], geometry["endX"]
            # Native coordinates round to physical pixels. At 125% scaling an
            # exact 342 CSS-pixel target can arrive as 341 in MouseEvent.clientX.
            # Cross the end stop slightly, staying inside the rail and viewport.
            release_x = max(end_x, min(end_x + 3, state["rail"]["x"] + state["rail"]["width"] - 1,
                                      geometry["viewport"]["width"] - 1))
            trace["release_x"] = release_x
            self.page.evaluate("""() => {
              window.__djiPythonStopMouseTrace?.();
              const observed = {moves: 0, down: null, up: null};
              const handler = event => {
                if (event.type === 'mousemove') { observed.moves++; return; }
                const handle = document.getElementById('aliyunCaptcha-sliding-slider');
                observed[event.type === 'mousedown' ? 'down' : 'up'] = {
                  x: event.clientX, y: event.clientY, trusted: event.isTrusted,
                  onHandle: !!handle?.contains(event.target), target: event.target.id || event.target.tagName
                };
              };
              for (const type of ['mousedown', 'mousemove', 'mouseup']) document.addEventListener(type, handler, true);
              window.__djiPythonMouseTrace = observed;
              window.__djiPythonStopMouseTrace = () => {
                for (const type of ['mousedown', 'mousemove', 'mouseup']) document.removeEventListener(type, handler, true);
                delete window.__djiPythonStopMouseTrace;
                return observed;
              };
            }""")
            mouse.move(start_x, start_y)
            self.sleep(0.15)
            self._validate_page(sn)
            before_press = self.inspect()
            if before_press["kind"] != "slider":
                raise Cancelled("拖动前滑块状态已改变，请重试")
            current = self.page.evaluate("state => DJISNSlider.getGeometry(state)", before_press)
            if any(abs(current[key] - geometry[key]) > 2 for key in ("x", "y", "endX")):
                raise Cancelled("拖动前页面布局发生变化，请重试")
            mouse_name = "系统鼠标" if mode == "system" else "浏览器鼠标"
            self.log(f"正在使用{mouse_name}动态变速拖动滑块（约 {trace['duration_s']:.2f} 秒）…")
            mouse.down()
            trace["mouse_down"] = True
            self.sleep(0.08)
            down = self.page.evaluate("window.__djiPythonMouseTrace.down")
            if not down or not down["onHandle"]:
                raise MouseUnavailable("鼠标没有落到官网滑块上，请保持窗口稳定后重试，或切换浏览器鼠标模式")
            started = time.monotonic()
            for index, (dx, dy, scheduled) in enumerate(path):
                self.check()
                if index % 12 == 0:
                    self._validate_page(sn)
                    if not self.page.evaluate("document.visibilityState === 'visible'"):
                        raise Cancelled("查询标签页已切换，已停止拖动")
                self.sleep(scheduled - (time.monotonic() - started))
                mouse.move(start_x + dx, max(8, min(geometry["viewport"]["height"] - 8, start_y + dy)))
                trace["moves"] += 1
            mouse.move(release_x, max(8, min(geometry["viewport"]["height"] - 8, start_y + path[-1][1])))
            trace["actual_duration_s"] = time.monotonic() - started
            self.sleep(0.1)
            mouse.up()
            trace["mouse_up"] = True
            self.log("拖动已完成，等待官网确认验证结果")
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                self.sleep(0.2)
                self._validate_page(sn)
                state = self.inspect()
                trace["after"] = state
                if state["kind"] == "passed":
                    return {"ok": True, "message": "官网已确认验证通过"}
                if state["kind"] in ("failed", "unsupported"):
                    return {"ok": False, "code": state["kind"], "message": state.get("message", "请手动完成验证")}
            return {"ok": False, "code": "unconfirmed", "message": "鼠标已拖动，但官网未确认验证通过，请手动验证或重试"}
        except MouseUnavailable as error:
            return {"ok": False, "code": "mouse_unavailable", "message": str(error)}
        finally:
            try:
                if mouse is not None and mouse.pressed:
                    mouse.up()
                    trace["mouse_up"] = True
            finally:
                try:
                    observed = self.page.evaluate("window.__djiPythonStopMouseTrace?.() || null")
                    if observed:
                        trace["observed_mouse"] = observed
                except PlaywrightError:
                    pass

    def result(self, sn: str):
        self._validate_page()
        auth = self.login_state()
        if auth["kind"] == "required":
            return {"status": "login_required", "message": auth["message"]}
        if auth["kind"] != "authenticated":
            return None
        texts = self.page.evaluate("""() => {
          const nodes = [...document.querySelectorAll('.device-page .active-wrapper, .device-page .detail-wrapper, .device-detail, .device-info, main')];
          return [...nodes.map(n => n.innerText || ''), document.body?.innerText || ''].filter(Boolean).map(t => t.slice(0, 15000));
        }""")
        for text in texts:
            parsed = parse_result(text, sn)
            if parsed:
                return parsed
        return None

    def submit_and_wait(self, sn: str, timeout=25) -> dict:
        self.require_login()
        self._validate_page(sn)
        if self.inspect()["kind"] != "passed":
            return {"status": "manual", "message": "官网尚未确认验证通过"}
        button = self.page.get_by_role("button", name=re.compile(r"^\s*查询\s*$")).first
        if not button.count() or not button.is_enabled():
            raise RuntimeError("没有找到可用的官网查询按钮")
        button.click()
        self.log(f"正在等待 {sn} 的查询结果")
        return self.wait_result(sn, timeout)

    def wait_result(self, sn: str, timeout=25) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check()
            result = self.result(sn)
            if result:
                return result
            errors = self.page.locator(".el-message--error, .el-message--warning")
            for index in range(errors.count()):
                node = errors.nth(index)
                if node.is_visible():
                    message = node.inner_text().strip()[:500]
                    if message:
                        if login_required(message):
                            return {"status": "login_required", "message": LOGIN_MESSAGE}
                        return {"status": "manual" if re.search(r"滑块|验证|请输入", message) else "error",
                                "message": message, "details": message}
            self.sleep(0.2)
        return {"status": "error", "message": "官网在 25 秒内没有返回与当前 SN 匹配的结果，请检查页面后重试"}

    def diagnostic(self, reason: str) -> Path:
        folder = self.data_dir / "diagnostics"
        folder.mkdir(parents=True, exist_ok=True)
        prefix = folder / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        payload = {"time": datetime.now().isoformat(), "reason": reason, "trace": self.last_trace,
                   "prepare": self.last_prepare}
        try:
            payload["page"] = self.inspect()
            self.page.screenshot(path=str(prefix.with_suffix(".png")))
        except Exception as error:
            payload["capture_error"] = str(error)
        prefix.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return prefix.with_suffix(".json")

    def close(self):
        try:
            if self.browser and self.owns_browser:
                # close() on a CDP-connected Playwright Browser can merely
                # disconnect. Ask our own browser process to shut down first.
                try:
                    session = self.browser.new_browser_cdp_session()
                    session.send("Browser.close")
                except PlaywrightError:
                    pass  # A successful close can disconnect before replying.
                self.browser.close()
        finally:
            if self.playwright:
                self.playwright.stop()
            if self.process and self.process.poll() is None:
                # This is only the subprocess launched with this app's private profile.
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=3)
            self.browser = self.context = self.page = self.playwright = None
