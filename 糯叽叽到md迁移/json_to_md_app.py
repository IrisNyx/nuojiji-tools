#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
糯叽叽 JSON → Markdown 反向转换工具 — PyQt6 桌面应用

功能:
  - 加载 Chat + TM 两个 JSON 文件
  - 自动按日期切分，同日内 Chat+TM 按时间合并排序
  - 左侧：日期勾选列表（可多选批量导出）
  - 右侧：当前选中日期的条目表格（时间|来源|角色|内容）
  - 右键菜单：移至其他日期、预览当前日期 MD
  - 批量导出：为勾选日期生成 YYYYMMDD_对话记录.md
  - 同一发言人 5 分钟内连续消息自动合并（在 md_writer.py 中实现）
"""

import sys, os

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QPushButton,
    QTableView, QHeaderView, QAbstractItemView, QFileDialog,
    QMessageBox, QStatusBar, QMenu, QSplitter,
    QListWidget, QListWidgetItem, QDialog, QDialogButtonBox,
    QComboBox, QTextEdit,
)
from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont

# ── 本地模块 ──
from json_parser import parse_and_group
from md_writer import generate_md, write_md_file

# ═══════════════════════════════════════════════════════════════
# 表格列定义
# ═══════════════════════════════════════════════════════════════

COLUMNS = ["#", "时间", "来源", "角色", "内容"]
COL_IDX = 0
COL_TIME = 1
COL_SOURCE = 2
COL_SPEAKER = 3
COL_CONTENT = 4


class EntryTableModel(QAbstractTableModel):
    """条目表格数据模型"""

    _statusMsg = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[dict] = []

    def setEntries(self, entries: list[dict]):
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()

    def entries(self) -> list[dict]:
        return self._entries

    def _indexOfEntry(self, entry: dict) -> int:
        """通过对象标识查找条目在列表中的位置"""
        for i, e in enumerate(self._entries):
            if e is entry:
                return i
        return -1

    def removeEntry(self, entry: dict) -> bool:
        """移除指定条目对象（通过 is 比较）"""
        idx = self._indexOfEntry(entry)
        if idx < 0:
            return False
        self.beginRemoveRows(QModelIndex(), idx, idx)
        self._entries.pop(idx)
        self.endRemoveRows()
        return True

    def rowCount(self, parent=QModelIndex()):
        return len(self._entries)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        e = self._entries[row]

        if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.EditRole:
            if col == COL_IDX:
                return row + 1
            elif col == COL_TIME:
                return e.get("time", "")
            elif col == COL_SOURCE:
                return e.get("source_label", "")
            elif col == COL_SPEAKER:
                return e.get("speaker", "")
            elif col == COL_CONTENT:
                return e.get("content", "")
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (COL_IDX, COL_TIME, COL_SOURCE, COL_SPEAKER):
                return Qt.AlignmentFlag.AlignCenter
        elif role == Qt.ItemDataRole.BackgroundRole:
            src = e.get("source", "")
            if src == "chat":
                return QColor(230, 255, 230)  # 浅绿=线上
            else:
                return QColor(255, 255, 230)  # 浅黄=线下

        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False
        row = index.row()
        col = index.column()
        e = self._entries[row]

        if col == COL_CONTENT:
            e["content"] = str(value)
            self.dataChanged.emit(index, index)
            return True
        return False

    def flags(self, index):
        f = super().flags(index)
        if index.column() == COL_CONTENT:
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def getSelectedEntries(self, indexes) -> tuple[list[dict], list[int]]:
        rows = sorted(set(idx.row() for idx in indexes))
        return [self._entries[r] for r in rows], rows


# ═══════════════════════════════════════════════════════════════
# "移至日期" 对话框
# ═══════════════════════════════════════════════════════════════

class MoveDateDialog(QDialog):
    """选择目标日期"""

    def __init__(self, dates: list[str], current_date: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("移至日期")
        self.setMinimumWidth(300)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"当前日期: {current_date}"))
        layout.addWidget(QLabel("选择目标日期:"))

        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.addItems(dates)
        idx = self._combo.findText(current_date)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        layout.addWidget(self._combo)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def selectedDate(self) -> str:
        return self._combo.currentText().strip()


# ═══════════════════════════════════════════════════════════════
# MD 预览对话框
# ═══════════════════════════════════════════════════════════════

class PreviewDialog(QDialog):
    """MD 文本预览"""

    def __init__(self, md_text: str, date_str: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"预览: {date_str}")
        self.resize(700, 500)

        layout = QVBoxLayout(self)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Microsoft YaHei", 10))
        self._text.setPlainText(md_text)
        layout.addWidget(self._text)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.close)
        btns.accepted.connect(self.close)
        layout.addWidget(btns)


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _format_date_item(date_str: str, count: int, chat_n: int, tm_n: int) -> str:
    """日期列表显示文本"""
    return f"{date_str}  [{count}条]  Chat{chat_n}/TM{tm_n}"


# ═══════════════════════════════════════════════════════════════
# 主窗口
# ═══════════════════════════════════════════════════════════════

class JsonToMdApp(QMainWindow):
    """JSON → MD 反向转换主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("糯叽叽 JSON → Markdown 反向转换工具")
        self.setMinimumSize(1100, 650)
        self.resize(1300, 780)

        # ── 状态数据 ──
        self._all_entries: list[dict] = []
        self._grouped: dict[str, list[dict]] = {}
        self._date_keys: list[str] = []
        self._selected_date: str = ""

        self._setup_ui()
        self._setup_statusbar()

    # ═══════════════════════════════════════════════════════════
    # UI 构建
    # ═══════════════════════════════════════════════════════════

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # ── 顶部：JSON 加载区 ──
        json_group = QGroupBox("📂 源文件加载")
        json_layout = QVBoxLayout(json_group)
        json_layout.setSpacing(4)

        # Chat JSON 行
        chat_row = QHBoxLayout()
        chat_row.addWidget(QLabel("Chat JSON:"))
        self._chat_path_edit = QLineEdit()
        self._chat_path_edit.setPlaceholderText("选择 chat_*.json 文件...")
        chat_row.addWidget(self._chat_path_edit, stretch=1)
        btn_browse_chat = QPushButton("浏览...")
        btn_browse_chat.clicked.connect(self._on_browse_chat)
        chat_row.addWidget(btn_browse_chat)
        json_layout.addLayout(chat_row)

        # TM JSON 行
        tm_row = QHBoxLayout()
        tm_row.addWidget(QLabel("TM JSON:"))
        self._tm_path_edit = QLineEdit()
        self._tm_path_edit.setPlaceholderText("选择 tm_*.json 文件...")
        tm_row.addWidget(self._tm_path_edit, stretch=1)
        btn_browse_tm = QPushButton("浏览...")
        btn_browse_tm.clicked.connect(self._on_browse_tm)
        tm_row.addWidget(btn_browse_tm)
        json_layout.addLayout(tm_row)

        # 加载按钮行
        load_row = QHBoxLayout()
        load_row.addStretch()
        btn_load = QPushButton("🔍 加载并解析 JSON")
        btn_load.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 8px 20px; "
            "background-color: #2196F3; color: white; border-radius: 4px; }"
        )
        btn_load.clicked.connect(self._on_load_json)
        load_row.addWidget(btn_load)
        json_layout.addLayout(load_row)

        root.addWidget(json_group)

        # ── 中部：日期列表 + 条目表格 (QSplitter) ──
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # -- 左侧：日期列表 --
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        left_layout.addWidget(QLabel("📅 日期列表（勾选 = 待导出）"))

        self._date_list = QListWidget()
        self._date_list.setAlternatingRowColors(True)
        self._date_list.currentItemChanged.connect(self._on_date_selected)
        left_layout.addWidget(self._date_list, stretch=1)

        # 全选/全不选
        sel_btns = QHBoxLayout()
        btn_sel_all = QPushButton("全选")
        btn_sel_all.clicked.connect(self._on_select_all)
        sel_btns.addWidget(btn_sel_all)
        btn_sel_none = QPushButton("全不选")
        btn_sel_none.clicked.connect(self._on_select_none)
        sel_btns.addWidget(btn_sel_none)
        left_layout.addLayout(sel_btns)

        self._lbl_date_summary = QLabel("")
        left_layout.addWidget(self._lbl_date_summary)

        splitter.addWidget(left_panel)

        # -- 右侧：表格 --
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        self._lbl_table_title = QLabel("选择左侧日期以查看条目")
        self._lbl_table_title.setStyleSheet("font-weight: bold; font-size: 12px;")
        right_layout.addWidget(self._lbl_table_title)

        self._table = QTableView()
        self._model = EntryTableModel(self)
        self._table.setModel(self._model)

        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)

        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.verticalHeader().setVisible(False)

        # 列宽
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(COL_IDX, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(COL_IDX, 40)
        hdr.setSectionResizeMode(COL_TIME, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(COL_TIME, 60)
        hdr.setSectionResizeMode(COL_SOURCE, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(COL_SOURCE, 65)
        hdr.setSectionResizeMode(COL_SPEAKER, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(COL_SPEAKER, 50)
        hdr.setSectionResizeMode(COL_CONTENT, QHeaderView.ResizeMode.Stretch)

        right_layout.addWidget(self._table, stretch=1)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, stretch=1)

        # ── 底部：操作按钮 ──
        btn_row = QHBoxLayout()

        self._lbl_total = QLabel("")
        btn_row.addWidget(self._lbl_total)
        btn_row.addStretch()

        btn_preview = QPushButton("👁 预览当前日期 MD")
        btn_preview.clicked.connect(self._on_preview_current)
        btn_row.addWidget(btn_preview)

        btn_export = QPushButton("🚀 批量导出勾选日期")
        btn_export.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 8px 24px; "
            "background-color: #4CAF50; color: white; border-radius: 4px; }"
        )
        btn_export.clicked.connect(self._on_export)
        btn_row.addWidget(btn_export)
        root.addLayout(btn_row)

    def _setup_statusbar(self):
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("就绪 — 请加载 Chat JSON 和 TM JSON 文件")
        # 连接模型信号
        self._model._statusMsg.connect(self._status.showMessage)

    # ═══════════════════════════════════════════════════════════
    # 槽函数
    # ═══════════════════════════════════════════════════════════

    def _on_browse_chat(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Chat JSON", "",
            "JSON Files (*.json);;All Files (*.*)"
        )
        if path:
            self._chat_path_edit.setText(path)

    def _on_browse_tm(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 TM JSON", "",
            "JSON Files (*.json);;All Files (*.*)"
        )
        if path:
            self._tm_path_edit.setText(path)

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
            all_entries, grouped = parse_and_group(chat_path, tm_path)
        except Exception as e:
            QMessageBox.critical(self, "解析失败", f"无法解析 JSON:\n{e}")
            return

        self._all_entries = all_entries
        self._grouped = grouped
        self._date_keys = list(grouped.keys())
        self._selected_date = ""

        # 填充日期列表
        self._date_list.blockSignals(True)
        self._date_list.clear()
        for date in self._date_keys:
            entries = grouped[date]
            chat_n = sum(1 for e in entries if e["source"] == "chat")
            tm_n = len(entries) - chat_n
            text = _format_date_item(date, len(entries), chat_n, tm_n)
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, date)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._date_list.addItem(item)
        self._date_list.blockSignals(False)

        self._update_date_summary()
        self._lbl_total.setText(
            f"共 {len(all_entries)} 条消息 / {len(grouped)} 个日期"
        )

        if self._date_list.count() > 0:
            self._date_list.setCurrentRow(0)

        self._status.showMessage(
            f"已加载: {os.path.basename(chat_path)} + {os.path.basename(tm_path)}  "
            f"→ {len(all_entries)} 条 / {len(grouped)} 天"
        )

    def _on_date_selected(self, current, previous):
        if current is None:
            self._model.setEntries([])
            self._lbl_table_title.setText("选择左侧日期以查看条目")
            return

        date = current.data(Qt.ItemDataRole.UserRole)
        self._selected_date = date
        entries = self._grouped.get(date, [])
        self._model.setEntries(entries)
        chat_n = sum(1 for e in entries if e["source"] == "chat")
        self._lbl_table_title.setText(
            f"📋 {date} — {len(entries)} 条消息  "
            f"(Chat {chat_n} / TM {len(entries) - chat_n})"
        )

    def _on_select_all(self):
        for i in range(self._date_list.count()):
            self._date_list.item(i).setCheckState(Qt.CheckState.Checked)
        self._update_date_summary()

    def _on_select_none(self):
        for i in range(self._date_list.count()):
            self._date_list.item(i).setCheckState(Qt.CheckState.Unchecked)
        self._update_date_summary()

    def _update_date_summary(self):
        total = self._date_list.count()
        checked = sum(
            1 for i in range(total)
            if self._date_list.item(i).checkState() == Qt.CheckState.Checked
        )
        self._lbl_date_summary.setText(f"已勾选: {checked} / {total} 天")

    def _on_table_context_menu(self, pos):
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return

        selected_entries, rows = self._model.getSelectedEntries(indexes)

        menu = QMenu(self)

        act_move = menu.addAction("📅 移至其他日期...")
        act_move.triggered.connect(lambda: self._on_move_to_date(selected_entries))

        menu.addSeparator()

        act_preview = menu.addAction("👁 预览当前日期 MD")
        act_preview.triggered.connect(self._on_preview_current)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _on_move_to_date(self, entries_to_move: list[dict]):
        """将选中的条目移至另一日期"""
        if not entries_to_move:
            return

        dlg = MoveDateDialog(self._date_keys, self._selected_date, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_date = dlg.selectedDate()
        if not new_date or new_date == self._selected_date:
            return

        old_date = self._selected_date

        # ── 更新 _grouped ──
        # 从旧日期移除
        if old_date in self._grouped:
            old_list = self._grouped[old_date]
            # 用 is 比较确保移除的是同一个对象
            for e in list(entries_to_move):
                try:
                    old_list.remove(e)
                except ValueError:
                    pass  # 已被移除或不在列表中

            # 清理空组
            if not old_list:
                del self._grouped[old_date]

        # 加入新日期
        if new_date not in self._grouped:
            self._grouped[new_date] = []
        for e in entries_to_move:
            e["date"] = new_date
        self._grouped[new_date].extend(entries_to_move)

        # 重新排序新日期组
        self._grouped[new_date].sort(key=lambda e: e["timestamp_ms"])

        # ── 更新 _date_keys ──
        self._date_keys = sorted(self._grouped.keys())

        # ── 刷新 UI ──
        # 从表格模型中移除（如果当前显示的是旧日期）
        for e in entries_to_move:
            self._model.removeEntry(e)

        # 刷新左侧日期列表
        self._refresh_date_list()

        self._status.showMessage(
            f"已移动 {len(entries_to_move)} 条: {old_date} → {new_date}"
        )

    def _refresh_date_list(self):
        """根据 _grouped 重建左侧日期列表，保留勾选状态"""
        old_checks = {}
        for i in range(self._date_list.count()):
            item = self._date_list.item(i)
            d = item.data(Qt.ItemDataRole.UserRole)
            old_checks[d] = item.checkState()

        self._date_list.blockSignals(True)
        self._date_list.clear()

        for date in self._date_keys:
            entries = self._grouped.get(date, [])
            chat_n = sum(1 for e in entries if e["source"] == "chat")
            tm_n = len(entries) - chat_n
            text = _format_date_item(date, len(entries), chat_n, tm_n)
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, date)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # 恢复勾选状态（新日期默认勾选）
            check = old_checks.get(date, Qt.CheckState.Checked)
            item.setCheckState(check)
            self._date_list.addItem(item)

        self._date_list.blockSignals(False)
        self._update_date_summary()

        # 尝试保持当前日期选中
        if self._selected_date in self._grouped:
            for i in range(self._date_list.count()):
                if self._date_list.item(i).data(Qt.ItemDataRole.UserRole) == self._selected_date:
                    self._date_list.setCurrentRow(i)
                    break

    def _on_preview_current(self):
        """预览当前日期 MD"""
        if not self._selected_date:
            QMessageBox.information(self, "提示", "请先在左侧选择日期")
            return

        entries = self._grouped.get(self._selected_date, [])
        if not entries:
            QMessageBox.information(self, "无数据", f"{self._selected_date} 没有条目")
            return

        try:
            md_text = generate_md(entries, self._selected_date)
        except Exception as e:
            QMessageBox.critical(self, "生成失败", f"生成 MD 预览失败:\n{e}")
            return

        dlg = PreviewDialog(md_text, self._selected_date, self)
        dlg.exec()

    def _on_export(self):
        """批量导出勾选日期"""
        selected_dates = []
        for i in range(self._date_list.count()):
            item = self._date_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                date = item.data(Qt.ItemDataRole.UserRole)
                selected_dates.append(date)

        if not selected_dates:
            QMessageBox.information(self, "提示", "请至少勾选一个日期再导出")
            return

        output_dir = QFileDialog.getExistingDirectory(
            self, "选择 MD 输出目录", ""
        )
        if not output_dir:
            return

        total_entries = sum(
            len(self._grouped.get(d, [])) for d in selected_dates
        )
        reply = QMessageBox.question(
            self, "确认导出",
            f"将导出 {len(selected_dates)} 个日期、共 {total_entries} 条消息\n\n"
            f"输出目录: {output_dir}\n\n确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        results = []
        errors = []
        for date in selected_dates:
            entries = self._grouped.get(date, [])
            if not entries:
                continue
            try:
                path = write_md_file(entries, date, output_dir)
                results.append(f"✅ {os.path.basename(path)} ({len(entries)} 条)")
            except Exception as e:
                errors.append(f"❌ {date}: {e}")

        msg = "\n".join(results)
        if errors:
            msg += "\n\n⚠ 错误:\n" + "\n".join(errors)

        QMessageBox.information(
            self, "导出完成",
            f"成功导出 {len(results)} 个文件:\n\n{msg}"
        )
        self._status.showMessage(
            f"已导出 {len(results)} 个 MD 文件 → {output_dir}"
        )


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    font = app.font()
    font.setPointSize(10)
    app.setFont(font)

    window = JsonToMdApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
