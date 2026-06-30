#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增量导入工具 — PyQt6 桌面应用
加载现有 chat/tm JSON → 导入 MD → 编辑消息 → 增量合并

功能:
  - 浏览并加载现有 chat_xxx.json / tm_xxx.json
  - 可编辑角色名称、角色ID、用户名称、用户ID
  - 导入多个 .md 对话文件，自动解析
  - 可编辑表格：日期、时间、模式(线上/线下)、内容
  - 时间分配：默认前条+45秒；右键"均匀分配时间"
  - 增量合并预览 → 自动 .bak 备份 → 写回 JSON
"""

import sys, os, json, re
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── PyQt6 imports ──
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QFormLayout, QLabel, QLineEdit, QPushButton,
    QTableView, QHeaderView, QAbstractItemView, QFileDialog,
    QMessageBox, QStatusBar, QMenu, QStyledItemDelegate,
    QComboBox, QDateEdit, QTimeEdit, QSpinBox, QDialog,
    QDialogButtonBox, QTextEdit, QSplitter, QListWidget,
    QListWidgetItem, QCheckBox, QStyleFactory,
)
from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QDate, QTime,
    pyqtSignal, pyqtSlot, QSize,
)
from PyQt6.QtGui import QAction, QColor, QFont, QKeySequence

# ── 本地模块 ──
from md_parser import parse_md_file, parse_md_text, extract_date_from_filename
from merge_engine import (
    merge_all, merge_chat, merge_tm, merge_tm_standalone,
    dtstr_to_ms, ms_to_dtstr, ms_to_date_str, ms_to_time_str,
    load_json, save_json, backup_file,
    clean_text_for_chat, normalize_tm_content,
)

CST = timezone(timedelta(hours=8))
DEFAULT_STEP_SEC = 45  # 默认消息间隔秒数


# ═══════════════════════════════════════════════════════════════
# 表格数据模型
# ═══════════════════════════════════════════════════════════════

COLUMNS = ["#", "日期", "时间", "模式", "发送者", "内容", "源文件", "源行号"]
COL_IDX = 0
COL_DATE = 1
COL_TIME = 2
COL_MODE = 3
COL_SENDER = 4
COL_CONTENT = 5
COL_FILE = 6
COL_LINE = 7

EDITABLE_COLS = {COL_DATE, COL_TIME, COL_MODE, COL_CONTENT}


class MessageTableModel(QAbstractTableModel):
    """消息表格数据模型：存储解析后的消息列表，支持编辑"""

    _statusMsg = pyqtSignal(str)  # 状态栏消息信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: list[dict] = []  # 每条消息含所有字段
        self._step_sec: int = DEFAULT_STEP_SEC  # 当前默认间隔

    def setStepSec(self, sec: int):
        self._step_sec = sec

    def rowCount(self, parent=QModelIndex()):
        return len(self._messages)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        m = self._messages[row]

        if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.EditRole:
            if col == COL_IDX:
                return row + 1
            elif col == COL_DATE:
                return ms_to_date_str(m.get("timestamp_ms", 0))
            elif col == COL_TIME:
                return ms_to_time_str(m.get("timestamp_ms", 0))
            elif col == COL_MODE:
                return "📱 线上" if m.get("mode") == "online" else "📝 线下"
            elif col == COL_SENDER:
                anchor = " ⚓" if m.get("_time_anchor") else ""
                return m.get("speaker_name", "") + (" (你)" if m.get("role") in ("user", "user_meta") else " (角色)") + anchor
            elif col == COL_CONTENT:
                return m.get("content", "")
            elif col == COL_FILE:
                return m.get("source_file", "")
            elif col == COL_LINE:
                return m.get("source_line", "")

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (COL_IDX, COL_LINE):
                return Qt.AlignmentFlag.AlignCenter
            elif col in (COL_DATE, COL_TIME, COL_MODE, COL_SENDER):
                return Qt.AlignmentFlag.AlignCenter

        elif role == Qt.ItemDataRole.BackgroundRole:
            mode = m.get("mode", "offline")
            if mode == "online":
                return QColor(230, 255, 230)  # 浅绿=线上
            else:
                return QColor(255, 255, 230)  # 浅黄=线下

        elif role == Qt.ItemDataRole.FontRole:
            if col == COL_SENDER and m.get("_time_anchor"):
                f = QFont()
                f.setBold(True)
                return f

        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False
        row = index.row()
        col = index.column()
        m = self._messages[row]

        if col == COL_DATE:
            date_str = value.toString("yyyy-MM-dd") if hasattr(value, "toString") else str(value)
            time_str = ms_to_time_str(m.get("timestamp_ms", 0))
            try:
                dt = datetime.fromisoformat(f"{date_str}T{time_str}")
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=CST)
                m["timestamp_ms"] = int(dt.timestamp() * 1000)
                m["_time_anchor"] = True
                self.dataChanged.emit(index, index)
                self._emitRowChanged(row)
                return True
            except Exception:
                return False

        elif col == COL_TIME:
            time_str = value.toString("HH:mm") if hasattr(value, "toString") else str(value)
            date_str = ms_to_date_str(m.get("timestamp_ms", 0))
            try:
                dt = datetime.fromisoformat(f"{date_str}T{time_str}")
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=CST)
                m["timestamp_ms"] = int(dt.timestamp() * 1000)
                m["_time_anchor"] = True
                self.dataChanged.emit(index, index)
                self._emitRowChanged(row)
                return True
            except Exception:
                return False

        elif col == COL_MODE:
            # 模式列不允许直接编辑（由点击切换处理）
            return False

        elif col == COL_CONTENT:
            m["content"] = str(value)
            self.dataChanged.emit(index, index)
            return True

        return False

    def _emitRowChanged(self, row: int):
        """通知整行刷新（锚点状态变化）"""
        top_left = self.index(row, 0)
        bottom_right = self.index(row, len(COLUMNS) - 1)
        self.dataChanged.emit(top_left, bottom_right)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        col = index.column()
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if col in (COL_DATE, COL_TIME, COL_CONTENT):
            flags |= Qt.ItemFlag.ItemIsEditable
        # COL_MODE 不可编辑（点击切换），但仍可选中
        return flags

    def toggleMode(self, row: int):
        """点击切换模式"""
        m = self._messages[row]
        m["mode"] = "online" if m.get("mode") != "online" else "offline"
        m["is_online"] = (m["mode"] == "online")
        self._emitRowChanged(row)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    # ── 数据操作 ──

    def messages(self) -> list[dict]:
        return self._messages

    def setMessages(self, msgs: list[dict]):
        self.beginResetModel()
        self._messages = list(msgs)
        self.endResetModel()

    def _try_extract_time_from_content(self, content: str) -> int | None:
        """尝试从消息内容提取时间戳，返回 epoch ms 或 None"""
        # 模式1: "2026-04-23 01:16" 或 "2026/04/23 01:16"
        m = re.search(r'(\d{4})[-/](\d{2})[-/](\d{2})\s+(\d{2}):(\d{2})', content)
        if m:
            try:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                              int(m.group(4)), int(m.group(5)), tzinfo=CST)
                return int(dt.timestamp() * 1000)
            except Exception:
                pass
        # 模式2: "01:16" (从文件名已知日期)
        m2 = re.search(r'(?:^|\s)(\d{2}):(\d{2})(?:\s|$)', content)
        if m2:
            return None  # 仅时间不完整，返回 None 由调用方补日期
        return None

    def addMessages(self, msgs: list[dict], file_date_hint: str = ""):
        """追加消息并智能分配时间"""
        if not msgs:
            return
        self.beginInsertRows(QModelIndex(), len(self._messages),
                             len(self._messages) + len(msgs) - 1)

        # 确定起始时间
        if self._messages:
            last_ts = self._messages[-1].get("timestamp_ms", 0)
        else:
            last_ts = int(datetime.now(CST).timestamp() * 1000)

        # 日期回退：如果文件日期提示早于最后一条消息日期，使用文件日期
        if file_date_hint:
            try:
                hint_dt = datetime.fromisoformat(file_date_hint)
                hint_dt = hint_dt.replace(tzinfo=CST)
                hint_ms = int(hint_dt.timestamp() * 1000)
                if last_ts == 0 or hint_ms > last_ts:
                    last_ts = hint_ms
            except Exception:
                pass

        carry = last_ts
        for m in msgs:
            content = m.get("content", "")
            ts = m.get("timestamp_ms", 0)

            # 智能提取：内容中显式时间戳优先
            extracted = self._try_extract_time_from_content(content)
            if extracted:
                carry = extracted
                m["timestamp_ms"] = carry
                m["_time_anchor"] = True
            elif ts and ts > 0:
                carry = ts
                m["timestamp_ms"] = carry
                m["_time_anchor"] = True
            else:
                carry = carry + self._step_sec * 1000
                m["timestamp_ms"] = carry
            self._messages.append(m)

        self.endInsertRows()

    def removeRows(self, rows: list[int]):
        """删除指定行（倒序删除避免索引偏移）"""
        for row in sorted(rows, reverse=True):
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._messages[row]
            self.endRemoveRows()

    def getSelectedMessages(self, indexes: list[QModelIndex]) -> list[dict]:
        """从选中索引去重提取消息"""
        rows = sorted(set(idx.row() for idx in indexes if idx.isValid()))
        return [self._messages[r] for r in rows], rows

    def uniformDistributeTime(self, rows: list[int]):
        """均匀分配时间：首尾不变，中间线性插值"""
        if len(rows) < 2:
            return
        first_ts = self._messages[rows[0]].get("timestamp_ms", 0)
        last_ts = self._messages[rows[-1]].get("timestamp_ms", 0)
        if first_ts == 0 or last_ts == 0 or last_ts <= first_ts:
            return

        n = len(rows) - 1
        total_span = last_ts - first_ts
        step = total_span // n

        for i, row in enumerate(rows):
            if i == 0 or i == n:
                continue
            self._messages[row]["timestamp_ms"] = first_ts + step * i

        top_left = self.index(rows[0], 0)
        bottom_right = self.index(rows[-1], len(COLUMNS) - 1)
        self.dataChanged.emit(top_left, bottom_right)

    def redistributeBetweenAnchors(self):
        """
        全量智能重排：找到所有锚点（含首尾），锚点之间均匀插值。
        没有锚点的段落：按 step_sec 匀速递推。
        """
        if len(self._messages) < 2:
            return

        # 收集所有锚点行号（包括手动修改时间后自动标记的）
        anchors = []
        for i, m in enumerate(self._messages):
            if m.get("_time_anchor") or m.get("timestamp_ms", 0) == 0:
                pass
            if m.get("_time_anchor"):
                anchors.append(i)

        # 确保首尾为隐式锚点
        if not anchors or anchors[0] != 0:
            anchors.insert(0, 0)
            self._messages[0]["_time_anchor"] = True
        if anchors[-1] != len(self._messages) - 1:
            anchors.append(len(self._messages) - 1)
            self._messages[-1]["_time_anchor"] = True

        # 逐段均匀分配
        for seg in range(len(anchors) - 1):
            a_start = anchors[seg]
            a_end = anchors[seg + 1]
            if a_end <= a_start + 1:
                continue
            first_ts = self._messages[a_start].get("timestamp_ms", 0)
            last_ts = self._messages[a_end].get("timestamp_ms", 0)
            if first_ts == 0 or last_ts == 0 or last_ts <= first_ts:
                # 回退：用 step 递推
                carry = first_ts if first_ts else int(datetime.now(CST).timestamp() * 1000)
                for r in range(a_start + 1, a_end):
                    carry = carry + self._step_sec * 1000
                    self._messages[r]["timestamp_ms"] = carry
                continue

            span = last_ts - first_ts
            n = a_end - a_start
            step = span // n
            for i, r in enumerate(range(a_start + 1, a_end)):
                self._messages[r]["timestamp_ms"] = first_ts + step * (i + 1)

        # 全量刷新
        top_left = self.index(0, 0)
        bottom_right = self.index(len(self._messages) - 1, len(COLUMNS) - 1)
        self.dataChanged.emit(top_left, bottom_right)

    def clearAnchors(self, rows: list[int] = None):
        """清除锚点标记"""
        targets = rows if rows else range(len(self._messages))
        for r in targets:
            self._messages[r].pop("_time_anchor", None)
        if rows:
            top_left = self.index(rows[0], 0)
            bottom_right = self.index(rows[-1], len(COLUMNS) - 1)
            self.dataChanged.emit(top_left, bottom_right)

    def batchSetMode(self, rows: list[int], mode: str):
        """批量设置模式"""
        for row in rows:
            self._messages[row]["mode"] = mode
            self._messages[row]["is_online"] = (mode == "online")
        if rows:
            top_left = self.index(rows[0], 0)
            bottom_right = self.index(rows[-1], len(COLUMNS) - 1)
            self.dataChanged.emit(top_left, bottom_right)

    def toggleModeRow(self, row: int):
        """点击切换单行模式"""
        self.toggleMode(row)
        self._statusMsg.emit(f"行 {row+1}: {'📱 线上' if self._messages[row]['mode'] == 'online' else '📝 线下'}")


# ═══════════════════════════════════════════════════════════════
# 自定义Delegate
# ═══════════════════════════════════════════════════════════════

class DateDelegate(QStyledItemDelegate):
    """日期选择器（宽大弹窗）"""
    def createEditor(self, parent, option, index):
        editor = QDateEdit(parent)
        editor.setCalendarPopup(True)
        editor.setDisplayFormat("yyyy-MM-dd")
        editor.setMinimumWidth(140)
        editor.setStyleSheet("QDateEdit { font-size: 14px; padding: 4px; }")
        date_str = index.data(Qt.ItemDataRole.EditRole)
        if date_str:
            qdate = QDate.fromString(date_str, "yyyy-MM-dd")
            if qdate.isValid():
                editor.setDate(qdate)
        return editor

    def setEditorData(self, editor, index):
        date_str = index.data(Qt.ItemDataRole.EditRole)
        if date_str:
            qdate = QDate.fromString(date_str, "yyyy-MM-dd")
            if qdate.isValid():
                editor.setDate(qdate)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.date(), Qt.ItemDataRole.EditRole)


class TimeDelegate(QStyledItemDelegate):
    """时间编辑器（宽大）"""
    def createEditor(self, parent, option, index):
        editor = QTimeEdit(parent)
        editor.setDisplayFormat("HH:mm")
        editor.setMinimumWidth(100)
        editor.setStyleSheet("QTimeEdit { font-size: 14px; padding: 4px; }")
        time_str = index.data(Qt.ItemDataRole.EditRole)
        if time_str:
            qtime = QTime.fromString(time_str, "HH:mm")
            if qtime.isValid():
                editor.setTime(qtime)
        return editor

    def setEditorData(self, editor, index):
        time_str = index.data(Qt.ItemDataRole.EditRole)
        if time_str:
            qtime = QTime.fromString(time_str, "HH:mm")
            if qtime.isValid():
                editor.setTime(qtime)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.time(), Qt.ItemDataRole.EditRole)


# ═══════════════════════════════════════════════════════════════
# 合并预览对话框
# ═══════════════════════════════════════════════════════════════

class MergePreviewDialog(QDialog):
    """显示合并前后对比 — 接受预览文本或旧格式 dict"""

    def __init__(self, preview: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("合并预览")
        self.resize(600, 450)

        layout = QVBoxLayout(self)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("Consolas", 10))

        # 支持新格式 {"text": "..."} 或旧格式 {chat_before_count, ...}
        if "text" in preview:
            text.setText(preview["text"])
        else:
            chat_before = preview.get("chat_before_count", 0)
            chat_after = preview.get("chat_after_count", 0)
            tm_before_sessions = preview.get("tm_before_sessions", 0)
            tm_after_sessions = preview.get("tm_after_sessions", 0)
            tm_before_entries = preview.get("tm_before_entries", 0)
            tm_after_entries = preview.get("tm_after_entries", 0)

            lines = [
                "═" * 50,
                "  增量合并预览",
                "═" * 50,
                "",
                "📱 Chat (线上) 消息:",
                f"  合并前: {chat_before} 条",
                f"  合并后: {chat_after} 条",
                f"  新增:   {chat_after - chat_before} 条",
                "",
                "📝 TM (线下) 记录:",
                f"  合并前: {tm_before_sessions} sessions / {tm_before_entries} entries",
                f"  合并后: {tm_after_sessions} sessions / {tm_after_entries} entries",
                f"  新增:   {tm_after_sessions - tm_before_sessions} sessions / "
                f"{tm_after_entries - tm_before_entries} entries",
                "",
                "─" * 50,
                "  执行合并前将自动备份原文件为 .bak",
                "─" * 50,
            ]
            text.setText("\n".join(lines))
        layout.addWidget(text)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)


# ═══════════════════════════════════════════════════════════════
# 主窗口
# ═══════════════════════════════════════════════════════════════

class ImporterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("增量导入工具 — MD对话 → Chat/TM JSON 增量合并")
        self.resize(1280, 900)

        # ── 状态变量 ──
        self._chat_path: str = ""
        self._tm_path: str = ""
        self._existing_chat: dict = None
        self._existing_tm: dict = None
        self._pending_md_files: list[str] = []  # 待导入MD文件列表

        # ── 构建UI ──
        self._setup_statusbar()
        self._build_ui()

    # ── UI 构建 ─────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ─── 顶部：JSON 加载 + 身份编辑 ───
        json_group = QGroupBox("📂 现有 JSON 文件 & 身份信息")
        json_layout = QFormLayout(json_group)

        # Chat JSON
        chat_row = QHBoxLayout()
        self._chat_path_edit = QLineEdit()
        self._chat_path_edit.setReadOnly(True)
        self._chat_path_edit.setPlaceholderText("选择 chat_xxx.json ...")
        chat_row.addWidget(self._chat_path_edit)
        btn_chat = QPushButton("浏览...")
        btn_chat.clicked.connect(self._on_browse_chat)
        chat_row.addWidget(btn_chat)
        json_layout.addRow("Chat JSON:", chat_row)

        # TM JSON
        tm_row = QHBoxLayout()
        self._tm_path_edit = QLineEdit()
        self._tm_path_edit.setReadOnly(True)
        self._tm_path_edit.setPlaceholderText("选择 tm_xxx.json ...")
        tm_row.addWidget(self._tm_path_edit)
        btn_tm = QPushButton("浏览...")
        btn_tm.clicked.connect(self._on_browse_tm)
        tm_row.addWidget(btn_tm)
        json_layout.addRow("TM JSON:", tm_row)

        # 身份信息
        id_row1 = QHBoxLayout()
        self._char_name_edit = QLineEdit()
        self._char_name_edit.setPlaceholderText("角色名")
        id_row1.addWidget(QLabel("角色名:"))
        id_row1.addWidget(self._char_name_edit)
        self._char_id_edit = QLineEdit()
        self._char_id_edit.setPlaceholderText("角色ID")
        id_row1.addWidget(QLabel("角色ID:"))
        id_row1.addWidget(self._char_id_edit)
        json_layout.addRow("角色:", id_row1)

        id_row2 = QHBoxLayout()
        self._user_name_edit = QLineEdit()
        self._user_name_edit.setPlaceholderText("用户名")
        id_row2.addWidget(QLabel("用户名:"))
        id_row2.addWidget(self._user_name_edit)
        self._user_id_edit = QLineEdit()
        self._user_id_edit.setPlaceholderText("用户ID")
        id_row2.addWidget(QLabel("用户ID:"))
        id_row2.addWidget(self._user_id_edit)
        json_layout.addRow("用户:", id_row2)

        # 加载按钮
        btn_load = QPushButton("🔍 加载 JSON 文件")
        btn_load.setStyleSheet("QPushButton { font-weight: bold; padding: 6px 16px; }")
        btn_load.clicked.connect(self._on_load_json)
        json_layout.addRow("", btn_load)

        root.addWidget(json_group)

        # ─── 中部：MD 导入面板（可折叠） ───
        md_group = QWidget()
        md_outer = QVBoxLayout(md_group)
        md_outer.setContentsMargins(0, 0, 0, 0)
        md_outer.setSpacing(2)

        # 折叠切换按钮
        self._btn_toggle_md = QPushButton("📥 导入新的 MD 对话文件  ▲")
        self._btn_toggle_md.setStyleSheet(
            "QPushButton { text-align: left; font-weight: bold; padding: 6px 12px; "
            "background-color: #e8e8e8; border: 1px solid #ccc; border-radius: 4px; }"
        )
        self._btn_toggle_md.clicked.connect(self._on_toggle_md_panel)
        md_outer.addWidget(self._btn_toggle_md)

        # 可折叠内容容器
        self._md_panel_content = QWidget()
        md_inner = QVBoxLayout(self._md_panel_content)
        md_inner.setContentsMargins(4, 4, 4, 4)

        md_top = QHBoxLayout()
        btn_add_md = QPushButton("➕ 添加 MD 文件...")
        btn_add_md.clicked.connect(self._on_add_md_files)
        md_top.addWidget(btn_add_md)
        btn_clear_md = QPushButton("清空列表")
        btn_clear_md.clicked.connect(self._on_clear_md_list)
        md_top.addWidget(btn_clear_md)
        md_top.addStretch()

        self._step_spin = QSpinBox()
        self._step_spin.setRange(5, 3600)
        self._step_spin.setValue(DEFAULT_STEP_SEC)
        self._step_spin.setSuffix(" 秒间隔")
        self._step_spin.setToolTip("默认消息时间间隔（秒）")
        md_top.addWidget(QLabel("默认间隔:"))
        md_top.addWidget(self._step_spin)

        md_inner.addLayout(md_top)

        # MD 文件列表
        self._md_list = QListWidget()
        self._md_list.setMaximumHeight(100)
        self._md_list.setAlternatingRowColors(True)
        md_inner.addWidget(self._md_list)

        # 解析按钮
        btn_parse = QPushButton("🔄 解析 MD 文件")
        btn_parse.setStyleSheet("QPushButton { font-weight: bold; padding: 6px 16px; }")
        btn_parse.clicked.connect(self._on_parse_md)
        md_inner.addWidget(btn_parse)

        md_outer.addWidget(self._md_panel_content)
        self._md_panel_expanded = True  # 默认展开

        root.addWidget(md_group)

        # ─── 底部：消息表格 ───
        tbl_group = QGroupBox("📋 解析结果（双击单元格编辑，点击模式列切换 线上/线下）")
        tbl_layout = QVBoxLayout(tbl_group)

        self._table = QTableView()
        self._model = MessageTableModel(self)
        self._model._statusMsg.connect(self._status.showMessage)
        self._table.setModel(self._model)

        # 设置Delegate（模式列无需Delegate，点击即切换）
        self._table.setItemDelegateForColumn(COL_DATE, DateDelegate(self))
        self._table.setItemDelegateForColumn(COL_TIME, TimeDelegate(self))

        # 表格属性
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        self._table.clicked.connect(self._on_table_clicked)

        # 行高增大，便于看清和编辑
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.verticalHeader().setVisible(False)

        # 列宽（固定宽度，足够显示）
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(COL_IDX, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(COL_IDX, 40)
        hdr.setSectionResizeMode(COL_DATE, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(COL_DATE, 130)
        hdr.setSectionResizeMode(COL_TIME, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(COL_TIME, 80)
        hdr.setSectionResizeMode(COL_MODE, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(COL_MODE, 80)
        hdr.setSectionResizeMode(COL_SENDER, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(COL_SENDER, 160)
        hdr.setSectionResizeMode(COL_CONTENT, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(COL_FILE, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(COL_FILE, 140)
        hdr.setSectionResizeMode(COL_LINE, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(COL_LINE, 60)

        tbl_layout.addWidget(self._table)

        # 表格操作按钮行
        tbl_btns = QHBoxLayout()

        self._lbl_count = QLabel("共 0 条消息")
        tbl_btns.addWidget(self._lbl_count)
        tbl_btns.addStretch()

        btn_anchor_redist = QPushButton("⏱ 锚点间智能重排")
        btn_anchor_redist.setToolTip("找到所有手动修改过时间的锚点，在锚点之间均匀分配时间")
        btn_anchor_redist.clicked.connect(self._on_anchor_redistribute)
        tbl_btns.addWidget(btn_anchor_redist)

        btn_del_rows = QPushButton("🗑 删除选中行")
        btn_del_rows.clicked.connect(self._on_delete_rows)
        tbl_btns.addWidget(btn_del_rows)

        btn_merge_preview = QPushButton("👁 合并预览")
        btn_merge_preview.setStyleSheet("QPushButton { font-weight: bold; padding: 6px 14px; }")
        btn_merge_preview.clicked.connect(self._on_preview_merge)
        tbl_btns.addWidget(btn_merge_preview)

        btn_exec_merge = QPushButton("🚀 执行合并")
        btn_exec_merge.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 6px 20px; "
            "background-color: #4CAF50; color: white; border-radius: 4px; }"
        )
        btn_exec_merge.clicked.connect(self._on_execute_merge)
        tbl_btns.addWidget(btn_exec_merge)

        tbl_layout.addLayout(tbl_btns)

        # ── 合并模式选项行 ──
        merge_opts = QHBoxLayout()
        merge_opts.addStretch()
        self._chk_chat_merge = QCheckBox("📱 Chat 增量合并")
        self._chk_chat_merge.setChecked(True)
        self._chk_chat_merge.setToolTip("将线上消息增量合并到现有 Chat JSON 中")
        merge_opts.addWidget(self._chk_chat_merge)

        self._chk_tm_merge = QCheckBox("📝 TM 增量合并")
        self._chk_tm_merge.setChecked(True)
        self._chk_tm_merge.setToolTip("将线下消息增量合并到现有 TM JSON 的对应 session 中")
        merge_opts.addWidget(self._chk_tm_merge)

        self._chk_tm_standalone = QCheckBox("🆕 TM 独立生成")
        self._chk_tm_standalone.setChecked(False)
        self._chk_tm_standalone.setToolTip("生成全新的 TM JSON（不复用现有 session，仅包含本次解析的线下消息）")
        merge_opts.addWidget(self._chk_tm_standalone)

        # TM独立生成 ⇔ TM增量互斥
        self._chk_tm_merge.toggled.connect(lambda checked: self._chk_tm_standalone.setChecked(not checked and self._chk_tm_standalone.isChecked()))
        self._chk_tm_standalone.toggled.connect(lambda checked: self._chk_tm_merge.setChecked(not checked and self._chk_tm_merge.isChecked()))

        tbl_layout.addLayout(merge_opts)
        root.addWidget(tbl_group, stretch=1)

    def _setup_statusbar(self):
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("就绪 — 请先加载现有 JSON 文件")

    # ── 槽函数 ─────────────────────────────────

    def _on_browse_chat(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Chat JSON", "",
            "JSON Files (*.json);;All Files (*.*)"
        )
        if path:
            self._chat_path_edit.setText(path)
            self._chat_path = path

    def _on_browse_tm(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 TM JSON", "",
            "JSON Files (*.json);;All Files (*.*)"
        )
        if path:
            self._tm_path_edit.setText(path)
            self._tm_path = path

    def _on_load_json(self):
        chat_path = self._chat_path_edit.text().strip()
        tm_path = self._tm_path_edit.text().strip()

        if not chat_path or not os.path.exists(chat_path):
            QMessageBox.warning(self, "路径错误", "请先选择有效的 Chat JSON 文件")
            return
        if not tm_path or not os.path.exists(tm_path):
            QMessageBox.warning(self, "路径错误", "请先选择有效的 TM JSON 文件")
            return

        try:
            self._existing_chat = load_json(chat_path)
            self._existing_tm = load_json(tm_path)
            self._chat_path = chat_path
            self._tm_path = tm_path
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法加载 JSON:\n{e}")
            return

        # 自动填充身份信息
        char = self._existing_chat.get("character", {})
        self._char_name_edit.setText(char.get("name", ""))
        self._char_id_edit.setText(char.get("id", ""))

        user = self._existing_chat.get("user", {})
        self._user_name_edit.setText(user.get("name", ""))
        self._user_id_edit.setText(user.get("id", ""))

        chat_count = len(self._existing_chat.get("messages", []))
        tm_sessions = len(self._existing_tm.get("sessions", []))
        tm_entries = sum(len(s.get("entries", [])) for s in self._existing_tm.get("sessions", []))

        self._status.showMessage(
            f"已加载: {os.path.basename(chat_path)} ({chat_count} msgs) | "
            f"{os.path.basename(tm_path)} ({tm_sessions} sessions / {tm_entries} entries)"
        )

    def _on_add_md_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择 MD 对话文件", "",
            "Markdown Files (*.md);;All Files (*.*)"
        )
        for p in paths:
            if p not in self._pending_md_files:
                self._pending_md_files.append(p)
                item = QListWidgetItem(os.path.basename(p))
                item.setToolTip(p)
                self._md_list.addItem(item)
        self._status.showMessage(f"已添加 {len(paths)} 个 MD 文件（共 {len(self._pending_md_files)} 个待解析）")

    def _on_toggle_md_panel(self):
        """折叠/展开 MD 导入面板"""
        self._md_panel_expanded = not self._md_panel_expanded
        self._md_panel_content.setVisible(self._md_panel_expanded)
        arrow = "▲" if self._md_panel_expanded else "▼"
        self._btn_toggle_md.setText(f"📥 导入新的 MD 对话文件  {arrow}")

    def _on_clear_md_list(self):
        self._pending_md_files.clear()
        self._md_list.clear()
        self._status.showMessage("MD 文件列表已清空")

    def _on_parse_md(self):
        if not self._pending_md_files:
            QMessageBox.information(self, "提示", "请先添加 MD 文件")
            return

        all_msgs = []
        errors = []
        default_step = self._step_spin.value()

        for md_path in self._pending_md_files:
            try:
                msgs = parse_md_file(md_path)
                # 从文件名提取日期提示
                date_hint = extract_date_from_filename(os.path.basename(md_path))
                # 确保每条消息有 timestamp_ms（先设为0，后面自动分配）
                for m in msgs:
                    if "timestamp_ms" not in m:
                        m["timestamp_ms"] = 0
                # 按文件分批添加（带日期提示）
                self._model.addMessages(msgs, file_date_hint=date_hint)
                self._update_count_label()
            except Exception as e:
                errors.append(f"{os.path.basename(md_path)}: {e}")

        if errors:
            QMessageBox.warning(self, "解析警告", "\n".join(errors))

        if not self._model.rowCount():
            self._status.showMessage("解析完成：0 条消息")
            return

        self._pending_md_files.clear()
        self._md_list.clear()

        self._status.showMessage(f"解析完成：表格共 {self._model.rowCount()} 条消息")

    def _on_table_context_menu(self, pos):
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return

        selected_msgs, rows = self._model.getSelectedMessages(indexes)

        menu = QMenu(self)

        # 均匀分配时间
        if len(rows) >= 2:
            act_uniform = menu.addAction("⏱ 均匀分配时间（选中行）")
            act_uniform.triggered.connect(lambda: self._do_uniform_time(rows))

        menu.addSeparator()

        # 锚点操作
        act_anchor_redist = menu.addAction("⏱ 锚点间智能重排（全表）")
        act_anchor_redist.triggered.connect(self._on_anchor_redistribute)

        act_clear_anchors = menu.addAction("🧹 清除所有锚点标记")
        act_clear_anchors.triggered.connect(lambda: self._model.clearAnchors())

        if rows:
            act_mark_anchor = menu.addAction("⚓ 标记选中行为锚点")
            act_mark_anchor.triggered.connect(lambda: self._do_mark_anchors(rows))

        menu.addSeparator()

        # 批量设模式
        act_online = menu.addAction("📱 批量设为 线上 (online)")
        act_online.triggered.connect(lambda: self._model.batchSetMode(rows, "online"))

        act_offline = menu.addAction("📝 批量设为 线下 (offline)")
        act_offline.triggered.connect(lambda: self._model.batchSetMode(rows, "offline"))

        menu.addSeparator()

        # 删除
        act_del = menu.addAction("🗑 删除选中行")
        act_del.triggered.connect(lambda: self._on_delete_rows())

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _on_table_clicked(self, index: QModelIndex):
        """点击模式列 → 切换线上/线下"""
        if index.column() == COL_MODE:
            self._model.toggleModeRow(index.row())
            self._update_count_label()

    def _on_anchor_redistribute(self):
        """锚点间智能重排"""
        anchor_count = sum(1 for m in self._model.messages() if m.get("_time_anchor"))
        if anchor_count < 2:
            reply = QMessageBox.question(
                self, "确认重排",
                "当前锚点不足 2 个（需手动修改时间以标记锚点）。\n\n"
                "将把首尾行作为隐式锚点，对全表进行均匀重排。\n确定继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._model.redistributeBetweenAnchors()
        self._status.showMessage(
            f"锚点间智能重排完成（共 {self._model.rowCount()} 行，{anchor_count} 个锚点）"
        )

    def _do_mark_anchors(self, rows: list[int]):
        """将选中行标记为锚点"""
        for r in rows:
            self._model._messages[r]["_time_anchor"] = True
        top_left = self._model.index(rows[0], 0)
        bottom_right = self._model.index(rows[-1], len(COLUMNS) - 1)
        self._model.dataChanged.emit(top_left, bottom_right)
        self._status.showMessage(f"已标记 {len(rows)} 行为锚点")

    def _do_uniform_time(self, rows: list[int]):
        if len(rows) < 2:
            return
        self._model.uniformDistributeTime(rows)
        self._status.showMessage(f"已均匀分配 {len(rows)} 行的时间")

    def _on_delete_rows(self):
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return
        rows = sorted(set(idx.row() for idx in indexes), reverse=True)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定删除选中的 {len(rows)} 行吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._model.removeRows(rows)
            self._update_count_label()
            self._status.showMessage(f"已删除 {len(rows)} 行")

    def _update_count_label(self):
        online = sum(1 for m in self._model.messages() if m.get("mode") == "online")
        offline = self._model.rowCount() - online
        self._lbl_count.setText(
            f"共 {self._model.rowCount()} 条消息  (线上: {online} / 线下: {offline})"
        )

    def _on_preview_merge(self):
        msgs = self._model.messages()
        if not msgs:
            QMessageBox.information(self, "无数据", "表格中没有消息")
            return

        do_chat = self._chk_chat_merge.isChecked()
        do_tm_inc = self._chk_tm_merge.isChecked()
        do_tm_standalone = self._chk_tm_standalone.isChecked()

        if not do_chat and not do_tm_inc and not do_tm_standalone:
            QMessageBox.warning(self, "未选择", "请至少选择一种合并模式")
            return

        char_id = self._char_id_edit.text().strip() or None
        char_name = self._char_name_edit.text().strip() or None
        user_id = self._user_id_edit.text().strip() or None
        user_name = self._user_name_edit.text().strip() or None

        # dry-run
        try:
            if do_chat:
                if not self._existing_chat:
                    QMessageBox.warning(self, "未加载", "Chat 增量合并需要先加载 Chat JSON")
                    return
                merged_chat = merge_chat(self._existing_chat, msgs, char_id, char_name, user_id, user_name)
            else:
                merged_chat = None

            if do_tm_standalone:
                merged_tm = merge_tm_standalone(msgs, char_id or "", char_name or "", user_id or "", user_name or "")
            elif do_tm_inc:
                if not self._existing_tm:
                    QMessageBox.warning(self, "未加载", "TM 增量合并需要先加载 TM JSON")
                    return
                merged_tm = merge_tm(self._existing_tm, msgs, char_id, char_name, user_id, user_name)
            else:
                merged_tm = None
        except Exception as e:
            QMessageBox.critical(self, "合并错误", f"预览合并失败:\n{e}")
            return

        lines = ["═" * 50, "  合并预览", "═" * 50, ""]
        if merged_chat:
            chat_before = len(self._existing_chat.get("messages", [])) if self._existing_chat else 0
            chat_after = len(merged_chat.get("messages", []))
            lines.append("📱 Chat (线上) 增量合并:")
            lines.append(f"  合并前: {chat_before} 条 → 合并后: {chat_after} 条 (+{chat_after - chat_before} 条)")
        else:
            lines.append("📱 Chat: 跳过（未勾选）")

        lines.append("")
        if merged_tm:
            if do_tm_standalone:
                tm_before_sessions = 0
                tm_before_entries = 0
            else:
                tm_before_sessions = len(self._existing_tm.get("sessions", [])) if self._existing_tm else 0
                tm_before_entries = sum(len(s["entries"]) for s in self._existing_tm.get("sessions", [])) if self._existing_tm else 0
            tm_after_sessions = len(merged_tm.get("sessions", []))
            tm_after_entries = sum(len(s["entries"]) for s in merged_tm.get("sessions", []))
            mode_label = "独立生成" if do_tm_standalone else "增量合并"
            lines.append(f"📝 TM (线下) {mode_label}:")
            lines.append(f"  合并前: {tm_before_sessions} sessions / {tm_before_entries} entries")
            lines.append(f"  合并后: {tm_after_sessions} sessions / {tm_after_entries} entries")
            lines.append(f"  新增:   {tm_after_sessions - tm_before_sessions} sessions / {tm_after_entries - tm_before_entries} entries")
        else:
            lines.append("📝 TM: 跳过（未勾选）")

        lines.append("")
        lines.append("─" * 50)
        lines.append("  执行合并前将自动备份原文件为 .bak")
        lines.append("─" * 50)

        dlg = MergePreviewDialog({"text": "\n".join(lines)}, self)
        dlg.accept = lambda: None  # 空操作，只是预览
        dlg.exec()

    def _on_execute_merge(self):
        msgs = self._model.messages()
        if not msgs:
            QMessageBox.information(self, "无数据", "表格中没有消息")
            return

        do_chat = self._chk_chat_merge.isChecked()
        do_tm_inc = self._chk_tm_merge.isChecked()
        do_tm_standalone = self._chk_tm_standalone.isChecked()

        if not do_chat and not do_tm_inc and not do_tm_standalone:
            QMessageBox.warning(self, "未选择", "请至少选择一种合并模式")
            return

        char_id = self._char_id_edit.text().strip() or None
        char_name = self._char_name_edit.text().strip() or None
        user_id = self._user_id_edit.text().strip() or None
        user_name = self._user_name_edit.text().strip() or None

        # 确认对话框
        online_count = sum(1 for m in msgs if m.get("mode") == "online")
        offline_count = len(msgs) - online_count
        modes_desc = []
        if do_chat:
            modes_desc.append("📱 Chat 增量合并")
        if do_tm_inc:
            modes_desc.append("📝 TM 增量合并")
        if do_tm_standalone:
            modes_desc.append("🆕 TM 独立生成")
        modes_str = " + ".join(modes_desc)

        reply = QMessageBox.question(
            self, "确认合并",
            f"合并模式: {modes_str}\n\n"
            f"  📱 线上消息: {online_count} 条\n"
            f"  📝 线下消息: {offline_count} 条\n"
            f"  共 {len(msgs)} 条\n\n"
            f"原文件将自动备份为 .bak\n\n"
            f"确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 执行合并
        results = []
        try:
            if do_chat:
                if not self._existing_chat:
                    QMessageBox.warning(self, "未加载", "Chat 增量合并需要先加载 Chat JSON")
                    return
                merged_chat = merge_chat(self._existing_chat, msgs, char_id, char_name, user_id, user_name)
                chat_before = len(self._existing_chat.get("messages", []))
                chat_after = len(merged_chat.get("messages", []))
                bak = backup_file(self._chat_path)
                save_json(self._chat_path, merged_chat)
                self._existing_chat = merged_chat
                results.append(f"📱 Chat: {chat_before}→{chat_after} (+{chat_after - chat_before} 条)")
                results.append(f"   备份: {os.path.basename(bak)}")

            if do_tm_standalone:
                merged_tm = merge_tm_standalone(msgs, char_id or "", char_name or "", user_id or "", user_name or "")
                tm_after_sessions = len(merged_tm.get("sessions", []))
                tm_after_entries = sum(len(s["entries"]) for s in merged_tm.get("sessions", []))
                if self._tm_path:
                    bak = backup_file(self._tm_path)
                    results.append(f"   备份: {os.path.basename(bak)}")
                save_json(self._tm_path, merged_tm)
                self._existing_tm = merged_tm
                results.append(f"📝 TM 独立生成: {tm_after_sessions} sessions / {tm_after_entries} entries")
            elif do_tm_inc:
                if not self._existing_tm:
                    QMessageBox.warning(self, "未加载", "TM 增量合并需要先加载 TM JSON")
                    return
                merged_tm = merge_tm(self._existing_tm, msgs, char_id, char_name, user_id, user_name)
                tm_before_sessions = len(self._existing_tm.get("sessions", []))
                tm_before_entries = sum(len(s["entries"]) for s in self._existing_tm.get("sessions", []))
                tm_after_sessions = len(merged_tm.get("sessions", []))
                tm_after_entries = sum(len(s["entries"]) for s in merged_tm.get("sessions", []))
                bak = backup_file(self._tm_path)
                save_json(self._tm_path, merged_tm)
                self._existing_tm = merged_tm
                results.append(f"📝 TM 增量: {tm_before_sessions}→{tm_after_sessions} sessions | "
                               f"{tm_before_entries}→{tm_after_entries} entries")
                results.append(f"   备份: {os.path.basename(bak)}")
        except Exception as e:
            QMessageBox.critical(self, "合并失败", f"执行合并时出错:\n{e}")
            return

        # 不清空表格 — 保留工作区以便检查/修正错误
        self._update_count_label()
        QMessageBox.information(
            self, "合并完成",
            "合并成功！\n\n" + "\n".join(results) + "\n\n💡 工作区已保留，如需修正可继续编辑后再次合并。"
        )
        self._status.showMessage("合并完成 — " + modes_str + " | 表格已保留可继续编辑")

    # ── 键盘快捷键 ──
    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Delete):
            self._on_delete_rows()
        else:
            super().keyPressEvent(event)


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    # PyQt6: High DPI 默认启用，无需手动设置
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))

    # 全局字体
    font = app.font()
    font.setPointSize(10)
    app.setFont(font)

    window = ImporterApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
