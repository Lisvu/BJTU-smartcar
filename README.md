# BJTU 智能巡检小车

[![CI](https://github.com/Lisvu/BJTU-smartcar/actions/workflows/ci.yml/badge.svg)](https://github.com/Lisvu/BJTU-smartcar/actions/workflows/ci.yml)

本仓库是面向室内复杂环境巡检场景的智能小车系统代码。项目基于 Yahboom Jetson 小车、ROS2 Foxy、激光雷达、摄像头、环境传感器和 Web 控制台，实现了从基础遥控到 SLAM 建图导航、视觉识别、人员跟随、自动避障、多传感器采集和大语言模型语音控制的一体化功能。

项目目标不是只做一个遥控网页，而是让小车具备“看得见环境、建得出地图、规划得出路线、听得懂自然语言、遇到风险能停止或绕行”的完整巡检闭环。

## 核心功能

### 1. Web 控制台

- 支持前进、后退、左转、右转、左平移、右平移和停止。
- 支持圆形摇杆控制，拖动摇杆即可连续调整运动方向。
- 支持线速度、角速度调节，适配不同巡检速度需求。
- 支持底盘启动、停止和状态查看。
- 支持摄像头实时画面显示。
- 支持电压/电量显示，便于判断小车是否适合继续执行任务。
- 支持 HTTPS 访问，用于浏览器麦克风权限和语音控制。

### 2. SLAM 建图与导航

- 支持基于雷达的在线 SLAM 建图。
- Web 端可以启动/停止建图流程。
- Web 端集成 RViz/noVNC，可直接使用 RViz 原生工具栏设置初始位姿和目标点。
- 支持地图保存、地图命名和多地图选择。
- 支持基于已保存地图启动 Nav2 导航。
- 导航过程中可显示地图、雷达点、小车位置和规划路径。

### 3. 自动避障与安全保护

- 小车运动过程中持续读取雷达障碍物距离。
- 当障碍物距离低于阈值时自动停止，降低碰撞风险。
- Web 控制、语音控制和导航控制均接入停止保护逻辑。
- 控制命令采用持续发布机制，网络断开或长时间没有新命令时小车会停止。

### 4. 大语言模型 Agent 语音控制

- Web 端接收电脑或手机麦克风语音输入。
- 浏览器将语音转为文字后发送给后端。
- 后端调用 DeepSeek，将自然语言拆解为小车可执行的运动指令。
- 支持简单命令，例如“前进一米”“停止”“启动建图”“打开摄像头”。
- 支持复杂空间指令，例如“去右前方四十五度零点三米处”“后退到左后方三十度零点二米处”。
- 针对“左后方”“右后方”等容易误解的语义，加入几何运动规则修正，避免简单把“左”理解为左转。

### 5. 多传感器环境采集

- 支持通过 TCP 读取传感器模块数据。
- 默认传感器地址为 `192.168.1.11:8888`。
- 支持温度、湿度、光照、气压、烟雾/可燃气体、电量等字段展示。
- Web 端提供独立传感器页面，用于实时查看环境数据。

### 6. YOLO 视觉识别与人员跟随

- 基于摄像头图像进行目标检测。
- 支持交通标识识别，包括停止、左转、右转、前进、禁止通行。
- 小车可根据识别到的标识执行对应运动。
- 支持识别画面中最近的人体目标并进行跟随。

### 7. 自主探索与多模块融合

- 支持在线 SLAM、Nav2、YOLO、雷达和传感器数据融合。
- 提供贪心前沿探索相关代码，用于未知区域探索。
- 使用分层状态机组织建图、导航、识别、避障、跟随和停止等状态。
- 目标是形成“感知环境 -> 构建地图 -> 规划路径 -> 执行任务 -> 安全保护”的巡检闭环。

## Web 访问方式

小车 Web 服务运行后，可在同一网络下访问：

```text
https://<小车IP>:8443/
http://<小车IP>:8000/
```

语音控制需要浏览器麦克风权限，推荐使用 HTTPS：

```text
https://<小车IP>:8443/
```

RViz/noVNC 默认使用 HTTP 6080 端口。如果 HTTPS 页面无法嵌入 RViz，可使用 HTTP 打开 SLAM 页面：

```text
http://<小车IP>:8000/slam.html
```

## 常用启动脚本

Web 控制台相关代码位于：

```text
yahboomcar_ws/smartcar_web/
```

ROS2 功能脚本位于：

```text
ROS2-Foxy/scripts/
ROS2-Foxy/deploy/
```

常用脚本包括：

```text
ROS2-Foxy/scripts/start_all.sh
ROS2-Foxy/scripts/start_sign_control.sh
ROS2-Foxy/scripts/stop_sign_control.sh
ROS2-Foxy/deploy/d1_nav/scripts/start_d1_slam_nav.sh
ROS2-Foxy/deploy/d2_fusion/scripts/run_d2_static_fusion.sh
ROS2-Foxy/deploy/d3_exploration/bjtu_frontier_explorer/
```

## 目录结构

```text
.
├── README.md
├── docs/                         # 使用手册、CI/CD 文档和项目说明
├── bjtu_ros2_ws/                 # BJTU ROS2 通信示例与测试包
├── ROS2-Foxy/                    # ROS2 Foxy 脚本、部署配置和自主探索模块
├── yahboomcar_ws/                # Yahboom 小车 ROS2 工作空间和 Web 控制台
│   ├── smartcar_web/             # Web 后端、前端页面、语音控制、传感器页面
│   ├── battery_monitor.py        # 电压/电量监控
│   ├── battery_status.py         # 电池状态计算逻辑
│   └── voice_cmd_vel.py          # 语音模块转 cmd_vel 控制
├── yolo-train/                   # YOLO 标识识别训练和部署相关文件
├── Rosmaster-App/                # Rosmaster 原厂/底层控制样例
└── software/library_ws/          # ROS2 第三方/支撑库源码
```

## 开发与测试

仓库配置了 GitHub Actions CI。CI 会执行：

- Python 语法检查。
- Web JavaScript 语法检查。
- 纯逻辑单元测试。
- `bjtu_comm` ROS2 Foxy 构建和测试。
- 部署包打包检查。

本地运行硬件无关测试：

```bash
python3 -m pip install coverage pytest
export PYTHONPATH="$PWD/bjtu_ros2_ws/src/bjtu_comm:$PWD/yahboomcar_ws"
python3 -m coverage run \
  --source=bjtu_comm.messages,battery_status \
  -m pytest \
  bjtu_ros2_ws/src/bjtu_comm/test/test_messages.py \
  yahboomcar_ws/test/test_battery_status.py
python3 -m coverage report --show-missing --fail-under=100
```

运行 D3 自主探索模块测试：

```bash
cd ROS2-Foxy/deploy/d3_exploration/bjtu_frontier_explorer
PYTHONPATH=. python3 -m pytest -q test
```

## 运行环境

- Jetson Ubuntu 20.04 / ARM64
- ROS2 Foxy
- Python 3.8
- Yahboom 小车底盘
- 激光雷达
- 摄像头
- 传感器 WiFi/TCP 模块

硬件相关节点需要在真实小车或等价 Jetson 环境中运行；CI 只覆盖不依赖硬件的逻辑代码。

## 安全说明

- Web 控制台默认用于局域网演示，不建议直接暴露到公网。
- `/api/move`、`/api/process/start`、`/api/agent/voice` 等接口具备真实控制能力，应在可信网络中使用。
- 语音长距离运动应配合自动避障和人工观察，避免在复杂环境中高速或长距离无监督运行。
- 大语言模型 API Key 不应提交到仓库，建议通过环境变量 `DEEPSEEK_API_KEY` 或本机私有文件配置。

## 文档

- `docs/SMARTCAR_USER_MANUAL.md`：系统使用手册。
- `docs/智能巡检小车系统使用手册.pdf`：PDF 版使用手册。
- `docs/CICD_GUIDE.md`：CI/CD 和测试说明。
- `yahboomcar_ws/smartcar_web/WEB_CONTROL_STATUS.md`：Web 控制台部署和状态说明。
