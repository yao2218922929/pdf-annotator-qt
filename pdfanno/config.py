# -*- coding: utf-8 -*-
"""配置与本地数据文件的读写（颜色板 / 历史 / 进度 / 文件夹 / 快捷键 / 笔记设置）。

全部为模块级函数，仅依赖标准库 + PySide6 的 QColor/QMessageBox。
由原单文件抽出；主文件通过门面重新导出，保持向后兼容。

注意 `_SAVE_ERRORS`：它是跨模块共享的可变列表，必须"原地修改"而非重新绑定，
否则主文件门面里 `from pdfanno.config import _SAVE_ERRORS` 拿到的是旧对象、
无法同步（回归测试 _t_opt.py 会直接访问该名字）。
"""
import json
import os

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMessageBox

from .constants import COLORS

# ---------------- 颜色板 ----------------
COLORS_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_colors.json")

# 写盘失败的文件清单（供统一提示，避免配置/数据静默丢失）
_SAVE_ERRORS = []


def _safe_write_json(path, obj):
    """原子写入 JSON（先写临时文件再替换）。成功 True，失败 False 并记录。"""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        _SAVE_ERRORS.append((path, str(e)))
        return False


def flush_save_errors(parent=None, title="保存失败"):
    """若有写盘失败记录，弹一次提示并清空。返回是否有失败。"""
    if not _SAVE_ERRORS:
        return False
    seen, lines = set(), []
    for path, err in _SAVE_ERRORS:
        if path in seen:
            continue
        seen.add(path)
        lines.append(f"· {os.path.basename(path)}：{err}")
    _SAVE_ERRORS.clear()   # 原地清空：保持与门面共享同一列表对象
    QMessageBox.warning(
        parent, title,
        "以下内容未能保存到磁盘，请检查磁盘空间或文件是否被占用：\n\n"
        + "\n".join(lines))
    return True


