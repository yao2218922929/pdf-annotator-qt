# -*- coding: utf-8 -*-
"""MainWindow annomixin mixin（拆分第4步）。"""
import math
import copy

from PySide6.QtCore import (QLineF, QPointF, QRectF, Qt)
from PySide6.QtGui import (QBrush, QColor, QFont, QPainterPath, QPen, QPolygonF)
from PySide6.QtWidgets import (QApplication, QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsPathItem, QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsTextItem)
from .canvas import (TextEditorItem)
from .constants import (HL_W, LEADER_DOTS_RE, LEADER_LINE_RE, PAGE_NUM_RE, WORDS_CACHE_MAX)
from .textutil import (hex_rgb, join_words_text)


class AnnoMixin:
    # ---------------- 批注项生成 ----------------
    def _mkpen(self, hexc, w):
        p = QPen(QColor(hexc))
        p.setWidthF(max(1.0, w))
        p.setCapStyle(Qt.RoundCap)
        p.setJoinStyle(Qt.RoundJoin)
        return p

    def anno_items(self, a, z=None, ox=0.0, oy=0.0):
        """把一条批注(存PDF坐标)转成场景图元列表。
        ox/oy = 该批注所在页的页顶/页左场景偏移（整篇文档坐标系）。"""
        z = z or self.zoom
        hexc, w = a["color"], max(1.0, a["wpt"] * z)
        t = a["type"]
        items = []

        def P(x, y):
            return QPointF(ox + x * z, oy + y * z)

        def R(x0, y0, x1, y1):
            x0, x1 = min(x0, x1), max(x0, x1)
            y0, y1 = min(y0, y1), max(y0, y1)
            return QRectF(ox + x0 * z, oy + y0 * z, (x1 - x0) * z, (y1 - y0) * z)

        if t == "rect":
            it = QGraphicsRectItem(R(*a["p1"], *a["p2"]))
            it.setPen(self._mkpen(hexc, a["wpt"] * z))
            items.append(it)
        elif t == "oval":
            it = QGraphicsEllipseItem(R(*a["p1"], *a["p2"]))
            it.setPen(self._mkpen(hexc, a["wpt"] * z))
            items.append(it)
        elif t == "line":
            p1, p2 = P(*a["p1"]), P(*a["p2"])
            it = QGraphicsLineItem(p1.x(), p1.y(), p2.x(), p2.y())
            it.setPen(self._mkpen(hexc, a["wpt"] * z))
            items.append(it)
        elif t == "arrow":
            items.extend(self.arrow_items(P(*a["p1"]), P(*a["p2"]),
                                          hexc, a["wpt"] * z))
        elif t in ("pen", "highlight"):
            pts = [P(x, y) for x, y in a["pts"]]
            if len(pts) >= 2:
                path = QPainterPath(pts[0])
                for p in pts[1:]:
                    path.lineTo(p)
                it = QGraphicsPathItem(path)
                pen = self._mkpen(hexc, HL_W * z if t == "highlight" else a["wpt"] * z)
                if t == "highlight":
                    c = QColor(hexc)
                    c.setAlpha(90)
                    pen.setColor(c)
                it.setPen(pen)
                items.append(it)
        elif t == "texthl":
            x0, y0, x1, y1 = a["rect"]
            it = QGraphicsRectItem(R(x0, y0, x1, y1))
            it.setPen(QPen(Qt.NoPen))
            c = QColor(hexc)
            c.setAlpha(100)
            it.setBrush(QBrush(c))
            items.append(it)
        elif t == "text":
            it = QGraphicsTextItem(a["text"])
            it.setDefaultTextColor(QColor(hexc))
            it.setFont(QFont("Microsoft YaHei", max(6, int(a["fs"] * z))))
            it.setPos(ox + a["p"][0] * z, oy + a["p"][1] * z)
            it.setData(1, "text")  # 标记为文字批注（供缩放柄识别）
            items.append(it)
        for it in items:
            it.setFlag(QGraphicsItem.ItemIsSelectable, True)  # 可被"选择批注"选中
            it.setData(1, t)   # 记录批注类型（供高亮的点选识别/删除）
        return items

    def arrow_items(self, p1, p2, hexc, w):
        pen = self._mkpen(hexc, w)
        line = QGraphicsLineItem(p1.x(), p1.y(), p2.x(), p2.y())
        line.setPen(pen)
        hl = max(10, w * 4)
        ang = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
        a1 = QPointF(p2.x() - hl * math.cos(ang - 0.42), p2.y() - hl * math.sin(ang - 0.42))
        a2 = QPointF(p2.x() - hl * math.cos(ang + 0.42), p2.y() - hl * math.sin(ang + 0.42))
        head = QGraphicsPolygonItem(QPolygonF([p2, a1, a2]))
        head.setPen(pen)
        head.setBrush(QColor(hexc))
        return [line, head]

    def rebuild_items(self):
        """重建批注图元：已渲染的页全部重画（整篇文档坐标系）。"""
        for pno in list(self._anno_items_map):
            for it in self._anno_items_map.pop(pno):
                try:
                    if it.scene() is not None:
                        self.scene.removeItem(it)
                except RuntimeError:
                    pass
        for pno in list(self._page_pix_items):
            self._anno_items_map[pno] = self._make_page_anno_items(pno)

    def _refresh_page_annos(self, pno):
        """某页批注改动后：只重建该页的批注图元。"""
        for it in self._anno_items_map.pop(pno, []):
            try:
                if it.scene() is not None:
                    self.scene.removeItem(it)
            except RuntimeError:
                pass
        if pno in self._page_pix_items:
            self._anno_items_map[pno] = self._make_page_anno_items(pno)

    def _add_anno_to_scene(self, a, record=True, pno=None):
        """追加一条批注并登记索引关联。record=False 时不记录撤销（供批量操作）。"""
        if pno is None:
            pno = self.page_no
        before = self._snapshot_page(pno) if record else None
        lst = self.annos.setdefault(pno, [])
        lst.append(a)
        ox, oy = self._page_origin(pno)
        if pno in self._page_pix_items:
            items = self._anno_items_map.setdefault(pno, [])
            for it in self.anno_items(a, ox=ox, oy=oy):
                it.setData(0, len(lst) - 1)
                it.setData(2, pno)
                self.scene.addItem(it)
                items.append(it)
        if record:
            self._push_undo(pno, before)

    def clear_preview(self):
        for it in self.preview_items:
            try:
                self.scene.removeItem(it)
            except Exception:
                pass   # scene.clear() 已销毁该对象：忽略，避免中断后续清理
        self.preview_items = []

    def add_preview(self, items):
        self.preview_items = items
        for it in items:
            self.scene.addItem(it)

    # ---------------- 鼠标事件 ----------------
    def scene_pos(self, e):
        # 注意: PySide6 的 mapToScene 只接受 QPoint（不接受 QPointF）
        return self.view.mapToScene(e.position().toPoint())

    def pdf_pos(self, sp, pno=None):
        """场景坐标 → 指定页的 PDF 坐标（减去该页页顶/页左偏移）。"""
        ox, oy = self._page_origin(self.page_no if pno is None else pno)
        return (sp.x() - ox) / self.zoom, (sp.y() - oy) / self.zoom

    def on_press(self, e):
        if not self.doc or self.tool in ("view", "selanno"):
            return False  # selanno 交给场景默认处理（点选/框选批注）
        if e.button() != Qt.LeftButton:
            return False
        sp = self.scene_pos(e)
        # 连续滚动下，落笔处所在页即本次绘制/编辑的目标页（可能≠当前页）
        self._draw_page = self._page_at_scroll(sp.y())
        if self.tool == "text":
            # 画布内联输入（类似图片编辑器）：已有编辑器则先提交旧的
            self.commit_text_editor()
            self._text_editor = TextEditorItem(self, sp, self.color_hex)
            self.statusBar().showMessage("输入文字，回车或点击空白处完成，Esc 取消")
            return True
        if self.tool == "selecttext":
            self.clear_selection()
        self.drawing = True
        self.start = sp
        self.free_pts = [sp]
        # 画笔/荧光笔：立即创建路径图元，后续 move 实时追加
        if self.tool in ("pen", "highlight"):
            path = QPainterPath()
            path.moveTo(sp)
            self._pen_item = QGraphicsPathItem(path)
            w = self.wpt * self.zoom
            if self.tool == "highlight":
                pen = self._mkpen(self.color_hex, HL_W * self.zoom)
                c = QColor(self.color_hex)
                c.setAlpha(90)
                pen.setColor(c)
            else:
                pen = self._mkpen(self.color_hex, w)
            self._pen_item.setPen(pen)
            self.scene.addItem(self._pen_item)
        return True

    def on_move(self, e):
        if not self.doc or self.tool == "view" or not self.drawing:
            return False
        return self.on_move_to(self.scene_pos(e))

    def on_move_to(self, sp):
        """按场景坐标 sp 更新拖拽预览（供鼠标移动与边缘自动滚动共用）。"""
        if not self.doc or self.tool == "view" or not self.drawing:
            return False
        t = self.tool
        w = self.wpt * self.zoom
        self.clear_preview()
        p0 = self.start
        if t == "rect":
            r = QRectF(p0, sp).normalized()
            it = QGraphicsRectItem(r)
            it.setPen(self._mkpen(self.color_hex, w))
            self.add_preview([it])
        elif t == "oval":
            r = QRectF(p0, sp).normalized()
            it = QGraphicsEllipseItem(r)
            it.setPen(self._mkpen(self.color_hex, w))
            self.add_preview([it])
        elif t == "line":
            # 注意：QGraphicsLineItem 没有 (QPointF, QPointF) 重载，必须用 QLineF
            it = QGraphicsLineItem(QLineF(QPointF(p0), QPointF(sp)))
            it.setPen(self._mkpen(self.color_hex, w))
            self.add_preview([it])
        elif t == "arrow":
            self.add_preview(self.arrow_items(QPointF(p0), QPointF(sp),
                                              self.color_hex, w))
        elif t in ("pen", "highlight"):
            # 实时预览：按 free_pts 重建整条路径（不依赖增量 lineTo，避免起点丢失画到左上角）
            self.free_pts.append(sp)
            if self._pen_item is not None:
                path = QPainterPath(self.start)
                for q in self.free_pts[1:]:
                    path.lineTo(q)
                self._pen_item.setPath(path)
        elif t == "selecttext":
            r = QRectF(p0, sp).normalized()
            items = self.draw_word_rects(self.words_in_scene(r))
            box = QGraphicsRectItem(r)
            box.setPen(QPen(QColor("#1e88e5"), 1, Qt.DashLine))
            items.append(box)
            self.add_preview(items)
        elif t == "texthl":
            r = QRectF(p0, sp).normalized()
            it = QGraphicsRectItem(r)
            it.setPen(QPen(QColor(self.color_hex), 1, Qt.DashLine))
            self.add_preview([it])
        return True

    def on_release(self, e):
        if not self.drawing:
            return False
        self.drawing = False
        sp = self.scene_pos(e)
        t = self.tool
        rgb = hex_rgb(self.color_hex)
        self.clear_preview()
        # 批注统一落到"起笔所在页"，避免跨页拖动时坐标错页
        dpage = self._draw_page
        if t == "selecttext":
            hits = self.words_in_scene(QRectF(self.start, sp).normalized())
            if not hits:
                self.statusBar().showMessage("未选中文字（该区域没有文字层，扫描件请用荧光笔）")
                return True
            self.sel_hits = hits
            self.sel_text = self.words_to_text(hits)
            self.render_selection()
            QApplication.clipboard().setText(self.sel_text)
            preview = self.sel_text.replace("\n", " ")
            if len(preview) > 60:
                preview = preview[:60] + "..."
            # 搜索框已打开时，自动填入选中文字并立即搜索
            if self._dock_active(self.search_dock):
                self.search_input.setText(" ".join(self.sel_text.split())[:60])
                self.do_search()
                self.statusBar().showMessage(
                    f"已搜索: {preview}", 4000)
            # 翻译面板已打开时，自动翻译选中文字
            elif self._dock_active(self.translate_dock):
                self.translate_panel.set_text(self.sel_text)
                self.statusBar().showMessage(
                    f"已翻译: {preview}", 4000)
            else:
                self.statusBar().showMessage(
                    f"已选中并复制 {len(self.sel_text)} 字符（按H可转为高亮批注）: {preview}")
            return True

        p1 = self.pdf_pos(self.start, dpage)
        p2 = self.pdf_pos(sp, dpage)
        if t in ("rect", "oval", "line", "arrow"):
            if (abs(sp.x() - self.start.x()) < 2 and
                    abs(sp.y() - self.start.y()) < 2):
                return True
            a = {"type": t, "p1": p1, "p2": p2, "color": self.color_hex,
                 "rgb": rgb, "wpt": self.wpt}
        elif t in ("pen", "highlight"):
            # 移除实时预览图元，转为正式批注
            if self._pen_item is not None:
                self.scene.removeItem(self._pen_item)
                self._pen_item = None
            if len(self.free_pts) < 2:
                return True  # 太短不保存
            pts = [self.pdf_pos(p, dpage) for p in self.free_pts]
            a = {"type": t, "pts": pts, "color": self.color_hex, "rgb": rgb,
                 "wpt": HL_W if t == "highlight" else self.wpt}
        elif t == "texthl":
            a_list = self.make_text_highlights(
                QRectF(QPointF(*p1), QPointF(*p2)).normalized(), pno=dpage)
            if not a_list:
                self.statusBar().showMessage(
                    "该区域未命中文字（文字高亮需拖过PDF真实文字）")
                return True
            before = self._snapshot_page(dpage)
            for a in a_list:
                self._add_anno_to_scene(a, record=False, pno=dpage)
            self._push_undo(dpage, before)
            self.statusBar().showMessage(f"已高亮 {len(a_list)} 行文字")
            return True
        else:
            return True

        self._add_anno_to_scene(a, pno=dpage)
        return True

    # ---------------- 文字选择/复制 ----------------
    def get_words(self, pno=None):
        """指定页文字词缓存（含坐标和内容），默认当前页。"""
        if pno is None:
            pno = self.page_no
        ws = self._words_cache.get(pno)
        if ws is None:
            ws = self.doc[pno].get_text("words")
            self._words_cache[pno] = ws
            # 只保留最近几页：本缓存按访问顺序淘汰，避免翻遍全书后
            # 把每页词表都留在内存里（大文档下这是几百 MB 级的泄漏）。
            while len(self._words_cache) > WORDS_CACHE_MAX:
                self._words_cache.popitem(last=False)
        else:
            self._words_cache.move_to_end(pno)
        return ws

    def _hit_words(self, pno, sel_rect):
        """在单页内返回与选区(该页PDF坐标)相交的词，保持阅读顺序。
        命中规则：选区与词框的竖向重叠 ≥ 词高35%（拖选罩住字的主体才算，擦边不算）。
        CAD导出PDF的词框比可见字形大很多（高17pt、字形仅10pt），阈值取35%既能让
        用户拖过文字时稳定命中，又能避免擦到相邻行（相邻行bbox仅重叠3pt≈17%）。"""
        page_h = self.doc[pno].rect.height if self.doc else 0
        out = []
        for w in self.get_words(pno):
            x0, y0, x1, y1 = w[:4]
            wr = QRectF(x0, y0, x1 - x0, y1 - y0)
            inter = sel_rect.intersected(wr)
            if inter.isEmpty():
                continue
            text = w[4].strip()
            # 目录点连线：独立点/连续点线直接忽略
            if LEADER_DOTS_RE.match(text):
                continue
            # 长纯符号线（----、____）忽略
            if len(text) >= 2 and LEADER_LINE_RE.match(text):
                continue
            wh = wr.height()
            ww = wr.width()
            # 命中判定沿"文字排列的垂直方向"：
            #   横排文字（含单个数字）：上下行相邻，按竖向覆盖判定
            #   竖排/旋转文字（原理图器件位号，如 HQ17101005GA0 高59pt）：左右相邻，
            #   按横向覆盖判定——否则用户横向拖选这一列时永远覆盖不到35%高度
            is_vert_text = wh > 20 and wh > ww * 1.8
            if is_vert_text:
                cov = inter.width() / ww if ww else 0
            else:
                cov = inter.height() / wh if wh else 0
            if cov < 0.35:
                continue
            # 页眉/页脚的独立数字（页码）：还要求横向覆盖40%，防拖动时误碰
            if (PAGE_NUM_RE.fullmatch(text)
                    and page_h and (y1 < 60 or y0 > page_h - 60)):
                ww = wr.width()
                if ww and inter.width() / ww < 0.4:
                    continue
            out.append(w)
        return out

    def words_in_scene(self, scene_rect):
        """跨页命中：选区(场景坐标)可能同时覆盖上/下邻页预览，逐页换算成该页
        PDF坐标后命中，按 上下页→当前页 的阅读顺序返回 [(页号, 词)]。"""
        hits = []
        for pno in sorted(self._scene_page_rects):
            pr = self._scene_page_rects[pno]
            inter = scene_rect.intersected(pr)
            if inter.isEmpty():
                continue
            # 场景坐标 → 该页 PDF 坐标
            pdf_r = QRectF((inter.left() - pr.left()) / self.zoom,
                           (inter.top() - pr.top()) / self.zoom,
                           inter.width() / self.zoom, inter.height() / self.zoom)
            for w in self._hit_words(pno, pdf_r):
                hits.append((pno, w))
        return hits

    def word_scene_rect(self, pno, w):
        """词(该页PDF坐标) → 场景坐标矩形（用于画选中高亮）。"""
        pr = self._scene_page_rects.get(pno)
        x0, y0, x1, y1 = w[:4]
        ox = pr.left() if pr else 0
        oy = pr.top() if pr else 0
        return QRectF(ox + x0 * self.zoom, oy + y0 * self.zoom,
                      (x1 - x0) * self.zoom, (y1 - y0) * self.zoom)

    def words_to_text(self, hits):
        """选中词按阅读顺序拼接：同行空格连接，换行换页都换行。"""
        lines, cur_key, cur = [], None, []
        for pno, w in hits:
            key = (pno, w[5], w[6])
            if key != cur_key:
                if cur:
                    lines.append(" ".join(cur))
                cur_key, cur = key, []
            cur.append(w[4])
        if cur:
            lines.append(" ".join(cur))
        return "\n".join(lines)

    def draw_word_rects(self, hits):
        """给命中的词画蓝色半透明高亮块（选择效果）。hits=[(页号, 词)]"""
        items = []
        for pno, w in hits:
            it = QGraphicsRectItem(self.word_scene_rect(pno, w))
            it.setPen(QPen(Qt.NoPen))
            c = QColor("#1e88e5")
            c.setAlpha(80)
            it.setBrush(QBrush(c))
            items.append(it)
        return items

    def clear_selection(self):
        """清除文字选中高亮。逐项 try：scene.clear() 后图元可能已被销毁，
        直接 removeItem 会抛 RuntimeError 中断清理，导致高亮"取消不掉"。"""
        for it in self.sel_items:
            try:
                self.scene.removeItem(it)
            except Exception:
                pass
        self.sel_items = []
        self.sel_text = ""
        self.sel_hits = []

    def render_selection(self):
        """按场景坐标重绘当前选择（缩放/翻页后调用）。"""
        for it in self.sel_items:
            try:
                self.scene.removeItem(it)
            except Exception:
                pass
        self.sel_items = []
        for pno, w in self.sel_hits:
            it = QGraphicsRectItem(self.word_scene_rect(pno, w))
            it.setPen(QPen(Qt.NoPen))
            c = QColor("#1e88e5")
            c.setAlpha(80)
            it.setBrush(QBrush(c))
            self.scene.addItem(it)
            self.sel_items.append(it)

    def copy_selection(self):
        if self._view_mode == "md":
            return   # MD 阅读时由 WebEngine 自带 Ctrl+C 复制
        if self.sel_text:
            QApplication.clipboard().setText(self.sel_text)
            self.statusBar().showMessage(
                f"已复制 {len(self.sel_text)} 字符（Ctrl+V 可粘贴）")

    def highlight_selection(self):
        """把'选择文字'工具拖选的内容转成高亮批注（按行合并，随PDF保存）。"""
        if self._view_mode == "md":
            return
        if not self.sel_hits:
            self.statusBar().showMessage(
                "请先用'选择文字'工具拖选文字，再按 H 或点'高亮选中'")
            return
        # 跨页选中时按页分别落到各自页面的批注里
        by_page = {}
        for pno, w in self.sel_hits:
            by_page.setdefault(pno, {}).setdefault((w[5], w[6]), []).append(w)
        n = 0
        snaps = {pno: self._snapshot_page(pno) for pno in by_page}
        for pno, groups in by_page.items():
            lst = self.annos.setdefault(pno, [])
            for words in groups.values():
                x0 = min(w[0] for w in words)
                y0 = min(w[1] for w in words)
                x1 = max(w[2] for w in words)
                y1 = max(w[3] for w in words)
                lst.append({"type": "texthl", "rect": (x0, y0, x1, y1),
                            "color": self.color_hex,
                            "rgb": hex_rgb(self.color_hex), "wpt": 1,
                            "text": join_words_text(words)})
                n += 1
            self._push_undo(pno, snaps[pno])
            self._refresh_page_annos(pno)
        self.clear_selection()
        self.statusBar().showMessage(
            f"已将选中文字转为高亮批注（{n} 行，颜色：当前所选颜色）")

    def delete_anno_dispatch(self):
        """Delete/Backspace 统一入口：焦点在左侧批注列表时删条目，否则删画布选中。"""
        if self.anno_list.hasFocus() and self.anno_list.selectedItems():
            self.delete_anno_list_selected()
        else:
            self.delete_selected()

    def _delete_annos_by_index(self, by_page):
        """按 {页号: {批注索引...}} 删除批注（可跨页），返回删除条数并记录撤销。"""
        snaps = {}
        for pno, idxs in by_page.items():
            lst = self.annos.get(pno)
            if not lst:
                continue
            snaps[pno] = self._snapshot_page(pno)
            for i in sorted(idxs, reverse=True):
                if 0 <= i < len(lst):
                    lst[i] = None
            self.annos[pno] = [a for a in lst if a]
        total = 0
        for pno, snap in snaps.items():
            total += sum(1 for a in snap if a) - len(self.annos.get(pno, []))
            self._refresh_page_annos(pno)
            self._push_undo(pno, snap)
        return total

    def delete_selected(self):
        """删除已选中的批注（Delete/Backspace）。
        选中来源：画布上点选的高亮/批注图元（查看/选择批注工具下），
        或「选择批注」工具框选。Markdown 模式下不拦截按键。"""
        if self._view_mode == "md":
            return
        # 按页归类被选中的批注：图元 data(2)=页号，data(0)=该页批注索引
        by_page = {}
        for it in self.scene.selectedItems():
            idx = it.data(0)
            pno = it.data(2)
            if isinstance(idx, int) and isinstance(pno, int):
                by_page.setdefault(pno, set()).add(idx)
        if not by_page:
            return
        total = self._delete_annos_by_index(by_page)
        if total:
            self.statusBar().showMessage(f"已删除 {total} 条批注")

    def delete_anno_list_selected(self):
        """删除左侧「批注列表」中选中的条目（Delete/Backspace）。"""
        items = self.anno_list.selectedItems()
        by_page = {}
        for it in items:
            pno = it.data(Qt.UserRole)
            idx = it.data(Qt.UserRole + 1)
            if isinstance(pno, int) and isinstance(idx, int):
                by_page.setdefault(pno, set()).add(idx)
        if not by_page:
            return
        total = self._delete_annos_by_index(by_page)
        self.refresh_anno_list()
        if total:
            self.statusBar().showMessage(f"已删除 {total} 条批注")

    def select_anno_at(self, sp):
        """画布点选：命中批注图元则选中它（按住 Ctrl 可追加多选），返回是否命中。"""
        hit = None
        for it in self.scene.items(sp):
            if isinstance(it.data(0), int) and isinstance(it.data(2), int):
                hit = it
                break
        if hit is None:
            return False
        if not (QApplication.keyboardModifiers() & Qt.ControlModifier):
            self.scene.clearSelection()
        hit.setSelected(True)
        self.statusBar().showMessage("已选中批注，按 Delete 删除")
        return True

    def make_text_highlights(self, sel_rect, pno=None):
        """查找与选区相交的文字词，按行合并成高亮批注。sel_rect为PDF坐标。"""
        words = self.get_words(pno)
        groups = {}
        for w in words:
            x0, y0, x1, y1, word, blk, line, wno = w[:8]
            wr = QRectF(x0, y0, x1 - x0, y1 - y0)
            if wr.intersects(sel_rect):
                groups.setdefault((blk, line), []).append(w)
        result = []
        for ws in groups.values():
            u = QRectF(ws[0][0], ws[0][1], ws[0][2] - ws[0][0], ws[0][3] - ws[0][1])
            for w in ws[1:]:
                u = u.united(QRectF(w[0], w[1], w[2] - w[0], w[3] - w[1]))
            result.append({"type": "texthl",
                           "rect": (u.left(), u.top(), u.right(), u.bottom()),
                           "color": self.color_hex,
                           "rgb": hex_rgb(self.color_hex), "wpt": 1,
                           "text": join_words_text(ws)})
        return result

    def _snapshot_page(self, pno):
        """深拷贝某页的批注列表（供撤销记录）。"""
        return copy.deepcopy(self.annos.get(pno, []))

    def _maybe_refresh_anno_list(self):
        """批注列表可见时才重建（避免无谓开销）。"""
        try:
            if self.left_stack.isVisible() and self.left_stack.currentIndex() == 3:
                self.refresh_anno_list()
        except Exception:
            pass

    def _push_undo(self, pno, before):
        """记录一次批注改动：before=改动前该页批注列表。清空重做栈。"""
        if before is None:
            return
        after = self._snapshot_page(pno)
        if before == after:
            return
        self._undo_stack.append({"page": pno, "before": before, "after": after})
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack = []
        self._sync_history_actions()
        self._maybe_refresh_anno_list()

    def _apply_page_snapshot(self, pno, snap):
        """把某页批注恢复为快照内容。若该页为空则移除键。"""
        if snap:
            self.annos[pno] = copy.deepcopy(snap)
        else:
            self.annos.pop(pno, None)

    def undo(self):
        """跨页撤销：回退最近一次批注改动（可跨页，自动跳到该页）。"""
        if self._view_mode == "md":
            return
        if not self._undo_stack:
            self.statusBar().showMessage("没有可撤销的批注改动")
            return
        rec = self._undo_stack.pop()
        pno = rec["page"]
        self._apply_page_snapshot(pno, rec["before"])
        self._redo_stack.append(rec)
        self._sync_history_actions()
        self._show_page_after_history(pno)
        self.statusBar().showMessage(f"已撤销第 {pno + 1} 页的批注改动")

    def redo(self):
        """重做：恢复最近一次被撤销的批注改动。"""
        if self._view_mode == "md":
            return
        if not self._redo_stack:
            self.statusBar().showMessage("没有可重做的批注改动")
            return
        rec = self._redo_stack.pop()
        pno = rec["page"]
        self._apply_page_snapshot(pno, rec["after"])
        self._undo_stack.append(rec)
        self._sync_history_actions()
        self._show_page_after_history(pno)
        self.statusBar().showMessage(f"已重做第 {pno + 1} 页的批注改动")

    def _show_page_after_history(self, pno):
        """撤销/重做后：滚到受影响页并重绘该页批注。"""
        if self.doc is None:
            return
        pno = max(0, min(pno, self.doc.page_count - 1))
        pno = max(0, min(pno, len(self._page_tops) - 1))
        self.page_no = pno
        self._loading = True
        self.page_spin.setValue(pno + 1)
        self._loading = False
        self.page_label.setText(f" {pno + 1}/{self.doc.page_count} ")
        self._set_scroll(self._page_tops[pno])   # 滚到该页页顶
        self._render_window()
        self._refresh_page_annos(pno)
        self._maybe_refresh_anno_list()

    def clear_page(self):
        if self._view_mode == "md":
            return
        if self.annos.get(self.page_no):
            before = self._snapshot_page(self.page_no)
            self.annos[self.page_no] = []
            self._refresh_page_annos(self.page_no)
            self._push_undo(self.page_no, before)

