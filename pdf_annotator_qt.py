# -*- coding: utf-8 -*-
"""PDF 批注工具 - Qt(PySide6) 版
功能：打开PDF · 翻页/缩放(Ctrl+滚轮) · 矩形/椭圆/箭头/直线/画笔/荧光笔/文字批注
     · 文字高亮(按词吸附，拖选PDF真实文字) · 颜色/线宽 · 撤销/清除本页 · 批注嵌入保存
依赖：pymupdf, PySide6
"""
import json
import math
import os
import shutil
import sys
import time

import pymupdf
from PySide6.QtCore import (QEvent, QMimeData, QPointF, QRectF, QSize,
                            Qt, QTimer, QThread, Signal, QUrl)
from PySide6.QtGui import (QAction, QBrush, QColor, QDrag, QFont, QIcon, QImage,
                           QImageReader, QKeySequence, QMouseEvent,
                           QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                           QShortcut, QTextCharFormat, QTextCursor, QTextDocument,
                           QTextImageFormat, QTextListFormat)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QColorDialog,
                               QComboBox,
                               QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QFrame, QGraphicsEllipseItem, QGraphicsItem,
                               QGraphicsLineItem, QGraphicsPathItem, QGraphicsPixmapItem,
                               QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsTextItem, QGraphicsView, QHBoxLayout, QInputDialog,
                               QKeySequenceEdit,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea,
                               QSpinBox, QSplitter, QStackedWidget, QStyle,
                               QPlainTextEdit, QStyledItemDelegate, QStyleFactory,
                               QTabBar, QTextBrowser, QToolBar,
                               QToolButton,
                               QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout,
                               QWidget)
from PySide6.QtWidgets import QDockWidget

# Markdown 阅读用 WebEngine；缺失时降级为纯文本显示（不阻塞程序启动）
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings
    HAS_WEBENGINE = True
except Exception:
    QWebEngineView = None
    QWebEngineSettings = None
    HAS_WEBENGINE = False

COLORS = [("红", "#e53935"), ("蓝", "#1e88e5"), ("绿", "#43a047"),
          ("黄", "#fdd835"), ("黑", "#212121")]
# 颜色按钮配置（可右键自定义）；存于用户目录，下次启动沿用
COLORS_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_colors.json")


def colors_load():
    """读取自定义颜色板。返回 [[名称, #rrggbb], ...]；无/损坏时用默认 COLORS。"""
    try:
        with open(COLORS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        lst = d.get("colors", [])
        if (isinstance(lst, list) and len(lst) == len(COLORS)
                and all(isinstance(x, list) and len(x) == 2
                        and isinstance(x[0], str) and isinstance(x[1], str)
                        and QColor(x[1]).isValid() for x in lst)):
            return [[x[0], QColor(x[1]).name()] for x in lst]
    except Exception:
        pass
    return [[n, h] for n, h in COLORS]


def colors_save(lst):
    try:
        with open(COLORS_FILE, "w", encoding="utf-8") as f:
            json.dump({"colors": lst}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


WIDTHS = [("细 1pt", 1.0), ("中 2pt", 2.0), ("粗 3.5pt", 3.5),
          ("特粗 5pt", 5.0), ("加粗 8pt", 8.0), ("最粗 12pt", 12.0)]
RENDER_SCALE = 3.0  # 内部渲染倍数：直接以 用户倍率×RENDER_SCALE×屏幕dpr 高分辨率渲染，不降采样
TOOLS = [("查看/拖动", "view"), ("选择文字", "selecttext"), ("选择批注", "selanno"),
         ("文字", "text"), ("文字高亮", "texthl")]
# 画笔类工具合并为工具栏上一个"画笔"按钮：左键用当前画笔，右键弹菜单切换
SHAPE_ITEMS = [("画笔", "pen"), ("荧光笔", "highlight"), ("矩形", "rect"),
               ("椭圆", "oval"), ("直线", "line"), ("箭头", "arrow")]
SHAPE_VALS = {v for _, v in SHAPE_ITEMS}
SHAPE_NAMES = {v: n for n, v in SHAPE_ITEMS}
TEXT_FS = 10
HL_W = 10

import re as _re
LEADER_DOTS_RE = _re.compile(r'^[\.\s]+$')          # 目录点连线: . / ... / 一长串点
LEADER_LINE_RE = _re.compile(r'^[\.\-–—_\s]+$')     # 点/横线/下划线 组成的连线
PAGE_NUM_RE = _re.compile(r'^\d{1,4}$')            # 独立页码

# ---------------- 最近打开历史（PDF管理） ----------------
HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_history.json")
HISTORY_MAX = 12


def history_load():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        lst = d.get("recent", [])
        # 只保留仍存在的文件
        return [p for p in lst if isinstance(p, str) and os.path.isfile(p)]
    except Exception:
        return []


def history_save(lst):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"recent": lst[:HISTORY_MAX]}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def history_add(path):
    lst = history_load()
    if path in lst:
        lst.remove(path)
    lst.insert(0, path)
    history_save(lst[:HISTORY_MAX])


def history_remove(path):
    lst = history_load()
    if path in lst:
        lst.remove(path)
        history_save(lst)


def history_clear():
    history_save([])


# ---------------- 文件夹收藏（左侧PDF管理） ----------------
FOLDERS_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_folders.json")


def folders_load():
    """读取收藏的文件夹列表。"""
    try:
        with open(FOLDERS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return [p for p in d.get("folders", []) if isinstance(p, str)]
    except Exception:
        return []


def folders_save(lst):
    try:
        with open(FOLDERS_FILE, "w", encoding="utf-8") as f:
            json.dump({"folders": lst}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------- 快捷键设置 ----------------
SHORTCUTS_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_shortcuts.json")

# 可自定义的动作：key -> (显示名, 默认按键)
SHORTCUT_ACTIONS = [
    ("prev_page", ("上一页", "Left")),
    ("next_page", ("下一页", "Right")),
    ("fit_window", ("适应窗口", "Ctrl+0")),
    ("zoom_in", ("放大", "Ctrl++")),
    ("zoom_out", ("缩小", "Ctrl+-")),
    ("undo", ("撤销批注", "Ctrl+Z")),
    ("copy", ("复制选中文字", "Ctrl+C")),
    ("highlight", ("高亮选中文字", "H")),
    ("delete_anno", ("删除选中批注", "Delete")),
    ("open_pdf", ("打开PDF", "Ctrl+O")),
    ("save_pdf", ("保存批注PDF", "Ctrl+S")),
    ("search", ("搜索", "Ctrl+F")),
    ("next_hit", ("下一个命中", "F3")),
    ("prev_hit", ("上一个命中", "Shift+F3")),
    ("toggle_folders", ("显示/隐藏文件夹面板", "Ctrl+B")),
    ("switch_tool", ("切换工具（查看/选择文字/选择批注 循环）", "Tab")),
    ("tool_view", ("工具：查看/拖动", "1")),
    ("tool_selecttext", ("工具：选择文字", "2")),
    ("tool_selanno", ("工具：选择批注", "3")),
    ("tool_highlight", ("工具：荧光笔", "4")),
    ("tool_text", ("工具：文字", "5")),
    ("tool_texthl", ("工具：文字高亮", "6")),
    ("translate", ("翻译选中文字", "Ctrl+T")),
    ("notes", ("笔记面板", "Ctrl+J")),
]

# ---------------- 翻译设置 ----------------
TRANSLATE_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_translate.json")

# 语言代码：Google 与 百度 通用/相近，百度用 zh/en/jp/kor/fra 等短码
TRANSLATE_LANGS = [("中文", "zh"), ("英语", "en"), ("日语", "jp"),
                   ("韩语", "kor"), ("德语", "de"), ("法语", "fra"),
                   ("俄语", "ru")]
LANG_ALIAS = {"zh": "zh-CN", "en": "en", "jp": "ja", "kor": "ko",
              "de": "de", "fra": "fr", "ru": "ru"}


def translate_load():
    """读取翻译设置：引擎、目标语言、百度密钥。"""
    d = {"engine": "google", "target": "zh", "appid": "", "appkey": ""}
    try:
        with open(TRANSLATE_FILE, "r", encoding="utf-8") as f:
            got = json.load(f)
        if isinstance(got, dict):
            d.update({k: got.get(k, d[k]) for k in d})
    except Exception:
        pass
    return d


def translate_save(cfg):
    try:
        with open(TRANSLATE_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def detect_lang(text):
    """粗略判断文本语言：返回 'zh' 或 'en'。有较多CJK字符即判为中文。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return "zh" if cjk > len(text) * 0.15 else "en"


def _http_get(url, timeout=8):
    """极简 GET（避免引入 requests 依赖）；返回 (状态码, 文本)。"""
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (PDFAnnoQt)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return r.status, raw.decode("utf-8")
    except Exception:
        return r.status, raw.decode("gbk", errors="replace")


def translate_text(text, cfg, src=None):
    """调用在线翻译。返回 (译文, 识别出的源语言)。失败抛异常。
    引擎：google（免Key，translate.googleapis.com）；baidu（需 appid/appkey）。"""
    text = text.strip()
    if not text:
        return "", ""
    src = src or detect_lang(text)
    tgt = cfg.get("target", "zh")
    if src == tgt:
        # 源语言与目标一致时自动反向翻译，避免"中译中"无意义
        tgt = "en" if src == "zh" else "zh"
    engine = cfg.get("engine", "google")
    if engine == "baidu":
        return _translate_baidu(text, cfg, src, tgt)
    return _translate_google(text, cfg, src, tgt)


def _translate_google(text, cfg, src, tgt):
    import urllib.parse
    sl = LANG_ALIAS.get(src, "auto")
    tl = LANG_ALIAS.get(tgt, "zh-CN")
    if sl == "auto":
        sl = "auto"
    q = urllib.parse.quote(text)
    url = ("https://translate.googleapis.com/translate_a/single"
           f"?client=gtx&sl={sl}&tl={tl}&dt=t&q={q}")
    _code, body = _http_get(url)
    data = json.loads(body)
    out = "".join(seg[0] for seg in data[0] if seg and seg[0])
    return out, src


def _translate_baidu(text, cfg, src, tgt):
    import hashlib
    import random
    import urllib.parse
    appid = (cfg.get("appid") or "").strip()
    key = (cfg.get("appkey") or "").strip()
    if not appid or not key:
        raise RuntimeError("尚未填写百度翻译 appid / 密钥（请到“设置→翻译设置”填写）")
    salt = str(random.randint(10000, 99999))
    sign = hashlib.md5((appid + text + salt + key).encode("utf-8")).hexdigest()
    params = urllib.parse.urlencode({
        "q": text, "from": src if src in ("zh", "en") else "auto",
        "to": tgt if tgt in ("zh", "en") else "zh", "appid": appid,
        "salt": salt, "sign": sign})
    _code, body = _http_get("https://fanyi-api.baidu.com/api/trans/vip/translate?" + params)
    data = json.loads(body)
    if "error_code" in data:
        raise RuntimeError(f"百度翻译错误 {data['error_code']}: "
                           f"{data.get('error_msg', '')}")
    out = "".join(item.get("dst", "") for item in data.get("trans_result", []))
    return out, src


class TranslateThread(QThread):
    """后台翻译线程：避免网络请求阻塞界面。"""
    done = Signal(str, str, str)    # (译文, 源语言, 错误信息)

    def __init__(self, text, cfg):
        super().__init__()
        self.text = text
        self.cfg = cfg

    def run(self):
        try:
            out, src = translate_text(self.text, self.cfg)
            self.done.emit(out, src, "")
        except Exception as e:
            self.done.emit("", "", str(e))


class TranslatePanel(QWidget):
    """翻译面板（放在右侧停靠窗，与搜索面板同一区域）：
    显示原文与译文，可复制译文、切换目标语言、再译。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.thread = None
        self.last_result = ""
        self.cfg = translate_load()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("目标语言:"))
        self.lang_combo = QComboBox()
        for name, code in TRANSLATE_LANGS:
            self.lang_combo.addItem(name, code)
        codes = [c for _n, c in TRANSLATE_LANGS]
        self.lang_combo.setCurrentIndex(
            codes.index(self.cfg.get("target", "zh"))
            if self.cfg.get("target") in codes else 0)
        self.lang_combo.currentIndexChanged.connect(self.retranslate)
        bar.addWidget(self.lang_combo, 1)
        lay.addLayout(bar)

        self.engine_label = QLabel()
        self.engine_label.setStyleSheet("color:#666; font-size:12px;")
        lay.addWidget(self.engine_label)

        lay.addWidget(QLabel("原文（可手动修改后再译）:"))
        self.src_edit = QPlainTextEdit()
        self.src_edit.setPlaceholderText("选中 PDF 文字自动填入；也可直接在此修改…")
        self.src_edit.setMaximumHeight(110)
        lay.addWidget(self.src_edit)

        lay.addWidget(QLabel("译文:"))
        self.out_edit = QPlainTextEdit()
        self.out_edit.setReadOnly(True)
        lay.addWidget(self.out_edit, 1)

        row = QHBoxLayout()
        self.retranslate_btn = QPushButton("重新翻译")
        self.retranslate_btn.clicked.connect(self.retranslate)
        self.copy_btn = QPushButton("复制译文")
        self.copy_btn.clicked.connect(self.copy_out)
        self.settings_btn = QPushButton("翻译设置…")
        self.settings_btn.clicked.connect(self.open_settings)
        row.addWidget(self.retranslate_btn)
        row.addWidget(self.copy_btn)
        row.addWidget(self.settings_btn)
        lay.addLayout(row)

        self.info = QLabel("请先用'选择文字'工具选中要翻译的内容")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color:#666; font-size:12px;")
        lay.addWidget(self.info)

        self.refresh_engine_label()

    def refresh_engine_label(self):
        eng = self.cfg.get("engine", "google")
        self.engine_label.setText("引擎：" + ("Google" if eng == "google"
                                           else "百度翻译"))

    def open_settings(self):
        dlg = TranslateSettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            translate_save(dlg.result_cfg())
            self.retranslate()

    def set_text(self, text):
        """设置待翻译文本并立即翻译（重复选中同一段则只聚焦不重译）。"""
        self.text = text
        self.src_edit.setPlainText(text)
        self.retranslate()

    def retranslate(self):
        # 以原文框最新内容为准（用户可能手动增删改正过文字）
        self.text = self.src_edit.toPlainText()
        self.cfg = translate_load()
        self.cfg["target"] = self.lang_combo.currentData()
        translate_save(self.cfg)   # 记住用户选择的目标语言
        self.refresh_engine_label()
        self.out_edit.clear()
        self.copy_btn.setEnabled(False)
        if not self.text.strip():
            self.info.setText("请先用'选择文字'工具选中要翻译的内容")
            return
        self.info.setText("翻译中…")
        # 上个请求还在跑就先等它结束，避免译文错位
        if self.thread and self.thread.isRunning():
            self.thread.wait(300)
        self.thread = TranslateThread(self.text, self.cfg)
        self.thread.done.connect(self.on_done)
        self.thread.start()

    def on_done(self, out, src, err):
        if err:
            self.info.setText(f"翻译失败：{err}\n\nTip：Google 接口在国内可能需代理；"
                              "也可点“翻译设置…”改用百度翻译并填写密钥。")
            return
        self.last_result = out
        self.out_edit.setPlainText(out)
        self.info.setText(f"原语言：{src} → 目标：{self.lang_combo.currentText()}"
                          f"（共 {len(out)} 字符）")
        self.copy_btn.setEnabled(True)

    def copy_out(self):
        if self.last_result:
            QApplication.clipboard().setText(self.last_result)
            self.info.setText("译文已复制到剪贴板")


class TranslateSettingsDialog(QDialog):
    """翻译设置：选择引擎、目标语言、填写百度密钥。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("翻译设置")
        self.setMinimumWidth(420)
        self.cfg = translate_load()

        form = QFormLayout(self)
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("Google（免 Key，可能需代理）", "google")
        self.engine_combo.addItem("百度翻译（需 appid / 密钥）", "baidu")
        self.engine_combo.setCurrentIndex(
            1 if self.cfg.get("engine") == "baidu" else 0)
        form.addRow("翻译引擎", self.engine_combo)

        self.target_combo = QComboBox()
        for name, code in TRANSLATE_LANGS:
            self.target_combo.addItem(name, code)
        codes = [c for _n, c in TRANSLATE_LANGS]
        self.target_combo.setCurrentIndex(
            codes.index(self.cfg.get("target", "zh"))
            if self.cfg.get("target") in codes else 0)
        form.addRow("默认目标语言", self.target_combo)

        self.appid_edit = QLineEdit(self.cfg.get("appid", ""))
        self.appkey_edit = QLineEdit(self.cfg.get("appkey", ""))
        self.appkey_edit.setEchoMode(QLineEdit.Password)
        form.addRow("百度 appid", self.appid_edit)
        form.addRow("百度密钥", self.appkey_edit)

        tip = QLabel("百度翻译密钥申请：fanyi-api.baidu.com → 管理控制台 → 开发者信息。\n"
                     "Google 免 Key 接口在国内网络可能不可用，此时请改用百度。")
        tip.setWordWrap(True)
        form.addRow(tip)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def result_cfg(self):
        return {"engine": self.engine_combo.currentData(),
                "target": self.target_combo.currentData(),
                "appid": self.appid_edit.text().strip(),
                "appkey": self.appkey_edit.text().strip()}


def shortcuts_load():
    """读取用户自定义快捷键；返回 {key: 按键串}，未自定义的用默认值补齐。"""
    d = {}
    try:
        with open(SHORTCUTS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    out = {}
    for key, (_name, default) in SHORTCUT_ACTIONS:
        v = d.get(key, default)
        out[key] = v if isinstance(v, str) else default
    return out


def shortcuts_save(cfg):
    try:
        with open(SHORTCUTS_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class NoteSettingsDialog(QDialog):
    """笔记设置：存储目录、图片目录、默认导出格式。
    改目录后可一键迁移旧数据到新位置。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("笔记设置")
        self.setMinimumWidth(520)
        self.cfg = notes_cfg_load()

        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)

        # 存储目录
        row1 = QHBoxLayout()
        self.dir_edit = QLineEdit(self.cfg.get("storage_dir", ""))
        self.dir_edit.setPlaceholderText("空=用户目录下（默认）")
        btn_pick = QPushButton("浏览…")
        btn_pick.clicked.connect(self._pick_dir)
        row1.addWidget(self.dir_edit, 1)
        row1.addWidget(btn_pick)
        form.addRow("笔记数据存储目录", row1)

        # 图片目录
        row2 = QHBoxLayout()
        self.img_edit = QLineEdit(self.cfg.get("img_dir", ""))
        self.img_edit.setPlaceholderText("空=用户目录下（默认）")
        btn_pick2 = QPushButton("浏览…")
        btn_pick2.clicked.connect(self._pick_img)
        row2.addWidget(self.img_edit, 1)
        row2.addWidget(btn_pick2)
        form.addRow("笔记图片存储目录", row2)

        # 默认导出格式
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItem("HTML（保留图片/格式）", "html")
        self.fmt_combo.addItem("Markdown（纯文本）", "md")
        idx = self.fmt_combo.findData(self.cfg.get("export_fmt", "html"))
        self.fmt_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("默认导出格式", self.fmt_combo)

        lay.addLayout(form)

        tip = QLabel(
            "• 存储目录：保存 .pdf_anno_notes.json 的位置，方便备份或同步。\n"
            "• 图片目录：保存笔记里粘贴/插入的图片，便于单独管理。\n"
            "• 改目录后，可选择把现有笔记和图片一起迁到新位置。\n"
            "• 导出格式：HTML 保留图片和富文本格式；Markdown 只导出文字。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#666; font-size:12px;")
        lay.addWidget(tip)

        self.chk_migrate = QCheckBox("保存后把现有笔记和图片迁移到新位置")
        self.chk_migrate.setChecked(True)
        lay.addWidget(self.chk_migrate)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择笔记存储目录",
                                             self.dir_edit.text() or "")
        if d:
            self.dir_edit.setText(d)

    def _pick_img(self):
        d = QFileDialog.getExistingDirectory(self, "选择笔记图片目录",
                                             self.img_edit.text() or "")
        if d:
            self.img_edit.setText(d)

    def result_cfg(self):
        return {"storage_dir": self.dir_edit.text().strip(),
                "img_dir": self.img_edit.text().strip(),
                "export_fmt": self.fmt_combo.currentData()}


class ShortcutDialog(QDialog):
    """快捷键设置对话框：为每个动作指定按键，支持恢复默认。"""

    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.setWindowTitle("快捷键设置")
        self.resize(460, 560)
        self.setMinimumSize(380, 320)
        self.cfg = dict(cfg)
        self.edits = {}

        lay = QVBoxLayout(self)
        tip = QLabel("点击右侧输入框后按下想用的组合键即可修改。")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # 表单项放进滚动区：条目多、窗口小（或缩小时）可滚动查看，不裁切
        form = QFormLayout()
        for key, (name, default) in SHORTCUT_ACTIONS:
            ed = QKeySequenceEdit(QKeySequence(self.cfg.get(key, default)))
            ed.setClearButtonEnabled(True)
            self.edits[key] = ed
            form.addRow(name, ed)
        wrap = QWidget()
        wrap.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(wrap)
        scroll.setFrameShape(QFrame.NoFrame)
        lay.addWidget(scroll, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel
                                | QDialogButtonBox.RestoreDefaults)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.RestoreDefaults).clicked.connect(
            self.restore_defaults)
        lay.addWidget(btns)

    def restore_defaults(self):
        for key, (_name, default) in SHORTCUT_ACTIONS:
            self.edits[key].setKeySequence(QKeySequence(default))

    def result_cfg(self):
        cfg = {}
        for key, ed in self.edits.items():
            cfg[key] = ed.keySequence().toString(QKeySequence.PortableText)
        return cfg


