#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
糯叽叽 JSON → 统一条目 解析器

输入: Chat JSON 路径 + TM JSON 路径
输出:
  - all_entries: [{date, time, source, role, speaker, content, timestamp_ms, ...}, ...]
    按 timestamp_ms 升序排列
  - grouped:  {date_str: [entries], ...}
    按日期分组，同日内再按时间排序
"""

import json
import re
from datetime import datetime, timezone, timedelta
from collections import defaultdict

CST = timezone(timedelta(hours=8))

# ── 过滤白名单 ─────────────────────────────────
SKIP_CHAT_TYPES = {
    "reply",          # 回复链
    "change_song",    # 切歌
    "recalled",       # 撤回
    "offline_invite", "offline_invite_response",
    "offline_story_complete",
    "commitment_notice",
    "user_offline_invite", "user_offline_invite_response",
}

# 发言人映射
SENDER_MAP = {
    "me":   "夜鸢",
    "them": "望",
    "char": "望",
    "user": "夜鸢",
}

SOURCE_LABEL = {"chat": "📱Chat", "tm": "📝TM"}


# ═══════════════════════════════════════════════════════════
# 时间解析
# ═══════════════════════════════════════════════════════════

def _parse_epoch_ms(raw) -> int:
    """解析可能为毫秒或秒的 epoch，返回毫秒"""
    if isinstance(raw, (int, float)):
        n = int(raw)
        return n if n > 1e12 else n * 1000
    return 0


def _parse_iso(raw: str) -> int:
    """解析 ISO 8601 字符串，返回 epoch ms"""
    if not raw:
        return 0
    try:
        s = raw.replace("Z", "+00:00")
        if "T" in s:
            dt = datetime.fromisoformat(s)
        else:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def _ts_to_parts(ts_ms: int) -> tuple[str, str]:
    """epoch ms → (date_str, time_str)"""
    if ts_ms:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=CST)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    return "无日期", "--:--"


# ═══════════════════════════════════════════════════════════
# Chat 消息抽取
# ═══════════════════════════════════════════════════════════

def _extract_chat_text(m: dict) -> str | None:
    """
    从一条 Chat 消息中提取纯文本内容。
    返回 None 表示该消息应被跳过。
    """
    # ── 检查 type 字段 ──
    mtype = m.get("type", "")
    if mtype in SKIP_CHAT_TYPES:
        return None
    if mtype == "state":
        return None  # App 感知状态
    if mtype == "text":
        return m.get("text", "") or None

    # ── 检查 stateKind (appsense) ──
    if m.get("stateKind") == "appsense":
        return None

    # ── 无 type 但可能有 content 字段 ──
    if "content" in m:
        c = m.get("content", "")
        if isinstance(c, str) and c.strip().startswith("{"):
            try:
                cobj = json.loads(c)
                ct = cobj.get("t", "")
                if ct == "text":
                    return cobj.get("c", "") or None
                elif ct == "image":
                    sub = cobj.get("sub", "图片")
                    sub_cn = {"selfie": "自拍", "scene": "场景"}.get(sub, sub)
                    return f"[图片: {sub_cn}]"
                elif ct == "reply":
                    return None  # 回复链
                elif ct == "change_song":
                    return None  # 切歌
                else:
                    return f"[{ct}]"
            except json.JSONDecodeError:
                return c or None
        elif isinstance(c, list):
            return " ".join(str(x) for x in c) or None
        elif isinstance(c, dict):
            return c.get("c", c.get("text", str(c))) or None
        elif isinstance(c, str):
            return c or None
        return str(c) if c else None

    # ── 纯 text 字段 ──
    if "text" in m:
        t = m.get("text", "")
        if isinstance(t, str) and t.strip():
            return t
        return None

    # ── 无内容 → 看是否有 sticker ──
    if m.get("sticker"):
        return "[表情]"

    return None


def _extract_chat_speaker(m: dict) -> str | None:
    """返回发言人姓名（中文），system 消息跳过"""
    sender = m.get("sender", m.get("role", ""))
    if sender == "system":
        return None
    return SENDER_MAP.get(sender, sender or "未知")


# ═══════════════════════════════════════════════════════════
# TM entry 抽取
# ═══════════════════════════════════════════════════════════

def _extract_tm_speaker(e: dict) -> str:
    role = e.get("role", "")
    return SENDER_MAP.get(role, role or "未知")


# ═══════════════════════════════════════════════════════════
# 主解析入口
# ═══════════════════════════════════════════════════════════

def parse_chat(chat_path: str) -> list[dict]:
    """解析 Chat JSON，返回清洗后的条目列表"""
    with open(chat_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries = []
    messages = data.get("messages", [])
    for i, m in enumerate(messages):
        text = _extract_chat_text(m)
        if text is None:
            continue
        speaker = _extract_chat_speaker(m)
        if speaker is None:
            continue

        ts_raw = m.get("timestamp", 0)
        ts_ms = _parse_iso(ts_raw) if isinstance(ts_raw, str) else _parse_epoch_ms(ts_raw)
        date_str, time_str = _ts_to_parts(ts_ms)

        entries.append({
            "date":       date_str,
            "time":       time_str,
            "timestamp_ms": ts_ms,
            "source":     "chat",
            "source_label": SOURCE_LABEL["chat"],
            "speaker":    speaker,
            "content":    text,
            "has_thinking": bool(m.get("hasThinking")),
            "original_index": i,
        })

    return entries


def parse_tm(tm_path: str) -> list[dict]:
    """解析 TM JSON，返回清洗后的条目列表"""
    with open(tm_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries = []
    sessions = data.get("sessions", [])
    for si, s in enumerate(sessions):
        for ei, e in enumerate(s.get("entries", [])):
            text = e.get("content", e.get("text", ""))
            if not text or not isinstance(text, str):
                continue
            speaker = _extract_tm_speaker(e)
            ts_ms = e.get("timestamp", e.get("createdAt", 0))
            if isinstance(ts_ms, str):
                ts_ms = _parse_iso(ts_ms)
            else:
                ts_ms = _parse_epoch_ms(ts_ms)
            date_str, time_str = _ts_to_parts(ts_ms)

            entries.append({
                "date":         date_str,
                "time":         time_str,
                "timestamp_ms": ts_ms,
                "source":       "tm",
                "source_label": SOURCE_LABEL["tm"],
                "speaker":      speaker,
                "content":      text,
                "has_thinking":  bool(e.get("hasThinking")),
                "session_index": si,
                "entry_index":   ei,
            })

    return entries


def parse_and_group(chat_path: str, tm_path: str) -> tuple[list[dict], dict[str, list[dict]]]:
    """
    主入口：解析 Chat + TM，合并排序，按日期分组。

    Returns:
        all_entries: 按时间升序的完整列表
        grouped:      {date_str: [entries], ...} 按日期分组，组内按时间排序
    """
    chat_entries = parse_chat(chat_path)
    tm_entries = parse_tm(tm_path)

    all_entries = chat_entries + tm_entries
    all_entries.sort(key=lambda e: e["timestamp_ms"])

    grouped = defaultdict(list)
    for e in all_entries:
        grouped[e["date"]].append(e)

    # 按键（日期）排序
    grouped_sorted = dict(sorted(grouped.items()))

    return all_entries, grouped_sorted


# ═══════════════════════════════════════════════════════════
# CLI 测试入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("用法: python json_parser.py <chat.json> <tm.json>")
        sys.exit(1)

    all_entries, grouped = parse_and_group(sys.argv[1], sys.argv[2])
    print(f"总计: {len(all_entries)} 条, {len(grouped)} 个日期")
    for date, entries in grouped.items():
        chat_n = sum(1 for e in entries if e["source"] == "chat")
        tm_n = len(entries) - chat_n
        print(f"  {date}: {len(entries)} 条 (Chat {chat_n} / TM {tm_n})")
