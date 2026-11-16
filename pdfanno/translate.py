# -*- coding: utf-8 -*-
"""在线翻译：设置读写、语言检测、Google/百度接口，以及翻译面板/线程/设置对话框。
由原单文件抽出，主文件通过门面重新导出。"""
import json
import os

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from .config import _safe_write_json

# ---------------- 翻译设置 ----------------
TRANSLATE_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_translate.json")

# 语言代码：Google 与 百度 通用/相近，百度用 zh/en/jp/kor/fra 等短码
TRANSLATE_LANGS = [("中文", "zh"), ("英语", "en"), ("日语", "jp"),
                   ("韩语", "kor"), ("德语", "de"), ("法语", "fra"),
                   ("俄语", "ru")]
LANG_ALIAS = {"zh": "zh-CN", "en": "en", "jp": "ja", "kor": "ko",
              "de": "de", "fra": "fr", "ru": "ru"}


def translate_load():
    """读取翻译设置：引擎、目标语言、百度密钥、追加模式。"""
    d = {"engine": "google", "target": "zh", "appid": "", "appkey": "",
         "append": False}
    try:
        with open(TRANSLATE_FILE, "r", encoding="utf-8") as f:
            got = json.load(f)
        if isinstance(got, dict):
            d.update({k: got.get(k, d[k]) for k in d})
    except Exception:
        pass
    return d


def translate_save(cfg):
    _safe_write_json(TRANSLATE_FILE, cfg)


