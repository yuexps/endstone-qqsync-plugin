FROM python:3.12-slim

LABEL maintainer="yuexps" description="Endstone + QQSync + Lagrange (All-in-One, Python 3.12)"

# 1. 最小系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget ca-certificates tar curl libicu72 libssl3 zlib1g netcat-traditional \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. 创建统一目录
RUN mkdir -p \
      /app/logs \
      /app/endstone/plugins \
      /app/lagrange/data \
 && chmod -R 777 /app

# 3. Python 层
RUN pip install --no-cache-dir "endstone[bedrock]" endstone-qqsync-plugin

# 4. Lagrange 自包含包
RUN wget -q -O- https://github.com/LagrangeDev/Lagrange.Core/releases/download/nightly/Lagrange.OneBot_linux-x64_net9.0_SelfContained.tar.gz \
 | tar -zxf - -C /app/lagrange \
 && chmod +x /app/lagrange/Lagrange.OneBot

# 5. 配置文件（内嵌）
RUN echo '{
  "$schema": "https://raw.githubusercontent.com/LagrangeDev/Lagrange.Core/master/Lagrange.OneBot/Resources/appsettings_schema.json",
  "Logging": { "LogLevel": { "Default": "Information" } },
  "SignServerUrl": "https://sign.lagrangecore.org/api/sign/39038",
  "Account": { "Uin": 0, "Password": "", "Protocol": "Linux", "AutoReconnect": true, "GetOptimumServer": true },
  "Message": { "IgnoreSelf": true, "StringPost": true },
  "QrCode": { "ConsoleCompatibilityMode": true },
  "DataDirectory": "/app/lagrange/data",
  "Implementations": [
    { "Type": "ForwardWebSocket", "Host": "127.0.0.1", "Port": 3001, "HeartBeatInterval": 5000, "HeartBeatEnable": true, "AccessToken": "" }
  ]
}' > /app/lagrange/appsettings.json

# 6. 启动脚本（信号转发 + 日志）
RUN echo '#!/bin/bash
set -euo pipefail
log() { echo "[$(date +%F\ %T)] $*"; }

log "Starting Lagrange.OneBot..."
cd /app/lagrange
./Lagrange.OneBot > /app/logs/lagrange.log 2>&1 &
while ! nc -z 127.0.0.1 3001; do sleep 1; done
log "Lagrange ready"

log "Starting Endstone..."
cd /app/endstone
exec endstone > /app/logs/endstone.log 2>&1
' > /app/start.sh && chmod +x /app/start.sh

# 7. 健康检查 & 端口
HEALTHCHECK --interval=30s --timeout=2s --start-period=30s --retries=3 \
  CMD nc -z -u 127.0.0.1 19132 || exit 1
EXPOSE 19132/udp

CMD ["/app/start.sh"]
