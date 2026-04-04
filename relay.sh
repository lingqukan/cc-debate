#!/usr/bin/env bash
# relay.sh — Claude Code Stop hook
# 从 payload 的 cwd 字段判断正方/反方，注入对方 tmux pane，并追加到 markdown 记录

set -euo pipefail

SESSION="cc_debate"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${SCRIPT_DIR}/state"
STATE="${STATE_DIR}/state.json"
LOG="${STATE_DIR}/relay.log"

# pane 布局：0.0=日志, 0.1=正方, 0.2=反方
PANE_PRO="${SESSION}:0.1"
PANE_CON="${SESSION}:0.2"

log() {
    echo "[$(date '+%H:%M:%S')] $1" >> "$LOG"
}

# ── 工具函数：原子更新 state.json ─────────────────────────────────────────────

update_state() {
    local filter="$1"
    jq "$filter" "$STATE" > "${STATE}.tmp" && mv "${STATE}.tmp" "$STATE"
}

# ── 读取 hook payload（stdin） ────────────────────────────────────────────────

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

case "$CWD" in
    */state/pro) SIDE="pro" ;;
    */state/con) SIDE="con" ;;
    *)           exit 0 ;;
esac

# ── 基础校验 ──────────────────────────────────────────────────────────────────

[[ ! -f "$STATE" ]] && exit 0

DONE=$(jq -r '.done' "$STATE")
[[ "$DONE" == "true" ]] && exit 0

# 路径由 debate_cc.py 写入 state.json
MD_PRO=$(jq -r '.md_pro' "$STATE")
MD_CON=$(jq -r '.md_con' "$STATE")

TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty')

if [[ -z "$TRANSCRIPT_PATH" || ! -f "$TRANSCRIPT_PATH" ]]; then
    log "$SIDE: transcript_path 为空或不存在，跳过"
    exit 0
fi

log "$SIDE: Stop hook 触发，读取 $(basename "$TRANSCRIPT_PATH")"

# ── 提取最后一条 assistant 文本（保留换行，供 markdown 存储） ─────────────────
# thinking 块可能比 text 块先落盘，且 Stop hook 可能在新响应写入前触发，最多重试 5 次

EXPECTED_COUNT=$(jq -r ".${SIDE}_msg_count" "$STATE")
EXTRACT_SCRIPT="${SCRIPT_DIR}/extract_text.py"

LAST_TEXT=""
for attempt in 1 2 3 4 5; do
    LAST_TEXT=$(python3 "$EXTRACT_SCRIPT" "$TRANSCRIPT_PATH" "$EXPECTED_COUNT")
    [[ -n "$LAST_TEXT" ]] && break
    log "$SIDE: attempt ${attempt} 未提取到文本，等待重试..."
    sleep 1
done

if [[ -z "$LAST_TEXT" ]]; then
    log "$SIDE: 未提取到文本，跳过"
    exit 0
fi

log "$SIDE: 提取到回答（${#LAST_TEXT} 字符）"

# 一次读取多个状态值，减少 IO
read -r PHASE ROUND MAX SEARCH_MODE <<< "$(jq -r '[.phase, .round, .max_rounds, (.search_mode // false)] | @sh' "$STATE" | tr -d "'")"
TIMESTAMP=$(date '+%H:%M:%S')

# 联网搜索模式：提取【最终论点】之后的内容用于注入；经典模式直接用全文
if [[ "$SEARCH_MODE" == "true" ]]; then
    RELAY_TEXT=$(printf '%s' "$LAST_TEXT" | python3 "$EXTRACT_SCRIPT" --final-argument)
else
    RELAY_TEXT="$LAST_TEXT"
fi

# ── 总结阶段处理 ──────────────────────────────────────────────────────────────

