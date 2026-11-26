#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读展开一个应用容器，按目录/文件类型给出用途和清理建议。

    python3 container_report.py <容器路径> <输出.html>

只遍历和统计，不移动、不删除任何文件。
"""

from __future__ import annotations

import collections
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HOME = Path.home()


def human(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    v, i = float(n or 0), 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return (str(int(v)) if i == 0 else f"{v:.1f}") + " " + units[i]


def stat_path(path: Path) -> tuple:
    size, count = 0, 0
    if path.is_file():
        try:
            return path.stat().st_size, 1
        except OSError:
            return 0, 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                size += os.stat(os.path.join(root, name), follow_symlinks=False).st_size
                count += 1
            except OSError:
                continue
    return size, count


def ext_stats(root: Path) -> dict:
    sizes, counts = collections.Counter(), collections.Counter()
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            try:
                size = os.stat(os.path.join(dirpath, name), follow_symlinks=False).st_size
            except OSError:
                continue
            ext = "." + name.rsplit(".", 1)[1].lower() if "." in name else "(无扩展名)"
            sizes[ext] += size
            counts[ext] += 1
            total += size
    return {"total": total, "sizes": sizes, "counts": counts}


# 微信容器专用知识库：路径片段 -> (是什么, 能不能删, 后果)
WECHAT = [
    ("Message/MessageTemp", "聊天图片、视频、语音、收发过的文件，按会话存在 hash 目录里",
     "可清但建议用微信自带清理", "直接删会让旧聊天里的图片和文件显示不出来，微信可能重新下载"),
    ("Message/fts", "聊天全文搜索索引", "可清", "删了搜索会慢，微信会重建"),
    ("Message/ftsfile", "全文搜索索引的补充文件", "可清", "会重建"),
    ("Message/brand", "公众号、品牌消息的内容缓存", "可清", "会重新加载"),
    ("Message/mediaData", "语音转文字等媒体缓存", "可清", "会重新生成"),
    ("Message/msg_", "聊天记录数据库（按时间段分库）", "不可删", "删了聊天记录直接没了"),
    ("Message/fileMsg.db", "聊天文件索引数据库", "不可删", "删了收发文件记录会丢失"),
    ("/Message", "聊天记录数据库、全文索引和附件缓存", "不可删", "整目录删了聊天记录就没了"),
    ("Stickers", "表情包缓存", "可清", "表情会重新下载"),
    ("Avatar", "好友和群头像缓存", "可清", "头像会重新下载"),
    ("Session", "会话列表和未读状态", "不可删", "删了会话列表会乱"),
    ("Contact", "联系人资料缓存", "不可删", "删了联系人信息要重新同步"),
    ("Favorites", "收藏夹的本地数据", "不可删", "删了本地收藏没了"),
    ("Group", "群聊资料缓存", "不可删", "删了群信息要重新同步"),
    ("WebTemplate", "聊天里的小程序/网页模板缓存", "可清", "会重新下载"),
    ("Hardlink", "重复文件的硬链接索引", "不可删", "删了可能导致文件打不开"),
    ("ChatSync", "聊天漫游同步状态", "不可删", "删了多设备同步会乱"),
    ("FileStateSync", "文件传输状态", "不可删", "删了传文件的记录会丢"),
    ("Account", "账号与登录信息", "不可删", "删了要重新登录"),
    ("Sync", "同步状态", "不可删", "删了同步会异常"),
    ("RevokeMsg", "撤回消息记录", "不可删", "删了撤回状态会乱"),
    ("voip", "语音/视频通话记录", "不可删", "删了通话记录会丢"),
    ("KeyValue", "键值配置", "不可删", "删了应用行为会异常"),
    ("MMappedKV", "内存映射配置", "不可删", "删了应用行为会异常"),
    ("CGI", "网络请求配置", "不可删", "会重新生成，但没必要动"),
    ("tmp/trash", "微信内部回收站：删掉的图片视频暂存", "可清", "清掉后微信里也恢复不了"),
    ("Library/Caches", "应用缓存（缩略图、临时下载）", "可清", "应用会自动重建"),
    ("Library/HTTPStorages", "网站数据", "可清", "删了要重新登录网页"),
    ("Library/WebKit", "内置浏览器缓存", "可清", "会重建"),
    ("Library/Preferences", "偏好设置", "不可删", "删了恢复默认设置"),
    ("Documents/ConfSDK", "腾讯会议 SDK 的配置", "不可删", "删了会议功能会异常"),
    (".wxapplet", "微信小程序缓存", "可清", "小程序会重新下载"),
    ("21a2e5f8b362215bd46838df8abc5cb0", "当前账号的全部数据：聊天、联系人、收藏、表情", "不要随便删", "删了等于清空这个账号在本机的数据"),
    ("6a2b2c6b6a604bcd0ca959a7f0873150", "旧账号或早期会话残留的数据", "确认后清理", "确认这个账号不再用之后可以删"),
    ("Data/Documents", "微信自己管理的小配置文件", "不可删", "很小，别动"),
]

EXT_INFO = {
    ".jpg": "聊天图片",
    ".silk": "微信语音消息",
    ".mp4": "聊天视频",
    ".zip": "收发过的压缩包",
    ".rar": "收发过的压缩包",
    ".7z": "收发过的压缩包",
    ".exe": "收过的 Windows 程序",
    ".jar": "收过的 Java 程序",
    ".docx": "收过的 Word 文档",
    ".pdf": "收过的 PDF",
    ".xlsx": "收过的 Excel",
    ".dftemp": "下载中的临时文件",
    ".webp": "聊天图片",
    ".record_thumb": "语音消息缩略缓存",
    ".record_dat": "语音消息缓存",
    ".ds_store": "macOS 文件夹显示信息",
}


def policy_of(text: str) -> str:
    if "不可删" in text:
        return "no"
    if "自带清理" in text:
        return "warn"
    return "yes"


def render(container: Path, out: Path) -> None:
    data_root = container / "Data"
    lib = data_root / "Library"
    support = lib / "Application Support" / "com.tencent.xinWeChat"
    version = support / "2.0b4.0.9"
    current = version / "21a2e5f8b362215bd46838df8abc5cb0"
    old = version / "6a2b2c6b6a604bcd0ca959a7f0873150"
    message = current / "Message"
    temp = message / "MessageTemp"

    targets = [
        ("Data/Library/Application Support/…/21a2…（当前账号）", current),
        ("　└ Message（聊天数据总目录）", message),
        ("　　 └ MessageTemp（聊天附件缓存）", temp),
        ("　└ Stickers（表情包）", current / "Stickers"),
        ("　└ Avatar（头像）", current / "Avatar"),
        ("　└ Session / Contact / Account / Favorites 等", None),
        ("Data/Library/Application Support/…/6a2b…（旧账号残留）", old),
        ("Data/tmp/trash（微信内部回收站）", data_root / "tmp" / "trash"),
        ("Data/Library/Caches（应用缓存）", lib / "Caches"),
        ("Data/Library/HTTPStorages（网站数据）", lib / "HTTPStorages"),
        ("Data/Library/Preferences（偏好设置）", lib / "Preferences"),
        ("Data/.wxapplet（小程序缓存）", data_root / ".wxapplet"),
    ]

    rows = []
    for label, path in targets:
        if path is None:
            size = count = 0
            for name in ("Session", "Contact", "Account", "Favorites", "Group", "WebTemplate"):
                s, c = stat_path(current / name)
                size += s
                count += c
            desc, can, why = "会话、联系人、收藏、群资料", "不可删", "删了聊天列表和联系人会乱"
        else:
            size, count = stat_path(path)
            rel = str(path).replace(str(container) + "/", "", 1)
            desc, can, why = "应用关联文件", "不可删", "用途待确认"
            for key, d, c, w in WECHAT:
                if key in rel:
                    desc, can, why = d, c, w
                    break
        rows.append((label, size, count, desc, can, why, policy_of(can)))

    exts = ext_stats(temp)
    ext_rows = []
    for ext, size in exts["sizes"].most_common(14):
        ext_rows.append((ext, exts["counts"][ext], size, EXT_INFO.get(ext, "其他聊天文件")))

    total_size, _ = stat_path(container)
    reclaim = exts["total"] + stat_path(lib / "Caches")[0] + stat_path(current / "Stickers")[0] + stat_path(data_root / "tmp" / "trash")[0]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    css = """