class MdPanel(QWidget):
    """Markdown 阅读面板（放在中间画布区，与 PDF 画布用堆栈切换）。
    用 QWebEngineView 渲染 HTML；缺失 WebEngine 时降级为纯文本只读显示。
    提供缩放 / 搜索定位 / 复制全文 / 用系统程序打开。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = ""
        self._zoom = 1.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        bar.setSpacing(6)
        self.btn_back_pdf = QPushButton("◀ 返回PDF")
        self.btn_back_pdf.setToolTip("回到 PDF 画布（若已打开 PDF）")
        bar.addWidget(self.btn_back_pdf)
        self.title = QLabel("（未打开 Markdown）")
        self.title.setStyleSheet("color:#555;")
        bar.addWidget(self.title, 1)
        self.btn_zoom_out = QPushButton("－")
        self.btn_zoom_out.setToolTip("缩小 (Ctrl+-)")
        self.btn_zoom_in = QPushButton("＋")
        self.btn_zoom_in.setToolTip("放大 (Ctrl++)")
        self.btn_zoom_reset = QPushButton("100%")
        self.btn_zoom_reset.setToolTip("重置缩放 (Ctrl+0)")
        for b in (self.btn_zoom_out, self.btn_zoom_reset, self.btn_zoom_in):
            b.setFixedWidth(56)
            bar.addWidget(b)
        self.btn_copy_all = QPushButton("复制全文")
        self.btn_copy_all.setToolTip("把 Markdown 原文复制到剪贴板")
        bar.addWidget(self.btn_copy_all)
        self.btn_open_ext = QPushButton("外部打开")
        self.btn_open_ext.setToolTip("用系统默认程序打开该 Markdown 文件")
        bar.addWidget(self.btn_open_ext)
        self.btn_close_md = QPushButton("✕")
        self.btn_close_md.setToolTip("关闭当前 Markdown（返回 PDF）")
        self.btn_close_md.setFixedWidth(34)
        self.btn_close_md.setStyleSheet(
            "QPushButton{border:none; color:#888; font-weight:bold;}"
            "QPushButton:hover{background:#e53935; color:white; border-radius:3px;}")
        bar.addWidget(self.btn_close_md)
        lay.addLayout(bar)

        self.search_row = QHBoxLayout()
        self.search_row.setContentsMargins(8, 0, 8, 4)
        self.search_row.setSpacing(6)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("在文档内查找…（回车定位）")
        self.search_row.addWidget(self.search_input, 1)
        lay.addLayout(self.search_row)

        if HAS_WEBENGINE:
            self.web = QWebEngineView(self)
            try:
                s = self.web.settings()
                s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
                s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
            except Exception:
                pass
            lay.addWidget(self.web, 1)
        else:
            self.web = None
            self.text_view = QPlainTextEdit()
            self.text_view.setReadOnly(True)
            lay.addWidget(self.text_view, 1)

    # ---- 内容 ----
    def load_file(self, path):
        """载入并渲染 MD 文件。返回 (成功?, 错误信息)。"""
        self.path = path
        self.title.setText(f"📖 {os.path.basename(path)}")
        try:
            text = md_read_text(path)
        except Exception as e:
            return False, f"读取失败：{e}"
        if self.web is not None:
            html = md_to_html(text)
            base = QUrl.fromLocalFile(os.path.dirname(os.path.abspath(path)) + os.sep)
            self.web.setHtml(html, base)
        else:
            self.text_view.setPlainText(text)
        self._zoom = 1.0
        self.set_zoom(1.0)
        return True, ""

    def apply_zoom(self):
        """把当前缩放比例作用到视图。"""
        if self.web is not None:
            self.web.setZoomFactor(self._zoom)
        else:
            f = self.text_view.font()
            f.setPointSizeF(max(6.0, 11.0 * self._zoom))
            self.text_view.setFont(f)

    def set_zoom(self, z):
        self._zoom = max(0.4, min(4.0, z))
        self.apply_zoom()
        if hasattr(self, "btn_zoom_reset"):
            self.btn_zoom_reset.setText(f"{int(self._zoom * 100)}%")

    def zoom_by(self, d):
        self.set_zoom(self._zoom * (1.25 if d > 0 else 1 / 1.25))

    # ---- 搜索 ----
    def find_text(self, kw):
        """在当前文档中查找并定位（返回是否找到）。"""
        kw = (kw or "").strip()
        if not kw:
            return False
        if self.web is not None:
            self.web.findText(kw)
            return True
        if self.text_view.find(kw):
            return True
        # 从头再来一次
        self.text_view.moveCursor(QTextCursor.Start)
        return bool(self.text_view.find(kw))

    def copy_all_text(self):
        try:
            if self.web is not None:
                # 从 HTML 取纯文本较麻烦：直接读回源文件，保证与磁盘一致
                QApplication.clipboard().setText(md_read_text(self.path))
            else:
                QApplication.clipboard().setText(self.text_view.toPlainText())
            return True
        except Exception:
            return False

    def selected_text(self):
        """当前在 MD 里选中的文字（用于翻译）。取不到时由调用方走剪贴板兜底。"""
        if self.web is not None:
            try:
                return (self.web.page().selectedText() or "").strip()
            except Exception:
                return ""
        return self.text_view.textCursor().selectedText().strip()

    def clear_file(self):
        """关闭当前 Markdown，复位为空白态。"""
        self.path = ""
        self._zoom = 1.0
        self.title.setText("（未打开 Markdown）")
        self.search_input.clear()
        if self.web is not None:
            self.web.setHtml(
                "<body style='background:#fafafa;'></body>",
                QUrl("about:blank"))
        else:
            self.text_view.clear()
        self.btn_zoom_reset.setText("100%")


# ==================== 笔记面板 ====================
NOTES_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_notes.json")
NOTES_IMG_DIR = os.path.join(os.path.expanduser("~"), ".pdf_anno_notes_images")
NOTES_CFG_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_notes_cfg.json")


def notes_cfg_load():
    """读取笔记设置：存储目录、图片目录、默认导出格式。空=用默认。"""
    d = {"storage_dir": "", "img_dir": "", "export_fmt": "html"}
    try:
        with open(NOTES_CFG_FILE, "r", encoding="utf-8") as f:
            got = json.load(f)
        if isinstance(got, dict):
            d.update({k: got.get(k, d[k]) for k in d})
    except Exception:
        pass
    return d


def notes_cfg_save(cfg):
    try:
        with open(NOTES_CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def notes_file_path():
    """当前笔记数据文件路径（受设置控制，空=默认~/.pdf_anno_notes.json）。"""
    cfg = notes_cfg_load()
    d = cfg.get("storage_dir", "").strip()
    return os.path.join(d, ".pdf_anno_notes.json") if d else NOTES_FILE


def notes_img_dir_path():
    """当前笔记图片目录路径（受设置控制，空=默认）。"""
    cfg = notes_cfg_load()
    d = cfg.get("img_dir", "").strip()
    return d if d else NOTES_IMG_DIR


class NoteEditor(QTextBrowser):
    """笔记富文本编辑器。
    重写粘贴相关虚函数，让所有粘贴入口（Ctrl+V / 右键粘贴 / Shift+Insert /
    拖放）都能接收图片——默认的 QTextBrowser 粘贴会丢弃纯位图。
    图片操作：单击选中（Delete 删除）、右键菜单缩放/删除、
    Ctrl+滚轮缩放、按住拖动移动位置。"""

    IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
    MOVE_MIME = "application/x-pdfanno-note-imgmove"   # 内部图片拖动标记

    def __init__(self, parent=None):
        super().__init__(parent)
        self.note_panel = None   # NotePanel 创建后回填
        self.setAcceptDrops(True)
        self._press_img_pos = None   # 左键按下时图片的字符位置
        self._press_pt = None        # 按下时的视图坐标
        self._dragging = False       # 正在拖动图片
        self._move_src = None        # 拖动中图片的原字符位置
        # 选中文字后浮出的格式条（窗口子控件，跟随选区）
        self.fmt_bar = FloatingFormatBar(self)
        self.selectionChanged.connect(self._on_selection_changed)
        # 滚动时选区位置会偏移，直接收起格式条（滚完重新选即可）
        self.verticalScrollBar().valueChanged.connect(self.fmt_bar.hide_bar)

    def _on_selection_changed(self):
        self._refresh_fmt_bar()

    def _refresh_fmt_bar(self):
        """无选区（或选区只是图片）→ 隐藏格式条；否则浮到选区上方。"""
        cur = self.textCursor()
        fmt = cur.charFormat()
        if (cur.hasSelection() and cur.selectedText().strip()
                and not fmt.isImageFormat()):
            self.fmt_bar.refresh()
        else:
            self.fmt_bar.hide_bar()

    def focusOutEvent(self, e):
        # 焦点离开编辑器时收起格式条；但点格式条自身（如字号下拉）不算
        w = QApplication.focusWidget()
        if w is not None and (w is self.fmt_bar
                              or self.fmt_bar.isAncestorOf(w)):
            super().focusOutEvent(e)
            return
        self.fmt_bar.hide_bar()
        super().focusOutEvent(e)

    # ---- 粘贴 ----
    def _mime_image(self, source):
        """从 mime 数据提取 (类型, 载荷)：('img', QImage)/('file', 路径)/(None, None)。"""
        if source is None:
            return None, None
        if source.hasImage():
            img = QImage(source.imageData())
            if not img.isNull():
                return "img", img
        if hasattr(source, "urls"):
            for u in source.urls():
                p = u.toLocalFile()
                if p and p.lower().endswith(self.IMG_EXTS) and os.path.isfile(p):
                    return "file", p
        return None, None

    def canInsertFromMimeData(self, source):
        kind, _ = self._mime_image(source)
        if kind:
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        kind, payload = self._mime_image(source)
        if kind == "img" and self.note_panel is not None:
            self.note_panel.insert_qimage(payload)
            return
        if kind == "file" and self.note_panel is not None:
            self.note_panel._insert_image_path(payload)
            return
        super().insertFromMimeData(source)

    # ---- 图片定位 ----
    @staticmethod
    def _img_path(name):
        """图片资源名（file:///… URL）转本地文件路径。"""
        u = QUrl(name)
        return u.toLocalFile() if u.isLocalFile() else name

    def _image_fmt_at(self, pos):
        """字符位置 pos 处若是图片字符，返回其 QTextImageFormat，否则 None。
        注意不能用 QTextCursor.charFormat()：它返回光标前一个字符的格式。"""
        doc = self.document()
        if pos is None or pos < 0 or pos >= doc.characterCount() - 1:
            return None
        blk = doc.findBlock(pos)
        it = blk.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.contains(pos):
                fmt = frag.charFormat()
                return fmt.toImageFormat() if fmt.isImageFormat() else None
            it += 1
        return None

    def _hit_image(self, pt):
        """视图坐标 pt 处若是图片，返回 (字符位置, 格式)，否则 (None, None)。"""
        cur = self.cursorForPosition(pt)
        for p in (cur.position() - 1, cur.position()):
            fmt = self._image_fmt_at(p)
            if fmt is not None:
                return p, fmt
        return None, None

    def _image_size(self, fmt):
        """返回 (当前宽, 当前高, 原始宽, 原始高)。"""
        reader = QImageReader(self._img_path(fmt.name()))
        sz = reader.size()
        if sz.isValid():
            ow, oh = sz.width(), sz.height()
        else:
            img = self.document().resource(
                QTextDocument.ImageResource, QUrl(fmt.name()))
            if isinstance(img, QImage) and not img.isNull():
                ow, oh = img.width(), img.height()
            else:
                ow, oh = 100, 100
        w = fmt.width() if fmt.width() > 0 else ow
        h = fmt.height() if fmt.height() > 0 else oh
        return w, h, ow, oh

    # ---- 图片编辑 ----
    def _apply_img_size(self, pos, w, h):
        """设置 pos 处图片的显示尺寸。"""
        cur = QTextCursor(self.document())
        cur.setPosition(pos)
        cur.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
        fmt = cur.charFormat()
        if not fmt.isImageFormat():
            return False
        imgfmt = fmt.toImageFormat()
        imgfmt.setWidth(int(max(24, min(2400, w))))
        imgfmt.setHeight(int(max(24, min(2400, h))))
        cur.setCharFormat(imgfmt)
        return True

    def _scale_image(self, pos, factor):
        """按比例缩放 pos 处的图片。"""
        fmt = self._image_fmt_at(pos)
        if fmt is None:
            return False
        w, h, _, _ = self._image_size(fmt)
        return self._apply_img_size(pos, w * factor, h * factor)

    def _reset_image(self, pos):
        """恢复 pos 处图片的原始尺寸。"""
        fmt = self._image_fmt_at(pos)
        if fmt is None:
            return False
        _, _, ow, oh = self._image_size(fmt)
        return self._apply_img_size(pos, ow, oh)

    def _fit_width_image(self, pos):
        """把 pos 处图片等比缩放到编辑器可视宽度。"""
        fmt = self._image_fmt_at(pos)
        if fmt is None:
            return False
        w, h, _, _ = self._image_size(fmt)
        maxw = self.viewport().width() - 16
        if w <= 0 or maxw <= 0:
            return False
        ratio = maxw / w
        return self._apply_img_size(pos, w * ratio, h * ratio)

    def _delete_image(self, pos):
        """删除 pos 处的图片。"""
        cur = QTextCursor(self.document())
        cur.setPosition(pos)
        cur.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
        cur.removeSelectedText()
        return True

    def _move_image(self, src_pos, dst_pos):
        """把 src_pos 处的图片移动到 dst_pos（字符位置）之前，保留缩放比例。"""
        fmt = self._image_fmt_at(src_pos)
        if fmt is None:
            return False
        w, h, ow, oh = self._image_size(fmt)
        url = fmt.name()
        self._delete_image(src_pos)
        if dst_pos > src_pos:
            dst_pos -= 1
        dst_pos = max(0, min(dst_pos, self.document().characterCount() - 1))
        cur = QTextCursor(self.document())
        cur.setPosition(dst_pos)
        imgfmt = QTextImageFormat()
        imgfmt.setName(url)
        if (w, h) != (ow, oh):
            imgfmt.setWidth(int(w))
            imgfmt.setHeight(int(h))
        cur.insertImage(imgfmt)
        return True

    # ---- 鼠标交互 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            pt = e.position().toPoint()
            pos, fmt = self._hit_image(pt)
            if fmt is not None:
                # 选中该图片：Delete 可删除，按住可拖动
                cur = QTextCursor(self.document())
                cur.setPosition(pos)
                cur.movePosition(QTextCursor.NextCharacter,
                                 QTextCursor.KeepAnchor)
                self.setTextCursor(cur)
                self._press_img_pos = pos
                self._press_pt = pt
                self._dragging = False
                return
        self._press_img_pos = None
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (self._press_img_pos is not None and not self._dragging
                and e.buttons() & Qt.LeftButton):
            pt = e.position().toPoint()
            if (pt - self._press_pt).manhattanLength() > 6:
                self._dragging = True
                self._start_image_drag()
                return
        super().mouseMoveEvent(e)

    def _start_image_drag(self):
        """按住图片拖动：拖到编辑器内新位置则移动（不复制文件）。"""
        pos, self._press_img_pos = self._press_img_pos, None
        fmt = self._image_fmt_at(pos) if pos is not None else None
        if fmt is None:
            return
        mime = QMimeData()
        mime.setUrls([QUrl(fmt.name())])
        mime.setData(self.MOVE_MIME, b"1")
        drag = QDrag(self)
        drag.setMimeData(mime)
        pm = QPixmap(self._img_path(fmt.name()))
        if not pm.isNull():
            drag.setPixmap(pm.scaled(
                80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self._move_src = pos
        # 只允许 Copy 语义：避免拖到资源管理器时"移动"图片文件本体
        drag.exec(Qt.CopyAction)
        self._move_src = None

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if (md.hasImage() or md.hasUrls()
                or md.hasFormat(self.MOVE_MIME)):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dropEvent(self, e):
        md = e.mimeData()
        # 内部图片移动：在落点重插图片并删除原位置
        if md.hasFormat(self.MOVE_MIME) and self._move_src is not None:
            src = self._move_src
            self._move_src = None
            dst = self.cursorForPosition(e.position().toPoint()).position()
            self._move_image(src, dst)
            e.acceptProposedAction()
            return
        # 外部拖入图片/图片文件：按粘贴处理
        if md.hasImage() or md.hasUrls():
            self.insertFromMimeData(md)
            e.acceptProposedAction()
            return
        super().dropEvent(e)

    def wheelEvent(self, e):
        """Ctrl+滚轮：鼠标在图片上时缩放该图（每档 10%）。"""
        if e.modifiers() & Qt.ControlModifier:
            pos, fmt = self._hit_image(e.position().toPoint())
            if fmt is not None:
                self._scale_image(
                    pos, 1.1 if e.angleDelta().y() > 0 else 1 / 1.1)
                e.accept()
                return
        super().wheelEvent(e)

    def contextMenuEvent(self, e):
        """右键图片：缩放/删除菜单；其他位置走默认编辑菜单。"""
        pos, fmt = self._hit_image(e.pos())
        if fmt is None:
            super().contextMenuEvent(e)
            return
        w, h, _, _ = self._image_size(fmt)
        m = QMenu(self)
        m.addAction(f"放大 25%（当前 {int(w)}×{int(h)}）",
                    lambda: self._scale_image(pos, 1.25))
        m.addAction("缩小 25%", lambda: self._scale_image(pos, 0.8))
        m.addAction("恢复原始大小", lambda: self._reset_image(pos))
        m.addAction("适应编辑器宽度", lambda: self._fit_width_image(pos))
        m.addSeparator()
        m.addAction("删除图片", lambda: self._delete_image(pos))
        m.exec(e.globalPos())


class FloatingFormatBar(QFrame):
    """在笔记编辑器里选中文字后浮出的小格式条，可就地编辑选中文字。
    作为窗口的子控件存在，内部按钮全部 NoFocus，点击时不会打断编辑器选区。"""

    def __init__(self, editor):
        super().__init__(editor.viewport())
        self.editor = editor
        self.setObjectName("floatFmtBar")
        self.setFrameShape(QFrame.NoFrame)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet("""
            #floatFmtBar { background:#ffffff; border:1px solid #c9c9c9;
                           border-radius:8px; }
            #floatFmtBar QToolButton {
                border:none; background:transparent; border-radius:5px;
                padding:3px 6px; color:#333; font-size:13px; }
            #floatFmtBar QToolButton:hover { background:#ececec; }
            #floatFmtBar QToolButton:pressed { background:#dcdcdc; }
            #floatFmtBar QComboBox {
                border:1px solid #cfcfcf; border-radius:5px;
                padding:1px 4px; min-width:46px; font-size:12px; }
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)

        def mk(text, tip, cb):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)      # 关键：不抢编辑器焦点
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(cb)
            lay.addWidget(b)
            return b

        self.btn_size = QComboBox()
        self.btn_size.setFocusPolicy(Qt.NoFocus)
        for s in (10, 12, 14, 16, 18, 20, 24, 28, 32):
            self.btn_size.addItem(f"{s}", s)
        self.btn_size.setToolTip("字号")
        self.btn_size.activated.connect(self._on_size)
        lay.addWidget(self.btn_size)

        self._sep(lay)
        self.btn_bold = mk("B", "加粗", lambda: self._toggle("bold"))
        self.btn_bold.setStyleSheet("font-weight:bold;")
        self.btn_italic = mk("I", "斜体", lambda: self._toggle("italic"))
        self.btn_italic.setStyleSheet("font-style:italic;")
        self.btn_under = mk("U", "下划线", lambda: self._toggle("underline"))
        self.btn_under.setStyleSheet("text-decoration:underline;")
        self.btn_strike = mk("S", "删除线", lambda: self._toggle("strike"))
        self.btn_strike.setStyleSheet("text-decoration:line-through;")
        self._sep(lay)
        self.btn_color = mk("A", "文字颜色", self._pick_color)
        self.btn_color.setStyleSheet("color:#d84315; font-weight:bold;")
        self.btn_bg = mk("▨", "高亮背景", self._pick_bg)
        self._sep(lay)
        mk("✕", "清除格式", self._clear_format)

        self.adjustSize()
        self.hide()

    @staticmethod
    def _sep(lay):
        ln = QFrame()
        ln.setFrameShape(QFrame.VLine)
        ln.setStyleSheet("color:#d5d5d5;")
        ln.setFixedHeight(16)
        lay.addWidget(ln)

    # ---- 显示/定位 ----
    def refresh(self):
        """有选区就浮到选区上方，没有就隐藏。"""
        cur = self.editor.textCursor()
        if not cur.hasSelection():
            self.hide()
            return
        vp = self.editor.viewport()
        c1 = QTextCursor(cur)
        c1.setPosition(cur.selectionStart())
        c2 = QTextCursor(cur)
        c2.setPosition(cur.selectionEnd())
        r1 = self.editor.cursorRect(c1)
        r2 = self.editor.cursorRect(c2)
        top = min(r1.top(), r2.top())
        cx = (r1.center().x() + r2.center().x()) // 2
        vp = self.editor.viewport()
        x = cx - self.width() // 2
        y = top - self.height() - 6
        # 贴到视口顶部时改放到选区下方，避免被工具栏挡住
        if y < 2:
            y = max(r1.bottom(), r2.bottom()) + 6
        x = max(2, min(x, vp.width() - self.width() - 2))
        y = max(2, min(y, vp.height() - self.height() - 2))
        self.move(x, y)
        self._sync_size()
        self.raise_()
        self.show()

    def hide_bar(self, *args):
        self.hide()

    # ---- 应用格式 ----
    def _sync_size(self):
        """把字号下拉同步为选区当前字号。"""
        cur = self.editor.textCursor()
        size = cur.charFormat().fontPointSize()
        if size <= 0:
            size = self.editor.fontPointSize() or self.editor.font().pointSize()
        idx = self.btn_size.findData(int(size)) if size > 0 else -1
        if idx >= 0:
            self.btn_size.blockSignals(True)
            self.btn_size.setCurrentIndex(idx)
            self.btn_size.blockSignals(False)

    def _apply(self, fn):
        cur = self.editor.textCursor()
        if not cur.hasSelection():
            return
        fn(cur)
        self.editor.setTextCursor(cur)
        self.editor.setFocus()
        if self.editor.note_panel is not None:
            self.editor.note_panel._on_text_changed()
        self.refresh()

    def _toggle(self, prop):
        cur = self.editor.textCursor()
        if not cur.hasSelection():
            return
        src = cur.charFormat()
        f = QTextCharFormat()
        if prop == "bold":
            f.setFontWeight(QFont.Normal if src.fontWeight() >= QFont.Bold
                            else QFont.Bold)
        elif prop == "italic":
            f.setFontItalic(not src.fontItalic())
        elif prop == "underline":
            f.setFontUnderline(not src.fontUnderline())
        elif prop == "strike":
            f.setFontStrikeOut(not src.fontStrikeOut())
        self._apply(lambda c: c.mergeCharFormat(f))

    def _on_size(self):
        size = self.btn_size.currentData()
        if not size:
            return
        f = QTextCharFormat()
        f.setFontPointSize(float(size))
        self._apply(lambda c: c.mergeCharFormat(f))

    def _pick_color(self):
        cur = self.editor.textCursor()
        base = cur.charFormat().foreground().color() or QColor("#212121")
        c = QColorDialog.getColor(base, self.editor.window(), "文字颜色")
        if c.isValid():
            f = QTextCharFormat()
            f.setForeground(QBrush(c))
            self._apply(lambda cc: cc.mergeCharFormat(f))

    def _pick_bg(self):
        cur = self.editor.textCursor()
        base = cur.charFormat().background().color() or QColor("#fff59d")
        c = QColorDialog.getColor(base, self.editor.window(), "高亮背景")
        if c.isValid():
            f = QTextCharFormat()
            f.setBackground(QBrush(c))
            self._apply(lambda cc: cc.mergeCharFormat(f))

    def _clear_format(self):
        self._apply(lambda c: c.setCharFormat(QTextCharFormat()))


class NotePanel(QWidget):
    """富文本笔记面板：像 Word 一样的笔记管理。
    支持新建/删除/重命名笔记，可选绑定到某个 PDF，切 PDF 标签时自动选中绑定的笔记。"""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self._current_id = None    # 当前正在编辑的笔记 ID
        self._loading = False      # 程序化 setHtml 时屏蔽自动保存
        # NotePanel 本身只作逻辑协调器，不直接显示；
        # UI 拆为两块：list_ui 放左侧堆栈，editor_ui 放右侧停靠窗。
        self.setHidden(True)

        # ============ 左侧：操作栏 + 笔记列表 ============
        self.list_ui = QWidget()
        llay = QVBoxLayout(self.list_ui)
        llay.setContentsMargins(0, 0, 0, 0)
        llay.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        bar.setSpacing(4)

        self.btn_new = QPushButton("＋ 新建")
        self.btn_new.setToolTip("新建笔记")
        self.btn_new.setFixedHeight(26)
        self.btn_new.clicked.connect(self.new_note)
        bar.addWidget(self.btn_new)

        self.btn_delete = QPushButton("🗑")
        self.btn_delete.setToolTip("删除当前笔记")
        self.btn_delete.setFixedSize(30, 26)
        self.btn_delete.clicked.connect(self.delete_current)
        bar.addWidget(self.btn_delete)

        self.btn_bind = QPushButton("绑定PDF")
        self.btn_bind.setToolTip("将此笔记绑定到当前打开的 PDF（再点一次解绑）")
        self.btn_bind.setFixedHeight(26)
        self.btn_bind.clicked.connect(self.toggle_bind)
        bar.addWidget(self.btn_bind)

        bar.addStretch()
        llay.addLayout(bar)

        # 笔记列表
        self.note_list = QListWidget()
        self.note_list.setUniformItemSizes(True)
        self.note_list.setStyleSheet(
            "QListWidget{background:#f5f5f5; font-size:12px;}"
            "QListWidget::item{padding:4px 6px;}"
            "QListWidget::item:selected{background:#1e88e5; color:white;}")
        self.note_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.note_list.itemClicked.connect(self._on_list_clicked)
        self.note_list.customContextMenuRequested.connect(
            self._on_list_context_menu)
        llay.addWidget(self.note_list, 1)

        # ============ 右侧：格式栏 + 编辑器 ============
        self.editor_ui = QWidget()
        self.editor_ui.setMinimumWidth(300)
        ebl = QVBoxLayout(self.editor_ui)
        ebl.setContentsMargins(0, 0, 0, 0)
        ebl.setSpacing(0)

        # 编辑工具栏
        fmt_bar = QHBoxLayout()
        fmt_bar.setContentsMargins(4, 2, 4, 2)
        fmt_bar.setSpacing(3)

        def mk_btn(text, tip, cb):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedSize(28, 24)
            b.clicked.connect(cb)
            return b

        self.btn_bold = mk_btn("B", "加粗 (Ctrl+B)", self.fmt_bold)
        self.btn_bold.setStyleSheet("font-weight:bold;")
        self.btn_italic = mk_btn("I", "斜体 (Ctrl+I)", self.fmt_italic)
        self.btn_italic.setStyleSheet("font-style:italic;")
        self.btn_list = mk_btn("•", "无序列表", self.fmt_list)
        self.btn_color = mk_btn("🎨", "文字颜色", self.fmt_color)
        self.btn_image = mk_btn("🖼", "插入图片", self.insert_image)
        self.btn_link = mk_btn("📌", "关联当前PDF页", self.insert_pdf_link)
        for b in (self.btn_bold, self.btn_italic, self.btn_list,
                  self.btn_color, self.btn_image, self.btn_link):
            fmt_bar.addWidget(b)
        fmt_bar.addStretch()
        ebl.addLayout(fmt_bar)

        # 笔记富文本编辑器（子类：支持图片粘贴）
        self.editor = NoteEditor()
        self.editor.note_panel = self
        self.editor.setReadOnly(False)
        self.editor.setOpenExternalLinks(False)
        self.editor.setPlaceholderText("新建笔记或从左侧选择一篇开始编辑…")
        self.editor.setStyleSheet(
            "QTextBrowser{background:#fffef8; font-size:14px; border:none;}")
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.anchorClicked.connect(self._on_anchor_clicked)
        ebl.addWidget(self.editor, 1)

        # ---- 自动保存定时器 ----
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_current)
        self._save_pending = False

        # 加载已有笔记列表
        self.refresh_list()

    # ---- 笔记存储 ----
    def _all_notes(self):
        """读取全部笔记。返回 {id: {title, html, pdf_path}}。
        兼容旧格式 {pdf_path: html} 并自动迁移。"""
        try:
            with open(notes_file_path(), "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return {}
        # 旧格式迁移：{pdf路径: html字符串} -> {id: {title, html, pdf_path}}
        if d and all(isinstance(v, str) for v in d.values()):
            migrated = {}
            for i, (pdf, html) in enumerate(d.items()):
                migrated[f"legacy{i}"] = {
                    "title": os.path.basename(pdf) if pdf else "旧笔记",
                    "html": html, "pdf_path": pdf}
            self._save_notes(migrated)
            return migrated
        return {k: v for k, v in d.items() if isinstance(v, dict)}

    def _save_notes(self, data):
        """原子写入+重试：避免文件被杀毒软件等短暂锁定时静默丢失数据。"""
        path = notes_file_path()
        tmp = path + ".tmp"
        for attempt in range(5):
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, path)
                return
            except Exception:
                if attempt == 4:
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
                else:
                    time.sleep(0.05 * (attempt + 1))

    # ---- 列表管理 ----
    def refresh_list(self, keep_id=None):
        """重建笔记列表，keep_id 指定选中哪篇。"""
        self.note_list.clear()
        data = self._all_notes()
        # 排序：绑定 PDF 的在前，独立笔记在后；各自按修改时间
        items = []
        for nid, info in data.items():
            title = info.get("title", "未命名")
            pdf = info.get("pdf_path", "")
            label = f"📌 {title}" if pdf else f"📝 {title}"
            items.append((nid, label, pdf, title))
        items.sort(key=lambda x: (x[2] == "",))   # 绑定的(非空)在前

        select_row = -1
        for row, (nid, label, pdf, title) in enumerate(items):
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, nid)
            it.setToolTip(
                f"{title}" + (f"\n绑定: {os.path.basename(pdf)}" if pdf else ""))
            self.note_list.addItem(it)
            if nid == keep_id:
                select_row = row
        if select_row >= 0:
            self.note_list.setCurrentRow(select_row)

    def _current_note_id(self):
        """安全获取当前选中笔记的 ID。"""
        row = self.note_list.currentRow()
        if row < 0:
            return None
        item = self.note_list.item(row)
        return item.data(Qt.UserRole) if item else None

    def show_editor(self):
        """显示笔记编辑器：无文档时铺满内容区，有文档时弹右侧停靠窗。"""
        self.win._show_note_editor()

    def _on_list_clicked(self, item):
        nid = item.data(Qt.UserRole)
        if nid == self._current_id:
            self.show_editor()
            return
        self._load_note(nid)
        self.show_editor()

    def _on_list_context_menu(self, pos):
        item = self.note_list.itemAt(pos)
        m = QMenu(self)
        m.addAction("新建笔记", self.new_note)
        if item:
            nid = item.data(Qt.UserRole)
            m.addAction("重命名", lambda: self.rename_note(nid))
            m.addAction("删除", lambda: self.delete_note(nid))
            m.addSeparator()
            # 导出笔记为独立文件（HTML 保留图片/格式，Markdown 仅文字）
            sub = m.addMenu("导出为…")
            sub.addAction("HTML 文件 (.html)",
                          lambda: self.export_note(nid, "html"))
            sub.addAction("Markdown 文件 (.md)",
                          lambda: self.export_note(nid, "md"))
            m.addSeparator()
            data = self._all_notes()
            info = data.get(nid, {})
            if info.get("pdf_path"):
                m.addAction("取消PDF绑定",
                            lambda: self._set_bind(nid, ""))
            else:
                m.addAction("绑定到当前PDF",
                            lambda: self._set_bind(nid,
                                self.win.pdf_path if self.win.doc else ""))
        m.exec(self.note_list.viewport().mapToGlobal(pos))

    # ---- 新建/删除/重命名 ----
    _note_seq = 0   # 保证同一毫秒内创建的笔记 ID 不冲突

    def new_note(self):
        """新建一篇空白笔记。"""
        self.save_current()
        from datetime import datetime
        title = f"笔记 {datetime.now().strftime('%m-%d %H:%M')}"
        NotePanel._note_seq += 1
        nid = f"n{int(time.time()*1000)}_{NotePanel._note_seq}"
        data = self._all_notes()
        data[nid] = {"title": title, "html": "", "pdf_path": ""}
        self._save_notes(data)
        self.refresh_list(keep_id=nid)
        self._load_note(nid)
        self.show_editor()
        self.editor.setFocus()
        self.win.statusBar().showMessage(f"已新建笔记: {title}", 3000)

    def delete_note(self, nid=None):
        """删除指定笔记（未指定则删当前）。"""
        if nid is None:
            nid = self._current_note_id()
        if not nid:
            return
        data = self._all_notes()
        title = data.get(nid, {}).get("title", "?")
        btn = QMessageBox.question(
            self, "删除笔记", f"确定删除笔记「{title}」吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if btn != QMessageBox.Yes:
            return
        data.pop(nid, None)
        self._save_notes(data)
        if self._current_id == nid:
            self._current_id = None
            self._loading = True
            self.editor.clear()
            self._loading = False
        self.refresh_list()
        # 自动选中第一篇
        if self.note_list.count() > 0:
            self.note_list.setCurrentRow(0)
            self._on_list_clicked(self.note_list.currentItem())
        self.win.statusBar().showMessage(f"已删除笔记: {title}", 3000)

    def delete_current(self):
        self.delete_note()

    def rename_note(self, nid=None):
        if nid is None:
            nid = self._current_note_id()
        if not nid:
            return
        data = self._all_notes()
        old = data.get(nid, {}).get("title", "")
        name, ok = QInputDialog.getText(
            self, "重命名笔记", "新名称:", text=old)
        if not ok or not name.strip() or name.strip() == old:
            return
        data[nid]["title"] = name.strip()
        self._save_notes(data)
        self.refresh_list(keep_id=nid)

    # ---- 绑定 PDF ----
    def export_note(self, nid=None, fmt=None):
        """把指定笔记导出为独立文件。
        fmt: "html" 保留图片和富文本格式（图片复制到同名文件夹）；
             "md"   仅导出文字（HTML→简易 Markdown）。"""
        if nid is None:
            nid = self._current_note_id()
        if not nid:
            self.win.statusBar().showMessage("请先选中一篇笔记", 3000)
            return
        if fmt is None:
            fmt = notes_cfg_load().get("export_fmt", "html")
        data = self._all_notes()
        info = data.get(nid, {})
        if not info:
            return
        title = info.get("title", "未命名") or "未命名"
        html = info.get("html", "")
        safe = "".join(c for c in title if c not in r'\/:*?"<>|').strip() or "笔记"
        if fmt == "md":
            ext = ".md"
            filt = "Markdown (*.md)"
        else:
            ext = ".html"
            filt = "HTML (*.html)"
        default_name = safe + ext
        path, _ = QFileDialog.getSaveFileName(
            self, "导出笔记", default_name, filt)
        if not path:
            return
        if fmt == "md":
            self._export_md(html, path)
        else:
            self._export_html(title, html, path)
        self.win.statusBar().showMessage(f"已导出: {os.path.basename(path)}", 4000)

    def _export_html(self, title, html, path):
        """导出 HTML：把图片复制到同名_files 目录并改写 src 为相对路径。"""
        img_dir = notes_img_dir_path()
        out_dir = os.path.splitext(path)[0] + "_files"
        os.makedirs(out_dir, exist_ok=True)
        out_html = html
        # 收集并复制图片
        for m in _re.finditer(r'src="([^"]+)"', html):
            url = m.group(1)
            local = QUrl(url).toLocalFile()
            if local and os.path.exists(local):
                try:
                    shutil.copy2(local, os.path.join(out_dir, os.path.basename(local)))
                except Exception:
                    pass
        # 改写 src 为相对路径
        def repl(m):
            url = m.group(1)
            local = QUrl(url).toLocalFile()
            if local and os.path.exists(local):
                return f'src="{os.path.basename(out_dir)}/{os.path.basename(local)}"'
            return m.group(0)
        out_html = _re.sub(r'src="([^"]+)"', repl, html)
        full = (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<title>{title}</title></head><body>"
                f"<h1>{title}</h1>{out_html}</body></html>")
        with open(path, "w", encoding="utf-8") as f:
            f.write(full)

    def _export_md(self, html, path):
        """简易 HTML→Markdown 转换（仅处理常见标签，图片转为链接）。"""
        s = html
        # 去 <head>-ish 残留
        s = _re.sub(r'<head>.*?</head>', '', s, flags=_re.DOTALL | _re.IGNORECASE)
        # 图片 → ![图片](路径)
        def img_repl(m):
            url = m.group(1)
            local = QUrl(url).toLocalFile()
            return f"![图片]({local or url})"
        s = _re.sub(r'<img[^>]*?src="([^"]+)"[^>]*?>', img_repl, s, flags=_re.IGNORECASE)
        # 加粗 / 斜体
        s = _re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', s, flags=_re.DOTALL | _re.IGNORECASE)
        s = _re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', s, flags=_re.DOTALL | _re.IGNORECASE)
        s = _re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', s, flags=_re.DOTALL | _re.IGNORECASE)
        s = _re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', s, flags=_re.DOTALL | _re.IGNORECASE)
        # 标题
        for i in range(6, 0, -1):
            s = _re.sub(f'<h{i}[^>]*>(.*?)</h{i}>', lambda m: '#' * i + ' ' + m.group(1) + '\n',
                       s, flags=_re.DOTALL | _re.IGNORECASE)
        # 列表项 → - item
        s = _re.sub(r'<li[^>]*>(.*?)</li>', lambda m: '- ' + m.group(1) + '\n',
                   s, flags=_re.DOTALL | _re.IGNORECASE)
        s = _re.sub(r'</?(ul|ol)[^>]*>', '', s, flags=_re.IGNORECASE)
        # 段落/换行
        s = _re.sub(r'<p[^>]*>(.*?)</p>', lambda m: m.group(1) + '\n\n',
                   s, flags=_re.DOTALL | _re.IGNORECASE)
        s = _re.sub(r'<br\s*/?>', '\n', s, flags=_re.IGNORECASE)
        # 超链接
        s = _re.sub(r'<a\s+[^>]*?href="([^"]+)"[^>]*>(.*?)</a>',
                   lambda m: f'[{m.group(2)}]({m.group(1)})',
                   s, flags=_re.DOTALL | _re.IGNORECASE)
        # 清其余标签
        s = _re.sub(r'<[^>]+>', '', s)
        # HTML 实体
        s = (s.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
              .replace('&amp;', '&').replace('&quot;', '"'))
        with open(path, "w", encoding="utf-8") as f:
            f.write(s.strip() + "\n")

    def toggle_bind(self):
        """当前笔记绑定到当前 PDF / 解绑。"""
        nid = self._current_note_id()
        if not nid:
            self.win.statusBar().showMessage("请先选中一篇笔记", 3000)
            return
        data = self._all_notes()
        info = data.get(nid, {})
        if info.get("pdf_path"):
            self._set_bind(nid, "")
        elif self.win.doc and self.win.pdf_path:
            self._set_bind(nid, self.win.pdf_path)
        else:
            self.win.statusBar().showMessage("当前没有打开的 PDF", 3000)

    def _set_bind(self, nid, pdf_path):
        data = self._all_notes()
        if nid not in data:
            return
        # 检查目标 PDF 是否已被其他笔记绑定
        if pdf_path:
            for other_id, other_info in data.items():
                if other_id != nid and other_info.get("pdf_path") == pdf_path:
                    QMessageBox.warning(
                        self, "绑定冲突",
                        f"PDF《{os.path.basename(pdf_path)}》"
                        f"已被笔记「{other_info.get('title','')}」绑定，"
                        f"一个 PDF 只能绑定一篇笔记。")
                    return
        data[nid]["pdf_path"] = pdf_path
        self._save_notes(data)
        self.refresh_list(keep_id=nid)
        if pdf_path:
            self.win.statusBar().showMessage(
                f"已绑定到: {os.path.basename(pdf_path)}", 3000)
        else:
            self.win.statusBar().showMessage("已取消PDF绑定", 3000)

    def on_pdf_changed(self, pdf_path):
        """切换 PDF 标签时调用：自动选中绑定的笔记（无绑定不打扰用户）。"""
        if not pdf_path:
            return
        data = self._all_notes()
        for nid, info in data.items():
            if info.get("pdf_path") == pdf_path:
                self._load_note(nid)
                self.refresh_list(keep_id=nid)
                # 正停在笔记列表页时，同步弹出编辑器；否则只静默加载
                st = getattr(self.win, "left_stack", None)
                if st is not None and st.isVisible() and st.currentIndex() == 2:
                    self.show_editor()
                return
        # 没有绑定的笔记：不打扰，保持当前编辑状态

    def _load_note(self, nid):
        """加载某篇笔记到编辑器（先保存当前笔记，除非正在切换到同一篇）。"""
        if self._current_id and self._current_id != nid:
            self.save_current()
        self._current_id = nid
        data = self._all_notes()
        info = data.get(nid, {})
        self._loading = True
        self.editor.setHtml(info.get("html", ""))
        self._loading = False
        self.editor.fmt_bar.hide_bar()
        self._save_pending = False

    # ---- 格式化 ----
    def fmt_bold(self):
        self.editor.setFontWeight(
            QFont.Bold if self.editor.fontWeight() < QFont.Bold else QFont.Normal)

    def fmt_italic(self):
        self.editor.setFontItalic(not self.editor.fontItalic())

    def fmt_list(self):
        cursor = self.editor.textCursor()
        fmt = QTextListFormat()
        fmt.setStyle(QTextListFormat.ListDisc)
        cursor.insertList(fmt)

    def fmt_color(self):
        c = QColorDialog.getColor(QColor("#212121"), self, "选择文字颜色")
        if c.isValid():
            self.editor.setTextColor(c)

    # ---- 插入图片 ----
    def insert_image(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.gif)")
        if not f:
            return
        self._insert_image_path(f)

    def _insert_image_path(self, src_path):
        img_dir = notes_img_dir_path()
        os.makedirs(img_dir, exist_ok=True)
        ext = os.path.splitext(src_path)[1].lower() or ".png"
        NotePanel._note_seq += 1
        dst = os.path.join(
            img_dir,
            f"img_{int(time.time()*1000)}_{NotePanel._note_seq}{ext}")
        try:
            shutil.copy2(src_path, dst)
        except Exception as e:
            QMessageBox.warning(self, "插入图片失败", str(e))
            return
        url = QUrl.fromLocalFile(dst).toString()
        cursor = self.editor.textCursor()
        cursor.insertHtml(
            f'<img src="{url}" style="max-width:100%;" />')

    def insert_qimage(self, img):
        """把一张 QImage 落盘到笔记图片目录并插入编辑器。"""
        if img is None or img.isNull():
            return
        img_dir = notes_img_dir_path()
        os.makedirs(img_dir, exist_ok=True)
        NotePanel._note_seq += 1
        dst = os.path.join(
            img_dir,
            f"img_{int(time.time()*1000)}_{NotePanel._note_seq}.png")
        if not img.save(dst, "PNG"):
            return
        self._insert_image_url(dst)
        self.win.statusBar().showMessage("已粘贴图片", 2000)

    def paste_image(self):
        """把剪贴板里的图片插入笔记（位图 或 复制的图片文件）。
        返回 True 表示成功处理为图片粘贴。"""
        cb = QApplication.clipboard()
        mime = cb.mimeData()
        if mime is None:
            return False
        if mime.hasImage():
            img = cb.image()
            if img.isNull():
                return False
            self.insert_qimage(img)
            return True
        exts = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
        for u in (mime.urls() if hasattr(mime, "urls") else []):
            p = u.toLocalFile()
            if p and p.lower().endswith(exts) and os.path.isfile(p):
                self._insert_image_path(p)
                self.win.statusBar().showMessage("已粘贴图片文件", 2000)
                return True
        return False

    def _insert_image_url(self, dst):
        url = QUrl.fromLocalFile(dst).toString()
        cursor = self.editor.textCursor()
        cursor.insertHtml(
            f'<img src="{url}" style="max-width:100%;" />')

    # ---- 关联 PDF 位置 ----
    def insert_pdf_link(self):
        win = self.win
        if not win.doc or not win.pdf_path:
            self.win.statusBar().showMessage("请先打开 PDF 再关联", 3000)
            return
        page = win.page_no + 1
        path = win.pdf_path
        name = os.path.basename(path)
        href = f"pdfref://{path}?page={page}"
        cursor = self.editor.textCursor()
        cursor.insertHtml(
            f'<a href="{href}" style="color:#1e88e5;">'
            f'📌 {name} · 第{page}页</a>&nbsp;')

    def on_link_clicked(self, url_str):
        if not url_str.startswith("pdfref://"):
            return
        rest = url_str[len("pdfref://"):]
        if "?" in rest:
            path, qs = rest.split("?", 1)
        else:
            path, qs = rest, ""
        page = 0
        for part in qs.split("&"):
            if part.startswith("page="):
                try:
                    page = int(part[5:]) - 1
                except ValueError:
                    pass
        if not os.path.isfile(path):
            self.win.statusBar().showMessage(f"找不到文件: {path}", 3000)
            return
        if path in self.win.sessions:
            self.win._switch_to_session(path)
        else:
            self.win.load_pdf_path(path)
        if 0 <= page < self.win.doc.page_count:
            self.win.page_no = page
            self.win.load_page()
            self.win.statusBar().showMessage(
                f"已跳转到 {os.path.basename(path)} 第{page+1}页", 3000)

    def _on_anchor_clicked(self, qurl):
        url_str = qurl.toString()
        if url_str.startswith("pdfref://"):
            self.on_link_clicked(url_str)

    # ---- 自动保存 ----
    def _on_text_changed(self):
        if self._loading or not self._current_id:
            return
        self._save_pending = True
        self._save_timer.start(2000)

    def save_current(self):
        """立即保存当前笔记。"""
        if not self._current_id:
            return
        self._save_timer.stop()
        html = self.editor.toHtml()
        data = self._all_notes()
        if self._current_id not in data:
            return
        data[self._current_id]["html"] = html
        self._save_notes(data)
        self._save_pending = False


def hex_rgb(h):
    c = QColor(h)
    return (c.red(), c.green(), c.blue())


class TocDelegate(QStyledItemDelegate):
    """目录（书签）树的美化绘制：
    根节点=加粗蓝字+书签图标；子节点=灰描边书签，当前页为橙色实心书签；
    层级间画浅灰虚线导引，选中/悬停为浅灰底。"""

    INDENT = 20
    BLUE = "#1e6fd9"
    TEXT = "#3c4043"
    GUIDE = "#d6d6d6"
    BOOK = "#9aa0a6"
    BOOK_CUR = "#e8631a"

    def __init__(self, win):
        super().__init__(win.toc_tree)
        self.win = win

    def _expanded(self, idx):
        try:
            return self.win.toc_tree.isExpanded(idx)
        except Exception:
            return True

    def sizeHint(self, opt, idx):
        s = super().sizeHint(opt, idx)
        s.setHeight(24)
        return s

    def paint(self, p, opt, idx):
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        r = opt.rect
        depth = 0
        par = idx.parent()
        while par.isValid():
            depth += 1
            par = par.parent()
        is_root = depth == 0
        enabled = bool(opt.state & QStyle.State_Enabled)
        if opt.state & QStyle.State_Selected:
            p.fillRect(r, QColor("#ececec"))
        elif opt.state & QStyle.State_MouseOver:
            p.fillRect(r, QColor("#f5f5f5"))

        icon_x = r.left() + 5
        icon_w = 13
        cy = r.center().y()

        # 根节点前的展开/收起小箭头（原生分支已关闭，这里自绘）
        if is_root and idx.model().hasChildren(idx):
            chev = "▾" if self._expanded(idx) else "▸"
            p.setPen(QColor("#8a8f98"))
            fc = QFont()
            fc.setPixelSize(11)
            p.setFont(fc)
            p.drawText(QRectF(r.left() - 2, r.top(), 14, r.height()),
                       Qt.AlignVCenter | Qt.AlignCenter, chev)
            icon_x = r.left() + 12

        # 层级虚线导引：每个祖先层级画一条竖线，形成从上到下的连接感
        pen = QPen(QColor(self.GUIDE))
        pen.setStyle(Qt.DotLine)
        p.setPen(pen)
        for k in range(1, depth + 1):
            gx = r.left() - k * self.INDENT + self.INDENT // 2 + 5
            p.drawLine(gx, r.top(), gx, r.bottom())

        # 当前页书签：该条约定的页码等于当前页
        cur_page = getattr(self.win, "page_no", -1)
        pno = idx.data(Qt.UserRole)
        is_cur = (not is_root) and isinstance(pno, int) and pno == cur_page
        self._bookmark(p, icon_x, cy, icon_w,
                       color=(self.BOOK_CUR if is_cur else self.BOOK),
                       filled=is_cur)

        # 文本
        f = QFont()
        f.setPixelSize(14 if is_root else 13)
        f.setBold(is_root)
        p.setFont(f)
        if not enabled:
            p.setPen(QColor("#a0a0a0"))
        else:
            p.setPen(QColor(self.BLUE if is_root else self.TEXT))
        tx = icon_x + icon_w + 7
        txt = idx.data(Qt.DisplayRole) or ""
        p.drawText(QRectF(tx, r.top(), r.right() - tx - 4, r.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, txt)
        p.restore()

    def _bookmark(self, p, x, cy, w, color, filled=False):
        h = 15
        top = cy - h / 2
        path = QPainterPath()
        path.moveTo(x, top)
        path.lineTo(x + w, top)
        path.lineTo(x + w, top + h)
        path.lineTo(x + w / 2, top + h - 5)
        path.lineTo(x, top + h)
        path.closeSubpath()
        p.setPen(QPen(QColor(color), 1.4))
        p.setBrush(QColor(color) if filled else Qt.NoBrush)
        p.drawPath(path)


def save_annotations(pdf_path, annos, out_path, toc=None, deleted_pages=None):
    """把批注写入PDF。annos: {页码(0基): [anno,...]}，坐标为PDF点。
    toc 不为 None 时同时把书签目录写回。
    deleted_pages: 需删除的"原始页索引"(0基)集合；按倒序删除，
    使删后新文档的页序与界面中当前页索引一致，批注/书签页码可直接套用。"""
    doc = pymupdf.open(pdf_path)
    if deleted_pages:
        for pno in sorted(deleted_pages, reverse=True):
            if 0 <= pno < doc.page_count:
                doc.delete_page(pno)
    for pno, lst in annos.items():
        if not lst or pno >= doc.page_count:
            continue
        page = doc[pno]
        shape = page.new_shape()
        texts = []
        for a in lst:
            rgb = tuple(c / 255 for c in a["rgb"])
            t = a["type"]
            if t == "rect":
                shape.draw_rect(pymupdf.Rect(a["p1"], a["p2"]))
                shape.finish(color=rgb, width=a["wpt"])
            elif t == "oval":
                shape.draw_oval(pymupdf.Rect(a["p1"], a["p2"]))
                shape.finish(color=rgb, width=a["wpt"])
            elif t == "line":
                shape.draw_line(pymupdf.Point(*a["p1"]), pymupdf.Point(*a["p2"]))
                shape.finish(color=rgb, width=a["wpt"])
            elif t == "arrow":
                p1, p2 = pymupdf.Point(*a["p1"]), pymupdf.Point(*a["p2"])
                shape.draw_line(p1, p2)
                shape.finish(color=rgb, width=a["wpt"])
                hl = max(6.0, a["wpt"] * 4)
                ang = math.atan2(p2.y - p1.y, p2.x - p1.x)
                head = [p2,
                        pymupdf.Point(p2.x - hl * math.cos(ang - 0.42),
                                      p2.y - hl * math.sin(ang - 0.42)),
                        pymupdf.Point(p2.x - hl * math.cos(ang + 0.42),
                                      p2.y - hl * math.sin(ang + 0.42)),
                        p2]
                shape.draw_polyline(head)
                shape.finish(color=rgb, fill=rgb)
            elif t == "pen":
                pts = [pymupdf.Point(x, y) for x, y in a["pts"]]
                if len(pts) >= 2:
                    shape.draw_polyline(pts)
                    shape.finish(color=rgb, width=a["wpt"])
            elif t == "highlight":
                pts = [pymupdf.Point(x, y) for x, y in a["pts"]]
                if len(pts) >= 2:
                    shape.draw_polyline(pts)
                    shape.finish(color=rgb, width=HL_W, stroke_opacity=0.35)
            elif t == "texthl":
                shape.draw_rect(pymupdf.Rect(a["rect"]))
                shape.finish(color=None, fill=rgb, fill_opacity=0.4)
            elif t == "text":
                texts.append(a)
        shape.commit()
        for a in texts:
            page.insert_text(pymupdf.Point(a["p"][0], a["p"][1] + 0.8 * a["fs"]),
                             a["text"], fontsize=a["fs"], fontname="china-s",
                             color=tuple(c / 255 for c in a["rgb"]))
    if toc is not None:
        doc.set_toc([[int(l), str(t), int(p)] for l, t, p in toc])
    doc.save(out_path)
    doc.close()


class TextEditorItem(QGraphicsTextItem):
    """画布内联文字输入（类似图片编辑器）：点击处出现光标直接打字，
    回车/点击外部提交，Esc 取消。空白时提交=放弃。
    edit_anno 不为 None 时为编辑已有批注模式（提交时原地更新）。"""

    def __init__(self, win, scene_pos, color_hex, edit_anno=None):
        super().__init__()
        self.win = win
        self.color_hex = color_hex
        self.edit_anno = edit_anno          # 编辑模式：被编辑的批注 dict
        self.fs_pt = edit_anno["fs"] if edit_anno else (
            getattr(win, "font_size", None).value() if
            hasattr(win, "font_size") else TEXT_FS)   # PDF字号(pt)
        self.fs = max(6, int(round(self.fs_pt * win.zoom)))  # 场景字号随缩放
        self.setDefaultTextColor(QColor(color_hex))
        f = QFont("Microsoft YaHei", self.fs)
        self.setFont(f)
        if edit_anno:
            z = win.zoom
            self.setPos(edit_anno["p"][0] * z, edit_anno["p"][1] * z)
            self.setPlainText(edit_anno["text"])
            # 光标放末尾（放开头会让 Backspace 无效；全选会误删）
            cur = self.textCursor()
            cur.clearSelection()
            cur.movePosition(QTextCursor.MoveOperation.End)
            self.setTextCursor(cur)
            # 隐藏被编辑的原文字图元，否则编辑器文字与原批注重叠成"残影"
            self._hidden = []
            lst = win.annos.get(win.page_no, [])
            if edit_anno in lst:
                idx = lst.index(edit_anno)
                for it in win.scene.items():
                    if it.data(0) == idx and it.data(1) == "text" and \
                            type(it).__name__ == "QGraphicsTextItem":
                        it.hide()
                        self._hidden.append(it)
        else:
            self.setPos(scene_pos)
            self._hidden = []
        self.setZValue(30)
        self.setTabChangesFocus(True)
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self._done = False
        win.scene.addItem(self)
        self.setFocus()
        win.set_typing_mode(True)  # 打字期间禁用翻页/撤销等快捷键

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.commit()
            return
        if e.key() == Qt.Key_Escape:
            self.finish(cancel=True)
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        # 点击画布其他处/切工具 = 提交
        if not self._done:
            self.commit()
        super().focusOutEvent(e)

    def commit(self):
        txt = self.toPlainText().strip()
        self._done = True
        if txt:
            z = self.win.zoom
            px, py = self.pos().x() / z, self.pos().y() / z
            if self.edit_anno is not None:
                # 编辑模式：原地更新批注内容与字号，不新增
                self.edit_anno["text"] = txt
                self.edit_anno["fs"] = self.fs_pt
                self.win.rebuild_items()
            else:
                a = {"type": "text", "p": (px, py), "text": txt, "fs": self.fs_pt,
                     "color": self.color_hex, "rgb": hex_rgb(self.color_hex), "wpt": 1}
                self.win._add_anno_to_scene(a)
        elif self.edit_anno is not None:
            # 编辑模式清空文字 = 删除该批注
            lst = self.win.annos.get(self.win.page_no, [])
            if self.edit_anno in lst:
                lst.remove(self.edit_anno)
            self.win.rebuild_items()
        self.finish()

    def finish(self, cancel=False):
        self._done = True
        self.win.set_typing_mode(False)
        if cancel:
            # 取消编辑：恢复被隐藏的原文字图元（rebuild 未发生，需手动恢复）
            for it in self._hidden:
                it.show()
        scene = self.scene()
        if scene:
            scene.removeItem(self)
        self.win.on_text_editor_closed()


class Canvas(QGraphicsView):
    """画布视图：鼠标/滚轮事件转发 + 中键平移 + 滚轮翻页。"""

    def __init__(self, win):
        super().__init__(win.scene)
        self.win = win
        self._mid = False
        self._rs = None     # 文字批注缩放状态：{"idx", "item", "fs0", "h0"}
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setRenderHint(QPainter.TextAntialiasing)        # 文字抗锯齿
        self.setBackgroundBrush(QColor("#e8e8e8"))  # 浅灰底：页面白纸浮于其上
        self.setMouseTracking(True)  # 确保始终收到 mouseMoveEvent

    def _text_hit(self, sp):
        """selanno 工具下检测点击是否落在文字批注的右下角缩放柄上。
        返回 (图元, 批注索引)；不是则 (None, None)。柄=文字块右下角 12px 方块。"""
        for it in self.win.scene.items(sp):
            idx = it.data(0)
            if isinstance(idx, int) and it.data(1) == "text" and \
                    type(it).__name__ == "QGraphicsTextItem":
                br = it.sceneBoundingRect()
                handle = QRectF(br.right() - 12, br.bottom() - 12, 16, 16)
                if handle.contains(sp):
                    return it, idx
        return None, None

    def mousePressEvent(self, e):
        # 中键按下 = 临时抓手平移（任意工具下可用）
        if e.button() == Qt.MiddleButton and self.win.doc:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self._mid = True
            le = QMouseEvent(QEvent.MouseButtonPress, e.position(),
                             e.globalPosition(), Qt.LeftButton, Qt.LeftButton,
                             e.modifiers())
            super().mousePressEvent(le)
            e.accept()
            return
        # "选择批注"工具：命中文字缩放柄 → 进入缩放模式（不走场景默认选择）
        if e.button() == Qt.LeftButton and self.win.tool == "selanno" and self.win.doc:
            it, idx = self._text_hit(self.win.scene_pos(e))
            if it is not None:
                a = self.win.annos[self.win.page_no][idx]
                z = self.win.zoom
                self._rs = {"idx": idx, "item": it, "fs0": a["fs"],
                            "h0": max(1.0, it.sceneBoundingRect().height())}
                self.setCursor(Qt.SizeFDiagCursor)
                e.accept()
                return
        # 绘图工具：直接 accept，不调 super()（避免基类修改事件/坐标）
        # setMouseTracking(True) + accept 足以保证后续 mouseMoveEvent 到达
        if self.win.doc and self.win.tool not in ("view", "selanno"):
            self.win.on_press(e)
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        # 文字缩放拖动中：按高度比例缩放字号并实时重建
        if self._rs is not None:
            sp = self.win.scene_pos(e)
            it = self._rs["item"]
            h0 = self._rs["h0"]
            if h0 > 0:
                ratio = max(0.15, (sp.y() - it.pos().y()) / h0)
                fs = max(4, min(200, self._rs["fs0"] * ratio))
                lst = self.win.annos[self.win.page_no]
                if 0 <= self._rs["idx"] < len(lst) and lst[self._rs["idx"]]:
                    lst[self._rs["idx"]]["fs"] = fs
                    self.win.rebuild_items()
                    # rebuild 后图元重建，找回对应项
                    for x in self.win.scene.items():
                        if x.data(0) == self._rs["idx"] and x.data(1) == "text" and \
                                type(x).__name__ == "QGraphicsTextItem":
                            self._rs["item"] = x
                            break
            e.accept()
            return
        if self.win.on_move(e):
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._rs is not None:
            self._rs = None
            self.setCursor(Qt.ArrowCursor)
            e.accept()
            return
        if e.button() == Qt.MiddleButton and self._mid:
            self._mid = False
            le = QMouseEvent(QEvent.MouseButtonRelease, e.position(),
                             e.globalPosition(), Qt.LeftButton, Qt.NoButton,
                             e.modifiers())
            super().mouseReleaseEvent(le)
            self.win.pick_tool(self.win.tool)
            e.accept()
            return
        if self.win.on_release(e):
            e.accept()
        else:
            super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        # 双击文字批注 → 原地编辑（清空提交=删除）。
        # "选择批注/选择文字/文字"工具都支持：文字工具下双击空白仍是新建
        if e.button() == Qt.LeftButton and \
                self.win.tool in ("selanno", "selecttext", "text") and self.win.doc:
            sp = self.win.scene_pos(e)
            for it in self.win.scene.items(sp):
                idx = it.data(0)
                if isinstance(idx, int) and it.data(1) == "text" and \
                        type(it).__name__ == "QGraphicsTextItem":
                    a = self.win.annos[self.win.page_no][idx]
                    if self.win.tool == "selecttext":
                        self.win.clear_selection()
                    self.win.commit_text_editor()
                    self.win._text_editor = TextEditorItem(
                        self.win, sp, a["color"], edit_anno=a)
                    self.win.statusBar().showMessage(
                        "编辑文字：改完回车或点空白处；清空文字=删除该批注")
                    e.accept()
                    return
        # "文字"工具双击空白：press 已提交了刚才那个编辑器，这里再开一个新的
        if e.button() == Qt.LeftButton and self.win.tool == "text" and self.win.doc:
            sp = self.win.scene_pos(e)
            self.win.commit_text_editor()
            self.win._text_editor = TextEditorItem(self.win, sp, self.win.color_hex)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            # 以鼠标位置为锚点缩放（缩放后鼠标下内容保持不动）
            self.win.step_zoom(1 if e.angleDelta().y() > 0 else -1,
                               (e.position().x(), e.position().y()))
            e.accept()
            return
        d = e.angleDelta().y()
        sb = self.verticalScrollBar()
        vh = self.viewport().height()
        # 翻页阈值=当前页完全滚出视口（而非滚完整段邻页预览）；
        # 翻页后按预览中正看到的内容落点 → 无缝连续滚动，不会"跳回重看一页"
        next_y = getattr(self.win, "_next_y", None)
        prev_h = getattr(self.win, "_prev_h", 0)
        if d < 0 and next_y is not None and sb.value() >= min(sb.maximum(), next_y):
            # 下滑：视口顶已过本页底 → 翻下一页，落点=预览里已看到的位置
            if self.win.turn_page(1, land=sb.value() - next_y):
                e.accept()
                return
        elif d > 0 and prev_h and sb.value() <= max(sb.minimum(), -vh):
            # 上滑：视口底已到本页顶 → 翻上一页，落点=预览里已看到的位置
            if self.win.turn_page(-1, land=sb.value() + prev_h):
                e.accept()
                return
        super().wheelEvent(e)


class SearchThread(QThread):
    """后台在多个PDF文件中搜索关键词（含书签/TOC搜索）。"""
    found = Signal(dict)       # 每条匹配
    finished_all = Signal(int)  # 匹配总数

    def __init__(self, files, keyword, whole, case_sensitive, search_toc=False):
        super().__init__()
        self.files = files
        self.keyword = keyword
        self.whole = whole
        self.case_sensitive = case_sensitive
        self.search_toc = search_toc
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        count = 0
        for f in self.files:
            if self._stop:
                break
            try:
                d = pymupdf.open(f)
            except Exception:
                continue
            try:
                # 书签/TOC 搜索
                if self.search_toc:
                    for _lvl, title, pno in d.get_toc(simple=True):
                        if self._stop:
                            break
                        if pno < 1 or not self._text_match(title):
                            continue
                        self.found.emit({
                            "file": f, "page": pno - 1, "rect": None,
                            "kind": "toc", "snippet": title.strip()})
                        count += 1
                for pno in range(d.page_count):
                    if self._stop:
                        break
                    if self.whole:
                        hits = self._match_words(d[pno], self.keyword,
                                                 self.case_sensitive)
                    else:
                        hits = self._match_substring(d[pno], self.keyword,
                                                     self.case_sensitive)
                    for r in hits:
                        if self._stop:
                            break
                        self.found.emit({
                            "file": f, "page": pno,
                            "rect": (r.x0, r.y0, r.x1, r.y1),
                            "kind": "text",
                            "snippet": self._snippet(d[pno], r)})
                        count += 1
            finally:
                d.close()
        self.finished_all.emit(count)

    def _text_match(self, text):
        """书签标题匹配（支持整词/大小写选项）。"""
        import re as _re
        t = text if self.case_sensitive else text.lower()
        kw = self.keyword if self.case_sensitive else self.keyword.lower()
        if not kw:
            return False
        if self.whole:
            return _re.search(_re.escape(kw).join((r"\b", r"\b")), t) is not None
        return kw in t

    @staticmethod
    def _snippet(page, r, limit=60):
        """以命中词为中心的同行文字摘要。
        CAD 导出 PDF 的词框相互重叠，get_text(clip=整行) 会输出重复乱序
        文字；改用 words 坐标：同行词按 x 排序 + 重叠去重，只取命中词
        前后各 3 个词，命中词用【】标出。"""
        try:
            words = [w for w in page.get_text("words")
                     if w[1] < r.y1 and w[3] > r.y0]   # 与命中同一行
            if not words:
                return ""
            words.sort(key=lambda w: w[0])
            # 去掉互相重叠的重复词（CAD 词框重叠导致同一词出现多次）
            uniq = []
            for w in words:
                dup = False
                for p in uniq:
                    ox = min(p[2], w[2]) - max(p[0], w[0])
                    oy = min(p[3], w[3]) - max(p[1], w[1])
                    if ox > 0.5 * min(p[2] - p[0], w[2] - w[0]) and \
                            oy > 0.5 * min(p[3] - p[1], w[3] - w[1]):
                        dup = True
                        break
                if not dup:
                    uniq.append(w)
            # 命中词 = 与命中矩形重叠面积最大的词
            best, best_a = 0, -1.0
            for i, w in enumerate(uniq):
                a = (max(0, min(w[2], r.x1) - max(w[0], r.x0)) *
                     max(0, min(w[3], r.y1) - max(w[1], r.y0)))
                if a > best_a:
                    best_a, best = a, i
            lo, hi = max(0, best - 3), min(len(uniq), best + 4)
            parts = [uniq[i][4] for i in range(lo, hi)]
            parts[best - lo] = f"【{parts[best - lo]}】"
            s = " ".join(parts)
            return s if len(s) <= limit else s[:limit - 1] + "…"
        except Exception:
            return ""

    @staticmethod
    def _match_words(page, keyword, case_sensitive):
        """整词匹配：用 words 坐标，返回 Rect 列表。"""
        kw = keyword if case_sensitive else keyword.lower()
        out = []
        for w in page.get_text("words"):
            word = w[4]
            cmp = word if case_sensitive else word.lower()
            if cmp == kw:
                out.append(pymupdf.Rect(w[:4]))
        return out

    @staticmethod
    def _match_substring(page, keyword, case_sensitive):
        """子串匹配：search_for 默认忽略大小写；要求区分大小写时二次校验。
        支持跨词词组：按行合并词，用字符串查找定位坐标。"""
        if not case_sensitive:
            return page.search_for(keyword, quads=False)
        # 区分大小写：用 rawdict 逐行拼接精确定位
        kw = keyword
        hits = []
        d = page.get_text("rawdict")
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                # 拼接整行文字并记录每字符 bbox
                line_txt = ""
                char_boxes = []  # (char, bbox)
                for span in spans:
                    for ch in span.get("chars", []):
                        c = ch.get("c", "")
                        if c:
                            line_txt += c
                            b = ch.get("bbox")
                            char_boxes.append((c, b))
                start = 0
                while True:
                    i = line_txt.find(kw, start)
                    if i < 0:
                        break
                    seg = char_boxes[i:i + len(kw)]
                    seg = [s for s in seg if s[1]]
                    if seg:
                        x0 = min(s[1][0] for s in seg)
                        y0 = min(s[1][1] for s in seg)
                        x1 = max(s[1][2] for s in seg)
                        y1 = max(s[1][3] for s in seg)
                        hits.append(pymupdf.Rect(x0, y0, x1, y1))
                    start = i + len(kw)
        return hits


# ---------------- Markdown 阅读 ----------------
MD_EXTS = (".md", ".markdown")

# 内置样式：排版贴近常见 MD 阅读器；代码块用 Pygments 高亮（其自带配色由 HTML 内联）
MD_CSS = """
:root { color-scheme: light; }
body { margin: 0; background: #ffffff; }
#md-root {
  box-sizing: border-box; max-width: 860px; margin: 0 auto; padding: 28px 34px 60px;
  font-family: "Microsoft YaHei", "Segoe UI", "PingFang SC", sans-serif;
  font-size: 15px; line-height: 1.75; color: #24292f; word-wrap: break-word;
}
#md-root h1, #md-root h2, #md-root h3, #md-root h4 {
  font-weight: 600; line-height: 1.3; margin: 1.5em 0 0.6em;
}
#md-root h1 { font-size: 1.9em; border-bottom: 1px solid #e3e3e3; padding-bottom: .3em; }
#md-root h2 { font-size: 1.5em; border-bottom: 1px solid #ececec; padding-bottom: .25em; }
#md-root h3 { font-size: 1.25em; }
#md-root p, #md-root ul, #md-root ol { margin: 0.7em 0; }
#md-root ul, #md-root ol { padding-left: 1.8em; }
#md-root li { margin: 0.25em 0; }
#md-root a { color: #1e6fd9; text-decoration: none; }
#md-root a:hover { text-decoration: underline; }
#md-root blockquote {
  margin: 0.9em 0; padding: 0.5em 1em; color: #57606a;
  border-left: 4px solid #d0d7de; background: #f6f8fa; border-radius: 0 4px 4px 0;
}
#md-root code {
  font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
  font-size: 0.92em; background: #f0f1f3; padding: 0.15em 0.4em; border-radius: 4px;
}
#md-root pre {
  background: #f6f8fa; border: 1px solid #e3e6ea; border-radius: 6px;
  padding: 12px 14px; overflow-x: auto; line-height: 1.55;
}
#md-root pre code { background: none; padding: 0; font-size: 0.9em; }
#md-root table { border-collapse: collapse; margin: 1em 0; display: block; overflow-x: auto; }
#md-root th, #md-root td { border: 1px solid #d0d7de; padding: 6px 12px; }
#md-root th { background: #f6f8fa; font-weight: 600; }
#md-root tr:nth-child(2n) td { background: #fafbfc; }
#md-root img { max-width: 100%; }
#md-root hr { border: none; border-top: 1px solid #e3e3e3; margin: 1.6em 0; }
#md-root .md-missing-img {
  display: inline-block; padding: 2px 8px; color: #a33; background: #fff4f4;
  border: 1px dashed #e0a0a0; border-radius: 4px; font-size: .9em;
}
"""


def _md_exts_ok(path):
    return path.lower().endswith(MD_EXTS)


def md_to_html(text):
    """把 Markdown 文本转成完整 HTML（含样式与代码高亮）。
    公式/流程图暂按普通文本处理（后续可接入本地 KaTeX/Mermaid）。"""
    import markdown as _md
    try:
        body = _md.markdown(
            text,
            extensions=["extra", "tables", "fenced_code", "codehilite",
                        "toc", "sane_lists", "nl2br"],
            extension_configs={
                # noclasses=True：高亮配色以内联样式写出，无需再引外部 CSS
                "codehilite": {"guess_lang": False, "noclasses": True,
                               "pygments_style": "default"},
            },
        )
    except Exception as e:
        body = ("<p style='color:#a33'>Markdown 解析失败：" + str(e) + "</p>"
                "<pre>" + text.replace("&", "&amp;").replace("<", "&lt;") + "</pre>")
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<style>" + MD_CSS + "</style></head>"
            "<body><div id='md-root'>" + body + "</div></body></html>")


def md_read_text(path):
    """读取 MD 文件文本，自动尝试常见编码（UTF-8 / GBK）。"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def make_tab_close_icon(color="#8a8a8a", hover="#e53935", size=16):
    """自绘标签关闭图标：两个状态下都返回细线 ✕，悬停时变红。"""
    def _draw(col):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(col))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        m = 3.5
        p.drawLine(QPointF(m, m), QPointF(size - m, size - m))
        p.drawLine(QPointF(size - m, m), QPointF(m, size - m))
        p.end()
        return pm
    ic = QIcon()
    ic.addPixmap(_draw(color), QIcon.Normal)
    ic.addPixmap(_draw(hover), QIcon.Active)
    ic.addPixmap(_draw(hover), QIcon.Selected)
    return ic


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF 批注工具 (Qt版)")
        self.resize(1200, 850)

        self.doc = None
        self.pdf_path = ""
        self.page_no = 0
        self._zoom_factor = 1.0   # 用户缩放比例（不限于预设列表）
        self.annos = {}
        self.toc_items = []       # 内存中的书签目录 [[lvl, title, pno(1基)], ...]，可编辑
        self.toc_dirty = False    # 书签是否被改动过（保存时写回PDF）
        self._toc_clip = None     # 书签剪贴板：{"lvl","title","pno"}
        self.deleted_pages = set()  # 已删除页的"原始页索引"(0基)，保存时按此删页
        self.page_map = []        # 当前页索引 -> 原始页索引（删页后用于保存定位）
        self._saved_sig = None    # 上次保存时的编辑状态签名（退出时比对是否需提示）
        self.tool = "selecttext"   # 默认"选择文字"：拖选即复制（普通阅读器习惯）
        self._shape_cur = "pen"    # "画笔"按钮当前画笔，默认画笔
        self.color_hex = COLORS[0][1]
        self.wpt = WIDTHS[1][1]

        self.drawing = False
        self.start = None          # QPointF 场景坐标
        self.free_pts = []
        self.preview_items = []
        self._pen_item = None      # 画笔/荧光笔的实时路径图元

        self.sel_items = []        # 文字选择高亮图元
        self.sel_text = ""         # 当前选中的文字
        self.sel_hits = []         # 选中词 [(页号, 词数据)]，跨页选择时含上下邻页的词
        self._words_cache = {}     # 页号 -> 词表缓存（当前页 + 上下邻页）
        self._scene_page_rects = {}  # 页号 -> 该页在场景中的矩形（当前页 + 邻页预览）
        self._text_editor = None   # 画布内联文字输入框（"文字"工具）

        self.scene = QGraphicsScene(self)
        self.view = Canvas(self)
        self.md_panel = MdPanel(self)   # Markdown 阅读面板（与画布堆栈切换）
        self.md_path = ""               # 当前打开的 MD 文件（空=未打开）
        self._view_mode = "pdf"         # "pdf" 或 "md"

        # ---- 多文档会话 ----
        # 每个打开的 PDF 一个会话字典，key=绝对路径；doc 对象常驻不反复开关。
        # 切换标签时把当前文档相关字段整批快照到旧会话、再从新会话恢复。
        self.sessions = {}             # path -> session dict
        self._suspend_guard = False    # 标签切换快照/恢复期间屏蔽 tab 信号重入
        self._closing_paths = set()    # 正在关闭的文档，快照回写时跳过

        # ---- 左侧文件管理树 ----
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.itemDoubleClicked.connect(self.on_tree_double_click)
        self.tree.customContextMenuRequested.connect(self.on_tree_context_menu)
        self.tree.setStyleSheet(
            "QTreeWidget{background:#f0f0f0; font-size:13px;}"
            "QTreeWidget::item{padding:2px;}"
            "QTreeWidget::item:selected{background:#1e88e5; color:white;}")

        # ---- 左侧目录（书签）树 ----
        self.toc_tree = QTreeWidget()
        self.toc_tree.setHeaderHidden(True)
        self.toc_tree.setUniformRowHeights(True)
        self.toc_tree.setIndentation(TocDelegate.INDENT)
        self.toc_tree.setRootIsDecorated(False)   # 图标/导引线由 TocDelegate 自绘
        self.toc_tree.setMouseTracking(True)      # 支持悬停高亮
        self.toc_tree.setStyle(QStyleFactory.create("Fusion"))
        self.toc_tree.itemClicked.connect(self.on_toc_click)
        self.toc_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.toc_tree.customContextMenuRequested.connect(self.on_toc_context_menu)
        self.toc_tree.setStyleSheet(
            "QTreeWidget{background:#ffffff; border:none; outline:none;}"
            "QTreeWidget::item{border:none;}")
        # 美化绘制（图标/导引线/配色）；page_no 用于标记"当前页"书签
        self.toc_tree.setItemDelegate(TocDelegate(self))

        # ---- 主布局：左侧活动栏 | splitter(左侧面板 + 内容区) ----
        # 笔记面板逻辑对象：UI 拆为 列表(并入左侧堆栈) + 编辑器(右侧停靠窗)
        self.note_panel = NotePanel(self)
        # 左侧面板用堆栈窗口：文件夹树 / 目录树 / 笔记列表 三选一显示
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self.tree)              # index 0: 文件夹
        self.left_stack.addWidget(self.toc_tree)          # index 1: 目录
        self.left_stack.addWidget(self.note_panel.list_ui)  # index 2: 笔记列表
        # 内容区堆栈：PDF 画布 / Markdown 阅读面板 / 笔记编辑器（无文档时全屏）
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.view)      # index 0: PDF 画布
        self.content_stack.addWidget(self.md_panel)  # index 1: Markdown
        # index 2: 笔记全屏容器（无 PDF/MD 打开时把编辑器挪进来占满内容区）
        self.note_full = QWidget()
        nfl = QVBoxLayout(self.note_full)
        nfl.setContentsMargins(0, 0, 0, 0)
        nfl.setSpacing(0)
        self.content_stack.addWidget(self.note_full)
        self._note_editor_full = False
        self._note_editor_open = False   # 用户是否已打开笔记编辑器（跟随文档切换自动恢复）
        self._editor_host_moving = False  # 编辑器在 dock/全屏间迁移中（屏蔽 dock 可见性信号）

        # 多文档标签栏：每个打开的 PDF 一个标签，标签右侧带 ✕
        self.doc_tabs = QTabBar()
        self.doc_tabs.setExpanding(False)
        self.doc_tabs.setDrawBase(True)
        self.doc_tabs.setTabsClosable(False)   # 用自绘的关闭按钮替代原生 ✕
        self.doc_tabs.setMovable(False)
        self.doc_tabs.setUsesScrollButtons(True)
        self.doc_tabs.setElideMode(Qt.ElideRight)
        self.doc_tabs.setStyleSheet("""
            QTabBar { background:#e8e8e8; }
            QTabBar::tab {
                background:#dcdcdc; color:#333; padding:5px 8px 5px 11px;
                border:1px solid #bdbdbd; border-bottom:none;
                border-top-left-radius:6px; border-top-right-radius:6px;
                margin-right:2px; max-width:340px;
            }
            QTabBar::tab:selected { background:#ffffff; color:#d84315;
                                    font-weight:bold; }
            QTabBar::tab:hover { background:#f0f0f0; }
        """)
        self.doc_tabs.currentChanged.connect(self.on_doc_tab_changed)
        self.doc_tabs.hide()   # 无文档时隐藏

        content_box = QWidget()
        cbl = QVBoxLayout(content_box)
        cbl.setContentsMargins(0, 0, 0, 0)
        cbl.setSpacing(0)
        cbl.addWidget(self.doc_tabs)
        cbl.addWidget(self.content_stack, 1)

        self.splitter = QSplitter(Qt.Horizontal)
        splitter = self.splitter
        splitter.addWidget(self.left_stack)
        splitter.addWidget(content_box)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 960])
        splitter.setChildrenCollapsible(False)

        # 左侧垂直活动栏（仿VSCode）
        self.activity_bar = QFrame()
        self.activity_bar.setFixedWidth(48)
        self.activity_bar.setStyleSheet("""
            QFrame { background: #2c2c2c; }
            QToolButton {
                background: transparent; color: #cccccc; border: none;
                font-size: 20px; width: 48px; height: 44px;
            }
            QToolButton:hover { background: #3c3c3c; }
            QToolButton:checked { background: #37373d; color: white;
                                   border-left: 2px solid white; }
        """)
        abl = QVBoxLayout(self.activity_bar)
        abl.setContentsMargins(0, 6, 0, 0)
        abl.setSpacing(2)
        self.activity_buttons = QButtonGroup(self)
        self.activity_buttons.setExclusive(True)

        def add_activity(text, tip, cb, checkable=False, checked=False):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setChecked(checked)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.clicked.connect(cb)
            if checkable:
                self.activity_buttons.addButton(b)
            abl.addWidget(b)
            return b

        # 文件夹面板（默认展开，按钮为选中态）
        self.btn_folders = add_activity("📁", "文件夹面板（显示/隐藏）",
                                        self.toggle_folders,
                                        checkable=True, checked=True)
        self.btn_toc = add_activity("☰", "目录（书签）面板", self.toggle_toc,
                                    checkable=True)
        self.btn_notes = add_activity("📝", "笔记面板 (Ctrl+J)",
                                      self.toggle_notes, checkable=True)
        add_activity("🔍", "搜索 (Ctrl+F)", self.toggle_search)
        add_activity("🌐", "翻译选中文字 (Ctrl+T)", self.toggle_translate)
        add_activity("📄", "打开PDF文件", self.open_pdf)
        # 弹性空白，把其余按钮推到底部
        abl.addStretch(1)

        central = QWidget()
        cl = QHBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self.activity_bar)
        cl.addWidget(splitter, 1)
        self.setCentralWidget(central)

        self.pix_item = None

        # ---- 右侧搜索面板（停靠窗） ----
        self.search_dock = QDockWidget("搜索", self)
        self.search_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.search_dock.hide()
        self.build_search_panel()
        self.addDockWidget(Qt.RightDockWidgetArea, self.search_dock)

        # ---- 右侧翻译面板（停靠窗，与搜索同区域） ----
        self.translate_dock = QDockWidget("翻译", self)
        self.translate_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.translate_panel = TranslatePanel(self)
        self.translate_dock.setWidget(self.translate_panel)
        self.translate_dock.hide()
        self.addDockWidget(Qt.RightDockWidgetArea, self.translate_dock)
        self.tabifyDockWidget(self.search_dock, self.translate_dock)

        # ---- 右侧笔记编辑器（停靠窗）；笔记列表已并入左侧堆栈 ----
        self.note_dock = QDockWidget("笔记编辑", self)
        self.note_dock.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.note_dock.setWidget(self.note_panel.editor_ui)
        self.note_dock.hide()
        self.note_dock.visibilityChanged.connect(self._on_note_dock_vis)
        self.addDockWidget(Qt.RightDockWidgetArea, self.note_dock)
        self.search_thread = None
        self.search_results = []
        self.search_idx = -1
        self.search_hit_items = []   # 当前页命中高亮图元
        self.search_kw = ""
        self.pixmap = None
        self._loading = False

        self.build_toolbar()
        self._wire_md_panel()
        self.build_shortcuts()
        self.pick_tool(self.tool)
        self.refresh_tree()
        self.statusBar().showMessage(
            "请打开PDF或双击左侧文件 ｜ 滚轮=翻页 · Ctrl+滚轮=缩放 · 拖选文字自动复制,按H转高亮 · "
            "'选择批注'工具点选后Delete删除 · Ctrl+Z撤销")

    # ---------------- UI ----------------
    def build_toolbar(self):
        # PDF 专属控件集合：切到 Markdown 模式时统一禁用
        self.pdf_only_actions = []
        self.pdf_only_widgets = []

        # 第一行：文件/翻页/缩放/颜色/线宽
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(tb)

        def add_text(txt, tip, cb, pdf_only=False):
            a = QAction(txt, self)
            a.setToolTip(tip)
            a.triggered.connect(cb)
            tb.addAction(a)
            if pdf_only:
                self.pdf_only_actions.append(a)
            return a

        self.recent_btn = QToolButton()
        self.recent_btn.setText("最近 ▾")
        self.recent_btn.setToolTip("最近打开的PDF（点击切换）")
        self.recent_btn.setPopupMode(QToolButton.InstantPopup)
        self.recent_menu = QMenu(self)
        self.recent_btn.setMenu(self.recent_menu)
        self.recent_menu.aboutToShow.connect(self.build_recent_menu)
        tb.addWidget(self.recent_btn)
        add_text("保存批注PDF", "批注嵌入保存为新PDF", self.save_pdf, pdf_only=True)
        tb.addSeparator()

        add_text("◀", "上一页", lambda: self.turn_page(-1), pdf_only=True)
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setToolTip("输入页码回车跳转")
        self.page_spin.valueChanged.connect(self.on_spin)
        tb.addWidget(self.page_spin)
        self.pdf_only_widgets.append(self.page_spin)
        self.page_label = QLabel()
        tb.addWidget(self.page_label)
        self.pdf_only_widgets.append(self.page_label)
        add_text("▶", "下一页", lambda: self.turn_page(1), pdf_only=True)
        tb.addSeparator()

        add_text("－", "缩小", lambda: self.step_zoom(-1))
        self.zoom_label = QLabel("  100%  ")
        tb.addWidget(self.zoom_label)
        add_text("＋", "放大", lambda: self.step_zoom(1))
        add_text("⤢", "适应窗口（整页完整显示）", self.fit_to_width)
        tb.addSeparator()

        # ---- 颜色按钮：左键选色；右键打开调色盘自定义该按钮颜色并替换 ----
        self.color_defs = colors_load()   # [[名称, #rrggbb], ...]，右键可改
        self.color_group = QButtonGroup(self)
        self.color_group.setExclusive(True)
        self.color_btns = []
        for i, (name, hexc) in enumerate(self.color_defs):
            b = QPushButton()          # 纯色块，不显示文字（自定义后文字会与新色不符）
            b.setCheckable(True)
            b.setFixedSize(28, 22)
            b.setToolTip(f"{name}（左键选用，右键自定义颜色）")
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda pos, k=i: self.pick_custom_color(k))
            self._style_color_btn(b, i)
            b.clicked.connect(
                lambda _, k=i: self.pick_color(self.color_defs[k][1]))
            self.color_group.addButton(b)
            self.color_btns.append(b)
            tb.addWidget(b)
            self.pdf_only_widgets.append(b)
        self.color_btns[0].setChecked(True)
        self.color_hex = self.color_defs[0][1]   # 首个颜色为默认（随自定义更新）

        self.width_combo = QComboBox()
        for label, _ in WIDTHS:
            self.width_combo.addItem(label)
        self.width_combo.setCurrentIndex(1)
        self.width_combo.currentIndexChanged.connect(
            lambda i: setattr(self, "wpt", WIDTHS[i][1]))
        tb.addWidget(self.width_combo)
        self.pdf_only_widgets.append(self.width_combo)

        tb.addSeparator()
        # ---- 设置按钮：左键直接打开快捷键设置；右键弹出菜单 ----
        self.settings_btn = QToolButton()
        self.settings_btn.setText("设置")
        self.settings_btn.setToolTip("设置（左键打开快捷键设置，右键弹出菜单）")
        self.settings_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.settings_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.settings_btn.clicked.connect(self.open_shortcut_settings)
        self.settings_btn.customContextMenuRequested.connect(
            lambda pos: self._settings_menu.exec(
                self.settings_btn.mapToGlobal(pos)))
        self._settings_menu = QMenu(self.settings_btn)
        self._settings_menu.addAction("快捷键设置…", self.open_shortcut_settings)
        self._settings_menu.addAction("翻译设置…", self.open_translate_settings)
        self._settings_menu.addAction("笔记设置…", self.open_note_settings)
        self._settings_menu.addAction("恢复默认快捷键", self.reset_shortcuts)
        self.settings_btn.setMenu(self._settings_menu)
        tb.addWidget(self.settings_btn)


        # 第二行：工具（强制换行，固定常显示，不折回第一行）
        tb2 = QToolBar("工具")
        tb2.setMovable(False)
        tb2.setFloatable(False)
        self.addToolBarBreak()
        self.addToolBar(tb2)

        def add_text2(txt, tip, cb, pdf_only=True):
            a = QAction(txt, self)
            a.setToolTip(tip)
            a.triggered.connect(cb)
            tb2.addAction(a)
            if pdf_only:
                self.pdf_only_actions.append(a)
            return a

        from PySide6.QtGui import QActionGroup
        self.tool_group = QActionGroup(self)
        ag = self.tool_group
        ag.setExclusive(True)
        self._tool_actions = {}
        for label, val in TOOLS:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setData(val)
            if val == self.tool:
                a.setChecked(True)
            a.triggered.connect(lambda checked, v=val: self.pick_tool(v))
            ag.addAction(a)
            tb2.addAction(a)
            self._tool_actions[val] = a
            self.pdf_only_actions.append(a)
        tb2.addSeparator()

        # ---- "画笔"合并按钮：左键用当前画笔，右键弹出菜单切换 ----
        self.shape_btn = QToolButton()
        self.shape_btn.setText("画笔")
        self.shape_btn.setCheckable(True)
        self.shape_btn.setToolTip("画笔（左键使用，右键切换：画笔/荧光笔/矩形/椭圆/直线/箭头）")
        self.shape_btn.setPopupMode(QToolButton.MenuButtonPopup)
        self.shape_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.shape_btn.customContextMenuRequested.connect(
            lambda pos: self.shape_menu.exec(self.shape_btn.mapToGlobal(pos)))
        self.shape_menu = QMenu(self.shape_btn)
        self.shape_group = QActionGroup(self)
        self.shape_group.setExclusive(True)
        self._shape_actions = {}
        for label, val in SHAPE_ITEMS:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setData(val)
            a.triggered.connect(lambda checked, v=val: self.pick_tool(v))
            self.shape_group.addAction(a)
            self.shape_menu.addAction(a)
            self._shape_actions[val] = a
        self.shape_btn.setMenu(self.shape_menu)
        self.shape_btn.clicked.connect(
            lambda: self.pick_tool(self._shape_tool()))  # 左键：切到当前画笔
        tb2.addWidget(self.shape_btn)
        self.pdf_only_widgets.append(self.shape_btn)
        tb2.addSeparator()

        add_text2("高亮选中(H)", "用'选择文字'拖选后按H：转成高亮批注（随PDF保存）",
                  self.highlight_selection)
        # 翻译在 PDF 和 Markdown 下都可用
        add_text2("翻译(Ctrl+T)", "打开/关闭翻译面板；开启后选中文字即自动翻译",
                  self.translate_selection, pdf_only=False)
        add_text2("删除批注(Del)", "用'选择批注'工具点选/框选后按Delete删除",
                  self.delete_selected)
        tb2.addSeparator()
        # ---- 文字批注字号（对"文字"工具的新输入生效；已有文字用角点拖拽缩放）----
        self.font_size_label = QLabel("字号 ")
        tb2.addWidget(self.font_size_label)
        self.pdf_only_widgets.append(self.font_size_label)
        self.font_size = QSpinBox()
        self.font_size.setRange(4, 96)
        self.font_size.setValue(TEXT_FS)
        self.font_size.setSuffix(" pt")
        self.font_size.setToolTip("文字批注的字号（对'文字'工具新输入的文字生效）")
        self.font_size.setFixedWidth(90)
        tb2.addWidget(self.font_size)
        self.pdf_only_widgets.append(self.font_size)
        tb2.addSeparator()

        add_text2("撤销", "撤销本页最后一条批注 (Ctrl+Z)", self.undo)
        add_text2("清除本页", "清除本页全部批注", self.clear_page)

    def build_shortcuts(self):
        """按用户配置（或默认）创建全部快捷键。可重复调用以应用新配置。"""
        for sc in getattr(self, "_all_sc", []):
            sc.setParent(None)
            sc.deleteLater()
        self._all_sc = []
        self._typing_sc = []
        self.sc_cfg = shortcuts_load()

        # 动作执行体
        actions = {
            "prev_page": lambda: self.turn_page(-1),
            "next_page": lambda: self.turn_page(1),
            "fit_window": self.fit_to_width,
            "zoom_in": lambda: self.step_zoom(1),
            "zoom_out": lambda: self.step_zoom(-1),
            "undo": self.undo,
            "copy": self.copy_selection,
            "highlight": self.highlight_selection,
            "delete_anno": self.delete_selected,
            "open_pdf": self.open_pdf,
            "save_pdf": self.save_pdf,
            "search": self.toggle_search,
            "next_hit": self.search_next,
            "prev_hit": self.search_prev,
            "toggle_folders": self.toggle_folders,
            "switch_tool": self.cycle_tool,
            "tool_view": lambda: self.pick_tool("view"),
            "tool_selecttext": lambda: self.pick_tool("selecttext"),
            "tool_selanno": lambda: self.pick_tool("selanno"),
            "tool_highlight": lambda: self.pick_tool("highlight"),
            "tool_text": lambda: self.pick_tool("text"),
            "tool_texthl": lambda: self.pick_tool("texthl"),
            "translate": self.translate_selection,
            "notes": self.toggle_notes,
        }
        # 打字时需要禁用的（会劫持按键或误触发）
        typing_keys = {
            "prev_page", "next_page", "undo", "copy", "highlight", "delete_anno",
            "toggle_folders", "switch_tool", "tool_view", "tool_selecttext",
            "tool_selanno", "tool_highlight", "tool_text", "tool_texthl",
            "translate",
        }
        for key, cb in actions.items():
            seq = self.sc_cfg.get(key, "")
            if not seq:
                continue
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(cb)
            self._all_sc.append(sc)
            if key in typing_keys:
                self._typing_sc.append(sc)

        # 固定快捷键（不提供自定义）
        for seq, cb in (("Backspace", self.delete_selected),
                        ("Escape", self.on_escape)):
            sc = QShortcut(QKeySequence(seq), self)
            self._all_sc.append(sc)
            if seq == "Backspace":
                self._typing_sc.append(sc)
        QShortcut(QKeySequence("Return"), self.search_input, self.do_search)
        QShortcut(QKeySequence("Enter"), self.search_input, self.do_search)

    def open_shortcut_settings(self):
        """打开快捷键设置对话框并在确定后立即应用、持久化。"""
        dlg = ShortcutDialog(self, shortcuts_load())
        if dlg.exec() == QDialog.Accepted:
            cfg = dlg.result_cfg()
            shortcuts_save(cfg)
            self.build_shortcuts()
            self.statusBar().showMessage("快捷键设置已保存并生效")

    def reset_shortcuts(self):
        """恢复默认快捷键（删除本地配置文件后重建）。"""
        try:
            if os.path.exists(SHORTCUTS_FILE):
                os.remove(SHORTCUTS_FILE)
        except OSError:
            pass
        self.build_shortcuts()
        self.statusBar().showMessage("已恢复默认快捷键")

    def set_typing_mode(self, on):
        """内联文字输入开始/结束：临时禁用会劫持按键的快捷键。"""
        for sc in self._typing_sc:
            sc.setEnabled(not on)

    # ---------------- 基本操作 ----------------
    def pick_color(self, hexc):
        self.color_hex = hexc

    def _style_color_btn(self, btn, i):
        """按 color_defs[i] 刷新纯色块底色（按钮不显示文字）。"""
        hexc = self.color_defs[i][1]
        btn.setStyleSheet(
            f"QPushButton{{background:{hexc}; border:1px solid #888;}}"
            f"QPushButton:checked{{border:2px solid #000;}}")

    def pick_custom_color(self, i):
        """右键颜色按钮：打开调色盘自定义该按钮的颜色并替换（会持久化）。"""
        if not (0 <= i < len(self.color_defs)):
            return
        name, cur = self.color_defs[i]
        c = QColorDialog.getColor(QColor(cur), self,
                                  f"自定义“{name}”的颜色")
        if not c.isValid():
            return
        new_hex = c.name()
        self.color_defs[i][1] = new_hex
        colors_save(self.color_defs)
        self._style_color_btn(self.color_btns[i], i)
        # 若当前正使用这个按钮的颜色，立即跟随更新，避免继续用旧色
        if self.color_btns[i].isChecked():
            self.color_hex = new_hex
        self.statusBar().showMessage(f"“{name}”颜色已改为 {new_hex}")

    def _shape_tool(self):
        """当前"画笔"按钮对应的画笔工具值。"""
        return self._shape_cur if self._shape_cur in SHAPE_VALS else "pen"

    # 循环切换的三个常用工具（Tab）
    CYCLE_TOOLS = ("view", "selecttext", "selanno")

    def cycle_tool(self):
        """在三个常用工具间循环切换（Tab）。
        焦点在文本输入框时忽略，避免在搜索框/快捷键输入框里误切工具。"""
        if isinstance(QApplication.focusWidget(), (QLineEdit, QKeySequenceEdit)):
            return
        cur = self.tool
        try:
            i = self.CYCLE_TOOLS.index(cur)
        except ValueError:
            nxt = self.CYCLE_TOOLS[0]
        else:
            nxt = self.CYCLE_TOOLS[(i + 1) % len(self.CYCLE_TOOLS)]
        self.pick_tool(nxt)
        self.statusBar().showMessage(
            f"工具已切换：{dict((v, n) for n, v in TOOLS)[nxt]}", 2000)

    def pick_tool(self, val):
        if val != "text":
            self.commit_text_editor()  # 切走工具时提交内联文字
        self.tool = val
        if val in SHAPE_VALS:
            # 记住当前画笔，供"画笔"按钮左键使用；同步按钮提示与菜单勾选
            self._shape_cur = val
            if hasattr(self, "shape_btn"):
                nm = SHAPE_NAMES.get(val, "画笔")
                self.shape_btn.setToolTip(
                    f"画笔：{nm}（左键使用，右键切换：画笔/荧光笔/矩形/椭圆/直线/箭头）")
            if hasattr(self, "_shape_actions") and val in self._shape_actions:
                self._shape_actions[val].setChecked(True)
            for v, a in getattr(self, "_tool_actions", {}).items():
                if v in SHAPE_VALS:
                    a.setChecked(False)
            if hasattr(self, "shape_btn"):
                self.shape_btn.setChecked(True)
        else:
            if hasattr(self, "shape_btn"):
                self.shape_btn.setChecked(False)
            if hasattr(self, "_tool_actions") and val in self._tool_actions:
                self._tool_actions[val].setChecked(True)
        if val == "view":
            self.view.setDragMode(QGraphicsView.ScrollHandDrag)
            self.view.setCursor(Qt.ArrowCursor)
        elif val == "selecttext":
            self.view.setDragMode(QGraphicsView.NoDrag)
            self.view.setCursor(Qt.IBeamCursor)
        elif val == "selanno":
            self.view.setDragMode(QGraphicsView.RubberBandDrag)
            self.view.setCursor(Qt.ArrowCursor)
        else:
            self.view.setDragMode(QGraphicsView.NoDrag)
            self.view.setCursor(Qt.CrossCursor)

    @property
    def zoom(self):
        # 场景缩放系数 = 用户倍率 × 内部超采样，直接以高分辨率渲染不降采样
        return self._zoom_factor * RENDER_SCALE

    @property
    def user_zoom(self):
        """用户可见倍率（不含内部超采样），用于标签显示。"""
        return self._zoom_factor

    def step_zoom(self, d, anchor=None):
        """步进缩放：每档5%，范围10%~400%。
        Markdown 模式：缩放 MD 阅读面板。
        anchor: (vx, vy) 鼠标视口坐标——缩放后鼠标下的内容保持不动；
        None 时以视口中心为锚点（工具栏±按钮）。"""
        if self._view_mode == "md":
            self.md_panel.zoom_by(d)
            self.zoom_label.setText(f"  {int(self.md_panel._zoom * 100)}%  ")
            return
        if not self.doc:
            return
        cur = self._zoom_factor
        n = cur / 0.05
        if d > 0:  # 放大：下一个更高档
            nxt = math.floor(n + 1e-9) * 0.05 + 0.05 if abs(n - round(n)) < 1e-9 else math.ceil(n - 1e-9) * 0.05
        else:      # 缩小：下一个更低档
            nxt = math.ceil(n - 1e-9) * 0.05 - 0.05 if abs(n - round(n)) < 1e-9 else math.floor(n + 1e-9) * 0.05
        new_z = max(0.10, min(4.0, round(nxt, 3)))
        if abs(new_z - cur) < 1e-9:  # 已到边界
            return
        vp = self.view.viewport()
        if anchor is None:
            anchor = (vp.width() / 2, vp.height() / 2)
        vx, vy = anchor
        # 本程序不用 view.scale()，场景坐标=视口坐标+滚动条值；
        # pixmap 以 zoom(=user×RENDER_SCALE) 渲染，PDF内容点 = 场景坐标 / zoom
        zs = self.zoom  # 缩放前场景系数
        vsb0 = self.view.verticalScrollBar().value()
        hsb0 = self.view.horizontalScrollBar().value()
        # 鼠标处 PDF 内容点（页面内坐标，不含上页预览偏移）
        pdf_x = (hsb0 + vx) / zs
        pdf_y = (vsb0 + vy - self._prev_h) / zs
        self._zoom_factor = new_z
        self.load_page(keep_scroll=True)
        # 缩放后：让同一 PDF 内容点落在鼠标视口位置
        zs2 = self.zoom
        vsb = self.view.verticalScrollBar()
        hsb = self.view.horizontalScrollBar()
        vsb.setValue(round(pdf_y * zs2 + self._prev_h - vy))
        hsb.setValue(round(pdf_x * zs2 - vx))

    def fit_to_width(self):
        """计算适合画布的缩放比例，使整页完整显示（不出现滚动条）。
        Markdown 模式：重置 MD 面板缩放到 100%。"""
        if self._view_mode == "md":
            self.md_panel.set_zoom(1.0)
            self.zoom_label.setText("  100%  ")
            return
        if not self.doc:
            return
        page = self.doc[self.page_no]
        vp = self.view.viewport()
        avail_w = max(200, vp.width() - 24)
        avail_h = max(200, vp.height() - 24)
        # 关键：实际显示像素 = PDF尺寸 × 用户倍率 × RENDER_SCALE，必须除回去
        z = min(avail_w / (page.rect.width * RENDER_SCALE),
                avail_h / (page.rect.height * RENDER_SCALE))
        self._zoom_factor = max(0.05, z)
        self.load_page()

    def open_pdf(self):
        f, _ = QFileDialog.getOpenFileName(self, "选择PDF文件", "", "PDF 文件 (*.pdf)")
        if not f:
            return
        self.load_pdf_path(f)

    # 每个 PDF 会话需要独立保存的字段（工具/颜色/搜索面板为全局共享，不快照）
    SESSION_FIELDS = ("doc", "pdf_path", "page_no", "_zoom_factor", "annos",
                      "toc_items", "toc_dirty", "_toc_clip", "deleted_pages",
                      "page_map", "_saved_sig")

    def _capture_session(self):
        """把当前文档相关字段收集成一个会话字典（不修改现场）。"""
        return {k: getattr(self, k) for k in self.SESSION_FIELDS}

    def _store_current_session(self):
        """把当前正在显示的 PDF 状态写回其会话（切换/关闭前调用）。
        _closing_paths 中的文档正在被关闭，不得再写回（否则产生幽灵会话）。"""
        if self.doc is None:
            return
        if self.pdf_path in getattr(self, "_closing_paths", ()):
            return
        try:
            self.commit_text_editor()
        except Exception:
            pass
        self.sessions[self.pdf_path] = self._capture_session()

    def _apply_session(self, data):
        """把某会话的字段装载到 self 作为当前文档，并清空渲染缓存/选区。"""
        for k in self.SESSION_FIELDS:
            setattr(self, k, data[k])
        # 以下为"当前页渲染"临时态，恢复后由 load_page 重建，先清空
        self.sel_text = ""
        self.sel_hits = []
        self._words_cache = {}
        self._scene_page_rects = {}
        self._text_editor = None
        self.drawing = False
        self.free_pts = []
        self.preview_items = []
        self.clear_hit_marks()
        self.scene.clear()

    def _tab_index_of(self, path):
        for i in range(self.doc_tabs.count()):
            if self.doc_tabs.tabData(i) == path:
                return i
        return -1

    def _switch_to_session(self, path):
        """切换到已打开的某个 PDF 会话（点击标签/重复打开同一文件）。"""
        if self.pdf_path == path and self.doc is not None:
            return
        if path not in self.sessions:
            return
        self._store_current_session()
        data = self.sessions[path]
        self._apply_session(data)
        idx = self._tab_index_of(path)
        signals_were = self.doc_tabs.blockSignals(True)
        if idx >= 0:
            self.doc_tabs.setCurrentIndex(idx)
        self.doc_tabs.blockSignals(signals_were)
        self.page_spin.setRange(1, self.doc.page_count)
        self.clear_selection()
        self.refresh_toc()
        self._apply_view_mode("pdf")
        self.load_page()
        self.zoom_label.setText(f"  {int(self.user_zoom * 100)}%  ")
        self.note_panel.on_pdf_changed(path)
        self.statusBar().showMessage(
            f"切换到: {os.path.basename(path)}（共 {self.doc.page_count} 页）")

    def load_pdf_path(self, f):
        f = os.path.abspath(f)
        # 已打开：直接切到对应标签，不重复加载
        if f in self.sessions:
            self._switch_to_session(f)
            history_add(f)
            return
        try:
            newdoc = pymupdf.open(f)
        except Exception as e:
            QMessageBox.critical(self, "出错", f"打开失败: {e}")
            history_remove(f)
            return
        # 先把当前文档状态快照回其会话
        self._store_current_session()
        self.doc = newdoc
        self.pdf_path = f
        self.annos = {}
        self.page_no = 0
        self._zoom_factor = 1.0
        self._words_cache = {}   # 换文档：清空词表缓存
        self.toc_items = [list(t) for t in self.doc.get_toc(simple=True)]
        self.toc_dirty = False
        self._toc_clip = None
        self.deleted_pages = set()                       # 换文档：重置删页记录
        self.page_map = list(range(self.doc.page_count))  # 当前页索引 -> 原始页索引
        self.sel_text = ""
        self.sel_hits = []
        self._scene_page_rects = {}
        self._text_editor = None
        self._saved_sig = self._state_sig()   # 新打开的文档视为"已保存"状态
        self.sessions[f] = self._capture_session()
        # 新增标签（屏蔽信号：当前 self 已是该文档，无需再触发切换恢复）
        blocked = self.doc_tabs.blockSignals(True)
        idx = self.doc_tabs.addTab(os.path.basename(f))
        self.doc_tabs.setTabData(idx, f)
        self.doc_tabs.setTabToolTip(idx, f)
        self._install_tab_close_btn(idx, f)
        self.doc_tabs.setCurrentIndex(idx)
        self.doc_tabs.blockSignals(blocked)
        self.doc_tabs.show()
        self.page_spin.setRange(1, self.doc.page_count)
        self.clear_selection()
        self.load_page()
        self.refresh_toc()  # 刷新左侧目录（书签）树
        # 延迟自动适应窗口宽度（等视口尺寸就绪）。
        # 绑定目标路径：若 50ms 内用户已切到别的标签，则不再改当前文档缩放
        def _delayed_fit(target=f):
            if self.pdf_path == target and self.doc is not None:
                self.fit_to_width()
        QTimer.singleShot(50, _delayed_fit)
        history_add(f)
        self.statusBar().showMessage(
            f"已打开: {os.path.basename(f)}（共 {self.doc.page_count} 页）")
        self.note_panel.on_pdf_changed(f)
        self._apply_view_mode("pdf")

    def on_doc_tab_changed(self, idx):
        """用户点击标签切换文档（程序化切换已屏蔽此信号，不会重入）。"""
        if idx < 0:
            return
        path = self.doc_tabs.tabData(idx)
        if path and path != self.pdf_path:
            self._switch_to_session(path)

    def _install_tab_close_btn(self, idx, path):
        """给标签右侧装一个自绘的关闭按钮（比原生 ✕ 更精致）。"""
        btn = QToolButton(self.doc_tabs)
        btn.setIcon(make_tab_close_icon())
        btn.setIconSize(QSize(14, 14))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setAutoRaise(True)
        btn.setFixedSize(18, 18)
        btn.setToolTip("关闭此文档")
        btn.setStyleSheet(
            "QToolButton { border:none; background:transparent;"
            " border-radius:9px; }"
            "QToolButton:hover { background:#e0e0e0; }"
            "QToolButton:pressed { background:#c8c8c8; }")
        # 按路径关闭：标签删除后索引会变，用路径定位更稳妥
        btn.clicked.connect(lambda _=False, p=path: self._close_pdf_path(p))
        self.doc_tabs.setTabButton(idx, QTabBar.RightSide, btn)

    def on_doc_tab_close(self, idx):
        """点击某标签的 ✕：关闭对应 PDF（未保存先提示）。"""
        path = self.doc_tabs.tabData(idx)
        if not path or path not in self.sessions:
            return
        self._close_pdf_path(path)

    def load_md_path(self, f):
        """在画布区打开 Markdown 阅读面板（PDF 仍保留，可一键切回）。"""
        ok, err = self.md_panel.load_file(f)
        if not ok:
            QMessageBox.critical(self, "出错", err)
            return
        self.md_path = f
        self._apply_view_mode("md")
        self.statusBar().showMessage(f"已打开 Markdown: {os.path.basename(f)}")

    def _apply_view_mode(self, mode):
        """切换内容区（PDF 画布 / Markdown / 笔记全屏）并同步工具栏可用状态。"""
        self._view_mode = mode
        is_md = (mode == "md")
        # 无任何文档时，若笔记编辑器已打开 → 编辑器铺满内容区（index 2）
        note_full_now = (not is_md) and (not self.doc) and (not self.md_path) \
            and self._note_editor_open
        self._sync_note_editor_host(note_full_now)
        self.content_stack.setCurrentIndex(2 if note_full_now
                                           else (1 if is_md else 0))
        if note_full_now:
            self.note_panel.editor_ui.show()
        elif self._note_editor_open:
            # 编辑器此前开着（全屏态）→ 打开文档后恢复为右侧停靠栏，继续显示
            self.note_dock.show()
            self.note_dock.raise_()
        # PDF 专属控件在 Markdown 模式下禁用（避免误操作空文档）
        for a in self.pdf_only_actions:
            a.setEnabled(not is_md)
        for w in self.pdf_only_widgets:
            w.setEnabled(not is_md)
        # 缩放在 Markdown 下要作用到 MD 面板，放宽缩放按钮可用性
        if is_md:
            base = os.path.basename(self.md_path)
            self.setWindowTitle(f"{base} - Markdown - PDF 批注工具")
        elif self.doc:
            base = os.path.basename(self.pdf_path)
            self.setWindowTitle(f"{base} - PDF 批注工具")
        else:
            self.setWindowTitle("PDF 批注工具 (Qt版)")

    def _on_note_dock_vis(self, vis):
        # 迁移到全屏时 dock 会被隐藏，这不算用户关闭
        if self._editor_host_moving:
            return
        if not vis:
            self._note_editor_open = False

    def _sync_note_editor_host(self, want_full):
        """want_full=True 把笔记编辑器挪到内容区占满全屏；False 挪回右侧停靠栏。"""
        if want_full == self._note_editor_full:
            return
        self._editor_host_moving = True
        ed = self.note_panel.editor_ui
        try:
            if want_full:
                self.note_dock.setWidget(None)
                self.note_dock.hide()
                ed.setMinimumWidth(0)
                self.note_full.layout().addWidget(ed)
                self._note_editor_full = True
            else:
                self.note_full.layout().removeWidget(ed)
                ed.setMinimumWidth(300)
                self.note_dock.setWidget(ed)
                self._note_editor_full = False
        finally:
            self._editor_host_moving = False

    def _show_note_editor(self):
        """显示笔记编辑器：无文档时内容区全屏，有文档时弹右侧停靠栏。"""
        self._note_editor_open = True
        want_full = (not self.doc) and (not self.md_path)
        self._sync_note_editor_host(want_full)
        if self._note_editor_full:
            self.content_stack.setCurrentIndex(2)
            self.note_panel.editor_ui.show()
        else:
            self._apply_view_mode("md" if self._view_mode == "md" else "pdf")
            self.note_dock.show()
            self.note_dock.raise_()

    def build_recent_menu(self):
        m = self.recent_menu
        m.clear()
        lst = history_load()
        if not lst:
            a = m.addAction("（暂无历史）")
            a.setEnabled(False)
        else:
            for p in lst:
                name = os.path.basename(p)
                act = m.addAction(name)
                act.setToolTip(p)
                act.triggered.connect(lambda checked=False, x=p: self.load_pdf_path(x))
            m.addSeparator()
            clr = m.addAction("清空历史记录")
            clr.triggered.connect(history_clear)
        # 在系统文件管理器中打开所在文件夹
        loc = m.addAction("打开当前PDF所在文件夹")
        loc.triggered.connect(self.open_in_explorer)

    def open_in_explorer(self):
        if not self.pdf_path:
            return
        try:
            os.startfile(os.path.dirname(self.pdf_path))  # noqa: S606
        except Exception:
            pass

    # ---------------- 左侧文件管理树 ----------------
    def add_folder_to_tree(self):
        d = QFileDialog.getExistingDirectory(self, "选择要管理的文件夹")
        if not d:
            return
        lst = folders_load()
        if d not in lst:
            lst.append(d)
            folders_save(lst)
        self.refresh_tree()
        self.statusBar().showMessage(f"已添加文件夹: {d}")

    def refresh_tree(self):
        """重建左侧树：每个收藏的文件夹=根节点，子目录和PDF=子节点。"""
        self.tree.clear()
        for folder_path in folders_load():
            if not os.path.isdir(folder_path):
                continue
            name = os.path.basename(folder_path) or folder_path
            root = QTreeWidgetItem(self.tree, [f"📁 {name}"])
            root.setData(0, Qt.UserRole, folder_path)
            root.setToolTip(0, folder_path)
            self._populate_subtree(root, folder_path, max_depth=3, depth=0)
            if root.childCount() == 0:
                child = QTreeWidgetItem(root, ["（空）"])
                child.setData(0, Qt.UserRole, None)
            self.tree.expandItem(root)

    def _populate_subtree(self, parent_item, dir_path, max_depth, depth,
                          md_only=False):
        """递归填充子目录与文档。返回该子树是否加入了任何节点。"""
        try:
            entries = sorted(os.listdir(dir_path))
        except OSError:
            return False
        dirs = [e for e in entries if os.path.isdir(os.path.join(dir_path, e))]
        docs = [e for e in entries
                if e.lower().endswith(".pdf") or _md_exts_ok(e)]
        has_content = False
        for d in dirs:
            full = os.path.join(dir_path, d)
            item = QTreeWidgetItem(parent_item, [f"📁 {d}"])
            item.setData(0, Qt.UserRole, full)
            item.setToolTip(0, full)
            if depth < max_depth:
                sub = self._populate_subtree(item, full, max_depth, depth + 1)
                if not sub:
                    empty = QTreeWidgetItem(item, ["（空）"])
                    empty.setData(0, Qt.UserRole, None)
            has_content = True
        for p in sorted(docs):
            full = os.path.join(dir_path, p)
            icon = "📄" if p.lower().endswith(".pdf") else "📝"
            item = QTreeWidgetItem(parent_item, [f"{icon} {p}"])
            item.setData(0, Qt.UserRole, full)
            item.setToolTip(0, full)
            has_content = True
        return has_content

    def on_tree_double_click(self, item):
        path = item.data(0, Qt.UserRole)
        if not path or not os.path.isfile(path):
            return
        if _md_exts_ok(path):
            self.load_md_path(path)
        else:
            self.load_pdf_path(path)

    def on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            # 空白处右键 = 文件夹管理入口
            m = QMenu(self)
            m.addAction("添加文件夹到收藏", self.add_folder_to_tree)
            m.addSeparator()
            m.addAction("刷新全部", self.refresh_tree)
            m.exec(self.tree.viewport().mapToGlobal(pos))
            return
        path = item.data(0, Qt.UserRole)
        m = QMenu(self)
        is_dir = bool(path and os.path.isdir(path))
        is_file = bool(path and os.path.isfile(path))
        is_root = (item.parent() is None and is_dir)
        if is_file:
            if _md_exts_ok(path):
                m.addAction("打开（Markdown 阅读）",
                            lambda: self.load_md_path(path))
                m.addAction("用系统程序打开",
                            lambda: os.startfile(path))  # noqa: S606
            else:
                m.addAction("打开", lambda: self.load_pdf_path(path))
            m.addAction("在文件夹中显示",
                        lambda: os.startfile(os.path.dirname(path)))  # noqa: S606
        if is_dir:
            m.addAction("新建子文件夹", lambda: self.tree_new_subfolder(item))
            m.addAction("重命名", lambda: self.tree_rename_item(item))
        m.addSeparator()
        m.addAction("刷新此节点", lambda: self._refresh_node(item))
        if is_dir:
            m.addAction("在文件夹中显示",
                        lambda: os.startfile(path))  # noqa: S606
            m.addSeparator()
            if is_root:
                m.addAction(f"移除收藏（不删文件）",
                            lambda: self._remove_folder(path))
            m.addAction("删除（移到回收站）",
                        lambda: self.tree_delete_item(item))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def tree_new_subfolder(self, item):
        """在选中文件夹节点下新建子文件夹。"""
        parent_path = item.data(0, Qt.UserRole)
        if not parent_path or not os.path.isdir(parent_path):
            return
        name, ok = QInputDialog.getText(
            self, "新建子文件夹", "文件夹名称:", text="新建文件夹")
        if not ok or not name.strip():
            return
        name = name.strip().replace("/", "_").replace("\\", "_")
        new_path = os.path.join(parent_path, name)
        if os.path.exists(new_path):
            QMessageBox.warning(self, "新建失败", f"已存在同名文件夹: {name}")
            return
        try:
            os.makedirs(new_path, exist_ok=False)
        except OSError as e:
            QMessageBox.critical(self, "新建失败", str(e))
            return
        self._refresh_node(item)
        self.statusBar().showMessage(f"已新建文件夹: {new_path}", 3000)

    def tree_rename_item(self, item):
        """重命名文件夹（磁盘上真实重命名）。"""
        old_path = item.data(0, Qt.UserRole)
        if not old_path or not os.path.isdir(old_path):
            return
        old_name = os.path.basename(old_path)
        new_name, ok = QInputDialog.getText(
            self, "重命名文件夹", "新名称:", text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        new_name = new_name.strip().replace("/", "_").replace("\\", "_")
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        if os.path.exists(new_path):
            QMessageBox.warning(self, "重命名失败", f"已存在同名文件夹: {new_name}")
            return
        try:
            os.rename(old_path, new_path)
        except OSError as e:
            QMessageBox.critical(self, "重命名失败", str(e))
            return
        # 若重命名的是收藏根目录，同步更新收藏列表
        roots = folders_load()
        if old_path in roots:
            roots = [new_path if p == old_path else p for p in roots]
            folders_save(roots)
        # 若当前打开的 PDF 在被重命名目录内，路径会失效，刷新树即可
        self.refresh_tree()
        self.statusBar().showMessage(f"已重命名: {old_name} → {new_name}", 3000)

    def tree_delete_item(self, item):
        """删除文件夹（移到系统回收站，避免误删不可恢复）。"""
        path = item.data(0, Qt.UserRole)
        if not path or not os.path.isdir(path):
            return
        name = os.path.basename(path)
        btn = QMessageBox.question(
            self, "删除文件夹",
            f"确定删除文件夹「{name}」吗？\n将移到系统回收站，可在回收站恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if btn != QMessageBox.Yes:
            return
        try:
            self._move_to_recycle_bin(path)
        except OSError as e:
            QMessageBox.critical(self, "删除失败", str(e))
            return
        # 若删除的是收藏根，同步移除收藏
        roots = folders_load()
        if path in roots:
            roots = [p for p in roots if p != path]
            folders_save(roots)
        self.refresh_tree()
        self.statusBar().showMessage(f"已删除文件夹: {name}", 3000)

    @staticmethod
    def _move_to_recycle_bin(path):
        """把文件/文件夹移到系统回收站（Windows 用 SHFileOperation，零依赖）。"""
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR),
                    ("fFlags", wintypes.WORD),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR),
                ]

            FO_DELETE = 3
            FOF_ALLOWUNDO = 0x0040
            FOF_NOCONFIRMATION = 0x0010
            FOF_SILENT = 0x0004
            # pFrom 必须以双 \0 结尾
            p_from = os.path.abspath(path) + "\0\0"
            op = SHFILEOPSTRUCTW()
            op.hwnd = None
            op.wFunc = FO_DELETE
            op.pFrom = p_from
            op.pTo = None
            op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
            res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
            if res != 0:
                raise OSError(f"系统回收站操作失败（代码 {res}）")
        else:
            # 非 Windows：无回收站 API，直接永久删除
            shutil.rmtree(path)

    def _refresh_node(self, item):
        path = item.data(0, Qt.UserRole)
        if not path or not os.path.isdir(path):
            return
        # 清空子节点重建
        while item.childCount():
            item.removeChild(item.child(0))
        self._populate_subtree(item, path, max_depth=3, depth=0)
        if item.childCount() == 0:
            child = QTreeWidgetItem(item, ["（空）"])
            child.setData(0, Qt.UserRole, None)
        self.tree.expandItem(item)

    def _remove_folder(self, folder_path):
        lst = folders_load()
        if folder_path in lst:
            lst.remove(folder_path)
            folders_save(lst)
            self.refresh_tree()
            self.statusBar().showMessage(f"已移除文件夹收藏: {folder_path}")

    # ---------------- 搜索面板 ----------------
    def build_search_panel(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        lay.addWidget(QLabel("搜索范围:"))
        self.search_scope = QComboBox()
        self.search_scope.addItem("当前PDF文档")
        # 收藏的文件夹加入范围选项
        for f in folders_load():
            self.search_scope.addItem(f"📁 {os.path.basename(f) or f}", f)
        self.search_scope.currentIndexChanged.connect(self._on_scope_changed)
        lay.addWidget(self.search_scope)

        lay.addWidget(QLabel("搜索关键词:"))
        row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入文字或词组后回车…")
        self.search_input.returnPressed.connect(self.do_search)
        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self.do_search)
        row.addWidget(self.search_input, 1)
        row.addWidget(self.search_btn)
        lay.addLayout(row)

        # 上一处/下一处：在命中结果间循环跳转（当前命中橙色高亮，其余黄色）
        nav = QHBoxLayout()
        self.btn_prev_hit = QPushButton("◀ 上一处")
        self.btn_next_hit = QPushButton("下一处 ▶")
        self.btn_prev_hit.setToolTip("上一个命中（Shift+F3）")
        self.btn_next_hit.setToolTip("下一个命中（F3）")
        self.btn_prev_hit.clicked.connect(self.search_prev)
        self.btn_next_hit.clicked.connect(self.search_next)
        nav.addWidget(self.btn_prev_hit)
        nav.addWidget(self.btn_next_hit)
        nav.addStretch(1)
        lay.addLayout(nav)

        opt = QHBoxLayout()
        self.chk_whole = QCheckBox("整词")
        self.chk_case = QCheckBox("区分大小写")
        self.chk_case.setChecked(True)
        self.chk_toc = QCheckBox("搜书签")
        self.chk_toc.setChecked(True)
        self.chk_toc.setToolTip("同时搜索PDF书签/目录标题")
        opt.addWidget(self.chk_whole)
        opt.addWidget(self.chk_case)
        opt.addWidget(self.chk_toc)
        opt.addStretch(1)
        lay.addLayout(opt)

        self.search_status = QLabel("")
        self.search_status.setStyleSheet("color:#666; font-size:12px;")
        lay.addWidget(self.search_status)

        self.search_list = QListWidget()
        self.search_list.itemDoubleClicked.connect(self.on_search_jump)
        self.search_list.itemClicked.connect(self.on_search_jump)
        lay.addWidget(self.search_list, 1)

        tip = QLabel("单击/双击结果跳转并高亮\nF3=下一个 · Shift+F3=上一个")
        tip.setStyleSheet("color:#999; font-size:11px;")
        lay.addWidget(tip)
        self.search_dock.setWidget(w)

    def _on_scope_changed(self, i):
        pass

    def _show_left_panel(self, index, btn):
        """显示左侧堆栈的第 index 个面板，并选中对应活动栏按钮。"""
        self.left_stack.setCurrentIndex(index)
        self.left_stack.setVisible(True)
        if self.splitter.sizes()[0] < 60:
            self.splitter.setSizes([240, 960])
        self._set_activity_checked(btn, True)

    def _hide_left_panel(self, btn):
        """收起左侧堆栈并取消活动栏按钮。"""
        self.left_stack.setVisible(False)
        self.splitter.setSizes([0, 1])
        self._set_activity_checked(btn, False)

    def toggle_folders(self):
        """切换左侧文件夹面板的显示/隐藏。"""
        if self.left_stack.isVisible() and self.left_stack.currentIndex() == 0:
            self._hide_left_panel(self.btn_folders)
        else:
            self._show_left_panel(0, self.btn_folders)
            self.refresh_tree()

    def toggle_toc(self):
        """切换左侧目录（书签）面板的显示/隐藏。"""
        if self.left_stack.isVisible() and self.left_stack.currentIndex() == 1:
            self._hide_left_panel(self.btn_toc)
        else:
            self._show_left_panel(1, self.btn_toc)
            self.refresh_toc()

    def toggle_notes(self):
        """切换左侧笔记列表；打开时若有当前笔记则同时弹出右侧编辑器。"""
        if self.left_stack.isVisible() and self.left_stack.currentIndex() == 2:
            # 再次点击：收起左侧笔记列表（右侧编辑器保留，可单独关闭）
            self._hide_left_panel(self.btn_notes)
            return
        self._show_left_panel(2, self.btn_notes)
        nid = self.note_panel._current_note_id()
        if nid:
            self.note_panel.show_editor()

    def _set_activity_checked(self, btn, on):
        """程序化设置活动栏按钮选中态。
        互斥按钮组只在'真实点击'时自动取消其他按钮，程序化 setChecked 不会，
        且会拦截对当前选中按钮的 setChecked(False)。故临时关闭互斥：
        选中某按钮时同时取消另外两个；取消时只关自己。"""
        grp = self.activity_buttons
        grp.setExclusive(False)
        if on:
            for b in grp.buttons():
                b.setChecked(b is btn)
        else:
            btn.setChecked(False)
        grp.setExclusive(True)

    def refresh_toc(self):
        """从内存书签表(self.toc_items)生成目录树。根节点=文件名+页码范围。
        每个书签条目在 UserRole+1 存它在 toc_items 里的下标，供右键增删改。"""
        self.toc_tree.clear()
        if not self.doc:
            return
        base = os.path.splitext(os.path.basename(self.pdf_path))[0]
        root = QTreeWidgetItem(self.toc_tree, [f"{base} (1-{self.doc.page_count})"])
        root.setToolTip(0, self.pdf_path)
        root.setData(0, Qt.UserRole, None)

        toc = self.toc_items
        if not toc:
            it = QTreeWidgetItem(root, ["（该PDF无书签目录，可右键新增）"])
            it.setDisabled(True)
            it.setData(0, Qt.UserRole, None)
            root.setExpanded(True)
            return

        # 按层级递归构建树（lvl=1 为顶层，依次嵌套）
        def build(parent, level, idx):
            prev = None
            while idx[0] < len(toc):
                src = idx[0]
                lvl, title, pno = toc[src]
                if lvl < level:
                    return
                if lvl > level:
                    # 更深层级：挂到上一个条目下继续构建
                    if prev is not None:
                        build(prev, level + 1, idx)
                    else:
                        # 层级跳变异常（首条即深层），按当前层处理
                        it = self._mk_toc_item(parent, title, pno, src)
                        idx[0] += 1
                        prev = it
                    continue
                it = self._mk_toc_item(parent, title, pno, src)
                idx[0] += 1
                prev = it

        build(root, 1, [0])
        root.setExpanded(True)

    def _mk_toc_item(self, parent, title, pno, src_idx):
        it = QTreeWidgetItem(parent, [title.strip() or f"第{pno}页"])
        it.setData(0, Qt.UserRole, pno - 1 if pno >= 1 else None)
        it.setData(0, Qt.UserRole + 1, src_idx)   # 在 self.toc_items 里的下标
        if pno >= 1:
            it.setToolTip(0, f"跳转到第 {pno} 页（右键可编辑书签）")
        return it

    # ---------------- 目录（书签）右键编辑 ----------------
    def on_toc_context_menu(self, pos):
        item = self.toc_tree.itemAt(pos)
        m = QMenu(self)
        if item is None:
            # 空白处：新增顶层书签
            m.addAction("新增书签…", lambda: self.toc_add(None))
            if self._toc_clip:
                m.addAction("粘贴书签", lambda: self.toc_paste(None))
            m.exec(self.toc_tree.viewport().mapToGlobal(pos))
            return

        is_root = item.data(0, Qt.UserRole + 1) is None
        if is_root:
            m.addAction("新增书签…", lambda: self.toc_add(None))
            if self._toc_clip:
                m.addAction("粘贴书签", lambda: self.toc_paste(None))
            m.addSeparator()
            m.addAction("全部展开", self.toc_tree.expandAll)
            m.addAction("全部收起", self.toc_tree.collapseAll)
            m.exec(self.toc_tree.viewport().mapToGlobal(pos))
            return

        m.addAction("跳转到此页", lambda: self.on_toc_click(item))
        m.addSeparator()
        m.addAction("重命名…", lambda: self.toc_rename(item))
        m.addAction("复制书签", lambda: self.toc_copy(item))
        m.addAction("复制此页为图片", lambda: self.toc_copy_page_image(item))
        if self._toc_clip:
            m.addAction("粘贴为子书签", lambda: self.toc_paste(item))
        m.addSeparator()
        m.addAction("新增同级书签…", lambda: self.toc_add(item, same_level=True))
        m.addAction("新增子书签…", lambda: self.toc_add(item))
        m.addSeparator()
        m.addAction("删除书签及对应页…", lambda: self.toc_delete(item))
        m.exec(self.toc_tree.viewport().mapToGlobal(pos))

    def _toc_index(self, item):
        """取该条目的书签下标（在 self.toc_items 里）。根节点返回 None。"""
        src = item.data(0, Qt.UserRole + 1)
        if src is None or not (0 <= src < len(self.toc_items)):
            return None
        return src

    def _toc_mark_dirty(self):
        self.toc_dirty = True
        self.refresh_toc()

    def toc_add(self, ref_item, same_level=False):
        """新增书签。ref_item=None 表示加到顶层末尾；
        same_level=True 加为参照条目的同级（其后），否则作为其子书签。"""
        title, ok = QInputDialog.getText(
            self, "新增书签", "书签标题:",
            text=(f"第 {self.page_no + 1} 页"))
        if not ok or not title.strip():
            return
        pno = self.page_no + 1   # 指向当前页（1基）
        if ref_item is None:
            lvl = 1
            pos = len(self.toc_items)
        else:
            src = self._toc_index(ref_item)
            if src is None:
                return
            if same_level:
                lvl = self.toc_items[src][0]
                pos = src + 1
            else:
                lvl = self.toc_items[src][0] + 1
                pos = self._toc_subtree_end(src)
        self.toc_items.insert(pos, [lvl, title.strip(), pno])
        self._toc_mark_dirty()
        self.statusBar().showMessage(f"已新增书签：{title.strip()}")

    def toc_rename(self, item):
        src = self._toc_index(item)
        if src is None:
            return
        old = self.toc_items[src][1]
        title, ok = QInputDialog.getText(self, "重命名书签", "书签标题:", text=old)
        if ok and title.strip():
            self.toc_items[src][1] = title.strip()
            self._toc_mark_dirty()
            self.statusBar().showMessage("书签已重命名")

    def toc_delete(self, item):
        """删除书签（连同子树），并删除这些书签指向的 PDF 页；其余书签与批注自动重排。"""
        src = self._toc_index(item)
        if src is None:
            return
        end = self._toc_subtree_end(src)   # 连同子书签一起删
        removed = self.toc_items[src:end]
        n = len(removed)
        title = removed[0][1]
        # 这些书签指向的页（去重，0基当前页索引）
        del_idx = sorted({p - 1 for lvl, t, p in removed
                          if isinstance(p, int) and p >= 1}, reverse=True)
        del_idx = [i for i in del_idx if 0 <= i < self.doc.page_count]
        if self.doc.page_count - len(del_idx) < 1:
            QMessageBox.warning(self, "无法删除", "至少要保留一页。")
            return
        msg = f"删除书签“{title}”"
        if n > 1:
            msg += f"及其 {n - 1} 个子书签"
        if del_idx:
            pnums = "、".join(str(i + 1) for i in sorted(del_idx))
            msg += f"，并删除对应的 PDF 页（第 {pnums} 页）"
        msg += "？\n\n删除后其余书签与批注会自动重排，且需保存才会写入文件。"
        if not self._confirm(msg):
            return
        # 1) 先按当前页号算好重排，再真正删页（删页会改变页码基准）
        self._renumber_bookmarks_after_page_delete(del_idx)
        del self.toc_items[src:end]
        # 2) 删除页面、批注，并维护原始页映射
        self._remove_pages_by_current_index(del_idx)
        self._toc_mark_dirty()
        # 3) 当前页夹紧到有效范围并重绘
        self.page_no = max(0, min(self.page_no, self.doc.page_count - 1))
        self.page_spin.setRange(1, self.doc.page_count)
        self.load_page()
        self.statusBar().showMessage(
            f"已删除 {n} 条书签、{len(del_idx)} 页（保存后生效）")

    def _renumber_bookmarks_after_page_delete(self, del_idx):
        """页面删除后，把剩余书签的页码前移/夹紧，保持指向正确。"""
        dset = set(del_idx)
        max_idx = max(0, self.doc.page_count - len(dset) - 1)
        out = []
        for lvl, title, pno in self.toc_items:
            idx = pno - 1
            if idx in dset:
                idx = min(idx, max_idx)          # 指向被删页：就近落到剩余页
            else:
                idx -= sum(1 for d in dset if d < idx)
            out.append([lvl, title, max(0, min(idx, max_idx)) + 1])
        self.toc_items = out

    def _remove_pages_by_current_index(self, cur_indices):
        """从内存文档删除指定当前页索引，同步维护原始页映射与批注页码。"""
        if not self.doc or not cur_indices:
            return
        for ci in sorted(set(cur_indices), reverse=True):
            if not (0 <= ci < len(self.page_map)):
                continue
            self.deleted_pages.add(self.page_map[ci])
            del self.page_map[ci]
            if 0 <= ci < self.doc.page_count:
                self.doc.delete_page(ci)
            self.annos.pop(ci, None)
            self.annos = {k - 1 if k > ci else k: v for k, v in self.annos.items()}
        self._words_cache.clear()
        self._scene_page_rects.clear()

    def toc_copy(self, item):
        src = self._toc_index(item)
        if src is None:
            return
        lvl, title, pno = self.toc_items[src]
        self._toc_clip = {"lvl": lvl, "title": title, "pno": pno}
        self.statusBar().showMessage(f"已复制书签：{title}（可粘贴到别处）")

    def _toc_subtree_end(self, src):
        """返回下标 src 的子树结束位置（不包含）——即后续 lvl<=src.lvl 之前的所有项。"""
        base_lvl = self.toc_items[src][0]
        i = src + 1
        while i < len(self.toc_items) and self.toc_items[i][0] > base_lvl:
            i += 1
        return i

    def _confirm(self, text):
        return QMessageBox.question(
            self, "确认", text, QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No) == QMessageBox.Yes

    def toc_paste(self, ref_item):
        """把剪贴板书签粘贴到 ref_item 下作为子书签；ref_item=None 时粘到顶层末尾。"""
        if not self._toc_clip:
            return
        c = self._toc_clip
        if ref_item is None:
            lvl = 1
            pos = len(self.toc_items)
        else:
            src = self._toc_index(ref_item)
            if src is None:
                return
            lvl = self.toc_items[src][0] + 1
            pos = src + 1   # 作为第一个子书签插入
        self.toc_items.insert(pos, [lvl, c["title"], c["pno"]])
        self._toc_mark_dirty()
        self.statusBar().showMessage(f"已粘贴书签：{c['title']}")

    def toc_copy_page_image(self, item):
        """把该书签指向的PDF页面整页渲染成图片放进剪贴板。"""
        pno = item.data(0, Qt.UserRole)
        if not self.doc or pno is None or not (0 <= pno < self.doc.page_count):
            self.statusBar().showMessage("该书签没有有效页面")
            return
        page = self.doc[pno]
        mat = pymupdf.Matrix(2.0, 2.0)   # 2倍分辨率，粘贴到别处更清晰
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                     QImage.Format_RGB888).copy()
        QApplication.clipboard().setImage(img)
        self.statusBar().showMessage(
            f"已将第 {pno + 1} 页整页图片复制到剪贴板（{pix.width}×{pix.height}）")

    def on_toc_click(self, item, col=0):
        """点击目录条目跳转到对应页；点击根节点则展开/收起。"""
        pno = item.data(0, Qt.UserRole)
        if pno is None:
            # 根节点（文件标题）：切换展开状态（无原生箭头，靠这里切换）
            if item.childCount():
                item.setExpanded(not item.isExpanded())
                # 展开状态变化的箭头方向由委托重绘
                self.toc_tree.viewport().update()
            return
        if not self.doc:
            return
        if 0 <= pno < self.doc.page_count and pno != self.page_no:
            self.page_no = pno
            self.load_page()

    def toggle_search(self):
        # Markdown 模式：Ctrl+F 直接在 MD 面板内查找（PDF 搜索面板不适用）
        if self._view_mode == "md" and self.md_path:
            self._md_find_prompt()
            return
        if self.search_dock.isVisible():
            self.close_search()
        else:
            # 刷新范围下拉中的文件夹列表
            self.search_scope.blockSignals(True)
            self.search_scope.clear()
            self.search_scope.addItem("当前PDF文档")
            for f in folders_load():
                self.search_scope.addItem(f"📁 {os.path.basename(f) or f}", f)
            self.search_scope.blockSignals(False)
            # 选中的文字自动填入搜索框并搜索
            # 优先用 sel_text，为空时回退到剪贴板（选中文字时会自动复制）
            text = self.sel_text.strip()
            if not text:
                clip = QApplication.clipboard().text().strip()
                # 只接受合理长度的纯文本，排除明显非搜索内容
                if clip and len(clip) <= 200:
                    text = clip
            if text:
                self.search_input.setText(" ".join(text.split())[:60])
            self.search_dock.show()
            self.search_dock.raise_()
            self.search_input.setFocus()
            self.search_input.selectAll()
            # 有文字时自动搜索，无需再点按钮/回车
            if self.search_input.text().strip():
                self.do_search()

    def current_selection_text(self, max_len=500):
        """当前选中的文字：MD 面板选区（MD 模式）→ PDF 选区 → 剪贴板（兜底）。
        长度超过 max_len 的剪贴板内容视为无关（避免上次复制的大段文本）。"""
        if self._view_mode == "md":
            text = self.md_panel.selected_text()
            if text:
                return text
        text = (self.sel_text or "").strip()
        if not text:
            clip = QApplication.clipboard().text().strip()
            if clip and len(clip) <= max_len:
                text = clip
        return text

    def toggle_translate(self):
        """显示/隐藏右侧翻译面板（与搜索面板一致）。
        打开后，用'选择文字'工具选中文字即自动翻译；MD 阅读时选中文字同样可用。"""
        if self.translate_dock.isVisible():
            self.translate_dock.hide()
            return
        self.translate_dock.show()
        self.translate_dock.raise_()
        text = self.current_selection_text()
        if text:
            self.translate_panel.set_text(text)

    def _md_find_prompt(self):
        """Markdown 模式下的查找：优先用已选中/剪贴板文字，否则聚焦查找框。"""
        text = self.current_selection_text(max_len=100)
        if text and "\n" not in text:
            self.md_panel.search_input.setText(text)
            self.md_panel.find_text(text)
            self.statusBar().showMessage(f"在 Markdown 中查找: {text}", 3000)
        self.md_panel.search_input.setFocus()
        self.md_panel.search_input.selectAll()

    def _wire_md_panel(self):
        """连接 Markdown 面板内各按钮。"""
        mp = self.md_panel
        mp.btn_back_pdf.clicked.connect(lambda: self._back_to_pdf())
        mp.btn_zoom_in.clicked.connect(lambda: self.step_zoom(1))
        mp.btn_zoom_out.clicked.connect(lambda: self.step_zoom(-1))
        mp.btn_zoom_reset.clicked.connect(lambda: self.fit_to_width())
        mp.btn_copy_all.clicked.connect(self._md_copy_all)
        mp.btn_open_ext.clicked.connect(self._md_open_ext)
        mp.btn_close_md.clicked.connect(self.close_md)
        mp.search_input.returnPressed.connect(
            lambda: mp.find_text(mp.search_input.text()))

    def _back_to_pdf(self):
        """从 Markdown 返回 PDF 画布（无 PDF 时提示）。"""
        if not self.doc:
            self.statusBar().showMessage("尚未打开 PDF，可用工具栏“打开PDF”")
            return
        self._apply_view_mode("pdf")
        self.zoom_label.setText(f"  {int(self.user_zoom * 100)}%  ")
        self.load_page()

    def _md_copy_all(self):
        if self.md_panel.copy_all_text():
            self.statusBar().showMessage("已复制 Markdown 全文到剪贴板", 3000)

    def _md_open_ext(self):
        if self.md_path and os.path.isfile(self.md_path):
            try:
                os.startfile(self.md_path)  # noqa: S606
            except Exception as e:
                QMessageBox.warning(self, "无法打开", str(e))

    def close_md(self):
        """关闭当前 Markdown：有已打开的 PDF 则切回 PDF，否则显示空白画布。"""
        if not self.md_path:
            return
        self.md_panel.clear_file()
        self.md_path = ""
        if self.doc:
            self._apply_view_mode("pdf")
            self.zoom_label.setText(f"  {int(self.user_zoom * 100)}%  ")
            self.load_page()
            self.statusBar().showMessage("已关闭 Markdown，返回 PDF", 3000)
        else:
            self._apply_view_mode("pdf")
            self.statusBar().showMessage("已关闭 Markdown", 3000)

    @staticmethod
    def _sig_of(data):
        """根据会话数据计算编辑状态签名。"""
        try:
            anno = json.dumps(data["annos"], sort_keys=True, default=str)
        except Exception:
            anno = repr(data["annos"])
        return (anno, repr(data["toc_items"]),
                tuple(sorted(data["deleted_pages"])))

    def _data_has_unsaved(self, data):
        return data["doc"] is not None and \
            self._sig_of(data) != data["_saved_sig"]

    def _prompt_save_session(self, path, data):
        """对某个有未保存改动的会话弹保存框。
        返回: True=可继续关闭（已保存或选择放弃），False=取消关闭。"""
        name = os.path.basename(path)
        # 保存操作只能作用于"当前"文档；非当前文档先切换过去
        if self.pdf_path != path:
            self._switch_to_session(path)
        btn = QMessageBox.question(
            self, "未保存的改动",
            f"《{name}》有未保存的批注/书签改动，关闭前要保存吗？",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save)
        if btn == QMessageBox.Cancel:
            return False
        if btn == QMessageBox.Save and not self.save_pdf():
            return False   # 另存为被取消/失败：放弃关闭
        # 保存后把最新签名同步回会话（若该文档仍在 sessions）
        if path in self.sessions:
            self.sessions[path]["_saved_sig"] = self._state_sig()
            self.sessions[path]["annos"] = self.annos
            self.sessions[path]["toc_items"] = self.toc_items
            self.sessions[path]["deleted_pages"] = self.deleted_pages
        return True

    def _blank_canvas(self):
        """复位为无文档的空白画布（关闭最后一个 PDF 后）。"""
        self.doc = None
        self.pdf_path = ""
        self.page_no = 0
        self.annos = {}
        self.toc_items = []
        self.toc_dirty = False
        self._toc_clip = None
        self.deleted_pages = set()
        self.page_map = []
        self._saved_sig = None
        self._zoom_factor = 1.0
        self.sel_text = ""
        self.sel_hits = []
        self._words_cache = {}
        self._scene_page_rects = {}
        self._text_editor = None
        self.search_kw = ""
        self.search_results = []
        self.search_idx = -1
        self.clear_hit_marks()
        self.scene.clear()
        self.page_spin.setRange(1, 1)
        self.page_spin.setValue(1)
        self.page_label.setText("")
        self.refresh_toc()
        self._apply_view_mode("pdf")

    def _close_pdf_path(self, path):
        """关闭指定 PDF 会话并移除其标签（未保存先提示）。"""
        if path not in self.sessions:
            return
        self._closing_paths.add(path)   # 切走时禁止把该文档快照写回
        try:
            self._do_close_pdf_path(path)
        finally:
            self._closing_paths.discard(path)

    def _do_close_pdf_path(self, path):
        focus_path = self.pdf_path   # 关闭前用户正在看的文档，关掉非当前标签后回到它
        is_current = (focus_path == path)
        data = self._capture_session() if is_current else self.sessions[path]
        if self._data_has_unsaved(data):
            if not self._prompt_save_session(path, data):
                # 取消：若为此临时切到了该后台文档，切回原来正在看的文档
                if not is_current and focus_path in self.sessions and \
                        self.pdf_path != focus_path:
                    self._switch_to_session(focus_path)
                return   # 用户取消
            is_current = (self.pdf_path == path)
            if is_current:
                data = self._capture_session()
        # 关闭文档对象
        doc = data["doc"]
        for th in (getattr(self.translate_panel, "thread", None),
                   getattr(self, "search_thread", None)):
            try:
                if th is not None and th.isRunning():
                    th.wait(1200)
            except Exception:
                pass
        try:
            doc.close()
        except Exception:
            pass
        self.sessions.pop(path, None)
        # 移除标签（屏蔽信号，手动处理切换）
        idx = self._tab_index_of(path)
        blocked = self.doc_tabs.blockSignals(True)
        if idx >= 0:
            self.doc_tabs.removeTab(idx)
        remain = self.doc_tabs.count()
        self.doc_tabs.blockSignals(blocked)
        if remain == 0:
            self.doc_tabs.hide()
        # 决定关闭后显示哪个文档
        if focus_path and focus_path != path and focus_path in self.sessions:
            # 关掉的是后台标签：回到原来正在看的文档
            self._switch_to_session(focus_path)
        elif is_current:
            if remain > 0:
                new_idx = min(idx, remain - 1)
                new_path = self.doc_tabs.tabData(new_idx)
                blocked = self.doc_tabs.blockSignals(True)
                self.doc_tabs.setCurrentIndex(new_idx)
                self.doc_tabs.blockSignals(blocked)
                self._switch_to_session(new_path)
            else:
                # 没有 PDF 了：MD 仍开则显示 MD，否则空白
                self._blank_canvas()
                if self.md_path:
                    self._apply_view_mode("md")
        self.statusBar().showMessage(f"已关闭: {os.path.basename(path)}", 3000)

    def on_escape(self):
        """Esc：先退出内联文字编辑；搜索框打开时关闭搜索并清除高亮，否则取消文字选区。"""
        ed = getattr(self, "_text_editor", None)
        if ed is not None and ed.scene() is not None and not ed._done:
            ed.finish(cancel=True)
            self._text_editor = None
            return
        if self.search_dock.isVisible():
            self.close_search()
        elif self.translate_dock.isVisible():
            self.translate_dock.hide()
        else:
            self.clear_selection()

    def close_search(self):
        """关闭搜索框，停止搜索并清除全部搜索高亮与结果。"""
        self._stop_search()
        self.search_dock.hide()
        # 清除搜索状态：关键词、结果列表、命中高亮、当前命中框
        self.search_kw = ""
        self.search_results = []
        self.search_idx = -1
        self.search_list.clear()
        self.search_status.setText("")
        self.clear_hit_marks()

    def do_search(self):
        kw = self.search_input.text().strip()
        if not kw:
            self.search_status.setText("请输入关键词")
            return
        self._stop_search()
        self.search_list.clear()
        self.search_results = []
        self.search_kw = kw
        self.search_idx = -1
        whole = self.chk_whole.isChecked()
        case = self.chk_case.isChecked()
        toc = self.chk_toc.isChecked()
        scope = self.search_scope.currentIndex()

        if scope == 0:
            if not self.doc:
                self.search_status.setText("请先打开一个PDF")
                return
            files = [self.pdf_path]
        else:
            folder = self.search_scope.itemData(scope)
            files = self._collect_pdfs(folder)
            if not files:
                self.search_status.setText("该文件夹下没有PDF")
                return
        # 统一后台线程搜索（当前文档也走线程，避免大文档卡界面）
        self.search_status.setText(f"正在搜索 {len(files)} 个文件…")
        self.search_btn.setEnabled(False)
        self.search_thread = SearchThread(files, kw, whole, case, toc)
        self.search_thread.found.connect(self.on_search_found)
        self.search_thread.finished_all.connect(self.on_search_done)
        self.search_thread.start()

    def _stop_search(self):
        if self.search_thread and self.search_thread.isRunning():
            self.search_thread.stop()
            self.search_thread.wait(2000)
        self.search_btn.setEnabled(True)

    def _collect_pdfs(self, folder):
        out = []
        for root, dirs, files in os.walk(folder):
            for fn in files:
                if fn.lower().endswith(".pdf"):
                    out.append(os.path.join(root, fn))
        return out

    def on_search_found(self, rec):
        self.search_results.append(rec)
        # 换文件时插入分组标题
        if (len(self.search_results) > 1
                and self.search_results[-2]["file"] != rec["file"]):
            head = QListWidgetItem(f"── {os.path.basename(rec['file'])} ──")
            head.setData(Qt.UserRole, None)
            from PySide6.QtGui import QBrush as _QB
            head.setBackground(_QB(QColor("#dfe8f5")))
            self.search_list.addItem(head)
        kind = "🔖 书签" if rec.get("kind") == "toc" else f"第 {rec['page']+1} 页"
        snippet = rec.get("snippet", "")
        # 书签结果snippet就是标题本身，不再重复；文字结果snippet已含【命中词】
        text = f"{kind}  {snippet}".strip()
        it = QListWidgetItem(text)
        it.setToolTip(text)
        it.setData(Qt.UserRole, len(self.search_results) - 1)
        self.search_list.addItem(it)

    def on_search_done(self, count):
        self.search_btn.setEnabled(True)
        # 按文件统计
        per = {}
        for r in self.search_results:
            k = os.path.basename(r["file"])
            per[k] = per.get(k, 0) + 1
        if len(per) > 1:
            stat = " · ".join(f"{k}:{v}" for k, v in per.items())
            if len(stat) > 100:
                stat = f"{len(per)} 个文件"
            self.search_status.setText(f"找到 {count} 处 — {stat}")
        elif count:
            self.search_status.setText(f"找到 {count} 处结果")
        else:
            self.search_status.setText("没有找到匹配结果")
        self.render_page_hits()

    def on_search_jump(self, item):
        idx = item.data(Qt.UserRole)
        if idx is None or idx >= len(self.search_results):
            return
        self._jump_result(idx)

    def _jump_result(self, idx):
        rec = self.search_results[idx]
        self.search_idx = idx
        # 跨文件：先打开
        if rec["file"] != self.pdf_path:
            self.load_pdf_path(rec["file"])
        # 跳页
        if self.page_no != rec["page"]:
            self.page_no = rec["page"]
            self.load_page()
        # 滚动定位 + 高亮（书签结果无rect，滚到页顶）
        if rec.get("rect"):
            self._focus_rect(rec["rect"])
            # _focus_rect 只标了当前一处；重画本页全部命中（当前=橙，其余=黄）
            self.render_page_hits()
        else:
            self.view.verticalScrollBar().setValue(0)
        # 同步列表选中态
        for i in range(self.search_list.count()):
            if self.search_list.item(i).data(Qt.UserRole) == idx:
                self.search_list.setCurrentRow(i)
                break

    def search_next(self):
        """F3：下一个命中。"""
        if not self.search_results:
            return
        self.search_idx = (self.search_idx + 1) % len(self.search_results)
        self._jump_result(self.search_idx)

    def search_prev(self):
        """Shift+F3：上一个命中。"""
        if not self.search_results:
            return
        self.search_idx = (self.search_idx - 1) % len(self.search_results)
        self._jump_result(self.search_idx)

    def _focus_rect(self, rect_pdf):
        z = self.zoom
        cx = (rect_pdf[0] + rect_pdf[2]) / 2 * z
        cy = (rect_pdf[1] + rect_pdf[3]) / 2 * z
        # 让命中点居中
        self.view.centerOn(QPointF(cx, cy))
        # 高亮命中矩形（橙色描边+深黄底，区别于其他命中的黄色）
        self.clear_hit_marks()
        r = QRectF(rect_pdf[0] * z, rect_pdf[1] * z,
                   (rect_pdf[2] - rect_pdf[0]) * z,
                   (rect_pdf[3] - rect_pdf[1]) * z)
        fill = QGraphicsRectItem(r)
        fill.setBrush(QColor(255, 152, 0, 130))   # 橙色半透明
        fill.setPen(QPen(QColor(230, 81, 0), 2))
        fill.setZValue(21)
        self.scene.addItem(fill)
        self.search_hit_items = [fill]

    def clear_hit_marks(self):
        for it in self.search_hit_items:
            try:
                self.scene.removeItem(it)
            except Exception:
                pass
        self.search_hit_items = []

    def render_page_hits(self):
        """加载页面后：高亮当前页的全部搜索命中。
        当前命中的那一处用橙色，其余用黄色，一眼看出跳转位置。"""
        self.clear_hit_marks()
        if not self.search_kw or not self.doc:
            return
        z = self.zoom
        cur = self.search_results[self.search_idx] if \
            (0 <= self.search_idx < len(self.search_results)) else None
        for i, rec in enumerate(self.search_results):
            if (rec["file"] == self.pdf_path and rec["page"] == self.page_no
                    and rec.get("rect")):
                x0, y0, x1, y1 = rec["rect"]
                r = QRectF(x0 * z, y0 * z, (x1 - x0) * z, (y1 - y0) * z)
                fill = QGraphicsRectItem(r)
                if rec is cur:
                    fill.setBrush(QColor(255, 152, 0, 130))   # 当前命中：橙色
                    fill.setPen(QPen(QColor(230, 81, 0), 2))
                    fill.setZValue(21)
                else:
                    fill.setBrush(QColor(255, 235, 59, 90))   # 其他命中：黄色
                    fill.setPen(QPen(QColor(251, 192, 45), 1))
                    fill.setZValue(20)
                self.scene.addItem(fill)
                self.search_hit_items.append(fill)

    def turn_page(self, d, land=None):
        if not self.doc or self._view_mode == "md":
            return False
        np = self.page_no + d
        if 0 <= np < self.doc.page_count:
            self.page_no = np
            # 往回翻默认落页底衔接连续滚动；land=滚轮无缝翻页的精确落点
            self.load_page(scroll_bottom=(d < 0 and land is None), land=land)
            return True
        return False

    def on_spin(self, v):
        if self._loading or not self.doc:
            return
        if 1 <= v <= self.doc.page_count and v - 1 != self.page_no:
            self.page_no = v - 1
            self.load_page()

    def load_page(self, scroll_bottom=False, keep_scroll=False, land=None):
        self.commit_text_editor()  # 翻页/缩放前提交内联文字（防图元悬挂到错误页）
        if not self.doc:
            return
        page = self.doc[self.page_no]
        z = self.zoom  # 已含 RENDER_SCALE，直接高分辨率渲染不降采样
        # 高DPI屏（如150%缩放）：场景按逻辑像素布局，pixmap 按 物理分辨率 渲染
        # （×dpr），贴图时 setDevicePixelRatio(dpr) → 物理像素1:1显示，零拉伸不糊
        dpr = self.view.devicePixelRatioF()
        rz = z * dpr
        pix = page.get_pixmap(matrix=pymupdf.Matrix(rz, rz), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.width * 3,
                     QImage.Format_RGB888).copy()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        self.pixmap = pm

        self._loading = True
        self.page_spin.setValue(self.page_no + 1)
        self._loading = False
        self.page_label.setText(f" {self.page_no + 1}/{self.doc.page_count} ")
        self.zoom_label.setText(f"  {int(self.user_zoom * 100)}%  ")

        # 保留滚动值（keep_scroll=True 时不重置，sceneRect 更新由 Qt 自动钳制）
        vsb = self.view.verticalScrollBar()

        self.scene.clear()
        self.pix_item = self.scene.addPixmap(self.pixmap)
        self._neighbor_items = []  # 邻页预览图元，rebuild_items 时保留
        # 布局必须用逻辑尺寸（物理÷dpr）：带 DPR 的 pixmap 在场景中按逻辑大小
        # 绘制；若用物理像素布局，高DPI屏上场景比可见内容宽(高)出 dpr 倍，
        # 收起左面板视口变宽后页面就贴左不居中
        d = dpr if dpr > 0 else 1.0
        pw = self.pixmap.width() / d
        ph = self.pixmap.height() / d
        GAP = 16  # 页与页之间的间隔（浅灰底自然形成分隔，无需额外装饰）
        # ---- 相邻页预览（连续滚动视觉：同时看到上一页/下一页部分内容） ----
        self._prev_h = 0
        self._next_y = ph + GAP
        # 记录每页在场景中的矩形，供跨页文字选择换算坐标（阅读顺序：上页→当前→下页）
        self._scene_page_rects = {}
        # 页面细边框：每页四周一圈极细灰线，像"一张纸"贴在浅灰底上
        def add_page_border(y0):
            it = QGraphicsRectItem(QRectF(-0.5, y0 - 0.5, pw + 1, ph + 1))
            it.setBrush(QBrush(Qt.NoBrush))
            it.setPen(QPen(QColor("#c0c0c0"), 1))
            it.setZValue(1)    # 压在页面之上，边框才不会被 pixmap 盖住
            it.setAcceptedMouseButtons(Qt.NoButton)   # 纯装饰，不拦截鼠标
            self.scene.addItem(it)
            self._neighbor_items.append(it)
        if self.doc.page_count > 1:
            if self.page_no > 0:  # 上一页预览（显示在当前页上方）
                pp = self.doc[self.page_no - 1]
                prev_pix = pp.get_pixmap(matrix=pymupdf.Matrix(rz, rz), alpha=False)
                pimg = QImage(prev_pix.samples, prev_pix.width, prev_pix.height,
                              prev_pix.width * 3, QImage.Format_RGB888).copy()
                ppm = QPixmap.fromImage(pimg)
                ppm.setDevicePixelRatio(dpr)
                pitem = self.scene.addPixmap(ppm)
                pitem.setPos(0, -(ph + GAP))
                pitem.setZValue(0)
                self._neighbor_items.append(pitem)
                self._prev_h = ph + GAP
                self._scene_page_rects[self.page_no - 1] = pitem.sceneBoundingRect()
                add_page_border(-(ph + GAP))
            if self.page_no + 1 < self.doc.page_count:  # 下一页预览（当前页下方）
                np_ = self.doc[self.page_no + 1]
                npix = np_.get_pixmap(matrix=pymupdf.Matrix(rz, rz), alpha=False)
                nimg = QImage(npix.samples, npix.width, npix.height,
                              npix.width * 3, QImage.Format_RGB888).copy()
                npm = QPixmap.fromImage(nimg)
                npm.setDevicePixelRatio(dpr)
                nitem = self.scene.addPixmap(npm)
                nitem.setPos(0, self._next_y)
                nitem.setZValue(0)
                self._neighbor_items.append(nitem)
                self._scene_page_rects[self.page_no + 1] = nitem.sceneBoundingRect()
                add_page_border(self._next_y)
        add_page_border(0)   # 当前页边框
        self._scene_page_rects[self.page_no] = QRectF(0, 0, pw, ph)
        self.scene.setSceneRect(0, -(self._prev_h), pw, ph + self._prev_h + GAP + ph)
        self.view.setSceneRect(0, -(self._prev_h), pw, ph + self._prev_h + GAP + ph)
        self.preview_items = []
        self.sel_items = []      # scene.clear()已销毁旧图元，引用置空
        self.rebuild_items()
        self.render_selection()
        # 滚动位置：下滑翻页落在新页页顶；上滑翻页落在新页页底；缩放时不动
        # 必须同步设置：若用 QTimer 延迟，用户翻页后立刻拖选时定时器才触发，
        # 会把视图突然挪走导致选区错位（表现为"刚切到这页选不中、过一会才行"）
        if land is not None:
            # 无缝翻页落点：邻页预览坐标→当前页坐标，并钳制在本页范围内
            # （不落进邻页预览区，否则会"重看刚离开的页"）
            vsb.setValue(max(0, min(land, ph - self.view.viewport().height())))
        elif scroll_bottom:
            # 落在当前页页底（场景底部是下一页预览，不能用 maximum，否则重看刚离开的页）
            vsb.setValue(max(vsb.minimum(), ph - self.view.viewport().height()))
        elif not keep_scroll:
            # 落在当前页页顶 y=0（场景顶部 -(prev_h) 是上一页预览，会"重复上一页"）
            vsb.setValue(0)
        self.render_page_hits()
        # 目录树里"当前页"书签颜色随翻页更新
        if hasattr(self, "toc_tree"):
            self.toc_tree.viewport().update()

    # ---------------- 批注项生成 ----------------
    def _mkpen(self, hexc, w):
        p = QPen(QColor(hexc))
        p.setWidthF(max(1.0, w))
        p.setCapStyle(Qt.RoundCap)
        p.setJoinStyle(Qt.RoundJoin)
        return p

    def anno_items(self, a, z=None):
        """把一条批注(存PDF坐标)转成场景图元列表。"""
        z = z or self.zoom
        hexc, w = a["color"], max(1.0, a["wpt"] * z)
        t = a["type"]
        items = []
        if t == "rect":
            r = QRectF(QPointF(*a["p1"]), QPointF(*a["p2"])).normalized()
            r = QRectF(r.left() * z, r.top() * z, r.width() * z, r.height() * z)
            it = QGraphicsRectItem(r)
            it.setPen(self._mkpen(hexc, a["wpt"] * z))
            items.append(it)
        elif t == "oval":
            r = QRectF(QPointF(*a["p1"]), QPointF(*a["p2"])).normalized()
            r = QRectF(r.left() * z, r.top() * z, r.width() * z, r.height() * z)
            it = QGraphicsEllipseItem(r)
            it.setPen(self._mkpen(hexc, a["wpt"] * z))
            items.append(it)
        elif t == "line":
            it = QGraphicsLineItem(a["p1"][0] * z, a["p1"][1] * z,
                                   a["p2"][0] * z, a["p2"][1] * z)
            it.setPen(self._mkpen(hexc, a["wpt"] * z))
            items.append(it)
        elif t == "arrow":
            items.extend(self.arrow_items(
                QPointF(a["p1"][0] * z, a["p1"][1] * z),
                QPointF(a["p2"][0] * z, a["p2"][1] * z), hexc, a["wpt"] * z))
        elif t in ("pen", "highlight"):
            pts = [QPointF(x * z, y * z) for x, y in a["pts"]]
            if len(pts) >= 2:
                path = QPainterPath(pts[0])
                for p in pts[1:]:
                    path.lineTo(p)
                it = QGraphicsPathItem(path)
                pen = self._mkpen(hexc, HL_W * z if t == "highlight" else a["wpt"] * z)
                if t == "highlight":
                    c = QColor(hexc)
                    c.setAlpha(90)
                    pen.setColor(c)
                it.setPen(pen)
                items.append(it)
        elif t == "texthl":
            x0, y0, x1, y1 = a["rect"]
            it = QGraphicsRectItem(QRectF(x0 * z, y0 * z, (x1 - x0) * z, (y1 - y0) * z))
            it.setPen(QPen(Qt.NoPen))
            c = QColor(hexc)
            c.setAlpha(100)
            it.setBrush(QBrush(c))
            items.append(it)
        elif t == "text":
            it = QGraphicsTextItem(a["text"])
            it.setDefaultTextColor(QColor(hexc))
            it.setFont(QFont("Microsoft YaHei", max(6, int(a["fs"] * z))))
            it.setPos(a["p"][0] * z, a["p"][1] * z)
            it.setData(1, "text")  # 标记为文字批注（供缩放柄识别）
            items.append(it)
        for it in items:
            it.setFlag(QGraphicsItem.ItemIsSelectable, True)  # 可被"选择批注"选中
        return items

    def arrow_items(self, p1, p2, hexc, w):
        pen = self._mkpen(hexc, w)
        line = QGraphicsLineItem(QPointF(p1), QPointF(p2))
        line.setPen(pen)
        hl = max(10, w * 4)
        ang = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
        a1 = QPointF(p2.x() - hl * math.cos(ang - 0.42), p2.y() - hl * math.sin(ang - 0.42))
        a2 = QPointF(p2.x() - hl * math.cos(ang + 0.42), p2.y() - hl * math.sin(ang + 0.42))
        head = QGraphicsPolygonItem(QPolygonF([p2, a1, a2]))
        head.setPen(pen)
        head.setBrush(QColor(hexc))
        return [line, head]

    def rebuild_items(self):
        keep = {self.pix_item, *getattr(self, "_neighbor_items", [])}
        for it in self.scene.items():
            if it not in keep:
                self.scene.removeItem(it)
        for i, a in enumerate(self.annos.get(self.page_no, [])):
            for it in self.anno_items(a):
                it.setData(0, i)  # 图元 ↔ 批注列表索引 关联
                self.scene.addItem(it)

    def _add_anno_to_scene(self, a):
        """追加一条批注并登记索引关联。"""
        lst = self.annos.setdefault(self.page_no, [])
        lst.append(a)
        for it in self.anno_items(a):
            it.setData(0, len(lst) - 1)
            self.scene.addItem(it)

    def clear_preview(self):
        for it in self.preview_items:
            self.scene.removeItem(it)
        self.preview_items = []

    def add_preview(self, items):
        self.preview_items = items
        for it in items:
            self.scene.addItem(it)

    # ---------------- 鼠标事件 ----------------
    def scene_pos(self, e):
        # 注意: PySide6 的 mapToScene 只接受 QPoint（不接受 QPointF）
        return self.view.mapToScene(e.position().toPoint())

    def pdf_pos(self, sp):
        return sp.x() / self.zoom, sp.y() / self.zoom

    def on_press(self, e):
        if not self.doc or self.tool in ("view", "selanno"):
            return False  # selanno 交给场景默认处理（点选/框选批注）
        if e.button() != Qt.LeftButton:
            return False
        sp = self.scene_pos(e)
        if self.tool == "text":
            # 画布内联输入（类似图片编辑器）：已有编辑器则先提交旧的
            self.commit_text_editor()
            self._text_editor = TextEditorItem(self, sp, self.color_hex)
            self.statusBar().showMessage("输入文字，回车或点击空白处完成，Esc 取消")
            return True
        if self.tool == "selecttext":
            self.clear_selection()
        self.drawing = True
        self.start = sp
        self.free_pts = [sp]
        # 画笔/荧光笔：立即创建路径图元，后续 move 实时追加
        if self.tool in ("pen", "highlight"):
            path = QPainterPath()
            path.moveTo(sp)
            self._pen_item = QGraphicsPathItem(path)
            w = self.wpt * self.zoom
            if self.tool == "highlight":
                pen = self._mkpen(self.color_hex, HL_W * self.zoom)
                c = QColor(self.color_hex)
                c.setAlpha(90)
                pen.setColor(c)
            else:
                pen = self._mkpen(self.color_hex, w)
            self._pen_item.setPen(pen)
            self.scene.addItem(self._pen_item)
        return True

    def on_move(self, e):
        if not self.doc or self.tool == "view" or not self.drawing:
            return False
        sp = self.scene_pos(e)
        t = self.tool
        w = self.wpt * self.zoom
        self.clear_preview()
        p0 = self.start
        if t == "rect":
            r = QRectF(p0, sp).normalized()
            it = QGraphicsRectItem(r)
            it.setPen(self._mkpen(self.color_hex, w))
            self.add_preview([it])
        elif t == "oval":
            r = QRectF(p0, sp).normalized()
            it = QGraphicsEllipseItem(r)
            it.setPen(self._mkpen(self.color_hex, w))
            self.add_preview([it])
        elif t == "line":
            it = QGraphicsLineItem(QPointF(p0), QPointF(sp))
            it.setPen(self._mkpen(self.color_hex, w))
            self.add_preview([it])
        elif t == "arrow":
            self.add_preview(self.arrow_items(QPointF(p0), QPointF(sp),
                                              self.color_hex, w))
        elif t in ("pen", "highlight"):
            # 实时预览：按 free_pts 重建整条路径（不依赖增量 lineTo，避免起点丢失画到左上角）
            self.free_pts.append(sp)
            if self._pen_item is not None:
                path = QPainterPath(self.start)
                for q in self.free_pts[1:]:
                    path.lineTo(q)
                self._pen_item.setPath(path)
        elif t == "selecttext":
            r = QRectF(p0, sp).normalized()
            items = self.draw_word_rects(self.words_in_scene(r))
            box = QGraphicsRectItem(r)
            box.setPen(QPen(QColor("#1e88e5"), 1, Qt.DashLine))
            items.append(box)
            self.add_preview(items)
        elif t == "texthl":
            r = QRectF(p0, sp).normalized()
            it = QGraphicsRectItem(r)
            it.setPen(QPen(QColor(self.color_hex), 1, Qt.DashLine))
            self.add_preview([it])
        return True

    def on_release(self, e):
        if not self.drawing:
            return False
        self.drawing = False
        sp = self.scene_pos(e)
        t = self.tool
        rgb = hex_rgb(self.color_hex)
        self.clear_preview()
        p1 = self.pdf_pos(self.start)
        p2 = self.pdf_pos(sp)

        if t == "selecttext":
            hits = self.words_in_scene(QRectF(self.start, sp).normalized())
            if not hits:
                self.statusBar().showMessage("未选中文字（该区域没有文字层，扫描件请用荧光笔）")
                return True
            self.sel_hits = hits
            self.sel_text = self.words_to_text(hits)
            self.render_selection()
            QApplication.clipboard().setText(self.sel_text)
            preview = self.sel_text.replace("\n", " ")
            if len(preview) > 60:
                preview = preview[:60] + "..."
            # 搜索框已打开时，自动填入选中文字并立即搜索
            if self.search_dock.isVisible():
                self.search_input.setText(" ".join(self.sel_text.split())[:60])
                self.do_search()
                self.statusBar().showMessage(
                    f"已搜索: {preview}", 4000)
            # 翻译面板已打开时，自动翻译选中文字
            elif self.translate_dock.isVisible():
                self.translate_panel.set_text(self.sel_text)
                self.statusBar().showMessage(
                    f"已翻译: {preview}", 4000)
            else:
                self.statusBar().showMessage(
                    f"已选中并复制 {len(self.sel_text)} 字符（按H可转为高亮批注）: {preview}")
            return True

        if t in ("rect", "oval", "line", "arrow"):
            if (abs(sp.x() - self.start.x()) < 2 and
                    abs(sp.y() - self.start.y()) < 2):
                return True
            a = {"type": t, "p1": p1, "p2": p2, "color": self.color_hex,
                 "rgb": rgb, "wpt": self.wpt}
        elif t in ("pen", "highlight"):
            # 移除实时预览图元，转为正式批注
            if self._pen_item is not None:
                self.scene.removeItem(self._pen_item)
                self._pen_item = None
            if len(self.free_pts) < 2:
                return True  # 太短不保存
            pts = [self.pdf_pos(p) for p in self.free_pts]
            a = {"type": t, "pts": pts, "color": self.color_hex, "rgb": rgb,
                 "wpt": HL_W if t == "highlight" else self.wpt}
        elif t == "texthl":
            a_list = self.make_text_highlights(
                QRectF(QPointF(*p1), QPointF(*p2)).normalized())
            if not a_list:
                self.statusBar().showMessage(
                    "该区域未命中文字（文字高亮需拖过PDF真实文字）")
                return True
            for a in a_list:
                self._add_anno_to_scene(a)
            self.statusBar().showMessage(f"已高亮 {len(a_list)} 行文字")
            return True
        else:
            return True

        self._add_anno_to_scene(a)
        return True

    # ---------------- 文字选择/复制 ----------------
    def get_words(self, pno=None):
        """指定页文字词缓存（含坐标和内容），默认当前页。"""
        if pno is None:
            pno = self.page_no
        ws = self._words_cache.get(pno)
        if ws is None:
            ws = self.doc[pno].get_text("words")
            self._words_cache[pno] = ws
        return ws

    def _hit_words(self, pno, sel_rect):
        """在单页内返回与选区(该页PDF坐标)相交的词，保持阅读顺序。
        命中规则：选区与词框的竖向重叠 ≥ 词高35%（拖选罩住字的主体才算，擦边不算）。
        CAD导出PDF的词框比可见字形大很多（高17pt、字形仅10pt），阈值取35%既能让
        用户拖过文字时稳定命中，又能避免擦到相邻行（相邻行bbox仅重叠3pt≈17%）。"""
        page_h = self.doc[pno].rect.height if self.doc else 0
        out = []
        for w in self.get_words(pno):
            x0, y0, x1, y1 = w[:4]
            wr = QRectF(x0, y0, x1 - x0, y1 - y0)
            inter = sel_rect.intersected(wr)
            if inter.isEmpty():
                continue
            text = w[4].strip()
            # 目录点连线：独立点/连续点线直接忽略
            if LEADER_DOTS_RE.match(text):
                continue
            # 长纯符号线（----、____）忽略
            if len(text) >= 2 and LEADER_LINE_RE.match(text):
                continue
            wh = wr.height()
            ww = wr.width()
            # 命中判定沿"文字排列的垂直方向"：
            #   横排文字（含单个数字）：上下行相邻，按竖向覆盖判定
            #   竖排/旋转文字（原理图器件位号，如 HQ17101005GA0 高59pt）：左右相邻，
            #   按横向覆盖判定——否则用户横向拖选这一列时永远覆盖不到35%高度
            is_vert_text = wh > 20 and wh > ww * 1.8
            if is_vert_text:
                cov = inter.width() / ww if ww else 0
            else:
                cov = inter.height() / wh if wh else 0
            if cov < 0.35:
                continue
            # 页眉/页脚的独立数字（页码）：还要求横向覆盖40%，防拖动时误碰
            if (PAGE_NUM_RE.fullmatch(text)
                    and page_h and (y1 < 60 or y0 > page_h - 60)):
                ww = wr.width()
                if ww and inter.width() / ww < 0.4:
                    continue
            out.append(w)
        return out

    def words_in_scene(self, scene_rect):
        """跨页命中：选区(场景坐标)可能同时覆盖上/下邻页预览，逐页换算成该页
        PDF坐标后命中，按 上下页→当前页 的阅读顺序返回 [(页号, 词)]。"""
        hits = []
        for pno in sorted(self._scene_page_rects):
            pr = self._scene_page_rects[pno]
            inter = scene_rect.intersected(pr)
            if inter.isEmpty():
                continue
            # 场景坐标 → 该页 PDF 坐标
            pdf_r = QRectF((inter.left() - pr.left()) / self.zoom,
                           (inter.top() - pr.top()) / self.zoom,
                           inter.width() / self.zoom, inter.height() / self.zoom)
            for w in self._hit_words(pno, pdf_r):
                hits.append((pno, w))
        return hits

    def word_scene_rect(self, pno, w):
        """词(该页PDF坐标) → 场景坐标矩形（用于画选中高亮）。"""
        pr = self._scene_page_rects.get(pno)
        x0, y0, x1, y1 = w[:4]
        ox = pr.left() if pr else 0
        oy = pr.top() if pr else 0
        return QRectF(ox + x0 * self.zoom, oy + y0 * self.zoom,
                      (x1 - x0) * self.zoom, (y1 - y0) * self.zoom)

    def words_to_text(self, hits):
        """选中词按阅读顺序拼接：同行空格连接，换行换页都换行。"""
        lines, cur_key, cur = [], None, []
        for pno, w in hits:
            key = (pno, w[5], w[6])
            if key != cur_key:
                if cur:
                    lines.append(" ".join(cur))
                cur_key, cur = key, []
            cur.append(w[4])
        if cur:
            lines.append(" ".join(cur))
        return "\n".join(lines)

    def draw_word_rects(self, hits):
        """给命中的词画蓝色半透明高亮块（选择效果）。hits=[(页号, 词)]"""
        items = []
        for pno, w in hits:
            it = QGraphicsRectItem(self.word_scene_rect(pno, w))
            it.setPen(QPen(Qt.NoPen))
            c = QColor("#1e88e5")
            c.setAlpha(80)
            it.setBrush(QBrush(c))
            items.append(it)
        return items

    def clear_selection(self):
        for it in self.sel_items:
            self.scene.removeItem(it)
        self.sel_items = []
        self.sel_text = ""
        self.sel_hits = []

    def render_selection(self):
        """按场景坐标重绘当前选择（缩放/翻页后调用）。"""
        for it in self.sel_items:
            self.scene.removeItem(it)
        self.sel_items = []
        for pno, w in self.sel_hits:
            it = QGraphicsRectItem(self.word_scene_rect(pno, w))
            it.setPen(QPen(Qt.NoPen))
            c = QColor("#1e88e5")
            c.setAlpha(80)
            it.setBrush(QBrush(c))
            self.scene.addItem(it)
            self.sel_items.append(it)

    def copy_selection(self):
        if self._view_mode == "md":
            return   # MD 阅读时由 WebEngine 自带 Ctrl+C 复制
        if self.sel_text:
            QApplication.clipboard().setText(self.sel_text)
            self.statusBar().showMessage(
                f"已复制 {len(self.sel_text)} 字符（Ctrl+V 可粘贴）")

    def highlight_selection(self):
        """把'选择文字'工具拖选的内容转成高亮批注（按行合并，随PDF保存）。"""
        if self._view_mode == "md":
            return
        if not self.sel_hits:
            self.statusBar().showMessage(
                "请先用'选择文字'工具拖选文字，再按 H 或点'高亮选中'")
            return
        # 跨页选中时按页分别落到各自页面的批注里
        by_page = {}
        for pno, w in self.sel_hits:
            by_page.setdefault(pno, {}).setdefault((w[5], w[6]), []).append(w[:4])
        n = 0
        for pno, groups in by_page.items():
            lst = self.annos.setdefault(pno, [])
            for rects in groups.values():
                x0 = min(r[0] for r in rects)
                y0 = min(r[1] for r in rects)
                x1 = max(r[2] for r in rects)
                y1 = max(r[3] for r in rects)
                lst.append({"type": "texthl", "rect": (x0, y0, x1, y1),
                            "color": self.color_hex,
                            "rgb": hex_rgb(self.color_hex), "wpt": 1})
                n += 1
        self.rebuild_items()
        self.clear_selection()
        self.statusBar().showMessage(
            f"已将选中文字转为高亮批注（{n} 行，颜色：当前所选颜色）")

    def translate_selection(self):
        """Ctrl+T：开关右侧翻译面板（与 Ctrl+F 搜索一致）。
        面板打开后，用'选择文字'工具选中文字即自动翻译，无需再按键。"""
        self.toggle_translate()

    def open_translate_settings(self):
        """打开翻译设置（引擎/目标语言/百度密钥）。"""
        dlg = TranslateSettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            translate_save(dlg.result_cfg())
            self.statusBar().showMessage("翻译设置已保存")

    def open_note_settings(self):
        """打开笔记设置（存储目录/图片目录/默认导出格式），可迁移旧数据。"""
        old_cfg = notes_cfg_load()
        old_dir = old_cfg.get("storage_dir", "")
        old_img = old_cfg.get("img_dir", "")
        dlg = NoteSettingsDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        new_cfg = dlg.result_cfg()
        notes_cfg_save(new_cfg)
        # 迁移：目录有变化且勾选了迁移
        if dlg.chk_migrate.isChecked() and (
                new_cfg["storage_dir"] != old_dir or
                new_cfg["img_dir"] != old_img):
            self._migrate_notes_data(old_dir, new_cfg["storage_dir"],
                                     old_img, new_cfg["img_dir"])
        self.note_panel.refresh_list()
        self.statusBar().showMessage("笔记设置已保存", 3000)

    def _migrate_notes_data(self, old_dir, new_dir, old_img, new_img):
        """把旧位置的 .pdf_anno_notes.json 和图片复制到新位置。"""
        moved = []
        # 迁移笔记 JSON
        old_json = os.path.join(old_dir, ".pdf_anno_notes.json") if old_dir \
            else NOTES_FILE
        new_json = os.path.join(new_dir, ".pdf_anno_notes.json") if new_dir \
            else NOTES_FILE
        if os.path.abspath(old_json) != os.path.abspath(new_json):
            if os.path.exists(old_json):
                os.makedirs(os.path.dirname(new_json) or ".", exist_ok=True)
                try:
                    shutil.copy2(old_json, new_json)
                    moved.append("笔记数据")
                except Exception as e:
                    QMessageBox.warning(self, "迁移失败", f"复制笔记数据：{e}")
        # 迁移图片
        old_img_dir = old_img if old_img else NOTES_IMG_DIR
        new_img_dir = new_img if new_img else NOTES_IMG_DIR
        if os.path.abspath(old_img_dir) != os.path.abspath(new_img_dir):
            if os.path.isdir(old_img_dir):
                os.makedirs(new_img_dir, exist_ok=True)
                cnt = 0
                for name in os.listdir(old_img_dir):
                    s = os.path.join(old_img_dir, name)
                    d = os.path.join(new_img_dir, name)
                    if os.path.isfile(s) and not os.path.exists(d):
                        try:
                            shutil.copy2(s, d); cnt += 1
                        except Exception:
                            pass
                if cnt:
                    moved.append(f"{cnt} 张图片")
        if moved:
            QMessageBox.information(self, "迁移完成",
                "已把 " + "、".join(moved) + " 复制到新位置。\n"
                "旧位置的文件保留作为备份，可手动删除。")

    def delete_selected(self):
        """删除'选择批注'工具选中的批注（Delete/Backspace）。
        Markdown 模式下不拦截按键（WebEngine 输入框内删除文字不受影响）。"""
        if self._view_mode == "md":
            return
        lst = self.annos.get(self.page_no)
        if not lst:
            self.statusBar().showMessage("本页没有批注可删除")
            return
        idxs = {it.data(0) for it in self.scene.selectedItems()
                if it.data(0) is not None}
        if not idxs:
            self.statusBar().showMessage(
                "请先用'选择批注'工具点选/框选要删除的批注（可多选）")
            return
        n = 0
        for i in sorted(idxs, reverse=True):
            if isinstance(i, int) and 0 <= i < len(lst) and lst[i] is not None:
                lst[i] = None
                n += 1
        self.annos[self.page_no] = [a for a in lst if a]
        self.rebuild_items()
        self.statusBar().showMessage(f"已删除 {n} 条批注")

    def make_text_highlights(self, sel_rect):
        """查找与选区相交的文字词，按行合并成高亮批注。sel_rect为PDF坐标。"""
        words = self.get_words()
        groups = {}
        for w in words:
            x0, y0, x1, y1, word, blk, line, wno = w[:8]
            wr = QRectF(x0, y0, x1 - x0, y1 - y0)
            if wr.intersects(sel_rect):
                groups.setdefault((blk, line), []).append(wr)
        result = []
        for rects in groups.values():
            u = rects[0]
            for r in rects[1:]:
                u = u.united(r)
            result.append({"type": "texthl",
                           "rect": (u.left(), u.top(), u.right(), u.bottom()),
                           "color": self.color_hex,
                           "rgb": hex_rgb(self.color_hex), "wpt": 1})
        return result

    # ---------------- 撤销/清除/保存 ----------------
    def commit_text_editor(self):
        """提交正在进行的内联文字编辑（若有）。"""
        ed = getattr(self, "_text_editor", None)
        if ed is not None and ed.scene() is not None and not ed._done:
            ed.commit()
        self._text_editor = None

    def on_text_editor_closed(self):
        self._text_editor = None

    def undo(self):
        if self._view_mode == "md":
            return
        lst = self.annos.get(self.page_no)
        if not lst:
            self.statusBar().showMessage("本页没有可撤销的批注")
            return
        lst.pop()
        self.rebuild_items()

    def clear_page(self):
        if self._view_mode == "md":
            return
        if self.annos.get(self.page_no):
            self.annos[self.page_no] = []
            self.rebuild_items()

    def _state_sig(self):
        """当前编辑状态签名（批注/书签/删页），用于判断是否有未保存改动。
        保存后重新取签名即可，无需在每个修改点插桩。"""
        try:
            anno = json.dumps(self.annos, sort_keys=True, default=str)
        except Exception:
            anno = repr(self.annos)
        return (anno, repr(self.toc_items), tuple(sorted(self.deleted_pages)))

    def has_unsaved(self):
        """是否有未保存的改动（批注 / 书签 / 删页）。"""
        return self.doc is not None and self._state_sig() != self._saved_sig

    def save_pdf(self):
        """保存批注/书签/删页。返回 True=已写入文件，False=用户取消或失败。"""
        if not self.doc:
            return False
        if not self.has_unsaved():
            QMessageBox.information(self, "提示", "还没有任何批注或书签改动")
            return False
        base = os.path.splitext(os.path.basename(self.pdf_path))[0]
        out, _ = QFileDialog.getSaveFileName(self, "保存批注PDF",
                                             f"{base}_批注.pdf", "PDF 文件 (*.pdf)")
        if not out:
            return False
        try:
            # 保存始终从原始 pdf_path 出发（先删页再写批注/书签），
            # 删页后新文档的页序与界面当前页序一致，故当前索引可直接套用；
            # 重复保存不会把批注叠加到已含批注的输出文件上
            save_annotations(self.pdf_path, self.annos, out,
                             toc=self.toc_items if self.toc_dirty else None,
                             deleted_pages=self.deleted_pages or None)
            self._saved_sig = self._state_sig()   # 记录已保存状态，供退出提示比对
            self.statusBar().showMessage(f"已保存: {out}")
            QMessageBox.information(self, "完成", f"批注已嵌入并保存:\n{out}")
            return True
        except Exception as e:
            QMessageBox.critical(self, "出错", f"保存失败: {e}")
            return False

    def closeEvent(self, e):
        """退出前逐个处理所有打开 PDF 的未保存改动；再收尾线程/文档。"""
        # 0) 保存当前笔记
        self.note_panel.save_current()
        # 1) 先把当前文档状态写回其会话
        self._store_current_session()
        # 2) 记录当前会话，便于用户取消时恢复显示
        current_path = self.pdf_path
        # 3) 逐个提示有未保存改动的文档
        dirty = [p for p, d in self.sessions.items()
                 if self._data_has_unsaved(d)]
        for path in dirty:
            data = self.sessions[path]
            if not self._data_has_unsaved(data):
                continue   # 前一轮保存可能已连带更新
            if self.pdf_path != path:
                self._switch_to_session(path)
            name = os.path.basename(path)
            btn = QMessageBox.question(
                self, "未保存的改动",
                f"《{name}》有未保存的批注/书签改动，退出前要保存吗？",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save)
            if btn == QMessageBox.Cancel:
                # 恢复到退出前正在看的文档，放弃退出
                if current_path and current_path in self.sessions and \
                        self.pdf_path != current_path:
                    self._switch_to_session(current_path)
                e.ignore()
                return
            if btn == QMessageBox.Save:
                if not self.save_pdf():
                    # 另存为取消/失败：不退出，避免丢改动
                    if current_path and current_path in self.sessions and \
                            self.pdf_path != current_path:
                        self._switch_to_session(current_path)
                    e.ignore()
                    return
                self._store_current_session()   # 保存后回写最新签名
        # 4) 收尾后台线程
        for th in (getattr(self.translate_panel, "thread", None),
                   getattr(self, "search_thread", None)):
            try:
                if th is not None and th.isRunning():
                    th.wait(1500)
            except Exception:
                pass
        # 5) 关闭全部文档对象
        for p, d in list(self.sessions.items()):
            try:
                if d.get("doc") is not None:
                    d["doc"].close()
            except Exception:
                pass
        # 关闭后必须置 None：pymupdf 的 Document 关闭后 len() 会抛错，
        # 而 `not self.doc` 会退化为 len() 判断；退出时延迟触发的
        # fit_to_width/load_page 若拿到已关闭的 doc 就会崩溃
        self.doc = None
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
