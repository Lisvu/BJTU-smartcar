#!/bin/bash
# 交通标志 YOLOv5s 微调脚本
# 用法: 整个 yolo-train/ 目录传到服务器后, cd yolo-train && bash train.sh
#
# 前置: pip install -r yolov5/requirements.txt  (clone yolov5 后)
set -e

# ---- 1. 环境检查 ----
echo "=== 环境 ==="
python3 -c "import torch; print('PyTorch', torch.__version__, '| CUDA', torch.cuda.is_available(), '| GPU', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"

# ---- 2. 克隆 YOLOv5(如果还没有) ----
if [ ! -d "yolov5" ]; then
    echo ">>> 克隆 YOLOv5 ..."
    git clone https://github.com/ultralytics/yolov5.git --depth 1
    pip install -r yolov5/requirements.txt -q
fi

# ---- 3. 关键: 关闭水平翻转 ----
# turn_left / turn_right 是镜像关系, 默认 fliplr=0.5 会把左转翻成右转但标签不变,
# 直接把这两类标签喂反 -> 左右各 50% 抛硬币. 必须置 0. (其余3类对称,不受影响)
HYP=yolov5/data/hyps/hyp.noflip.yaml
cp yolov5/data/hyps/hyp.scratch-low.yaml "$HYP"
sed -i 's/fliplr: 0.5/fliplr: 0.0/' "$HYP"

# ---- 4. 训练 ----
echo ">>> 开始训练 ..."
python3 yolov5/train.py \
    --img 640 \
    --batch 64 \
    --epochs 80 \
    --data traffic_signs.yaml \
    --weights yolov5s.pt \
    --hyp "$HYP" \
    --project runs \
    --name traffic_signs \
    --cache ram \
    --workers 8 \
    --device 0 \
    --freeze 10

echo "=== 训练完成 ==="
echo "权重: runs/traffic_signs/weights/best.pt"
echo "日志: runs/traffic_signs/"
