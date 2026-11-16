# -*- coding: utf-8 -*-
"""共享常量：颜色板、批注类型标记、文字尺寸等。
由原单文件顶部常量区抽出；主文件通过门面重新导出，保持向后兼容。"""

# 颜色按钮配置（可右键自定义）；存于用户目录，下次启动沿用
COLORS = [("红", "#e53935"), ("蓝", "#1e88e5"), ("绿", "#43a047"),
          ("黄", "#fdd835"), ("黑", "#212121")]

TEXT_FS = 10
HL_W = 10
# 批注以 PDF 原生注释(annotation)对象写入：用 subject 标记归属，
# 保存时先删除本工具写的旧注释再重写 → 幂等，重复保存不会叠加。
ANNO_SUBJECT = "pdfanno"
ANNO_DATA_NAME = "_pdfanno_data.json"   # 旧版烧入方式留下的内嵌数据（仅用于读取）

# ---------------- 渲染 / 缩放 ----------------
WIDTHS = [("细 1pt", 1.0), ("中 2pt", 2.0), ("粗 3.5pt", 3.5),
          ("特粗 5pt", 5.0), ("加粗 8pt", 8.0), ("最粗 12pt", 12.0)]
RENDER_SCALE = 3.0  # 内部渲染倍数：直接以 用户倍率×RENDER_SCALE×屏幕dpr 高分辨率渲染，不降采样
PAGE_PIX_BUDGET = 6_000_000     # 整页渲染的像素预算：超出即改走"只渲染视口可见区域"。
                                # 整页纹理随缩放平方膨胀（常规页 200% 达 27Mpx），
                                # 上传/绘制很卡；区域渲染像素量只与屏幕大小同级
                                # （~1Mpx，与缩放无关），是高倍流畅的关键。
MAX_PIX_DIM = 12000             # 单边像素上限：超过显卡纹理上限(常为16384)会使 QPixmap 变空 → 白屏
REGION_GRID = 256               # 可见区域渲染的对齐网格（场景单位）：同格内滚动复用同一张图
REGION_CACHE_MAX = 12           # 渲染缓存条数上限
WORDS_CACHE_MAX = 4             # 文字词表缓存保留页数（当前页 + 少量邻页）
REGION_PIX_BUDGET = 20_000_000   # 渲染缓存总像素预算（≈80MB）。QPixmap 每像素 4 字节，
                                # 之前 120Mpx≈458MB 是内存失控的主因：翻页/缩放把
                                # 缓存堆到 266MB+ 也不释放。视口附近通常只有 2~3 页、
                                # 每页 ~2.4Mpx，80MB 足够覆盖数个缩放档位仍不重渲。
REGION_MAX_PIXELS = 12_000_000   # 单次区域渲染的像素上限（≈48MB，4K 视口 8Mpx 仍够用；
                                # 高 DPI 大视口时自动降倍，防纹理超限）
ZOOM_RENDER_STEP = 1.25         # 渲染倍率档位：上取到 1.25 的幂，连续缩放多步复用同一批图
ZOOM_REFINE_MS = 260            # 停止缩放多少毫秒后按最终档位重渲染
PAGE_GAP = 16      # 场景中页与页之间的间隔像素（浅灰底自然形成分隔）
TOOLS = [("查看/拖动", "view"), ("选择文字", "selecttext"), ("选择批注", "selanno"),
         ("文字", "text"), ("文字高亮", "texthl")]
# 画笔类工具合并为工具栏上一个"画笔"按钮：左键用当前画笔，右键弹菜单切换
SHAPE_ITEMS = [("画笔", "pen"), ("荧光笔", "highlight"), ("矩形", "rect"),
               ("椭圆", "oval"), ("直线", "line"), ("箭头", "arrow")]
SHAPE_VALS = {v for _, v in SHAPE_ITEMS}
SHAPE_NAMES = {v: n for n, v in SHAPE_ITEMS}

# ---------------- 目录/书签文本清洗 ----------------
import re as _re
LEADER_DOTS_RE = _re.compile(r'^[\.\s]+$')          # 目录点连线: . / ... / 一长串点
LEADER_LINE_RE = _re.compile(r'^[\.\-–—_\s]+$')     # 点/横线/下划线 组成的连线
PAGE_NUM_RE = _re.compile(r'^\d{1,4}$')            # 独立页码

# Altium 导出的原理图 PDF：子图在书签里带父图纸记号，形如
#   pwr_efuse_mp5991_12vin_20a_compress<page101_i4>(121)
#   block2<page24_fan_17>(106-108)
# 尖括号内 pageN = 父图纸名（Altium 自动命名为 page1/page2/…，不是页码！），
# 其后是实例名（i4 / fan_17 …）；括号内 = 子图所在页（可能是一段页范围）。
# 注意：顶层图纸恰好按顺序排列，所以顶层条目的 N 看起来像页码，嵌套图纸
# 就完全对不上（如 <page2> 实际是 PDF 第 108 页）——父页须靠书签树的
# 层级关系推断，见 MainWindow._hier_parent_page。
HIER_TITLE_RE = _re.compile(
    r'^(?P<name>.+?)\s*<page(?P<parent>\d+)_(?P<inst>[A-Za-z0-9_\-]+)>'
    r'(?:\s*\((?P<pages>[\d\-]+)\))?\s*$', _re.IGNORECASE)

# Altium 导出的 PDF 还会给每个 sheet 块放一个 Link 注释（内嵌 JS 动作），
# 这是官方 Descend：/Rect 是该块的精确点击范围，/A 指向的 JS 里
#   var name="Instance I4 of PWR_EFUSE_MP5991_12VIN_20A_COMPRESS";
#   var dst="@LIB.SHEET(SCH_1):PAGE12135_I4@LIB.PWR_EFUSE_..._COMPRESS(SCH_1)";
# dst 就是命名目标键，可直接查到真实页号。比书签推断精确得多：
# 同页多个同名块也各有自己的 Rect，能精确区分点的是哪一个。
ALT_RECT_RE = _re.compile(
    r'/Rect\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s*\]')
ALT_ACTION_RE = _re.compile(r'/A\s+(\d+)\s+0\s+R')
ALT_NAME_RE = _re.compile(r'var\s+name="([^"]*)"')
ALT_DST_RE = _re.compile(r'var\s+dst="([^"]*)"')
ALT_INSTANCE_RE = _re.compile(r'Instance\s+(\S+)\s+of\s+(\S+)', _re.IGNORECASE)
