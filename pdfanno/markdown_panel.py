# -*- coding: utf-8 -*-
"""Markdown 阅读：HTML 转换、文件读取，以及渲染面板 MdPanel。
由原单文件抽出，主文件通过门面重新导出。"""
import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QVBoxLayout,
                               QWidget)

# Markdown 阅读用 WebEngine；缺失时降级为纯文本显示（不阻塞程序启动）
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings
    HAS_WEBENGINE = True
except Exception:
    QWebEngineView = None
    QWebEngineSettings = None
    HAS_WEBENGINE = False


MD_EXTS = (".md", ".markdown")

# 内置样式：排版贴近常见 MD 阅读器；代码块用 Pygments 高亮（其自带配色由 HTML 内联）
MD_CSS = """
:root { color-scheme: light; }
body { margin: 0; background: #ffffff; }
#md-root {
  box-sizing: border-box; max-width: 860px; margin: 0 auto; padding: 28px 34px 60px;
  font-family: "Microsoft YaHei", "Segoe UI", "PingFang SC", sans-serif;
  font-size: 15px; line-height: 1.75; color: #24292f; word-wrap: break-word;
}
#md-root h1, #md-root h2, #md-root h3, #md-root h4 {
  font-weight: 600; line-height: 1.3; margin: 1.5em 0 0.6em;
}
#md-root h1 { font-size: 1.9em; border-bottom: 1px solid #e3e3e3; padding-bottom: .3em; }
#md-root h2 { font-size: 1.5em; border-bottom: 1px solid #ececec; padding-bottom: .25em; }
#md-root h3 { font-size: 1.25em; }
#md-root p, #md-root ul, #md-root ol { margin: 0.7em 0; }
#md-root ul, #md-root ol { padding-left: 1.8em; }
#md-root li { margin: 0.25em 0; }
#md-root a { color: #1e6fd9; text-decoration: none; }
#md-root a:hover { text-decoration: underline; }
#md-root blockquote {
  margin: 0.9em 0; padding: 0.5em 1em; color: #57606a;
  border-left: 4px solid #d0d7de; background: #f6f8fa; border-radius: 0 4px 4px 0;
}
#md-root code {
  font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
  font-size: 0.92em; background: #f0f1f3; padding: 0.15em 0.4em; border-radius: 4px;
}
#md-root pre {
  background: #f6f8fa; border: 1px solid #e3e6ea; border-radius: 6px;
  padding: 12px 14px; overflow-x: auto; line-height: 1.55;
}
#md-root pre code { background: none; padding: 0; font-size: 0.9em; }
#md-root table { border-collapse: collapse; margin: 1em 0; display: block; overflow-x: auto; }
#md-root th, #md-root td { border: 1px solid #d0d7de; padding: 6px 12px; }
#md-root th { background: #f6f8fa; font-weight: 600; }
#md-root tr:nth-child(2n) td { background: #fafbfc; }
#md-root img { max-width: 100%; }
#md-root hr { border: none; border-top: 1px solid #e3e3e3; margin: 1.6em 0; }
#md-root .md-missing-img {
  display: inline-block; padding: 2px 8px; color: #a33; background: #fff4f4;
  border: 1px dashed #e0a0a0; border-radius: 4px; font-size: .9em;
}
"""


def _md_exts_ok(path):
    return path.lower().endswith(MD_EXTS)


def md_to_html(text):
    """把 Markdown 文本转成完整 HTML（含样式与代码高亮）。
    公式/流程图暂按普通文本处理（后续可接入本地 KaTeX/Mermaid）。"""
    import markdown as _md
    try:
        body = _md.markdown(
            text,
            extensions=["extra", "tables", "fenced_code", "codehilite",
                        "toc", "sane_lists", "nl2br"],
            extension_configs={
                # noclasses=True：高亮配色以内联样式写出，无需再引外部 CSS
                "codehilite": {"guess_lang": False, "noclasses": True,
                               "pygments_style": "default"},
            },
        )
    except Exception as e:
        body = ("<p style='color:#a33'>Markdown 解析失败：" + str(e) + "</p>"
                "<pre>" + text.replace("&", "&amp;").replace("<", "&lt;") + "</pre>")
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<style>" + MD_CSS + "</style></head>"
            "<body><div id='md-root'>" + body + "</div></body></html>")


