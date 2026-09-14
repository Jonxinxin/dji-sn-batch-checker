"""Queue data and result parsing, independent of the browser and GUI."""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from uuid import uuid4

SN_PATTERN = re.compile(r"^[A-Z0-9]{14,20}$")
FINAL = {"activated", "inactive", "unknown", "error", "skipped"}
LABELS = {
    "pending": "待查询", "ready": "待验证", "retrying": "等待重试", "manual": "待人工验证", "querying": "查询中", "login_required": "等待登录",
    "activated": "已激活", "inactive": "未激活", "unknown": "待确认", "error": "查询失败", "skipped": "已跳过",
}
DATE_PATTERN = r"(?:19|20)\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
LOGIN_MESSAGE = "请在本程序打开的查询窗口登录 DJI 账号；返回设备查询页后会继续，登录状态会保留在本机"
LOGIN_REQUIRED_PATTERN = re.compile(
    r"(?:请|需要|需先|尚未|还未|未)\s*(?:先|重新)?\s*登[录陆]"
    r"|登[录陆](?:状态|会话)?\s*(?:已)?\s*(?:失效|过期)"
    r"|登[录陆][^\n。]{0,40}(?:后|以|即可|才能|可)[^\n。]{0,30}(?:查看|查询)"
    r"|(?:log\s*in|sign\s*in)\s+to\s+(?:view|see|check)|session\s+expired", re.I
)
DEFAULT_SETTINGS = {"mode": "system", "auto_drag": True, "continuous": True, "max_slider_attempts": 3}
MAX_SLIDER_ATTEMPTS = 10


def normalize_settings(values: dict | None) -> dict:
    values = values if isinstance(values, dict) else {}
    settings = {**DEFAULT_SETTINGS}
    if values.get("mode") in ("system", "browser"):
        settings["mode"] = values["mode"]
    for key in ("auto_drag", "continuous"):
        if isinstance(values.get(key), bool):
            settings[key] = values[key]
    try:
        count = int(values.get("max_slider_attempts", settings["max_slider_attempts"]))
        settings["max_slider_attempts"] = max(1, min(MAX_SLIDER_ATTEMPTS, count))
    except (TypeError, ValueError, OverflowError):
        pass
    return settings


def login_required(text: str) -> bool:
    return bool(LOGIN_REQUIRED_PATTERN.search(text))


def parse_sns(text: str, existing=()) -> tuple[list[str], list[str], list[str]]:
    seen = {str(sn).strip().upper() for sn in existing}
    valid, invalid, duplicates = [], [], []
    for sn in re.split(r"[\s,，;；]+", text.strip().upper()):
        if not sn:
            continue
        if not SN_PATTERN.fullmatch(sn):
            invalid.append(sn)
        elif sn in seen:
            duplicates.append(sn)
        else:
            seen.add(sn)
            valid.append(sn)
    return valid, invalid, duplicates


def parse_result(text: str, sn: str) -> dict | None:
    text = re.sub(r"[ \t\xa0]+", " ", text).strip()
    # A serial number must match a complete token, not a substring of another SN.
    if not re.search(r"(?<![A-Z0-9])" + re.escape(sn.upper()) + r"(?![A-Z0-9])", text.upper()):
        return None
    if login_required(text):
        return {"status": "login_required", "message": LOGIN_MESSAGE}
    activation = re.search(r"激活时间\s*[:：]?\s*(" + DATE_PATTERN + r")", text)
    inactive = re.search(r"未激活|尚未激活|未查询到激活|暂无激活时间|激活时间\s*[:：]?\s*(?:--|—|无|暂无)", text)
    if not activation and not inactive:
        return None
    product = re.search(r"(?:产品名称|设备名称|产品型号|设备型号)\s*[:：]?\s*([^\n]{1,80})", text)
    if not product:
        product = re.search(r"(?:^|\n)\s*([^\n:：]{2,80})\s*\n\s*序列号\s*[:：]", text)
    return {
        "status": "inactive" if inactive else "activated",
        "product": product.group(1).strip() if product else "",
        "activation_time": activation.group(1).strip() if activation else "",
        "details": text[:15000],
    }


@dataclass
class Item:
    sn: str
    id: str = ""
    status: str = "pending"
    product: str = ""
    activation_time: str = ""
    checked_at: str = ""
    details: str = ""
    message: str = ""
    slider_attempts: int = 0
    manual_required: bool = False
    refresh_verification: bool = False

    def __post_init__(self):
        if not self.id:
            self.id = uuid4().hex
        try:
            self.slider_attempts = max(0, int(self.slider_attempts))
        except (TypeError, ValueError, OverflowError):
            self.slider_attempts = 0


class QueueStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.items: list[Item] = []
        self.current_id = ""
        self.settings = normalize_settings(None)
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            allowed = {field.name for field in fields(Item)}
            for raw in payload.get("items", []):
                if not isinstance(raw, dict) or not SN_PATTERN.fullmatch(str(raw.get("sn", ""))):
                    continue
                item = Item(**{key: value for key, value in raw.items() if key in allowed})
                if item.status not in LABELS:
                    item.status = "pending"
                if item.status == "manual":
                    item.manual_required = True
                self.items.append(item)
            self.current_id = str(payload.get("current_id", ""))
            self.settings = normalize_settings(payload.get("settings"))

    @property
    def current(self) -> Item | None:
        return next((item for item in self.items if item.id == self.current_id), None)

    def next_item(self) -> Item | None:
        current = self.current
        if current and current.status not in FINAL:
            return current
        item = next((row for row in self.items if row.status == "pending"), None)
        self.current_id = item.id if item else self.current_id
        return item

    def add(self, text: str) -> tuple[int, int, int]:
        valid, invalid, duplicates = parse_sns(text, (item.sn for item in self.items))
        self.items.extend(Item(sn=sn) for sn in valid)
        self.save()
        return len(valid), len(invalid), len(duplicates)

    def snapshot(self) -> dict:
        return {"items": [asdict(item) for item in self.items], "current_id": self.current_id, "settings": dict(self.settings)}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def csv_text(items: list[dict]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["SN", "查询状态", "产品", "激活时间", "查询时间", "官网结果原文"])
    for item in items:
        row = [item.get("sn", ""), LABELS.get(item.get("status"), item.get("status", "")),
               item.get("product", ""), item.get("activation_time", ""), item.get("checked_at", ""),
               item.get("details") or item.get("message", "")]
        # Keep website text from becoming an executable spreadsheet formula.
        writer.writerow(["'" + str(value) if str(value).lstrip().startswith(("=", "+", "-", "@")) else value for value in row])
    return "\ufeff" + output.getvalue()
