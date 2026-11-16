# -*- coding: utf-8 -*-
"""MainWindow uimixin mixin（拆分第4步）。"""
import json
import math
import os
import shutil
import copy

from PySide6.QtCore import (Qt)
from PySide6.QtGui import (QAction, QColor, QKeySequence, QShortcut)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QColorDialog, QComboBox, QDialog, QGraphicsView, QKeySequenceEdit, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox, QToolBar, QToolButton)
from .config import (NOTES_FILE, NOTES_IMG_DIR, SHORTCUTS_FILE, colors_load, colors_save, notes_cfg_load, notes_cfg_save, shortcuts_load, shortcuts_save)
from .constants import (RENDER_SCALE, SHAPE_ITEMS, SHAPE_NAMES, SHAPE_VALS, TEXT_FS, TOOLS, WIDTHS)
from .dialogs import (NoteSettingsDialog, ShortcutDialog)
from .translate import (TranslateSettingsDialog, translate_save)


class UiMixin:
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
        # 保存：下拉提供"保存到原文件 / 另存为"，与 Ctrl+S / Ctrl+Shift+S 对应
        save_btn = QToolButton()
        save_btn.setText("保存 ▾")
        save_btn.setToolTip("保存批注到原文件（Ctrl+S）或另存为新 PDF（Ctrl+Shift+S）")
        save_btn.setPopupMode(QToolButton.InstantPopup)
        save_menu = QMenu(save_btn)
        act_inplace = save_menu.addAction("保存到原文件（Ctrl+S）")
        act_inplace.triggered.connect(self.save_pdf_inplace)
        act_saveas = save_menu.addAction("另存为批注PDF…（Ctrl+Shift+S）")
        act_saveas.triggered.connect(self.save_pdf)
        save_btn.setMenu(save_menu)
        tb.addWidget(save_btn)
        self.pdf_only_widgets.append(save_btn)
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
        self.shape_btn.setMinimumWidth(72)   # 固定宽度：文字在画笔/荧光笔间切换时不抖动
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
        add_text2("OCR 文字层", "为扫描版 PDF 添加可选中/可搜索的文字层（OCR）",
                  self.ocr_pdf)
        add_text2("删除批注(Del)", "点选/框选批注后按Delete删除；左侧批注列表中选中条目后按Delete也可删除",
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

        self._undo_action = add_text2(
            "撤销", "撤销最近的批注改动（可跨页，Ctrl+Z）", self.undo)
        self._redo_action = add_text2(
            "重做", "重做被撤销的批注改动 (Ctrl+Y)", self.redo)
        add_text2("清除本页", "清除本页全部批注", self.clear_page)
        self._sync_history_actions()

    def _sync_history_actions(self):
        """撤销/重做按钮可用性跟随历史栈（空栈时置灰，避免点了才提示）。"""
        u = getattr(self, "_undo_action", None)
        r = getattr(self, "_redo_action", None)
        if u is not None:
            u.setEnabled(bool(self._undo_stack))
        if r is not None:
            r.setEnabled(bool(self._redo_stack))

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
            "redo": self.redo,
            "copy": self.copy_selection,
            "highlight": self.highlight_selection,
            "delete_anno": self.delete_anno_dispatch,
            "open_pdf": self.open_pdf,
            "save_pdf": self.save_pdf_inplace,
            "save_pdf_as": self.save_pdf,
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
            "prev_page", "next_page", "undo", "redo", "copy", "highlight",
            "delete_anno",
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
        for seq, cb in (("Backspace", self.delete_anno_dispatch),
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
                self.shape_btn.setText(nm)   # 按钮文字跟随当前画笔
                self.shape_btn.setToolTip(
                    f"画笔：{nm}（左键使用，右键切换：画笔/荧光笔/矩形/椭圆/直线/箭头）")
            if hasattr(self, "_shape_actions") and val in self._shape_actions:
                self._shape_actions[val].setChecked(True)
            # 画笔与工具栏普通工具互斥：清掉工具组里的选中项。
            # （画笔按钮不在 tool_group 里，不手动清会两个按钮同时高亮；
            #   exclusive 组默认不允许"全部取消"，需临时关掉）
            g = getattr(self, "tool_group", None)
            if g is not None:
                was_exclusive = g.isExclusive()
                g.setExclusive(False)
                for a in g.actions():
                    a.setChecked(False)
                g.setExclusive(was_exclusive)
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
        # 本程序不用 view.scale()，场景坐标 = 视口坐标 + 滚动条值。
        # 求出鼠标下那一"页内点"，缩放后让它仍落在鼠标位置（锚点不动）。
        zs = self.zoom  # 缩放前场景系数
        sb0 = self.view.verticalScrollBar().value()
        hb0 = self.view.horizontalScrollBar().value()
        scene_y = sb0 + vy
        pno = self._page_at_scroll(scene_y)
        _, ptop = self._page_origin(pno)
        pleft = self._page_lefts[pno] if self._page_lefts else 0.0
        dx_in = (hb0 + vx - pleft) / zs      # 页内偏移（PDF 坐标）
        dy_in = (scene_y - ptop) / zs
        self._zoom_factor = new_z
        # 连续缩放期间按 1.25 档位渲染（复用同一批缓存），停手后补精确清晰版
        self._zooming_fast = True
        # defer_render：此处按旧滚动位置渲染无意义，等下面设好新位置再统一渲染
        self.load_page(keep_scroll=True, defer_render=True)
        # 缩放后：同一页内点重新对准鼠标视口位置
        zs2 = self.zoom
        _, ptop2 = self._page_origin(pno)
        pleft2 = self._page_lefts[pno] if self._page_lefts else 0.0
        self._set_scroll(round(ptop2 + dy_in * zs2 - vy))
        self.view.horizontalScrollBar().setValue(
            round(pleft2 + dx_in * zs2 - vx))
        # _set_scroll 屏蔽了滚动信号（避免重入），但上面的 load_page 是按"旧滚动值
        # + 新几何"渲染并算页号的，与最终位置不符 —— 表现为页码跳变、视口内的页
        # 从未渲染（白屏）。这里按最终滚动位置补做窗口化渲染与页码同步。
        self._render_window()
        self._sync_page_from_scroll()
        self._ensure_page_visible_x()
        # 停手后重渲染清晰版（若期间又缩放，定时器会被重新起，只做最后一次）
        self._zoom_refine_timer.start()

    def _refine_zoom_render(self):
        """缩放/翻页停止后：退出缩放档位，按屏幕原生 1:1 倍率重渲染，画面最清晰。
        渲染签名含实际倍率，倍率变化后 _render_window 会重建受影响的页。"""
        if not self._zooming_fast:
            return
        self._zooming_fast = False
        if not self.doc or not self._page_tops:
            return
        self._render_window()

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
                failed = 0
                for name in os.listdir(old_img_dir):
                    s = os.path.join(old_img_dir, name)
                    d = os.path.join(new_img_dir, name)
                    if os.path.isfile(s) and not os.path.exists(d):
                        try:
                            shutil.copy2(s, d); cnt += 1
                        except Exception:
                            failed += 1
                if cnt:
                    moved.append(f"{cnt} 张图片")
                if failed:
                    QMessageBox.warning(
                        self, "部分图片未迁移",
                        f"有 {failed} 张图片复制失败（源文件可能被占用或磁盘已满），"
                        "旧位置文件仍保留，可手动复制。")
        if moved:
            QMessageBox.information(self, "迁移完成",
                "已把 " + "、".join(moved) + " 复制到新位置。\n"
                "旧位置的文件保留作为备份，可手动删除。")

    # ---------------- 撤销/清除/保存 ----------------
    def commit_text_editor(self):
        """提交正在进行的内联文字编辑（若有）。"""
        ed = getattr(self, "_text_editor", None)
        if ed is not None and ed.scene() is not None and not ed._done:
            ed.commit()
        self._text_editor = None

    def on_text_editor_closed(self):
        self._text_editor = None

