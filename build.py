# -*- coding: utf-8 -*-
"""构建 PDF批注工具Qt版 为单文件 .pyw（源码以 zip 内嵌），并管理包缓存。

用法：
    py build.py                 # 构建并写入桌面（默认输出）
    py build.py --out <path>    # 指定输出文件
    py build.py --verify        # 只校验内嵌包可导入，不写文件
    py build.py --clean         # 只清理旧版本缓存

原理：把源码打包成 zip → base64 内嵌进 .pyw；运行时还原到
~/.pdfanno_pkgcache/<hash>/bundle.zip 并注入 sys.path，zipimport 直接从
zip 导入（无需解压）。启动器自带旧版本缓存自动清理。
"""
import os
import sys
import io
import time
import base64
import hashlib
import shutil
import zipfile

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_ROOT = os.path.join(os.path.expanduser("~"), ".pdfanno_pkgcache")
# 运行时清理宽限：新建不足该秒数的缓存目录一律保留（避免误删并发启动实例
# 尚在 import 的缓存——zipimport 加载完即释放句柄，无法用占用检测判断）。
CACHE_GRACE = 6 * 3600
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Desktop",
                           "PDF批注工具Qt版.pyw")

# 打包内容：相对项目目录的条目（文件或目录）。
# 拆分后 src 主文件依赖 pdfanno 包，须一并打进 zip。
PACK_ITEMS = ["pdf_annotator_qt.py", "pdfanno"]
ENTRY = "pdf_annotator_qt"     # 启动器 import 的入口模块
ENTRY_FUNC = "main"            # 入口函数名


def cleanup_pkgcache(root=None, keep=None, grace=CACHE_GRACE):
    """删除旧版本缓存目录，返回删除个数。

    - keep: 当前版本目录名，永不删除；
    - grace: 新建不足该秒数的目录跳过（防误删并发启动实例的缓存）；
    - 任何异常都吞掉，绝不因清理失败影响启动。
    """
    root = root or CACHE_ROOT
    deleted = 0
    try:
        items = []
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if name == keep or not os.path.isdir(path):
                continue
            try:
                items.append((os.path.getmtime(path), path))
            except OSError:
                pass
        now = time.time()
        for mt, path in items:
            if (now - mt) >= grace:
                shutil.rmtree(path, ignore_errors=True)
                deleted += 1
    except OSError:
        pass
    return deleted


_LAUNCHER = r'''# -*- coding: utf-8 -*-
"""PDF批注工具Qt版 —— 单文件启动器（由 build.py 自动生成，请勿手改）。

源码以 zip 形式内嵌于本文件；首次运行还原到用户缓存目录并注入 sys.path
（zipimport 直接从 zip 导入，无需解压），随后拉起主程序。启动时自动清理
旧版本缓存目录。
"""
import os
import sys
import time
import base64
import hashlib
import shutil

_EMBED = "@@B64@@"
_DIGEST = "@@DIGEST@@"
_CACHE_ROOT = os.path.join(os.path.expanduser("~"), ".pdfanno_pkgcache")
_CACHE_GRACE = @@GRACE@@


def _cleanup_old_cache(keep_dir, grace=_CACHE_GRACE):
    """删除旧版本缓存目录。保留当前版本；新建不足 grace 秒的跳过，
    避免误删并发启动中实例正在使用的缓存。异常一律忽略，不影响启动。"""
    try:
        items = []
        for name in os.listdir(_CACHE_ROOT):
            p = os.path.join(_CACHE_ROOT, name)
            if name == keep_dir or not os.path.isdir(p):
                continue
            try:
                items.append((os.path.getmtime(p), p))
            except OSError:
                pass
        now = time.time()
        for mt, p in items:
            if (now - mt) >= grace:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def _bootstrap():
    """还原内嵌包到缓存目录并注入 sys.path，返回 zip 路径。"""
    data = base64.b64decode(_EMBED)
    if hashlib.sha256(data).hexdigest() != _DIGEST:
        raise RuntimeError("内嵌包校验失败，文件可能已损坏")
    keep = _DIGEST[:16]
    cache = os.path.join(_CACHE_ROOT, keep)
    zpath = os.path.join(cache, "bundle.zip")
    if not os.path.exists(zpath):
        os.makedirs(cache, exist_ok=True)
        tmp = zpath + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, zpath)
    sys.path.insert(0, zpath)
    return keep


_keep = _bootstrap()
_cleanup_old_cache(_keep)          # 启动即清理旧版本缓存
from @@ENTRY@@ import @@ENTRY_FUNC@@ as _main
if os.environ.get("PDFANNO_SELFTEST") == "1":
    print("SELFTEST OK", getattr(_main, "__module__", "?"))
    sys.exit(0)
_main()
'''


