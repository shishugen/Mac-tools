#!/usr/bin/env python3
"""Thaw 2.x 汉化 + 改名 + 开启下拉栏补丁。

用法：
  python3 patch_thaw.py                # 打补丁
  python3 patch_thaw.py --restore      # 还原原版
  python3 patch_thaw.py --app /path/Thaw.app   # 指定 App 路径

原理：Thaw 中文包有 68 条没翻译完（value 等于英文 key），
本脚本把这些 key 补成中文；同时改应用名、开启 Thaw Bar。
不修改任何二进制代码，只改资源文件和偏好设置。
"""
import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
APP_NAME = "菜单栏管家"
BUNDLE_ID = "com.stonerl.Thaw"
BACKUP_DIR = Path.home() / ".thaw-cn-patch-backup"

# 已确认的坑：不要带 --options runtime 重签名，
# 否则 Sparkle.framework 的 Team ID 与主程序不一致，dyld 加载失败无法启动。
CODESIGN_CMD = ["codesign", "--force", "--deep", "--sign", "-"]


def run(cmd, check=True, capture=False):
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def find_app(explicit=None):
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.exists():
            sys.exit(f"找不到 App: {p}")
        return p
    for name in (APP_NAME + ".app", "Thaw.app"):
        p = Path("/Applications") / name
        if p.exists():
            return p
    sys.exit("在 /Applications 找不到 Thaw.app 或 菜单栏管家.app")


def translate_strings(app, translations, backup):
    res = app / "Contents/Resources/zh-Hans.lproj/Localizable.strings"
    if not res.exists():
        sys.exit(f"找不到中文语言包: {res}")

    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(res, backup / "Localizable.strings.original")

    xml_tmp = backup / "zh-Hans-converted.xml"
    run(["plutil", "-convert", "xml1", "-o", str(xml_tmp), str(res)])
    d = plistlib.load(open(xml_tmp, "rb"))
    patched = 0
    for k, v in translations.items():
        if k in d:
            d[k] = v
            patched += 1
    plistlib.dump(d, open(xml_tmp, "wb"), fmt=plistlib.FMT_XML, sort_keys=True)
    shutil.copy2(xml_tmp, res)
    print(f"  [i18n] 补齐 {patched} 条翻译")
    return patched


def rename_app(app):
    plist = app / "Contents/Info.plist"
    for key in ("CFBundleName", "CFBundleDisplayName"):
        run(["/usr/libexec/PlistBuddy", "-c", f"Set :{key} {APP_NAME}", str(plist)], check=False)
    if app.name != APP_NAME + ".app":
        new = app.parent / (APP_NAME + ".app")
        if new.exists():
            shutil.rmtree(new)
        app.rename(new)
        print(f"  [name] {app.name} -> {new.name}")
        return new
    print("  [name] 显示名已设置")
    return app


def enable_thaw_bar():
    """开启所有显示器的 Thaw Bar（点击图标时隐藏项在菜单栏下方展开）。"""
    plist = Path.home() / "Library/Preferences/BUNDLE_ID.plist"
    # 用 defaults export/import，避免直接写二进制 plist
    tmp = BACKUP_DIR / "prefs.plist"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    run(["defaults", "export", BUNDLE_ID, str(tmp)])
    d = plistlib.load(open(tmp, "rb"))
    raw = d.get("DisplayIceBarConfigurations")
    if raw is None:
        print("  [bar] 未找到 DisplayIceBarConfigurations，跳过")
        return
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    cfg = json.loads(raw)
    for disp, v in cfg.items():
        v["useIceBar"] = True
    d["DisplayIceBarConfigurations"] = json.dumps(cfg, separators=(",", ":")).encode("utf-8")
    plistlib.dump(d, open(tmp, "wb"))
    run(["defaults", "import", BUNDLE_ID, str(tmp)])
    print(f"  [bar] 已开启 {len(cfg)} 台显示器的 Thaw Bar")

    run(["defaults", "write", BUNDLE_ID, "AppleLanguages", "-array", "zh-Hans", "zh-Hans-CN"])
    print("  [i18n] 已指定应用语言为简体中文")


def resign_and_launch(app):
    run(["xattr", "-dr", "com.apple.quarantine", str(app)], check=False)
    run(CODESIGN_CMD + [str(app)], check=False)
    print("  [sign] 已重新 ad-hoc 签名")
    run(["killall", "Thaw"], check=False)
    run(["killall", "Finder"], check=False)
    run(["open", "-a", str(app)], check=False)
    print("  [launch] 已重启应用和 Finder")


def restore(app):
    backup = BACKUP_DIR / "Localizable.strings.original"
    if not backup.exists():
        sys.exit(f"找不到语言包备份: {backup}")
    res = app / "Contents/Resources/zh-Hans.lproj/Localizable.strings"
    shutil.copy2(backup, res)
    print("  [restore] 语言包已还原")
    plist = app / "Contents/Info.plist"
    for key in ("CFBundleName", "CFBundleDisplayName"):
        run(["/usr/libexec/PlistBuddy", "-c", f"Set :{key} Thaw", str(plist)], check=False)
    if app.name != "Thaw.app":
        new = app.parent / "Thaw.app"
        if new.exists():
            shutil.rmtree(new)
        app.rename(new)
        app = new
    run(["xattr", "-dr", "com.apple.quarantine", str(app)], check=False)
    run(CODESIGN_CMD + [str(app)], check=False)
    run(["killall", "Thaw"], check=False)
    run(["open", "-a", str(app)], check=False)
    print("  [restore] 已完成，应用已重启")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", help="Thaw.app 路径")
    ap.add_argument("--restore", action="store_true", help="还原原版")
    args = ap.parse_args()

    app = find_app(args.app)
    print(f"目标: {app}")

    if args.restore:
        restore(app)
        return

    translations = json.load(open(HERE / "translations.json", encoding="utf-8"))
    print(f"翻译条目: {len(translations)}")
    translate_strings(app, translations, BACKUP_DIR)
    app = rename_app(app)
    enable_thaw_bar()
    resign_and_launch(app)
    print("\n完成。备份在", BACKUP_DIR)


if __name__ == "__main__":
    main()
