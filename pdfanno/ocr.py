# -*- coding: utf-8 -*-
"""扫描版 PDF 的 OCR 文字层。

思路：把每页渲染成图片 → RapidOCR 识别每行文字 → 用 PyMuPDF 以
“不可见文字”(render_mode=3) 写入原坐标，形成可选中/可搜索/可高亮的
文字层。识别在后台线程进行，主界面不卡顿。

依赖 rapidocr + onnxruntime（二者的模型随包自带，离线可用）。
"""
import os

import pymupdf
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from .pdfcore import open_pdf_doc

# 判断“这页已经有文字层”的最小字符数：扫描页可能带页眉/页码等零星文字，
# 阈值太低会把真正的扫描页误判为已有文字。
TEXT_PAGE_MIN_CHARS = 20

# 单行文字的最小置信度
MIN_SCORE = 0.5

# 渲染倍率：72dpi × 2 ≈ 144dpi，兼顾识别率与速度
OCR_ZOOM = 2.0


def _page_has_text(page):
    try:
        return len(page.get_text("text").strip()) >= TEXT_PAGE_MIN_CHARS
    except Exception:
        return False


def _ocr_engine():
    """创建 RapidOCR 引擎（可能较慢，务必在后台线程调用）。"""
    from rapidocr import RapidOCR
    return RapidOCR()


def _ocr_page_lines(engine, page, zoom=OCR_ZOOM):
    """识别一页，返回 [(bbox, text), ...]，坐标已换算成 PDF 点。

    只处理 rotation==0 的页；旋转页返回 None（调用方按跳过处理）。
    """
    if page.rotation:
        return None
    rect = page.rect
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    # 交给 RapidOCR 的 PIL 图（内部会做 RGB→BGR），可兼容 CMYK/灰度扫描件
    img = pix.pil_image().convert("RGB")
    out = engine(img)
    if out is None or getattr(out, "boxes", None) is None:
        return []
    boxes = out.boxes
    txts = out.txts or []
    scores = out.scores if out.scores is not None else []
    lines = []
    for box, txt, score in zip(boxes, txts, scores):
        if not txt or not txt.strip() or float(score) < MIN_SCORE:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        # 图片像素 → PDF 点
        r = pymupdf.Rect(rect.x0 + min(xs) / zoom,
                         rect.y0 + min(ys) / zoom,
                         rect.x0 + max(xs) / zoom,
                         rect.y0 + max(ys) / zoom)
        if r.width < 1 or r.height < 1:
            continue
        lines.append((r, txt.strip()))
    return lines


def _add_text_layer(page, lines, fontname="china-s"):
    """把识别结果写成不可见文字层（不影响原扫描图像）。"""
    n = 0
    for r, txt in lines:
        # 字号按行高估算（china-s 的字形高度约 1.2×字号），再逐步缩小到能
        # 完整装进识别框，避免文字超出扫描行导致选中区域错位。
        fs = max(4.0, r.height * 0.8)
        placed = False
        for _ in range(10):
            try:
                rc = page.insert_textbox(
                    r, txt, fontsize=fs, fontname=fontname,
                    color=(0, 0, 0), render_mode=3)
            except Exception:
                break
            if rc >= 0:
                placed = True
                break
            fs *= 0.9
        if not placed:
            # 极窄/极短的框：直接按基线插入（选择区域略宽，但文字保真）
            try:
                page.insert_text((r.x0, r.y1 - r.height * 0.2), txt,
                                 fontsize=max(4.0, r.height * 0.7),
                                 fontname=fontname, color=(0, 0, 0),
                                 render_mode=3)
                placed = True
            except Exception:
                pass
        if placed:
            n += 1
    return n