if [[ "$PHASE" == "summary" ]]; then
    if [[ "$SIDE" == "pro" ]]; then
        MD_FILE="$MD_PRO"; LABEL="正方"
    else
        MD_FILE="$MD_CON"; LABEL="反方"
    fi

    {
        echo "## 总结 · ${LABEL} · ${TIMESTAMP}"
        echo ""
        echo "${LAST_TEXT}"
        echo ""
        echo "---"
        echo ""
    } >> "$MD_FILE"

    log "$SIDE: 总结已保存"

    NEW_MSG_COUNT=$(( EXPECTED_COUNT + 1 ))
    update_state ".summary_${SIDE} = true | .${SIDE}_msg_count = ${NEW_MSG_COUNT}"

    SUMMARY_PRO=$(jq -r '.summary_pro' "$STATE")
    SUMMARY_CON=$(jq -r '.summary_con' "$STATE")

    if [[ "$SUMMARY_PRO" == "true" && "$SUMMARY_CON" == "true" ]]; then
        update_state '.done = true'
        log "双方总结完毕__DEBATE_DONE__"
    fi

    exit 0
fi

# ── 追加辩论轮次到 markdown ───────────────────────────────────────────────────

if [[ "$SIDE" == "pro" ]]; then
    MD_FILE="$MD_PRO"; LABEL="正方"
    DISPLAY_ROUND=$(( ROUND + 1 ))
else
    MD_FILE="$MD_CON"; LABEL="反方"
    DISPLAY_ROUND=$(( ROUND + 1 ))
fi

{
    echo "## 第 ${DISPLAY_ROUND}/${MAX} 轮 · ${LABEL} · ${TIMESTAMP}"
    echo ""
    echo "${LAST_TEXT}"
    echo ""
    echo "---"
    echo ""
} >> "$MD_FILE"

log "$SIDE: 已追加到 $(basename "$MD_FILE")"

# 记录已处理消息数，防止下次 Stop hook 重复读取同一条消息
NEW_MSG_COUNT=$(( EXPECTED_COUNT + 1 ))
update_state ".${SIDE}_msg_count = ${NEW_MSG_COUNT}"

# ── 更新轮次（con 说完才算一轮结束） ─────────────────────────────────────────

if [[ "$SIDE" == "con" ]]; then
    NEW_ROUND=$(( ROUND + 1 ))
    update_state ".round = ${NEW_ROUND}"

    log "con: 第 ${NEW_ROUND}/${MAX} 轮结束"

    if (( NEW_ROUND >= MAX )); then
        update_state '.phase = "summary"'
        log "辩论阶段结束，进入总结阶段"

        if [[ "$SEARCH_MODE" == "true" ]]; then
            SUMMARY_PROMPT="辩论已结束，请搜索相关数据，做最终总结陈词：重申核心立场，总结最有力的数据论据，指出对方论点的根本缺陷。最后以【最终论点】输出总结（150-200字）。"
        else
            SUMMARY_PROMPT="辩论已结束，请做最终总结陈词：重申你的核心立场，总结最有力的论据，指出对方论点的根本缺陷。100-150字。"
        fi
        printf '%s' "$SUMMARY_PROMPT" > "${STATE_DIR}/relay_buf.txt"
        tmux load-buffer "${STATE_DIR}/relay_buf.txt"
        tmux paste-buffer -t "$PANE_PRO"
        sleep 0.3
        tmux send-keys -t "$PANE_PRO" Enter
        sleep 0.5
        tmux load-buffer "${STATE_DIR}/relay_buf.txt"
        tmux paste-buffer -t "$PANE_CON"
        sleep 0.3
        tmux send-keys -t "$PANE_CON" Enter

        exit 0
    fi
fi

# ── 注入对方 pane（含对方论点和研究指令） ────────────────────────────────────

if [[ "$SIDE" == "pro" ]]; then
    TARGET="$PANE_CON"
else
    TARGET="$PANE_PRO"
fi

log "$SIDE → $([ "$SIDE" = "pro" ] && echo "反方" || echo "正方") 注入中..."

if [[ "$SEARCH_MODE" == "true" ]]; then
    printf '%s' "【对方论点】
${RELAY_TEXT}

请先用 WebSearch 搜索反驳数据，最后以【最终论点】输出你的结论（150-200字）。" > "${STATE_DIR}/relay_buf.txt"
else
    printf '%s' "${RELAY_TEXT//$'\n'/ }" > "${STATE_DIR}/relay_buf.txt"
fi
tmux load-buffer "${STATE_DIR}/relay_buf.txt"
tmux paste-buffer -t "$TARGET"
sleep 0.3
tmux send-keys -t "$TARGET" Enter

log "$SIDE: 注入完成"
exit 0
