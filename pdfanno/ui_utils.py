# -*- coding: utf-8 -*-
"""小型 UI 工具函数。"""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


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
