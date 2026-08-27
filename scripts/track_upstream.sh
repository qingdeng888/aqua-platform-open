#!/bin/bash
# ============================================================
# 开源项目上游跟踪脚本
# 用于定期检查开源项目更新，对比版本差异，生成报告
# ============================================================
set -euo pipefail

# ========== 配置 ==========
PROJECTS_DIR="/tmp/open-source-analysis"
REPORT_DIR="./upstream-reports"
LOG_FILE="/var/log/acu-upstream-tracker.log"
GIT_REMOTE_CMD="git remote update --prune 2>&1"
COMPARE_CMD="git log --oneline HEAD..origin/main 2>&1"
STATS_CMD="git rev-list --count HEAD..origin/main 2>&1"

# 项目配置列表
# 格式：项目名|GitHub地址|仓库本地路径|分支名
PROJECTS=(
    "new-api|https://github.com/Calcium-Ion/new-api.git|${PROJECTS_DIR}/new-api|main"
    "sub2api|https://github.com/Wei-Shaw/sub2api.git|${PROJECTS_DIR}/sub2api|main"
    "litellm|https://github.com/BerriAI/litellm.git|${PROJECTS_DIR}/litellm|main"
    "portkey|https://github.com/Portkey-AI/gateway.git|${PROJECTS_DIR}/portkey|main"
    "metapi|https://github.com/cita-777/metapi.git|${PROJECTS_DIR}/metapi|main"
)

mkdir -p "$REPORT_DIR"

log() {
    local level="$1"
    local msg="$2"
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${ts}] [${level}] ${msg}" >> "$LOG_FILE"
    if [ "$level" != "DEBUG" ]; then
        echo "[${ts}] [${level}] ${msg}"
    fi
}

# ========== 步骤 1: 获取上游更新 ==========
fetch_updates() {
    log "INFO" "===== 开始同步上游代码 ====="

    for project_config in "${PROJECTS[@]}"; do
        IFS='|' read -r name repo_url local_path branch <<< "$project_config"

        log "INFO" "检查项目: ${name} (${repo_url})"

        # 如果本地仓库不存在，先克隆
        if [ ! -d "${local_path}/.git" ]; then
            log "INFO" "项目 ${name} 本地不存在，正在克隆..."
            mkdir -p "$(dirname "$local_path")"
            if git clone --branch "$branch" "$repo_url" "$local_path" >> "$LOG_FILE" 2>&1; then
                log "INFO" "✓ ${name} 克隆成功"
            else
                log "ERROR" "✗ ${name} 克隆失败"
            fi
            continue
        fi

        # 获取远程更新
        pushd "$local_path" > /dev/null || continue
        if git remote update --prune >> "$LOG_FILE" 2>&1; then
            log "INFO" "✓ ${name} 远程更新获取成功"
        else
            log "WARN" "⚠ ${name} 远程更新获取失败"
            popd > /dev/null || true
            continue
        fi

        popd > /dev/null || true
    done

    log "INFO" "===== 同步完成 ====="
}

