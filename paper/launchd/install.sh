#!/bin/zsh
# 安裝定時更新（每 4 小時）與網站伺服器（登入後常駐 http://127.0.0.1:8787）
# 排程檔是模板，安裝時把 __ROOT__ 換成這個專案的實際位置
set -e
DIR=${0:A:h}; ROOT=${DIR:h:h}; LA=~/Library/LaunchAgents
mkdir -p $LA $ROOT/paper/state
for name in engine server; do
  label=com.signaltower.$name
  launchctl bootout gui/$(id -u)/$label 2>/dev/null || true
  sed "s#__ROOT__#$ROOT#g" $DIR/$label.plist.template > $LA/$label.plist
  launchctl bootstrap gui/$(id -u) $LA/$label.plist
done
echo "已安裝。網站：http://127.0.0.1:8787　紀錄：paper/state/engine.log、server.log"
