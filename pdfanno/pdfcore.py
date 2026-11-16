# -*- coding: utf-8 -*-
"""PDF 批注核心：打开文档（含加密）+ 原生注释的幂等读写。
由原单文件抽出，主文件通过门面重新导出。

设计要点：
- 批注以 PDF 原生注释(annotation)写入，用 subject=ANNO_SUBJECT 标记归属；
  保存前先删除本工具旧注释再重写 → 幂等，重复保存不叠加。
- 无损原始数据以 JSON 内嵌附件保存，读回时优先用它完整还原；
  附件丢失时按原生注释几何近似还原。
"""
import json
import os

import pymupdf
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from .constants import ANNO_DATA_NAME, ANNO_SUBJECT, HL_W, TEXT_FS


def open_pdf_doc(path, parent=None):
    """打开 PDF 并处理加密文档：需要密码时弹框索要（最多试 3 次）。
    返回 (doc, password)；失败或用户取消返回 (None, None)。
    非加密文档 password 为空串。"""
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        if parent is not None:
            QMessageBox.critical(parent, "出错", f"打开失败: {e}")
        return None, None
    if not doc.needs_pass:
        return doc, ""
    name = os.path.basename(path)
    for _ in range(3):
        pw, ok = QInputDialog.getText(
            parent, "需要密码",
            f"《{name}》是加密文档，请输入打开密码：",
            QLineEdit.Password)
        if not ok:
            doc.close()
            return None, None
        # authenticate: 0=失败，2=用户密码，4/6=所有者密码
        if doc.authenticate(pw):
            return doc, pw
        QMessageBox.warning(parent, "密码错误", "密码不正确，请重试。")
    doc.close()
    if parent is not None:
        QMessageBox.critical(parent, "打开失败", "密码错误次数过多，已取消打开。")
    return None, None


def _strip_pdfanno_annots(doc):
    """删除文档中所有由本工具写入的原生注释（按 subject 标记识别）。
    保存前先清除旧注释，使重复保存幂等、不会叠加。"""
    for pno in range(doc.page_count):
        try:
            page = doc[pno]
        except Exception:
            continue
        # 先收集目标 xref：删除后旧 Annot 对象会失效，须每次按 xref 重新取
        xrefs = [a.xref for a in page.annots()
                 if (a.info.get("subject") or "") == ANNO_SUBJECT]
        for x in xrefs:
            for a in page.annots():
                if a.xref == x:
                    page.delete_annot(a)
                    break


