#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macclean - macOS 清理与卸载原型（只读扫描 + 白名单 + 废纸篓）

设计约束：
  1. 扫描阶段不写任何文件。
  2. 白名单之外一律不清理；红线路径永远拒绝。
  3. 删除只有一条路径：移入废纸篓。绝不调用 rm -rf。
  4. 系统级残留不自动执行，只生成可审阅的脚本。

用法见 README.md，或 python3 macclean.py --help
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
RULES_PATH = APP_DIR / "rules.json"
LEVEL_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3}
LEVEL_LABEL = {
    "S": "S 可重建",
    "A": "A 含数据",
    "B": "B 需管理员",
    "C": "C 红线",
}
VERSION = "0.1.0"


# ---------------------------------------------------------------- 输出工具

def dwidth(text: str) -> int:
    """按终端显示宽度计算（中文算 2 列）。"""
    w = 0
    for ch in str(text):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad(text: str, width: int) -> str:
    text = str(text)
    gap = width - dwidth(text)
    return text + " " * max(gap, 0)


def cut(text: str, width: int) -> str:
    """按显示宽度截断，超出加省略号。"""
    text = str(text)
    if dwidth(text) <= width:
        return text
    out, w = "", 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width - 1:
            break
        out += ch
        w += cw
    return out + "…"


def human(n) -> str:
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def short_path(path: str, home: Path) -> str:
    try:
        if str(path).startswith(str(home)):
            return "~" + str(path)[len(str(home)):]
    except Exception:
        pass
    return str(path)


def hr(title: str = "") -> None:
    if title:
        print(f"\n{title}")
        print("─" * 86)
    else:
        print("─" * 86)


# ---------------------------------------------------------------- 规则

