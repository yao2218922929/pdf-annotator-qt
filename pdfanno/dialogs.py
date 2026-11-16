# -*- coding: utf-8 -*-
"""设置对话框：笔记设置、快捷键设置。
由原单文件抽出，主文件通过门面重新导出。"""
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout,
                               QKeySequenceEdit, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from .config import SHORTCUT_ACTIONS, notes_cfg_load


class NoteSettingsDialog(QDialog):
    """笔记设置：存储目录、图片目录、默认导出格式。
    改目录后可一键迁移旧数据到新位置。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("笔记设置")
        self.setMinimumWidth(520)
        self.cfg = notes_cfg_load()

        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)

        # 存储目录
        row1 = QHBoxLayout()
        self.dir_edit = QLineEdit(self.cfg.get("storage_dir", ""))
        self.dir_edit.setPlaceholderText("空=用户目录下（默认）")
        btn_pick = QPushButton("浏览…")
        btn_pick.clicked.connect(self._pick_dir)
        row1.addWidget(self.dir_edit, 1)
        row1.addWidget(btn_pick)
        form.addRow("笔记数据存储目录", row1)

        # 图片目录
        row2 = QHBoxLayout()
        self.img_edit = QLineEdit(self.cfg.get("img_dir", ""))
        self.img_edit.setPlaceholderText("空=用户目录下（默认）")
        btn_pick2 = QPushButton("浏览…")
        btn_pick2.clicked.connect(self._pick_img)
        row2.addWidget(self.img_edit, 1)
        row2.addWidget(btn_pick2)
        form.addRow("笔记图片存储目录", row2)

        # 默认导出格式
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItem("HTML（保留图片/格式）", "html")
        self.fmt_combo.addItem("Markdown（纯文本）", "md")
        idx = self.fmt_combo.findData(self.cfg.get("export_fmt", "html"))
        self.fmt_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("默认导出格式", self.fmt_combo)

        lay.addLayout(form)

        tip = QLabel(
            "• 存储目录：保存 .pdf_anno_notes.json 的位置，方便备份或同步。\n"
            "• 图片目录：保存笔记里粘贴/插入的图片，便于单独管理。\n"
            "• 改目录后，可选择把现有笔记和图片一起迁到新位置。\n"
            "• 导出格式：HTML 保留图片和富文本格式；Markdown 只导出文字。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#666; font-size:12px;")
        lay.addWidget(tip)

        self.chk_migrate = QCheckBox("保存后把现有笔记和图片迁移到新位置")
        self.chk_migrate.setChecked(True)
        lay.addWidget(self.chk_migrate)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择笔记存储目录",
                                             self.dir_edit.text() or "")
        if d:
            self.dir_edit.setText(d)

    def _pick_img(self):
        d = QFileDialog.getExistingDirectory(self, "选择笔记图片目录",
                                             self.img_edit.text() or "")
        if d:
            self.img_edit.setText(d)

    def result_cfg(self):
        return {"storage_dir": self.dir_edit.text().strip(),
                "img_dir": self.img_edit.text().strip(),
                "export_fmt": self.fmt_combo.currentData()}


class ShortcutDialog(QDialog):
    """快捷键设置对话框：为每个动作指定按键，支持恢复默认。"""

    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.setWindowTitle("快捷键设置")
        self.resize(460, 560)
        self.setMinimumSize(380, 320)
        self.cfg = dict(cfg)
        self.edits = {}

        lay = QVBoxLayout(self)
        tip = QLabel("点击右侧输入框后按下想用的组合键即可修改。")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # 表单项放进滚动区：条目多、窗口小（或缩小时）可滚动查看，不裁切
        form = QFormLayout()
        for key, (name, default) in SHORTCUT_ACTIONS:
            ed = QKeySequenceEdit(QKeySequence(self.cfg.get(key, default)))
            ed.setClearButtonEnabled(True)
            self.edits[key] = ed
            form.addRow(name, ed)
        wrap = QWidget()
        wrap.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(wrap)
        scroll.setFrameShape(QFrame.NoFrame)
        lay.addWidget(scroll, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel
                                | QDialogButtonBox.RestoreDefaults)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.RestoreDefaults).clicked.connect(
            self.restore_defaults)
        lay.addWidget(btns)

    def restore_defaults(self):
        for key, (_name, default) in SHORTCUT_ACTIONS:
            self.edits[key].setKeySequence(QKeySequence(default))

    def result_cfg(self):
        cfg = {}
        for key, ed in self.edits.items():
            cfg[key] = ed.keySequence().toString(QKeySequence.PortableText)
        return cfg
