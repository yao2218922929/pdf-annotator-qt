# -*- coding: utf-8 -*-
"""MainWindow sessionmixin mixin（拆分第4步）。"""
import json
import os
from collections import OrderedDict
import pymupdf

from PySide6.QtCore import (QSize, QTimer, Qt)
from PySide6.QtWidgets import (QFileDialog, QMessageBox, QTabBar, QToolButton)
from .config import (history_add, history_remove, progress_get)
from .pdfcore import (load_embedded_annos, open_pdf_doc, save_annotations)
from .ui_utils import (make_tab_close_icon)


class SessionMixin:
    def open_pdf(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择PDF文件", "", "PDF 文件 (*.pdf)")
        for f in files:
            self.load_pdf_path(f)

    def open_md(self):
        """弹框选择一个 Markdown 文件并在阅读面板中打开。"""
        f, _ = QFileDialog.getOpenFileName(
            self, "选择 Markdown 文件", self._last_folder_start(),
            "Markdown 文件 (*.md *.markdown *.txt);;所有文件 (*)")
        if not f:
            return
        self.load_md_path(f)

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
        self._words_cache = OrderedDict()   # 页号 -> 词表（按访问顺序有限保留）
        self._clear_doc_geometry()
        self._text_editor = None
        self.drawing = False
        self.free_pts = []
        self.preview_items = []
        self.clear_hit_marks()
        self._page_pix_cache.clear()   # 换文档，渲染缓存失效
        self._rendering = True         # 屏蔽清场景期间的滚动信号重入
        try:
            self.scene.clear()
        finally:
            self._rendering = False
        # scene.clear() 已销毁全部 C++ 图元，引用必须置空，
        # 否则后续 clear_selection 会操作已释放对象而抛错
        self.sel_items = []
        self.preview_items = []

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
            newdoc, pw = open_pdf_doc(f, self)
        except Exception as e:
            QMessageBox.critical(self, "出错", f"打开失败: {e}")
            history_remove(f)
            return
        if newdoc is None:
            history_remove(f)   # 用户取消或因密码失败：不留历史
            return
        # 先把当前文档状态快照回其会话
        self._store_current_session()
        self.doc = newdoc
        self.pdf_path = f
        self.pdf_password = pw   # 加密文档的打开密码，保存批注时复用
        self.annos = load_embedded_annos(newdoc)   # 恢复内嵌的可编辑批注数据
        prog = progress_get(f)   # 上次阅读进度：页号 + 缩放
        self.page_no = min(max(0, prog[0]), self.doc.page_count - 1) if prog else 0
        self._zoom_factor = prog[1] if prog else 1.0
        self._restored_progress = bool(prog)
        self._undo_stack = []    # 新文档：清空撤销/重做
        self._redo_stack = []
        self._sync_history_actions()
        self._words_cache = OrderedDict()   # 换文档：清空词表缓存
        self._page_pix_cache.clear()   # 换文档：清空页面渲染缓存
        self.toc_items = [list(t) for t in self.doc.get_toc(simple=True)]
        self.toc_dirty = False
        self._toc_clip = None
        self._hier_index = None   # 换文档：层级索引作废（惰性重建）
        self._alt_ready = False   # 换文档：Altium 链接缓存作废（惰性重建）
        self.deleted_pages = set()                       # 换文档：重置删页记录
        self.page_map = list(range(self.doc.page_count))  # 当前页索引 -> 原始页索引
        self.sel_text = ""
        self.sel_hits = []
        self._clear_doc_geometry()
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
        # 有历史阅读进度时保留上次缩放，不自动适应窗口
        def _delayed_fit(target=f):
            if self.pdf_path == target and self.doc is not None \
                    and not self._restored_progress:
                self.fit_to_width()
        QTimer.singleShot(50, _delayed_fit)
        history_add(f)
        if prog:
            self.statusBar().showMessage(
                f"已打开: {os.path.basename(f)}（共 {self.doc.page_count} 页，"
                f"已回到第 {self.page_no + 1} 页）")
        else:
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
        # 缩放百分比标签跟随当前视图：PDF 与 MD 各有一套缩放，
        # 不切换会让切回后仍显示另一模式的旧值
        lbl = getattr(self, "zoom_label", None)
        if lbl is not None:
            if is_md:
                lbl.setText(f"  {int(self.md_panel._zoom * 100)}%  ")
            elif self.doc is not None:
                lbl.setText(f"  {int(self.user_zoom * 100)}%  ")
        # 上面把所有 PDF 专属动作统一置为可用，撤销/重做要按历史栈重新判定
        if not is_md and hasattr(self, "_sync_history_actions"):
            self._sync_history_actions()

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
        self.pdf_password = ""
        self.page_no = 0
        self.annos = {}
        self._undo_stack = []
        self._redo_stack = []
        self._sync_history_actions()
        self.toc_items = []
        self.toc_dirty = False
        self._toc_clip = None
        self.deleted_pages = set()
        self.page_map = []
        self._saved_sig = None
        self._zoom_factor = 1.0
        self.sel_text = ""
        self.sel_hits = []
        self._words_cache = OrderedDict()   # 页号 -> 词表（按访问顺序有限保留）
        self._clear_doc_geometry()
        self._text_editor = None
        self.search_kw = ""
        self.search_results = []
        self.search_idx = -1
        self.clear_hit_marks()
        self._page_pix_cache.clear()   # 无文档，清空渲染缓存
        self._rendering = True         # 屏蔽清场景期间的滚动信号重入
        try:
            self.scene.clear()
        finally:
            self._rendering = False
        # scene.clear() 已销毁全部 C++ 图元，引用必须置空，
        # 否则后续 clear_selection 会操作已释放对象而抛错
        self.sel_items = []
        self.preview_items = []
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
        """另存为新的批注PDF。返回 True=已写入文件，False=用户取消或失败。"""
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
            # 批注以原生注释写入且保存前先清除旧注释，故不会叠加
            save_annotations(self.pdf_path, self.annos, out,
                             toc=self.toc_items if self.toc_dirty else None,
                             deleted_pages=self.deleted_pages or None,
                             password=self.pdf_password or None)
            self._saved_sig = self._state_sig()   # 记录已保存状态，供退出提示比对
            self.statusBar().showMessage(f"已保存: {out}")
            QMessageBox.information(self, "完成", f"批注已保存:\n{out}")
            return True
        except Exception as e:
            QMessageBox.critical(self, "出错", f"保存失败: {e}")
            return False

    def save_pdf_inplace(self):
        """保存到原文件（覆盖）。返回 True=已写入，False=取消或失败。
        做法：写入同目录临时文件 → 关闭当前文档 → 原子替换 → 重开该文件。
        （doc 打开时无法替换其源文件，故必须先关闭再替换。）"""
        if not self.doc:
            return False
        if not self.has_unsaved():
            QMessageBox.information(self, "提示", "还没有任何批注或书签改动")
            return False
        path = self.pdf_path
        name = os.path.basename(path)
        if not os.access(path, os.W_OK):
            QMessageBox.critical(self, "无法保存", f"《{name}》不可写（只读或被占用）。")
            return False
        btn = QMessageBox.question(
            self, "保存到原文件",
            f"将用当前批注覆盖原文件：\n{path}\n\n原文件内容会被替换，确定继续吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if btn != QMessageBox.Yes:
            return False
        d = os.path.dirname(path)
        tmp = os.path.join(d, f".~{name}.saving.pdf")
        try:
            save_annotations(path, self.annos, tmp,
                             toc=self.toc_items if self.toc_dirty else None,
                             deleted_pages=self.deleted_pages or None,
                             password=self.pdf_password or None)
        except Exception as e:
            QMessageBox.critical(self, "出错", f"保存失败: {e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            return False
        # 关闭当前文档 → 替换 → 重开（保留页号/缩放/批注/书签状态）
        saved_page, saved_zoom = self.page_no, self._zoom_factor
        try:
            if self.doc is not None:
                self.doc.close()
            self.doc = None
            os.replace(tmp, path)
        except Exception as e:
            QMessageBox.critical(self, "出错", f"覆盖原文件失败: {e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            # 尽力恢复：重新打开原文件，避免窗口空置
            try:
                newdoc, pw = open_pdf_doc(path, self)
                if newdoc is not None:
                    self.doc = newdoc
                    self.pdf_password = pw
                    self.load_page()
            except Exception:
                pass
            return False
        # 重开被覆盖的文件：保留原页号与缩放
        newdoc, pw = open_pdf_doc(path, self)
        if newdoc is None:
            QMessageBox.warning(self, "提示",
                                "已保存，但重新打开失败，请手动重新打开该文件。")
            self.statusBar().showMessage(f"已保存: {path}")
            return True
        self.doc = newdoc
        self.pdf_password = pw
        self.page_no = min(max(0, saved_page), newdoc.page_count - 1)
        self._zoom_factor = saved_zoom
        self._hier_index = None
        self._alt_ready = False
        self._page_pix_cache.clear()
        self._words_cache = OrderedDict()
        self._clear_doc_geometry()
        self.toc_items = [list(t) for t in self.doc.get_toc(simple=True)]
        self.toc_dirty = False
        self.page_map = list(range(self.doc.page_count))
        self.page_spin.setRange(1, self.doc.page_count)
        self.clear_selection()
        self.load_page()
        self.refresh_toc()
        self._saved_sig = self._state_sig()   # 已落盘
        self.sessions[path] = self._capture_session()   # 同步会话快照
        self.statusBar().showMessage(f"已保存到原文件: {path}")
        return True

    def closeEvent(self, e):
        """退出前逐个处理所有打开 PDF 的未保存改动；再收尾线程/文档。"""
        # 0) 保存当前笔记 + 当前阅读进度
        self.note_panel.save_current()
        self._progress_timer.stop()
        self._save_progress()
        # 0.5) OCR 后台任务：退出会丢弃未写入的识别结果，需用户确认
        ocr_thr = getattr(self, "_ocr_thread", None)
        if ocr_thr is not None and ocr_thr.isRunning():
            btn = QMessageBox.question(
                self, "OCR 进行中",
                "OCR 识别尚未完成，退出将丢弃未写入的识别结果。仍要退出吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if btn != QMessageBox.Yes:
                e.ignore()
                return
            try:
                ocr_thr.stop()
                ocr_thr.wait(8000)   # 等当前页识别结束（约数秒）
            except Exception:
                pass
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