def detect_lang(text):
    """粗略判断文本语言：返回 'zh' 或 'en'。有较多CJK字符即判为中文。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return "zh" if cjk > len(text) * 0.15 else "en"


def _http_get(url, timeout=8):
    """极简 GET（避免引入 requests 依赖）；返回 (状态码, 文本)。"""
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (PDFAnnoQt)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return r.status, raw.decode("utf-8")
    except Exception:
        return r.status, raw.decode("gbk", errors="replace")


def translate_text(text, cfg, src=None):
    """调用在线翻译。返回 (译文, 识别出的源语言)。失败抛异常。
    引擎：google（免Key，translate.googleapis.com）；baidu（需 appid/appkey）。"""
    text = text.strip()
    if not text:
        return "", ""
    src = src or detect_lang(text)
    tgt = cfg.get("target", "zh")
    if src == tgt:
        # 源语言与目标一致时自动反向翻译，避免"中译中"无意义
        tgt = "en" if src == "zh" else "zh"
    engine = cfg.get("engine", "google")
    if engine == "baidu":
        return _translate_baidu(text, cfg, src, tgt)
    return _translate_google(text, cfg, src, tgt)


def _translate_google(text, cfg, src, tgt):
    import urllib.parse
    sl = LANG_ALIAS.get(src, "auto")
    tl = LANG_ALIAS.get(tgt, "zh-CN")
    if sl == "auto":
        sl = "auto"
    q = urllib.parse.quote(text)
    url = ("https://translate.googleapis.com/translate_a/single"
           f"?client=gtx&sl={sl}&tl={tl}&dt=t&q={q}")
    _code, body = _http_get(url)
    data = json.loads(body)
    out = "".join(seg[0] for seg in data[0] if seg and seg[0])
    return out, src


def _translate_baidu(text, cfg, src, tgt):
    import hashlib
    import random
    import urllib.parse
    appid = (cfg.get("appid") or "").strip()
    key = (cfg.get("appkey") or "").strip()
    if not appid or not key:
        raise RuntimeError("尚未填写百度翻译 appid / 密钥（请到“设置→翻译设置”填写）")
    salt = str(random.randint(10000, 99999))
    sign = hashlib.md5((appid + text + salt + key).encode("utf-8")).hexdigest()
    params = urllib.parse.urlencode({
        "q": text, "from": src if src in ("zh", "en") else "auto",
        "to": tgt if tgt in ("zh", "en") else "zh", "appid": appid,
        "salt": salt, "sign": sign})
    _code, body = _http_get("https://fanyi-api.baidu.com/api/trans/vip/translate?" + params)
    data = json.loads(body)
    if "error_code" in data:
        raise RuntimeError(f"百度翻译错误 {data['error_code']}: "
                           f"{data.get('error_msg', '')}")
    out = "".join(item.get("dst", "") for item in data.get("trans_result", []))
    return out, src


class TranslateThread(QThread):
    """后台翻译线程：避免网络请求阻塞界面。"""
    done = Signal(str, str, str)    # (译文, 源语言, 错误信息)

    def __init__(self, text, cfg):
        super().__init__()
        self.text = text
        self.cfg = cfg

    def run(self):
        try:
            out, src = translate_text(self.text, self.cfg)
            self.done.emit(out, src, "")
        except Exception as e:
            self.done.emit("", "", str(e))


class TranslatePanel(QWidget):
    """翻译面板（放在右侧停靠窗，与搜索面板同一区域）：
    显示原文与译文，可复制译文、切换目标语言、再译。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.thread = None
        self.last_result = ""
        self.last_src = ""       # 上次已翻译的原文（防抖回调用它判断是否真变了）
        self.cfg = translate_load()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("目标语言:"))
        self.lang_combo = QComboBox()
        for name, code in TRANSLATE_LANGS:
            self.lang_combo.addItem(name, code)
        codes = [c for _n, c in TRANSLATE_LANGS]
        self.lang_combo.setCurrentIndex(
            codes.index(self.cfg.get("target", "zh"))
            if self.cfg.get("target") in codes else 0)
        self.lang_combo.currentIndexChanged.connect(self.retranslate)
        bar.addWidget(self.lang_combo, 1)
        lay.addLayout(bar)

        self.engine_label = QLabel()
        self.engine_label.setStyleSheet("color:#666; font-size:12px;")
        lay.addWidget(self.engine_label)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("原文（可手动修改后再译）:"))
        src_row.addStretch(1)
        # 追加模式：勾选后新选中的文字追加到原文末尾（空行分隔），不勾则替换
        self.append_chk = QCheckBox("追加选中文字")
        self.append_chk.setToolTip(
            "勾选后：在 PDF 里新选中的文字会追加到原文末尾，不覆盖已编辑的内容")
        self.append_chk.setChecked(bool(self.cfg.get("append", False)))
        self.append_chk.toggled.connect(self._on_append_toggled)
        src_row.addWidget(self.append_chk)
        self.clear_src_btn = QPushButton("清空")
        self.clear_src_btn.setToolTip("清空原文框")
        self.clear_src_btn.clicked.connect(self.src_edit_clear)
        src_row.addWidget(self.clear_src_btn)
        lay.addLayout(src_row)
        self.src_edit = QPlainTextEdit()
        self.src_edit.setPlaceholderText("选中 PDF 文字自动填入；也可直接在此修改…")
        self.src_edit.setMaximumHeight(110)
        lay.addWidget(self.src_edit)
        # 原文改动后自动重译（防抖：停止输入 700ms 才发请求，避免逐字打接口）
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(700)
        self._auto_timer.timeout.connect(self._auto_retranslate)
        self.src_edit.textChanged.connect(self._on_src_changed)

        lay.addWidget(QLabel("译文:"))
        self.out_edit = QPlainTextEdit()
        self.out_edit.setReadOnly(True)
        lay.addWidget(self.out_edit, 1)

        row = QHBoxLayout()
        self.retranslate_btn = QPushButton("重新翻译")
        self.retranslate_btn.clicked.connect(self.retranslate)
        self.copy_btn = QPushButton("复制译文")
        self.copy_btn.clicked.connect(self.copy_out)
        self.settings_btn = QPushButton("翻译设置…")
        self.settings_btn.clicked.connect(self.open_settings)
        row.addWidget(self.retranslate_btn)
        row.addWidget(self.copy_btn)
        row.addWidget(self.settings_btn)
        lay.addLayout(row)

        self.info = QLabel("请先用'选择文字'工具选中要翻译的内容")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color:#666; font-size:12px;")
        lay.addWidget(self.info)

        self.refresh_engine_label()

    def refresh_engine_label(self):
        eng = self.cfg.get("engine", "google")
        self.engine_label.setText("引擎：" + ("Google" if eng == "google"
                                           else "百度翻译"))

    def open_settings(self):
        dlg = TranslateSettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            translate_save(dlg.result_cfg())
            self.retranslate()

    def set_text(self, text):
        """把新选中的文字放进原文框并立即翻译。
        追加模式（勾选“追加选中文字”）下追加到末尾、空行分隔，保留已有内容；
        否则整体替换（重复选中同一段则只聚焦不重译）。"""
        text = (text or "").strip()
        if not text:
            return
        cur = self.src_edit.toPlainText()
        if self.append_chk.isChecked() and cur.strip():
            # 已含相同段落就不重复追加
            if text in [s.strip() for s in cur.split("\n\n")]:
                self.retranslate()
                return
            text = cur.rstrip() + "\n\n" + text
        elif cur.strip() == text:
            self.retranslate()   # 内容相同：也能触发已存在的翻译
            return
        self.text = text
        self._auto_timer.stop()          # 程序写入不算用户编辑，撤掉待触发的自动重译
        self.src_edit.blockSignals(True)
        self.src_edit.setPlainText(text)
        self.src_edit.blockSignals(False)
        self.retranslate()

    def src_edit_clear(self):
        """清空原文框与译文（追加模式下用于重新开始）。"""
        self._auto_timer.stop()
        self.src_edit.blockSignals(True)
        self.src_edit.setPlainText("")
        self.src_edit.blockSignals(False)
        self.text = ""
        self.last_src = ""
        self.last_result = ""
        self.out_edit.clear()
        self.copy_btn.setEnabled(False)
        self.info.setText("请先用'选择文字'工具选中要翻译的内容")

    def _on_append_toggled(self, checked):
        """记住追加模式开关（下次启动仍生效）。"""
        cfg = translate_load()
        cfg["append"] = bool(checked)
        translate_save(cfg)

    def _on_src_changed(self):
        """原文框被用户改动：启动防抖定时器，停手后自动重译。"""
        if self.text.strip() or self.src_edit.toPlainText().strip():
            self._auto_timer.start()

    def _auto_retranslate(self):
        """防抖到期：内容确实变了才重新翻译，否则不打扰接口。"""
        cur = self.src_edit.toPlainText()
        if cur == self.last_src:
            return
        self.retranslate()

    def retranslate(self):
        # 以原文框最新内容为准（用户可能手动增删改正过文字）
        self._auto_timer.stop()
        self.text = self.src_edit.toPlainText()
        self.last_src = self.text
        self.cfg = translate_load()
        self.cfg["target"] = self.lang_combo.currentData()
        self.cfg["append"] = self.append_chk.isChecked()
        translate_save(self.cfg)   # 记住目标语言与追加模式
        self.refresh_engine_label()
        self.out_edit.clear()
        self.copy_btn.setEnabled(False)
        if not self.text.strip():
            self.info.setText("请先用'选择文字'工具选中要翻译的内容")
            return
        self.info.setText("翻译中…")
        # 上个请求还在跑就先等它结束，避免译文错位
        if self.thread and self.thread.isRunning():
            self.thread.wait(300)
        self.thread = TranslateThread(self.text, self.cfg)
        self.thread.done.connect(self.on_done)
        self.thread.start()

    def on_done(self, out, src, err):
        if err:
            self.info.setText(f"翻译失败：{err}\n\nTip：Google 接口在国内可能需代理；"
                              "也可点“翻译设置…”改用百度翻译并填写密钥。")
            return
        self.last_result = out
        self.out_edit.setPlainText(out)
        self.info.setText(f"原语言：{src} → 目标：{self.lang_combo.currentText()}"
                          f"（共 {len(out)} 字符）")
        self.copy_btn.setEnabled(True)

    def copy_out(self):
        if self.last_result:
            QApplication.clipboard().setText(self.last_result)
            self.info.setText("译文已复制到剪贴板")