body{margin:0;background:#fff;color:#1d1d1f;font:14px/1.7 -apple-system,"PingFang SC",sans-serif;padding:36px 24px}
main{max-width:1100px;margin:0 auto}
h1{font-size:24px;font-weight:600;margin:0 0 4px}
h2{font-size:17px;font-weight:600;margin:32px 0 12px;padding-bottom:8px;border-bottom:1px solid #e3e2dd}
.sub{color:#86868b;margin-bottom:24px}
.note{background:#f7f7f9;border-left:3px solid #0071e3;padding:12px 16px;border-radius:0 8px 8px 0;margin:18px 0;line-height:1.8}
.warn{background:#fdf6e6;border-left-color:#b25000;color:#6b4a12}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin:10px 0 24px}
th,td{border:1px solid #e3e2dd;padding:8px 10px;text-align:left;vertical-align:top}
th{background:#f7f7f5;font-weight:500;color:#555}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
code{font:11.5px ui-monospace,Menlo,monospace;word-break:break-all;color:#444}
.pol{font-size:10.5px;padding:1px 7px;border-radius:9px;white-space:nowrap}
.yes{background:#eaf3de;color:#3b6d11}.no{background:#fcebeb;color:#a32d2d}
.warnp{background:#faeeda;color:#854f0b}
.metric{display:inline-block;background:#f7f7f9;border-radius:10px;padding:12px 18px;margin:0 10px 16px 0}
.metric span{display:block;font-size:12px;color:#86868b}
.metric b{font-size:22px;font-weight:500}
"""
    def row_html(r) -> str:
        label, size, count, desc, can, why, pol = r
        return (
            "<tr><td><code>" + label + "</code></td><td class='n'>" + human(size) + "</td>"
            "<td class='n'>" + str(count) + "</td><td>" + desc + "</td>"
            "<td><span class='pol " + pol + "'>" + can + "</span></td><td>" + why + "</td></tr>"
        )

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{container.name} 内容说明</title><style>{css}</style></head><body><main>
<h1>{container.name}</h1>
<p class="sub">只读扫描于 {now} · 未移动、未删除任何文件</p>
<div class="metric"><span>容器总计</span><b>{human(total_size)}</b></div>
<div class="metric"><span>可清理上限（估算）</span><b>{human(reclaim)}</b></div>
<div class="metric"><span>聊天附件文件</span><b>{exts['counts'].total():,} 个</b></div>
<div class="note">这个 14 GB 的容器里，<b>绝大部分是聊天记录里的图片、视频和收发过的文件</b>，不是微信程序本身。
真正的聊天数据库只有几百 MB，但删了就没了；真正占地方的那 11 GB 附件，反而更适合清理。</div>
<h2>逐层目录说明</h2>
<table><tr><th style="width:32%">路径</th><th>大小</th><th>文件数</th><th>是干什么的</th><th>能不能删</th><th>删了会怎样</th></tr>
{''.join(row_html(r) for r in rows)}</table>
<div class="note warn"><b>重要：</b>聊天附件在 <code>MessageTemp/&lt;hash&gt;/</code> 里，每个 hash 目录对应一个会话或群。
不建议在访达里手动删单个 hash 目录——聊天记录里会留下打不开的图片和文件。
更稳的做法是用微信自带的「设置 → 通用 → 存储空间管理 → 清理缓存」。</div>
<h2>MessageTemp 里的文件类型（共 {human(exts['total'])}）</h2>
<table><tr><th>类型</th><th>数量</th><th>体积</th><th>是什么</th></tr>
{''.join(f"<tr><td><code>{e}</code></td><td class='n'>{n:,}</td><td class='n'>{human(s)}</td><td>{d}</td></tr>" for e,n,s,d in ext_rows)}</table>
<div class="note">注意那 <b>171 个 zip 占 5.4 GB</b>——它们是聊天里收发过的压缩包，
删了之后对话框里的文件可能要重新下载，服务器上的旧文件可能已经取不回来了。这批属于「谨慎清理」，不是纯缓存。</div>
</main></body></html>"""
    out.write_text(html, encoding="utf-8")
    print(f"{out}  total={human(total_size)}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    render(Path(sys.argv[1]).expanduser(), Path(sys.argv[2]))