def load_rules(path: Path = RULES_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "rules" not in data:
        raise SystemExit(f"规则文件格式不对：{path}")
    return data


def expand(pattern: str, home: Path) -> str:
    if pattern == "~":
        return str(home)
    if pattern.startswith("~/"):
        return str(home) + pattern[1:]
    return pattern


def norm(path) -> str:
    return os.path.abspath(os.path.expanduser(str(path)))


def is_under(path: str, root: str) -> bool:
    path, root = norm(path), norm(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


# ---------------------------------------------------------------- 数据结构

@dataclass
class Item:
    path: str
    name: str
    rule_id: str
    level: str
    size: int
    mtime: float
    reason: str
    rebuildable: bool
    needs_admin: bool = False
    action: str = "trash"
    selected: bool = True

    def to_json(self) -> dict:
        d = asdict(self)
        d["size_human"] = human(self.size)
        d["mtime_iso"] = datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M")
        d["path"] = short_path(self.path, HOME_FOR_PRINT)
        return d


HOME_FOR_PRINT = Path.home()


@dataclass
class RedlineInfo:
    path: str
    label: str
    reason: str


# ---------------------------------------------------------------- 扫描

class SizeMeter:
    def __init__(self, deadline: float, max_entries: int = 400000):
        self.deadline = deadline
        self.max_entries = max_entries
        self.entries = 0
        self.truncated = False
        self._cache: dict[str, int] = {}

    def du(self, path: str) -> int:
        if path in self._cache:
            return self._cache[path]
        total = self._du(path)
        self._cache[path] = total
        return total

    def _du(self, path: str) -> int:
        try:
            st = os.lstat(path)
        except OSError:
            return 0
        if stat.S_ISLNK(st.st_mode):
            return 0
        if stat.S_ISREG(st.st_mode):
            return st.st_size
        if not stat.S_ISDIR(st.st_mode):
            return 0
        total = 0
        stack = [path]
        while stack:
            if time.time() > self.deadline or self.entries > self.max_entries:
                self.truncated = True
                break
            current = stack.pop()
            try:
                it = os.scandir(current)
            except OSError:
                continue
            with it:
                for entry in it:
                    self.entries += 1
                    if self.entries > self.max_entries:
                        self.truncated = True
                        break
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            try:
                                total += entry.stat(follow_symlinks=False).st_size
                            except OSError:
                                pass
                    except OSError:
                        continue
        return total


def redline_roots(rules: dict, home: Path) -> list:
    return [expand(p, home) for p in rules.get("redlines", [])]


def find_redline(path: str, roots: list) -> str:
    for root in roots:
        if is_under(path, root):
            return root
    return ""


def gather_items(rules: dict, home: Path, flags: set, time_budget: float = 90.0,
                 include_system: bool = True) -> tuple:
    meter = SizeMeter(time.time() + time_budget)
    roots = redline_roots(rules, home)
    items: dict[str, Item] = {}
    skipped_flags = set()
    scoped_system = include_system and norm(home) == norm(Path.home())

    for rule in rules["rules"]:
        need = rule.get("requires_flag")
        if need and need not in flags:
            skipped_flags.add(need)
            continue

        for pattern in rule.get("patterns", []):
            if not pattern.startswith("~") and not scoped_system:
                continue
            try:
                matches = glob.glob(expand(pattern, home))
            except Exception:
                continue
            for match in matches:
                key = norm(match)
                if not os.path.lexists(key):
                    continue
                if os.path.islink(key):
                    continue
                if find_redline(key, roots) and not _rule_overrides_redline(rule, key, roots):
                    continue
                if any(fnmatch.fnmatch(key, expand(ex, home)) for ex in rule.get("exclude", [])):
                    continue
                try:
                    st = os.lstat(key)
                except OSError:
                    continue
                age_days = (time.time() - st.st_mtime) / 86400
                if rule.get("min_age_days") and age_days < rule["min_age_days"]:
                    continue

                old = items.get(key)
                if old and LEVEL_ORDER[old.level] >= LEVEL_ORDER[rule["level"]]:
                    continue
                items[key] = Item(
                    path=key,
                    name=rule["name"],
                    rule_id=rule["id"],
                    level=rule["level"],
                    size=meter.du(key),
                    mtime=st.st_mtime,
                    reason=rule.get("reason", ""),
                    rebuildable=bool(rule.get("rebuildable")),
                    needs_admin=bool(rule.get("needs_admin")),
                )

    ordered = sorted(items.values(), key=lambda i: (-i.size, i.level, i.path))
    return ordered, meter, skipped_flags


def _rule_overrides_redline(rule: dict, path: str, roots: list) -> bool:
    """白名单规则可以处理红线目录的子路径，但永远不能处理红线根本身。"""
    for root in roots:
        if norm(path) == norm(root):
            return False
    return rule.get("level") in ("S", "A", "B")


def build_redline_report(rules: dict, home: Path, include_system: bool = True) -> list:
    out = []
    scoped = include_system and norm(home) == norm(Path.home())
    labels = {
        "Keychains": "钥匙串与密码",
        "Preferences": "应用配置",
        "Cookies": "登录 Cookie",
        "Application Support": "应用数据主体",
        "Mail": "邮件数据",
        "Messages": "信息记录",
        "Calendars": "日历",
        "Contacts": "通讯录",
        "Photos": "照片图库",
        "Accounts": "系统账号",
        "Documents": "个人文档",
        "Desktop": "桌面文件",
        "Downloads": "下载文件",
        "ssh": "SSH 私钥",
        "config": "工具配置",
        "aws": "云凭证",
        "gnupg": "GPG 密钥",
        "workbuddy": "工作区状态",
    }
    for raw in rules.get("redlines", []):
        if not raw.startswith("~") and not scoped:
            continue
        p = expand(raw, home)
        if not os.path.lexists(p):
            continue
        key = Path(p).name
        label = labels.get(key) or labels.get(key.lstrip("."), "系统或敏感目录")
        out.append(RedlineInfo(path=p, label=label,
                               reason="只展示，禁止清理"))
    return out


# ---------------------------------------------------------------- 打印

def print_items(items: list, home: Path, top: int = 25, title: str = "扫描结果"):
    hr(title)
    if not items:
        print("没有匹配到可清理项。")
        return

    total = sum(i.size for i in items)
    print(f"共 {len(items)} 项，合计约 {human(total)}")
    print()
    print(f"{pad('级别', 6)}{pad('大小', 10)}{pad('项目', 24)}{pad('路径', 44)}")
    print("─" * 86)
    shown = items if top <= 0 else items[:top]
    for item in shown:
        print(f"{pad(item.level, 6)}{pad(human(item.size), 10)}"
              f"{pad(cut(item.name, 22), 24)}{cut(short_path(item.path, home), 44)}")
    if top > 0 and len(items) > top:
        print(f"… 还有 {len(items) - top} 项，用 --top 0 查看全部")


def level_summary(items: list) -> dict:
    summary = {}
    for item in items:
        s = summary.setdefault(item.level, {"count": 0, "size": 0})
        s["count"] += 1
        s["size"] += item.size
    return summary


# ---------------------------------------------------------------- 废纸篓

TRASH_SCRIPT = """
on run argv
  set out to {}
  repeat with p in argv
    try
      tell application "Finder" to delete (POSIX file p)
      set end of out to "OK"
    on error errMsg
      set end of out to "ERR|" & errMsg
    end try
  end repeat
  return out
end run
"""


def assert_safe(path: str, home: Path, allow_admin: bool, roots: list) -> str:
    """执行前的最后一道闸。返回空串表示可以，否则返回拒绝原因。"""
    p = norm(path)
    if p in ("/", "/Users", "/Applications", "/Library", "/System"):
        return "指向系统根目录"
    if p == norm(home):
        return "指向用户主目录"
    if os.path.islink(p):
        return "是符号链接"
    for root in roots:
        if p == norm(root):
            return f"是红线目录 {short_path(root, home)}"
    under_home = is_under(p, home)
    under_lib = is_under(p, "/Library")
    if not under_home and not (allow_admin and under_lib):
        return "不在允许范围内"
    if under_home:
        rel = os.path.relpath(p, str(home))
        if rel.count(os.sep) < 1:
            return "层级过浅"
    return ""


def trash_paths(paths: list, dry_run: bool = False, trash_dir: str = "") -> list:
    """唯一写入口：先走 Finder（支持「放回原处」），失败则直接移入废纸篓目录。

    返回 [(path, ok, message)]。trash_dir 仅用于测试，指定后不碰真实废纸篓。
    """
    results = []
    if not paths:
        return results
    if dry_run:
        return [(p, True, "dry-run") for p in paths]

    batch = [p for p in paths if os.path.lexists(p)]
    if not batch:
        return results

    if trash_dir:
        return [_move_to_trash(p, Path(trash_dir).expanduser()) for p in batch]

    try:
        proc = subprocess.run(
            ["osascript", "-e", TRASH_SCRIPT, *batch],
            capture_output=True, text=True, timeout=900,
        )
        parts = [x.strip() for x in proc.stdout.strip().split(",")] if proc.stdout.strip() else []
    except Exception:  # noqa: BLE001
        parts = []

    for idx, path in enumerate(batch):
        status = parts[idx] if idx < len(parts) else ""
        if status.startswith("OK"):
            results.append((path, True, "已移入废纸篓"))
        elif status.startswith("ERR|"):
            results.append(_move_to_trash(path, Path.home() / ".Trash", status[4:]))
        else:
            if not os.path.lexists(path):
                results.append((path, True, "已移入废纸篓"))
            else:
                results.append(_move_to_trash(path, Path.home() / ".Trash"))
    return results


def _move_to_trash(path: str, trash: Path, note: str = "") -> tuple:
    """兜底：直接移动到废纸篓目录，名称加时间戳避免覆盖。"""
    try:
        trash.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = Path(path).name or "item"
        dest = trash / f"macclean-{stamp}-{name}"
        n = 1
        while dest.exists():
            dest = trash / f"macclean-{stamp}-{n}-{name}"
            n += 1
        shutil.move(path, str(dest))
        suffix = "（直接移入废纸篓，无「放回原处」）"
        if note:
            suffix += f"，Finder 原因：{note}"
        return (path, True, "已移入废纸篓" + suffix)
    except Exception as exc:  # noqa: BLE001
        return (path, False, f"移入废纸篓失败：{exc}")


def prune_nested(items: list) -> list:
    """父目录已选中时去掉被包含的子项，保持原有顺序（按大小）。"""
    ordered = sorted(items, key=lambda i: -i.size)
    kept = []
    for item in ordered:
        p = norm(item.path)
        if any(k != p and is_under(p, k) for k, _ in kept):
            continue
        kept = [(k, i) for k, i in kept if not (k != p and is_under(k, p))]
        kept.append((p, item))
    return [i for _, i in kept]


# ---------------------------------------------------------------- scan 命令

def cmd_scan(args) -> int:
    rules = load_rules(Path(args.rules))
    home = Path(args.home).expanduser().resolve()
    flags = {"include-trash"} if args.include_trash else set()

    t0 = time.time()
    print(f"macclean {VERSION} · 只读扫描")
    print(f"范围：{home}   时间预算：{args.time_budget:.0f}s")
    items, meter, skipped = gather_items(rules, home, flags, args.time_budget)
    elapsed = time.time() - t0

    visible = [i for i in items if i.level in args.levels_set]
    print_items(visible, home, top=args.top, title="可清理项")

    summary = level_summary(items)
    if summary:
        print()
        for lv in ("S", "A", "B"):
            if lv in summary:
                print(f"  {LEVEL_LABEL[lv]}: {summary[lv]['count']} 项，{human(summary[lv]['size'])}")

    if skipped:
        print(f"\n提示：有规则需要额外开关（{' '.join(sorted(skipped))}），默认未启用。")
    if meter.truncated:
        print("提示：达到时间或条目上限，部分目录大小未算完。")

    reds = build_redline_report(rules, home, include_system=(norm(home) == norm(Path.home())))
    if reds:
        hr("红线目录（只展示，禁止清理）")
        for r in reds:
            print(f"  {pad(r.label, 14)}{cut(short_path(r.path, home), 46)}  {r.reason}")

    hr("汇总")
    print(f"扫描耗时 {elapsed:.1f}s，遍历 {meter.entries} 个目录项")
    print(f"可释放约 {human(sum(i.size for i in items if i.level in ('S', 'A')))}"
          f"（S + A 级，B 级需管理员）")

    if args.json:
        payload = {
            "tool": "macclean",
            "version": VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "home": str(home),
            "elapsed_seconds": round(elapsed, 1),
            "items": [i.to_json() for i in items],
            "summary": {k: {"count": v["count"], "bytes": v["size"]} for k, v in summary.items()},
            "redlines": [asdict(r) for r in reds],
        }
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入 {out}")
    return 0


# ---------------------------------------------------------------- clean 命令

def cmd_clean(args) -> int:
    rules = load_rules(Path(args.rules))
    home = Path(args.home).expanduser().resolve()
    roots = redline_roots(rules, home)
    flags = {"include-trash"} if args.include_trash else set()

    allowed = {"S"}
    if args.include_a:
        allowed.add("A")
    if args.allow_admin:
        allowed.add("B")

    items, _, _ = gather_items(rules, home, flags, args.time_budget)
    chosen = [i for i in items
              if i.level in allowed and i.size >= args.min_size and i.rule_id]
    if args.rule:
        chosen = [i for i in chosen if i.rule_id in args.rule]

    if not chosen:
        print("没有符合条件的可清理项。")
        return 0

    normal = [i for i in chosen if not i.needs_admin]
    admin = [i for i in chosen if i.needs_admin]

    print(f"macclean {VERSION} · 清理计划")
    print(f"范围：{home}   允许级别：{'/'.join(sorted(allowed))}")
    print_items(prune_nested(normal), home, top=args.top, title="将移入废纸篓")

    refused = []
    runnable = []
    for item in prune_nested(normal):
        why = assert_safe(item.path, home, allow_admin=False, roots=roots)
        if why:
            refused.append((item, why))
        else:
            runnable.append(item)

    if refused:
        print("\n以下项目被安全闸拒绝：")
        for item, why in refused:
            print(f"  {short_path(item.path, home)} → {why}")

    total = sum(i.size for i in runnable)
    print(f"\n预计释放 {human(total)}，共 {len(runnable)} 项")

    if not args.yes:
        print("\n未执行。确认无误后加 --yes 才会移入废纸篓。")
        if admin:
            print(f"另有 {len(admin)} 项系统级缓存需管理员权限，加 --allow-admin 生成脚本。")
        return 0

    results = trash_paths([i.path for i in runnable], dry_run=False, trash_dir=args.trash_dir)
    ok = [(p, m) for p, good, m in results if good]
    fail = [(p, m) for p, good, m in results if not good]

    hr("执行结果")
    print(f"成功 {len(ok)} 项，失败 {len(fail)} 项")
    for path, msg in fail:
        print(f"  失败 {short_path(path, home)}：{msg}")
    print("\n撤销方式：打开废纸篓，右键「放回原处」。")

    if admin and args.allow_admin:
        script = write_admin_script(admin, home, args.admin_script)
        print(f"\n系统级项目未自动执行。已生成待审阅脚本：{script}")
        print("请打开检查每一条命令，再自行决定是否运行。")

    if args.json:
        payload = {
            "tool": "macclean",
            "version": VERSION,
            "executed_at": datetime.now().isoformat(timespec="seconds"),
            "planned_bytes": total,
            "items": [i.to_json() for i in runnable],
            "results": [{"path": p, "ok": g, "message": m} for p, g, m in results],
        }
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"清理记录：{args.json}")
    return 0


ADMIN_TEMPLATE = """#!/bin/zsh
# macclean 生成：系统级残留清理脚本
# 逐条检查后再运行。每条命令都是「移入废纸篓」，不是直接删除。
# 生成时间：{ts}
set -u
TRASH="$HOME/.Trash"
mkdir -p "$TRASH"

{commands}
"""


def write_admin_script(items: list, home: Path, out_path: str = "") -> str:
    out = Path(out_path) if out_path else (Path.cwd() / "macclean-admin-cleanup.sh")
    lines = []
    for item in items:
        src = item.path
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = f"$TRASH/macclean-{stamp}-{Path(src).name}"
        lines.append(f'echo "→ {src}"')
        lines.append(
            f'sudo mv -- "{src}" "{dst}" '
            f'&& sudo chown -R "$(id -u):$(id -g)" "{dst}" '
            f'|| echo "  失败：{src}"'
        )
    out.write_text(ADMIN_TEMPLATE.format(
        ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        commands="\n".join(lines) if lines else 'echo "无项目"',
    ), encoding="utf-8")
    os.chmod(out, 0o755)
    return str(out)


# ---------------------------------------------------------------- 应用与卸载

@dataclass
class AppInfo:
    name: str
    path: str
    bundle_id: str
    executable: str
    version: str
    size: int = 0
    display_name: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["size_human"] = human(self.size)
        return d


def read_app(path: Path) -> AppInfo | None:
    plist = path / "Contents" / "Info.plist"
    if not plist.exists():
        for nested in path.glob("*.app"):
            candidate = nested / "Contents" / "Info.plist"
            if candidate.exists():
                plist = candidate
                break
    name = path.stem
    display = name
    bid, exe, ver = "", "", ""
    try:
        with open(plist, "rb") as f:
            info = plistlib.load(f)
        bid = str(info.get("CFBundleIdentifier", "") or "")
        exe = str(info.get("CFBundleExecutable", "") or "")
        ver = str(info.get("CFBundleShortVersionString", "") or "")
        name = str(info.get("CFBundleName") or info.get("CFBundleDisplayName") or name)
        display = str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or name)
    except Exception:
        pass

    resources = path / "Contents" / "Resources"
    for lproj in ("zh-Hans.lproj", "zh_CN.lproj", "en.lproj"):
        strings = resources / lproj / "InfoPlist.strings"
        if not strings.exists():
            continue
        try:
            out = subprocess.run(["plutil", "-convert", "json", "-o", "-", str(strings)],
                                 capture_output=True, text=True, timeout=5)
            loc = json.loads(out.stdout)
            candidate = str(loc.get("CFBundleDisplayName") or loc.get("CFBundleName") or "")
            if candidate:
                display = candidate
            if lproj.startswith("zh"):
                break
        except Exception:
            continue

    return AppInfo(name=name, display_name=display or name, path=str(path),
                   bundle_id=bid, executable=exe, version=ver)


