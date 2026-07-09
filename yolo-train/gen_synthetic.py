#!/usr/bin/env python3
"""
交通标志合成数据集生成器(正式全量)。
复刻「黑白打印到A4纸 -> 斜着贴地/凳腿 -> 彩色车载相机拍下」这条物理退化链，自动出 YOLO 标注。

域约束(来自现场判断)：
- 相机是彩色的，只有 A4 纸是黑白打印；标志印在白纸中间，四周有白边。
- 标志在画面里始终是"小图标"(车不会撞它，怼近才反应)，尺寸分布偏小。
- 遮挡两种：边角贴色块 + 随机切掉一个斜角。

用法:  .venv/bin/python gen_synthetic.py
产出:  datasets/traffic_signs/{images,labels}/{train,val}/  + traffic_signs.yaml
"""
import os
import glob
import random
import shutil
import numpy as np
import cv2
from PIL import Image

# ---------------- 配置 ----------------
RAW = "raw_data"
BG_DIR = "background-data"
OUT = "datasets/traffic_signs"
CANVAS = 640                    # 输出图尺寸(YOLO imgsz)
N_TRAIN = 3000
N_VAL = 500
MAX_SIGNS = 3                   # 每图最多贴几个标志
SEED = 0

CLASSES = ["turn_left", "turn_right", "ahead", "stop", "no_entry"]
CLASS_ID = {n: i for i, n in enumerate(CLASSES)}
# 注意: raw_data 里 turn_left/turn_right 两张模板文件名与内容左右颠倒，这里按"实际内容"映射，避免标签反。
TEMPLATES = {
    "turn_left":  "turn_right.webp",   # turn_right.webp 画的是左转
    "turn_right": "turn_left.webp",    # turn_left.webp 画的是右转
    "ahead":      "ahead.webp",
    "stop":       "stop.webp",
    "no_entry":   "no_entry.webp",
}


# ---------------- 素材加载 ----------------
def load_templates():
    tpl = {}
    for name, fn in TEMPLATES.items():
        arr = np.array(Image.open(os.path.join(RAW, fn)).convert("RGBA"))
        tpl[name] = arr
    return tpl


