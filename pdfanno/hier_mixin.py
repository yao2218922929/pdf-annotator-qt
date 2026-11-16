# -*- coding: utf-8 -*-
"""MainWindow hiermixin mixin（拆分第4步）。"""
import pymupdf

from PySide6.QtWidgets import (QMenu)
from .constants import (ALT_ACTION_RE, ALT_DST_RE, ALT_INSTANCE_RE, ALT_NAME_RE, ALT_RECT_RE, HIER_TITLE_RE)


class HierMixin:
    # ---------------- Altium 原理图层级导航（Descend / Ascend） ----------------
    def _ensure_hier_index(self):
        """惰性构建层级索引：书签内容变化（编辑标题/页码、切换文档）时自动重建。
        签名含书签内容而非仅条数——否则"重命名书签但条数不变"会用到过期索引。"""
        sig = (id(self.doc),
               tuple((lv, ti, pn) for lv, ti, pn in self.toc_items))
        if self._hier_index is not None and self._hier_sig == sig:
            return
        self._hier_index = self._build_hier_index()
        self._hier_sig = sig

    def _build_hier_index(self):
        """解析 Altium 层级书签，返回 [(name_lower, parent_pno, inst,
        first_child, last_child)]（页号均 0 基）。

        关键：书签里的 <pageN> 是 **Altium 图纸名**，不是页码。顶层图纸偶然
        按顺序命名，所以顶层条目的 N 看着像页码；一旦嵌套就对不上——例如
        controller_vr14_4a4phase_bhs_compress<page2_i11>(109) 里的 "page2"
        图纸实际在 PDF 第 108 页。真实父页取自**书签树里最近的上级图纸**
        （lvl>=2 的最近祖先）所在页；顶层条目没有这种祖先，此时 N 恰好等于
        页码，直接用 N。"""
        toc = self.toc_items
        n = self.doc.page_count
        out = []
        for i, (lvl, title, _pno) in enumerate(toc):
            m = HIER_TITLE_RE.match((title or "").strip())
            if not m:
                continue
            rng = self._page_range(m.group("pages"))
            if rng is None:
                continue
            first, last = rng
            if not (0 <= first < n):
                continue
            parent = self._hier_parent_page(toc, i, first)
            if parent is None or not (0 <= parent < n):
                continue
            out.append((m.group("name").strip().lower(), parent,
                        m.group("inst"), first, min(last, n - 1)))
        return out

    @staticmethod
    def _page_range(s):
        """'121' -> (120,120)；'110-111' -> (109,110)；无效返回 None（均 0 基）。"""
        if not s:
            return None
        s = s.strip()
        if "-" in s:
            a, b = s.split("-", 1)
            if a.strip().isdigit() and b.strip().isdigit():
                return int(a) - 1, int(b) - 1
            return None
        return (int(s) - 1, int(s) - 1) if s.isdigit() else None

    def _hier_parent_page(self, toc, idx, child_first):
        """层级条目 idx 的父图纸所在页（0 基）。

        书签树本身就是 Altium 的图纸层级：往上找第一个 lvl 更小的条目即
        最近祖先；若该祖先是图纸（lvl>=2）且排在子图之前，取它所在页。
        走到根节点（lvl1）仍没有图纸祖先时，退回用 N（顶层图纸名的 N 即页码）。"""
        cur = toc[idx][0]
        j = idx - 1
        while j >= 0:
            lj = toc[j][0]
            if lj < cur:
                if lj >= 2:
                    p = toc[j][2] - 1
                    if 0 <= p < child_first:
                        return p
                cur = lj
            j -= 1
        m = HIER_TITLE_RE.match((toc[idx][1] or "").strip())
        return int(m.group("parent")) - 1 if m else None

    def hier_children(self, pno, name=None):
        """某页的子图条目 [(name, first_child, last_child, inst)]，按子图页排序。
        优先用 Altium 原生链接（精确到块），无链接时退回书签推断。"""
        by_page, _ = self._alt_links()
        ents = [e for e, _r in by_page.get(pno, [])]
        if not ents:
            self._ensure_hier_index()
            ents = [(nm, first, last, inst)
                    for nm, par, inst, first, last in self._hier_index
                    if par == pno]
        if name is not None:
            ents = [e for e in ents if e[0] == name]
        return sorted(ents, key=lambda t: t[1])

    # ---- Altium 原生 Descend 链接（首选，精确到每个 sheet 块） ----
    def _alt_links(self):
        """解析 PDF 内嵌的 Altium Descend 链接，返回 (by_page, parent_of)。

        Altium 导出 PDF 时会给每个 sheet 块放一个 Link 注释，其 /A 指向内嵌
        JS（就是 Altium 里右键弹出的那个 Descend 菜单）：
            var name="Instance I4 of PWR_EFUSE_MP5991_12VIN_20A_COMPRESS";
            var dst="@LIB.SHEET(SCH_1):PAGE12135_I4@LIB.PWR_EFUSE_..._COMPRESS(SCH_1)";
            MyFunc(name, attr, lst, "Descend", dst);
        /Rect 就是该块的点击范围，dst 是命名目标键、可直接查到真实页号。
        这是官方数据：同页多个同名块各有自己的 Rect，能精确区分点的是哪一个。
        解析一次后缓存（仅在换文档时重做）。"""
        if self._alt_ready and self._alt_sig == id(self.doc) \
                and self._alt_doc is self.doc:
            return self._alt_map, self._alt_parent
        self._alt_doc = self.doc
        by_page, parent_of = {}, {}
        doc = self.doc
        if doc is not None:
            try:
                names = doc.resolve_names()
            except Exception:
                names = {}
            for pno in range(doc.page_count):
                try:
                    axrefs = list(doc[pno].annot_xrefs())
                except Exception:
                    continue
                for a in axrefs:
                    xr = a[0] if isinstance(a, (tuple, list)) else a
                    try:
                        obj = doc.xref_object(int(xr), compressed=True)
                    except Exception:
                        continue
                    if not obj or "/Subtype/Link" not in obj.replace(" ", ""):
                        continue
                    ma = ALT_ACTION_RE.search(obj)
                    mr = ALT_RECT_RE.search(obj)
                    if not (ma and mr):
                        continue
                    try:
                        act = doc.xref_object(int(ma.group(1)), compressed=True)
                    except Exception:
                        continue
                    if "Descend" not in (act or ""):
                        continue
                    mname = ALT_NAME_RE.search(act)
                    mdst = ALT_DST_RE.search(act)
                    if not (mname and mdst):
                        continue
                    info = names.get(mdst.group(1).replace("\\", ""))
                    if not info or info.get("page") is None:
                        continue
                    target = info["page"]
                    if not (0 <= target < doc.page_count):
                        continue
                    mi = ALT_INSTANCE_RE.match(mname.group(1))
                    inst = mi.group(1) if mi else ""
                    sheet = (mi.group(2) if mi else "").lower()
                    # 注释里的 /Rect 用 PDF 原始坐标（左下原点），须换算成
                    # pymupdf 的左上原点：fitz_y = 页高 - pdf_y。
                    ph = doc[pno].rect.height
                    x0, y0, x1, y1 = (float(mr.group(i)) for i in range(1, 5))
                    rect = pymupdf.Rect(min(x0, x1), ph - max(y0, y1),
                                        max(x0, x1), ph - min(y0, y1))
                    by_page.setdefault(pno, []).append(
                        ((sheet, target, target, inst), rect))
                    parent_of.setdefault(target, pno)
        self._alt_map, self._alt_parent = by_page, parent_of
        self._alt_sig, self._alt_ready = id(doc), True
        return by_page, parent_of

    def _hier_boxes_for(self, pno):
        """某页的 [(条目, 点击矩形)]（PDF 坐标）——直接来自 Altium 原生链接。"""
        by_page, _ = self._alt_links()
        return by_page.get(pno, [])

    def _hier_entry_at(self, sp):
        """点击处(场景坐标)对应的层级条目；无法确定时 None。
        首选 Altium 原生链接的矩形（精确到块，同名块也能区分）；该页无链接时
        退化为按名称文字匹配——且仅当该名称只对应唯一子图时才采用，否则返回
        None，交给调用方列出候选（避免猜错块跳到错误页）。"""
        by_page, _ = self._alt_links()
        for pno in sorted(self._scene_page_rects):
            pr = self._scene_page_rects[pno]
            if not pr.contains(sp):
                continue
            entries = by_page.get(pno)
            if not entries:
                continue
            px = (sp.x() - pr.left()) / self.zoom
            py = (sp.y() - pr.top()) / self.zoom
            pt = pymupdf.Point(px, py)
            for ent, rect in entries:
                if rect.contains(pt):
                    return ent
        name = self._hier_name_at(sp)
        if not name:
            return None
        pno = self._page_at_scroll(sp.y())
        kids = self.hier_children(pno, name)
        return kids[0] if len(kids) == 1 else None

    def _hier_name_at(self, sp):
        """点击处(场景坐标)命中的书签子图名；没有则 None。
        逐页把场景坐标换算成 PDF 坐标，取覆盖该点的词，再与层级索引里的
        名字比对（大小写无关、允许名称被拆词时的前缀匹配）。"""
        for pno in sorted(self._scene_page_rects):
            pr = self._scene_page_rects[pno]
            if not pr.contains(sp):
                continue
            px, py = (sp.x() - pr.left()) / self.zoom, (sp.y() - pr.top()) / self.zoom
            children = {nm for nm, _f, _l, _i in self.hier_children(pno)}
            if not children:
                continue
            best = None
            for w in self.get_words(pno):
                x0, y0, x1, y1, t = w[:5]
                if not (x0 - 1 <= px <= x1 + 1 and y0 - 1 <= py <= y1 + 1):
                    continue
                tl = t.strip().lower()
                # 完整名命中优先；其次名称被拆成多词时取最长前缀词
                if tl in children:
                    return tl
                if len(tl) >= 6:
                    for nm in children:
                        if nm.startswith(tl) and (best is None or len(tl) > len(best)):
                            best = tl
                break
            return best
        return None

    def build_hier_menu(self, sp):
        """构造层级右键菜单；无层级信息时返回 None（不影响普通 PDF）。"""
        if not self.doc:
            return None
        by_page, _ = self._alt_links()
        self._ensure_hier_index()
        if not by_page and not self._hier_index:
            return None         # 既无 Altium 链接也无层级书签：普通 PDF
        pno = self._page_at_scroll(sp.y())
        menu = QMenu(self.view)

        # ---- Descend：进入子图 ----
        # 优先用 Altium 原生链接的精确矩形（同名块也能区分点的是哪一个）
        hit = self._hier_entry_at(sp)
        if hit is not None:
            nm, first, last, inst = hit
            a = menu.addAction(
                f"⬇ Descend 进入子图：{nm}（{self._page_span(first, last)}）")
            a.triggered.connect(lambda _=False, c=first: self._hier_jump(c))
            allkids = self.hier_children(pno)
            if len(allkids) > 1:
                # 同页还有别的块：一并列出，便于跨块跳转
                sub = menu.addMenu(f"本页其他子图（{len(allkids) - 1} 个）")
                for onm, ofirst, olast, oinst in allkids:
                    if (onm, ofirst) == (nm, first):
                        continue
                    x = sub.addAction(
                        f"{self._page_span(ofirst, olast)}  （{oinst}）")
                    x.triggered.connect(lambda _=False, c=ofirst: self._hier_jump(c))
        else:
            name = self._hier_name_at(sp)
            kids = self.hier_children(pno, name) if name else []
            if kids:
                if len(kids) == 1:
                    nm, first, last, _inst = kids[0]
                    a = menu.addAction(
                        f"⬇ Descend 进入子图：{nm}（{self._page_span(first, last)}）")
                    a.triggered.connect(lambda _=False, c=first: self._hier_jump(c))
                else:
                    # 同页多个同名 sheet，但丝印未能配对（PDF 无实例标记）：
                    # 无法自动分辨点击的是哪一个，列出全部候选让用户选。
                    sub = menu.addMenu(f"⬇ Descend 进入子图（{len(kids)} 个同名）")
                    for _nm, first, last, inst in kids:
                        a = sub.addAction(
                            f"{self._page_span(first, last)}  （{inst}）")
                        a.triggered.connect(lambda _=False, c=first: self._hier_jump(c))
            elif name:
                menu.addAction(f"（“{name}”没有对应的子图页）").setEnabled(False)
            else:
                # 点在空白处：列出本页全部子图，供手动选择
                allkids = self.hier_children(pno)
                if allkids:
                    sub = menu.addMenu(f"⬇ Descend 本页子图（{len(allkids)} 个）")
                    for nm, first, last, _inst in allkids:
                        a = sub.addAction(
                            f"{nm[:40]}  →  {self._page_span(first, last)}")
                        a.triggered.connect(lambda _=False, c=first: self._hier_jump(c))

        # ---- Ascend：当前页若是由某个父页下钻而来，提供返回 ----
        parent = self.hier_parent_of(pno)
        if parent is not None:
            if not menu.isEmpty():
                menu.addSeparator()
            a = menu.addAction(f"⬆ Ascend 返回上级（第 {parent + 1} 页）")
            a.triggered.connect(lambda _=False, p=parent: self._hier_jump(p))
        # 既无子图也无上级：不弹空菜单
        return menu if not menu.isEmpty() else None

    @staticmethod
    def _page_span(first, last):
        """页范围文字：单页 '第 121 页'；多页 '第 110-111 页'（入参 0 基）。"""
        if first == last:
            return f"第 {first + 1} 页"
        return f"第 {first + 1}-{last + 1} 页"

    def hier_parent_of(self, pno):
        """子图页 → 它所在的父页；没有则 None。多父时取第一个。
        优先用 Altium 原生链接的父子关系（精确），退回书签推断。"""
        _, parent_of = self._alt_links()
        if pno in parent_of:
            return parent_of[pno]
        self._ensure_hier_index()
        for _nm, par, _inst, first, last in self._hier_index:
            if first <= pno <= last:
                return par
        return None

    def _hier_jump(self, pno):
        """跳转到指定页（层级导航用）。"""
        if not self.doc or not (0 <= pno < self.doc.page_count):
            return
        self.page_no = pno
        self.load_page()