def find_apps(home: Path, with_sizes: bool = False) -> list:
    if norm(home) == norm(Path.home()):
        dirs = [Path("/Applications"), Path("/Applications/Utilities"), home / "Applications"]
    else:
        dirs = [home / "Applications"]
    seen = {}
    for d in dirs:
        if not d.is_dir():
            continue
        try:
            entries = []
            for p in d.iterdir():
                if not p.is_dir():
                    continue
                if p.suffix == ".app" or (p / "Contents" / "Info.plist").exists():
                    entries.append(p)
                    continue
                try:
                    if any((n / "Contents" / "Info.plist").exists() for n in p.glob("*.app")):
                        entries.append(p)
                except OSError:
                    pass
        except OSError:
            continue
        for p in entries:
            info = read_app(p)
            if not info:
                continue
            key = info.bundle_id or info.path
            if key in seen:
                continue
            if with_sizes:
                info.size = dir_size(str(p))
            seen[key] = info
    return sorted(seen.values(), key=lambda a: a.name.lower())


def dir_size(path: str, max_seconds: float = 12.0) -> int:
    meter = SizeMeter(time.time() + max_seconds, max_entries=200000)
    return meter.du(path)


def cmd_apps(args) -> int:
    home = Path(args.home).expanduser().resolve()
    apps = find_apps(home, with_sizes=args.sizes)
    print(f"macclean {VERSION} · 已安装应用（{len(apps)}）")
    hr()
    print(f"{pad('应用', 30)}{pad('大小', 10)}{pad('版本', 14)}Bundle ID / 路径")
    for app in apps:
        if args.filter and args.filter.lower() not in (app.name + " " + app.bundle_id).lower():
            continue
        size_text = human(app.size) if args.sizes else "—"
        print(f"{pad(cut(app.name, 28), 30)}{pad(size_text, 10)}"
              f"{pad(cut(app.version, 12), 14)}{app.bundle_id or '—'}")
        print(f"{'':30}{'':10}{'':14}{short_path(app.path, home)}")
    return 0


