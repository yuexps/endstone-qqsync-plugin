FROM python:3.12-slim

LABEL maintainer="yuexps" description="Endstone + QQSync + Lagrange (All-in-One, Python 3.12)"

# 1. 最小系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget ca-certificates tar curl libssl3 zlib1g netcat-traditional \
 && apt-get clean && rm -rf /var/lib/apt/lists/*
 
WORKDIR /app

# 2. 创建统一目录
RUN mkdir -p \
      /app/logs \
      /app/endstone \
      /app/lagrange \
 && chmod -R 777 /app

# 3. Python 层
RUN pip install --no-cache-dir "endstone" endstone-qqsync-plugin

# 4. Lagrange 自包含包
RUN set -ex && \
    mkdir -p /tmp/lag /app/lagrange && \
    wget -qO- https://github.com/LagrangeDev/Lagrange.Core/releases/download/nightly/Lagrange.OneBot_linux-x64_net9.0_SelfContained.tar.gz \
    | tar -zxf - -C /tmp/lag && \
    mv /tmp/lag/*/bin/Release/net9.0/linux-x64/publish/* /app/lagrange/ && \
    rm -rf /tmp/lag && \
    chmod +x /app/lagrange/Lagrange.OneBot

# 5. 配置文件
RUN cat > /app/lagrange/appsettings.json <<'EOF'
{
    "$schema": "https://raw.githubusercontent.com/LagrangeDev/Lagrange.Core/master/Lagrange.OneBot/Resources/appsettings_schema.json",
    "Logging": {
        "LogLevel": {
            "Default": "Information"
        }
    },
    "SignServerUrl": "https://sign.lagrangecore.org/api/sign/39038",
    "SignProxyUrl": "",
    "MusicSignServerUrl": "",
    "Account": {
        "Uin": 0,
        "Password": "",
        "Protocol": "Linux",
        "AutoReconnect": true,
        "GetOptimumServer": true
    },
    "Message": {
        "IgnoreSelf": true,
        "StringPost": false
    },
    "QrCode": {
        "ConsoleCompatibilityMode": false
    },
    "Implementations": [
        {
            "Type": "ForwardWebSocket",
            "Host": "127.0.0.1",
            "Port": 3001,
            "HeartBeatInterval": 5000,
            "HeartBeatEnable": true,
            "AccessToken": ""
        }
    ]
}
EOF

# 6. 预留模板目录（供 start.sh 首次填充）
RUN mkdir -p /app/lagrange.image && \
    cp -a /app/lagrange/* /app/lagrange.image/

# 7. 启动脚本（信号转发 + 日志）
RUN cat > /app/start.sh <<'EOF'
#!/bin/bash
set -euo pipefail
log() { echo "[$(date +%F\ %T)] $*"; }

log "Starting Lagrange.OneBot..."
cd /app/lagrange
./Lagrange.OneBot > /app/logs/lagrange.log 2>&1 &
while ! nc -z 127.0.0.1 3001; do sleep 1; done
log "Lagrange ready"

log "Starting Endstone..."
cd /app/endstone
# 使用镜像拉取 bedrock-server 元数据
exec endstone -r https://ghfast.top/https://raw.githubusercontent.com/EndstoneMC/bedrock-server-data/v2 > /app/logs/endstone.log 2>&1
EOF
RUN chmod +x /app/start.sh

# 8. 构建验收
RUN set -ex && \
    echo "=== Check Lagrange ===" && \
    ls -l /app/lagrange && \
    test -x /app/lagrange/Lagrange.OneBot && \
    test -f /app/lagrange/appsettings.json && \
    echo "=== Check Endstone ===" && \
    python -c "import endstone,sys,os; print('endstone ver.', endstone.__version__)" && \
    echo "=== Check start.sh ===" && \
    test -x /app/start.sh && \
    echo "=== All OK ==="

# 9. 健康检查 & 端口
HEALTHCHECK --interval=30s --timeout=2s --start-period=30s --retries=3 \
  CMD nc -z -u 127.0.0.1 19132 || exit 1
EXPOSE 19132/udp

CMD ["/app/start.sh"]
