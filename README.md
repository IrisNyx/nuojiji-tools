# nuojiji-tools

糯叽叽（Nuojiji）AI 对话记录的双向转换工具集。

> 两个 PyQt6 桌面工具，分别实现 **MD → JSON（增量导入）** 和 **JSON → MD（反向导出）**，均打包为独立 EXE，无需安装 Python 环境。

---

## 工具一览

### 📥 增量导入工具

**方向**：Markdown → JSON（将 MD 对话文件增量合并到糯叽叽 Chat/TM JSON）

| 功能 | 说明 |
|---|---|
| 加载 JSON | 浏览并加载现有 `chat_*.json` + `tm_*.json` |
| 导入 MD | 批量添加 `.md` 对话文件，自动解析 |
| 编辑表格 | 双击编辑日期、时间、模式（线上/线下）、内容 |
| 智能时间 | 锚点标记 + 锚点间均匀重排 |
| 增量合并 | 预览 → 自动 `.bak` 备份 → 写回 JSON |
| 模式识别 | 自动识别 `## 📱 线上` / `## 📝 线下` 标记（兼容反向工具） |

### 📤 JSON 转 MD 工具

**方向**：JSON → Markdown（将糯叽叽 Chat/TM JSON 反向导出为 `.md` 文件）

| 功能 | 说明 |
|---|---|
| 双 JSON 加载 | 同时加载 Chat + TM，按日期切分、按时间合并排序 |
| 日期选择器 | 左侧勾选日期，支持全选/全不选，批量导出 |
| 条目表格 | 右侧展示当日条目（时间 \| 来源 \| 角色 \| 内容），背景色区分线上线下 |
| 右键改日期 | 选中条目 → 右键 → 移至其他日期 |
| 预览 | 实时预览当前日期的 MD 输出 |
| 智能合并 | **5 分钟内同一发言人的连续消息自动合并为一个引用块** |
| 模式标记 | 来源切换时自动插入 `## 📱 线上` / `## 📝 线下` H2 标题 |

---

## 项目结构

```
nuojiji-tools/
├── README.md
├── .gitignore
├── 增量导入工具/
│   ├── importer_app.py          # PyQt6 主窗口
│   ├── md_parser.py             # MD 解析器（支持 ## 📱/📝 标记）
│   ├── merge_engine.py          # 合并引擎
│   ├── build_exe.bat            # 一键打包脚本
│   └── dist/
│       └── 增量导入工具.exe      # 独立可执行文件 (~36 MB)
└── 糯叽叽到md迁移/
    ├── json_parser.py            # Chat/TM JSON 解析引擎
    ├── md_writer.py              # MD 格式生成器（含5分钟合并）
    ├── json_to_md_app.py         # PyQt6 主窗口
    ├── build_exe.bat             # 一键打包脚本
    └── dist/
        └── JSON转MD工具.exe      # 独立可执行文件 (~36 MB)
```

---

## 快速开始

### 方式一：直接使用 EXE（推荐）

下载对应 `dist/` 目录下的 `.exe` 文件，双击运行。无需安装 Python 或任何依赖。

### 方式二：从源码运行

```bash
pip install PyQt6

# 增量导入工具
cd 增量导入工具
python importer_app.py

# JSON 转 MD 工具
cd 糯叽叽到md迁移
python json_to_md_app.py
```

### 自行打包

```bash
# 在对应目录下执行
build_exe.bat
```

---

## 双向兼容设计

两个工具通过统一的模式标记实现无缝衔接：

```
JSON → MD 导出               MD → JSON 导入
─────────────────────       ─────────────────────
## 📱 线上                  →  md_parser 识别为 ctx_mode = "online"
> **user**：09:12
> 内容...

## 📝 线下                  →  md_parser 识别为 ctx_mode = "offline"
> **char**：14:30
> （推开门）回来了...
```

- **导出端**（[`md_writer.py`](糯叽叽到md迁移/md_writer.py)）在 Chat/TM 来源切换处自动插入 H2 标记
- **导入端**（[`md_parser.py`](增量导入工具/md_parser.py)）解析时优先匹配 H2 标记，比原有的 `系统提示：进入终端模式` 正则更稳定可靠

---

## MD 格式约定

```markdown
---
created_at: 2026-04-23
title: 2026-04-23 对话记录
---

## 📱 线上

**user**：09:12
> 早安！

**char**：09:13
> 早啊
>
> 昨晚睡得好吗？
```

- 首行 `**发言人**：HH:MM`
- 内容行统一 `> ` 前缀
- 5 分钟内同一发言人连续消息合并，中间用空引用行（`>`）分隔
- YAML frontmatter 记录创建日期

---

## 技术栈

| 组件 | 技术 |
|---|---|
| GUI 框架 | PyQt6 |
| 打包 | PyInstaller (--onefile --windowed) |
| 运行环境 | Python 3.10+ / Windows 10+ |
| EXE 大小 | ~36 MB（含完整 Qt6 运行时） |

---

## 过滤规则（JSON → MD）

| 消息类型 | 处理方式 |
|---|---|
| 纯文本 | ✅ 保留 |
| 图片 (`selfie`/`scene`) | → `[图片: 自拍]` / `[图片: 场景]` |
| App 感知状态 (`appsense`) | ❌ 跳过 |
| 回复链 (`reply`) | ❌ 跳过 |
| 切歌 (`change_song`) | ❌ 跳过 |
| 撤回 (`recalled`) | ❌ 跳过 |
| 表情 (`sticker`) | → `[表情]` |

---

## License

MIT