@dataclass
class Candidate:
    path: str
    group: str
    note: str
    size: int = 0

    def to_json(self) -> dict:
        d = asdict(self)
        d["size_human"] = human(self.size)
        return d


def uninstall_candidates(app: AppInfo, home: Path, with_sizes: bool = True) -> list:
    bid, name = app.bundle_id, app.name
    found: dict[str, Candidate] = {}

    def add(path, group, note):
        p = str(path)
        if not os.path.lexists(p):
            return
        key = norm(p)
        if key in found:
            return
        found[key] = Candidate(path=key, group=group, note=note,
                                   size=dir_size(key) if with_sizes else 0)

    lib = home / "Library"
    if bid:
        add(lib / "Caches" / bid, "cache", "Bundle ID 精确命中")
        add(lib / "Containers" / bid, "data", "沙盒容器：含账号与文档")
        add(lib / "Application Scripts" / bid, "data", "应用脚本目录")
        add(lib / "Preferences" / f"{bid}.plist", "data", "偏好设置")
        add(lib / "Saved Application State" / f"{bid}.savedState", "data", "窗口恢复状态")
        add(lib / "HTTPStorages" / bid, "data", "网络存储")
        add(lib / "WebKit" / bid, "data", "WebKit 数据")
        add(lib / "Cookies" / f"{bid}.binarycookies", "data", "Cookie")
        for pattern in (f"LaunchAgents/*{bid}*.plist", f"Preferences/ByHost/{bid}.*.plist"):
            for hit in glob.glob(str(lib / pattern)):
                add(hit, "data", "按 Bundle ID 匹配")

    for base, note in ((lib / "Application Support", "按应用名精确匹配"),
                       (lib / "Logs", "日志目录")):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            stem = child.name.lower().replace(" ", "")
            if stem in {name.lower().replace(" ", ""), bid.lower()}:
                add(child, "data", note)

    for child in lib.glob("LaunchAgents/*.plist") if (lib / "LaunchAgents").is_dir() else []:
        stem = child.stem.lower()
        if name.lower().replace(" ", "") in stem.replace(" ", ""):
            add(child, "data", "按应用名匹配的启动项")

    if bid:
        team = bid.split(".")[0]
        if re.fullmatch(r"[A-Z0-9]{10}", team):
            for child in glob.glob(str(lib / "Group Containers" / f"{team}.*")):
                add(child, "probable", "同开发者的共享容器，需人工判断")
    for child in lib.glob("Group Containers/*"):
        if name.lower().replace(" ", "") in child.name.lower().replace(" ", ""):
            add(child, "probable", "名称包含应用名，需人工判断")

    if bid:
        add(Path("/Library/Application Support") / name, "admin", "系统级应用数据")
        add(Path("/Library/Preferences") / f"{bid}.plist", "admin", "系统级偏好")
        patterns = [
            f"/Library/LaunchDaemons/*{bid}*.plist",
            f"/Library/LaunchAgents/*{bid}*.plist",
            f"/Library/PrivilegedHelperTools/*{bid}*",
        ]
        for pattern in patterns:
            for hit in glob.glob(pattern):
                add(hit, "admin", "系统级启动项 / 特权助手")

    return sorted(found.values(), key=lambda c: (-c.size, c.group, c.path))