# ========== 步骤 2: 对比版本差异 ==========
check_diffs() {
    log "INFO" "===== 开始版本差异分析 ====="

    local report_file="${REPORT_DIR}/diff-report-$(date '+%Y%m%d').md"
    cat > "$report_file" << 'HEADER'
# 开源项目差异报告

> 生成时间：$(date '+%Y-%m-%d %H:%M:%S')

| 项目 | 状态 | 待拉取提交数 | 关键变更 |
|------|------|------------|---------|
HEADER

    for project_config in "${PROJECTS[@]}"; do
        IFS='|' read -r name repo_url local_path branch <<< "$project_config"

        if [ ! -d "${local_path}/.git" ]; then
            echo "| ${name} | ❌ 未克隆 | - | 本地仓库不存在 |" >> "$report_file"
            continue
        fi

        pushd "$local_path" > /dev/null || continue

        local ahead_count
        ahead_count=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "0")
        local local_commit
        local_commit=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
        local remote_commit
        remote_commit=$(git rev-parse --short origin/main 2>/dev/null || echo "unknown")

        if [ "$ahead_count" -gt 0 ]; then
            log "INFO" "${name}: 落后上游 ${ahead_count} 个提交"

            # 获取详细的变更日志
            local commit_log
            commit_log=$(git log --oneline --no-decorate HEAD..origin/main 2>/dev/null | head -20)

            # 分析关键变更类型
            local features
            features=$(echo "$commit_log" | grep -ciE "feat|feature|add|new|support" || echo "0")
            local fixes
            fixes=$(echo "$commit_log" | grep -ciE "fix|bug|patch|hotfix" || echo "0")
            local refactors
            refactors=$(echo "$commit_log" | grep -ciE "refactor|improve|optimize|perf" || echo "0")
            local security
            security=$(echo "$commit_log" | grep -ciE "security|vuln|CVE|auth" || echo "0")

            local summary="feat:${features} fix:${fixes} refactor:${refactors} security:${security}"
            echo "| ${name} | 🔄 ${ahead_count}个待更新 | ${ahead_count} | ${summary} |" >> "$report_file"

            # 生成详细报告
            local detail_file="${REPORT_DIR}/${name}-commits-$(date '+%Y%m%d').md"
            {
                echo "# ${name} 版本差异详情"
                echo ""
                echo "## 概览"
                echo "- 本地提交: \`${local_commit}\`"
                echo "- 最新提交: \`${remote_commit}\`"
                echo "- 待拉取提交数: ${ahead_count}"
                echo ""
                echo "## 待拉取提交列表"
                echo '```'
                git log --oneline --no-decorate HEAD..origin/main
                echo '```'
                echo ""
                echo "## 详细差异"
                echo '```'
                git log --stat --no-decorate HEAD..origin/main 2>/dev/null | head -100
                echo '```'
                echo ""
                echo "## 变更文件统计"
                echo '```'
                git diff --stat HEAD..origin/main 2>/dev/null
                echo '```'
            } > "$detail_file"
            log "INFO" "  详细报告: ${detail_file}"
        else
            echo "| ${name} | ✅ 已是最新 | 0 | 无变更 (${local_commit}) |" >> "$report_file"
            log "DEBUG" "${name}: 已是最新 (${local_commit})"
        fi

        popd > /dev/null || true
    done

    log "INFO" "差异报告已生成: ${report_file}"
}

# ========== 步骤 3: 拉取最新代码 ==========
pull_latest() {
    log "INFO" "===== 开始拉取最新代码 ====="

    for project_config in "${PROJECTS[@]}"; do
        IFS='|' read -r name repo_url local_path branch <<< "$project_config"

        if [ ! -d "${local_path}/.git" ]; then
            log "WARN" "⚠ ${name} 本地仓库不存在，跳过拉取"
            continue
        fi

        pushd "$local_path" > /dev/null || continue

        # 保存当前 HEAD
        local old_head
        old_head=$(git rev-parse HEAD)

        if git pull origin "$branch" >> "$LOG_FILE" 2>&1; then
            local new_head
            new_head=$(git rev-parse HEAD)

            if [ "$old_head" != "$new_head" ]; then
                log "INFO" "✓ ${name} 已更新: ${old_head:0:8} → ${new_head:0:8}"
                # 记录更新事件
                echo "$(date '+%Y-%m-%d %H:%M:%S') UPDATED ${name} ${old_head:0:8} ${new_head:0:8}" >> "${REPORT_DIR}/update-history.log"
            else
                log "DEBUG" "${name}: 无新变更"
            fi
        else
            log "WARN" "⚠ ${name} 拉取失败 (可能有本地修改)"
        fi

        popd > /dev/null || true
    done

    log "INFO" "===== 拉取完成 ====="
}

