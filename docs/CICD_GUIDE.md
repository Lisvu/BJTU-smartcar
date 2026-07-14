# GitHub CI/CD 流水线说明

## 1. 任务范围

本项目使用 GitHub Actions 建立持续集成和持续交付流水线：

- 使用 GitHub 托管代码和版本历史。
- 使用功能分支和 Pull Request 管理改动。
- 代码提交后自动执行语法检查、单元测试和覆盖率检查。
- 在 ROS2 Foxy 容器中自动构建和测试 ROS2 软件包。
- 合并到 `main` 后自动生成 Jetson 源码部署包。
- 使用分支保护阻止未通过检查的代码进入 `main`。

## 2. 已实现流程

```text
本地创建功能分支
    -> 修改代码并执行本地测试
    -> push 到 GitHub
    -> 创建 Pull Request
    -> GitHub Actions 自动运行
       -> Python 语法检查
       -> 单元测试
       -> 覆盖率阈值检查
       -> ROS2 Foxy 构建
       -> ROS2 测试
    -> 必需检查通过
    -> 合并到 main
    -> main 再次运行完整流水线
    -> 自动生成覆盖率报告和 Jetson 部署 Artifact
```

## 3. 关键文件

```text
.github/workflows/ci.yml
.gitignore
README.md
bjtu_ros2_ws/src/bjtu_comm/bjtu_comm/messages.py
bjtu_ros2_ws/src/bjtu_comm/test/test_messages.py
yahboomcar_ws/battery_status.py
yahboomcar_ws/test/test_battery_status.py
ROS2-Foxy/deploy/d3_exploration/bjtu_frontier_explorer/
tests/test_hardware_independent_logic.py
docs/CICD_GUIDE.md
```

各文件职责：

| 文件 | 职责 |
| --- | --- |
| `.github/workflows/ci.yml` | 定义自动检查、ROS2 构建、测试和部署包生成流程 |
| `.gitignore` | 排除缓存、覆盖率文件、ROS 构建目录和大文件 |
| `README.md` | 展示 CI 状态并提供本地构建和测试命令 |
| `messages.py` | 可独立测试的 ROS 消息格式化逻辑 |
| `test_messages.py` | 消息格式化正常和异常输入测试 |
| `battery_status.py` | 不依赖 ROS 的电池状态分类逻辑 |
| `test_battery_status.py` | 电池阈值、边界和错误配置测试 |
| `bjtu_frontier_explorer/` | D3 前沿探索实现、配置和 36 个硬件无关测试 |
| `tests/test_hardware_independent_logic.py` | 交通标志、目标跟随和 PID 等硬件无关业务逻辑测试 |

## 4. GitHub Actions 触发条件

流水线由 `.github/workflows/ci.yml` 定义，在以下情况运行：

- push 到 `main`。
- 创建或更新目标为 `main` 的 Pull Request。
- 在 GitHub Actions 页面手动触发 `workflow_dispatch`。

同一分支有新提交时，旧的未完成任务会被取消，避免浪费 runner 时间。

工作流只申请 `contents: read` 权限，普通 CI 不具有写仓库权限。

## 5. Python Checks

`Python checks` Job 使用 GitHub 托管的 Linux runner 和 Python 3.8，执行：

1. 检出仓库。
2. 安装 `pytest`、`coverage`、`PyYAML` 和 `NumPy`。
3. 使用 `compileall` 检查 `bjtu_comm`、ROS2 自研脚本、全部部署目录、Web 后端和车辆工作区脚本的 Python 语法。
4. 使用 `bash -n` 检查 `ROS2-Foxy/deploy` 下的全部 Shell 脚本语法。
5. 解析 `ROS2-Foxy/deploy` 下的全部 YAML，检查配置文件格式。
6. 使用 `node --check` 检查 Web JavaScript 语法。
7. 执行原有 14 个纯 Python 单元测试。
8. 执行 D3 frontier exploration 的 36 个硬件无关测试。
9. 执行 15 个 ROS2 节点和激光模块中的硬件无关业务逻辑测试。
10. 统计原有目标纯逻辑模块覆盖率。
11. 要求原有目标模块语句覆盖率不低于 100%。
12. 生成并上传 `coverage.xml`。

