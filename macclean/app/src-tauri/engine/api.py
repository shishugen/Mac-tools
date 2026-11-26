#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macclean Tauri 桥接层。

把 macclean.py 的能力包成 JSON 输出，供 Tauri 后端调用：

    python3 api.py scan --budget 60
    python3 api.py plan-clean --levels S
    python3 api.py run-clean --path "..." --path "..."
    python3 api.py apps
    python3 api.py plan-uninstall 微信
    python3 api.py run-uninstall 微信 --with-data
    python3 api.py doctor

所有写操作都复用 macclean 的 assert_safe + 废纸篓逻辑，不另开路径。
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import subprocess
import tempfile
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if (HERE / "macclean.py").exists():
    BASE = HERE
else:
    BASE = HERE.parents[1]
sys.path.insert(0, str(BASE))
import macclean as mc  # noqa: E402


def emit(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def home_of(value: str) -> Path:
    home = Path(value).expanduser().resolve() if value else Path.home()
    mc.HOME_FOR_PRINT = home
    return home


def load_rules():
    return mc.load_rules(BASE / "rules.json")


def scan_items(home: Path, budget: float, include_trash: bool = False):
    rules = load_rules()
    flags = {"include-trash"} if include_trash else set()
    items, meter, skipped = mc.gather_items(rules, home, flags, budget)
    reds = mc.build_redline_report(
        rules, home, include_system=(mc.norm(home) == mc.norm(Path.home()))
    )
    return rules, items, meter, reds, skipped


def summary_of(items) -> dict:
    out = {}
    for item in items:
        row = out.setdefault(item.level, {"count": 0, "bytes": 0})
        row["count"] += 1
        row["bytes"] += item.size
    return out


def cmd_scan(args) -> int:
    home = home_of(args.home)
    t0 = time.time()
    rules, items, meter, reds, skipped = scan_items(home, args.budget, args.include_trash)
    emit({
        "ok": True,
        "version": mc.VERSION,
        "home": str(home),
        "elapsed": round(time.time() - t0, 1),
        "visited": meter.entries,
        "truncated": meter.truncated,
        "items": [i.to_json() for i in items],
        "summary": summary_of(items),
        "redlines": [asdict(r) for r in reds],
        "skipped_flags": sorted(skipped),
    })
    return 0


def cmd_plan_clean(args) -> int:
    home = home_of(args.home)
    rules, items, meter, _, _ = scan_items(home, args.budget, args.include_trash)
    roots = mc.redline_roots(rules, home)
    levels = set(args.levels or ["S"])
    picked = []
    for item in items:
        if item.level not in levels:
            continue
        why = mc.assert_safe(item.path, home, allow_admin=item.needs_admin, roots=roots)
        picked.append({**item.to_json(), "safe": not why, "why": why,
                       "requires_admin": item.needs_admin})
    emit({
        "ok": True,
        "home": str(home),
        "items": picked,
        "count": len(picked),
        "bytes": sum(i["size"] for i in picked if i["safe"]),
        "blocked": [i for i in picked if not i["safe"]],
    })
    return 0


def cmd_run_clean(args) -> int:
    clear_app_cache()
    home = home_of(args.home)
    rules, items, _, _, _ = scan_items(home, args.budget, False)
    roots = mc.redline_roots(rules, home)
    allowed = {mc.norm(i.path): i for i in items}
    chosen = []
    rejected = []
    for raw in args.path:
        key = mc.norm(raw)
        item = allowed.get(key)
        if not item:
            rejected.append({"path": raw, "why": "不在本次扫描的白名单结果里"})
            continue
        why = mc.assert_safe(item.path, home, allow_admin=False, roots=roots)
        if why:
            rejected.append({"path": raw, "why": why})
            continue
        chosen.append(item)

    chosen = mc.prune_nested(chosen)
    results = mc.trash_paths([i.path for i in chosen], dry_run=args.dry_run)
    emit({
        "ok": True,
        "dry_run": bool(args.dry_run),
        "freed": sum(i.size for i in chosen if any(r[0] == i.path and r[1] for r in results)),
        "results": [{"path": p, "ok": g, "message": m} for p, g, m in results],
        "rejected": rejected,
    })
    return 0


def _du_kb(path: str) -> int:
    """du -sk 比 Python 递归快一个数量级；符号链接先解析，否则拿到 0。"""
    real = os.path.realpath(path)
    try:
        out = subprocess.run(["du", "-sk", real], capture_output=True, text=True, timeout=120)
        if out.returncode == 0 and out.stdout.strip():
            value = int(out.stdout.split()[0]) * 1024
            if value > 0:
                return value
    except Exception:
        pass
    return mc.dir_size(real)


def _last_used(path: str) -> tuple:
    """返回 (epoch 秒, 使用次数)，没有记录返回 (None, 0)。"""
    try:
        out = subprocess.run(
            ["mdls", "-raw", "-name", "kMDItemLastUsedDate", "-name", "kMDItemUseCount", path],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode != 0:
            return None, 0
        raw = out.stdout.replace("\x00", "\n")
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        stamp, count = None, 0
        for line in lines:
            if line.isdigit():
                count = int(line)
            elif line.startswith("20"):
                try:
                    stamp = int(datetime.strptime(line[:25], "%Y-%m-%d %H:%M:%S %z").timestamp())
                except Exception:
                    pass
        return stamp, count
    except Exception:
        return None, 0


def _birth(path: str) -> float:
    """应用包的创建时间（安装或最近一次重装），APFS 上可靠。"""
    try:
        out = subprocess.run(["stat", "-f", "%B", path], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip().isdigit():
            return float(out.stdout.strip())
    except Exception:
        pass
    return 0.0


def _access(path: str) -> float:
    """应用包的修改时间。macOS「存储管理 → 上次访问时间」用的就是这个。"""
    try:
        return float(os.stat(path).st_mtime)
    except Exception:
        return 0.0


def _app_info(path: str) -> tuple:
    stamp, count = _last_used(path)
    return _du_kb(path), stamp, count, _birth(path), _access(path)


def enrich_apps(apps) -> None:
    if not apps:
        return
    paths = [a.path for a in apps]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        info = list(pool.map(_app_info, paths))
    for app, (size, stamp, count, birth, access) in zip(apps, info):
        app.size = size
        app.last_used = stamp
        app.use_count = count
        app.added = birth
        app.accessed = access


GROUP_POLICY = {
    "app": {"label": "主程序", "policy": "delete",
            "why": "卸载时删除，需要时可从 App Store 或官网重装"},
    "cache": {"label": "缓存", "policy": "safe",
              "why": "应用会自动重建，不含账号和文档，可放心删"},
    "data": {"label": "应用数据", "policy": "careful",
             "why": "含登录态、本地文档与设置，删除后无法恢复"},
    "probable": {"label": "疑似共享目录", "policy": "manual",
                 "why": "名称或开发者匹配，证据不足，需人工确认"},
    "admin": {"label": "系统级组件", "policy": "admin",
              "why": "守护进程、驱动与系统配置，只生成脚本，不自动删"},
}


def cmd_apps(args) -> int:
    if (args.sizes or args.meta) and not getattr(args, "fresh", False):
        hit = load_full_cache()
        if hit:
            emit(hit)
            return 0
    home = home_of(args.home)
    apps = [a for a in mc.find_apps(home, with_sizes=False) if os.path.exists(a.path)]
    if args.sizes or args.meta:
        enrich_apps(apps)
    parts_by_app = {}
    if args.sizes or args.meta:
        pairs = []
        for app in apps:
            cands = mc.uninstall_candidates(app, home, with_sizes=False)
            pairs.append((app, cands))
        all_cands = [c for _, cands in pairs for c in cands]
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            sizes = list(pool.map(_du_kb, [c.path for c in all_cands]))
        for cand, size in zip(all_cands, sizes):
            cand.size = size
        for app, cands in pairs:
            kept = mc.prune_nested(cands)
            parts_by_app[app.bundle_id or app.path] = [{
                "path": c.path,
                "group": c.group,
                "note": c.note,
                "size": c.size,
                "size_human": human_size(c.size),
                "label": GROUP_POLICY.get(c.group, {}).get("label", c.group),
                "policy": GROUP_POLICY.get(c.group, {}).get("policy", "manual"),
                "why": GROUP_POLICY.get(c.group, {}).get("why", "需人工判断"),
                "purpose": path_purpose(c.path),
            } for c in kept]

    icons = []
    if args.sizes or args.meta:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            icons = list(pool.map(_icon_data, apps))

    tracked = tracker_data()
    rows = []
    stamps = []
    for index, app in enumerate(apps):
        row = app.to_json()
        row["name"] = app.display_name or app.name
        row["bundle_name"] = app.name
        row["icon"] = icons[index] if icons else ""
        stamps.append(_app_stamp(app))
        file_date = getattr(app, "accessed", None) or None
        spotlight = getattr(app, "last_used", None)
        local = (tracked.get(app.bundle_id) or {}).get("last_seen")
        candidates = []
        for value, source in ((file_date, "file"), (spotlight, "spotlight"), (local, "tracker")):
            if value:
                candidates.append((value, source))
        if candidates:
            best = max(candidates, key=lambda pair: pair[0])
            row["last_used"], row["usage_source"] = best
        else:
            row["last_used"], row["usage_source"] = None, None
        row["accessed"] = file_date
        row["use_count"] = getattr(app, "use_count", 0)
        row["added"] = getattr(app, "added", None)
        rows.append(row)
    covered = len([r for r in rows if r.get("last_used")])
    local_covered = len([r for r in rows if r.get("usage_source") == "tracker"])
    for row in rows:
        parts = parts_by_app.get(row.get("bundle_id") or "", [])
        row["parts"] = parts
        row["total_size"] = row.get("size", 0) + sum(
            p["size"] for p in parts if p["group"] in ("cache", "data", "probable")
        )
        row["cache_size"] = sum(p["size"] for p in parts if p["group"] == "cache")
        row["data_size"] = sum(p["size"] for p in parts if p["group"] in ("data", "probable"))
        row["admin_size"] = sum(p["size"] for p in parts if p["group"] == "admin")
        row["total_size_human"] = human_size(row["total_size"])

    for row in rows:
        row.pop("_stamp", None)
    result = {"ok": True, "apps": rows, "total": len(apps),
              "enriched": bool(args.sizes or args.meta), "usage_covered": covered,
              "tracker_covered": local_covered, "tracker_records": len(tracked)}
    if args.sizes or args.meta:
        for row, stamp in zip(rows, stamps):
            row["_stamp"] = stamp
        save_app_cache(result)
        for row in rows:
            row.pop("_stamp", None)
    emit(result)
    return 0


def plan_of(app, home: Path, allow_cache: bool = True) -> dict:
    cached = (load_app_cache().get(app.bundle_id) or {}) if allow_cache else {}
    if cached and cached.get("stamp") == _app_stamp(app) and cached.get("parts"):
        app.size = cached.get("size", 0)
        groups = {"cache": [], "data": [], "probable": [], "admin": []}
        for row in cached["parts"]:
            groups.setdefault(row.get("group", "data"), []).append(row)
    else:
        app.size = mc.dir_size(app.path)
        candidates = mc.uninstall_candidates(app, home)
        groups = {"cache": [], "data": [], "probable": [], "admin": []}
        for c in candidates:
            info = GROUP_POLICY.get(c.group, {})
            row = c.to_json()
            row.update({"label": info.get("label", c.group),
                        "policy": info.get("policy", "manual"),
                        "why": info.get("why", "需人工判断"),
                        "purpose": path_purpose(c.path),
                        "note": c.note})
            groups.setdefault(c.group, []).append(row)
    app_json = app.to_json()
    app_json["name"] = app.display_name or app.name
    app_json["bundle_name"] = app.name
    return {
        "app": app_json,
        "groups": groups,
        "running": mc.app_running(app),
        "receipts": mc.pkg_receipts(app),
        "protected": app.bundle_id.startswith("com.apple."),
    }


def cmd_plan_uninstall(args) -> int:
    home = home_of(args.home)
    apps = mc.find_apps(home)
    app = mc.resolve_app(apps, args.app)
    if not app:
        emit({"ok": False, "error": f"没找到唯一匹配的应用：{args.app}"})
        return 0
    plan = plan_of(app, home)
    emit({"ok": True, **plan})
    return 0


def cmd_run_uninstall(args) -> int:
    clear_app_cache()
    home = home_of(args.home)
    apps = mc.find_apps(home)
    app = mc.resolve_app(apps, args.app)
    if not app:
        emit({"ok": False, "error": f"没找到唯一匹配的应用：{args.app}"})
        return 0
    if app.bundle_id.startswith("com.apple."):
        emit({"ok": False, "error": "macOS 自带应用不在卸载范围"})
        return 0
    if mc.app_running(app) and not args.force:
        emit({"ok": False, "error": f"{app.name} 正在运行，先退出再卸载"})
        return 0

    plan = plan_of(app, home)
    targets = [app.path] + [c["path"] for c in plan["groups"]["cache"]]
    if args.with_data:
        targets += [c["path"] for c in plan["groups"]["data"]]
    if args.include_probable:
        targets += [c["path"] for c in plan["groups"]["probable"]]

    results = mc.trash_paths(targets, dry_run=args.dry_run)
    emit({
        "ok": True,
        "dry_run": bool(args.dry_run),
        "app": app.to_json(),
        "results": [{"path": p, "ok": g, "message": m} for p, g, m in results],
        "admin": plan["groups"]["admin"],
    })
    return 0


PROBES = [
    ("~/Library/Mail", "邮件"),
    ("~/Library/Messages", "信息"),
    ("~/Library/Safari", "Safari"),
]


def path_purpose(path: str) -> str:
    """用路径判断这堆文件到底是干嘛的，给用户一句人话。"""
    p = path.replace("\\", "/")
    if "LaunchAgents" in p or "LaunchDaemons" in p:
        return "开机自启项，删了相关功能不再自动运行"
    if "PrivilegedHelperTools" in p:
        return "特权助手，后台服务靠它干活"
    if p.startswith("/Library/") or p.startswith("/System/Library/"):
        return "系统级服务和配置，影响所有用户"
    if "Group Containers" in p:
        return "多个应用共享的数据，归属需人工确认"
    if "/Containers/" in p:
        return "沙盒容器：账号、聊天记录、本地文件都在这里"
    if "Application Support" in p:
        return "应用配置和本地数据，删了要重新登录和设置"
    if "HTTPStorages" in p or "WebKit" in p or "Cookies" in p:
        return "网站数据与登录态，删了要重新登录"
    if "Preferences" in p:
        return "偏好设置，删了恢复默认"
    if "Saved Application State" in p:
        return "窗口位置和恢复状态"
    if "/Caches/" in p or p.rstrip("/").endswith("/Cache"):
        return "临时缓存，应用会自动重建"
    if "/Logs/" in p:
        return "排障日志，平时用不到"
    if "Application Scripts" in p:
        return "给自动化工具用的脚本"
    return "应用关联文件，删前先确认用途"


ICON_DIR = Path.home() / "Library" / "Application Support" / "macclean" / "icons"


def _find_icon_file(app) -> Path:
    root = Path(app.path)
    roots = [root]
    if not (root / "Contents" / "Info.plist").exists():
        roots.extend(list(root.glob("*.app")))
    preferred = ["AppIcon.icns", "app.icns", "icon.icns"]
    for base in roots:
        resources = base / "Contents" / "Resources"
        if not resources.is_dir():
            continue
        for name in preferred:
            candidate = resources / name
            if candidate.exists():
                return candidate
        try:
            all_icns = list(resources.glob("*.icns"))
        except OSError:
            continue
        apps_icons = [f for f in all_icns if "app" in f.stem.lower() and "file" not in f.stem.lower()]
        if apps_icons:
            return sorted(apps_icons, key=lambda f: f.stat().st_size, reverse=True)[0]
    return None


def _icon_data(app) -> str:
    """生成 64px PNG 的 data URI，失败返回空串。"""
    key = (app.bundle_id or Path(app.path).name).replace("/", "_")
    cache = ICON_DIR / f"{key}.png"
    try:
        if not cache.exists():
            source = _find_icon_file(app)
            ICON_DIR.mkdir(parents=True, exist_ok=True)
            if source:
                out = subprocess.run(["sips", "-s", "format", "png", "-Z", "64",
                                      str(source), "--out", str(cache)],
                                     capture_output=True, text=True, timeout=30)
                if out.returncode != 0 or not cache.exists():
                    return ""
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    subprocess.run(["qlmanage", "-t", "-s", "64", "-o", tmp, app.path],
                                   capture_output=True, text=True, timeout=30)
                    pngs = list(Path(tmp).glob("*.png"))
                    if not pngs:
                        return ""
                    os.replace(pngs[0], cache)
        raw = cache.read_bytes()
        if len(raw) > 200_000:
            return ""
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    except Exception:
        return ""


# ---- 应用数据缓存：点开详情不用重新遍历几十 GB 的关联目录 ----
APPS_CACHE = Path.home() / "Library" / "Application Support" / "macclean" / "apps-cache.json"
CACHE_TTL = 1800  # 30 分钟


def _app_stamp(app) -> float:
    try:
        return os.stat(app.path).st_mtime
    except OSError:
        return 0.0


def load_app_cache() -> dict:
    try:
        data = json.loads(APPS_CACHE.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) > CACHE_TTL:
            return {}
        return data.get("apps", {})
    except Exception:
        return {}


def save_app_cache(result: dict) -> None:
    """存整份应用数据（体积、用途、图标），下次进应用页直接读。"""
    try:
        APPS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": time.time(),
            "payload": result,
            "apps": {
                r["bundle_id"]: {
                    "parts": r.get("parts", []),
                    "size": r.get("size", 0),
                    "total_size": r.get("total_size", 0),
                    "total_size_human": r.get("total_size_human", ""),
                    "stamp": r.get("_stamp", 0),
                }
                for r in result.get("apps", []) if r.get("bundle_id")
            },
        }
        APPS_CACHE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def load_full_cache() -> dict:
    try:
        data = json.loads(APPS_CACHE.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) > CACHE_TTL:
            return {}
        result = data.get("payload") or {}
        result["cache_hit"] = True
        return result
    except Exception:
        return {}


def clear_app_cache() -> None:
    try:
        APPS_CACHE.unlink(missing_ok=True)
    except Exception:
        pass


def human_size(n) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    v, i = float(n or 0), 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return (str(int(v)) if i == 0 else f"{v:.1f}") + " " + units[i]


def tracker_data() -> dict:
    path = Path.home() / "Library" / "Application Support" / "macclean" / "usage.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("apps", {})
    except Exception:
        return {}


def cmd_usage(args) -> int:
    script = BASE / "usage_tracker.py"
    if not script.exists():
        emit({"ok": False, "error": "usage_tracker.py 不存在"})
        return 0
    out = subprocess.run([sys.executable, str(script), args.action],
                         capture_output=True, text=True)
    text = out.stdout.strip()
    if text:
        try:
            emit(json.loads(text))
            return 0
        except Exception:
            pass
    emit({"ok": False, "error": (out.stderr or text or "没有输出").strip()[:400]})
    return 0


def cmd_container(args) -> int:
    try:
        import container as ctr
    except ImportError:
        emit({"ok": False, "error": "引擎缺少 container.py，请重新编译桌面版"})
        return 0
    path = Path(args.path).expanduser()
    if not path.exists():
        emit({"ok": False, "error": "路径不存在"})
        return 0
    emit(ctr.inspect(path))
    return 0


def cmd_container_delete(args) -> int:
    try:
        import container as ctr
    except ImportError:
        emit({"ok": False, "error": "引擎缺少 container.py，请重新编译桌面版"})
        return 0
    root = Path(args.root).expanduser()
    emit(ctr.delete_subpaths(root, args.path, dry_run=args.dry_run))
    return 0


def cmd_delete_paths(args) -> int:
    clear_app_cache()
    """只删勾选的关联项，保留主程序。"""
    home = home_of(args.home)
    apps = mc.find_apps(home)
    app = mc.resolve_app(apps, args.app)
    if not app:
        emit({"ok": False, "error": f"没找到唯一匹配的应用：{args.app}"})
        return 0

    plan = plan_of(app, home)
    allowed = {}
    for group in ("cache", "data", "probable"):
        for row in plan["groups"][group]:
            allowed[row["path"]] = row

    roots = mc.redline_roots(load_rules(), home)
    chosen, rejected = [], []
    for raw in args.path:
        key = mc.norm(raw)
        if key not in allowed:
            rejected.append({"path": raw, "why": "不在该应用的关联文件清单里"})
            continue
        why = mc.assert_safe(key, home, allow_admin=False, roots=roots)
        if why:
            rejected.append({"path": raw, "why": why})
            continue
        chosen.append(key)

    chosen = mc.prune_nested_paths(chosen) if hasattr(mc, "prune_nested_paths") else chosen
    results = mc.trash_paths(chosen, dry_run=args.dry_run)
    emit({
        "ok": True,
        "dry_run": bool(args.dry_run),
        "app": app.display_name or app.name,
        "freed": sum(allowed[p]["size"] for p in chosen
                     if any(r[0] == p and r[1] for r in results)),
        "results": [{"path": p, "ok": good, "message": msg} for p, good, msg in results],
        "rejected": rejected,
    })
    return 0


def cmd_doctor(args) -> int:
    home = home_of(args.home)
    probes = []
    for raw, label in PROBES:
        path = Path(mc.expand(raw, home))
        if not path.exists():
            probes.append({"label": label, "path": str(path), "status": "missing"})
            continue
        try:
            next(os.scandir(path), None)
            probes.append({"label": label, "path": str(path), "status": "ok"})
        except PermissionError:
            probes.append({"label": label, "path": str(path), "status": "denied"})
        except OSError as exc:
            probes.append({"label": label, "path": str(path), "status": str(exc)})

    rules = load_rules()
    emit({
        "ok": True,
        "version": mc.VERSION,
        "home": str(home),
        "python": sys.version.split()[0],
        "probes": probes,
        "rules": {
            "total": len(rules["rules"]),
            "redlines": len(rules.get("redlines", [])),
            "by_level": {
                lv: len([r for r in rules["rules"] if r["level"] == lv])
                for lv in ("S", "A", "B")
            },
        },
    })
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="api.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--home", default="")
        sp.add_argument("--budget", type=float, default=90.0)

    s = sub.add_parser("scan")
    common(s)
    s.add_argument("--include-trash", action="store_true")
    s.set_defaults(func=cmd_scan)

    c = sub.add_parser("plan-clean")
    common(c)
    c.add_argument("--levels", nargs="*", default=["S"])
    c.add_argument("--include-trash", action="store_true")
    c.set_defaults(func=cmd_plan_clean)

    r = sub.add_parser("run-clean")
    common(r)
    r.add_argument("--path", action="append", default=[])
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_run_clean)

    a = sub.add_parser("apps")
    common(a)
    a.add_argument("--sizes", action="store_true")
    a.add_argument("--meta", action="store_true")
    a.add_argument("--fresh", action="store_true", help="忽略缓存，重新计算体积")
    a.set_defaults(func=cmd_apps)

    u = sub.add_parser("plan-uninstall")
    common(u)
    u.add_argument("app")
    u.set_defaults(func=cmd_plan_uninstall)

    x = sub.add_parser("run-uninstall")
    common(x)
    x.add_argument("app")
    x.add_argument("--with-data", action="store_true")
    x.add_argument("--include-probable", action="store_true")
    x.add_argument("--force", action="store_true")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(func=cmd_run_uninstall)

    us = sub.add_parser("usage")
    us.add_argument("action", choices=["status", "install", "uninstall", "report"])
    us.set_defaults(func=cmd_usage)

    ct = sub.add_parser("container")
    ct.add_argument("--path", required=True)
    ct.set_defaults(func=cmd_container)

    cd = sub.add_parser("container-delete")
    cd.add_argument("--root", required=True)
    cd.add_argument("--path", action="append", default=[])
    cd.add_argument("--dry-run", action="store_true")
    cd.set_defaults(func=cmd_container_delete)

    dp = sub.add_parser("delete-paths")
    common(dp)
    dp.add_argument("app")
    dp.add_argument("--path", action="append", default=[])
    dp.add_argument("--dry-run", action="store_true")
    dp.set_defaults(func=cmd_delete_paths)

    d = sub.add_parser("doctor")
    common(d)
    d.set_defaults(func=cmd_doctor)
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        emit({"ok": False, "error": str(exc)})
        return 0


if __name__ == "__main__":
    sys.exit(main())
