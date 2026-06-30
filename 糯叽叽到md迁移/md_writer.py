#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MD 格式生成器

输入: 单日条目列表 [{speaker, time, content, ...}, ...]
输出: Markdown 文本（供写入 .md 文件）

格式约定:
  **发言人**：HH:MM
  > 内容行1
  > 内容行2

多段内容（含 \n）自动拆分为多条 > 行。

合并规则:
  同一发言人在 5 分钟内连续发送的多条消息，合并为一个引用块。
  多条消息之间用空行（> 单独一行）分隔。
"""

import os

# 同一发言人合并阈值（毫秒）
MERGE_THRESHOLD_MS = 5 * 60 * 1000  # 5 分钟

SOURCE_MARKER = {
    "chat": "## 📱 线上",
    "tm":   "## 📝 线下",
}


def _escape_md(text: str) -> str:
    """基础转义，防止破坏 Markdown 格式"""
    return text.strip()


def _merge_consecutive_entries(entries: list[dict]) -> list[dict]:
    """
    将同一发言人 5 分钟内的连续消息合并。

    合并条件（全部满足）：
      - speaker 相同
      - source 相同（Chat/TM 不混）
      - 相邻条目 timestamp_ms 差值 ≤ 5 分钟

    合并后：
      - 时间取第一条的 time
      - 内容用 \n\n 拼接
      - timestamp_ms 取第一条的（保持排序稳定）
    """
    if not entries:
        return []

    merged = []
    current = dict(entries[0])  # 浅拷贝
    current["content"] = _escape_md(current.get("content", ""))

    for e in entries[1:]:
        same_speaker = e.get("speaker") == current["speaker"]
        same_source = e.get("source") == current["source"]
        time_diff = e.get("timestamp_ms", 0) - current.get("timestamp_ms", 0)
        within_threshold = 0 <= time_diff <= MERGE_THRESHOLD_MS

        if same_speaker and same_source and within_threshold:
            # 合并：追加内容
            extra = _escape_md(e.get("content", ""))
            if extra:
                current["content"] += "\n\n" + extra
            # 更新时间为较晚的那条（保持时间推进）
            # 不更新，保持第一条时间即可，用户明确说"合并成一个引用块"
        else:
            merged.append(current)
            current = dict(e)
            current["content"] = _escape_md(current.get("content", ""))

    merged.append(current)
    return merged


def format_entry(speaker: str, time_str: str, content: str) -> str:
    """
    将单条（或合并后）记录格式化为 MD 块。

    Args:
        speaker: 发言人名称（如 "望"、"夜鸢"）
        time_str: 时间字符串（如 "09:12"）
        content: 消息内容（可能含 \n\n 分隔的多条消息）

    Returns:
        格式化的 MD 文本块（含尾部空行）
    """
    content = _escape_md(content)
    if not content:
        return ""

    lines = []
    # 首行：**发言人**：HH:MM
    lines.append(f"**{speaker}**：{time_str}")

    # 后续行：> 内容
    for part in content.split("\n"):
        part = part.strip()
        if not part:
            # 空行 → 保留为空的引用行（分隔合并消息用）
            lines.append(">")
        elif part.startswith(">"):
            lines.append(part)
        else:
            lines.append(f"> {part}")

    # 条目间空一行
    return "\n".join(lines) + "\n\n"


def generate_md(entries: list[dict], date_str: str = "") -> str:
    """
    将一日内的所有条目生成完整 MD 文本。

    处理流程：
      1. 合并同一发言人 5 分钟内的连续消息
      2. 当条目来源（Chat/TM）切换时，自动插入模式分隔标记：
           ## 📱 线上
           ## 📝 线下

    Args:
        entries: 该日条目列表（已按时间排序）
        date_str: 日期字符串（用于 YAML frontmatter）

    Returns:
        完整 MD 文本
    """
    # 先合并连续消息
    entries = _merge_consecutive_entries(entries)

    parts = []

    # YAML frontmatter
    if date_str:
        parts.append("---")
        parts.append(f"created_at: {date_str}")
        parts.append(f"title: {date_str} 对话记录")
        parts.append("---")
        parts.append("")

    current_source = None
    for e in entries:
        src = e.get("source", "")
        if src != current_source:
            current_source = src
            marker = SOURCE_MARKER.get(src, "")
            if marker:
                parts.append(marker + "\n\n")

        md = format_entry(e["speaker"], e["time"], e["content"])
        parts.append(md)

    return "".join(parts)


def write_md_file(entries: list[dict], date_str: str, output_dir: str) -> str:
    """
    将一日条目写入 .md 文件。

    Args:
        entries: 该日条目列表
        date_str: 日期字符串（如 "2026-04-23"）
        output_dir: 输出目录

    Returns:
        生成的文件路径
    """
    date_compact = date_str.replace("-", "")
    filename = f"{date_compact}_对话记录.md"
    filepath = os.path.join(output_dir, filename)

    md_text = generate_md(entries, date_str)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md_text)

    return filepath
