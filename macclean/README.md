# macclean

macOS 清理与卸载原型（CLI）。只读扫描 + 白名单分级 + 移入废纸篓，不做静默删除。

界面设计稿在 `ui-mockup.html`，可直接点：左侧切页、勾选实时算总量、点行看详情、点「清理…」出确认弹窗。

## 桌面版（Tauri）

`app/` 是可运行的应用壳：前端复用设计稿，后端调 Python 引擎，不重写清理逻辑。

```sh
cd app
cargo run --manifest-path src-tauri/Cargo.toml   # 编译并启动
# 或者装好依赖后用 CLI
npm install && npm run dev
```

首次编译要拉 Tauri 依赖，约 5 分钟；之后增量编译几秒。已在本机实测：`cargo build` 通过，生成的 `target/debug/macclean-app` 能正常起窗口。

结构：

| 路径 | 作用 |
|---|---|
| `app/src/index.html`、`app/src/app.js` | 界面，无构建步骤，Tauri 直接加载 |
| `app/src-tauri/src/main.rs` | 注册 scan / clean_plan / clean_run / apps / uninstall_plan / uninstall_run / doctor / disk_info / reveal |
| `app/src-tauri/engine/api.py` | JSON 桥接层，复用 `macclean.py` 的扫描、安全闸与废纸篓逻辑 |
| `app/sync-engine.sh` | 把根目录的 `macclean.py`、`rules.json` 同步进引擎目录 |

改完 CLI 记得跑一次 `./sync-engine.sh`，否则应用里还是旧引擎。

**应用排序**：应用页支持按 名称 / 大小 / 最近使用 / 安装时间 排序，点同一项切换升降序。

- 大小：`du -sk` 先解析符号链接再算，8 线程并行，62 个应用约 2.6 秒。
- 上次访问（系统口径）：读应用包的**修改时间** `os.stat(path).st_mtime`。已逐项对照 macOS「系统设置 → 通用 → 储存空间 → 应用程序」：微信 2023/8/8 20:16、DingTalk 2024/6/8 16:01、WPS 2024/4/10 23:50、Quark 2026/6/10 23:00、ChatGPT 2026/11/14 15:02 全部一致。LaunchServices 在启动/更新应用时改这个时间。
- 最近使用：本地记录 + Spotlight `kMDItemLastUsedDate`。Spotlight 对第三方应用覆盖率极低（本机 1/62），所以主要靠本地记录。它可能比「上次访问」更新，界面会显示「今天用过 · 访问 2023-08-08」。
- 安装时间：读应用包的创建时间（`stat -f %B`），始终可用，适合找「装了很久没动过」的应用。

**大小口径**：应用页显示主程序 + 关联数据合计；系统「储存空间 → 应用程序」口径相近。例如微信主程序 355 MB + 沙盒容器 14 GB = 14.2 GB，接近系统显示的 15.27 GB。

**扫描交互**：不做独立页面，放在「清理」页的三态里——空态（还没有结果）→ 扫描中（进度条、当前阶段、已用时、取消）→ 结果态（可筛选、可重新扫描）。概览页和清理页共用同一个扫描动作，切走页面时扫描继续，侧边栏「清理」旁显示进度点。

**使用记录（可选，默认不开启）**：设置页有开关，开启后后台每 30 秒采样一次正在运行的 App，补不回历史，只记录开启之后的使用。

| 项 | 值 |
|---|---|
| 后台方式 | LaunchAgent `com.macclean.usage`，`RunAtLoad` + 失败重启 |
| 数据 | `~/Library/Application Support/macclean/usage.json`，仅本机 |
| 采样间隔 | 30 秒，一次约 20ms |
| 关闭 | 设置页点「关闭」，停进程、删 plist，历史数据保留 |

命令行等价操作：`python3 src-tauri/engine/usage_tracker.py install | uninstall | status | report`

注意：本机沙箱里 `launchctl bootstrap` 会报 `I/O error 5`，代码会兜底用当前会话直接拉起进程，并在下次登录时交给 LaunchAgent；正常桌面环境不需要这个兜底。

