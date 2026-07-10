#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MD对话解析器 — 从 parse_unified.py 抽出的纯解析核心
输入: .md 文件路径 或 纯文本
输出: 解析后的消息列表 [{role, content, mode, reasons, source_file, source_line, speaker_name, is_online}, ...]

无副作用、无状态、可独立使用。
"""

import os, re

# ── 发言人识别 ─────────────────────────────────
KNOWN_SPEAKERS = {
    "夜鸢": "user", "望": "char",
    "夜鸢（场外）": "user_meta", "望（场外）": "char_meta",
    "望（文字）": "char_text", "望（语音）": "char_voice",
    "望(文字)": "char_text", "望(语音)": "char_voice",
    "场外（deepseek代问）": "ds_proxy",
}

ROLE_NORMALIZE = {
    "user": "user", "user_meta": "user",
    "char": "char", "char_meta": "char",
    "char_text": "char", "char_voice": "char",
    "ds_proxy": "char",
}

# ── 模式检测 ────────────────────────────────────
ONLINE_SIGNATURES = [
    r'\[对方正在输入[.\]]*\]',
    r'\[语音\s*[\d:]+\]',
    r'\[屏幕上的输入状态',
    r'\[对方撤回了一条消息\]',
]
SYS_ENTER_ONLINE = re.compile(r'系统提示[：:]\s*(进入|切换到)\s*终端模式')
SYS_ENTER_OFFLINE = re.compile(r'系统提示[：:]\s*(退出|离开|切换到)\s*(日常|线下)')
SYS_HINT = re.compile(r'系统提示[：:]\s*')
# 新增：## 📱 线上 / ## 📝 线下 标记识别（来自 JSON→MD 反向转换工具）
H2_ONLINE = re.compile(r'^##\s*📱\s*线上')
H2_OFFLINE = re.compile(r'^##\s*📝\s*线下')


def detect_mode(text):
    """检测对话模式: online / offline / mixed / unknown / switch_to_online / switch_to_offline"""
    reasons = []
    if SYS_ENTER_ONLINE.search(text):
        reasons.append("系统提示: 进入终端模式")
        return "switch_to_online", reasons
    if SYS_ENTER_OFFLINE.search(text):
        reasons.append("系统提示: 离开终端模式")
        return "switch_to_offline", reasons
    if SYS_HINT.search(text):
        reasons.append("系统提示(其他)")

    has_online = False
    for pat in ONLINE_SIGNATURES:
        if re.search(pat, text):
            has_online = True
            reasons.append(f"在线标记: {pat}")
            break

    has_action = bool(re.search(r'[（(][^）)]*[）)]', text))
    if has_action:
        reasons.append("离线特征: 括号动作描写")

    has_quoted_dialogue = bool(re.search(r'["「][^"」]{5,}["」]', text))

    if has_online and has_action:
        return "mixed", reasons
    elif has_online:
        return "online", reasons
    elif has_action:
        return "offline", reasons

    if has_quoted_dialogue:
        return "offline", reasons + ["推断: 含引导对话→线下"]

    return "unknown", reasons


# ── 思流新格式（含完整时间戳）───────────────
# 👤/🤖 Name YYYY-MM-DD HH:MM:SS
SIS_NEW_SPEAKER = re.compile(r'^(👤|🤖)\s+(.+?)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*$')
# 元数据行: > 创建: ... | 更新: ... | 消息数: ...
SIS_META_LINE = re.compile(r'^>\s*(创建|更新|消息数)[：:]')

# 👤→user, 🤖→char
SIS_EMOJI_ROLE = {"👤": "user", "🤖": "char"}


def _detect_sis_format(text):
    """检测是否为思流新格式（👤/🤖 + 完整时间戳）"""
    return bool(re.search(r'^(👤|🤖)\s+\S+?\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}', text, re.MULTILINE))


# ── 发言人解析正则 ──────────────────────────
SPEAKER_INLINE = re.compile(r'^>\s*\*\*(.+?)\*\*\s*[：:]\s*(.*)$')
SPEAKER_HEADER = re.compile(r'^\*\*(.+?)\*\*\s*[：:]?\s*$')
SPEAKER_HEADER_BARE = re.compile(r'^\*\*(.+?)\*\*\s*$')
SPEAKER_INLINE_BARE = re.compile(r'^\*\*(.+?)\*\*\s*[：:]\s*(.*)$')
SPEAKER_INLINE_BARE2 = re.compile(r'^\*\*(.+?)[：:]\*\*\s*(.*)$')
SPEAKER_QUOTE_NONBOLD = re.compile(r'^>\s*([^*\n]{1,30}?)[：:]\s*(.*)$')


def extract_speaker(bold_text):
    """从粗体标签提取发言人名称和角色"""
    text = bold_text.strip()
    if text in KNOWN_SPEAKERS:
        return text, KNOWN_SPEAKERS[text]
    base = re.sub(r'[（(][^）)]*[）)]', '', text).strip()
    if base in KNOWN_SPEAKERS:
        return base, KNOWN_SPEAKERS[base]
    for name, role in KNOWN_SPEAKERS.items():
        if name in text:
            return name, role
    return text, None


def extract_date_from_filename(fname):
    """从文件名提取日期: '202604230116_xxx.md' → '2026-04-23'"""
    m = re.match(r'^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})?_', fname)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return f"{y}-{mo}-{d}"
    return "2026-04-22"


def parse_md_text(raw_text, source_filename="unknown.md"):
    """
    解析MD纯文本为消息列表。
    支持三种格式: 旧版引用块(>)、JSON→MD反向导出、思流新格式(👤/🤖+时间戳)
    
    参数:
        raw_text: str — 完整的 .md 文件内容
        source_filename: str — 用于 source_file 字段和日期推断
    
    返回:
        list[dict] — 每条消息包含:
            role:        'user' | 'char' | 'unknown'
            content:     str — 消息正文
            mode:        'online' | 'offline'
            reasons:     str — 模式判定依据
            source_file: str
            source_line: int
            speaker_name: str
            is_online:   bool — 简化标记 (mode=='online')
            timestamp:   str | None — 思流格式的真实时间戳 (YYYY-MM-DD HH:MM:SS)
    """
    basename = os.path.basename(source_filename) if source_filename else "unknown.md"

    # ── 格式检测 ──
    is_sis = _detect_sis_format(raw_text)

    # ── 解析 frontmatter ──
    body = raw_text
    body_start_offset = 0
    if raw_text.startswith("---"):
        end = raw_text.find("---", 3)
        if end != -1:
            body = raw_text[end + 3:]
            body_start_offset = raw_text.count("\n", 0, end + 3)

    lines = body.split("\n")
    messages = []

    current_speaker = None
    current_role = None
    current_lines = []
    current_start_line = 0
    current_timestamp = None  # 思流格式：真实时间戳
    ctx_mode = "offline"

    def flush_message():
        nonlocal current_speaker, current_role, current_lines, current_start_line, ctx_mode, current_timestamp
        if current_speaker and current_lines:
            content = "\n".join(current_lines).strip()
            content = re.sub(r'\n{3,}', '\n\n', content)
            if content:
                mode, reasons = detect_mode(content)

                if mode == "switch_to_online":
                    reasons.append("CTX: →在线")
                    mode = "online"
                elif mode == "switch_to_offline":
                    reasons.append("CTX: →线下")
                    mode = "offline"
                elif mode == "unknown":
                    mode = ctx_mode
                    reasons.append(f"CTX: 继承{ctx_mode}")
                elif mode == "mixed":
                    mode = ctx_mode
                    reasons.append(f"CTX: mixed→{ctx_mode}")

                msg = {
                    "role": current_role or "unknown",
                    "content": content,
                    "mode": mode,
                    "reasons": "; ".join(reasons),
                    "source_file": basename,
                    "source_line": current_start_line + 1,
                    "speaker_name": current_speaker,
                    "is_online": mode == "online",
                }
                if current_timestamp:
                    msg["timestamp"] = current_timestamp
                messages.append(msg)

                if mode in ("online", "offline"):
                    ctx_mode = mode

        current_speaker = None
        current_role = None
        current_lines = []
        current_start_line = 0
        current_timestamp = None

    for line_no, line in enumerate(lines):
        stripped = line.strip()

        # ── 思流新格式：👤/🤖 Name YYYY-MM-DD HH:MM:SS ──
        if is_sis:
            m_sis = SIS_NEW_SPEAKER.match(stripped)
            if m_sis:
                flush_message()
                emoji = m_sis.group(1)
                name_raw = m_sis.group(2).strip()
                current_timestamp = m_sis.group(3)
                speaker_name, role = extract_speaker(name_raw)
                if not role:
                    role = SIS_EMOJI_ROLE.get(emoji, "unknown")
                current_speaker = name_raw  # 思流格式保留原始名称
                current_role = role
                current_lines = []
                current_start_line = line_no
                continue

            # 思流元数据行: > 创建/更新/消息数
            if SIS_META_LINE.match(stripped):
                continue

            # 思流格式: --- 分隔符
            if stripped.startswith("---"):
                flush_message()
                continue

            # 思流格式: # 标题行
            if stripped.startswith("#"):
                flush_message()
                continue

            # 思流格式: 空行跳过
            if not stripped:
                continue

            # 思流格式: 剩余行全是内容
            if current_speaker:
                current_lines.append(stripped)
            continue

        # Case 1: > **Name**：content
        m_inline = SPEAKER_INLINE.match(stripped)
        if m_inline:
            flush_message()
            name_raw = m_inline.group(1).strip()
            content_part = m_inline.group(2).strip()
            speaker_name, role = extract_speaker(name_raw)
            if role:
                current_speaker = speaker_name
                current_role = role
                current_lines = [content_part] if content_part else []
                current_start_line = line_no
            continue

        # Case 1b: **Name**：content or **Name：** content (no >)
        m_inline_bare = SPEAKER_INLINE_BARE.match(stripped) or SPEAKER_INLINE_BARE2.match(stripped)
        if m_inline_bare:
            name_raw = m_inline_bare.group(1).strip()
            content_part = m_inline_bare.group(2).strip()
            if content_part:
                speaker_name, role = extract_speaker(name_raw)
                if role:
                    flush_message()
                    content_part = re.sub(r'^>\s?', '', content_part, count=1)
                    current_speaker = speaker_name
                    current_role = role
                    current_lines = [content_part]
                    current_start_line = line_no
            if content_part:
                continue

        # Case 2: standalone **Name** or **Name**：
        if SPEAKER_HEADER.match(stripped) or SPEAKER_HEADER_BARE.match(stripped):
            name_raw = stripped.strip("*").strip("：:").strip()
            speaker_name, role = extract_speaker(name_raw)
            if role:
                flush_message()
                current_speaker = speaker_name
                current_role = role
                current_lines = []
                current_start_line = line_no
            else:
                flush_message()
            continue

        # Case 3: > quoted line
        if stripped.startswith(">"):
            m_quote_nb = SPEAKER_QUOTE_NONBOLD.match(stripped)
            if m_quote_nb:
                name_raw = m_quote_nb.group(1).strip()
                content_part = m_quote_nb.group(2).strip()
                speaker_name, role = extract_speaker(name_raw)
                if role:
                    flush_message()
                    current_speaker = speaker_name
                    current_role = role
                    current_lines = [content_part] if content_part else []
                    current_start_line = line_no
                    continue
            content = re.sub(r'^>\s?', '', stripped, count=1)
            if current_speaker:
                current_lines.append(content)
            continue

        # Case 3b: plain text after speaker header (no >)
        if current_speaker and stripped and not stripped.startswith("#") and not stripped.startswith("---"):
            if SPEAKER_HEADER.match(stripped) or SPEAKER_HEADER_BARE.match(stripped) or SPEAKER_INLINE_BARE.match(stripped):
                continue
            if re.match(r'^\*\*\d{4}-\d{2}-\d{2}', stripped):
                continue
            current_lines.append(stripped)
            continue

        # Case 4: ## or heading
        if stripped.startswith("#"):
            flush_message()
            # 识别 ## 📱 线上 / ## 📝 线下 模式标记
            if H2_ONLINE.match(stripped):
                ctx_mode = "online"
            elif H2_OFFLINE.match(stripped):
                ctx_mode = "offline"
            continue

        # Case 5: date line
        if re.match(r'^\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}', stripped):
            continue

        # Case 6: --- separator
        if stripped.startswith("---"):
            flush_message()
            continue

    flush_message()
    return messages


def parse_md_file(filepath):
    """便捷函数: 从文件路径解析MD"""
    with open(filepath, 'r', encoding='utf-8') as f:
        raw = f.read()
    return parse_md_text(raw, os.path.basename(filepath))


# ── 简短测试 ──
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        path = sys.argv[1]
        msgs = parse_md_file(path)
        for i, m in enumerate(msgs):
            print(f"[{i+1}] {m['speaker_name']}({m['role']}) | {m['mode']} | L{m['source_line']}")
            print(f"     {m['content'][:120]}")
            print()
        print(f"Total: {len(msgs)} messages")
    else:
        print("用法: python md_parser.py <path-to-.md>")
