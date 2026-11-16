# -*- coding: utf-8 -*-
"""画布视图与画布内联文字编辑器。"""
import os

from PySide6.QtCore import (QEvent, QTimer, QRectF, Qt)
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QTextCursor
from PySide6.QtWidgets import QGraphicsTextItem, QGraphicsView

from .constants import TEXT_FS
from .textutil import hex_rgb


class TextEditorItem(QGraphicsTextItem):
    """画布内联文字输入（类似图片编辑器）：点击处出现光标直接打字，
    回车/点击外部提交，Esc 取消。空白时提交=放弃。
    edit_anno 不为 None 时为编辑已有批注模式（提交时原地更新）。"""

    def __init__(self, win, scene_pos, color_hex, edit_anno=None):
        super().__init__()
        self.win = win
        self.color_hex = color_hex
        self.edit_anno = edit_anno          # 编辑模式：被编辑的批注 dict
        self.fs_pt = edit_anno["fs"] if edit_anno else (
            getattr(win, "font_size", None).value() if
            hasattr(win, "font_size") else TEXT_FS)   # PDF字号(pt)
        self.fs = max(6, int(round(self.fs_pt * win.zoom)))  # 场景字号随缩放
        self.setDefaultTextColor(QColor(color_hex))
        f = QFont("Microsoft YaHei", self.fs)
        self.setFont(f)
        if edit_anno:
            z = win.zoom
            self.setPos(edit_anno["p"][0] * z, edit_anno["p"][1] * z)
            self.setPlainText(edit_anno["text"])
            # 光标放末尾（放开头会让 Backspace 无效；全选会误删）
            cur = self.textCursor()
            cur.clearSelection()
            cur.movePosition(QTextCursor.MoveOperation.End)
            self.setTextCursor(cur)
            # 隐藏被编辑的原文字图元，否则编辑器文字与原批注重叠成"残影"
            self._hidden = []
            lst = win.annos.get(win.page_no, [])
            if edit_anno in lst:
                idx = lst.index(edit_anno)
                for it in win.scene.items():
                    if it.data(0) == idx and it.data(1) == "text" and \
                            type(it).__name__ == "QGraphicsTextItem":
                        it.hide()
                        self._hidden.append(it)
        else:
            self.setPos(scene_pos)
            self._hidden = []
        self.setZValue(30)
        self.setTabChangesFocus(True)
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self._done = False
        win.scene.addItem(self)
        self.setFocus()
        win.set_typing_mode(True)  # 打字期间禁用翻页/撤销等快捷键

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.commit()
            return
        if e.key() == Qt.Key_Escape:
            self.finish(cancel=True)
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        # 点击画布其他处/切工具 = 提交
        if not self._done:
            self.commit()
        super().focusOutEvent(e)

    def commit(self):
        txt = self.toPlainText().strip()
        self._done = True
        if txt:
            z = self.win.zoom
            pno = self.win._page_at_scroll(self.pos().y())
            ox, oy = self.win._page_origin(pno)
            px, py = (self.pos().x() - ox) / z, (self.pos().y() - oy) / z
            if self.edit_anno is not None:
                # 编辑模式：原地更新批注内容与字号，不新增
                before = self.win._snapshot_page(pno)
                self.edit_anno["text"] = txt
                self.edit_anno["fs"] = self.fs_pt
                self.win._refresh_page_annos(pno)
                self.win._push_undo(pno, before)
            else:
                a = {"type": "text", "p": (px, py), "text": txt, "fs": self.fs_pt,
                     "color": self.color_hex, "rgb": hex_rgb(self.color_hex), "wpt": 1}
                self.win._add_anno_to_scene(a, pno=pno)
        elif self.edit_anno is not None:
            # 编辑模式清空文字 = 删除该批注
            pno = self.win._page_at_scroll(self.pos().y())
            lst = self.win.annos.get(pno, [])
            if self.edit_anno in lst:
                before = self.win._snapshot_page(pno)
                lst.remove(self.edit_anno)
                self.win._refresh_page_annos(pno)
                self.win._push_undo(pno, before)
        self.finish()

    def finish(self, cancel=False):
        self._done = True
        self.win.set_typing_mode(False)
        if cancel:
            # 取消编辑：恢复被隐藏的原文字图元（rebuild 未发生，需手动恢复）
            for it in self._hidden:
                it.show()
        scene = self.scene()
        if scene:
            scene.removeItem(self)
        self.win.on_text_editor_closed()



