# -*- coding: utf-8 -*-
"""MainWindow treemixin mixin（拆分第4步）。"""
import os
import shutil
import sys

from PySide6.QtCore import (Qt)
from PySide6.QtWidgets import (QFileDialog, QInputDialog, QMenu, QMessageBox, QTreeWidgetItem)
from .config import (folders_load, folders_save, history_clear, history_load)
from .markdown_panel import (_md_exts_ok)


class TreeMixin:
    def build_recent_menu(self):
        m = self.recent_menu
        m.clear()
        op = m.addAction("📄 打开 PDF…")
        op.triggered.connect(self.open_pdf)
        om = m.addAction("📝 打开 Markdown…")
        om.triggered.connect(self.open_md)
        of = m.addAction("📂 打开文件夹…")
        of.triggered.connect(self.choose_folder)
        m.addSeparator()
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
            clr = m.addAction("清空历史记录")
            clr.triggered.connect(history_clear)
        # 在系统文件管理器中打开所在文件夹
        m.addSeparator()
        loc = m.addAction("在文件夹中显示当前PDF")
        loc.triggered.connect(self.open_in_explorer)

    def open_in_explorer(self):
        if not self.pdf_path:
            return
        try:
            os.startfile(os.path.dirname(self.pdf_path))  # noqa: S606
        except Exception:
            pass

    # ---------------- 左侧文件管理树 ----------------
    def choose_folder(self):
        """选择一个文件夹加入收藏并在树中浏览（可反复使用以添加多个）。"""
        d = QFileDialog.getExistingDirectory(self, "选择要管理的文件夹",
                                             self._last_folder_start())
        if not d:
            return
        lst = folders_load()
        if d not in lst:
            lst.append(d)
            folders_save(lst)
        self.refresh_tree()
        self._show_left_panel(0, self.btn_folders)
        self.statusBar().showMessage(f"已添加文件夹: {d}")

    def replace_folder(self):
        """把当前收藏的文件夹整体替换为新选择的一个（不删磁盘文件）。"""
        d = QFileDialog.getExistingDirectory(self, "选择要浏览的文件夹",
                                             self._last_folder_start())
        if not d:
            return
        folders_save([d])
        self.refresh_tree()
        self._show_left_panel(0, self.btn_folders)
        self.statusBar().showMessage(f"已切换到文件夹: {d}")

    def remove_all_folders(self):
        """清空收藏的文件夹列表（仅移除收藏，不动磁盘文件）。"""
        if not folders_load():
            return
        r = QMessageBox.question(
            self, "移除全部收藏",
            "确定移除左侧所有收藏的文件夹吗？\n（只移除收藏，不会删除磁盘上的文件）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        folders_save([])
        self.refresh_tree()
        self.statusBar().showMessage("已移除全部收藏文件夹")

    def _last_folder_start(self):
        """选择框的起始目录：优先上次收藏的第一个，否则用户主目录。"""
        lst = folders_load()
        for p in lst:
            if os.path.isdir(p):
                return p
        return os.path.expanduser("~")

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
        m = self._build_tree_menu(self.tree.itemAt(pos))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _build_tree_menu(self, item):
        """构造左侧树的右键菜单（item 为 None 表示点在空白处）。"""
        if item is None:
            # 空白处右键 = 文件夹管理入口
            m = QMenu(self)
            m.addAction("📂 打开文件夹…", self.choose_folder)
            m.addAction("替换为其他文件夹…", self.replace_folder)
            m.addSeparator()
            m.addAction("移除全部收藏文件夹", self.remove_all_folders)
            m.addSeparator()
            m.addAction("刷新全部", self.refresh_tree)
            return m
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
            m.addAction("打开文件夹…", self.choose_folder)
            m.addAction("替换为其他文件夹…", self.replace_folder)
            m.addSeparator()
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
        return m

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

