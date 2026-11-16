# -*- coding: utf-8 -*-
"""文本/颜色小工具函数。由原单文件抽出，主文件通过门面重新导出。"""
from PySide6.QtGui import QColor


def hex_rgb(h):
    c = QColor(h)
    return (c.red(), c.green(), c.blue())


def join_words_text(words):
    """把同一行的一组词按阅读顺序拼成文本（词间空格），存入文字高亮批注，
    供批注列表显示"具体选中的文字内容"。words 为 pymupdf 词元组(含 x0..x1 与文本)。"""
    ws = sorted(words, key=lambda w: (w[5], w[6], w[7]))
    return " ".join(w[4] for w in ws)
