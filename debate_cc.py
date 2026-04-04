#!/usr/bin/env python3
"""
Claude Code 辩论系统
两个 claude 实例通过 Stop hook 轮流辩论，tmux 左右分屏展示

用法: python3 debate_cc.py <辩题> [轮次=5] [--search]
示例: python3 debate_cc.py "人工智能弊大于利" 5
      python3 debate_cc.py "人工智能弊大于利" 5 --search
"""
import argparse
import subprocess
import sys
import os
import json
import shutil
import time

SESSION   = "cc_debate"
# 运行时由 main() 根据脚本位置设定，这里先占位
STATE_DIR = ""


# ── 系统提示（经典模式） ──────────────────────────────────────────────────────

SYSTEM_PROMPTS_CLASSIC = {
    "pro": """\
你是辩论赛的正方辩手。
你的立场：支持辩题。

规则：
- 每次发言 100-150 字，观点明确、论据充分
- 直接反驳对方的具体论点，并补充新论据
- 不要加任何前缀标签，直接说论点
- 用中文辩论""",

    "con": """\
你是辩论赛的反方辩手。
你的立场：反对辩题。

规则：
- 每次发言 100-150 字，观点明确、论据充分
- 直接反驳对方的具体论点，并补充新论据
- 不要加任何前缀标签，直接说论点
- 用中文辩论""",
}

# ── 系统提示（联网搜索模式） ──────────────────────────────────────────────────

SYSTEM_PROMPTS_SEARCH = {
    "pro": """\
你是辩论赛的正方辩手，立场：支持辩题。

每次发言流程：
1. 用 WebSearch 搜索支持你立场的数据、统计数字、专家观点
2. 如需分析数据可使用 Bash
3. 整合研究结果，以【最终论点】开头，用150-200字输出最终辩论内容

注意：
- 只有【最终论点】之后的内容会传给对方辩手，其余是你的研究过程
- 必须引用具体数字、来源或案例，不得空泛陈述
- 直接反驳对方具体论点
- 用中文辩论""",

    "con": """\
你是辩论赛的反方辩手，立场：反对辩题。

每次发言流程：
1. 用 WebSearch 搜索反驳对方立场的数据、统计数字、专家观点
2. 如需分析数据可使用 Bash
3. 整合研究结果，以【最终论点】开头，用150-200字输出最终辩论内容

注意：
- 只有【最终论点】之后的内容会传给对方辩手，其余是你的研究过程
- 必须引用具体数字、来源或案例，不得空泛陈述
- 直接反驳对方具体论点
- 用中文辩论""",
}


# ── 项目级 Stop hook 管理 ─────────────────────────────────────────────────────

def _install_stop_hook(settings_path: str, relay_script: str) -> str | None:
    """
    把 Stop hook 写入项目级 settings.json（Claude Code 真正加载的那个）。
    relay.sh 从 payload 的 cwd 字段判断正方/反方，无需传参。
    返回原始内容（用于 finally 里恢复）。
    """
    original = None
    existing = {}
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    if os.path.exists(settings_path):
        with open(settings_path) as f:
            original = f.read()
            existing = json.loads(original)

    existing.setdefault("hooks", {})["Stop"] = [
        {
            "matcher": "*",
            "hooks": [{"type": "command", "command": f"bash {relay_script}"}],
        }
    ]
    with open(settings_path, "w") as f:
        json.dump(existing, f, indent=2)

    print(f"✅  Stop hook 已写入 {settings_path}")
    return original


def _restore_stop_hook(settings_path: str, original: str | None) -> None:
    """辩论结束后恢复 settings.json 原始状态"""
    try:
        if original is None:
            # 原来不存在，删除我们写入的 Stop key
            if os.path.exists(settings_path):
                with open(settings_path) as f:
                    data = json.load(f)
                data.pop("Stop", None)
                if data:
                    with open(settings_path, "w") as f:
                        json.dump(data, f, indent=2)
                else:
                    os.remove(settings_path)
        else:
            with open(settings_path, "w") as f:
                f.write(original)
    except Exception as e:
        print(f"⚠️  恢复 settings.json 失败: {e}")


# ── 初始化 ────────────────────────────────────────────────────────────────────

