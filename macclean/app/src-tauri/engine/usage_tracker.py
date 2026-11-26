#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macclean 轻量使用记录器。

后台每 30 秒采样一次「正在运行的 App」，把首次看到的时间记到本地 JSON。
它只能记录「从安装记录器之后」的使用情况，补不回历史。

    python3 usage_tracker.py once      # 采样一次（cron 用）
    python3 usage_tracker.py loop      # 常驻，30 秒一次（LaunchAgent 用）
    python3 usage_tracker.py install   # 安装 LaunchAgent 并启动
    python3 usage_tracker.py uninstall # 停掉并移除 LaunchAgent（保留数据）
    python3 usage_tracker.py status    # 是否在运行 + 记录了多少个应用
    python3 usage_tracker.py report    # 打印记录
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

LABEL = "com.macclean.usage"
SUPPORT = Path.home() / "Library" / "Application Support" / "macclean"
DATA = SUPPORT / "usage.json"
SCRIPT = SUPPORT / "usage_tracker.py"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
PIDFILE = SUPPORT / "usage.pid"
INTERVAL = 30

BLOCK_RE = re.compile(r'^\s*\d+\)\s+"([^"]*)"')


def running_apps() -> list:
    """解析 lsappinfo，返回 [(bundle_id, name, pid)]。"""
    try:
        out = subprocess.run(["lsappinfo", "list"], capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    if out.returncode != 0:
        return []

    apps = []
    current = None
    for line in out.stdout.splitlines():
        head = BLOCK_RE.match(line)
        if head:
            current = {"display": head.group(1), "bundle": "", "path": "", "pid": 0}
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("bundleID="):
            value = stripped.split("=", 1)[1].strip().strip('"')
            current["bundle"] = "" if value in ("[ NULL ]", "NULL") else value
        elif stripped.startswith("bundle path="):
            current["path"] = stripped.split("=", 1)[1].strip().strip('"')
        elif stripped.startswith("pid ="):
            match = re.search(r"pid\s*=\s*(\d+)", stripped)
            if match:
                current["pid"] = int(match.group(1))
            path = current["path"]
            if (
                current["bundle"]
                and current["pid"]
                and path.endswith(".app")
                and "/Applications/" in path
                and not path.startswith("/System/Library")
            ):
                name = Path(path).stem
                apps.append((current["bundle"], name, current["pid"]))
            current = None
    return apps


def load() -> dict:
    try:
        with open(DATA, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "apps" not in data:
            data["apps"] = {}
        return data
    except Exception:
        return {"version": 1, "apps": {}}


def save(data: dict) -> None:
    SUPPORT.mkdir(parents=True, exist_ok=True)
    tmp = DATA.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA)


def sample() -> int:
    now = time.time()
    apps = running_apps()
    data = load()
    seen = data.get("seen", {})
    live = set()

    for bundle, name, pid in apps:
        live.add(bundle)
        record = data["apps"].get(bundle) or {
            "name": name,
            "first_seen": now,
            "launches": 0,
        }
        record["name"] = name
        record["last_seen"] = now
        if seen.get(bundle) != pid:
            record["launches"] = record.get("launches", 0) + 1
            record["last_launch"] = now
            seen[bundle] = pid
        data["apps"][bundle] = record

    for bundle in list(seen):
        if bundle not in live:
            del seen[bundle]
    data["seen"] = seen
    data["updated_at"] = now
    data["version"] = 1
    save(data)
    return len(apps)


def running_pid() -> int:
    try:
        pid = int(PIDFILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return 0


def loop() -> int:
    SUPPORT.mkdir(parents=True, exist_ok=True)
    other = running_pid()
    if other and other != os.getpid():
        return 0
    PIDFILE.write_text(str(os.getpid()))
    log = SUPPORT / "usage.log"
    while True:
        try:
            count = sample()
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} sampled {count}\n")
        except Exception as exc:  # noqa: BLE001
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} error {exc}\n")
        time.sleep(INTERVAL)


def plist_payload() -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/python3", str(SCRIPT), "loop"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(SUPPORT / "usage.log"),
        "StandardErrorPath": str(SUPPORT / "usage.err"),
    }


def install() -> int:
    SUPPORT.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve(), SCRIPT)
    with open(PLIST, "wb") as f:
        plistlib.dump(plist_payload(), f)

    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
                   capture_output=True, text=True)
    result = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        subprocess.run(["launchctl", "load", "-w", str(PLIST)], capture_output=True, text=True)

    loaded = is_loaded()
    if not loaded and not running_pid():
        log = open(SUPPORT / "usage.log", "a", encoding="utf-8")
        subprocess.Popen(
            ["/usr/bin/python3", str(SCRIPT), "loop"],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
        time.sleep(1.2)

    sample()
    running = is_loaded() or bool(running_pid())
    print(json.dumps({
        "ok": running,
        "action": "install",
        "running": running,
        "launch_agent": PLIST.exists(),
        "plist": str(PLIST),
        "script": str(SCRIPT),
        "data": str(DATA),
        "message": "已启动" if running else "权限不足，无法启动后台记录",
    }, ensure_ascii=False))
    return 0


def uninstall() -> int:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True, text=True)
    pid = running_pid()
    if pid:
        try:
            os.kill(pid, 15)
        except Exception:
            pass
    if PIDFILE.exists():
        PIDFILE.unlink()
    if PLIST.exists():
        PLIST.unlink()
    print(json.dumps({"ok": True, "action": "uninstall", "message": "已停止，历史记录保留在 usage.json"},
                     ensure_ascii=False))
    return 0


def is_loaded() -> bool:
    uid = os.getuid()
    out = subprocess.run(["launchctl", "print", f"gui/{uid}/{LABEL}"],
                         capture_output=True, text=True)
    return out.returncode == 0


def status() -> int:
    data = load()
    apps = data.get("apps", {})
    print(json.dumps({
        "ok": True,
        "installed": PLIST.exists(),
        "running": is_loaded() or bool(running_pid()),
        "records": len(apps),
        "updated_at": data.get("updated_at"),
        "data_path": str(DATA),
        "interval": INTERVAL,
    }, ensure_ascii=False))
    return 0


def report() -> int:
    data = load()
    rows = sorted(data.get("apps", {}).items(), key=lambda kv: kv[1].get("last_seen", 0), reverse=True)
    for bundle, record in rows[:40]:
        stamp = record.get("last_seen", 0)
        when = time.strftime("%m-%d %H:%M", time.localtime(stamp)) if stamp else "—"
        print(f"{when}  {record.get('launches', 0):>3} 次  {record.get('name', ''):<28} {bundle}")
    print(f"\n共 {len(rows)} 个应用，数据：{DATA}")
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "once":
        print(json.dumps({"ok": True, "sampled": sample()}, ensure_ascii=False))
        return 0
    if command == "loop":
        loop()
        return 0
    if command == "install":
        return install()
    if command == "uninstall":
        return uninstall()
    if command == "status":
        return status()
    if command == "report":
        return report()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