def colors_load():
    """读取自定义颜色板。返回 [[名称, #rrggbb], ...]；无/损坏时用默认 COLORS。"""
    try:
        with open(COLORS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        lst = d.get("colors", [])
        if (isinstance(lst, list) and len(lst) == len(COLORS)
                and all(isinstance(x, list) and len(x) == 2
                        and isinstance(x[0], str) and isinstance(x[1], str)
                        and QColor(x[1]).isValid() for x in lst)):
            return [[x[0], QColor(x[1]).name()] for x in lst]
    except Exception:
        pass
    return [[n, h] for n, h in COLORS]


def colors_save(lst):
    _safe_write_json(COLORS_FILE, {"colors": lst})


# ---------------- 最近打开历史（PDF管理） ----------------
HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_history.json")
HISTORY_MAX = 12


def history_load():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        lst = d.get("recent", [])
        # 只保留仍存在的文件
        return [p for p in lst if isinstance(p, str) and os.path.isfile(p)]
    except Exception:
        return []


def history_save(lst):
    _safe_write_json(HISTORY_FILE, {"recent": lst[:HISTORY_MAX]})


def history_add(path):
    lst = history_load()
    if path in lst:
        lst.remove(path)
    lst.insert(0, path)
    history_save(lst[:HISTORY_MAX])


def history_remove(path):
    lst = history_load()
    if path in lst:
        lst.remove(path)
        history_save(lst)


def history_clear():
    history_save([])


# ---------------- 阅读进度记忆（每个PDF的页码/缩放） ----------------
PROGRESS_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_progress.json")
PROGRESS_MAX = 200   # 最多记住多少个文件的进度


def progress_load():
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def progress_get(path):
    """返回 (页码0基, 用户缩放倍率)；无记录返回 None。"""
    rec = progress_load().get(path)
    if isinstance(rec, dict):
        try:
            return int(rec.get("page", 0)), float(rec.get("zoom", 1.0))
        except Exception:
            return None
    return None


def progress_set(path, page, zoom):
    """记录某 PDF 的阅读进度（超出上限时丢弃最旧的记录）。"""
    if not path:
        return
    d = progress_load()
    if path in d:
        d.pop(path, None)
    d[path] = {"page": int(page), "zoom": float(zoom)}
    if len(d) > PROGRESS_MAX:   # 裁剪最旧（按插入顺序）
        for k in list(d.keys())[: len(d) - PROGRESS_MAX]:
            d.pop(k, None)
    _safe_write_json(PROGRESS_FILE, d)


# ---------------- 文件夹收藏（左侧PDF管理） ----------------
FOLDERS_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_folders.json")


def folders_load():
    """读取收藏的文件夹列表。"""
    try:
        with open(FOLDERS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return [p for p in d.get("folders", []) if isinstance(p, str)]
    except Exception:
        return []


def folders_save(lst):
    _safe_write_json(FOLDERS_FILE, {"folders": lst})


# ---------------- 快捷键设置 ----------------
SHORTCUTS_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_shortcuts.json")

# 可自定义的动作：key -> (显示名, 默认按键)
SHORTCUT_ACTIONS = [
    ("prev_page", ("上一页", "Left")),
    ("next_page", ("下一页", "Right")),
    ("fit_window", ("适应窗口", "Ctrl+0")),
    ("zoom_in", ("放大", "Ctrl++")),
    ("zoom_out", ("缩小", "Ctrl+-")),
    ("undo", ("撤销批注", "Ctrl+Z")),
    ("redo", ("重做批注", "Ctrl+Y")),
    ("copy", ("复制选中文字", "Ctrl+C")),
    ("highlight", ("高亮选中文字", "H")),
    ("delete_anno", ("删除选中批注", "Delete")),
    ("open_pdf", ("打开PDF", "Ctrl+O")),
    ("save_pdf", ("保存到原文件", "Ctrl+S")),
    ("save_pdf_as", ("另存为批注PDF", "Ctrl+Shift+S")),
    ("search", ("搜索", "Ctrl+F")),
    ("next_hit", ("下一个命中", "F3")),
    ("prev_hit", ("上一个命中", "Shift+F3")),
    ("toggle_folders", ("显示/隐藏文件夹面板", "Ctrl+B")),
    ("switch_tool", ("切换工具（查看/选择文字/选择批注 循环）", "Tab")),
    ("tool_view", ("工具：查看/拖动", "1")),
    ("tool_selecttext", ("工具：选择文字", "2")),
    ("tool_selanno", ("工具：选择批注", "3")),
    ("tool_highlight", ("工具：荧光笔", "4")),
    ("tool_text", ("工具：文字", "5")),
    ("tool_texthl", ("工具：文字高亮", "6")),
    ("translate", ("翻译选中文字", "Ctrl+T")),
    ("notes", ("笔记面板", "Ctrl+J")),
]


def shortcuts_load():
    """读取用户自定义快捷键；返回 {key: 按键串}，未自定义的用默认值补齐。"""
    d = {}
    try:
        with open(SHORTCUTS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    out = {}
    for key, (_name, default) in SHORTCUT_ACTIONS:
        v = d.get(key, default)
        out[key] = v if isinstance(v, str) else default
    return out


def shortcuts_save(cfg):
    _safe_write_json(SHORTCUTS_FILE, cfg)


# ---------------- 笔记设置 ----------------
NOTES_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_notes.json")
NOTES_IMG_DIR = os.path.join(os.path.expanduser("~"), ".pdf_anno_notes_images")
NOTES_CFG_FILE = os.path.join(os.path.expanduser("~"), ".pdf_anno_notes_cfg.json")


def notes_cfg_load():
    """读取笔记设置：存储目录、图片目录、默认导出格式。空=用默认。"""
    d = {"storage_dir": "", "img_dir": "", "export_fmt": "html"}
    try:
        with open(NOTES_CFG_FILE, "r", encoding="utf-8") as f:
            got = json.load(f)
        if isinstance(got, dict):
            d.update({k: got.get(k, d[k]) for k in d})
    except Exception:
        pass
    return d


def notes_cfg_save(cfg):
    _safe_write_json(NOTES_CFG_FILE, cfg)


def notes_file_path():
    """当前笔记数据文件路径（受设置控制，空=默认~/.pdf_anno_notes.json）。"""
    cfg = notes_cfg_load()
    d = cfg.get("storage_dir", "").strip()
    return os.path.join(d, ".pdf_anno_notes.json") if d else NOTES_FILE


def notes_img_dir_path():
    """当前笔记图片目录路径（受设置控制，空=默认）。"""
    cfg = notes_cfg_load()
    d = cfg.get("img_dir", "").strip()
    return d if d else NOTES_IMG_DIR
