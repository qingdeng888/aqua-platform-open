#!/bin/bash
# ============================================================
# AQUA 服务自动恢复脚本
# 检测 Gateway(8000) 和 Platform(8001) 健康状态
# 服务宕机时自动重启
# ============================================================
set -euo pipefail

LOG_FILE="/var/log/acu-auto-recovery.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

check_and_restart() {
    local name="$1"
    local port="$2"
    local health_url="$3"
    local work_dir="$4"
    local cmd="$5"

    # 健康检查
    if curl -sf "$health_url" > /dev/null 2>&1; then
        return 0
    fi

    log "⚠ ${name}(${port}) 健康检查失败，尝试重启..."

    # 尝试正常停止旧进程
    local old_pid
    old_pid=$(lsof -ti:"${port}" 2>/dev/null || true)
    if [ -n "$old_pid" ]; then
        kill "$old_pid" 2>/dev/null || true
        sleep 3
        # 强制终止
        if kill -0 "$old_pid" 2>/dev/null; then
            kill -9 "$old_pid" 2>/dev/null || true
            sleep 1
        fi
    fi

    # 启动新进程
    cd "$work_dir" || return 1
    nohup $cmd > /dev/null 2>&1 &
    local new_pid=$!
    log "✓ ${name} 已重启 (PID=${new_pid})"

    # 等待并验证
    sleep 5
    if curl -sf "$health_url" > /dev/null 2>&1; then
        log "✓ ${name} 恢复成功"
        return 0
    else
        log "✗ ${name} 恢复失败，可能需要手动介入"
        return 1
    fi
}

# ========== 检查 Gateway ==========
check_and_restart \
    "Gateway" \
    8000 \
    "http://127.0.0.1:8000/healthz" \
    "./gateway" \
    "./venv314/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level warning"

# ========== 检查 Platform ==========
check_and_restart \
    "Platform" \
    8001 \
    "http://127.0.0.1:8001/healthz" \
    "./platform" \
    "./venv314/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --log-level warning"

# ========== 检查 Nginx ==========
if ! pgrep -x nginx > /dev/null; then
    log "⚠ Nginx 未运行，尝试启动..."
    nginx -t && systemctl start nginx && log "✓ Nginx 已启动" || log "✗ Nginx 启动失败"
fi

# 日志清理（保留7天）
find /var/log/acu-*.log -mtime +7 -delete 2>/dev/null || true
