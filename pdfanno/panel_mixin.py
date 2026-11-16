# -*- coding: utf-8 -*-
"""MainWindow panelmixin mixin（拆分第4步）。"""
import os

from PySide6.QtWidgets import (QMessageBox)


class PanelMixin:
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

    def toggle_translate(self):
        """显示/隐藏右侧翻译面板（与搜索面板一致）。
        打开后，用'选择文字'工具选中文字即自动翻译；MD 阅读时选中文字同样可用。"""
        if self._dock_active(self.translate_dock):
            self.translate_dock.hide()
            return
        self.translate_dock.show()
        self.translate_dock.raise_()   # tabify 同区：切到前台，覆盖搜索面板
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