因此，当前 Python checks 一共执行 65 个测试。D3 测试和硬件无关业务逻辑测试作为独立步骤运行，避免把它们的覆盖率与原有两个模块的 100% 覆盖率混在一起。

当前纳入覆盖率统计的模块：

```text
bjtu_ros2_ws/src/bjtu_comm/bjtu_comm/messages.py
yahboomcar_ws/battery_status.py
```

当前统计结果（覆盖率统计范围仍是两个目标模块）：

```text
Name                                               Stmts   Miss  Cover
--------------------------------------------------------------------------------
bjtu_ros2_ws/src/bjtu_comm/bjtu_comm/messages.py       6      0   100%
yahboomcar_ws/battery_status.py                        8      0   100%
--------------------------------------------------------------------------------
TOTAL                                                 14      0   100%
```

该结果只能表述为“两个目标纯逻辑模块的语句覆盖率为 100%”，不能表述为整个智能车项目覆盖率为 100%。摄像头、雷达、底盘和 Jetson 专用功能需要真实硬件，未纳入普通 GitHub runner 的覆盖率统计。

## 6. ROS2 Foxy Build And Test

`ROS2 Foxy build and test` Job 在 `osrf/ros:foxy-desktop` 容器中运行：

```bash
source /opt/ros/foxy/setup.bash
cd bjtu_ros2_ws
colcon build --packages-select bjtu_comm --event-handlers console_direct+
colcon test --packages-select bjtu_comm --event-handlers console_direct+
colcon test-result --verbose
```

当前只自动构建 `bjtu_comm`，原因是：

- 原厂工作空间包含摄像头、雷达、串口和底盘硬件依赖。
- 项目实际运行平台是 Jetson ARM64，GitHub 托管 runner 是 x86_64。
- ROS2 Foxy 已结束官方维护，旧依赖在新 runner 上不一定可安装。
- 限定自研小包可以提供稳定、可重复且有意义的反馈。

不应通过删除有效测试或在命令后添加 `|| true` 来制造绿色流水线。

## 7. 持续交付 Artifact

`Package Jetson deployment` Job 只在以下条件同时满足时运行：

- 事件是 push。
- 分支是 `main`。
- `Python checks` 成功。
- `ROS2 Foxy build and test` 成功。

Job 会生成：

```text
bjtu-smartcar-<commit-sha>.tar.gz
```

部署包包含：

- `bjtu_comm` 自研 ROS2 包。
- Smart Car Web 控制代码。
- 电池监控和状态判断脚本。
- ROS2 自研启动/控制脚本。
- D1 在线 SLAM/Nav2、D2 静态融合和 D3 前沿探索部署目录。
- README 和 CI/CD 说明文档。

打包时会检查 D1 启动脚本、D2 融合脚本和 Web 后端是否存在，并验证压缩包可正常读取。整个 `ROS2-Foxy/deploy` 会被打包，因此 D3 前沿探索实现、测试和配置也包含在 Artifact 中。部署包只包含源码、脚本、配置和文档，不包含 GitHub x86 runner 生成的 ROS2 二进制。Jetson 使用 ARM64，x86 二进制不能直接在车上运行。

Artifact 保留 30 天，可在对应 Actions 运行页面底部下载。

## 8. 本地验证

### 8.1 单元测试和覆盖率

Linux、macOS 或 Git Bash：

```bash
python3 -m pip install coverage pytest pyyaml numpy
export PYTHONPATH="$PWD/bjtu_ros2_ws/src/bjtu_comm:$PWD/yahboomcar_ws"
python3 -m coverage run \
  --source=bjtu_comm.messages,battery_status \
  -m pytest \
  bjtu_ros2_ws/src/bjtu_comm/test/test_messages.py \
  yahboomcar_ws/test/test_battery_status.py
python3 -m coverage report --show-missing --fail-under=100
```

Windows PowerShell：

```powershell
python -m pip install coverage pytest pyyaml numpy
$env:PYTHONPATH="$PWD\bjtu_ros2_ws\src\bjtu_comm;$PWD\yahboomcar_ws"
python -m coverage run --source=bjtu_comm.messages,battery_status -m pytest `
  bjtu_ros2_ws/src/bjtu_comm/test/test_messages.py `
  yahboomcar_ws/test/test_battery_status.py
python -m coverage report --show-missing --fail-under=100
```

