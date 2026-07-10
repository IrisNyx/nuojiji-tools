#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增量合并引擎 — 从 merge.py 抽出的核心工具函数 + 增量合并逻辑
输入: 现有JSON (chat/tm) + 新解析消息列表 (已带 timestamp_ms)
输出: 合并后的完整 dict，由 GUI 负责写回文件
"""

import os, json, re, random, string, shutil
from datetime import datetime, timezone, timedelta
from collections import defaultdict

CST = timezone(timedelta(hours=8))
UTC = timezone.utc


# ═══════════════════════════════════════════════════════════════
# 工具函数 (从 merge.py 提取)
# ═══════════════════════════════════════════════════════════════

def dtstr_to_ms(s: str) -> int:
    """'2026-04-24T23:20:40' → epoch ms (CST)"""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return int(dt.timestamp() * 1000)


def ms_to_time_str(ms: int) -> str:
    """epoch ms → 'HH:MM' (CST)"""
    dt = datetime.fromtimestamp(ms / 1000, tz=CST)
    return dt.strftime("%H:%M")


def ms_to_export_date(ms: int) -> str:
    """epoch ms → ISO UTC '2026-06-23T12:23:49.595Z'"""
    dt = datetime.fromtimestamp(ms / 1000, tz=UTC)
    ms_part = ms % 1000
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms_part:03d}Z"


def ms_to_dtstr(ms: int) -> str:
    """epoch ms → '2026-04-24T23:20:40' (CST)"""
    dt = datetime.fromtimestamp(ms / 1000, tz=CST)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def ms_to_date_str(ms: int) -> str:
    """epoch ms → '2026-04-23' (CST)"""
    dt = datetime.fromtimestamp(ms / 1000, tz=CST)
    return dt.strftime("%Y-%m-%d")


def _safe_ts(val) -> int:
    """将 timestamp 值统一为 int (epoch ms)，兼容 str/int/float"""
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        try:
            return dtstr_to_ms(val.replace(" ", "T").rstrip("Z"))
        except Exception:
            return 0
    return 0


def make_round_id(ts_ms: int) -> str:
    rand = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))
    return f"round_{ts_ms}_{rand}"


def infer_tempo(content: str) -> str:
    clean = re.sub(r'\*[^*]+\*', '', content)
    clean = re.sub(r'[「」""\n]', '', clean)
    if len(clean) > 150:
        return "S"
    elif len(clean) < 20:
        return "R"
    else:
        return "N"


def is_status_message(content: str) -> bool:
    return content.strip().startswith("<status")


def clean_text_for_chat(content: str) -> str:
    """去掉 *...* 动作标记 & 清理 markdown 残留"""
    text = re.sub(r'\*([^*]+)\*', r'\1', content)
    text = text.replace('>', '').replace('&nbsp;', ' ')
    return text


def normalize_tm_content(content: str, role: str = 'char') -> str:
    """
    统一 tm 内容格式：
    - (动作描写) / （动作描写） → *动作描写*
    - 弯引号 → 「」 (char) / 保留 "" (user)
    - 对话段包「」
    - *...* 和非*...* 之间用 \\n\\n 分隔
    """
    if not content:
        return content

    # 0. 清理 **Name：** 残留
    content = re.sub(r'\*\*[^*\n]+：\*\*', '', content)

    # 1. 全角括号 → 半角
    content = content.replace('\uff08', '(').replace('\uff09', ')')

    # 2. 弯引号 → 「」(char) / 保留 ""(user)
    if role == 'char':
        content = content.replace('\u201c', '「').replace('\u201d', '」')

    # 3. (动作) → *动作*
    content = re.sub(r'\(([^()]+)\)', r'*\1*', content)

    # 4. 检测是否含 *...*
    has_action = bool(re.search(r'\*[^*]+\*', content))

    if not has_action:
        if role == 'char':
            inner = re.sub(r'「([^」]{1,20})」', r'"\1"', content)
            inner = inner.replace('「', '').replace('」', '')
            return '「' + inner.strip() + '」'
        else:
            return content.strip()

    # 5. 有动作 → 按 *...* 拆分
    tokens = re.split(r'(\*[^*]+\*)', content)

    result = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if re.match(r'^[\s,，、。；;：:…\u2026\.\-—]+$', tok):
            continue
        if re.match(r'^\*[^*]+\*$', tok):
            result.append(tok)
        else:
            tok = _norm_dialog(tok, role)
            if tok:
                result.append(tok)

    text = '\n\n'.join(result)

    # 7. 收尾
    text = re.sub(r'^[ \t]+', '', text, flags=re.MULTILINE)
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def _norm_dialog(text: str, role: str) -> str:
    """处理非动作段：剥外皮 → 行内「」→"" → 包「」"""
    text = text.strip()
    if not text:
        return text

    if text.startswith('「') and text.endswith('」'):
        inner = text[1:-1]
        if '「' not in inner and '」' not in inner:
            return text
        text = inner

    if text.startswith('"') and text.endswith('"'):
        inner = text[1:-1]
        if '"' not in inner:
            return '「' + inner + '」'
        text = inner

    text = re.sub(r'「([^」]{1,20})」', r'"\1"', text)
    text = text.replace('「', '').replace('」', '')
    text = text.replace('**', '').replace('__', '')

    text = text.strip()
    if text:
        text = '「' + text + '」'

    return text


# ═══════════════════════════════════════════════════════════════
# 文件 I/O
# ═══════════════════════════════════════════════════════════════

def backup_file(filepath: str) -> str:
    """创建 .bak 备份，返回备份路径"""
    bak = filepath + ".bak"
    shutil.copy2(filepath, bak)
    return bak


def load_json(filepath: str) -> dict:
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(filepath: str, data: dict):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════
# 增量合并 — Chat
# ═══════════════════════════════════════════════════════════════

def merge_chat(
    existing_chat: dict,
    new_msgs: list,
    char_id: str = None,
    char_name: str = None,
    user_id: str = None,
    user_name: str = None,
) -> dict:
    """
    将新解析消息增量合并到现有 chat JSON 中。

    参数:
        existing_chat: 已加载的 chat_xxx.json (dict)
        new_msgs: 新解析消息列表，每条需含:
                  role, content, mode, timestamp_ms, source_file, source_line
        char_id / char_name / user_id / user_name:
                  覆盖值；为 None 时沿用现有 JSON 中的值

    返回:
        合并后的完整 chat dict (不写文件)
    """
    # ── 身份信息 ──
    cid = char_id or existing_chat.get("character", {}).get("id", "")
    cname = char_name or existing_chat.get("character", {}).get("name", "")
    uid = user_id or existing_chat.get("user", {}).get("id", "")
    uname = user_name or existing_chat.get("user", {}).get("name", "")

    existing_msgs = existing_chat.get("messages", [])

    # 计算下一个 _id 和 id
    max_id = 0
    max__id = 0
    for m in existing_msgs:
        if m.get("_id", 0) > max__id:
            max__id = m["_id"]
        if m.get("id", 0) > max_id:
            max_id = m["id"]

    # 确定上一个发送者 (用于 Round 判定)
    prev_sender = None
    current_round_id = None
    if existing_msgs:
        prev_sender = existing_msgs[-1].get("sender")
        # 从最后一条 them 消息提取 roundId
        for m in reversed(existing_msgs):
            if m.get("sender") == "them" and m.get("roundId"):
                current_round_id = m["roundId"]
                break

    # ── 过滤 & 排序 ──
    online_new = [m for m in new_msgs if m.get("mode") == "online"]
    # 按 timestamp_ms 排序
    online_new.sort(key=lambda x: x.get("timestamp_ms", 0))

    # ── 生成 chat 消息 ──
    new_chat_msgs = []
    next_id = max_id + 1

    for i, m in enumerate(online_new):
        ts_ms = m.get("timestamp_ms", 0)
        sender = "me" if m.get("role") in ("user", "user_meta") else "them"
        text = m.get("content", "")
        text_clean = clean_text_for_chat(text)

        # Round 逻辑
        if i == 0:
            # 第一条新消息，看是否需要新 round
            if sender == "me":
                current_round_id = make_round_id(ts_ms)
            elif prev_sender == "me":
                current_round_id = make_round_id(ts_ms)
            # else: 沿用 existing 最后一条 them 的 roundId
            if current_round_id is None:
                current_round_id = make_round_id(ts_ms)
        elif sender == "me" and prev_sender == "them":
            current_round_id = make_round_id(ts_ms)
        # them 跟在 me 或 them 后面：沿用当前 roundId

        prev_sender = sender
        time_str = ms_to_time_str(ts_ms)

        msg_obj = {
            "id": next_id,
            "sender": sender,
            "timestamp": ts_ms,
            "userId": uid,
            "characterId": cid,
            "_id": max__id + i + 1,
        }

        if sender == "me":
            msg_obj.update({
                "text": text_clean,
                "time": time_str,
                "replyTo": None,
                "reactions": [],
            })
        else:
            if is_status_message(text):
                msg_obj.update({
                    "text": text_clean,
                    "rawContent": text,
                    "isHtml": False,
                    "sticker": None,
                    "time": time_str,
                    "roundId": current_round_id,
                    "generatedMode": False,
                    "hasThinking": True,
                    "translation": None,
                })
            else:
                msg_obj.update({
                    "text": text_clean,
                    "timestamp": ts_ms,
                    "tempo": infer_tempo(text),
                    "rawContent": text,
                    "roundId": current_round_id,
                })

        new_chat_msgs.append(msg_obj)
        next_id += 1

    # ── 合并 ──
    all_msgs = existing_msgs + new_chat_msgs

    # 更新元数据
    if all_msgs:
        last_ts = _safe_ts(all_msgs[-1].get("timestamp", 0))
        if last_ts == 0:
            last_ts = _safe_ts(all_msgs[-1].get("timestamp_ms", 0))
        export_date = ms_to_export_date(last_ts if last_ts > 0 else int(datetime.now(UTC).timestamp() * 1000))
        last_message_date = last_ts if last_ts > 0 else int(datetime.now(CST).timestamp() * 1000)
    else:
        export_date = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"
        last_message_date = int(datetime.now(CST).timestamp() * 1000)

    return {
        "version": existing_chat.get("version", "1.0"),
        "exportDate": export_date,
        "character": {
            "id": cid,
            "name": cname,
            "role": existing_chat.get("character", {}).get("role", ""),
            "description": existing_chat.get("character", {}).get("description", ""),
        },
        "user": {"id": uid, "name": uname},
        "messages": all_msgs,
        "summaryHistory": existing_chat.get("summaryHistory", []),
        "episodeSummary": existing_chat.get("episodeSummary", ""),
        "userFacts": existing_chat.get("userFacts", []),
        "messageCount": len(all_msgs),
        "lastMessageDate": last_message_date,
    }


# ═══════════════════════════════════════════════════════════════
# 增量合并 — TM
# ═══════════════════════════════════════════════════════════════

def merge_tm(
    existing_tm: dict,
    new_msgs: list,
    char_id: str = None,
    char_name: str = None,
    user_id: str = None,
    user_name: str = None,
) -> dict:
    """
    将新解析消息增量合并到现有 tm JSON 中。

    参数:
        existing_tm: 已加载的 tm_xxx.json (dict)
        new_msgs: 新解析消息列表，每条需含:
                  role, content, mode, timestamp_ms, source_file
        char_id / char_name / user_id / user_name:
                  覆盖值；为 None 时沿用现有 JSON 中的值

    返回:
        合并后的完整 tm dict (不写文件)
    """
    cid = char_id or existing_tm.get("character", {}).get("id", "")
    cname = char_name or existing_tm.get("character", {}).get("name", "")
    uid = user_id or existing_tm.get("user", {}).get("id", "")
    uname = user_name or existing_tm.get("user", {}).get("name", "")

    existing_sessions = existing_tm.get("sessions", [])

    # 最大 sourceId
    max_source_id = 0
    for s in existing_sessions:
        if s.get("sourceId", 0) > max_source_id:
            max_source_id = s["sourceId"]

    # ── 过滤离线消息 ──
    offline_new = [m for m in new_msgs if m.get("mode") != "online"]

    if not offline_new:
        # 无离线消息，直接返回原样（更新元数据）
        return _make_tm_output(existing_tm, existing_sessions, cid, cname, uid, uname)

    # ── 按 source_file 分组 ──
    session_groups = defaultdict(list)
    for m in offline_new:
        session_groups[m.get("source_file", "unknown.md")].append(m)

    # ── 构建现有 session 名称索引 ──
    # session name 格式: "2026/4/23"
    existing_by_name = {}
    for s in existing_sessions:
        existing_by_name[s.get("name", "")] = s

    next_source_id = max_source_id + 1
    result_sessions = list(existing_sessions)  # 浅拷贝，后面会替换修改过的

    for fn, msgs in sorted(session_groups.items()):
        if not msgs:
            continue

        # 按 source_line 排序
        msgs.sort(key=lambda x: x.get("source_line", 0))

        # 推断 session name
        date_match = re.match(r'^(\d{4})(\d{2})(\d{2})', fn)
        if date_match:
            session_name = f"{int(date_match.group(1))}/{int(date_match.group(2))}/{int(date_match.group(3))}"
        else:
            # 尝试从第一条消息的时间戳推断
            first_ts = msgs[0].get("timestamp_ms", 0)
            if first_ts:
                dt = datetime.fromtimestamp(first_ts / 1000, tz=CST)
                session_name = f"{dt.year}/{dt.month}/{dt.day}"
            else:
                session_name = fn[:20]

        # ── 生成 entries ──
        new_entries = []
        for m in msgs:
            role = "char" if m.get("role") in ("char", "char_meta", "char_text", "char_voice") else "user"
            raw_content = m.get("content", "")
            display_content = normalize_tm_content(raw_content, role)
            has_thinking = (
                role == "char"
                and len(display_content) > 200
                and ("*" in display_content or "「" in display_content)
            )
            new_entries.append({
                "role": role,
                "content": display_content,
                "timestamp": m.get("timestamp_ms", 0),
                "hasThinking": has_thinking,
            })

        if session_name in existing_by_name:
            # ── 追加到现有 session ──
            sess = existing_by_name[session_name]
            # 找到 result_sessions 中对应的 session 并修改
            for idx, rs in enumerate(result_sessions):
                if rs.get("name") == session_name:
                    combined = rs["entries"] + new_entries
                    # 按 timestamp 排序（安全处理 str/int 混合）
                    combined.sort(key=lambda x: _safe_ts(x.get("timestamp", 0)))
                    first_ts = _safe_ts(combined[0]["timestamp"])
                    last_ts = _safe_ts(combined[-1]["timestamp"])
                    result_sessions[idx] = {
                        **rs,
                        "entries": combined,
                        "createdAt": min(_safe_ts(rs.get("createdAt", first_ts)), first_ts),
                        "lastActiveAt": max(_safe_ts(rs.get("lastActiveAt", last_ts)), last_ts),
                    }
                    break
        else:
            # ── 新建 session ──
            first_ts = new_entries[0]["timestamp"]
            last_ts = new_entries[-1]["timestamp"]
            result_sessions.append({
                "sourceId": next_source_id,
                "name": session_name,
                "type": "this-moment",
                "createdAt": first_ts,
                "lastActiveAt": last_ts,
                "entries": new_entries,
            })
            existing_by_name[session_name] = result_sessions[-1]
            next_source_id += 1

    return _make_tm_output(existing_tm, result_sessions, cid, cname, uid, uname)


def _make_tm_output(existing_tm: dict, sessions: list,
                    cid: str, cname: str, uid: str, uname: str) -> dict:
    return {
        "__schema": existing_tm.get("__schema", "nuojiji.tmRecord.v1"),
        "exportedAt": int(datetime.now(CST).timestamp() * 1000),
        "character": {"id": cid, "name": cname},
        "user": {"id": uid, "name": uname},
        "sessions": sessions,
    }


def merge_tm_standalone(
    new_msgs: list,
    char_id: str = "",
    char_name: str = "",
    user_id: str = "",
    user_name: str = "",
) -> dict:
    """
    独立生成全新 TM JSON（不复用现有 session）。
    适用于第一次导入 / 重新生成场景。

    参数:
        new_msgs: 新解析消息列表（仅取 mode != "online"）
        char_id / char_name / user_id / user_name: 身份信息

    返回:
        全新的 tm dict
    """
    cid = char_id or ""
    cname = char_name or ""
    uid = user_id or ""
    uname = user_name or ""

    offline_new = [m for m in new_msgs if m.get("mode") != "online"]
    if not offline_new:
        return {
            "__schema": "nuojiji.tmRecord.v1",
            "exportedAt": int(datetime.now(CST).timestamp() * 1000),
            "character": {"id": cid, "name": cname},
            "user": {"id": uid, "name": uname},
            "sessions": [],
        }

    # 按 source_file 分组
    from collections import defaultdict
    session_groups = defaultdict(list)
    for m in offline_new:
        session_groups[m.get("source_file", "unknown.md")].append(m)

    next_source_id = 1
    sessions = []

    for fn, msgs in sorted(session_groups.items()):
        if not msgs:
            continue
        msgs.sort(key=lambda x: x.get("source_line", 0))

        # 推断 session name
        date_match = re.match(r'^(\d{4})(\d{2})(\d{2})', fn)
        if date_match:
            session_name = f"{int(date_match.group(1))}/{int(date_match.group(2))}/{int(date_match.group(3))}"
        else:
            first_ts = msgs[0].get("timestamp_ms", 0)
            if first_ts:
                dt = datetime.fromtimestamp(first_ts / 1000, tz=CST)
                session_name = f"{dt.year}/{dt.month}/{dt.day}"
            else:
                session_name = fn[:20]

        entries = []
        for m in msgs:
            role = "char" if m.get("role") in ("char", "char_meta", "char_text", "char_voice") else "user"
            raw_content = m.get("content", "")
            display_content = normalize_tm_content(raw_content, role)
            has_thinking = (
                role == "char"
                and len(display_content) > 200
                and ("*" in display_content or "「" in display_content)
            )
            entries.append({
                "role": role,
                "content": display_content,
                "timestamp": m.get("timestamp_ms", 0),
                "hasThinking": has_thinking,
            })

        first_ts = entries[0]["timestamp"]
        last_ts = entries[-1]["timestamp"]
        sessions.append({
            "sourceId": next_source_id,
            "name": session_name,
            "type": "this-moment",
            "createdAt": first_ts,
            "lastActiveAt": last_ts,
            "entries": entries,
        })
        next_source_id += 1

    return {
        "__schema": "nuojiji.tmRecord.v1",
        "exportedAt": int(datetime.now(CST).timestamp() * 1000),
        "character": {"id": cid, "name": cname},
        "user": {"id": uid, "name": uname},
        "sessions": sessions,
    }


# ═══════════════════════════════════════════════════════════════
# 综合合并入口 (同时更新 chat + tm)
# ═══════════════════════════════════════════════════════════════

def merge_all(
    chat_path: str,
    tm_path: str,
    new_msgs: list,
    char_id: str = None,
    char_name: str = None,
    user_id: str = None,
    user_name: str = None,
    dry_run: bool = False,
) -> dict:
    """
    一站式增量合并：加载 → 备份 → 合并 → 写回。

    参数:
        chat_path: 现有 chat JSON 路径
        tm_path:   现有 tm JSON 路径
        new_msgs:  新解析消息列表
        char_id / char_name / user_id / user_name: 身份覆盖
        dry_run:   True = 只返回合并结果，不写文件不备份

    返回:
        {
            "chat": merged_chat_dict,
            "tm": merged_tm_dict,
            "chat_bak": "path/to/chat.bak" or None,
            "tm_bak": "path/to/tm.bak" or None,
            "chat_new_count": int,
            "tm_new_count": int,
            "tm_new_sessions": int,
        }
    """
    existing_chat = load_json(chat_path)
    existing_tm = load_json(tm_path)

    # 身份默认值从现有 JSON 读取
    if char_id is None:
        char_id = existing_chat.get("character", {}).get("id", "")
    if char_name is None:
        char_name = existing_chat.get("character", {}).get("name", "")
    if user_id is None:
        user_id = existing_chat.get("user", {}).get("id", "")
    if user_name is None:
        user_name = existing_chat.get("user", {}).get("name", "")

    # 统计现有数量
    old_chat_count = len(existing_chat.get("messages", []))
    old_tm_sessions = len(existing_tm.get("sessions", []))

    # 合并
    merged_chat = merge_chat(existing_chat, new_msgs, char_id, char_name, user_id, user_name)
    merged_tm = merge_tm(existing_tm, new_msgs, char_id, char_name, user_id, user_name)

    new_chat_count = len(merged_chat.get("messages", [])) - old_chat_count
    new_tm_sessions = len(merged_tm.get("sessions", [])) - old_tm_sessions
    new_tm_entries = sum(len(s["entries"]) for s in merged_tm.get("sessions", [])) - \
                     sum(len(s["entries"]) for s in existing_tm.get("sessions", []))

    result = {
        "chat": merged_chat,
        "tm": merged_tm,
        "chat_bak": None,
        "tm_bak": None,
        "chat_new_count": new_chat_count,
        "tm_new_count": new_tm_entries,
        "tm_new_sessions": new_tm_sessions,
    }

    if not dry_run:
        result["chat_bak"] = backup_file(chat_path)
        result["tm_bak"] = backup_file(tm_path)
        save_json(chat_path, merged_chat)
        save_json(tm_path, merged_tm)

    return result


# ── 简短测试 ──
if __name__ == "__main__":
    print("merge_engine.py — 工具函数自检")
    print(f"  dtstr_to_ms('2026-04-24T23:20:40') = {dtstr_to_ms('2026-04-24T23:20:40')}")
    print(f"  ms_to_time_str({dtstr_to_ms('2026-04-24T23:20:40')}) = {ms_to_time_str(dtstr_to_ms('2026-04-24T23:20:40'))}")
    print(f"  infer_tempo('Hello') = {infer_tempo('Hello')}")
    print(f"  infer_tempo('...' + 'x'*200) = {infer_tempo('x'*200)}")
    print(f"  clean_text_for_chat('*smiles* Hello > world') = '{clean_text_for_chat('*smiles* Hello > world')}'")
    print(f"  normalize_tm_content('(轻笑)你好') = '{normalize_tm_content('(轻笑)你好', 'char')}'")
    print(f"  make_round_id(1700000000000) = {make_round_id(1700000000000)}")
    print("  所有工具函数就绪。")