# ========== 步骤 4: 生成汇总报告 ==========
generate_summary() {
    local today
    today=$(date '+%Y-%m-%d')
    local summary_file="${REPORT_DIR}/summary-${today}.md"

    {
        echo "# 上游跟踪汇总报告"
        echo ""
        echo "**日期**: ${today}"
        echo ""
        echo "## 项目状态总览"
        echo ""
        echo "| 项目 | 版本 | 提交数 | 最近更新 | 健康状态 |"
        echo "|------|------|--------|---------|---------|"
    } > "$summary_file"

    for project_config in "${PROJECTS[@]}"; do
        IFS='|' read -r name repo_url local_path branch <<< "$project_config"

        if [ ! -d "${local_path}/.git" ]; then
            echo "| ${name} | - | - | - | ❌ 未克隆 |" >> "$summary_file"
            continue
        fi

        pushd "$local_path" > /dev/null || continue

        local total_commits
        total_commits=$(git rev-list --count HEAD 2>/dev/null || echo "0")
        local last_commit_date
        last_commit_date=$(git log -1 --format="%ci" 2>/dev/null || echo "unknown")
        local last_commit_msg
        last_commit_msg=$(git log -1 --format="%s" 2>/dev/null || echo "unknown")
        local behind
        behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "0")

        if [ "$behind" -gt 0 ]; then
            local status="🔄 落后${behind}提交"
        else
            local status="✅ 已是最新"
        fi

        echo "| ${name} | 总${total_commits}提交 | ${total_commits} | ${last_commit_date:0:10} | ${status} |" >> "$summary_file"
        echo "| | | | 最新: ${last_commit_msg:0:50} | |" >> "$summary_file"

        popd > /dev/null || true
    done

    {
        echo ""
        echo "## 影响评估"
        echo ""
        echo "### 对 AQUA 网关的影响"
        echo ""
        echo "请在阅读上述报告后，评估每个项目的更新是否涉及以下方面："
        echo "- 安全漏洞修复"
        echo "- 核心路由算法变更"
        echo "- 新协议/平台支持"
        echo "- API 接口变更"
        echo "- 数据库 schema 变更"
        echo "- 配置文件变更"
        echo ""
        echo "### 需要采取的行动"
        echo ""
        echo "- [ ] 阅读差异报告，评估影响"
        echo "- [ ] 更新优化整改方案"
        echo "- [ ] 更新技术文档"
        echo "- [ ] 如涉及安全修复，尽快合并"
    } >> "$summary_file"

    log "INFO" "汇总报告已生成: ${summary_file}"
}

# ========== 步骤 5: 清理旧报告（保留30天） ==========
cleanup_old_reports() {
    log "INFO" "清理30天前的旧报告..."
    find "${REPORT_DIR}" -name "*.md" -mtime +30 -delete 2>/dev/null || true
    find "${REPORT_DIR}" -name "*.log" -mtime +30 -delete 2>/dev/null || true
    log "INFO" "清理完成"
}

# ========== 主流程 ==========
main() {
    local mode="${1:-all}"

    case "$mode" in
        fetch)
            fetch_updates
            ;;
        diff)
            check_diffs
            ;;
        pull)
            pull_latest
            ;;
        summary)
            generate_summary
            ;;
        all)
            fetch_updates
            check_diffs
            summary
            cleanup_old_reports
            ;;
        full)
            fetch_updates
            check_diffs
            pull_latest
            generate_summary
            cleanup_old_reports
            ;;
        *)
            echo "用法: $0 {fetch|diff|pull|summary|all|full}"
            echo "  fetch   - 仅获取远程更新（不合并）"
            echo "  diff    - 对比版本差异生成报告"
            echo "  pull    - 拉取最新代码到本地"
            echo "  summary - 生成汇总报告"
            echo "  all     - 获取更新+差异报告+汇总（默认）"
            echo "  full    - 完整流程（含拉取代码）"
            exit 1
            ;;
    esac
}

main "$@"