def pack(items, base=PROJECT_DIR):
    """把条目打包成 zip 字节；跳过 __pycache__ 与 .pyc。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for item in items:
            full = os.path.join(base, item)
            if os.path.isfile(full):
                z.write(full, item)
            elif os.path.isdir(full):
                for root, dirs, files in os.walk(full):
                    dirs[:] = [d for d in dirs if d != "__pycache__"]
                    for f in files:
                        if f.endswith(".pyc"):
                            continue
                        fp = os.path.join(root, f)
                        z.write(fp, os.path.relpath(fp, base))
    return buf.getvalue()


def render_launcher(items=None, base=PROJECT_DIR, grace=CACHE_GRACE):
    """生成启动器源码文本，返回 (文本, digest, zip 字节数)。"""
    data = pack(items or PACK_ITEMS, base)
    b64 = base64.b64encode(data).decode("ascii")
    digest = hashlib.sha256(data).hexdigest()
    text = (_LAUNCHER
            .replace("@@B64@@", b64)
            .replace("@@DIGEST@@", digest)
            .replace("@@GRACE@@", str(int(grace)))
            .replace("@@ENTRY@@", ENTRY)
            .replace("@@ENTRY_FUNC@@", ENTRY_FUNC))
    return text, digest, len(data)


def _selftest(text):
    """把启动器写到临时文件并运行，验证内嵌包可正常导入。"""
    import tempfile
    import subprocess
    tmp = os.path.join(tempfile.gettempdir(), "_pdfanno_build_selftest.py")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    env = dict(os.environ, PDFANNO_SELFTEST="1")
    r = subprocess.run([sys.executable, tmp], env=env,
                       capture_output=True, text=True)
    try:
        os.remove(tmp)
    except OSError:
        pass
    ok = r.returncode == 0 and "SELFTEST OK" in (r.stdout or "")
    return ok, (r.stdout or "").strip(), (r.stderr or "").strip()


def build(out_path=DEFAULT_OUT, verify=False):
    text, digest, nbytes = render_launcher()
    ok, out, err = _selftest(text)
    if not ok:
        print("构建自检失败：内嵌包无法导入")
        print("stdout:", out)
        print("stderr:", err[-800:])
        return False
    if verify:
        print(f"校验通过：包 {nbytes} 字节，digest {digest[:16]}，入口 {ENTRY}.{ENTRY_FUNC}")
        return True
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    removed = cleanup_pkgcache(keep=digest[:16], grace=0)   # 构建后清掉所有旧缓存
    print(f"已生成：{out_path}")
    print(f"包：{nbytes} 字节 | digest {digest[:16]} | 清理旧缓存 {removed} 个")
    return True


def main():
    args = sys.argv[1:]
    if "--clean" in args:
        n = cleanup_pkgcache(grace=0)
        print(f"已清理旧版本缓存 {n} 个（保留当前无→ 目录：{CACHE_ROOT}）")
        return
    out = DEFAULT_OUT
    if "--out" in args:
        i = args.index("--out")
        if i + 1 < len(args):
            out = args[i + 1]
    build(out_path=out, verify="--verify" in args)


if __name__ == "__main__":
    main()