class OcrThread(QThread):
    """后台 OCR：识别指定页范围并写出带文字层的新 PDF。"""
    progress = Signal(int, int, str)   # 已完成页数, 总页数, 提示文字
    done = Signal(dict)                # 结果信息（含 canceled）
    failed = Signal(str)

    def __init__(self, src, out, first, last, skip_text=True, parent=None):
        super().__init__(parent)
        self.src = src
        self.out = out
        self.first = first          # 0 基、含
        self.last = last            # 0 基、含
        self.skip_text = skip_text
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        total = max(1, self.last - self.first + 1)
        self.progress.emit(0, total, "正在加载 OCR 引擎…")
        try:
            engine = _ocr_engine()
        except Exception as e:
            self.failed.emit(
                "OCR 引擎初始化失败：%s\n\n"
                "请确认已安装依赖：\n"
                "py -m pip install rapidocr onnxruntime" % e)
            return
        doc = None
        tmp_path = None
        try:
            doc = pymupdf.open(self.src)
            pages_done = lines_done = 0
            skip_text = skip_rot = 0
            canceled = False
            for i, pno in enumerate(range(self.first, self.last + 1), 1):
                if self._stop:
                    canceled = True
                    break
                page = doc[pno]
                self.progress.emit(i - 1, total,
                                   "正在识别第 %d/%d 页…" % (i, total))
                if page.rotation:
                    skip_rot += 1
                    continue
                if self.skip_text and _page_has_text(page):
                    skip_text += 1
                    continue
                lines = _ocr_page_lines(engine, page)
                if lines:
                    lines_done += _add_text_layer(page, lines)
                pages_done += 1
            if canceled:
                self.done.emit({"canceled": True})
                return
            self.progress.emit(total, total, "正在写入 PDF…")
            # PyMuPDF 不允许直接覆盖正在读取的源文件：先写临时文件再替换
            same = os.path.abspath(self.out) == os.path.abspath(self.src)
            target = self.out + ".ocrtmp" if same else self.out
            tmp_path = target if same else None
            doc.save(target, deflate=True, garbage=3)
            if same:
                # Windows 下源文件只要被占用（例如仍打开在工具里）就无法
                # 就地 os.replace；把临时文件交给主线程，等关闭文档句柄
                # 后再替换并重开（见 OcrMixin._apply_ocr_inplace）
                tmp_path = None   # 所有权移交主线程，finally 不再清理
            self.done.emit({
                "canceled": False, "out": self.out, "same": same,
                "tmp": target if same else None,
                "pages": pages_done, "lines": lines_done,
                "skip_text": skip_text, "skip_rot": skip_rot,
            })
        except Exception as e:
            self.failed.emit("OCR 失败：%s" % e)
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)   # 失败时清理半成品临时文件
                except Exception:
                    pass


class OcrDialog(QDialog):
    """选择 OCR 范围与输出位置。"""

    def __init__(self, parent, pdf_path, current_page):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.setWindowTitle("OCR 文字层识别")
        self.setMinimumWidth(560)

        try:
            _d = pymupdf.open(pdf_path)
            page_count = _d.page_count
            _d.close()   # 不能持有句柄：否则之后覆盖保存同一路径会失败
        except Exception:
            page_count = 1

        lay = QVBoxLayout(self)
        info = QLabel(
            "为扫描版 PDF 添加可选中、可搜索、可高亮的文字层。\n"
            "原扫描图像不变，识别结果写入新的 PDF 文件。")
        info.setWordWrap(True)
        info.setStyleSheet("color:#555;")
        lay.addWidget(info)

        form = QFormLayout()
        form.addRow("文件", QLabel(os.path.basename(pdf_path)))
        form.addRow("页数", QLabel(str(page_count)))

        self.range_combo = QComboBox()
        self.range_combo.addItem("全部页", "all")
        self.range_combo.addItem("仅当前页", "cur")
        self.range_combo.addItem("自定义范围", "range")
        self.range_combo.setCurrentIndex(0)
        form.addRow("识别范围", self.range_combo)

        row = QHBoxLayout()
        self.spin_from = QSpinBox()
        self.spin_to = QSpinBox()
        for s in (self.spin_from, self.spin_to):
            s.setRange(1, page_count)
        self.spin_from.setValue(1)
        self.spin_to.setValue(page_count)
        self.spin_cur = max(1, min(current_page + 1, page_count))
        row.addWidget(QLabel("从"))
        row.addWidget(self.spin_from)
        row.addWidget(QLabel("到"))
        row.addWidget(self.spin_to)
        form.addRow("页码范围", row)
        self.range_combo.currentIndexChanged.connect(self._sync_range)
        self._sync_range()
        self._page_count = page_count

        out_row = QHBoxLayout()
        base, ext = os.path.splitext(pdf_path)
        self.out_edit = QLineEdit(base + "_OCR" + (ext or ".pdf"))
        btn = QPushButton("浏览…")
        btn.clicked.connect(self._pick_out)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(btn)
        form.addRow("输出文件", out_row)
        lay.addLayout(form)

        self.chk_skip = QCheckBox("跳过已有文字层的页（避免重复识别）")
        self.chk_skip.setChecked(True)
        lay.addWidget(self.chk_skip)
        self.chk_open = QCheckBox("完成后自动切换到输出文件（会离开当前阅读页）")
        self.chk_open.setChecked(False)
        lay.addWidget(self.chk_open)

        tip = QLabel("说明：中文与英文自动识别；识别在后台进行，期间可继续阅读、\n"
                     "翻页与批注，完成后在状态栏通知。速度约每页数秒，可随时取消。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#777; font-size:12px;")
        lay.addWidget(tip)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("开始识别")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _sync_range(self):
        custom = self.range_combo.currentData() == "range"
        self.spin_from.setEnabled(custom)
        self.spin_to.setEnabled(custom)

    def _pick_out(self):
        f, _ = QFileDialog.getSaveFileName(
            self, "保存 OCR 结果", self.out_edit.text(), "PDF 文件 (*.pdf)")
        if f:
            if not f.lower().endswith(".pdf"):
                f += ".pdf"
            self.out_edit.setText(f)

    def config(self):
        kind = self.range_combo.currentData()
        if kind == "all":
            first, last = 0, self._page_count - 1
        elif kind == "cur":
            first = last = self.spin_cur - 1
        else:
            a, b = self.spin_from.value(), self.spin_to.value()
            first, last = min(a, b) - 1, max(a, b) - 1
        return {
            "first": first, "last": last,
            "skip_text": self.chk_skip.isChecked(),
            "open_after": self.chk_open.isChecked(),
            "out": os.path.abspath(self.out_edit.text().strip()),
        }


