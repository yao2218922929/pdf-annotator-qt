# -*- coding: utf-8 -*-
"""目录（书签）树的自绘 delegate。"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QStyle


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


