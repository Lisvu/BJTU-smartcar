# GitHub CI/CD 流水线实施与验收说明

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
2. 安装 `pytest` 和 `coverage`。
3. 使用 `compileall` 检查目标 Python 文件语法。
4. 执行 14 个纯 Python 单元测试。
5. 统计目标纯逻辑模块覆盖率。
6. 要求目标模块语句覆盖率不低于 100%。
7. 生成并上传 `coverage.xml`。

当前纳入覆盖率统计的模块：

```text
bjtu_ros2_ws/src/bjtu_comm/bjtu_comm/messages.py
yahboomcar_ws/battery_status.py
```

当前统计结果：

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
- README 和 CI/CD 说明文档。

部署包只包含源码、脚本、配置和文档，不包含 GitHub x86 runner 生成的 ROS2 二进制。Jetson 使用 ARM64，x86 二进制不能直接在车上运行。

Artifact 保留 30 天，可在对应 Actions 运行页面底部下载。

## 8. 本地验证

### 8.1 单元测试和覆盖率

Linux、macOS 或 Git Bash：

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

Windows PowerShell：

```powershell
python -m pip install coverage pytest
$env:PYTHONPATH="$PWD\bjtu_ros2_ws\src\bjtu_comm;$PWD\yahboomcar_ws"
python -m coverage run --source=bjtu_comm.messages,battery_status -m pytest `
  bjtu_ros2_ws/src/bjtu_comm/test/test_messages.py `
  yahboomcar_ws/test/test_battery_status.py
python -m coverage report --show-missing --fail-under=100
```

### 8.2 Python 语法检查

```bash
python3 -m compileall -q \
  bjtu_ros2_ws/src/bjtu_comm/bjtu_comm \
  bjtu_ros2_ws/src/bjtu_comm/test/test_messages.py \
  yahboomcar_ws/battery_monitor.py \
  yahboomcar_ws/battery_status.py \
  yahboomcar_ws/test/test_battery_status.py
```

### 8.3 ROS2 构建和测试

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

## 12. 已验证记录

### PR #1：建立 CI

```text
https://github.com/Lisvu/BJTU-smartcar/pull/1
```

完成 Python 测试和 ROS2 Foxy 构建。首轮 ROS2 Job 因旧软件源安装失败，修复后重新运行成功，保留了“失败、定位、修复、通过”的工程记录。

成功运行：

```text
https://github.com/Lisvu/BJTU-smartcar/actions/runs/29175275325
```

### PR #2：覆盖率与持续交付

```text
https://github.com/Lisvu/BJTU-smartcar/pull/2
```

增加 14 个测试、100% 目标覆盖率阈值、覆盖率报告和 Jetson 部署 Artifact。

`main` 完整成功运行：

```text
https://github.com/Lisvu/BJTU-smartcar/actions/runs/29177071290
```

该次运行三个 Job 全部成功，并生成：

```text
coverage-report
jetson-deployment-e9356c41232fae940804dc3711f746172275c713
```

## 13. 验收清单

### CI

- [x] `.github/workflows/ci.yml` 已提交。
- [x] push 到 `main` 自动触发。
- [x] Pull Request 自动触发。
- [x] 支持手动触发。
- [x] Python 语法检查自动执行。
- [x] 14 个单元测试自动执行。
- [x] 目标纯逻辑模块覆盖率阈值为 100%。
- [x] 覆盖率 XML 自动上传。
- [x] ROS2 Foxy 自动构建 `bjtu_comm`。
- [x] ROS2 测试结果自动报告。

### CD

- [x] `main` 测试成功后自动打包。
- [x] Jetson 源码部署 Artifact 自动上传。
- [x] Artifact 不包含错误架构的 x86 ROS2 二进制。
- [x] Artifact 保留 30 天。

### 工程规范

- [x] 使用功能分支和 Pull Request。
- [x] `main` 已配置分支保护。
- [x] 合并前要求 Python 和 ROS2 检查通过。
- [x] 已保留失败后修复并通过的记录。
- [x] README 包含 CI 徽章和本地验证方法。
- [x] `.gitignore` 排除构建和测试产物。

## 14. 答辩建议

答辩时可以按以下顺序演示：

1. 展示 GitHub 仓库和 README 顶部的 CI 徽章。
2. 展示 PR #1 和 PR #2，说明功能分支与代码审查流程。
3. 展示 Actions 中的 `Python checks`、`ROS2 Foxy build and test` 和 `Package Jetson deployment`。
4. 展示 14 个测试以及两个目标纯逻辑模块 100% 覆盖率。
5. 展示首轮失败日志和修复后成功记录，说明流水线能真正发现问题。
6. 下载并展示 Jetson 部署 Artifact 的内容。
7. 展示 `main` 分支保护，说明未通过检查的代码不能合并。

推荐表述：

> 项目使用 GitHub Actions 建立了持续集成和持续交付流水线。每次 Pull Request 自动执行 Python 检查、14 个单元测试、100% 目标覆盖率检查以及 ROS2 Foxy 构建测试；代码合并到受保护的 main 分支后，自动生成适用于 Jetson 的源码部署包。
