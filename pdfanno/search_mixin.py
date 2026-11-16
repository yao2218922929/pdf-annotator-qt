# -*- coding: utf-8 -*-
"""MainWindow searchmixin mixin（拆分第4步）。"""
import os

from PySide6.QtCore import (QPointF, QRectF, Qt)
from PySide6.QtGui import (QColor, QPen)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QGraphicsRectItem, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)
from .config import (folders_load)
from .search import (SearchThread)


class SearchMixin:
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

    def toggle_anno_list(self):
        """切换左侧批注列表面板；打开时刷新列表。"""
        if self.left_stack.isVisible() and self.left_stack.currentIndex() == 3:
            self._hide_left_panel(self.btn_anno_list)
        else:
            self._show_left_panel(3, self.btn_anno_list)
            self.refresh_anno_list()

    def toggle_search(self):
        # Markdown 模式：Ctrl+F 直接在 MD 面板内查找（PDF 搜索面板不适用）
        if self._view_mode == "md" and self.md_path:
            self._md_find_prompt()
            return
        if self._dock_active(self.search_dock):
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

    @staticmethod
    def _dock_active(dock):
        """dock 是否真正显示在前台。
        翻译与搜索被 tabifyDockWidget 放在同一区域时，两者的 isVisible() 会
        同时为 True，只有前台那个的 visibleRegion() 非空，故用后者判断。"""
        if not dock.isVisible():
            return False
        return not dock.visibleRegion().isEmpty()

    def on_escape(self):
        """Esc：先退出内联文字编辑；搜索框打开时关闭搜索并清除高亮，否则取消文字选区。"""
        ed = getattr(self, "_text_editor", None)
        if ed is not None and ed.scene() is not None and not ed._done:
            ed.finish(cancel=True)
            self._text_editor = None
            return
        if self._dock_active(self.search_dock):
            self.close_search()
        elif self._dock_active(self.translate_dock):
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
            self._set_scroll(self._page_tops[self.page_no] if
                             self.page_no < len(self._page_tops) else 0)
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
        # 命中所在页（跨文件跳转后以 rec 页为准，这里用当前页）
        ox, oy = self._page_origin(self.page_no)
        cx = ox + (rect_pdf[0] + rect_pdf[2]) / 2 * z
        cy = oy + (rect_pdf[1] + rect_pdf[3]) / 2 * z
        # 让命中点居中
        self.view.centerOn(QPointF(cx, cy))
        # 高亮命中矩形（橙色描边+深黄底，区别于其他命中的黄色）
        self.clear_hit_marks()
        r = QRectF(ox + rect_pdf[0] * z, oy + rect_pdf[1] * z,
                   (rect_pdf[2] - rect_pdf[0]) * z,
                   (rect_pdf[3] - rect_pdf[1]) * z)
        fill = QGraphicsRectItem(r)
        fill.setBrush(QColor(255, 152, 0, 130))   # 橙色半透明
        fill.setPen(QPen(QColor(230, 81, 0), 2))
        fill.setZValue(21)
        self.scene.addItem(fill)
        self.search_hit_items = [fill]