def pkg_receipts(app: AppInfo) -> list:
    try:
        proc = subprocess.run(["pkgutil", "--pkgs"], capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    key = (app.bundle_id or app.name).lower()
    return [p.strip() for p in proc.stdout.splitlines()
            if key and key in p.lower()][:10]


def app_running(app: AppInfo) -> bool:
    if not app.executable:
        return False
    try:
        proc = subprocess.run(["pgrep", "-x", app.executable], capture_output=True, text=True, timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


def resolve_app(apps: list, query: str) -> AppInfo | None:
    query_l = query.lower()
    exact = [a for a in apps if a.path == query]
    if exact:
        return exact[0]
    same_bid = [a for a in apps if a.bundle_id and a.bundle_id.lower() == query_l]
    if same_bid:
        return same_bid[0]
    same_name = [a for a in apps if a.name.lower() == query_l or Path(a.path).stem.lower() == query_l]
    if same_name:
        return same_name[0]
    partial = [a for a in apps if query_l in a.name.lower() or query_l in a.bundle_id.lower()]
    return partial[0] if len(partial) == 1 else None


def cmd_uninstall(args) -> int:
    home = Path(args.home).expanduser().resolve()
    apps = find_apps(home)
    app = resolve_app(apps, args.app)
    if not app:
        print(f"没找到唯一匹配的应用：{args.app}")
        print("用 `python3 macclean.py apps --filter 关键词` 先确认名称。")
        return 2

    if app.bundle_id.startswith("com.apple."):
        print(f"拒绝：{app.name} 是 macOS 自带应用，不在卸载范围。")
        return 2

    print(f"macclean {VERSION} · 卸载 {app.name}")
    print(f"主程序：{app.path}")
    app.size = dir_size(app.path)
    print(f"Bundle ID：{app.bundle_id or '—'}   版本：{app.version or '—'}   {human(app.size)}")

    if app_running(app) and not args.force:
        print(f"\n{app.name} 正在运行。先退出再卸载，或加 --force 跳过检查。")
        return 2

    candidates = uninstall_candidates(app, home)
    groups = {"cache": [], "data": [], "probable": [], "admin": []}
    for c in candidates:
        groups.setdefault(c.group, []).append(c)

    chosen = [Candidate(app.path, "app", "主程序", app.size)] + groups.get("cache", [])

    print("\n[1] 主程序")
    print(f"    {pad(human(app.size), 10)}{short_path(app.path, home)}")

    def show(group, title, note=""):
        rows = groups.get(group, [])
        print(f"\n[{title}]{note}")
        if not rows:
            print("    无")
            return
        for c in rows:
            print(f"    {pad(human(c.size), 10)}{pad(cut(c.note, 26), 28)}{short_path(c.path, home)}")

    show("cache", "2", "  默认勾选，删了会重建")
    show("data", "3", "  需加 --with-data 才处理，含账号与设置")
    show("probable", "4", "  证据不足，需加 --include-probable")
    show("admin", "5", "  系统级，需管理员；本工具只生成脚本")

    if args.with_data:
        chosen += groups.get("data", [])
    if args.include_probable:
        chosen += groups.get("probable", [])

    receipts = pkg_receipts(app)
    if receipts:
        print("\n[安装收据] 以下 pkg 记录不会被自动清除：")
        for r in receipts:
            print(f"    {r}")

    total = sum(c.size for c in chosen)
    print(f"\n将处理 {len(chosen)} 项，约 {human(total)}")

    if not args.yes:
        print("未执行。确认后加 --yes；要连应用数据一起清理再加 --with-data。")
        return 0

    results = trash_paths([c.path for c in chosen], dry_run=False, trash_dir=args.trash_dir)
    ok = [r for r in results if r[1]]
    fail = [r for r in results if not r[1]]
    hr("执行结果")
    print(f"成功 {len(ok)} 项，失败 {len(fail)} 项")
    for path, _, msg in fail:
        print(f"  失败 {short_path(path, home)}：{msg}")

    if groups.get("admin") and args.allow_admin:
        script = write_admin_script(
            [Item(path=c.path, name=app.name, rule_id="uninstall-admin", level="B",
                  size=c.size, mtime=time.time(), reason=c.note, rebuildable=False)
             for c in groups["admin"]],
            home, args.admin_script,
        )
        print(f"\n系统级残留未执行，已生成脚本：{script}")

    if args.forget_receipts and receipts:
        for r in receipts:
            if args.yes:
                subprocess.run(["pkgutil", "--forget", r], capture_output=True, text=True)
                print(f"已移除安装收据：{r}")

    if args.json:
        payload = {
            "tool": "macclean",
            "version": VERSION,
            "executed_at": datetime.now().isoformat(timespec="seconds"),
            "app": app.to_json(),
            "items": [c.to_json() for c in chosen],
            "results": [{"path": p, "ok": g, "message": m} for p, g, m in results],
            "receipts": receipts,
        }
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"卸载记录：{args.json}")
    return 0


# ---------------------------------------------------------------- doctor

PROTECTED_PROBES = [
    ("~/Library/Mail", "邮件数据（需要完全磁盘访问）"),
    ("~/Library/Messages", "信息数据库（需要完全磁盘访问）"),
    ("~/Library/Safari", "Safari 数据（需要完全磁盘访问）"),
    ("/Library/Caches", "系统级缓存（需要管理员权限）"),
]


def cmd_doctor(args) -> int:
    home = Path(args.home).expanduser().resolve()
    print(f"macclean {VERSION} · 环境检查")
    hr()
    print(f"Python      {sys.version.split()[0]}  ({sys.executable})")
    print(f"系统        macOS {platform_version()}")
    print(f"用户主目录  {home}")
    print(f"osascript   {'可用' if shutil.which('osascript') else '缺失'}")

    hr("权限探测")
    for raw, label in PROTECTED_PROBES:
        path = Path(expand(raw, home))
        if not path.exists():
            print(f"  ○ {label}：路径不存在，跳过")
            continue
        try:
            next(os.scandir(path), None)
            print(f"  ✓ {label}：可读")
        except PermissionError:
            print(f"  ✗ {label}：被系统拒绝")
        except OSError as exc:
            print(f"  ? {label}：{exc}")

    print("\n若上面出现 ✗：系统设置 → 隐私与安全性 → 完全磁盘访问，"
          "把运行本工具的终端或 Python 加进去，然后重开终端。")
    print("系统级缓存（/Library/Caches）始终需要 sudo，本工具不自动提权。")

    rules = load_rules(Path(args.rules))
    hr("规则")
    print(f"  规则 {len(rules['rules'])} 条，红线 {len(rules.get('redlines', []))} 条")
    for lv in ("S", "A", "B"):
        ids = [r["id"] for r in rules["rules"] if r["level"] == lv]
        print(f"  {LEVEL_LABEL[lv]}：{len(ids)} 条")
    return 0


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>macclean 扫描报告</title>
<style>
body{{margin:0;background:#fff;color:#1f1f1d;font:14px/1.7 -apple-system,"PingFang SC",sans-serif;padding:40px 24px}}
main{{max-width:980px;margin:0 auto}}
h1{{font-size:24px;margin:0 0 4px;font-weight:600}}
h2{{font-size:17px;margin:32px 0 10px;padding-bottom:6px;border-bottom:1px solid #e3e2dd;font-weight:600}}
.sub{{color:#6b6a65;margin-bottom:24px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{border:1px solid #e3e2dd;padding:7px 9px;text-align:left}}
th{{background:#f7f7f5}}
td.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.lv{{display:inline-block;padding:0 7px;border-radius:9px;font-size:12px;font-weight:600}}
.S{{background:#eaf3de;color:#3b6d11}}.A{{background:#faeeda;color:#854f0b}}.B{{background:#fcebeb;color:#a32d2d}}
code{{font:12px ui-monospace,Menlo,monospace;background:#f7f7f5;padding:1px 4px;border-radius:4px}}
.note{{background:#f7f7f5;border-left:3px solid #185fa5;padding:10px 14px;border-radius:0 8px 8px 0;margin:16px 0}}
</style></head><body><main>
<h1>macclean 扫描报告</h1>
<p class="sub">{generated} · 范围 {home} · 耗时 {elapsed}s · 共 {count} 项</p>
<div class="note">本报告由只读扫描生成，未移动或删除任何文件。清理前请逐项核对。</div>
<h2>汇总</h2>
<table><tr><th>级别</th><th>含义</th><th>项数</th><th>合计</th></tr>{summary_rows}</table>
<h2>可清理项（按大小）</h2>
<table><tr><th>级别</th><th>项目</th><th>路径</th><th>大小</th><th>最后修改</th></tr>{item_rows}</table>
<h2>红线目录（只展示，禁止清理）</h2>
<table><tr><th>类别</th><th>路径</th><th>处置</th></tr>{redline_rows}</table>
</main></body></html>"""


def write_html_report(payload: dict, out_path: str) -> str:
    levels = {"S": "可重建", "A": "含用户数据", "B": "需管理员", "C": "红线"}
    summary_rows = "".join(
        f"<tr><td><span class='lv {k}'>{k}</span></td><td>{levels.get(k, '')}</td>"
        f"<td class='n'>{v['count']}</td><td class='n'>{human(v['bytes'])}</td></tr>"
        for k, v in sorted(payload.get("summary", {}).items())
    )
    item_rows = "".join(
        f"<tr><td><span class='lv {i['level']}'>{i['level']}</span></td><td>{i['name']}</td>"
        f"<td><code>{i['path']}</code></td><td class='n'>{i['size_human']}</td>"
        f"<td class='n'>{i['mtime_iso']}</td></tr>"
        for i in payload.get("items", [])
    )
    redline_rows = "".join(
        f"<tr><td>{r['label']}</td><td><code>{short_path(r['path'], Path.home())}</code></td>"
        f"<td>{r['reason']}</td></tr>"
        for r in payload.get("redlines", [])
    )
    html = HTML_TEMPLATE.format(
        generated=payload.get("generated_at", ""), home=payload.get("home", ""),
        elapsed=payload.get("elapsed_seconds", ""), count=len(payload.get("items", [])),
        summary_rows=summary_rows, item_rows=item_rows or "<tr><td colspan='5'>无</td></tr>",
        redline_rows=redline_rows or "<tr><td colspan='3'>无</td></tr>",
    )
    Path(out_path).write_text(html, encoding="utf-8")
    return out_path


def cmd_report(args) -> int:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    out = args.html or str(Path(args.input).with_suffix(".html"))
    write_html_report(payload, out)
    items = payload.get("items", [])
    print(f"已生成 {out}")
    print(f"共 {len(items)} 项，合计 {human(sum(i.get('size', 0) for i in items))}")
    return 0


def platform_version() -> str:
    try:
        return subprocess.run(["sw_vers", "-productVersion"], capture_output=True,
                              text=True, timeout=5).stdout.strip() or "未知"
    except Exception:
        return "未知"


# ---------------------------------------------------------------- CLI

def add_common(parser):
    parser.add_argument("--home", default=str(Path.home()), help="扫描根目录，测试时可指向临时目录")
    parser.add_argument("--rules", default=str(RULES_PATH), help="规则文件路径")
    parser.add_argument("--json", default="", help="把结果写成 JSON")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="macclean",
        description="macOS 清理与卸载原型：只读扫描、白名单分级、移入废纸篓。",
    )
    p.add_argument("--version", action="version", version=f"macclean {VERSION}")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("scan", help="只读扫描，输出可清理项与红线目录")
    add_common(s)
    s.add_argument("--top", type=int, default=25, help="显示前 N 项，0 表示全部")
    s.add_argument("--levels", default="SAB", help="包含的级别，默认 SAB")
    s.add_argument("--time-budget", type=float, default=90.0, help="扫描时间上限（秒）")
    s.add_argument("--include-trash", action="store_true", help="把废纸篓也列为候选")
    s.set_defaults(func=cmd_scan)

    c = sub.add_parser("clean", help="按级别清理，默认只演练不执行")
    add_common(c)
    c.add_argument("--top", type=int, default=25)
    c.add_argument("--min-size", type=int, default=0, help="只处理大于该字节数的项")
    c.add_argument("--rule", action="append", default=[], help="只处理指定规则 id")
    c.add_argument("--include-a", action="store_true", help="允许处理 A 级（含用户数据）")
    c.add_argument("--include-trash", action="store_true", help="允许清空废纸篓")
    c.add_argument("--allow-admin", action="store_true", help="为 B 级生成管理员脚本")
    c.add_argument("--admin-script", default="", help="管理员脚本输出路径")
    c.add_argument("--time-budget", type=float, default=90.0)
    c.add_argument("--yes", action="store_true", help="确认执行，真的移入废纸篓")
    c.add_argument("--trash-dir", default="", help="[仅测试] 覆盖废纸篓目录，不影响真实废纸篓")
    c.set_defaults(func=cmd_clean)

    a = sub.add_parser("apps", help="列出已安装应用")
    add_common(a)
    a.add_argument("--filter", default="", help="按名称或 Bundle ID 过滤")
    a.add_argument("--sizes", action="store_true", help="计算每个应用占用（较慢）")
    a.set_defaults(func=cmd_apps)

    u = sub.add_parser("uninstall", help="卸载应用并列出关联文件")
    add_common(u)
    u.add_argument("app", help="应用名、Bundle ID 或 .app 路径")
    u.add_argument("--with-data", action="store_true", help="一并清理应用数据（容器、偏好、设置）")
    u.add_argument("--include-probable", action="store_true", help="包含证据不足的关联目录")
    u.add_argument("--allow-admin", action="store_true", help="为系统级残留生成脚本")
    u.add_argument("--admin-script", default="")
    u.add_argument("--forget-receipts", action="store_true", help="清理后移除 pkg 安装收据")
    u.add_argument("--force", action="store_true", help="跳过运行中检查")
    u.add_argument("--yes", action="store_true", help="确认执行")
    u.add_argument("--trash-dir", default="", help="[仅测试] 覆盖废纸篓目录")
    u.set_defaults(func=cmd_uninstall)

    d = sub.add_parser("doctor", help="检查权限与规则状态")
    add_common(d)
    d.set_defaults(func=cmd_doctor)

    r = sub.add_parser("rules", help="打印规则清单")
    add_common(r)
    r.set_defaults(func=cmd_rules)

    rp = sub.add_parser("report", help="把 scan 的 JSON 转成可读 HTML")
    rp.add_argument("input", help="scan --json 生成的报告")
    rp.add_argument("--html", default="", help="输出 HTML 路径")
    rp.set_defaults(func=cmd_report)
    return p


def cmd_rules(args) -> int:
    rules = load_rules(Path(args.rules))
    print(f"规则版本 {rules.get('version', '?')}   文件 {args.rules}")
    hr()
    print(f"{pad('级别', 6)}{pad('ID', 26)}{pad('名称', 24)}说明")
    for rule in rules["rules"]:
        flag = " [需 --include-trash]" if rule.get("requires_flag") else ""
        adm = " [需管理员]" if rule.get("needs_admin") else ""
        print(f"{pad(rule['level'], 6)}{pad(rule['id'], 26)}"
              f"{pad(cut(rule['name'], 22), 24)}{rule.get('reason', '')}{flag}{adm}")
    print(f"\n红线目录 {len(rules.get('redlines', []))} 条：")
    for r in rules.get("redlines", []):
        print(f"  {r}")
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    args.levels_set = {x for x in getattr(args, "levels", "SAB").upper() if x in "SABC"}
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except FileNotFoundError as exc:
        print(f"文件不存在：{exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
