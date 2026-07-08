# Smart Car Web Control Status

更新时间：2026-07-08

## 项目位置

Jetson 上的宿主源码目录：

```text
/home/jetson/code/yahboomcar_ws/smartcar_web
```

ROS mount namespace 内的运行目录：

```text
/root/smartcar_web
```

当前本地整理目录：

```text
/Users/lisvu/tmp_smartcar_web
```

## 当前 Web 功能

Web 控制台入口：

```text
http://192.168.43.84:8000/
```

注意：小车 IP 会随热点或网络变化而变化。此前旧地址 `192.168.187.153` 已不是当前可用地址。

已集成的页面功能：

- 手动控制：前进、后退、左转、右转、停止、急停。
- 速度调节：线速度、角速度滑条。
- 雷达避障：开关、停车距离设置。
- 摄像头画面：通过 Web 后端直接读取 `/dev/video0` 并输出 MJPEG。
- SLAM 建图控制：启动底盘/雷达、启动建图、保存地图。
- 地图可视化：显示 `/map` 栅格地图、机器人位姿、雷达点、规划路径。
- 导航控制：启动 DWA/TEB 导航，发送目标点。
- 地图点击：点击地图后自动填入目标点 X/Y 坐标。

## 后端 API

### 状态

```http
GET /api/status
```

返回内容包括：

- 电压 `/voltage`
- 前方障碍距离 `/scan`
- 地图尺寸 `/map`
- 规划点数 `/plan`
- 机器人位姿 `/odom`
- 摄像头状态
- Web 管理的进程状态

### 可视化数据

```http
GET /api/viz
```

返回内容包括：

- `map`：OccupancyGrid 栅格地图，包括 `width`、`height`、`resolution`、`origin`、`data`
- `scan`：抽样后的雷达点
- `pose`：机器人当前位姿
- `plan`：抽样后的导航路径点
- `ages`：各类数据的刷新年龄

### 摄像头 MJPEG

```http
GET /api/camera/stream
```

实现方式：

- 使用 OpenCV 读取 `/dev/video0`
- 输出 `multipart/x-mixed-replace` MJPEG
- 不依赖 ROS Image topic

### 手动运动控制

```http
POST /api/move
POST /api/stop
```

`/api/move` JSON 示例：

```json
{
  "linear_x": 0.12,
  "linear_y": 0.0,
  "angular_z": 0.0
}
```

后端发布：

```text
/cmd_vel geometry_msgs/msg/Twist
```

### 进程控制

```http
POST /api/process/start
POST /api/process/stop
```

支持的进程名：

- `bringup`
- `camera`
- `slam`
- `mapping_keyboard`
- `save_map`
- `nav_dwa`
- `nav_teb`

当前使用的 ROS 环境：

```bash
source /opt/ros/foxy/setup.bash
source /root/icar_ros2_ws/icar_ws/install/setup.bash
export ROS_DOMAIN_ID=32
export ROS_LOCALHOST_ONLY=0
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROBOT_TYPE=x3
export RPLIDAR_TYPE=a1
```

关键启动命令：

```bash
ros2 launch icar_nav laser_bringup_launch.py robot_type:=x3 rplidar_type:=a1
ros2 launch icar_nav map_gmapping_launch.py
ros2 launch icar_nav navigation_dwa_launch.py
ros2 launch icar_nav navigation_teb_launch.py
ros2 launch icar_nav save_map_launch.py
```

## 已解决的问题

### 1. Web 按钮无响应

排查发现过以下问题：

- Web 服务和 ROS 进程必须运行在相同 mount namespace 和 ROS 环境中。
- 当前车实际使用 `icar_ros2_ws`，不是 `yahboomcar_ros2_ws`。
- 当前 ROS 域为 `ROS_DOMAIN_ID=32`。
- `colorHSV`、`colorTracker`、`joy_ctrl` 会抢占 `/cmd_vel`，导致 Web 控制被覆盖。
- 重复启动多个 `Mcnamu_driver_X3` 或 `sllidar_node` 会抢串口资源。
- 原 `subprocess.Popen(stdout=PIPE)` 没有读取输出，ROS 子进程日志可能写满 pipe 后阻塞。

已采取措施：

- Web 后端启动命令改为显式 source ROS 环境。
- 进程输出改为写入 `/tmp/smartcar_web_<name>.log`。
- `bringup` 明确传入 `robot_type:=x3 rplidar_type:=a1`。
- 停止视觉控制和手柄控制节点，保证 Web 是唯一运动控制源。
- 增加 ROS 图检测，避免重复启动底盘/雷达。

