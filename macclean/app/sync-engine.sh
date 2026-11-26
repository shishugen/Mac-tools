#!/bin/sh
# 把 CLI 引擎同步到 Tauri 应用内，避免两份代码漂移。
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cp "$HERE/../macclean.py" "$HERE/src-tauri/engine/macclean.py"
cp "$HERE/../rules.json" "$HERE/src-tauri/engine/rules.json"
echo "engine 已同步：macclean.py / rules.json"