**图标与名称**：应用图标从包内 `AppIcon.icns` 用 `sips` 转成 64px PNG，缓存在 `~/Library/Application Support/macclean/icons/`，以 data URI 交给前端；没有 icns 的退回 Quick Look（`qlmanage`），再失败才用首字母占位。名称优先读 `zh-Hans.lproj/InfoPlist.strings`，所以显示「微信」「夸克」而不是 WeChat / Quark。本机 62 个应用实测 49 个拿到真实图标。

**总占用与可删标注**：应用页「大小」现在显示主程序 + 缓存 + 数据 + 疑似共享目录（不含系统级），点进卸载详情有构成条和每组徽标：

| 徽标 | 对应 | 默认 | 说明 |
|---|---|---|---|
| 卸载时删 | 主程序 | 必选 | 可从 App Store / 官网重装 |
| 可删 | 缓存 | 勾选 | 自动重建，不含账号和文档 |
| 谨慎 | 应用数据 | 不勾 | 含登录态、本地文档与设置，删了不可恢复 |
| 需判断 | 疑似共享目录 | 禁用 | 证据不足，只展示 |
| 禁自动删 | 系统级组件 | 禁用 | 只生成脚本，需管理员确认 |

**应用内展开容器**：卸载详情每条关联路径右侧有「展开」按钮，点进去是「容器内容」页——逐层列出子目录/文件、大小、文件数、用途、能不能删、删了会怎样；还带文件类型分布。可以点「展开」继续下钻。

容器里的**每一项都有勾选框**，等级只是提示（可清 / 谨慎 / 重要数据 / 需判断），删不删由用户决定。点「删除所选子项…」走 `container-delete`：服务端重新生成提示并随结果返回警告，只硬性拒绝用户目录之外的路径；前端传来的标记不被信任。

卸载详情底部有**两个按钮**：

- **卸载…**：主程序 + 勾选的关联文件，一起移入废纸篓。
- **删除所选…**：只删勾选的关联文件，主程序保留。适合只想清缓存/数据但继续用这个 App。

「删除所选」走 `delete-paths` 命令：后端重新算一遍该应用的关联清单，只接受清单内的路径，再过 `assert_safe()`，系统级路径一律拒绝，不会误删主程序或别的应用。

卸载详情顶部另有「可以删除 / 不要随便删」两栏摘要，每条路径下面用大白话写用途：

| 目录 | 用途 |
|---|---|
| `Library/Caches/…` | 临时缓存，应用自动重建 |
| `Library/Containers/…` | 沙盒容器：账号、聊天记录、本地文件 |
| `Library/Application Support/…` | 应用配置和本地数据，删了要重新登录和设置 |
| `Library/Preferences/…` | 偏好设置，删了恢复默认 |
| `Library/HTTPStorages/…`、`WebKit/…`、`Cookies/…` | 网站数据与登录态 |
| `Library/LaunchAgents/…`、`LaunchDaemons/…` | 开机自启项 |
| `Library/Logs/…` | 排障日志 |
| `/Library/…`、`PrivilegedHelperTools/…` | 系统级服务和配置，只生成脚本 |

全量枚举 62 个应用约 10 秒，列表期间显示「正在读取…」。枚举时顺手修了两处：`Bundle ID` 第一段不是 10 位 Team ID 时不再把别人的 Group Containers 算进来；Adobe Photoshop 这类装在无 `.app` 后缀的嵌套目录里的应用也能识别。

**已实现**：真实扫描、清理计划与执行、应用列表、卸载计划与执行、磁盘占用、完全磁盘访问状态、在访达中显示。

**未实现**：扫描进度事件（当前是不确定进度 + 已用时）、真正的取消（按钮已改成「停止等待结果」，后台会跑完）、排除列表、清理记录持久化（概览里的历史仍是示例数据）。

**应用页性能**：首次进应用页要遍历 62 个应用的主程序 + 190 个关联目录（约 11 秒），结果缓存到 `~/Library/Application Support/macclean/apps-cache.json`（30 分钟有效）。缓存期内进列表 0.13 秒、点进详情 0.47 秒。点进详情时前端先用列表已算好的体积和关联文件直接渲染（实测 2ms），只在后台补「是否正在运行」，不再等一遍遍历。清理/卸载/删除子项后自动失效缓存；体积变了可点列表右上角「刷新体积」强制重算。

