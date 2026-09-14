"""Build, inspect and offline-test a single-file Windows release."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EXE_NAME = "DJI-SN-Checker.exe"


def main():
    if sys.platform != "win32" or sys.maxsize <= 2 ** 32:
        raise SystemExit("Build this release with 64-bit Python on Windows.")
    from PyInstaller.archive.readers import CArchiveReader

    DIST.mkdir(exist_ok=True)
    work = ROOT / ".work" / "exe-build"
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--log-level", "WARN",
                    "--distpath", str(DIST), "--workpath", str(work), str(ROOT / "DJI-SN-Checker.spec")],
                   cwd=ROOT, check=True)
    exe = DIST / EXE_NAME
    archive = CArchiveReader(str(exe))
    names = {name.replace("\\", "/") for name in archive.toc}
    required = {"core.js", "slider.js", "selftest/device.html", "python311.dll", "_tkinter.pyd",
                "playwright/driver/node.exe", "playwright/driver/package/cli.js",
                "licenses/slider-captcha-lab/LICENSE"}
    missing = required - names
    if missing:
        raise RuntimeError(f"Missing bundled resources: {sorted(missing)}")
    forbidden = [name for name in names if any(part in name.lower() for part in (
        ".python-data/", "browser-profile/", "/queue.json", "node_modules/", "diagnostics/",
    ))]
    if forbidden:
        raise RuntimeError(f"Unexpected local data in release: {forbidden}")
    pyz = archive.open_embedded_archive("PYZ.pyz")
    assert "dji_checker._vendor.slider_captcha_lab.human_track" in pyz.toc

    # Only the EXE is copied. No .venv, source scripts or Python on PATH.
    with tempfile.TemporaryDirectory(prefix="EXE 独立运行 ", dir=work) as temporary:
        isolated = Path(temporary).resolve()
        standalone = isolated / EXE_NAME
        shutil.copy2(exe, standalone)
        env = os.environ.copy()
        for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "TCL_LIBRARY", "TK_LIBRARY", "PLAYWRIGHT_NODEJS_PATH"):
            env.pop(name, None)
        windows = Path(env.get("SystemRoot", "C:/Windows"))
        env["PATH"] = str(windows / "System32") + os.pathsep + str(windows)
        report = isolated / "self-test.json"
        print("Checking the EXE from an isolated directory without Python on PATH...", flush=True)
        result = subprocess.run([str(standalone), "--self-test", str(report)], cwd=isolated, env=env,
                                timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
        if not report.is_file():
            raise RuntimeError(f"EXE did not write its self-test report (exit {result.returncode}).")
        checks = json.loads(report.read_text(encoding="utf-8"))
        output = ROOT / "test-results"
        output.mkdir(exist_ok=True)
        shutil.copy2(report, output / "exe-self-test.json")
        if result.returncode or not checks.get("passed") or not checks.get("frozen") or checks.get("python_on_path"):
            raise RuntimeError("EXE self-test failed: " + json.dumps(checks, ensure_ascii=False))

    # Export only known bundled licenses, including Playwright's Node runtime.
    license_exports = []
    extra_licenses = {
        "playwright/driver/LICENSE": "licenses/node/LICENSE",
        "playwright/driver/package/NOTICE": "licenses/playwright/NOTICE",
        "playwright/driver/package/ThirdPartyNotices.txt": "licenses/playwright/ThirdPartyNotices.txt",
    }
    for name in sorted(names):
        target = name if name.startswith("licenses/") else extra_licenses.get(name)
        if target:
            destination = DIST / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            original = next(key for key in archive.toc if key.replace("\\", "/") == name)
            destination.write_bytes(archive.extract(original))
            license_exports.append(destination)
    guide = DIST / "使用说明.txt"
    shutil.copy2(ROOT / "windows/使用说明.txt", guide)
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    info = {"version": "1.2.0", "built_at_utc": datetime.now(timezone.utc).isoformat(),
            "target": "Windows 10/11 x64", "single_file": True, "system_python_required": False,
            "browser_required": "Microsoft Edge or Google Chrome", "exe_sha256": digest,
            "exe_size_bytes": exe.stat().st_size, "offline_self_test_passed": True,
            "components": {name: version(name) for name in ("pyinstaller", "playwright", "numpy", "greenlet", "pyee")}}
    (DIST / "build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    bundle = DIST / "DJI-SN-Checker-Windows-x64.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for file in (exe, guide, DIST / "build-info.json", *license_exports):
            if file.is_file():
                package.write(file, Path("DJI-SN-Checker") / file.relative_to(DIST))
    zip_digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    (DIST / "SHA256SUMS.txt").write_text(f"{digest}  {exe.name}\n{zip_digest}  {bundle.name}\n", encoding="ascii")
    print(json.dumps({"exe": str(exe), "zip": str(bundle), "size_mib": round(exe.stat().st_size / 1024 ** 2, 1),
                      "sha256": digest, "offline_self_test": "passed"}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream and not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    main()