def load_backgrounds(split_ratio=0.7):
    """真实背景实拍图，按 split 切成 train/val 两组(背景不重叠，防验证集背景泄漏)。"""
    files = sorted(glob.glob(os.path.join(BG_DIR, "*")))
    files = [f for f in files if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
    imgs = [cv2.imread(f) for f in files]
    imgs = [im for im in imgs if im is not None]
    n_tr = max(1, int(round(len(imgs) * split_ratio)))
    return imgs[:n_tr], imgs[n_tr:] if len(imgs) > n_tr else imgs[-1:]


# ---------------- 阶段 A：黑白打印到白色 A4 纸 ----------------
def stage_a_print(rgba):
    """标志灰度 -> 印到白 A4 纸(留白边) -> 打印退化。可能随机切掉一个斜角。
    返回: 纸(3ch), 纸alpha, 标志图形mask。"""
    rgb = rgba[:, :, :3].astype(np.float32)
    alpha = rgba[:, :, 3].astype(np.float32)
    a = (alpha / 255.0)[:, :, None]
    sign_on_white = (rgb * a + 255.0 * (1 - a)).astype(np.uint8)
    sign_gray = cv2.cvtColor(sign_on_white, cv2.COLOR_RGB2GRAY)
    sh, sw = sign_gray.shape

    margin = random.uniform(0.10, 0.22)
    paper_short = int(max(sw, sh) / (1 - 2 * margin))
    paper_long = int(paper_short * 1.414)
    pw, ph = paper_short, paper_long
    paper = np.full((ph, pw), random.randint(244, 255), np.uint8)
    ox = (pw - sw) // 2
    oy = (ph - sh) // 2
    paper[oy:oy + sh, ox:ox + sw] = sign_gray
    sign_mask = np.zeros((ph, pw), np.uint8)
    sign_mask[oy:oy + sh, ox:ox + sw] = alpha.astype(np.uint8)

    # 打印退化
    g = paper.astype(np.float32)
    levels = random.choice([3, 4, 5])
    g = np.round(g / 255.0 * (levels - 1)) / (levels - 1) * 255.0
    g = (g - 128) * random.uniform(0.8, 0.95) + 128
    g = g + np.random.normal(0, random.uniform(3, 8), g.shape)
    g = np.clip(g, 0, 255).astype(np.uint8)
    if random.random() < 0.5:
        g = cv2.GaussianBlur(g, (3, 3), 0)
    sheet_rgb = cv2.cvtColor(g, cv2.COLOR_GRAY2RGB)
    sheet_alpha = np.full((ph, pw), 255, np.uint8)

    # 随机切掉一个斜角(纸被裁/翘掉一角)——一种遮挡
    if random.random() < 0.30:
        corner = random.choice([(0, 0), (pw, 0), (pw, ph), (0, ph)])
        legx = int(pw * random.uniform(0.15, 0.45))
        legy = int(ph * random.uniform(0.15, 0.45))
        cx, cy = corner
        sx = cx - legx if cx > 0 else cx + legx
        sy = cy - legy if cy > 0 else cy + legy
        tri = np.array([[cx, cy], [sx, cy], [cx, sy]], np.int32)
        cv2.fillConvexPoly(sheet_alpha, tri, 0)
        cv2.fillConvexPoly(sign_mask, tri, 0)   # 框只统计仍可见的标志部分

    return sheet_rgb, sheet_alpha, sign_mask


# ---------------- 阶段 B：把纸贴到彩色背景(单个标志图层) ----------------
def render_layer(sheet_rgb, sheet_alpha, sign_mask, canvas=CANVAS):
    """整张纸缩放(偏小) -> 旋转 -> 透视 -> 平移 -> 光照，返回 (rgb层, alpha层, 标志mask层)。"""
    ph0, pw0 = sheet_rgb.shape[:2]
    # 尺寸偏小：长边占画面 10%~45%，平方采样压向小端
    r = random.random() ** 2
    long_target = canvas * (0.10 + 0.35 * r)
    f = long_target / max(ph0, pw0)
    nw, nh = max(8, int(pw0 * f)), max(8, int(ph0 * f))
    sheet = cv2.resize(sheet_rgb, (nw, nh))
    sal = cv2.resize(sheet_alpha, (nw, nh))
    smask = cv2.resize(sign_mask, (nw, nh))

    pad = int(canvas * 1.5)
    big = np.zeros((pad, pad, 3), np.uint8)
    bal = np.zeros((pad, pad), np.uint8)
    bmk = np.zeros((pad, pad), np.uint8)
    oy = (pad - nh) // 2
    ox = (pad - nw) // 2
    big[oy:oy + nh, ox:ox + nw] = sheet
    bal[oy:oy + nh, ox:ox + nw] = sal
    bmk[oy:oy + nh, ox:ox + nw] = smask

    ang = random.uniform(-25, 25)
    M = cv2.getRotationMatrix2D((pad / 2, pad / 2), ang, 1.0)
    big = cv2.warpAffine(big, M, (pad, pad))
    bal = cv2.warpAffine(bal, M, (pad, pad))
    bmk = cv2.warpAffine(bmk, M, (pad, pad))

    src = np.float32([[ox, oy], [ox + nw, oy], [ox + nw, oy + nh], [ox, oy + nh]])
    jit = min(nw, nh) * random.uniform(0.12, 0.28)
    dst = src + np.random.uniform(-jit, jit, src.shape).astype(np.float32)
    P = cv2.getPerspectiveTransform(src, dst)
    big = cv2.warpPerspective(big, P, (pad, pad))
    bal = cv2.warpPerspective(bal, P, (pad, pad))
    bmk = cv2.warpPerspective(bmk, P, (pad, pad))

    ys, xs = np.where(bal > 10)
    if len(xs) == 0:
        return None
    px0, px1, py0, py1 = xs.min(), xs.max(), ys.min(), ys.max()
    bw, bh = px1 - px0, py1 - py0
    if bw >= canvas or bh >= canvas:
        return None
    tx = random.randint(0, canvas - bw) - px0
    ty = random.randint(0, canvas - bh) - py0
    Mt = np.float32([[1, 0, tx], [0, 1, ty]])
    big = cv2.warpAffine(big, Mt, (canvas, canvas))
    bal = cv2.warpAffine(bal, Mt, (canvas, canvas))
    bmk = cv2.warpAffine(bmk, Mt, (canvas, canvas))

    light = np.ones((canvas, canvas), np.float32) * random.uniform(0.7, 1.15)
    cyc, cxc = random.randint(0, canvas), random.randint(0, canvas)
    yy, xx = np.mgrid[0:canvas, 0:canvas]
    spot = np.exp(-((xx - cxc) ** 2 + (yy - cyc) ** 2) / (2 * (canvas * 0.4) ** 2))
    light += spot * random.uniform(0.0, 0.35)
    big = np.clip(big.astype(np.float32) * light[:, :, None], 0, 255).astype(np.uint8)
    return big, bal, bmk


def make_bg(backgrounds, size=CANVAS):
    src = random.choice(backgrounds)
    h, w = src.shape[:2]
    crop = int(min(h, w) * random.uniform(0.4, 0.9))
    x0 = random.randint(0, w - crop)
    y0 = random.randint(0, h - crop)
    bg = cv2.resize(src[y0:y0 + crop, x0:x0 + crop], (size, size))
    if random.random() < 0.5:
        bg = cv2.flip(bg, 1)
    return bg


def bbox_from_mask(bmk, size=CANVAS):
    ys, xs = np.where(bmk > 10)
    if len(xs) == 0:
        return None
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    return ((x0 + x1) / 2 / size, (y0 + y1) / 2 / size, (x1 - x0) / size, (y1 - y0) / size)


def boxes_overlap(a, b):
    """paper 外接框重叠判定(归一化 cx,cy,w,h)，避免标志互相压。"""
    ax0, ay0 = a[0] - a[2] / 2, a[1] - a[3] / 2
    ax1, ay1 = a[0] + a[2] / 2, a[1] + a[3] / 2
    bx0, by0 = b[0] - b[2] / 2, b[1] - b[3] / 2
    bx1, by1 = b[0] + b[2] / 2, b[1] + b[3] / 2
    return not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)