**扫描动画**：Tauri 的同步命令跑在主线程，扫盘十几秒会把界面冻住，看起来像卡死。现在 scan / clean_plan / clean_run / apps / uninstall_plan / uninstall_run / doctor / usage 全部改成 async + `tauri::async_runtime::spawn_blocking`，Python 在后台线程跑，转圈、进度条和计时器都正常动。

## 界面（设计稿）

| 页面 | 内容 |
|---|---|
| 概览 | 4 个指标卡、磁盘占用分段条、占用最大的项、最近清理 |
| 清理 | 级别筛选 + 搜索、分组列表（S/A/B）、右侧详情面板、底部合计与主按钮 |
| 应用 | 应用列表，标注残留项数量 |
| 卸载 | 主程序 / 缓存 / 应用数据 / 系统级 四组，分别勾选 |
| 规则 | 19 条白名单 + 红线，开关只对 S/A/B 生效 |
| 清理记录 | 每次清理的条目、释放量、撤销入口 |
| 设置 | 完全磁盘访问状态、确认方式、废纸篓策略、管理员脚本目录 |

界面约束与 CLI 一致：默认只勾 S 级；A 级不预选；B 级没有勾选框，只有「生成脚本」；C 级开关禁用。

## 快速开始

```sh
cd macclean

# 1. 看这台机器能清出多少（不写任何文件）
python3 macclean.py scan

# 2. 生成可读报告
python3 macclean.py scan --json reports/scan.json
python3 macclean.py report reports/scan.json

# 3. 演练清理（默认只打印计划）
python3 macclean.py clean

# 4. 确认无误后执行
python3 macclean.py clean --yes
```

安装/卸载相关：

```sh
python3 macclean.py apps                      # 列出应用
python3 macclean.py apps --sizes --filter 微信  # 带体积
python3 macclean.py uninstall 微信             # 只演练，显示关联文件
python3 macclean.py uninstall 微信 --with-data --yes
```

## 命令一览

| 命令 | 作用 | 默认是否写入 |
|---|---|---|
| `scan` | 只读扫描，列出可清理项与红线目录 | 否 |
| `clean` | 按级别清理，需 `--yes` 才真正执行 | 否 |
| `apps` | 列出 /Applications 与 ~/Applications 下的应用 | 否 |
| `uninstall` | 卸载主程序 + 关联文件，需 `--yes` | 否 |
| `report` | 把 scan 的 JSON 转成 HTML | 写报告文件 |
| `doctor` | 检查权限（完全磁盘访问）、规则状态 | 否 |
| `rules` | 打印规则清单 | 否 |

常用参数：`--home 目录`（测试用，扫描别处）、`--top N`、`--json 路径`、`--min-size 字节`、`--rule 规则id`。

## 安全设计

1. **白名单之外不清理**。规则写在 `rules.json`，没有匹配到规则的文件永远不会进入清理计划。
2. **红线目录**（钥匙串、Preferences、Application Support 根、邮件、信息、个人文档、`~/.ssh`、`/System`、`/Library` 等）只展示，执行前还有一道 `assert_safe()` 复核。
3. **只有一条写路径**：移入废纸篓。先调用 Finder（支持「放回原处」），失败则直接移动进 `~/.Trash`（名称带时间戳，不会覆盖已有文件）。全程没有 `rm -rf`。
4. **默认不执行**。`clean` / `uninstall` 不加 `--yes` 只打印计划。
5. **符号链接一律跳过**，不会顺着链接跑到别处。

## 分级

| 级别 | 含义 | 默认 |
|---|---|---|
| S | 可自动重建（缓存、日志、编译产物） | 允许清理 |
| A | 含用户数据（设备备份、模拟器、废纸篓） | 需 `--include-a` / `--include-trash` |
| B | 系统级，需管理员（`/Library/Caches`、`/Library/Logs`） | 需 `--allow-admin`，只生成脚本 |
| C | 红线，只展示 | 永不执行 |

## 卸载逻辑

关联文件按证据强度分组展示：

