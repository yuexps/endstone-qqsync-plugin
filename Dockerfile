FROM python:3.12-slim

LABEL maintainer="yuexps" description="Endstone + QQSync (Python 3.12)"

# 1. 最小系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget ca-certificates tar curl libssl3 libicu76 zlib1g netcat-traditional \
 && apt-get clean && rm -rf /var/lib/apt/lists/*
 
WORKDIR /app

# 2. 创建统一目录
RUN mkdir -p \
      /app/logs \
      /app/endstone \
 && chmod -R 777 /app

# 3. Python 层
RUN pip install --no-cache-dir "endstone" endstone-qqsync-plugin


# 4. 启动脚本
RUN cat > /app/start.sh <<'EOF'
#!/bin/bash
set -euo pipefail
log() { echo "[$(date +%F\ %T)] $*"; }

log "Starting Endstone..."
cd /app/endstone
# 使用镜像拉取 bedrock-server 元数据，同时输出日志到文件和控制台
exec endstone -r https://ghfast.top/https://raw.githubusercontent.com/EndstoneMC/bedrock-server-data/v2 -y 2>&1 | tee /app/logs/endstone.log
EOF
RUN chmod +x /app/start.sh

# 5. 构建验收
RUN set -ex && \
    echo "=== Check Endstone ===" && \
    python -c "import endstone,sys,os; print('endstone ver.', endstone.__version__)" && \
    echo "=== Check start.sh ===" && \
    test -x /app/start.sh && \
    echo "=== All OK ==="

# 6. 健康检查 & 端口
HEALTHCHECK --interval=30s --timeout=2s --start-period=30s --retries=3 \
  CMD nc -z -u 127.0.0.1 19132 || exit 1
EXPOSE 19132/udp

CMD ["/app/start.sh"]