### 8.2 D3 前沿探索测试

Linux、macOS 或 Git Bash：

```bash
cd ROS2-Foxy/deploy/d3_exploration/bjtu_frontier_explorer
PYTHONPATH=. python3 -m pytest -q test
```

Windows PowerShell：

```powershell
$env:PYTHONPATH="ROS2-Foxy\deploy\d3_exploration\bjtu_frontier_explorer"
python -m pytest -q ROS2-Foxy/deploy/d3_exploration/bjtu_frontier_explorer/test
```

### 8.3 其他硬件无关业务逻辑测试

该测试只导入并验证纯计算函数和 PID 状态逻辑。测试文件内使用最小 ROS 消息桩，不启动 ROS 节点、不访问摄像头、雷达、串口或底盘：

```bash
python3 -m pytest -q tests/test_hardware_independent_logic.py
```

### 8.4 Python 语法检查

```bash
python3 -m compileall -q \
  bjtu_ros2_ws/src/bjtu_comm/bjtu_comm \
  bjtu_ros2_ws/src/bjtu_comm/test/test_messages.py \
  ROS2-Foxy/scripts \
  ROS2-Foxy/deploy \
  yahboomcar_ws/smartcar_web \
  yahboomcar_ws/*.py \
  yahboomcar_ws/test/test_battery_status.py
```

### 8.5 Shell、YAML 和 Web JavaScript 检查

以下命令需要 Linux、macOS 或 Git Bash：

```bash
find ROS2-Foxy/deploy -type f -name '*.sh' -print0 \
  | xargs -0 -n1 bash -n

python3 - <<'PY'
from pathlib import Path

import yaml

files = list(Path('ROS2-Foxy/deploy').rglob('*.yaml'))
if not files:
    raise SystemExit('No deployment YAML files found')
for path in files:
    with path.open(encoding='utf-8') as stream:
        yaml.safe_load(stream)
    print(f'Valid YAML: {path}')
PY

find yahboomcar_ws/smartcar_web/static -type f -name '*.js' -print0 \
  | xargs -0 -n1 node --check
```

### 8.6 ROS2 构建和测试

需要 Ubuntu 20.04 和 ROS2 Foxy：

```bash
source /opt/ros/foxy/setup.bash
cd bjtu_ros2_ws
colcon build --packages-select bjtu_comm
colcon test --packages-select bjtu_comm
colcon test-result --verbose
```

## 9. 分支与 Pull Request 规范

`main` 是稳定分支，开发应使用功能分支：

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/example

# 修改并测试

git add <需要提交的文件>
git commit -m "feat: describe the change"
git push -u origin feature/example
```

提交前在 GitHub 创建目标为 `main` 的 Pull Request，等待必需检查通过后再合并。

推荐提交前缀：

```text
feat: 新功能
fix: 修复问题
test: 测试
ci: 流水线
docs: 文档
refactor: 不改变行为的重构
```

## 10. 分支保护

仓库所有者已为 `main` 配置分支保护。正确的必需检查是：

```text
Python checks
ROS2 Foxy build and test
```

`Package Jetson deployment` 不能设为 Pull Request 的必需检查，因为它按设计只在代码进入 `main` 后执行，在 PR 中显示 `Skipped` 是正常行为。

不要配置不存在的 `python` 检查，否则 GitHub 会一直显示 `Expected - Waiting for status to be reported` 并阻止合并。

## 11. 安全和仓库规范

不得提交：

- SSH 私钥和密码。
- GitHub Personal Access Token。
- 车辆热点密码。
- `.env` 和真实服务配置。
- ROS2 的 `build/`、`install/` 和 `log/`。
- 数据集、模型权重和大体积运行日志。
- 覆盖率临时文件 `.coverage`、`coverage.xml` 和 `htmlcov/`。

提交前检查：

```bash
git status
git diff --cached
git grep -n -I -E 'password|secret|token|BEGIN.*PRIVATE KEY'
```

命中变量名或示例文本不一定代表泄漏，但必须人工确认没有真实凭据。