def apply_occlusion(comp, box, backgrounds):
    """从背景抠一小块贴到标志外圈，模拟被物体部分遮挡。缩小、压边、不碰核心。框不变。"""
    if random.random() > 0.25:
        return comp
    h, w = comp.shape[:2]
    cx, cy, bw, bh = box
    x0 = int((cx - bw / 2) * w); y0 = int((cy - bh / 2) * h)
    x1 = int((cx + bw / 2) * w); y1 = int((cy + bh / 2) * h)
    ow = int((x1 - x0) * random.uniform(0.10, 0.22))
    oh = int((y1 - y0) * random.uniform(0.10, 0.22))
    if ow < 4 or oh < 4:
        return comp
    # 贴在标志某条边上(压外圈，尽量不盖中心)
    edge = random.choice(["top", "bottom", "left", "right"])
    if edge == "top":
        px, py = random.randint(x0, x1 - ow), y0
    elif edge == "bottom":
        px, py = random.randint(x0, x1 - ow), y1 - oh
    elif edge == "left":
        px, py = x0, random.randint(y0, y1 - oh)
    else:
        px, py = x1 - ow, random.randint(y0, y1 - oh)
    src = random.choice(backgrounds)
    sh, sw = src.shape[:2]
    sx, sy = random.randint(0, sw - ow), random.randint(0, sh - oh)
    comp[py:py + oh, px:px + ow] = src[sy:sy + oh, sx:sx + ow]
    return comp