| 组 | 依据 | 什么时候处理 |
|---|---|---|
| 主程序 | `.app` 路径 | 总是 |
| 缓存 | Bundle ID 精确命中 `~/Library/Caches/<id>` | 默认 |
| 数据 | 沙盒容器、`Application Support/<名称>`、偏好设置、启动项、日志 | 加 `--with-data` |
| 疑似 | 同开发者的 Group Containers、名称模糊匹配 | 加 `--include-probable` |
| 系统级 | `/Library/Application Support`、`LaunchDaemons`、`PrivilegedHelperTools` | 加 `--allow-admin`，只生成脚本 |

保护规则：`com.apple.*` 一律拒绝卸载；应用在运行时拒绝执行（`--force` 可跳过检查）；pkg 安装收据默认只列出，`--forget-receipts` 才会清除。

## 完全磁盘访问

`scan` 大部分缓存不需要额外权限，但邮件、信息、Safari 等目录会被系统拒绝（`doctor` 会标 ✗）。

开启方式：系统设置 → 隐私与安全性 → 完全磁盘访问 → 加入你运行命令的终端 App（或 Python）→ 重开终端。

## 系统级（B 级）处理

B 级不会自动执行。加 `--allow-admin` 后会生成 `macclean-admin-cleanup.sh`：

```sh
python3 macclean.py clean --include-a --allow-admin --yes
# 打开生成的脚本，逐条核对
open -e macclean-admin-cleanup.sh
```

脚本里每条都是 `sudo mv` 到废纸篓并修正归属，不会直接删除。跑不跑由你决定。

## 已验证 / 未验证

已实测（在 `/tmp` 假目录里跑，未动真实文件）：

- 扫描只列出白名单项：CloudKit 排除生效、1 天内的新日志不列、`config.db` 不列、`Documents` / `Preferences` / `.ssh` 不出现。
- 清理 5 项 S 级后，受保护文件全部保留。
- 卸载假 App：主程序 + 缓存 + 5 个数据目录全部进废纸篓，只剩红线目录里的文件。
- 安全闸 9 个用例全部通过：主目录、钥匙串、Documents、根目录、Applications、符号链接被拒；正常缓存放行；`/Library/Caches` 未授权拒绝、授权后放行。
- 真实只读扫描：本机 344 项 / 17.2 GB，红线泄漏 0。

未验证：

- 在真实用户终端里通过 Finder 移入废纸篓（当前执行环境被沙箱挡住，报 `权限违例`）。首次运行会弹「终端想要控制 Finder」，允许即可；拒绝则自动走直接移动的兜底路径。
- B 级 `/Library/Caches` 的实际体积（需要 sudo 才能读全，未授权时显示 0 B）。

## 已知限制

- 名称匹配无法覆盖所有残留。用 Installer 安装的 App 会有收据，但文件可能散落在 `/Library` 各处，这部分只列出、不自动删。
- Electron 类应用的 `Application Support/<App>` 往往就是它的全部数据，`--with-data` 前请确认没有未同步的本地文件。
- 废纸篓里的项目如果之后被清空，就无法恢复。重要数据先另做备份。

## 下一步

1. 在真实终端跑一遍 `scan` + `clean`（S 级），确认 Finder 路径可用。
2. 把规则改成可热更新（远程/本地覆盖），不必改代码。
3. 需要界面时再包一层 SwiftUI，规则引擎和 `assert_safe` 直接复用。

## 容器内部展开

`container_report.py` 只读展开某个应用容器，按目录和文件类型告诉你每一项是干什么的、能不能删、删了会怎样：

```sh
python3 container_report.py ~/Library/Containers/com.tencent.xinWeChat docs/wechat-container.html
```

只统计不改动文件。微信的实测报告在 `docs/wechat-container.html`，结论：14.7 GB 里 11 GB 是聊天附件缓存，5.4 GB 是 171 个收发过的 zip（谨慎对待），聊天数据库只有几百 MB 但不能删。

## 清理页全选与展开

- 工具栏有「全选 / 取消全选」，只作用于当前筛选下可选的项（需管理员的项不会被选中）。
- 点任意行打开右侧详情，详情里新增「展开查看」：进入容器内容页，逐层看目录里有什么、大小、用途、能不能删，可勾选子项后「删除所选子项」。
- 从清理页展开时，容器页返回按钮变成「返回清理列表」；从应用详情展开则仍返回应用详情。