def init_state(topic: str, max_rounds: int, search_mode: bool) -> None:
    import datetime, re
    if os.path.exists(STATE_DIR):
        shutil.rmtree(STATE_DIR)
    os.makedirs(STATE_DIR)

    date_str = datetime.datetime.now().strftime("%Y%m%d")
    safe_topic = re.sub(r'[\\/:*?"<>|]', "_", topic)[:40]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    debate_dir = os.path.join(script_dir, f"{date_str}_{safe_topic}")
    os.makedirs(debate_dir, exist_ok=True)
    md_pro = os.path.join(debate_dir, "正方.md")
    md_con = os.path.join(debate_dir, "反方.md")

    state = {
        "topic": topic,
        "round": 0,
        "max_rounds": max_rounds,
        "search_mode": search_mode,
        "phase": "debate",    # "debate" | "summary"
        "summary_pro": False,
        "summary_con": False,
        "done": False,
        "md_pro": md_pro,
        "md_con": md_con,
        "pro_msg_count": 0,
        "con_msg_count": 0,
    }
    with open(f"{STATE_DIR}/state.json", "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def setup_instance(side: str, topic: str, relay_script: str, search_mode: bool) -> str:
    """
    为每个辩手创建独立工作目录：
      {STATE_DIR}/{side}/
        .claude/settings.json  ← Stop hook（Claude Code 从该目录启动时加载）
        system_prompt.txt      ← 系统提示（供 start.py 读取）
        start.py               ← 启动脚本，避免 shell 引号问题
    """
    inst_dir = f"{STATE_DIR}/{side}"
    os.makedirs(inst_dir, exist_ok=True)

    # 写入实例级 .claude/settings.json，含 Stop hook
    _install_stop_hook(f"{inst_dir}/.claude/settings.json", relay_script)

    # 系统提示文件（含辩题）
    prompts = SYSTEM_PROMPTS_SEARCH if search_mode else SYSTEM_PROMPTS_CLASSIC
    full_prompt = f"辩题：{topic}\n\n" + prompts[side]
    with open(f"{inst_dir}/system_prompt.txt", "w") as f:
        f.write(full_prompt)

    # 启动脚本：用 Python 传参，完全避免 shell 引号转义问题
    claude_flags = ["--dangerously-skip-permissions"] if search_mode else []
    flags_repr = repr(claude_flags)
    start_py = f"""\
#!/usr/bin/env python3
import os

inst_dir = {repr(inst_dir)}
os.chdir(inst_dir)

with open(f"{{inst_dir}}/system_prompt.txt") as fh:
    prompt = fh.read()

extra_flags = {flags_repr}
os.execvp("claude", ["claude"] + extra_flags + ["--system-prompt", prompt])
"""
    start_path = f"{inst_dir}/start.py"
    with open(start_path, "w") as f:
        f.write(start_py)
    os.chmod(start_path, 0o755)

    return inst_dir


# ── tmux 工具 ─────────────────────────────────────────────────────────────────

def tmux(*args) -> None:
    subprocess.run(["tmux", *args], check=True)


def tmux_send(pane: str, text: str, enter: bool = True) -> None:
    cmd = ["tmux", "send-keys", "-t", f"{SESSION}:{pane}", text]
    if enter:
        cmd.append("Enter")
    subprocess.run(cmd, check=True)




def send_initial_prompt_async(pane: str, prompt: str, delay: int = 5) -> None:
    """后台线程：等 claude 启动后发送初始 prompt，不阻塞主进程。
    用固定延迟代替字符检测——'>' 在 shell/history 中也会出现，不可靠。
    """
    import threading

    def _worker():
        time.sleep(delay)
        tmux_send(pane, prompt)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Claude Code 辩论系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  python3 debate_cc.py "人工智能弊大于利" 5          # 经典模式（纯文本）
  python3 debate_cc.py "人工智能弊大于利" 5 --search # 联网搜索模式""",
    )
    parser.add_argument("topic", help="辩题")
    parser.add_argument("rounds", nargs="?", type=int, default=5, help="辩论轮次（默认5）")
    parser.add_argument("--search", action="store_true", help="启用联网搜索模式，辩手可调用 WebSearch/Bash 工具")
    args = parser.parse_args()

    topic       = args.topic
    max_rounds  = args.rounds
    search_mode = args.search

    global STATE_DIR
    script_dir   = os.path.dirname(os.path.abspath(__file__))
    STATE_DIR    = os.path.join(script_dir, "state")
    relay_script = os.path.join(script_dir, "relay.sh")

    if not os.path.exists(relay_script):
        print(f"❌  找不到 relay.sh: {relay_script}")
        sys.exit(1)

    mode_label = "联网搜索模式" if search_mode else "经典模式"
    print(f"\n🎭  辩题：{topic}")
    print(f"📊  轮次：{max_rounds} 轮（正反各一次为一轮）")
    print(f"🔧  模式：{mode_label}")
    print(f"⏳  启动 tmux 分屏，正方就绪后自动发送开场提示...\n")

    # 1. 初始化状态 & 实例目录（Stop hook 写入各自 .claude/settings.json）
    init_state(topic, max_rounds, search_mode)
    setup_instance("pro", topic, relay_script, search_mode)
    setup_instance("con", topic, relay_script, search_mode)

    try:
    #    ┌─────────────────────────────┐
    #    │   0.0  日志 / Log  (上)     │
    #    ├──────────────┬──────────────┤
    #    │ 0.1  正方    │  0.2  反方   │
    #    └──────────────┴──────────────┘
        subprocess.run(["tmux", "kill-session", "-t", SESSION], stderr=subprocess.DEVNULL)
        time.sleep(0.3)

        tmux("new-session", "-d", "-s", SESSION, "-x", "220", "-y", "50")
        tmux("set", "-t", SESSION, "pane-border-status", "top")
        tmux("set", "-t", SESSION, "pane-border-format", " #{pane_title} ")
        tmux("set", "-t", SESSION, "mouse", "on")

        # 上下切分：-l 35 给底部辩论区 35 行，顶部日志区保留其余
        tmux("split-window", "-v", "-l", "35", "-t", f"{SESSION}:0")
        # 辩论区左右切分
        tmux("split-window", "-h", "-t", f"{SESSION}:0.1")

        tmux("select-pane", "-t", f"{SESSION}:0.0", "-T", "📋  日志 / Log")
        tmux("select-pane", "-t", f"{SESSION}:0.1", "-T", "✅  正方 (PRO)")
        tmux("select-pane", "-t", f"{SESSION}:0.2", "-T", "❌  反方 (CON)")

        # 3. 日志区运行 log_watch.sh（辩论结束后提示按 ESC 退出）
        log_file = f"{STATE_DIR}/relay.log"
        open(log_file, "w").close()
        log_watch = f"{STATE_DIR}/log_watch.sh"
        with open(log_watch, "w") as f:
            f.write(f"""\
#!/usr/bin/env bash
tail -f "{log_file}" | while IFS= read -r line; do
    echo "$line"
    [[ "$line" == *"__DEBATE_DONE__"* ]] && break
done
echo ""
echo "════════════════════════════════════════"
echo "  辩论结束！按 ESC 键退出"
echo "════════════════════════════════════════"
while IFS= read -r -s -n1 key; do
    [[ "$key" == $'\\e' ]] && break
done
tmux kill-session -t "{SESSION}" 2>/dev/null || true
""")
        os.chmod(log_watch, 0o755)
        tmux_send("0.0", f"bash {log_watch}", enter=True)

        # 4. 在各自目录启动 claude
        tmux_send("0.1", f"python3 {STATE_DIR}/pro/start.py")
        tmux_send("0.2", f"python3 {STATE_DIR}/con/start.py")

        # 5. 后台等待正方就绪后自动发送开场提示
        if search_mode:
            opening = "请先用 WebSearch 搜索相关数据，然后开始你的开场陈述，最后以【最终论点】输出你的立场和主要论据（150-200字）。"
        else:
            opening = "请开始你的开场陈述，阐明你的立场和主要论据。"
        send_initial_prompt_async("0.1", opening)

        # 6. 立刻进入 tmux
        tmux("select-pane", "-t", f"{SESSION}:0.1")
        subprocess.run(["tmux", "attach-session", "-t", SESSION])

    except KeyboardInterrupt:
        print("\n⚠️  用户中断，正在清理...")
    finally:
        # 清理 tmux session（如果存在）
        subprocess.run(["tmux", "kill-session", "-t", SESSION], stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()

