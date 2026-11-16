# -*- coding: utf-8 -*-
"""笔记面板：标题标尺、富文本编辑器、浮动格式条、笔记列表/存储。
由原单文件抽出，主文件通过门面重新导出。"""
import json
import os
import re as _re
import shutil
import time

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRect, QTimer, QUrl, Qt
from PySide6.QtGui import (QBrush, QColor, QDrag, QFont, QImage, QImageReader,
                           QPainter, QPixmap, QTextBlockFormat, QTextCharFormat,
                           QTextCursor, QTextDocument, QTextImageFormat,
                           QTextListFormat)
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox,
                               QFileDialog, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox, QPushButton, QTextBrowser,
                               QToolButton, QVBoxLayout, QWidget)

from .config import (_SAVE_ERRORS, flush_save_errors, notes_cfg_load,
                     notes_file_path, notes_img_dir_path)


class HeadingGutter(QWidget):
    """编辑区左侧的标题标尺：为每个标题行画 H1/H2… 徒标与折叠三角，
    点击三角或徒标即可收起/展开该节。"""
    WIDTH = 48

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setMouseTracking(True)
        self.setCursor(Qt.ArrowCursor)
        self._hover = None     # 悬停的行号，用于突出显示
        editor.document().contentsChanged.connect(self.update)
        editor.document().documentLayout().documentSizeChanged.connect(
            lambda *_: self.update())
        editor.verticalScrollBar().valueChanged.connect(self.update)

    # ---- 定位 ----
    def _gutter_rect(self):
        """标尺在编辑器窗口中的位置：左侧竖条，与视口同高。"""
        vp = self.editor.viewport()
        top = vp.mapTo(self.editor, vp.rect().topLeft()).y()
        return QRect(0, top, self.WIDTH, vp.height())

    def _rows(self):
        """返回可见标题行的 [(block, y_in_gutter, height), ...]（文档坐标→标尺坐标）。"""
        ed = self.editor
        vp = ed.viewport()
        top = vp.mapTo(ed, vp.rect().topLeft()).y()
        off = ed.verticalScrollBar().value()
        out = []
        b = ed.document().firstBlock()
        lay = ed.document().documentLayout()
        while b.isValid():
            lvl = b.blockFormat().headingLevel()
            # 空段落（如清空后的残留格式）不画徒标，避免"没文字标题还在"
            if lvl >= 1 and b.isVisible() and b.text().strip():
                r = lay.blockBoundingRect(b)
                y = top + r.y() - off
                if -r.height() <= y - top <= vp.height():
                    out.append((b, int(y), int(r.height()), lvl))
            b = b.next()
        return out

    def _hit(self, pt):
        """pt（标尺坐标）处命中的标题块，否则 None。"""
        for blk, y, h, lvl in self._rows():
            if y <= pt.y() <= y + h:
                return blk, QRect(0, y, self.WIDTH, h)
        return None, None

    # ---- 绘制 ----
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        for blk, y, h, lvl in self._rows():
            folded = self.editor.is_block_folded(blk)
            hover = (self._hover == blk.position())
            r = QRect(2, y, self.WIDTH - 4, h)
            col = QColor("#1e88e5")
            # 徒标背景 + H 级别文字
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#e8f1fd") if hover else QColor("#f2f7fe"))
            p.drawRoundedRect(QRect(r.x(), r.y() + 2, 22, r.height() - 4), 4, 4)
            f = QFont()
            f.setBold(True)
            f.setPointSize(9)
            p.setFont(f)
            p.setPen(col)
            p.drawText(QRect(r.x(), r.y(), 22, r.height()),
                       Qt.AlignCenter, f"H{lvl}")
            # 折叠三角（▾ 展开态 / ▸ 折叠态），画在徒标右侧
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            cx = r.x() + 31
            cy = r.y() + r.height() // 2
            if folded:   # 折叠：朝右
                p.drawPolygon([QPoint(cx - 4, cy - 5), QPoint(cx - 4, cy + 5),
                               QPoint(cx + 5, cy)])
            else:        # 展开：朝下
                p.drawPolygon([QPoint(cx - 5, cy - 3), QPoint(cx + 5, cy - 3),
                               QPoint(cx, cy + 5)])

    # ---- 交互 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            blk, _ = self._hit(e.position().toPoint())
            if blk is not None:
                self.editor.toggle_fold_block(blk)
                self._hover = blk.position()
                self.update()
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        blk, _ = self._hit(e.position().toPoint())
        pos = blk.position() if blk is not None else None
        if pos != self._hover:
            self._hover = pos
            self.update()
            # 标尺区域是编辑器左留白，鼠标不一定进入编辑器，手动提示
            if blk is not None:
                act = "展开" if self.editor.is_block_folded(blk) else "收起"
                self.setToolTip(f"{act}（点击 H{blk.blockFormat().headingLevel()} "
                                f"标尺）")
            else:
                self.setToolTip("")
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._hover = None
        self.update()
        super().leaveEvent(e)

    def wheelEvent(self, e):
        """标尺是编辑器左侧延伸：滚轮直接作用到编辑器，保证滚动体验一致。"""
        e.ignore()
        self.editor.wheelEvent(e)


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
        # 折叠状态：记录哪些标题被折叠，随笔记一起保存
        self._folded_keys = set()
        # 左侧标题标尺（H1/H2 徒标 + 折叠三角）
        self.gutter = HeadingGutter(self)
        self.setViewportMargins(HeadingGutter.WIDTH, 0, 0, 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.gutter.setGeometry(self.gutter._gutter_rect())

    def showEvent(self, e):
        super().showEvent(e)
        self.gutter.setGeometry(self.gutter._gutter_rect())
        self.gutter.raise_()

    # ---- 标题折叠 ----
    def heading_at(self, pt):
        """视图坐标 pt 处若在**有文字**的标题段落上，返回该 QTextBlock，否则 None。"""
        blk = self.cursorForPosition(pt).block()
        if blk.blockFormat().headingLevel() >= 1 and blk.text().strip():
            return blk
        return None

    def toggle_fold_at(self, pt):
        """双击标题：折叠/展开该标题下辖内容。返回是否有处理。"""
        blk = self.heading_at(pt)
        if blk is None:
            return False
        self.toggle_fold_block(blk)
        return True

    def toggle_fold_block(self, blk):
        key = self._fold_key(blk)
        if key in self._folded_keys:
            self._folded_keys.discard(key)
            self._set_section_visible(blk, True)
        else:
            self._folded_keys.add(key)
            self._set_section_visible(blk, False)
        self.gutter.update()
        self._notify_fold_changed()

    def is_block_folded(self, blk):
        """该标题块当前是否处于折叠状态。"""
        return self._fold_key(blk) in self._folded_keys

    def _fold_key(self, blk):
        """折叠状态键：标题级别 + 标题文本 + 出现序号（同文本多标题也能各自独立）。"""
        lvl = blk.blockFormat().headingLevel()
        text = blk.text().strip()
        n = 0
        b = self.document().firstBlock()
        while b.isValid() and b.position() < blk.position():
            if (b.blockFormat().headingLevel() == lvl
                    and b.text().strip() == text):
                n += 1
            b = b.next()
        return f"{lvl}|{text}|{n}"

    def _section_blocks(self, blk):
        """返回该标题下辖的内容块（到下一个同级或更高级标题之前，不含标题自身）。"""
        lvl = blk.blockFormat().headingLevel()
        out = []
        b = blk.next()
        while b.isValid():
            bl = b.blockFormat().headingLevel()
            if bl >= 1 and bl <= lvl:
                break
            out.append(b)
            b = b.next()
        return out

    def _set_section_visible(self, blk, visible):
        for b in self._section_blocks(blk):
            b.setVisible(visible)
        # 让布局重新计算（否则隐藏块仍占位）
        self.document().markContentsDirty(0, self.document().characterCount())

    def _iter_blocks(self):
        b = self.document().firstBlock()
        while b.isValid():
            yield b
            b = b.next()

    def _notify_fold_changed(self):
        if self.note_panel is not None:
            self.note_panel._on_fold_changed()

    def folded_keys(self):
        return sorted(self._folded_keys)

    def apply_folded_keys(self, keys):
        """笔记加载后恢复折叠状态；顺带丢弃已不存在标题的过期记录。"""
        want = set(keys or [])
        self._folded_keys = set()
        for b in self._iter_blocks():
            b.setVisible(True)
        for blk in self._iter_blocks():
            if blk.blockFormat().headingLevel() < 1:
                continue
            k = self._fold_key(blk)
            if k in want:
                self._folded_keys.add(k)
                for b in self._section_blocks(blk):
                    b.setVisible(False)
        self.document().markContentsDirty(
            0, self.document().characterCount())
        self.gutter.update()

    def mouseDoubleClickEvent(self, e):
        """双击标题行 → 折叠/展开该节内容。"""
        if e.button() == Qt.LeftButton and self.toggle_fold_at(
                e.position().toPoint()):
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

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
        # 焦点离开编辑器时收起格式条；但点到格式条自身/其下拉弹层不算。
        # 延迟到下一轮事件循环再判断：此时新焦点与弹层状态才已就绪。
        super().focusOutEvent(e)
        QTimer.singleShot(0, self._hide_bar_if_focus_left)

    def _hide_bar_if_focus_left(self):
        """焦点确实离开编辑器与格式条（含下拉弹层）时，才收起格式条。"""
        if not self.fmt_bar.isVisible():
            return
        w = QApplication.focusWidget()
        if w is not None and (w is self.fmt_bar
                              or self.fmt_bar.isAncestorOf(w)):
            return
        pop = QApplication.activePopupWidget()
        if pop is not None and (pop is self.fmt_bar
                                or self.fmt_bar.isAncestorOf(pop)):
            return
        # 字号/标题下拉的弹层是独立顶层窗口，需单独判断其可见性
        if self.fmt_bar.popup_open():
            return
        self.fmt_bar.hide_bar()

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

    # 段落标题级别：下拉显示名 → （headingLevel, 字号pt）
    HEADINGS = [("正文", 0), ("标题 1", 1), ("标题 2", 2),
                ("标题 3", 3), ("标题 4", 4)]
    HEADING_PT = {0: 14.0, 1: 26.0, 2: 22.0, 3: 18.0, 4: 16.0}

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
        # 标题级别下拉：正文 / 标题1~4
        self.btn_heading = QComboBox()
        self.btn_heading.setFocusPolicy(Qt.NoFocus)
        self.btn_heading.setToolTip("段落标题级别（作用于光标所在段落）")
        self.btn_heading.setMinimumWidth(66)
        for name, lvl in self.HEADINGS:
            self.btn_heading.addItem(name, lvl)
        self.btn_heading.activated.connect(self._on_heading)
        lay.addWidget(self.btn_heading)

        self._sep(lay)
        self.btn_bold = mk("B", "加粗", lambda: self._toggle("bold"))
        self.btn_bold.setStyleSheet("font-weight:bold;")
        self.btn_italic = mk("I", "斜体", lambda: self._toggle("italic"))
        self.btn_italic.setStyleSheet("font-style:italic;")
        self.btn_under = mk("U", "下划线", lambda: self._toggle("underline"))
        self.btn_under.setStyleSheet("text-decoration:underline;")
        self.btn_strike = mk("S", "删除线", lambda: self._toggle("strike"))
        self.btn_strike.setStyleSheet("text-decoration:line-through;")
        self.btn_sup = mk("x²", "上标", lambda: self._toggle("superscript"))
        self.btn_sub = mk("x₂", "下标", lambda: self._toggle("subscript"))
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
        self._sync_state()
        self.raise_()
        self.show()

    def hide_bar(self, *args):
        self.hide()

    def popup_open(self):
        """任一字号/标题下拉的弹层当前是否已展开。"""
        for cb in (self.btn_size, self.btn_heading):
            view = cb.view()
            if view is not None:
                w = view.window()
                if w is not None and w.isVisible():
                    return True
        return False

    # ---- 应用格式 ----
    def _sync_state(self):
        """把字号/标题下拉同步为选区当前状态。"""
        cur = self.editor.textCursor()
        size = cur.charFormat().fontPointSize()
        if size <= 0:
            size = self.editor.fontPointSize() or self.editor.font().pointSize()
        idx = self.btn_size.findData(int(size)) if size > 0 else -1
        if idx >= 0:
            self.btn_size.blockSignals(True)
            self.btn_size.setCurrentIndex(idx)
            self.btn_size.blockSignals(False)
        lvl = cur.blockFormat().headingLevel()
        hidx = self.btn_heading.findData(lvl)
        if hidx >= 0:
            self.btn_heading.blockSignals(True)
            self.btn_heading.setCurrentIndex(hidx)
            self.btn_heading.blockSignals(False)

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
        elif prop == "superscript":
            f.setVerticalAlignment(
                QTextCharFormat.VerticalAlignment.AlignNormal
                if src.verticalAlignment()
                == QTextCharFormat.VerticalAlignment.AlignSuperScript
                else QTextCharFormat.VerticalAlignment.AlignSuperScript)
        elif prop == "subscript":
            f.setVerticalAlignment(
                QTextCharFormat.VerticalAlignment.AlignNormal
                if src.verticalAlignment()
                == QTextCharFormat.VerticalAlignment.AlignSubScript
                else QTextCharFormat.VerticalAlignment.AlignSubScript)
        self._apply(lambda c: c.mergeCharFormat(f))

    def _on_size(self):
        size = self.btn_size.currentData()
        if not size:
            return
        f = QTextCharFormat()
        f.setFontPointSize(float(size))
        self._apply(lambda c: c.mergeCharFormat(f))

    def _on_heading(self):
        """设置选区所在段落的标题级别（标题 1~4），并套用对应字号/加粗。
        标题是段落级格式，作用于选区覆盖的所有完整段落。"""
        lvl = self.btn_heading.currentData()
        if lvl is None:
            return
        self._apply(lambda c: self._apply_heading(c, lvl))

    def _apply_heading(self, cur, lvl):
        """对 cur 选区覆盖的每个段落应用 headingLevel 与配套字号/加粗。"""
        doc = self.editor.document()
        start = doc.findBlock(cur.selectionStart()).position()
        end_blk = doc.findBlock(cur.selectionEnd())
        end = end_blk.position() + end_blk.length() - 1
        sub = QTextCursor(doc)
        sub.setPosition(start)
        sub.setPosition(end, QTextCursor.KeepAnchor)
        pt = self.HEADING_PT.get(lvl, 14.0)
        bf = QTextBlockFormat()
        bf.setHeadingLevel(int(lvl))
        # 标题按层级套字号与加粗；正文恢复常规
        cf = QTextCharFormat()
        cf.setFontPointSize(pt)
        cf.setFontWeight(QFont.Bold if lvl >= 1 else QFont.Normal)
        sub.mergeBlockFormat(bf)
        sub.mergeCharFormat(cf)
        # 让原选区仍覆盖这些段落
        cur.setPosition(start)
        cur.setPosition(end, QTextCursor.KeepAnchor)

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
        """清除字符格式，并把段落恢复为正文。"""
        def _do(c):
            c.setCharFormat(QTextCharFormat())
            self._apply_heading(c, 0)
        self._apply(_do)


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
        self.btn_delete.setToolTip("删除笔记（列表可 Ctrl/Shift 多选后批量删除）")
        self.btn_delete.setFixedSize(30, 26)
        self.btn_delete.clicked.connect(self.delete_selected_or_current)
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
        # 多选：Ctrl/Shift/框选后可一次批量删除（Delete 键或右键/🗑按钮）
        self.note_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.note_list.setStyleSheet(
            "QListWidget{background:#f5f5f5; font-size:12px;}"
            "QListWidget::item{padding:4px 6px;}"
            "QListWidget::item:selected{background:#1e88e5; color:white;}")
        self.note_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.note_list.itemClicked.connect(self._on_list_clicked)
        self.note_list.customContextMenuRequested.connect(
            self._on_list_context_menu)
        self.note_list.installEventFilter(self)   # Delete 键批量删除
        llay.addWidget(self.note_list, 1)

        # ============ 右侧：标题栏 + 格式栏 + 编辑器 ============
        self.editor_ui = QWidget()
        self.editor_ui.setMinimumWidth(300)
        ebl = QVBoxLayout(self.editor_ui)
        ebl.setContentsMargins(0, 0, 0, 0)
        ebl.setSpacing(0)

        # 笔记标题：与左侧列表中的笔记名同步
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("请输入标题")
        self.title_edit.setToolTip("笔记标题（会自动同步为左侧列表中的笔记名）")
        self.title_edit.setStyleSheet(
            "QLineEdit { border:none; border-bottom:1px solid #e0e0e0;"
            " background:transparent; font-size:20px; font-weight:bold;"
            " color:#212121; padding:8px 10px 6px 10px; }"
            "QLineEdit:focus { border-bottom:1px solid #1e88e5; }")
        self.title_edit.textEdited.connect(self._on_title_edited)
        ebl.addWidget(self.title_edit)

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

        # ---- 标题编辑防抖：避免每敲一个字就读写 JSON + 重建列表 ----
        self._title_timer = QTimer(self)
        self._title_timer.setSingleShot(True)
        self._title_timer.timeout.connect(self._commit_title)
        self._title_pending = None

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
        """原子写入+重试：避免文件被杀毒软件等短暂锁定时静默丢失数据。
        成功返回 True；5 次均失败返回 False 并记录错误（由调用方统一提示）。"""
        path = notes_file_path()
        tmp = path + ".tmp"
        last_err = None
        for attempt in range(5):
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, path)
                return True
            except Exception as e:
                last_err = e
                if attempt == 4:
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
                else:
                    time.sleep(0.05 * (attempt + 1))
        _SAVE_ERRORS.append((path, str(last_err)))
        return False

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
        # 右键的条目若已处于多选中，则保留整个选区；否则只操作该条
        if item is not None and not item.isSelected():
            self.note_list.setCurrentItem(item)
        sel = self.note_list.selectedItems()
        n = len(sel)
        m = QMenu(self)
        m.addAction("新建笔记", self.new_note)
        if item:
            nid = item.data(Qt.UserRole)
            if n <= 1:
                m.addAction("重命名", lambda: self.rename_note(nid))
            del_label = f"删除 {n} 篇笔记" if n > 1 else "删除"
            m.addAction(del_label, self.delete_selected_or_current)
            m.addSeparator()
            # 导出笔记为独立文件（HTML 保留图片/格式，Markdown 仅文字）
            if n <= 1:
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
        self.title_edit.setFocus()   # 新建后光标落在标题，直接输入标题
        self.win.statusBar().showMessage(f"已新建笔记: {title}", 3000)

    def delete_note(self, nid=None):
        """删除指定笔记（未指定则删当前）。"""
        if nid is None:
            nid = self._current_note_id()
        if not nid:
            return
        self._delete_ids({nid})

    def delete_current(self):
        self.delete_note()

    def delete_selected_or_current(self):
        """删除列表中选中的笔记（支持 Ctrl/Shift 多选批量删）；无选中时删当前。"""
        ids = {it.data(Qt.UserRole) for it in self.note_list.selectedItems()}
        ids.discard(None)
        if not ids:
            nid = self._current_note_id()
            if nid:
                ids = {nid}
        if not ids:
            return
        self._delete_ids(ids)

    def _delete_ids(self, ids):
        """批量删除笔记：一次确认、一次落盘。"""
        # 有待保存的编辑内容先落盘，避免保存回写到将被删除/已切换的笔记
        self.save_current()
        data = self._all_notes()
        ids = {i for i in ids if i in data}
        if not ids:
            return
        titles = [data[i].get("title", "?") for i in ids]
        if len(ids) == 1:
            text = f"确定删除笔记「{titles[0]}」吗？"
        else:
            preview = "、".join(titles[:5])
            if len(titles) > 5:
                preview += f" …等 {len(titles)} 篇"
            text = f"确定删除以下 {len(ids)} 篇笔记吗？\n\n{preview}"
        btn = QMessageBox.question(
            self, "删除笔记", text,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if btn != QMessageBox.Yes:
            return
        for i in ids:
            data.pop(i, None)
        self._save_notes(data)
        if self._current_id in ids:
            self._current_id = None
            self._loading = True
            self.editor.clear()
            self._loading = False
        self.refresh_list()
        # 自动选中第一篇
        if self.note_list.count() > 0:
            self.note_list.setCurrentRow(0)
            self._on_list_clicked(self.note_list.currentItem())
        n = len(ids)
        name = titles[0] if n == 1 else f"{n} 篇笔记"
        self.win.statusBar().showMessage(f"已删除 {name}", 3000)

    def eventFilter(self, obj, e):
        # 笔记列表按 Delete/Backspace：批量删除选中笔记
        if obj is self.note_list and e.type() == QEvent.KeyPress and \
                e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected_or_current()
            return True
        return super().eventFilter(obj, e)

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
        if nid == self._current_id:
            self._loading = True
            self.title_edit.setText(name.strip())
            self._loading = False

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
                # 已是当前笔记：不再重载，避免打断编辑/覆盖未保存内容
                if nid != self._current_id:
                    self._load_note(nid)
                    self.refresh_list(keep_id=nid)
                # 正停在笔记列表页时，同步弹出编辑器；否则只静默加载
                st = getattr(self.win, "left_stack", None)
                if st is not None and st.isVisible() and st.currentIndex() == 2:
                    self.show_editor()
                return
        # 没有绑定的笔记：不打扰，保持当前编辑状态

    def _load_note(self, nid):
        """加载某篇笔记到编辑器。
        先保存当前编辑器内容：同一篇重载也必须先落盘，
        否则磁盘旧内容会覆盖掉未保存的编辑（表现为"笔记内容莫名变空"）。"""
        if self._current_id:
            self.save_current()
        self._current_id = nid
        data = self._all_notes()
        info = data.get(nid, {})
        self._loading = True
        self.editor.setHtml(info.get("html", ""))
        self.editor.apply_folded_keys(info.get("folded", []))
        self.title_edit.setText(info.get("title", ""))
        self._loading = False
        self.editor.fmt_bar.hide_bar()
        self._save_pending = False
        self._title_pending = None

    def _on_title_edited(self, text):
        """标题框输入：仅记录待提交标题，防抖后再写盘+刷新列表。"""
        if self._loading or not self._current_id:
            return
        self._title_pending = text.strip()
        self._save_pending = True
        self._save_timer.start(2000)
        self._title_timer.start(400)   # 停止输入 400ms 后才真正写入

    def _commit_title(self):
        """把防抖后的标题写入笔记并刷新列表。"""
        if self._title_pending is None or not self._current_id:
            return
        title = self._title_pending
        self._title_pending = None
        self._set_title(self._current_id, title)
        self.refresh_list(keep_id=self._current_id)

    def _set_title(self, nid, title):
        """写入笔记标题（空标题回退为占位名，避免列表中不可见）。"""
        data = self._all_notes()
        if nid not in data:
            return
        data[nid]["title"] = title or "未命名"
        self._save_notes(data)

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
        # 有未提交的标题改动：先落盘并刷新列表，避免切换/退出时丢失
        if self._title_pending is not None:
            self._title_timer.stop()
            self._commit_title()
        html = self.editor.toHtml()
        data = self._all_notes()
        if self._current_id not in data:
            return
        data[self._current_id]["html"] = html
        data[self._current_id]["folded"] = self.editor.folded_keys()
        if self._save_notes(data):
            self._save_pending = False
        else:
            # 写盘失败：保留待保存标记，提示用户（下次改动会自动再试）
            self._save_pending = True
            flush_save_errors(self)

    def _on_fold_changed(self):
        """折叠状态变化：标记待保存（走自动保存定时器）。"""
        if self._loading or not self._current_id:
            return
        self._save_pending = True
        self._save_timer.start(2000)


