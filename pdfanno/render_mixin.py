# -*- coding: utf-8 -*-
"""MainWindow rendermixin mixin（拆分第4步）。"""
import math
import copy
import pymupdf

from PySide6.QtCore import (QRectF, Qt)
from PySide6.QtGui import (QBrush, QColor, QImage, QPen, QPixmap)
from PySide6.QtWidgets import (QGraphicsPixmapItem, QGraphicsRectItem)
from .config import (progress_set)
from .constants import (MAX_PIX_DIM, PAGE_GAP, PAGE_PIX_BUDGET, REGION_CACHE_MAX, REGION_GRID, REGION_MAX_PIXELS, REGION_PIX_BUDGET, RENDER_SCALE, ZOOM_RENDER_STEP)


class RenderMixin:
    def turn_page(self, d, land=None):
        if not self.doc or self._view_mode == "md":
            return False
        np = self.page_no + d
        if 0 <= np < self.doc.page_count:
            self.page_no = np
            # 往回翻默认落页底衔接连续滚动；land=精确落点（页内偏移）
            self.load_page(scroll_bottom=(d < 0 and land is None), land=land)
            return True
        return False

    def on_spin(self, v):
        if self._loading or not self.doc:
            return
        if 1 <= v <= self.doc.page_count and v - 1 != self.page_no:
            self.page_no = v - 1
            self.load_page()

    def _quantized_render_scale(self, rz, dpr):
        """决定渲染倍率。

        稳态（未在连续缩放）直接用精确 rz —— 它正好等于"1 场景单位 = dpr 物理
        像素"的屏幕原生分辨率，即 1:1：图元不需任何缩放重采样，最清晰也最快。
        缩放进行中则把倍率上取到 1.25 的幂（复用同一批缓存，多步缩放只渲一次，
        跟手不卡）；停手 260ms 后由 _refine_zoom_render 重渲为精确 1:1。"""
        if not self._zooming_fast:
            return max(0.02, rz)
        base = RENDER_SCALE * dpr
        u = rz / base if base > 0 else 1.0
        if u > 0:
            k = math.ceil(math.log(u) / math.log(ZOOM_RENDER_STEP) - 1e-9)
            u = ZOOM_RENDER_STEP ** k
        return max(0.02, u * base)

    def _page_pix_needed(self, pno, rz_eff):
        """整页按 rz_eff 渲染所需的像素尺寸 (w, h)。"""
        r = self.doc[pno].rect
        return r.width * rz_eff, r.height * rz_eff

    def _use_full_page_render(self, pno, rz_eff):
        """整页渲染是否在预算内。超出则改为只渲染视口可见区域 —— 后者像素量与
        屏幕大小同级（与缩放无关），因此超宽原理图放到 400% 也保持 1:1 清晰，
        而不会像"整页降倍渲染"那样变糊，也不会因分配失败而白屏。"""
        w, h = self._page_pix_needed(pno, rz_eff)
        return w * h <= PAGE_PIX_BUDGET and max(w, h) <= MAX_PIX_DIM

    def _get_page_pixmap(self, pno, rz_eff, dpr):
        """整页渲染（用于像素预算内的常规页面）：缓存键含页号与档位。"""
        key = (pno, "page", round(rz_eff, 4))
        pm = self._page_pix_cache.get(key)
        if pm is not None:
            self._page_pix_cache.move_to_end(key)
            return pm
        page = self.doc[pno]
        # annots=False：批注由画布按 self.annos 绘制（可编辑、可撤销）。
        # 若把 PDF 里保存的原生注释一并渲染，会与画布绘制叠加成"双画"。
        pix = page.get_pixmap(matrix=pymupdf.Matrix(rz_eff, rz_eff), alpha=False,
                              annots=False)
        pm = self._to_pixmap(pix, dpr)
        self._page_pix_cache[key] = pm
        self._trim_pix_cache()
        return pm

    def _get_region_pixmap(self, pno, k0, j0, k1, j1, rz_eff, dpr):
        """只渲染页面上一块区域（场景坐标按 REGION_GRID 对齐后换算成 PDF 坐标）。

        区域起点严格等于 k0/j0 对齐值（不会被裁掉），故调用方可用
        pos=(页左+k0*GRID, 页顶+j0*GRID)、scale=rz/rz_eff 精确定位。"""
        key = (pno, "reg", k0, j0, k1, j1, round(rz_eff, 4))
        pm = self._page_pix_cache.get(key)
        if pm is not None:
            self._page_pix_cache.move_to_end(key)
            return pm
        page = self.doc[pno]
        z = self.zoom
        x0, y0 = k0 * REGION_GRID / z, j0 * REGION_GRID / z
        x1, y1 = k1 * REGION_GRID / z, j1 * REGION_GRID / z
        clip = pymupdf.Rect(x0, y0, x1, y1) & page.rect   # 只截右下超出部分
        # annots=False：同上，避免与画布绘制叠加成"双画"
        pix = page.get_pixmap(matrix=pymupdf.Matrix(rz_eff, rz_eff), clip=clip,
                              alpha=False, annots=False)
        pm = self._to_pixmap(pix, dpr)
        self._page_pix_cache[key] = pm
        self._trim_pix_cache()
        return pm

    @staticmethod
    def _to_pixmap(pix, dpr):
        img = QImage(pix.samples, pix.width, pix.height, pix.width * 3,
                     QImage.Format_RGB888).copy()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        return pm

    def _trim_pix_cache(self):
        """按条数与总像素预算双重裁剪渲染缓存。"""
        while len(self._page_pix_cache) > REGION_CACHE_MAX:
            self._page_pix_cache.popitem(last=False)
        total = 0
        for pm in self._page_pix_cache.values():
            total += pm.width() * pm.height()
        while total > REGION_PIX_BUDGET and len(self._page_pix_cache) > 1:
            _, pm = self._page_pix_cache.popitem(last=False)
            total -= pm.width() * pm.height()

    def _rebuild_doc_geometry(self):
        """按当前缩放重建"整篇文档"的场景几何：每页左上角 y 与页面尺寸。
        场景是一个连续的纵向长条（页与页之间留 PAGE_GAP），滚动条因此可以
        一路拖到第 1 页顶部与最后一页底部。各页横向居中于最宽页。"""
        z = self.zoom
        # PDF 原始尺寸与缩放无关，缓存一次即可；否则每档缩放都要遍历
        # 全文档取 131~749 次 page.rect，是缩放卡顿的固定开销之一。
        if self._raw_page_sizes is None or \
                len(self._raw_page_sizes) != self.doc.page_count:
            self._raw_page_sizes = [
                (self.doc[i].rect.width, self.doc[i].rect.height)
                for i in range(self.doc.page_count)]
        widths = [w * z for w, _h in self._raw_page_sizes]
        heights = [h * z for _w, h in self._raw_page_sizes]
        wmax = max(widths) if widths else 1.0
        self._page_tops = []
        self._page_sizes = []
        y = 0.0
        for i, (pw, ph) in enumerate(zip(widths, heights)):
            self._page_tops.append(y)
            self._page_sizes.append((pw, ph))
            y += ph + PAGE_GAP
        n = self.doc.page_count
        self._doc_scene_h = max(1.0, y - PAGE_GAP) if n else 1.0
        self._doc_scene_w = max(1.0, wmax)
        self._page_lefts = [(wmax - pw) / 2.0 for pw in widths]
        self._scene_page_rects = {
            i: QRectF(self._page_lefts[i], self._page_tops[i],
                      self._page_sizes[i][0], self._page_sizes[i][1])
            for i in range(n)}

    def _clear_doc_geometry(self):
        """清空整篇文档几何与窗口化渲染记录（换文档/关文档时调用）。"""
        self._scene_page_rects = {}
        self._page_tops = []
        self._page_lefts = []
        self._page_sizes = []
        self._raw_page_sizes = None
        self._doc_scene_h = 0.0
        self._doc_scene_w = 0.0
        self._page_pix_items = {}
        self._anno_items_map = {}
        self._page_render_sig = {}

    def _page_origin(self, pno=None):
        """某页左上角在场景中的坐标（缺省当前页）。"""
        if pno is None:
            pno = self.page_no
        if 0 <= pno < len(self._page_tops):
            return self._page_lefts[pno], self._page_tops[pno]
        return 0.0, 0.0

    def _page_at_scroll(self, val):
        """场景 y=val 处对应的页号（用于滚动条位置 → 当前页）。"""
        tops = self._page_tops
        if not tops:
            return 0
        for i in range(len(tops) - 1, -1, -1):
            if val >= tops[i] - 1:
                return i
        return 0

    def _set_scroll(self, val):
        """设置竖直滚动位置，且不触发"滚动→切页"联动。"""
        sb = self.view.verticalScrollBar()
        self._sync_scroll = True
        try:
            sb.setValue(int(max(sb.minimum(), min(val, sb.maximum()))))
        finally:
            self._sync_scroll = False

    def _ensure_page_visible_x(self):
        """确保当前页在视口内横向可见：若被横向滚动推到视口之外，则把该页
        横向居中。修复"上一页是超宽原理图并横拖到最右，切到窄页后画面空白"
        （窄页左右居中、左偏移大，残留的横向滚动位置把它留在视口左侧之外）。"""
        if not self._page_tops or not (0 <= self.page_no < len(self._page_tops)):
            return
        hb = self.view.horizontalScrollBar()
        vp = self.view.viewport()
        left = self._page_lefts[self.page_no]
        w = self._page_sizes[self.page_no][0]
        h0 = hb.value()
        if left + w < h0 or left > h0 + vp.width():
            hb.setValue(int(max(hb.minimum(),
                                min(left - (vp.width() - w) / 2.0, hb.maximum()))))

    def _render_window(self):
        """窗口化渲染：只保留视口附近的页（上下各多留一屏），其余页图元移除以省内存。"""
        if self._rendering or not self.doc or not self._page_tops:
            return
        self._rendering = True
        try:
            self._render_window_inner()
        finally:
            self._rendering = False

    def _render_plan(self, pno, rz, dpr, vw, vh, sbv, hbv):
        """统一决定某页"怎么渲染"，返回 (mode, rz_eff, grids)，并据此生成签名。

        mode="full"：整页一张图（rz_eff 受整页像素预算约束）
        mode="reg" ：只渲染视口覆盖区域（rz_eff 受单次区域像素上限约束）
        两个上限都保证 QPixmap 不会超过显卡纹理上限 → 不白屏。"""
        rz_eff = self._quantized_render_scale(rz, dpr)
        if self._use_full_page_render(pno, rz_eff):
            # 整页：按屏幕原生 1:1 渲染（纹理随缩放平方膨胀，超采样会卡）
            return "full", rz_eff, None
        # 区域渲染：像素量只与屏幕大小同级，同样按屏幕原生 1:1 渲染
        k0, j0, k1, j1 = self._region_grids(pno, vw, vh, sbv, hbv)
        # 区域像素上限：单边不超过纹理上限，总像素不超过预算
        z = self.zoom
        g = REGION_GRID
        reg_w_pdf = (k1 - k0) * g / z if z else 1.0
        reg_h_pdf = (j1 - j0) * g / z if z else 1.0
        if reg_w_pdf > 0 and reg_h_pdf > 0:
            by_area = math.sqrt(REGION_MAX_PIXELS / (reg_w_pdf * reg_h_pdf))
            by_dim = MAX_PIX_DIM / max(reg_w_pdf, reg_h_pdf)
            cap = min(by_area, by_dim)
            if rz_eff > cap:
                rz_eff = max(0.02, cap)
        return "reg", rz_eff, (k0, j0, k1, j1)

    def _render_window_inner(self):
        dpr = self.view.devicePixelRatioF()
        rz = self.zoom * dpr
        sb = self.view.verticalScrollBar()
        hb = self.view.horizontalScrollBar()
        vp = self.view.viewport()
        vh, vw = vp.height(), vp.width()
        lo = sb.value() - vh
        hi = sb.value() + vh * 2
        want = set()
        for i, r in enumerate(self._scene_page_rects.values()):
            if r.bottom() >= lo and r.top() <= hi:
                want.add(i)
        for pno in list(self._page_pix_items):
            if pno not in want:
                self._drop_page_items(pno)
        for pno in sorted(want):
            mode, rz_eff, grids = self._render_plan(
                pno, rz, dpr, vw, vh, sb.value(), hb.value())
            new_sig = (mode, round(rz_eff, 4)) if mode == "full" else \
                      (mode, round(rz_eff, 4)) + tuple(grids)
            if pno in self._page_pix_items and \
                    self._page_render_sig.get(pno) == new_sig:
                continue          # 已按同一档位/区域渲染过，无需重建
            if pno in self._page_pix_items:
                self._drop_page_items(pno)   # 档位或可见区域变了：重渲该页
            self._render_one_page(pno, rz, rz_eff, dpr, mode, grids, new_sig)

    def _region_grids(self, pno, vw, vh, sbv, hbv):
        """当前视口在该页内覆盖的区域，按 REGION_GRID 向外对齐后的格坐标。"""
        pl, pt = self._page_origin(pno)
        pw, ph = self._page_sizes[pno]
        x0 = max(0.0, min(hbv - pl, pw))
        x1 = max(0.0, min(hbv + vw - pl, pw))
        y0 = max(0.0, min(sbv - pt, ph))
        y1 = max(0.0, min(sbv + vh - pt, ph))
        g = REGION_GRID
        # 向外各留一格：小幅滚动时区域不变，免去反复重渲
        k0 = max(0, int(math.floor(x0 / g)) - 1)
        j0 = max(0, int(math.floor(y0 / g)) - 1)
        k1 = min(int(math.ceil(pw / g)), int(math.ceil(x1 / g)) + 1)
        j1 = min(int(math.ceil(ph / g)), int(math.ceil(y1 / g)) + 1)
        return k0, j0, k1, j1

    def _drop_page_items(self, pno):
        """移除某页的页面图元与批注图元（窗口化/重渲前调用）。"""
        for it in self._page_pix_items.pop(pno, []):
            if it.scene() is not None:
                self.scene.removeItem(it)
        for it in self._anno_items_map.pop(pno, []):
            if it.scene() is not None:
                self.scene.removeItem(it)
        self._page_render_sig.pop(pno, None)

    def _render_one_page(self, pno, rz, rz_eff, dpr, mode, grids, sig):
        """渲染单页：预算内整页渲染；超宽/超高页只渲染视口可见区域。
        rz_eff 可能因面积上限而低于 rz，故按 rz/rz_eff 缩放回场景尺寸。"""
        pl, pt = self._page_origin(pno)
        pw, ph = self._page_sizes[pno]
        ratio = rz / rz_eff if rz_eff else 1.0
        items = []
        pit = QGraphicsPixmapItem()
        if mode == "full":
            pit.setPixmap(self._get_page_pixmap(pno, rz_eff, dpr))
            pit.setPos(pl, pt)
        else:
            k0, j0, k1, j1 = grids
            pm = self._get_region_pixmap(pno, k0, j0, k1, j1, rz_eff, dpr)
            pit.setPixmap(pm)
            pit.setPos(pl + k0 * REGION_GRID, pt + j0 * REGION_GRID)
        pit.setScale(ratio)   # 档位高于当前缩放时按比例缩小回正确尺寸
        # 平滑缩小（默认最近邻会让缩小时的细线丢像素发糊、且每帧重采样更卡）；
        # 档位最多比当前缩放高 1.25 倍，缩小幅度很小，平滑开销可忽略。
        pit.setTransformationMode(Qt.SmoothTransformation)
        pit.setZValue(0)
        pit.setAcceptedMouseButtons(Qt.NoButton)   # 点击穿透到批注层
        self.scene.addItem(pit)
        items.append(pit)
        b = QGraphicsRectItem(QRectF(pl - 0.5, pt - 0.5, pw + 1, ph + 1))
        b.setBrush(QBrush(Qt.NoBrush))
        b.setPen(QPen(QColor("#c0c0c0"), 1))
        b.setZValue(1)    # 压在页面之上，边框才不会被 pixmap 盖住
        b.setAcceptedMouseButtons(Qt.NoButton)   # 纯装饰，不拦截鼠标
        self.scene.addItem(b)
        items.append(b)
        self._page_pix_items[pno] = items
        self._page_render_sig[pno] = sig
        self._anno_items_map[pno] = self._make_page_anno_items(pno)

    def _make_page_anno_items(self, pno):
        """生成某页全部批注图元并加入场景（坐标系含该页页顶偏移）。"""
        out = []
        ox, oy = self._page_origin(pno)
        for i, a in enumerate(self.annos.get(pno, [])):
            if not a:
                continue
            for it in self.anno_items(a, ox=ox, oy=oy):
                it.setData(0, i)  # 图元 ↔ 批注列表索引 关联
                it.setData(2, pno)  # 记录所属页号（供按 Delete 删除定位）
                self.scene.addItem(it)
                out.append(it)
        return out

    def _on_view_scrolled(self):
        """滚动条变化：把"跟随滚动"的工作全部推迟到事件循环执行。
        绝不能在 valueChanged 回调里同步做任何事——此时 Qt 正在滚动/绘制中
        遍历内部状态，重入会导致偶发访问违例（表现为退出时随机崩溃）。
        0ms 单次定时器同时把连续多帧滚动合并成一次处理。"""
        if self._sync_scroll or self._rendering or not self.doc or not self._page_tops:
            return
        self._win_timer.start()

    def _apply_scroll_state(self):
        """定时器回调：更新当前页并刷新窗口化渲染（滚动已稳定）。"""
        if not self.doc or not self._page_tops:
            return
        self._render_window()
        self._sync_page_from_scroll()
        self._ensure_page_visible_x()   # 竖滚进入更窄的页时，页可能整页在视口外

    def _sync_page_from_scroll(self):
        """按滚动位置更新"当前页"（页码框/标签/进度/批注列表加粗）。"""
        sb = self.view.verticalScrollBar()
        pno = self._page_at_scroll(sb.value())
        if pno == self.page_no:
            return
        self.page_no = pno
        self._loading = True
        self.page_spin.setValue(pno + 1)
        self._loading = False
        self.page_label.setText(f" {pno + 1}/{self.doc.page_count} ")
        self._progress_timer.start(500)
        if hasattr(self, "toc_tree"):
            self.toc_tree.viewport().update()
        self._maybe_refresh_anno_list()

    def load_page(self, scroll_bottom=False, keep_scroll=False, land=None,
                  defer_render=False):
        self.commit_text_editor()  # 翻页/缩放前提交内联文字（防图元悬挂到错误页）
        if not self.doc:
            return
        dpr = self.view.devicePixelRatioF()

        self._rebuild_doc_geometry()   # 整篇文档场景几何（页顶 y、总高）
        ph = self._page_sizes[self.page_no][1]
        top = self._page_tops[self.page_no]

        self._loading = True
        self.page_spin.setValue(self.page_no + 1)
        self._loading = False
        self.page_label.setText(f" {self.page_no + 1}/{self.doc.page_count} ")
        self.zoom_label.setText(f"  {int(self.user_zoom * 100)}%  ")

        # 场景 = 整篇文档：滚动条贯穿全文（可一路拖到首页顶部/末页底部）
        doc_rect = QRectF(0.0, 0.0, self._doc_scene_w, self._doc_scene_h)
        # 先丢弃图元映射再 scene.clear()：setSceneRect 会同步触发滚动信号 →
        # _render_window，若映射仍指向已被 clear 销毁的 C++ 图元，removeItem
        # 会访问已释放对象（表现为退出时偶发崩溃）。绘制期间一并加锁重入。
        self._page_pix_items = {}
        self._anno_items_map = {}
        self._page_render_sig = {}
        self._rendering = True
        try:
            self.scene.clear()
            self.scene.setSceneRect(doc_rect)
            self.view.setSceneRect(doc_rect)
        finally:
            self._rendering = False
        self.preview_items = []
        self.sel_items = []      # scene.clear()已销毁旧图元，引用置空
        # 先按目标位置定滚动条，再渲染视口附近的页（窗口化）
        vh = self.view.viewport().height()
        if land is not None:
            # 无缝翻页落点：页内偏移（相对页顶），钳制在本页范围内
            target = top + max(0.0, min(land, ph - vh))
        elif scroll_bottom:
            target = top + max(0.0, ph - vh)
        elif not keep_scroll:
            target = top
        else:
            target = None
        if target is not None:
            self._set_scroll(target)
        self._ensure_page_visible_x()   # 横向残留曾导致切页后画面空白
        # defer_render：调用方（缩放）会在设好最终滚动位置后自行渲染，
        # 此处按"旧滚动位置"渲染的这次是多余的，跳过可省一半栅格化开销。
        if not defer_render:
            self._render_window()
        self._sync_page_from_scroll()
        self.render_selection()
        self.render_page_hits()
        # 目录树里"当前页"书签颜色随翻页更新
        if hasattr(self, "toc_tree"):
            self.toc_tree.viewport().update()
        self._progress_timer.start(500)   # 记录阅读进度（防抖）
        self._maybe_refresh_anno_list()   # 更新批注列表中"当前页"加粗标记

    def _save_progress(self):
        """把当前文档的页码/缩放写入进度文件。"""
        if self.doc is not None and self.pdf_path:
            try:
                progress_set(self.pdf_path, self.page_no, self._zoom_factor)
            except Exception:
                pass