def md_read_text(path):
    """读取 MD 文件文本，自动尝试常见编码（UTF-8 / GBK）。"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")




class MdPanel(QWidget):
    """Markdown 阅读面板（放在中间画布区，与 PDF 画布用堆栈切换）。
    用 QWebEngineView 渲染 HTML；缺失 WebEngine 时降级为纯文本只读显示。
    提供缩放 / 搜索定位 / 复制全文 / 用系统程序打开。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = ""
        self._zoom = 1.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        bar.setSpacing(6)
        self.btn_back_pdf = QPushButton("◀ 返回PDF")
        self.btn_back_pdf.setToolTip("回到 PDF 画布（若已打开 PDF）")
        bar.addWidget(self.btn_back_pdf)
        self.title = QLabel("（未打开 Markdown）")
        self.title.setStyleSheet("color:#555;")
        bar.addWidget(self.title, 1)
        self.btn_zoom_out = QPushButton("－")
        self.btn_zoom_out.setToolTip("缩小 (Ctrl+-)")
        self.btn_zoom_in = QPushButton("＋")
        self.btn_zoom_in.setToolTip("放大 (Ctrl++)")
        self.btn_zoom_reset = QPushButton("100%")
        self.btn_zoom_reset.setToolTip("重置缩放 (Ctrl+0)")
        for b in (self.btn_zoom_out, self.btn_zoom_reset, self.btn_zoom_in):
            b.setFixedWidth(56)
            bar.addWidget(b)
        self.btn_copy_all = QPushButton("复制全文")
        self.btn_copy_all.setToolTip("把 Markdown 原文复制到剪贴板")
        bar.addWidget(self.btn_copy_all)
        self.btn_open_ext = QPushButton("外部打开")
        self.btn_open_ext.setToolTip("用系统默认程序打开该 Markdown 文件")
        bar.addWidget(self.btn_open_ext)
        self.btn_close_md = QPushButton("✕")
        self.btn_close_md.setToolTip("关闭当前 Markdown（返回 PDF）")
        self.btn_close_md.setFixedWidth(34)
        self.btn_close_md.setStyleSheet(
            "QPushButton{border:none; color:#888; font-weight:bold;}"
            "QPushButton:hover{background:#e53935; color:white; border-radius:3px;}")
        bar.addWidget(self.btn_close_md)
        lay.addLayout(bar)

        self.search_row = QHBoxLayout()
        self.search_row.setContentsMargins(8, 0, 8, 4)
        self.search_row.setSpacing(6)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("在文档内查找…（回车定位）")
        self.search_row.addWidget(self.search_input, 1)
        lay.addLayout(self.search_row)

        # 渲染视图惰性创建：QWebEngineView 会拉起 Chromium 渲染进程（实测
        # +140MB 内存），而导入模块本身仅 +9MB。故此处只放占位容器，等首次
        # 真正打开 .md 时再建视图 —— 程序启动、仅看 PDF 时完全不加载。
        self.web = None             # QWebEngineView 或 None
        self.text_view = None       # 无 WebEngine 时的降级纯文本视图
        self._view_holder = QWidget(self)
        self._holder_lay = QVBoxLayout(self._view_holder)
        self._holder_lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._view_holder, 1)

    def _ensure_view(self):
        """首次需要渲染时创建视图（有 WebEngine 用网页视图，否则降级纯文本）。"""
        if self.web is not None or self.text_view is not None:
            return
        if HAS_WEBENGINE:
            try:
                self.web = QWebEngineView(self._view_holder)
                s = self.web.settings()
                s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
                s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
                self._holder_lay.addWidget(self.web)
                return
            except Exception:
                self.web = None
        self.text_view = QPlainTextEdit(self._view_holder)
        self.text_view.setReadOnly(True)
        self._holder_lay.addWidget(self.text_view)

    # ---- 内容 ----
    def load_file(self, path):
        """载入并渲染 MD 文件。返回 (成功?, 错误信息)。"""
        self.path = path
        self.title.setText(f"📖 {os.path.basename(path)}")
        try:
            text = md_read_text(path)
        except Exception as e:
            return False, f"读取失败：{e}"
        self._ensure_view()   # 首次打开才真正创建渲染视图
        if self.web is not None:
            html = md_to_html(text)
            base = QUrl.fromLocalFile(os.path.dirname(os.path.abspath(path)) + os.sep)
            self.web.setHtml(html, base)
        else:
            self.text_view.setPlainText(text)
        self._zoom = 1.0
        self.set_zoom(1.0)
        return True, ""

    def apply_zoom(self):
        """把当前缩放比例作用到视图。"""
        if self.web is not None:
            self.web.setZoomFactor(self._zoom)
        elif self.text_view is not None:
            f = self.text_view.font()
            f.setPointSizeF(max(6.0, 11.0 * self._zoom))
            self.text_view.setFont(f)

    def set_zoom(self, z):
        self._zoom = max(0.4, min(4.0, z))
        self.apply_zoom()
        if hasattr(self, "btn_zoom_reset"):
            self.btn_zoom_reset.setText(f"{int(self._zoom * 100)}%")

    def zoom_by(self, d):
        self.set_zoom(self._zoom * (1.25 if d > 0 else 1 / 1.25))

    # ---- 搜索 ----
    def find_text(self, kw):
        """在当前文档中查找并定位（返回是否找到）。"""
        kw = (kw or "").strip()
        if not kw:
            return False
        if self.web is not None:
            self.web.findText(kw)
            return True
        if self.text_view is None:
            return False
        if self.text_view.find(kw):
            return True
        # 从头再来一次
        self.text_view.moveCursor(QTextCursor.Start)
        return bool(self.text_view.find(kw))

    def copy_all_text(self):
        try:
            if self.web is not None or self.text_view is None:
                # 从 HTML 取纯文本较麻烦：直接读回源文件，保证与磁盘一致
                QApplication.clipboard().setText(md_read_text(self.path))
            else:
                QApplication.clipboard().setText(self.text_view.toPlainText())
            return True
        except Exception:
            return False

    def selected_text(self):
        """当前在 MD 里选中的文字（用于翻译）。取不到时由调用方走剪贴板兜底。"""
        if self.web is not None:
            try:
                return (self.web.page().selectedText() or "").strip()
            except Exception:
                return ""
        if self.text_view is None:
            return ""
        return self.text_view.textCursor().selectedText().strip()

    def clear_file(self):
        """关闭当前 Markdown，复位为空白态。"""
        self.path = ""
        self._zoom = 1.0
        self.title.setText("（未打开 Markdown）")
        self.search_input.clear()
        if self.web is not None:
            self.web.setHtml(
                "<body style='background:#fafafa;'></body>",
                QUrl("about:blank"))
        elif self.text_view is not None:
            self.text_view.clear()
        self.btn_zoom_reset.setText("100%")