def _write_one_anno(page, a):
    """把一条批注写成 PDF 原生注释（用于在其他阅读器中可见、且可幂等重写）。
    仅打上 subject 归属标记；无损的原始数据另存内嵌附件（见 save_annotations）。"""
    rgb = tuple(c / 255 for c in a["rgb"])
    t = a["type"]
    if t == "rect":
        an = page.add_rect_annot(pymupdf.Rect(a["p1"], a["p2"]))
        an.set_colors(stroke=rgb)
        an.set_border(width=max(0.5, a["wpt"]))
    elif t == "oval":
        an = page.add_circle_annot(pymupdf.Rect(a["p1"], a["p2"]))
        an.set_colors(stroke=rgb)
        an.set_border(width=max(0.5, a["wpt"]))
    elif t == "line":
        an = page.add_line_annot(pymupdf.Point(*a["p1"]), pymupdf.Point(*a["p2"]))
        an.set_colors(stroke=rgb)
        an.set_border(width=max(0.5, a["wpt"]))
    elif t == "arrow":
        an = page.add_line_annot(pymupdf.Point(*a["p1"]), pymupdf.Point(*a["p2"]))
        an.set_colors(stroke=rgb)
        an.set_border(width=max(0.5, a["wpt"]))
        try:   # 箭头在末端：起点无、终点实心箭头
            an.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE,
                             pymupdf.PDF_ANNOT_LE_CLOSED_ARROW)
        except Exception:
            pass
    elif t == "pen":
        pts = [(x, y) for x, y in a["pts"]]
        if len(pts) < 2:
            return
        an = page.add_ink_annot([pts])
        an.set_colors(stroke=rgb)
        an.set_border(width=max(0.5, a["wpt"]))
    elif t == "highlight":
        pts = [(x, y) for x, y in a["pts"]]
        if len(pts) < 2:
            return
        an = page.add_ink_annot([pts])
        an.set_colors(stroke=rgb)
        an.set_border(width=HL_W)
        an.set_opacity(0.35)
    elif t == "texthl":
        x0, y0, x1, y1 = a["rect"]
        an = page.add_rect_annot(pymupdf.Rect(x0, y0, x1, y1))
        an.set_colors(stroke=None, fill=rgb)
        an.set_border(width=0)
        an.set_opacity(0.4)
    elif t == "text":
        # 文字：用 FreeText 注释（可移动/编辑，且可幂等清除）。
        # 注意 FreeText 的 content 就是显示文本，绝不能覆写成 JSON。
        # 用 get_text_length 精确量宽（中英文混排都准），否则 CJK 会被裁掉。
        px, py = a["p"]
        fs = float(a.get("fs", TEXT_FS))
        txt = a.get("text") or ""
        lines_ = (txt.split("\n") or [""])
        try:
            wmax = max(pymupdf.get_text_length(ln, fontname="china-s",
                                               fontsize=fs)
                       for ln in lines_)
        except Exception:
            wmax = max([len(ln) for ln in lines_] or [1]) * fs * 0.62
        w = max(24.0, wmax + 8)
        h = max(fs * 1.4, len(lines_) * fs * 1.35 + 6)
        an = page.add_freetext_annot(
            pymupdf.Rect(px - 1, py - 1, px + w, py + h),
            txt, fontsize=fs, fontname="china-s",
            text_color=rgb, border_width=0)
    else:
        return
    try:
        an.set_info(subject=ANNO_SUBJECT, title="PDF批注工具")
    except Exception:
        pass
    try:
        an.update()
    except Exception:
        pass


def save_annotations(pdf_path, annos, out_path, toc=None, deleted_pages=None,
                     password=None):
    """把批注写入PDF。annos: {页码(0基): [anno,...]}，坐标为PDF点。
    toc 不为 None 时同时把书签目录写回。
    deleted_pages: 需删除的"原始页索引"(0基)集合；按倒序删除，
    使删后新文档的页序与界面中当前页索引一致，批注/书签页码可直接套用。
    password: 源文件为加密 PDF 时的打开密码。

    批注以 PDF 原生注释写入：保存前先删除旧的本工具注释再重写，故
    重复保存（含覆盖原文件）幂等、不会叠加；其他阅读器可正常查看这些注释。"""
    doc = pymupdf.open(pdf_path)
    if password and doc.needs_pass:
        doc.authenticate(password)
    if deleted_pages:
        for pno in sorted(deleted_pages, reverse=True):
            if 0 <= pno < doc.page_count:
                doc.delete_page(pno)
    _strip_pdfanno_annots(doc)   # 幂等：先清除旧的批注注释
    for pno, lst in annos.items():
        if not lst or pno >= doc.page_count:
            continue
        page = doc[pno]
        for a in lst:
            if a:
                try:
                    _write_one_anno(page, a)
                except Exception:
                    pass   # 单条失败不影响其余
    if toc is not None:
        doc.set_toc([[int(l), str(t), int(p)] for l, t, p in toc])
    # 无损数据：批注原始结构以 JSON 内嵌附件保存。原生注释负责"看得见/可被
    # 其他阅读器识别且能幂等清除"，附件负责"原样还原成可编辑批注"。
    try:
        payload = json.dumps({"annos": annos}, ensure_ascii=False).encode("utf-8")
        if ANNO_DATA_NAME in doc.embfile_names():
            doc.embfile_del(ANNO_DATA_NAME)
        doc.embfile_add(ANNO_DATA_NAME, payload, filename=ANNO_DATA_NAME,
                        desc="PDF批注工具的可编辑批注数据")
    except Exception:
        pass   # 附件失败不影响注释本身的保存
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()