目标正常链路：

```text
Web 按钮 -> /api/move -> smartcar_web_bridge_* -> /cmd_vel -> driver_node -> 底盘
```

### 2. 摄像头页面看不到

ROS 中 `/camera/camera` 节点存在，但没有发布 Image topic。

处理方式：

- 不再依赖 `web_video_server` 或 ROS Image topic。
- Web 后端直接用 OpenCV 读取 `/dev/video0`。
- `/api/camera/stream` 已验证能返回 JPEG 帧。

### 3. SLAM 地图可视化

Web 后端订阅：

```text
/map
/scan
/odom
/plan
```

前端 canvas 绘制：

- OccupancyGrid 灰度地图
- 雷达点
- 机器人朝向
- 导航路径

SLAM 启动后已验证 `/map` 生成过：

```text
width: 384
height: 384
resolution: 0.05
```

## 当前代码文件

```text
server.py
static/index.html
static/app.js
static/style.css
WEB_CONTROL_STATUS.md
```

## 部署方式

将代码同步到 Jetson 宿主源码目录：

```bash
scp server.py jetson@jetson-desktop.local:/home/jetson/code/yahboomcar_ws/smartcar_web/server.py
scp static/index.html static/app.js static/style.css jetson@jetson-desktop.local:/home/jetson/code/yahboomcar_ws/smartcar_web/static/
```

将代码同步到 ROS namespace 内运行目录：

```bash
sudo cp /home/jetson/code/yahboomcar_ws/smartcar_web/server.py /proc/<web_pid>/root/root/smartcar_web/server.py
sudo cp /home/jetson/code/yahboomcar_ws/smartcar_web/static/index.html /proc/<web_pid>/root/root/smartcar_web/static/index.html
sudo cp /home/jetson/code/yahboomcar_ws/smartcar_web/static/app.js /proc/<web_pid>/root/root/smartcar_web/static/app.js
sudo cp /home/jetson/code/yahboomcar_ws/smartcar_web/static/style.css /proc/<web_pid>/root/root/smartcar_web/static/style.css
```

在 ROS namespace 中启动 Web：

```bash
sudo nsenter -t <driver_or_slam_pid> -m -u -i -n -- bash -lc '
cd /root/smartcar_web &&
source /opt/ros/foxy/setup.bash &&
source /root/icar_ros2_ws/icar_ws/install/setup.bash &&
export ROS_DOMAIN_ID=32 ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROBOT_TYPE=x3 RPLIDAR_TYPE=a1 &&
exec python3 server.py
'
```

## 注意事项

- 不要同时手动运行多个 `Mcnamu_driver_X3`。
- 不要同时启动多个雷达节点。
- Web 手动控制时不要运行 `colorHSV`、`colorTracker`、`joy_ctrl` 等会发布 `/cmd_vel` 的节点。
- 如果 SLAM launch 自己带起底盘/雷达，不要再单独点“启动底盘/雷达”。
- 如果页面突然无法访问，优先检查小车 IP 是否变化。
- 如果 Web 能打开但控制无效，优先检查：

```bash
ros2 topic info /cmd_vel -v
```

理想状态：

```text
Publisher count: 1
Node name: smartcar_web_bridge_<pid>

Subscription count: 1
Node name: driver_node
```

## 最后一次状态

在最后一次会话中，已完成文档前的主要功能开发和多轮验证：

- 摄像头 MJPEG 端点返回 JPEG 帧。
- SLAM `/map` 曾成功返回地图数据。
- `/api/viz` 曾成功返回地图栅格。
- `/cmd_vel` 曾清理到只剩 Web 发布和底盘订阅。

最后一次操作中，正在修复 Web bridge 的 ROS subscription 生命周期问题：

- 已将 subscription 句柄保存到 `self.subscriptions`。
- 该修复需要重新同步并重启 Web。

如果重启后发现 `/api/status` 中 `map/pose/scan` 为空，需要检查 Web 节点是否进入 ROS 图：

```bash
ros2 node list --no-daemon | grep smartcar
ros2 topic info /cmd_vel -v
```

如果 `smartcar_web_bridge_<pid>` 存在但 `/cmd_vel` 没有 publisher，重启 Web 并确认启动环境包含：

```bash
RMW_IMPLEMENTATION=rmw_fastrtps_cpp
ROS_DOMAIN_ID=32
```
