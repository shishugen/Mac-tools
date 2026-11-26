#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读展开应用容器 + 受控删除子项。

给桌面版「应用详情 → 展开」用。所有遍历只读；删除必须走 delete_subpaths，
且只能删服务端重新判定为可清的子目录。
"""

from __future__ import annotations

import collections
import os
import re
from pathlib import Path

AUTO_NAMES = {
    "Data", "Library", "Application Support", "com.tencent.xinWeChat",
    "Containers", "Resources",
}
AUTO_RE = re.compile(r"^\d+\.\d+[0-9a-zA-Z.\-_]*$")

PURPOSES = [
    ("Message/MessageTemp", "聊天图片、视频、语音、收发过的文件，按会话存在 hash 目录里", "warn",
     "直接删会让旧聊天里的图片显示不出来，建议用微信自带清理"),
    ("Message/fts", "聊天全文搜索索引", "safe", "删了搜索会慢，微信会重建"),
    ("Message/ftsfile", "全文搜索索引补充文件", "safe", "会重建"),
    ("Message/brand", "公众号、品牌消息缓存", "safe", "会重新加载"),
    ("Message/mediaData", "语音转文字等媒体缓存", "safe", "会重新生成"),
    ("msg_", "聊天记录数据库（按时间段分库）", "no", "删了聊天记录直接没了"),
    ("fileMsg.db", "聊天文件索引数据库", "no", "删了收发文件记录会丢失"),
    ("/Message", "聊天记录数据库、全文索引与附件缓存", "no", "整目录删了聊天记录就没了"),
    ("Stickers", "表情包缓存", "safe", "表情会重新下载"),
    ("Avatar", "好友和群头像缓存", "safe", "头像会重新下载"),
    ("Session", "会话列表和未读状态", "no", "删了会话列表会乱"),
    ("Contact", "联系人资料缓存", "no", "删了联系人要重新同步"),
    ("Favorites", "收藏夹本地数据", "no", "删了本地收藏没了"),
    ("Group", "群聊资料缓存", "no", "删了群信息要重新同步"),
    ("WebTemplate", "聊天里的小程序/网页模板缓存", "safe", "会重新下载"),
    ("Hardlink", "重复文件的硬链接索引", "no", "删了可能导致文件打不开"),
    ("ChatSync", "聊天漫游同步状态", "no", "删了多设备同步会乱"),
    ("FileStateSync", "文件传输状态", "no", "删了传文件记录会丢"),
    ("Account", "账号与登录信息", "no", "删了要重新登录"),
    ("/Sync", "同步状态", "no", "删了同步会异常"),
    ("RevokeMsg", "撤回消息记录", "no", "删了撤回状态会乱"),
    ("voip", "语音/视频通话记录", "no", "删了通话记录会丢"),
    ("KeyValue", "键值配置", "no", "删了应用行为会异常"),
    ("MMappedKV", "内存映射配置", "no", "删了应用行为会异常"),
    ("tmp/trash", "应用内部回收站：删掉的文件暂存", "safe", "清掉后在应用里也恢复不了"),
    ("Library/Caches", "应用缓存（缩略图、临时下载）", "safe", "应用会自动重建"),
    ("HTTPStorages", "网站数据", "safe", "删了要重新登录网页"),
    ("WebKit", "内置浏览器缓存", "safe", "会重建"),
    ("Preferences", "偏好设置", "no", "删了恢复默认设置"),
    ("ConfSDK", "腾讯会议 SDK 配置", "no", "删了会议功能会异常"),
    (".wxapplet", "微信小程序缓存", "safe", "小程序会重新下载"),
    ("21a2e5f8b362215bd46838df8abc5cb0", "当前账号的全部数据：聊天、联系人、收藏、表情", "no",
     "删了等于清空这个账号在本机的数据"),
    ("6a2b2c6b6a604bcd0ca959a7f0873150", "旧账号或早期会话残留的数据", "warn",
     "确认这个账号不再用之后可以删"),
    ("Caches", "缓存目录", "safe", "应用会自动重建"),
    ("cacheDir", "缓存目录", "safe", "应用会自动重建"),
    ("TemporaryItems", "系统临时目录", "safe", "会重建"),
    (".DS_Store", "macOS 文件夹显示信息", "safe", "系统会自动生成"),
    ("Library", "应用数据主目录：配置、缓存和数据", "manual", "点进去看具体子目录再决定"),
    ("tmp", "临时目录，可能含回收站和缓存", "manual", "展开看看里面是什么"),
    ("Documents", "应用自己管理的小配置文件", "no", "很小，别动"),
]

EXT_INFO = {
    ".jpg": "聊天图片", ".silk": "微信语音消息", ".mp4": "聊天视频",
    ".zip": "收发过的压缩包", ".rar": "收发过的压缩包", ".7z": "收发过的压缩包",
    ".exe": "收过的 Windows 程序", ".jar": "收过的 Java 程序",
    ".docx": "收过的 Word 文档", ".pdf": "收过的 PDF", ".xlsx": "收过的 Excel",
    ".dftemp": "下载中的临时文件", ".webp": "聊天图片", ".ds_store": "macOS 文件夹信息",
}

POLICY_LABEL = {"safe": "可清", "warn": "谨慎", "no": "重要数据", "manual": "需判断"}


def human(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    v, i = float(n or 0), 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return (str(int(v)) if i == 0 else f"{v:.1f}") + " " + units[i]


def stat_path(path: Path) -> tuple:
    try:
        if path.is_file() and not path.is_symlink():
            return path.stat().st_size, 1
    except OSError:
        return 0, 0
    size, count = 0, 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                size += os.stat(os.path.join(root, name), follow_symlinks=False).st_size
                count += 1
            except OSError:
                continue
    return size, count


def describe(path: str, name: str = "") -> tuple:
    normalized = path.replace("\\", "/")
    for key, desc, policy, why in PURPOSES:
        if "/" in key:
            if key in normalized:
                return desc, policy, why
        elif name and (name == key or (key.endswith("_") and name.startswith(key.rstrip("_")))):
            return desc, policy, why
    return "应用关联文件（通常是账号或会话数据）", "manual", "展开看看里面是什么，再决定要不要删"


def auto_descend(path: Path, max_steps: int = 8) -> tuple:
    """从容器根往下走过明显的包装目录，省掉用户点四五次。"""
    trail = []
    current = path
    for _ in range(max_steps):
        if not current.is_dir():
            break
        try:
            entries = [p for p in current.iterdir() if p.is_dir() and not p.is_symlink()]
        except OSError:
            break
        if len(entries) != 1:
            break
        child = entries[0]
        if child.name not in AUTO_NAMES and not AUTO_RE.match(child.name):
            break
        trail.append(child.name)
        current = child
    return current, trail


def inspect(path: Path) -> dict:
    actual, trail = auto_descend(path)
    rows = []
    try:
        entries = list(os.scandir(actual))
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    for entry in entries:
        if entry.name.startswith(".") and entry.name != ".wxapplet":
            continue
        child = Path(entry.path)
        try:
            if child.is_symlink():
                continue
            elif child.is_dir():
                size, count = stat_path(child)
                kind = "dir"
            else:
                size, count = child.stat().st_size, 1
                kind = "file"
        except OSError:
            size, count, kind = 0, 0, "unknown"
        desc, policy, why = describe(str(child), child.name)
        rows.append({
            "name": child.name,
            "path": str(child),
            "size": size,
            "size_human": human(size),
            "count": count,
            "kind": kind,
            "purpose": desc,
            "policy": policy,
            "policy_label": POLICY_LABEL[policy],
            "why": why,
        })

    rows.sort(key=lambda r: r["size"], reverse=True)
    total = sum(r["size"] for r in rows)

    exts = []
    if total > 100 * 1024 * 1024:
        sizes, counts = collections.Counter(), collections.Counter()
        for root, _dirs, files in os.walk(actual):
            for name in files:
                try:
                    value = os.stat(os.path.join(root, name), follow_symlinks=False).st_size
                except OSError:
                    continue
                ext = "." + name.rsplit(".", 1)[1].lower() if "." in name else "(无扩展名)"
                sizes[ext] += value
                counts[ext] += 1
        for ext, value in sizes.most_common(10):
            exts.append({
                "ext": ext, "size": value, "size_human": human(value),
                "count": counts[ext], "purpose": EXT_INFO.get(ext, "其他聊天文件"),
            })

    return {
        "ok": True,
        "requested": str(path),
        "actual": str(actual),
        "trail": trail,
        "total": total,
        "total_human": human(total),
        "children": rows,
        "exts": exts,
        "hint": "这些只是提示，不代表必须删或不能删。是否删除由你决定；删除会进废纸篓，可以放回。",
    }


def delete_subpaths(root: Path, paths: list, dry_run: bool = False) -> dict:
    """只删根目录下明确标为可清的子项。"""
    import macclean as mc

    actual = root.resolve()
    allowed = {}
    for child in actual.iterdir():
        if child.is_symlink():
            continue
        desc, policy, why = describe(str(child), child.name)
        allowed[mc.norm(child)] = {"path": child, "policy": policy, "why": why, "purpose": desc}

    chosen, rejected, warnings = [], [], []
    for raw in paths:
        key = mc.norm(raw)
        hit = allowed.get(key)
        if not hit:
            rejected.append({"path": raw, "why": "不在当前展开目录的直接子项里，请展开对应目录后再选"})
            continue
        if not is_under(key, actual):
            rejected.append({"path": raw, "why": "不在当前展开目录内"})
            continue
        if not str(key).startswith(str(Path.home())):
            rejected.append({"path": raw, "why": "不在用户目录内，工具不允许删"})
            continue
        chosen.append(hit["path"])
        if hit["policy"] != "safe":
            warnings.append({"path": raw, "policy": hit["policy"], "why": hit["why"]})

    results = mc.trash_paths([str(p) for p in chosen], dry_run=dry_run)
    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "results": [{"path": p, "ok": good, "message": msg} for p, good, msg in results],
        "rejected": rejected,
        "warnings": warnings,
    }


def is_under(path: str, root: Path) -> bool:
    p = str(path).rstrip("/")
    r = str(root).rstrip("/")
    return p.startswith(r + "/")
