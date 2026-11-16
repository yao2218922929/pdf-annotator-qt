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
import copy
from collections import OrderedDict

import pymupdf
from PySide6.QtCore import (QEvent, QMimeData, QPoint, QPointF, QRect, QRectF,
                            QSize, Qt, QTimer, QThread, Signal, QUrl)
from PySide6.QtGui import (QAction, QBrush, QColor, QCursor, QDrag, QFont, QIcon,
                           QImage, QImageReader, QKeySequence, QMouseEvent,
                           QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                           QShortcut, QTextBlockFormat, QTextCharFormat,
                           QTextCursor, QTextDocument,
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

# ---------------- 门面：从 pdfanno 包重新导出 ----------------
# 源码已按模块拆分到 pdfanno/ 包中；此处重新导出，保持 `import
# pdf_annotator_qt as M` 的旧路径继续可用（测试与本文件内部均依赖）。
from pdfanno.constants import (
    COLORS, TEXT_FS, HL_W, ANNO_SUBJECT, ANNO_DATA_NAME,
    WIDTHS, RENDER_SCALE, PAGE_PIX_BUDGET, MAX_PIX_DIM, REGION_GRID,
    REGION_CACHE_MAX, WORDS_CACHE_MAX, REGION_PIX_BUDGET, REGION_MAX_PIXELS,
    ZOOM_RENDER_STEP, ZOOM_REFINE_MS, PAGE_GAP, TOOLS,
    SHAPE_ITEMS, SHAPE_VALS, SHAPE_NAMES,
    LEADER_DOTS_RE, LEADER_LINE_RE, PAGE_NUM_RE, HIER_TITLE_RE,
    ALT_RECT_RE, ALT_ACTION_RE, ALT_NAME_RE, ALT_DST_RE, ALT_INSTANCE_RE)
from pdfanno.config import (
    COLORS_FILE, _SAVE_ERRORS, _safe_write_json, flush_save_errors,
    colors_load, colors_save, HISTORY_FILE, HISTORY_MAX, history_load,
    history_save, history_add, history_remove, history_clear,
    PROGRESS_FILE, PROGRESS_MAX, progress_load, progress_get, progress_set,
    FOLDERS_FILE, folders_load, folders_save,
    SHORTCUTS_FILE, SHORTCUT_ACTIONS, shortcuts_load, shortcuts_save,
    NOTES_FILE, NOTES_IMG_DIR, NOTES_CFG_FILE, notes_cfg_load, notes_cfg_save,
    notes_file_path, notes_img_dir_path)
from pdfanno.textutil import hex_rgb, join_words_text
from pdfanno.translate import (
    TRANSLATE_FILE, TRANSLATE_LANGS, LANG_ALIAS, translate_load,
    translate_save, detect_lang, _http_get, translate_text,
    _translate_google, _translate_baidu, TranslateThread,
    TranslatePanel, TranslateSettingsDialog)
from pdfanno.dialogs import NoteSettingsDialog, ShortcutDialog
from pdfanno.note_panel import (HeadingGutter, NoteEditor, FloatingFormatBar,
                                NotePanel)
from pdfanno.markdown_panel import (HAS_WEBENGINE, MD_EXTS, MD_CSS,
                                    _md_exts_ok, md_to_html, md_read_text,
                                    MdPanel)
from pdfanno.pdfcore import (open_pdf_doc, _strip_pdfanno_annots,
                            _write_one_anno, save_annotations,
                            load_embedded_annos, _anno_from_annot)
from pdfanno.toc import TocDelegate
from pdfanno.canvas import TextEditorItem, Canvas
from pdfanno.search import SearchThread
from pdfanno.ocr import OcrMixin, OcrDialog, OcrThread
from pdfanno.hier_mixin import HierMixin


from pdfanno.ui_utils import make_tab_close_icon
from pdfanno.panel_mixin import PanelMixin
from pdfanno.ui_mixin import UiMixin
from pdfanno.anno_mixin import AnnoMixin
from pdfanno.session_mixin import SessionMixin
from pdfanno.render_mixin import RenderMixin
from pdfanno.tree_mixin import TreeMixin
from pdfanno.annolist_mixin import AnnoListMixin
from pdfanno.search_mixin import SearchMixin


class MainWindow(PanelMixin, UiMixin, AnnoMixin, SessionMixin, RenderMixin, TreeMixin, AnnoListMixin, SearchMixin, OcrMixin, HierMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF 批注工具 (Qt版)")
        self.resize(1200, 850)

        self.doc = None
        self.pdf_path = ""
        self.pdf_password = ""    # 加密 PDF 的打开密码（保存批注时复用）
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
        self._draw_page = 0        # 本次绘制落在哪一页（连续滚动时≠当前页）
        self.free_pts = []
        self.preview_items = []
        self._pen_item = None      # 画笔/荧光笔的实时路径图元
        self._undo_stack = []      # 跨页撤销栈：[{page, before, after}]（批注深拷贝）
        self._redo_stack = []      # 跨页重做栈

        self.sel_items = []        # 文字选择高亮图元
        self.sel_text = ""         # 当前选中的文字
        self.sel_hits = []         # 选中词 [(页号, 词数据)]，跨页选择时含上下邻页的词
        self._words_cache = OrderedDict()   # 页号 -> 词表（按访问顺序有限保留）
        self._scene_page_rects = {}  # 页号 -> 该页在场景中的矩形（整篇文档坐标系）
        self._page_tops = []       # 页号 -> 该页顶边在场景中的 y（整篇文档坐标系）
        self._page_lefts = []      # 页号 -> 该页左边在场景中的 x（各页横向居中）
        self._page_sizes = []      # 页号 -> (宽, 高)（场景单位）
        self._raw_page_sizes = None  # 页号 -> (宽, 高)（PDF 原始尺寸，缓存避免每档重取）
        self._doc_scene_h = 0.0    # 整篇文档场景总高
        self._doc_scene_w = 0.0    # 整篇文档场景总宽（取最宽页）
        self._sync_scroll = False  # 程序内调整滚动条时屏蔽"滚动→切页"联动
        self._rendering = False    # 正在重建场景/窗口化渲染（屏蔽滚动信号重入）
        self._text_editor = None   # 画布内联文字输入框（"文字"工具）
        self._zooming_fast = False  # 快速连续缩放中：按档位渲染（不低于屏幕分辨率）
        self._hier_index = None     # Altium 层级子图索引（书签推断，见 _ensure_hier_index）
        self._hier_sig = -1         # 构建时的书签签名（书签变化时触发重建）
        self._alt_map = {}          # 页号 -> [(条目, 点击矩形)]（Altium 原生 Descend 链接）
        self._alt_parent = {}       # 子图页 -> 父页（来自原生链接）
        self._alt_sig = -1          # 缓存对应的 doc id
        self._alt_doc = None        # 缓存对应的 doc 对象（防 id 复用）
        self._alt_ready = False

        self.scene = QGraphicsScene(self)
        self.view = Canvas(self)
        self._page_pix_items = {}   # 页号 -> [页面图元, 边框图元]（窗口化）
        self._anno_items_map = {}   # 页号 -> 该页批注图元（窗口化）
        self._page_render_sig = {}  # 页号 -> 当前已渲染内容的签名（判断是否需要重渲）
        # 滚动条变化 → 更新当前页 + 窗口化渲染（拖动滚动条即"整篇翻阅"）
        # 渲染经 0ms 定时器推迟到事件循环，避免在滚动回调里增删图元
        self._win_timer = QTimer(self)
        self._win_timer.setSingleShot(True)
        self._win_timer.setInterval(0)
        self._win_timer.timeout.connect(self._apply_scroll_state)
        self.view.verticalScrollBar().valueChanged.connect(self._on_view_scrolled)
        # 水平滚动同样要触发窗口化渲染：区域渲染的范围按水平滚动值计算，
        # 超宽页（原理图等）左右拖动若不重渲，会露出未渲染的灰底。
        self.view.horizontalScrollBar().valueChanged.connect(self._on_view_scrolled)
        # 快速缩放结束后重渲染清晰版（缩放期间按 1.25 档位渲染以复用缓存）
        self._zoom_refine_timer = QTimer(self)
        self._zoom_refine_timer.setSingleShot(True)
        self._zoom_refine_timer.setInterval(ZOOM_REFINE_MS)
        self._zoom_refine_timer.timeout.connect(self._refine_zoom_render)
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

        # ---- 左侧批注列表（总览 + 点击定位） ----
        self.anno_list_ui = QWidget()
        alv = QVBoxLayout(self.anno_list_ui)
        alv.setContentsMargins(0, 0, 0, 0)
        alv.setSpacing(0)
        self.anno_list_hint = QLabel("  批注列表")
        self.anno_list_hint.setStyleSheet(
            "QLabel{background:#f3f3f3; color:#555; padding:6px 4px;"
            " border-bottom:1px solid #e0e0e0; font-weight:bold;}")
        alv.addWidget(self.anno_list_hint)
        self.anno_list = QListWidget()
        self.anno_list.setStyle(QStyleFactory.create("Fusion"))
        self.anno_list.setStyleSheet(
            "QListWidget{background:#ffffff; border:none; outline:none;}"
            "QListWidget::item{padding:6px 8px; border-bottom:1px solid #f0f0f0;}"
            "QListWidget::item:selected{background:#e3f2fd; color:#0d47a1;}")
        self.anno_list.itemClicked.connect(self._on_anno_list_click)
        self.anno_list.itemDoubleClicked.connect(self._on_anno_list_click)
        self.anno_list.setFocusPolicy(Qt.ClickFocus)  # 点击后获得焦点，供 Delete 删除条目
        alv.addWidget(self.anno_list, 1)

        # ---- 主布局：左侧活动栏 | splitter(左侧面板 + 内容区) ----
        # 笔记面板逻辑对象：UI 拆为 列表(并入左侧堆栈) + 编辑器(右侧停靠窗)
        self.note_panel = NotePanel(self)
        # 左侧面板用堆栈窗口：文件夹树 / 目录树 / 笔记列表 三选一显示
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self.tree)              # index 0: 文件夹
        self.left_stack.addWidget(self.toc_tree)          # index 1: 目录
        self.left_stack.addWidget(self.note_panel.list_ui)  # index 2: 笔记列表
        self.left_stack.addWidget(self.anno_list_ui)      # index 3: 批注列表
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
        self.btn_anno_list = add_activity("🖍", "批注列表面板（总览+定位）",
                                          self.toggle_anno_list, checkable=True)
        self.btn_notes = add_activity("📝", "笔记面板 (Ctrl+J)",
                                      self.toggle_notes, checkable=True)
        add_activity("🔍", "搜索 (Ctrl+F)", self.toggle_search)
        add_activity("🌐", "翻译选中文字 (Ctrl+T)", self.toggle_translate)
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
        self._page_pix_cache = OrderedDict()   # (页号, 瓦片k, 瓦片j, 渲染档位) -> QPixmap
        self._restored_progress = False
        # 阅读进度落盘（防抖：翻页/缩放停止 500ms 后写一次）
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.timeout.connect(self._save_progress)
        self._loading = False

        self.build_toolbar()
        self._wire_md_panel()
        self.build_shortcuts()
        self.pick_tool(self.tool)
        self.refresh_tree()
        self.statusBar().showMessage(
            "请打开PDF或双击左侧文件 ｜ 滚轮=翻页 · Ctrl+滚轮=缩放 · 拖选文字自动复制,按H转高亮 · "
            "'选择批注'工具点选后Delete删除 · Ctrl+Z撤销")


    # 循环切换的三个常用工具（Tab）
    CYCLE_TOOLS = ("view", "selecttext", "selanno")


    # 每个 PDF 会话需要独立保存的字段（工具/颜色/搜索面板为全局共享，不快照）
    SESSION_FIELDS = ("doc", "pdf_path", "pdf_password", "page_no", "_zoom_factor",
                      "annos", "toc_items", "toc_dirty", "_toc_clip",
                      "deleted_pages", "page_map", "_saved_sig",
                      "_undo_stack", "_redo_stack")


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
        ox, oy = self._page_origin(self.page_no)
        cur = self.search_results[self.search_idx] if \
            (0 <= self.search_idx < len(self.search_results)) else None
        for i, rec in enumerate(self.search_results):
            if (rec["file"] == self.pdf_path and rec["page"] == self.page_no
                    and rec.get("rect")):
                x0, y0, x1, y1 = rec["rect"]
                r = QRectF(ox + x0 * z, oy + y0 * z,
                           (x1 - x0) * z, (y1 - y0) * z)
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


def pdfs_from_argv(argv):
    """从命令行参数里挑出 PDF 路径（右键"打开方式"/拖到图标时会传入）。
    忽略选项参数（以 - 开头）与非 .pdf 的参数。"""
    out = []
    for a in argv:
        if not a or a.startswith("-"):
            continue
        if a.lower().endswith(".pdf"):
            out.append(a)
    return out


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    # 支持"右键→打开方式 / 拖到图标 / 关联打开"传入的 PDF 路径：
    # Windows 会把被打开的文件绝对路径作为命令行参数传进来
    argv_pdfs = pdfs_from_argv(sys.argv[1:])
    if argv_pdfs:
        # 延迟到事件循环启动后加载：此时窗口尺寸/视口已就绪。
        # 拖入/关联多个文件时逐个打开为标签页（第一个加载完成后依次打开其余）。
        def _open_all():
            for p in argv_pdfs:
                if os.path.isfile(p):
                    win.load_pdf_path(p)
        QTimer.singleShot(0, _open_all)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
