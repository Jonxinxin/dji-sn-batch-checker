"""Windows mouse input scoped to the application's own browser window."""
from __future__ import annotations

import ctypes
import math
import os
import time
from ctypes import wintypes
from dataclasses import dataclass

from .tracks import drag_path


class Cancelled(Exception):
    pass


class MouseUnavailable(RuntimeError):
    pass


def enable_dpi_awareness():
    if os.name == "nt":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass


@dataclass(frozen=True)
class ViewportMapping:
    x: int
    y: int
    width: int
    height: int
    css_width: float
    css_height: float

    def point(self, x: float, y: float) -> tuple[int, int]:
        if not (0 <= x < self.css_width and 0 <= y < self.css_height):
            raise MouseUnavailable("滑块没有完整显示在浏览器窗口内")
        return round(self.x + x * self.width / self.css_width), round(self.y + y * self.height / self.css_height)


class SystemMouse:
    """SendInput uses physical screen coordinates, including DPI and zoom."""
    def __init__(self, pid: int, viewport: dict):
        if os.name != "nt":
            raise MouseUnavailable("系统鼠标模式仅支持 Windows，请选择浏览器鼠标模式")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._configure_api()
        try:
            self.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            pass
        self.pressed = False
        self.last_position = None
        roots = []

        def root_callback(hwnd, _):
            process_id = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            if process_id.value == pid and self.user32.IsWindowVisible(hwnd) and self._class(hwnd) == "Chrome_WidgetWin_1":
                roots.append(hwnd)
            return True

        self.user32.EnumWindows(self.callback_type(root_callback), 0)
        if len(roots) != 1:
            raise MouseUnavailable(f"未能定位独立浏览器窗口（找到 {len(roots)} 个）。请关闭本程序打开的多余窗口后重试，或选择浏览器鼠标模式")
        self.hwnd = roots[0]
        if self.user32.IsIconic(self.hwnd):
            self.user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
        self.user32.SetForegroundWindow(self.hwnd)
        # Windows activation crosses process message queues and can complete
        # just after SetForegroundWindow returns. Do not press during that gap.
        deadline = time.monotonic() + 0.7
        while self.user32.GetAncestor(self.user32.GetForegroundWindow(), 2) != self.hwnd and time.monotonic() < deadline:
            if self.user32.GetAsyncKeyState(0x1B) & 0x8000:
                raise Cancelled("已按 Esc 停止")
            time.sleep(0.025)
        self.check_foreground()
        children = []

        def child_callback(hwnd, _):
            if self._class(hwnd) == "Chrome_RenderWidgetHostHWND" and self.user32.IsWindowVisible(hwnd):
                x, y, width, height = self._rectangle(hwnd)
                if width > 100 and height > 100:
                    children.append((width * height, hwnd, x, y, width, height))
            return True

        self.user32.EnumChildWindows(self.hwnd, self.callback_type(child_callback), 0)
        if not children:
            raise MouseUnavailable("浏览器未暴露页面坐标，无法安全定位系统鼠标；请选择浏览器鼠标模式后重试")
        _, self.viewport_hwnd, x, y, width, height = max(children)
        scale_x, scale_y = width / viewport["width"], height / viewport["height"]
        if not 0.5 <= scale_x <= 5 or abs(scale_x - scale_y) > 0.04:
            raise MouseUnavailable("浏览器缩放或页面坐标不一致，请保持窗口稳定后重试")
        self.mapping = ViewportMapping(x, y, width, height, viewport["width"], viewport["height"])

    def _configure_api(self):
        api = self.user32
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        for name, args, result in [
            ("EnumWindows", [self.callback_type, wintypes.LPARAM], wintypes.BOOL),
            ("EnumChildWindows", [wintypes.HWND, self.callback_type, wintypes.LPARAM], wintypes.BOOL),
            ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            ("GetClassNameW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
            ("IsIconic", [wintypes.HWND], wintypes.BOOL),
            ("GetClientRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            ("ClientToScreen", [wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            ("GetCursorPos", [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            ("GetForegroundWindow", [], wintypes.HWND),
            ("GetAncestor", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            ("WindowFromPoint", [wintypes.POINT], wintypes.HWND),
            ("SetForegroundWindow", [wintypes.HWND], wintypes.BOOL),
            ("ShowWindow", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            ("GetAsyncKeyState", [ctypes.c_int], ctypes.c_short),
            ("GetSystemMetrics", [ctypes.c_int], ctypes.c_int),
        ]:
            function = getattr(api, name)
            function.argtypes, function.restype = args, result

        class MouseInput(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class InputUnion(ctypes.Union):
            _fields_ = [("mi", MouseInput)]

        class Input(ctypes.Structure):
            _anonymous_ = ("value",)
            _fields_ = [("type", wintypes.DWORD), ("value", InputUnion)]

        self.Input = Input
        api.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        api.SendInput.restype = wintypes.UINT

    def _class(self, hwnd):
        value = ctypes.create_unicode_buffer(128)
        self.user32.GetClassNameW(hwnd, value, len(value))
        return value.value

    def _rectangle(self, hwnd):
        rect, origin = wintypes.RECT(), wintypes.POINT()
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)) or not self.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise MouseUnavailable("无法读取浏览器窗口坐标")
        return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top

    def check_foreground(self):
        if self.user32.GetAsyncKeyState(0x1B) & 0x8000:
            raise Cancelled("已按 Esc 停止")
        if self.user32.GetAncestor(self.user32.GetForegroundWindow(), 2) != self.hwnd:
            raise Cancelled("查询浏览器不在前台，已停止鼠标操作；切回查询窗口后重试")

    def check(self):
        self.check_foreground()
        current = self._rectangle(self.viewport_hwnd)
        if current != (self.mapping.x, self.mapping.y, self.mapping.width, self.mapping.height):
            raise Cancelled("浏览器窗口位置或大小发生变化，已停止拖动")
        if self.last_position:
            cursor = wintypes.POINT()
            self.user32.GetCursorPos(ctypes.byref(cursor))
            if math.hypot(cursor.x - self.last_position[0], cursor.y - self.last_position[1]) > 16:
                raise Cancelled("检测到手动移动鼠标，已停止自动拖动")

    def _send(self, flags, x=0, y=0):
        event = self.Input()
        event.type = 0  # INPUT_MOUSE
        event.mi.dx, event.mi.dy, event.mi.dwFlags = x, y, flags
        if self.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
            raise MouseUnavailable("Windows 未接受鼠标输入，请勿以管理员身份运行浏览器")

    def move(self, x: float, y: float):
        self.check()
        screen_x, screen_y = self.mapping.point(x, y)
        origin_x, origin_y, width, height = (self.user32.GetSystemMetrics(code) for code in (76, 77, 78, 79))
        self._send(0x0001 | 0x8000 | 0x4000,
                   round((screen_x - origin_x) * 65535 / (width - 1)),
                   round((screen_y - origin_y) * 65535 / (height - 1)))
        self.last_position = (screen_x, screen_y)

    def down(self):
        self.check()
        x, y = self.last_position
        hit = self.user32.WindowFromPoint(wintypes.POINT(x, y))
        if self.user32.GetAncestor(hit, 2) != self.hwnd:
            raise MouseUnavailable("滑块被其他窗口遮挡，请切回查询窗口")
        self.pressed = True
        self._send(0x0002)

    def up(self):
        if self.pressed:
            try:
                self._send(0x0004)
            finally:
                self.pressed = False
