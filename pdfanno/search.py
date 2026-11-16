# -*- coding: utf-8 -*-
"""后台多文件关键词 / 书签搜索线程。"""
import pymupdf
from PySide6.QtCore import QThread, Signal


class SearchThread(QThread):
    """后台在多个PDF文件中搜索关键词（含书签/TOC搜索）。"""
    found = Signal(dict)       # 每条匹配
    finished_all = Signal(int)  # 匹配总数

    def __init__(self, files, keyword, whole, case_sensitive, search_toc=False):
        super().__init__()
        self.files = files
        self.keyword = keyword
        self.whole = whole
        self.case_sensitive = case_sensitive
        self.search_toc = search_toc
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        count = 0
        for f in self.files:
            if self._stop:
                break
            try:
                d = pymupdf.open(f)
            except Exception:
                continue
            if d.needs_pass:
                d.close()      # 加密文档无法无密码检索，跳过
                continue
            try:
                # 书签/TOC 搜索
                if self.search_toc:
                    for _lvl, title, pno in d.get_toc(simple=True):
                        if self._stop:
                            break
                        if pno < 1 or not self._text_match(title):
                            continue
                        self.found.emit({
                            "file": f, "page": pno - 1, "rect": None,
                            "kind": "toc", "snippet": title.strip()})
                        count += 1
                for pno in range(d.page_count):
                    if self._stop:
                        break
                    if self.whole:
                        hits = self._match_words(d[pno], self.keyword,
                                                 self.case_sensitive)
                    else:
                        hits = self._match_substring(d[pno], self.keyword,
                                                     self.case_sensitive)
                    for r in hits:
                        if self._stop:
                            break
                        self.found.emit({
                            "file": f, "page": pno,
                            "rect": (r.x0, r.y0, r.x1, r.y1),
                            "kind": "text",
                            "snippet": self._snippet(d[pno], r)})
                        count += 1
            finally:
                d.close()
        self.finished_all.emit(count)

    def _text_match(self, text):
        """书签标题匹配（支持整词/大小写选项）。"""
        import re as _re
        t = text if self.case_sensitive else text.lower()
        kw = self.keyword if self.case_sensitive else self.keyword.lower()
        if not kw:
            return False
        if self.whole:
            return _re.search(_re.escape(kw).join((r"\b", r"\b")), t) is not None
        return kw in t

    @staticmethod
    def _snippet(page, r, limit=60):
        """以命中词为中心的同行文字摘要。
        CAD 导出 PDF 的词框相互重叠，get_text(clip=整行) 会输出重复乱序
        文字；改用 words 坐标：同行词按 x 排序 + 重叠去重，只取命中词
        前后各 3 个词，命中词用【】标出。"""
        try:
            words = [w for w in page.get_text("words")
                     if w[1] < r.y1 and w[3] > r.y0]   # 与命中同一行
            if not words:
                return ""
            words.sort(key=lambda w: w[0])
            # 去掉互相重叠的重复词（CAD 词框重叠导致同一词出现多次）
            uniq = []
            for w in words:
                dup = False
                for p in uniq:
                    ox = min(p[2], w[2]) - max(p[0], w[0])
                    oy = min(p[3], w[3]) - max(p[1], w[1])
                    if ox > 0.5 * min(p[2] - p[0], w[2] - w[0]) and \
                            oy > 0.5 * min(p[3] - p[1], w[3] - w[1]):
                        dup = True
                        break
                if not dup:
                    uniq.append(w)
            # 命中词 = 与命中矩形重叠面积最大的词
            best, best_a = 0, -1.0
            for i, w in enumerate(uniq):
                a = (max(0, min(w[2], r.x1) - max(w[0], r.x0)) *
                     max(0, min(w[3], r.y1) - max(w[1], r.y0)))
                if a > best_a:
                    best_a, best = a, i
            lo, hi = max(0, best - 3), min(len(uniq), best + 4)
            parts = [uniq[i][4] for i in range(lo, hi)]
            parts[best - lo] = f"【{parts[best - lo]}】"
            s = " ".join(parts)
            return s if len(s) <= limit else s[:limit - 1] + "…"
        except Exception:
            return ""

    @staticmethod
    def _match_words(page, keyword, case_sensitive):
        """整词匹配：用 words 坐标，返回 Rect 列表。"""
        kw = keyword if case_sensitive else keyword.lower()
        out = []
        for w in page.get_text("words"):
            word = w[4]
            cmp = word if case_sensitive else word.lower()
            if cmp == kw:
                out.append(pymupdf.Rect(w[:4]))
        return out

    @staticmethod
    def _match_substring(page, keyword, case_sensitive):
        """子串匹配：search_for 默认忽略大小写；要求区分大小写时二次校验。
        支持跨词词组：按行合并词，用字符串查找定位坐标。"""
        if not case_sensitive:
            return page.search_for(keyword, quads=False)
        # 区分大小写：用 rawdict 逐行拼接精确定位
        kw = keyword
        hits = []
        d = page.get_text("rawdict")
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                # 拼接整行文字并记录每字符 bbox
                line_txt = ""
                char_boxes = []  # (char, bbox)
                for span in spans:
                    for ch in span.get("chars", []):
                        c = ch.get("c", "")
                        if c:
                            line_txt += c
                            b = ch.get("bbox")
                            char_boxes.append((c, b))
                start = 0
                while True:
                    i = line_txt.find(kw, start)
                    if i < 0:
                        break
                    seg = char_boxes[i:i + len(kw)]
                    seg = [s for s in seg if s[1]]
                    if seg:
                        x0 = min(s[1][0] for s in seg)
                        y0 = min(s[1][1] for s in seg)
                        x1 = max(s[1][2] for s in seg)
                        y1 = max(s[1][3] for s in seg)
                        hits.append(pymupdf.Rect(x0, y0, x1, y1))
                    start = i + len(kw)
        return hits

