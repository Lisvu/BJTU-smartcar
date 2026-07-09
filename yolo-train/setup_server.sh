#!/bin/bash
# 服务器端一键配环境脚本
# 用法: cd yolo-train && bash setup_server.sh
set -e

echo "=== 1. 创建 conda 环境 ==="
conda create -n yolo python=3.10 -y
eval "$(conda shell.bash hook)"
conda activate yolo

echo "=== 2. 安装 PyTorch 2.7.1 (CUDA) ==="
pip install torch==2.7.1 torchvision --index-url https://download.pytorch.org/whl/cu128

echo "=== 3. 安装 YOLOv5 依赖 ==="
pip install -r yolov5/requirements.txt

echo "=== 4. 验证 ==="
python -c "import torch; print('PyTorch', torch.__version__, '| CUDA', torch.cuda.is_available(), '| GPU', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"

echo "=== 环境就绪，运行 bash train.sh 开始训练 ==="