def load_embedded_annos(doc):
    """从 PDF 读回本工具的批注，返回 {页码(0基): [anno,...]}。
    优先读内嵌 JSON 附件（无损）；缺失时按原生注释几何近似还原。
    没有本工具批注（外部 PDF / 旧文件）时返回 {}。"""
    # 1) 优先：内嵌 JSON（无损，能完整还原类型/坐标/颜色/文字）
    try:
        if ANNO_DATA_NAME in doc.embfile_names():
            raw = doc.embfile_get(ANNO_DATA_NAME)
            data = json.loads(raw.decode("utf-8"))
            annos = data.get("annos") if isinstance(data, dict) else None
            if isinstance(annos, dict):
                out = {}
                for k, v in annos.items():
                    try:
                        p = int(k)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(v, list) and 0 <= p < doc.page_count:
                        out[p] = v
                if out:
                    return out
    except Exception:
        pass
    # 2) 回退：解析本工具写入的原生注释（如同步过程中附件丢失）
    out = {}
    for pno in range(doc.page_count):
        try:
            page = doc[pno]
            anns = list(page.annots())
        except Exception:
            continue
        for a in anns:
            try:
                if (a.info.get("subject") or "") != ANNO_SUBJECT:
                    continue
                obj = _anno_from_annot(a)
            except Exception:
                obj = None
            if obj:
                out.setdefault(pno, []).append(obj)
    return out


def _anno_from_annot(a):
    """把一条原生注释还原成可编辑批注 dict：优先用 content 里的原始 JSON；
    无 JSON（外部文件）时按注释几何近似还原。"""
    raw = (a.info.get("content") or "").strip()
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and obj.get("type"):
                return obj
        except Exception:
            pass
    # 几何近似还原（应对丢失 content 的情况）
    r = a.rect
    kind = a.type[1] if isinstance(a.type, tuple) else str(a.type)
    stroke = (a.colors or {}).get("stroke") or [0, 0, 0]
    fill = (a.colors or {}).get("fill") or []
    rgb = [int(round(c * 255)) for c in (stroke[:3] or [0, 0, 0])]
    hexc = "#%02x%02x%02x" % tuple(rgb)
    wpt = max(1.0, (a.border or {}).get("width", 1.0) or 1.0)
    if kind == "Square":
        if fill:
            frgb = [int(round(c * 255)) for c in fill[:3]]
            return {"type": "texthl", "rect": [r.x0, r.y0, r.x1, r.y1],
                    "rgb": frgb, "wpt": 1, "color": "#%02x%02x%02x" % tuple(frgb)}
        return {"type": "rect", "p1": [r.x0, r.y0], "p2": [r.x1, r.y1],
                "rgb": rgb, "wpt": wpt, "color": hexc}
    if kind == "Circle":
        return {"type": "oval", "p1": [r.x0, r.y0], "p2": [r.x1, r.y1],
                "rgb": rgb, "wpt": wpt, "color": hexc}
    if kind == "Line":
        try:
            vs = a.vertices
        except Exception:
            vs = None
        if vs and len(vs) >= 2:
            # 末尾带箭头则以 arrow 还原
            le = None
            try:
                le = a.line_ends
            except Exception:
                pass
            p1 = [vs[0][0], vs[0][1]]
            p2 = [vs[-1][0], vs[-1][1]]
            if le and le[1] not in (pymupdf.PDF_ANNOT_LE_NONE, 0):
                return {"type": "arrow", "p1": p1, "p2": p2,
                        "rgb": rgb, "wpt": wpt, "color": hexc}
            return {"type": "line", "p1": p1, "p2": p2,
                    "rgb": rgb, "wpt": wpt, "color": hexc}
    if kind == "Ink":
        pts = []
        try:
            for stroke_pts in a.vertices:
                pts.extend([stroke_pts[0], stroke_pts[1]])
        except Exception:
            pass
        if len(pts) >= 4:
            return {"type": "pen", "pts": pts, "rgb": rgb,
                    "wpt": wpt, "color": hexc}
    if kind == "FreeText":
        try:
            txt = a.info.get("content") or ""
        except Exception:
            txt = ""
        return {"type": "text", "p": [r.x0 + 1, r.y0 + 1], "text": txt,
                "fs": TEXT_FS, "rgb": rgb, "wpt": 1, "color": hexc}
    return None
