#!/usr/bin/env python3
"""
从 Claude Code transcript 文件中提取最后一条 assistant 消息的文本内容。
用于 relay.sh Stop hook。

用法:
  python3 extract_text.py <transcript_path> <expected_count>  # 提取最后一条消息
  python3 extract_text.py --final-argument                    # 从 stdin 读取，提取【最终论点】
"""

import json
import re
import sys
from typing import Optional


def extract_last_text(path: str, expected_count: int) -> Optional[str]:
    """
    从 transcript 文件中提取最后一条 assistant 消息。

    Args:
        path: transcript 文件路径
        expected_count: 已处理的消息数，只有超过此数才返回新消息

    Returns:
        最后一条消息的文本，如果没有新消息则返回 None
    """
    msgs = []

    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("type") != "assistant":
                continue

            content = obj.get("message", {}).get("content", [])
            texts = [
                block["text"]
                for block in content
                if isinstance(content, list)
                and block.get("type") == "text"
                and block.get("text", "").strip()
            ]
            if texts:
                msgs.append(" ".join(texts))

    # 只有当消息数超过已处理数时，才返回新消息
    if len(msgs) > expected_count:
        return msgs[-1].strip()
    return None


def extract_final_argument(text: str) -> str:
    """
    从文本中提取【最终论点】之后的内容。
    如果没有找到标记，返回原文。

    Args:
        text: 输入文本

    Returns:
        提取的最终论点或原文
    """
    m = re.search(r"【最终论点】(.*)", text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()


def main():
    if len(sys.argv) < 2:
        print("用法: extract_text.py <transcript_path> <expected_count>", file=sys.stderr)
        print("      extract_text.py --final-argument < text", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--final-argument":
        # 从 stdin 读取，提取【最终论点】
        text = sys.stdin.read()
        result = extract_final_argument(text)
        print(result, end="")
        return

    if len(sys.argv) < 3:
        print("用法: extract_text.py <transcript_path> <expected_count>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    expected = int(sys.argv[2])

    result = extract_last_text(path, expected)
    if result:
        print(result, end="")


if __name__ == "__main__":
    main()
