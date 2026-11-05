#!/bin/bash
# workbuddy2api 健康自愈（systemd 版）
#
# 为什么不是 scripts/watchdog.sh：
#   那个脚本是给「main.py 自己拉起 uvicorn」的宝塔部署写的，会 runuser 启
#   一个 main.py。线上现在是 systemd 管 workbuddy2api.service（Restart=always），
#   两者同时拉起会抢 8790 端口、留双实例（2026-11 就清过两个这样的残留）。
#   这里只做一件事：连续不健康就 `systemctl restart`，把进程管理交回 systemd。
#
# 判定：GET /api/app/version 必须 200。单次失败不重启（避免长请求误杀），
#       连续 3 次（约 3 分钟）才重启。

STATE=/var/tmp/wb2api-health.fail
URL=http://127.0.0.1:8790/api/app/version

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 12 "$URL" 2>/dev/null || echo 000)
if [ "$code" = "200" ]; then
  [ -f "$STATE" ] && rm -f "$STATE"
  exit 0
fi

n=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STATE"
logger -t wb2api-health "健康检查失败 code=$code 连续=$n"
if [ "$n" -ge 3 ]; then
  logger -t wb2api-health "连续 $n 次不健康，重启 workbuddy2api.service"
  systemctl restart workbuddy2api.service
  rm -f "$STATE"
fi
exit 0
