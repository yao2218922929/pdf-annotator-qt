# -*- coding: utf-8 -*-
"""MainWindow annolistmixin mixin（拆分第4步）。"""
import os
import copy
import pymupdf

from PySide6.QtCore import (Qt)
from PySide6.QtGui import (QColor, QImage)
from PySide6.QtWidgets import (QApplication, QInputDialog, QListWidgetItem, QMenu, QMessageBox, QTreeWidgetItem)


class AnnoListMixin:
    def _anno_kind_label(self, a):
        """批注类型 -> 列表显示用标签与图标。"""
        t = a.get("type")
        return {"rect": "▭ 矩形", "oval": "◯ 椭圆", "line": "╱ 直线",
                "arrow": "➚ 箭头", "pen": "✎ 画笔", "highlight": "🖍 荧光笔",
                "texthl": "🖍 文字高亮", "text": "T 文字"}.get(t, "• 批注")

    def refresh_anno_list(self):
        """重建批注列表：汇总当前文档所有页的批注，按页码排序。"""
        self.anno_list.clear()
        if not self.doc:
            self.anno_list_hint.setText("  批注列表（未打开文档）")
            return
        total = sum(len(v) for v in self.annos.values() if v)
        self.anno_list_hint.setText(f"  批注列表（{total} 条）")
        for pno in sorted(k for k, v in self.annos.items() if v):
            for i, a in enumerate(self.annos[pno]):
                if not a:
                    continue
                kind = self._anno_kind_label(a)
                # 文字类批注（文字/文字高亮）附上具体内容预览
                if a.get("type") in ("text", "texthl"):
                    preview = (a.get("text") or "").replace("\n", " ").strip()[:24]
                else:
                    preview = ""
                label = f"第 {pno + 1} 页 · {kind}"
                if preview:
                    label += f"　{preview}"
                it = QListWidgetItem(label)
                it.setData(Qt.UserRole, pno)
                it.setData(Qt.UserRole + 1, i)
                try:
                    it.setForeground(QColor(a.get("color", "#333")))
                except Exception:
                    pass
                if pno == self.page_no:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.anno_list.addItem(it)
        if self.anno_list.count() == 0:
            it = QListWidgetItem("（当前文档还没有批注）")
            # QListWidgetItem 没有 setDisabled，需去掉可交互标志来达到禁用效果
            it.setFlags(it.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
            it.setData(Qt.UserRole, None)
            self.anno_list.addItem(it)

    def _on_anno_list_click(self, item):
        """点击批注列表项：跳转到对应页。"""
        if item is None:
            return
        pno = item.data(Qt.UserRole)
        if pno is None or not self.doc:
            return
        if 0 <= pno < self.doc.page_count and pno != self.page_no:
            self.page_no = pno
            self.load_page()
            self.statusBar().showMessage(f"已定位到第 {pno + 1} 页的批注")

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
        # 页号整体前移，历史栈中的页号失效：清空撤销/重做
        self._undo_stack = []
        self._redo_stack = []
        self._sync_history_actions()
        self._words_cache.clear()
        self._clear_doc_geometry()
        self._page_pix_cache.clear()   # 页内容已变，缓存失效

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

