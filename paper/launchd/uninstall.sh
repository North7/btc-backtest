#!/bin/zsh
# 移除定時更新與網站伺服器
for name in engine server; do
  launchctl bootout gui/$(id -u)/com.signaltower.$name 2>/dev/null || true
  rm -f ~/Library/LaunchAgents/com.signaltower.$name.plist
done
echo "已移除。"