# ---------------- 阶段 C：彩色采集退化 ----------------
def stage_c_capture(img):
    out = img.copy()
    if random.random() < 0.6:
        klen = random.choice([3, 5, 7])
        kern = np.zeros((klen, klen))
        kern[klen // 2, :] = 1.0 / klen
        Mk = cv2.getRotationMatrix2D((klen / 2, klen / 2), random.uniform(0, 180), 1)
        kern = cv2.warpAffine(kern, Mk, (klen, klen))
        out = cv2.filter2D(out, -1, kern)
    if random.random() < 0.5:
        out = cv2.GaussianBlur(out, (3, 3), 0)
    gains = np.array([random.uniform(0.9, 1.1) for _ in range(3)], np.float32)
    out = np.clip(out.astype(np.float32) * gains[None, None, :], 0, 255).astype(np.uint8)
    out = np.clip(out.astype(np.float32) + np.random.normal(0, random.uniform(3, 10), out.shape), 0, 255).astype(np.uint8)
    _, enc = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, random.randint(35, 75)])
    return cv2.imdecode(enc, cv2.IMREAD_COLOR)


# ---------------- 组装一张图 ----------------
def make_image(templates, backgrounds):
    comp = make_bg(backgrounds)
    placed_boxes = []   # paper 外接框，用于查重叠
    labels = []         # (class_id, sign_box)
    k = random.randint(1, MAX_SIGNS)
    for _ in range(k):
        name = random.choice(CLASSES)
        sheet_rgb, sheet_alpha, sign_mask = stage_a_print(templates[name])
        placed = False
        for _try in range(6):
            layer = render_layer(sheet_rgb, sheet_alpha, sign_mask)
            if layer is None:
                continue
            big, bal, bmk = layer
            paper_box = bbox_from_mask(bal)   # 用整张纸的范围查重叠
            sign_box = bbox_from_mask(bmk)    # 标注只框标志图形
            if paper_box is None or sign_box is None:
                continue
            if any(boxes_overlap(paper_box, pb) for pb in placed_boxes):
                continue
            af = (bal.astype(np.float32) / 255.0)[:, :, None]
            comp = (big.astype(np.float32) * af + comp.astype(np.float32) * (1 - af)).astype(np.uint8)
            comp = apply_occlusion(comp, sign_box, backgrounds)
            placed_boxes.append(paper_box)
            labels.append((CLASS_ID[name], sign_box))
            placed = True
            break
        # 放不下就跳过这个标志
    comp = stage_c_capture(comp)
    return comp, labels


def main():
    random.seed(SEED)
    np.random.seed(SEED)
    templates = load_templates()
    bg_train, bg_val = load_backgrounds(0.7)
    print(f"背景: train {len(bg_train)} 张 / val {len(bg_val)} 张 (不重叠)")

    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    for split in ("train", "val"):
        os.makedirs(os.path.join(OUT, "images", split), exist_ok=True)
        os.makedirs(os.path.join(OUT, "labels", split), exist_ok=True)

    for split, n, bgs in (("train", N_TRAIN, bg_train), ("val", N_VAL, bg_val)):
        for i in range(n):
            comp, labels = make_image(templates, bgs)
            stem = f"{split}_{i:05d}"
            cv2.imwrite(os.path.join(OUT, "images", split, stem + ".jpg"), comp)
            with open(os.path.join(OUT, "labels", split, stem + ".txt"), "w") as fh:
                for cid, (cx, cy, w, h) in labels:
                    fh.write(f"{cid} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n")
            if (i + 1) % 500 == 0:
                print(f"  {split}: {i + 1}/{n}")

    # 写 yaml
    with open("traffic_signs.yaml", "w") as fh:
        fh.write(
            "path: datasets/traffic_signs\n"
            "train: images/train\n"
            "val: images/val\n"
            "test: images/test   # 真车验证集(另建)\n"
            f"nc: {len(CLASSES)}\n"
            f"names: {CLASSES}\n"
        )
    print("done ->", OUT, "+ traffic_signs.yaml")


if __name__ == "__main__":
    main()