class TranslateSettingsDialog(QDialog):
    """翻译设置：选择引擎、目标语言、填写百度密钥。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("翻译设置")
        self.setMinimumWidth(420)
        self.cfg = translate_load()

        form = QFormLayout(self)
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("Google（免 Key，可能需代理）", "google")
        self.engine_combo.addItem("百度翻译（需 appid / 密钥）", "baidu")
        self.engine_combo.setCurrentIndex(
            1 if self.cfg.get("engine") == "baidu" else 0)
        form.addRow("翻译引擎", self.engine_combo)

        self.target_combo = QComboBox()
        for name, code in TRANSLATE_LANGS:
            self.target_combo.addItem(name, code)
        codes = [c for _n, c in TRANSLATE_LANGS]
        self.target_combo.setCurrentIndex(
            codes.index(self.cfg.get("target", "zh"))
            if self.cfg.get("target") in codes else 0)
        form.addRow("默认目标语言", self.target_combo)

        self.appid_edit = QLineEdit(self.cfg.get("appid", ""))
        self.appkey_edit = QLineEdit(self.cfg.get("appkey", ""))
        self.appkey_edit.setEchoMode(QLineEdit.Password)
        form.addRow("百度 appid", self.appid_edit)
        form.addRow("百度密钥", self.appkey_edit)

        tip = QLabel("百度翻译密钥申请：fanyi-api.baidu.com → 管理控制台 → 开发者信息。\n"
                     "Google 免 Key 接口在国内网络可能不可用，此时请改用百度。")
        tip.setWordWrap(True)
        form.addRow(tip)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def result_cfg(self):
        # 在原有设置上覆盖本对话框编辑的字段，避免丢掉 append 等其它键
        cfg = dict(self.cfg)
        cfg.update({"engine": self.engine_combo.currentData(),
                    "target": self.target_combo.currentData(),
                    "appid": self.appid_edit.text().strip(),
                    "appkey": self.appkey_edit.text().strip()})
        return cfg