class OcrMixin:
    """MainWindow 的 OCR 入口与后台进度管理。

    识别全程非阻塞：进度显示在状态栏右侧常驻小部件（含取消按钮），
    不弹模态框，阅读/翻页/批注不受影响。
    """

    def ocr_pdf(self):
        if not getattr(self, "doc", None) or not getattr(self, "pdf_path", ""):
            QMessageBox.information(self, "OCR", "请先打开一个 PDF 文件。")
            return
        if getattr(self, "_ocr_thread", None) is not None:
            QMessageBox.information(self, "OCR", "已有 OCR 任务正在进行。")
            return
        dlg = OcrDialog(self, self.pdf_path, self.page_no)
        if dlg.exec() != QDialog.Accepted:
            return
        cfg = dlg.config()
        out = cfg["out"]
        if not out:
            QMessageBox.warning(self, "OCR", "请填写输出文件路径。")
            return
        if os.path.abspath(out) == os.path.abspath(self.pdf_path):
            if QMessageBox.question(
                    self, "OCR", "输出文件与当前 PDF 相同，确定要覆盖原文件吗？"
                    ) != QMessageBox.Yes:
                return
        elif os.path.exists(out):
            if QMessageBox.question(
                    self, "OCR", "输出文件已存在，确定覆盖吗？\n%s" % out
                    ) != QMessageBox.Yes:
                return

        thr = OcrThread(self.pdf_path, out, cfg["first"], cfg["last"],
                        cfg["skip_text"], self)
        self._ocr_thread = thr
        self._ocr_open_after = cfg["open_after"]
        self._ocr_cancel_requested = False
        self._make_ocr_status_chip()

        thr.progress.connect(self._on_ocr_progress)
        thr.done.connect(self._on_ocr_done)
        thr.failed.connect(self._on_ocr_failed)
        thr.finished.connect(self._on_ocr_thread_finished)
        thr.start()
        self.statusBar().showMessage("OCR 已在后台开始，期间可继续阅读", 4000)

    # ---- 状态栏常驻进度小部件（非模态） ----

    def _make_ocr_status_chip(self):
        """状态栏右侧常驻小部件：进度文字 + 取消按钮。"""
        self._remove_ocr_status_chip()
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 0, 4, 0)
        h.setSpacing(6)
        lbl = QLabel("OCR 正在后台识别…")
        lbl.setStyleSheet("color:#B45309;")
        btn = QPushButton("取消")
        btn.setFixedHeight(22)
        btn.setToolTip("停止后台识别（已完成页不会写入文件）")
        btn.clicked.connect(self._cancel_ocr)
        h.addWidget(lbl)
        h.addWidget(btn)
        self.statusBar().addPermanentWidget(w)
        self._ocr_status_chip = w
        self._ocr_status_label = lbl
        self._ocr_cancel_btn = btn

    def _remove_ocr_status_chip(self):
        w = getattr(self, "_ocr_status_chip", None)
        if w is not None:
            try:
                self.statusBar().removeWidget(w)
                w.deleteLater()
            except Exception:
                pass
        self._ocr_status_chip = None
        self._ocr_status_label = None
        self._ocr_cancel_btn = None

    def _on_ocr_progress(self, done_pages, total, msg):
        lbl = getattr(self, "_ocr_status_label", None)
        if lbl is None or getattr(self, "_ocr_cancel_requested", False):
            return   # 取消中保持"正在停止"文案
        lbl.setText("OCR 后台识别：%s" % msg)

    def _cancel_ocr(self):
        thr = getattr(self, "_ocr_thread", None)
        if thr is None:
            return
        self._ocr_cancel_requested = True
        thr.stop()
        lbl = getattr(self, "_ocr_status_label", None)
        if lbl is not None:
            lbl.setText("OCR 正在停止…（等待当前页完成）")
        btn = getattr(self, "_ocr_cancel_btn", None)
        if btn is not None:
            btn.setEnabled(False)

    # ---- 结果处理（全部非模态） ----

    def _on_ocr_done(self, info):
        self._remove_ocr_status_chip()
        if info.get("canceled"):
            self.statusBar().showMessage("OCR 已取消", 4000)
            return
        stats = "识别 %d 页，写入 %d 行文字" % (
            info.get("pages", 0), info.get("lines", 0))
        skipped = []
        if info.get("skip_text"):
            skipped.append("%d 页已有文字" % info["skip_text"])
        if info.get("skip_rot"):
            skipped.append("%d 页页面旋转被跳过" % info["skip_rot"])
        if skipped:
            stats += "（跳过：%s）" % "，".join(skipped)
        if info.get("same"):
            # 覆盖源文件：主线程负责关闭句柄→替换→重开，不打断阅读位置
            if self._apply_ocr_inplace(info):
                self.statusBar().showMessage(
                    "OCR 完成：%s，文字层已生效，可直接选中/搜索" % stats, 10000)
            return
        out = info.get("out", "")
        if getattr(self, "_ocr_open_after", False) and out:
            self.load_pdf_path(out)
            self.statusBar().showMessage("OCR 完成：%s，输出 %s" % (stats, out))
        else:
            self.statusBar().showMessage(
                "OCR 完成：%s，输出 %s" % (stats, out), 10000)

    def _apply_ocr_inplace(self, info):
        """就地覆盖源文件：关闭该文档句柄 → 原子替换 → 重开并恢复阅读位置。

        Windows 下源文件被占用时无法直接 os.replace（WinError 5），
        故线程只写临时文件，替换动作放到这里（主线程）完成。
        返回 True=已应用；False=失败（已另行提示）。
        """
        path = info["out"]
        tmp = info["tmp"]
        was_current = (self.pdf_path == path and self.doc is not None)
        if was_current:
            # 以最新的页号/缩放为准（正在读的位置替换后原样恢复）
            self._store_current_session()
        sess = self.sessions.get(path)
        if sess:
            doc = sess.get("doc")
            try:
                if doc is not None:
                    doc.close()
            except Exception:
                pass
            sess["doc"] = None
            if was_current:
                self.doc = None
        try:
            os.replace(tmp, path)
        except Exception as e:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            self._on_ocr_failed("写入输出文件失败：%s" % e)
            return False
        if not sess:
            return True   # 识别期间该文件已被关闭：仅替换文件即可
        newdoc, newpw = open_pdf_doc(path, self)
        if newdoc is None:
            # 重开被取消（如加密文档）：该文档保持关闭状态，从会话中移除
            self._on_ocr_failed("OCR 已完成，但重新打开《%s》失败。"
                                % os.path.basename(path))
            self._do_close_pdf_path(path)
            return False
        sess["doc"] = newdoc
        sess["pdf_password"] = newpw
        if was_current:
            # 恢复当前视图：页号/缩放/批注/书签全部原样保留
            self.doc = newdoc
            self.pdf_password = newpw
            self.page_no = min(max(0, self.page_no), newdoc.page_count - 1)
            self._hier_index = None   # 文档对象已更换：派生缓存作废
            self._alt_ready = False
            self._page_pix_cache.clear()
            self._words_cache.clear()
            self._clear_doc_geometry()
            self.page_spin.setRange(1, newdoc.page_count)
            self.clear_selection()
            self.load_page()
            self.sessions[path] = self._capture_session()
        return True

    def _on_ocr_failed(self, msg):
        self._remove_ocr_status_chip()
        # 非模态提示：不阻塞阅读，用户看完随手关掉即可
        old = getattr(self, "_ocr_fail_box", None)
        if old is not None:
            try:
                old.close()
                old.deleteLater()
            except Exception:
                pass
        box = QMessageBox(QMessageBox.Critical, "OCR 失败", msg,
                          QMessageBox.Ok, self)
        box.show()
        self._ocr_fail_box = box

    def _on_ocr_thread_finished(self):
        # 线程真正结束后再回收对象，避免 QThread 运行中被销毁
        thr = getattr(self, "_ocr_thread", None)
        if thr is not None:
            thr.deleteLater()
            self._ocr_thread = None
        # 安全网：确保状态栏小部件一定被移除
        self._remove_ocr_status_chip()