class Canvas(QGraphicsView):
    """画布视图：鼠标/滚轮事件转发 + 中键平移 + 滚轮翻页。"""

    def __init__(self, win):
        super().__init__(win.scene)
        self.win = win
        self._mid = False
        self._rs = None     # 文字批注缩放状态：{"idx", "item", "fs0", "h0"}
        # 拖拽到窗口边缘时的自动滚动（否则选不全屏幕外的文字/画不到边缘）
        self._last_vp = None       # 最近一次鼠标视口坐标
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(30)
        self._edge_timer.timeout.connect(self._auto_scroll_step)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.TextAntialiasing)        # 文字抗锯齿
        self.setBackgroundBrush(QColor("#e8e8e8"))  # 浅灰底：页面白纸浮于其上
        self.setMouseTracking(True)  # 确保始终收到 mouseMoveEvent
        # 页面是静态位图，用 GPU 视口绘制（纹理合成走显卡），大页滚动/缩放明显更顺。
        # 图元各自已设 SmoothTransformation，无需再开视图级 SmoothPixmapTransform
        # （那会让每帧对整屏位图做一次 CPU 平滑重采样，是卡顿主因）。
        # 个别机器（远程桌面/老驱动）GL 视口可能画不出内容，可设环境变量
        # PDFANNO_GPU=0 退回默认光栅视口。
        if os.environ.get("QT_QPA_PLATFORM", "") != "offscreen" and \
                os.environ.get("PDFANNO_GPU", "1") != "0":
            try:
                from PySide6.QtOpenGLWidgets import QOpenGLWidget
                self.setViewport(QOpenGLWidget())
            except Exception:
                pass

    def _text_hit(self, sp):
        """selanno 工具下检测点击是否落在文字批注的右下角缩放柄上。
        返回 (图元, 批注索引)；不是则 (None, None)。柄=文字块右下角 12px 方块。"""
        for it in self.win.scene.items(sp):
            idx = it.data(0)
            if isinstance(idx, int) and it.data(1) == "text" and \
                    type(it).__name__ == "QGraphicsTextItem":
                br = it.sceneBoundingRect()
                handle = QRectF(br.right() - 12, br.bottom() - 12, 16, 16)
                if handle.contains(sp):
                    return it, idx
        return None, None

    EDGE_MARGIN = 36   # 距视口边缘多少像素内触发自动滚动
    EDGE_STEP = 24     # 每步滚动像素

    def _edge_delta(self, vp):
        """按鼠标视口坐标算边缘自动滚动的 (dx, dy)；不需滚动返回 (0, 0)。"""
        if vp is None:
            return 0, 0
        w, h = self.viewport().width(), self.viewport().height()
        dx = dy = 0

        def amt(pos, size):
            m = self.EDGE_MARGIN
            if pos < m:
                # 越靠边滚得越快（1~2 倍步长）
                return -int(self.EDGE_STEP * (1 + (m - max(0, pos)) / m))
            if pos > size - m:
                return int(self.EDGE_STEP * (1 + (pos - (size - m)) / m))
            return 0

        dx = amt(vp.x(), w)
        dy = amt(vp.y(), h)
        return dx, dy

    def _update_edge_scroll(self):
        """根据最近鼠标位置启停边缘自动滚动定时器。"""
        dx, dy = self._edge_delta(self._last_vp)
        if self.win.drawing and (dx or dy):
            if not self._edge_timer.isActive():
                self._edge_timer.start()
        else:
            self._edge_timer.stop()

    def _auto_scroll_step(self):
        """定时器：滚动视图并把拖拽预览延伸到新的鼠标位置。"""
        if not self.win.drawing or self._last_vp is None:
            self._edge_timer.stop()
            return
        dx, dy = self._edge_delta(self._last_vp)
        if not dx and not dy:
            self._edge_timer.stop()
            return
        hsb = self.horizontalScrollBar()
        vsb = self.verticalScrollBar()
        old_h, old_v = hsb.value(), vsb.value()
        hsb.setValue(old_h + dx)
        vsb.setValue(old_v + dy)
        # 滚动后用同一鼠标位置重算预览（场景坐标随滚动移动），实现"边滚边选"
        self.win.on_move_to(self.mapToScene(self._last_vp))
        # 滚到头（值没变）则停表，避免空转
        if hsb.value() == old_h and vsb.value() == old_v:
            self._edge_timer.stop()

    def mousePressEvent(self, e):
        # 右键：交给默认处理，让 contextMenuEvent 正常弹出层级菜单
        if e.button() == Qt.RightButton:
            super().mousePressEvent(e)
            return
        # 中键按下 = 临时抓手平移（任意工具下可用）
        if e.button() == Qt.MiddleButton and self.win.doc:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self._mid = True
            le = QMouseEvent(QEvent.MouseButtonPress, e.position(),
                             e.globalPosition(), Qt.LeftButton, Qt.LeftButton,
                             e.modifiers())
            super().mousePressEvent(le)
            e.accept()
            return
        # "选择批注"工具：命中文字缩放柄 → 进入缩放模式（不走场景默认选择）
        if e.button() == Qt.LeftButton and self.win.tool == "selanno" and self.win.doc:
            it, idx = self._text_hit(self.win.scene_pos(e))
            if it is not None:
                rp = self.win._page_at_scroll(it.sceneBoundingRect().center().y())
                a = self.win.annos[rp][idx]
                self._rs = {"idx": idx, "item": it, "fs0": a["fs"], "page": rp,
                            "h0": max(1.0, it.sceneBoundingRect().height())}
                self.setCursor(Qt.SizeFDiagCursor)
                e.accept()
                return
        # "查看"工具：左键若点中批注图元 → 选中它（便于按 Delete 删除），
        # 否则清除选择并交给基类做抓手平移。Ctrl+点击可追加多选。
        if e.button() == Qt.LeftButton and self.win.tool == "view" and self.win.doc:
            if self.win.select_anno_at(self.win.scene_pos(e)):
                e.accept()
                return
            self.win.scene.clearSelection()
        # 绘图工具：直接 accept，不调 super()（避免基类修改事件/坐标）
        # setMouseTracking(True) + accept 足以保证后续 mouseMoveEvent 到达
        if self.win.doc and self.win.tool not in ("view", "selanno"):
            self._last_vp = e.position().toPoint()
            self.win.on_press(e)
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self._last_vp = e.position().toPoint()
        # 拖拽到边缘时启用自动滚动（滚动条走到头会自动停）
        self._update_edge_scroll()
        # 文字缩放拖动中：按高度比例缩放字号并实时重建
        if self._rs is not None:
            sp = self.win.scene_pos(e)
            it = self._rs["item"]
            h0 = self._rs["h0"]
            rp = self._rs.get("page", self.win.page_no)
            if h0 > 0:
                ratio = max(0.15, (sp.y() - it.pos().y()) / h0)
                fs = max(4, min(200, self._rs["fs0"] * ratio))
                lst = self.win.annos[rp]
                if 0 <= self._rs["idx"] < len(lst) and lst[self._rs["idx"]]:
                    lst[self._rs["idx"]]["fs"] = fs
                    self.win._refresh_page_annos(rp)
                    # rebuild 后图元重建，找回对应项
                    for x in self.win.scene.items():
                        if x.data(0) == self._rs["idx"] and x.data(1) == "text" and \
                                type(x).__name__ == "QGraphicsTextItem":
                            self._rs["item"] = x
                            break
            e.accept()
            return
        if self.win.on_move(e):
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._edge_timer.stop()   # 松开鼠标：停止边缘自动滚动
        if self._rs is not None:
            self._rs = None
            self.setCursor(Qt.ArrowCursor)
            e.accept()
            return
        if e.button() == Qt.MiddleButton and self._mid:
            self._mid = False
            le = QMouseEvent(QEvent.MouseButtonRelease, e.position(),
                             e.globalPosition(), Qt.LeftButton, Qt.NoButton,
                             e.modifiers())
            super().mouseReleaseEvent(le)
            self.win.pick_tool(self.win.tool)
            e.accept()
            return
        if self.win.on_release(e):
            e.accept()
        else:
            super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        # 双击文字批注 → 原地编辑（清空提交=删除）。
        # "选择批注/选择文字/文字"工具都支持：文字工具下双击空白仍是新建
        if e.button() == Qt.LeftButton and \
                self.win.tool in ("selanno", "selecttext", "text") and self.win.doc:
            sp = self.win.scene_pos(e)
            for it in self.win.scene.items(sp):
                idx = it.data(0)
                if isinstance(idx, int) and it.data(1) == "text" and \
                        type(it).__name__ == "QGraphicsTextItem":
                    a = self.win.annos[self.win.page_no][idx]
                    if self.win.tool == "selecttext":
                        self.win.clear_selection()
                    self.win.commit_text_editor()
                    self.win._text_editor = TextEditorItem(
                        self.win, sp, a["color"], edit_anno=a)
                    self.win.statusBar().showMessage(
                        "编辑文字：改完回车或点空白处；清空文字=删除该批注")
                    e.accept()
                    return
        # "文字"工具双击空白：press 已提交了刚才那个编辑器，这里再开一个新的
        if e.button() == Qt.LeftButton and self.win.tool == "text" and self.win.doc:
            sp = self.win.scene_pos(e)
            self.win.commit_text_editor()
            self.win._text_editor = TextEditorItem(self.win, sp, self.win.color_hex)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        """右键菜单：Altium 原理图的 Descend（进入子图）/ Ascend（返回上级）。

        仅当文档的书签里带 Altium 层级记号 <pageN_iM> 时才提供，普通 PDF
        不弹菜单，保持原有交互不变。"""
        if not self.win.doc:
            super().contextMenuEvent(e)
            return
        sp = self.mapToScene(e.pos())
        menu = self.win.build_hier_menu(sp)
        if menu is None:
            super().contextMenuEvent(e)
            return
        menu.exec(e.globalPos())
        e.accept()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            # 以鼠标位置为锚点缩放（缩放后鼠标下内容保持不动）
            self.win.step_zoom(1 if e.angleDelta().y() > 0 else -1,
                               (e.position().x(), e.position().y()))
            e.accept()
            return
        # 整篇文档连续滚动：滚轮交给基类滚动视图（跨页自然衔接，无需"翻页阈值"）
        super().wheelEvent(e)


