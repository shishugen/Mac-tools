# Thaw 汉化补丁（菜单栏图标管理器）

给 [Thaw 2.x](https://github.com/thaw-app/Thaw) 补上没翻译完的中文、改成中文名、开启点击图标往下拉显示的补丁脚本。Thaw 本身 GPL-3.0 开源免费，本仓库只包含翻译映射和补丁脚本，不含 Thaw 二进制。

## 做了什么

1. **补翻译**：Thaw 2.1.0 的 `zh-Hans.lproj/Localizable.strings` 里有 68 条 value 仍是英文，本仓库把它们补成中文（`translations.json`）。
2. **改名**：`CFBundleName` / `CFBundleDisplayName` 改为「菜单栏管家」，App 文件名也改成 `菜单栏管家.app`（Finder 显示的是文件名，只改内部字段没用）。
3. **下拉显示**：把 `DisplayIceBarConfigurations` 里每台显示器的 `useIceBar` 设为 `true`。点菜单栏图标时，隐藏的图标在菜单栏下方单独一条栏里展开。
4. **语言指定**：`defaults write com.stonerl.Thaw AppleLanguages -array zh-Hans zh-Hans-CN`。

## 用法

前提：已用 Homebrew 安装 Thaw 2.x（`brew install --cask thaw`），macOS 26。

```bash
# 打补丁
python3 patch_thaw.py

# 还原原版
python3 patch_thaw.py --restore

# 指定 App 路径
python3 patch_thaw.py --app /path/to/Thaw.app
```

脚本会：备份原语言包到 `~/.thaw-cn-patch-backup/` → 改翻译 → 改显示名 → 开启下拉栏 → 去掉隔离属性 → 重新 ad-hoc 签名 → 重启应用和 Finder。

## 已确认的坑

- **签名**：改过资源文件后必须重签名。用 `codesign --force --deep --sign -`；**不要带 `--options runtime`**，否则和 `Sparkle.framework` 的 Team ID 不一致，dyld 加载失败、App 起不来。
- **写 `/Applications`**：工具沙箱可能拒绝直接写 App 内部文件。脚本由用户在终端直接运行，不受此限制。
- **自动更新**：Thaw 自动更新会把补丁覆盖。更新后重新跑一次脚本即可。
- **图标名**：菜单栏里 `Battery` / `Clock` 等是各 App 自己提供的辅助功能标签，Thaw 只负责显示，补丁改不了。

## 文件

- `translations.json` — 68 条英文→中文映射
- `patch_thaw.py` — 补丁脚本（含还原）
- `README.md` — 本说明

## 许可

翻译和脚本按 MIT 许可。Thaw 本体版权归原作者，GPL-3.0，见 https://github.com/thaw-app/Thaw 。
