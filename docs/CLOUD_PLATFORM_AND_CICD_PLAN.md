# GitHub 云平台集成与 CI/CD 实施指南

## 1. 任务范围

本任务统一在 GitHub 上完成，不再接入太乙平台，也不实现 MQTT、车辆遥测上云或远程控制。

GitHub 在本任务中承担以下职责：

- 云端保存项目代码和版本历史。
- 使用分支和 Pull Request 进行团队协作与代码审查。
- 使用 GitHub Actions 自动执行代码检查、测试和 ROS2 构建。
- 保存每次流水线的日志和测试结果，作为课程验收材料。
- 可选：构建成功后生成 Artifact 或 Release，供 Jetson 下载部署。

需要说明：GitHub 是代码托管与研发协作平台，不是车辆运行数据平台。本方案中的“云平台集成”指项目代码、协作流程和构建结果在 GitHub 云端统一管理，不包含车辆电压、位置、雷达等实时数据的云端存储。

## 2. 最终目标

完成后应形成以下流程：

```text
本地创建功能分支
    -> 修改代码并运行本地测试
    -> push 到 GitHub
    -> 创建 Pull Request
    -> GitHub Actions 自动检查、测试和构建
    -> CI 通过并完成代码审查
    -> 合并到 main
    -> 可选：生成构建产物或 Release
```

最小验收标准：

- 项目代码已托管在 GitHub。
- push 和 Pull Request 可以自动触发流水线。
- 流水线至少包含 Python 检查、单元测试和 ROS2 软件包构建。
- 测试失败时禁止合并，修复后流水线恢复通过。
- GitHub Actions 页面保留可查看的执行日志。

## 3. 当前仓库情况

当前仓库地址：

```text
https://github.com/Lisvu/BJTU-smartcar.git
```

当前项目基础：

- 主分支为 `main`。
- 车辆运行环境为 Ubuntu 20.04、Jetson ARM64 和 ROS2 Foxy。
- `bjtu_ros2_ws/src/bjtu_comm` 是较小的 Python ROS2 包，适合作为第一阶段 CI 构建目标。
- 仓库中已有部分 ROS2 自动生成的 `ament_flake8`、`ament_pep257` 和版权测试。
- `yahboomcar_ws` 和 `software/library_ws` 包含大量原厂代码、硬件驱动和第三方依赖。
- 当前尚无 `.github/workflows/`，即尚未配置 GitHub Actions。

第一阶段不要全量构建所有工作空间。摄像头、雷达、串口、Jetson 驱动和旧版第三方依赖在 GitHub 托管的 x86 Linux 环境中很容易失败。先让 `bjtu_comm` 的流水线稳定通过，再逐步扩大范围。

## 4. 第一步：规范 GitHub 协作方式

### 4.1 分支约定

保留 `main` 作为稳定分支，开发工作在功能分支完成。

分支命名示例：

```text
feature/github-actions
feature/cloud-bridge
fix/battery-monitor
docs/deployment-guide
```

日常开发流程：

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/github-actions

# 修改并测试代码

git add <需要提交的文件>
git commit -m "Add ROS2 CI workflow"
git push -u origin feature/github-actions
```

然后在 GitHub 创建 Pull Request，不直接在 `main` 上堆积未经验证的修改。

### 4.2 Commit 约定

提交应小而明确，一次提交只处理一个主题。推荐使用以下前缀：

```text
feat: 新功能
fix: 修复问题
test: 增加或修改测试
ci: 修改流水线
docs: 修改文档
refactor: 重构但不改变行为
```

示例：

```text
ci: add ROS2 Foxy build workflow
test: cover telemetry serialization
docs: document GitHub development process
```

## 5. 第二步：确定 CI 检查范围

第一版流水线建议包含两个 Job。

### 5.1 Python Checks

负责快速反馈，不依赖真实车辆：

- 使用 `python -m compileall` 检查 Python 语法。
- 使用 `pytest` 执行纯 Python 单元测试。
- 可选使用 `flake8` 检查新增的自研代码。
- 检查仓库中是否误提交密钥或构建产物。

不要一开始对整个历史仓库启用严格格式检查。原厂代码可能存在大量历史问题，会使团队无法判断新提交是否正确。静态检查应先限定到 `bjtu_comm` 或本次新增文件。

### 5.2 ROS2 Build And Test

负责验证 ROS2 软件包：

- 在 ROS2 Foxy Docker 容器中运行。
- 使用 `colcon build --packages-select bjtu_comm` 构建目标包。
- 使用 `colcon test --packages-select bjtu_comm` 运行测试。
- 使用 `colcon test-result --verbose` 输出失败原因。

ROS2 Foxy 已结束官方维护，而且 GitHub 的 `ubuntu-latest` 不能直接等同于 Ubuntu 20.04。使用固定 ROS2 Foxy 容器比直接在 runner 中安装 Foxy 更稳定。

## 6. 第三步：补充可自动执行的测试

CI 只有在存在有效测试时才有价值。优先给自研且不依赖硬件的逻辑编写测试。

推荐测试内容：

- 输入数据转换和 JSON 序列化。
- 参数默认值与非法参数处理。
- 电压等级判断。
- 雷达数据中的 `NaN`、`Inf`、0 和超量程值过滤。
- Web API 中不依赖 ROS2 和摄像头的纯函数。
- 配置文件读取失败时的错误处理。

不适合在普通 GitHub Actions 中测试：

- Jetson GPIO、串口和电机。
- 实际摄像头 `/dev/video0`。
- 实际激光雷达。
- 依赖 NVIDIA L4T/CUDA 的节点。
- 真实车辆运动效果。

这些功能应在真车上进行集成测试，并把测试步骤和结果写入文档。

测试文件建议放在：

```text
bjtu_ros2_ws/src/bjtu_comm/test/
```

执行本地测试：

```bash
source /opt/ros/foxy/setup.bash
cd bjtu_ros2_ws
colcon build --packages-select bjtu_comm
colcon test --packages-select bjtu_comm
colcon test-result --verbose
```

## 7. 第四步：创建 GitHub Actions 工作流

创建文件：

```text
.github/workflows/ci.yml
```

推荐的第一版工作流如下：

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  python-checks:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.8"

      - name: Install test dependencies
        run: python -m pip install --upgrade pip pytest

      - name: Check Python syntax
        run: python -m compileall -q bjtu_ros2_ws/src/bjtu_comm

      - name: Run unit tests
        run: python -m pytest bjtu_ros2_ws/src/bjtu_comm/test -v

  ros2-build-and-test:
    runs-on: ubuntu-latest
    container:
      image: osrf/ros:foxy-desktop
    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Install build tools
        run: |
          apt-get update
          apt-get install -y python3-colcon-common-extensions python3-pytest

      - name: Build package
        shell: bash
        run: |
          source /opt/ros/foxy/setup.bash
          cd bjtu_ros2_ws
          colcon build --packages-select bjtu_comm --event-handlers console_direct+

      - name: Test package
        shell: bash
        run: |
          source /opt/ros/foxy/setup.bash
          cd bjtu_ros2_ws
          colcon test --packages-select bjtu_comm --event-handlers console_direct+
          colcon test-result --verbose
```

注意事项：

- 这是实施起点，必须以 GitHub Actions 实际运行结果为准。
- 如果测试直接导入 `rclpy`，普通 Python Job 可能无法运行，应将纯逻辑与 ROS2 Node 分离，或只在 ROS2 Job 中执行该测试。
- `osrf/ros:foxy-desktop` 镜像或其软件源未来可能失效，应尽量固定经过验证的镜像摘要或建立项目自己的 CI 镜像。
- 新增 Python 依赖后，要同步更新 `package.xml`、`setup.py` 或依赖文件以及 CI 安装步骤。

## 8. 第五步：首次运行和修复流水线

1. 在 `feature/github-actions` 分支提交 `.github/workflows/ci.yml`。
2. push 到 GitHub 并创建 Pull Request。
3. 打开 GitHub 仓库的 `Actions` 页面。
4. 确认 `python-checks` 和 `ros2-build-and-test` 均被触发。
5. 如果失败，展开失败步骤，定位第一条真实错误。
6. 在同一功能分支修复并再次 push，流水线会自动重跑。
7. 两个 Job 均变绿后再合并 Pull Request。

常见失败原因：

- Docker 镜像中缺少 `colcon` 或测试依赖。
- `package.xml` 未声明依赖。
- Python 测试在非 ROS 环境中导入 `rclpy`。
- 自动生成的版权测试因文件头缺失而失败。
- 原厂代码不符合 `flake8` 或 `pep257`。
- 工作目录写错，导致找不到 ROS2 软件包。

修复原则是缩小检查范围并补齐真实依赖，不要用 `|| true`、跳过失败步骤或删除有效测试来制造“绿色流水线”。

## 9. 第六步：配置分支保护

流水线稳定后，在 GitHub 设置 `main` 保护规则：

1. 打开仓库 `Settings`。
2. 进入 `Branches` 或 `Rules -> Rulesets`。
3. 为 `main` 创建规则。
4. 启用合并前必须创建 Pull Request。
5. 启用合并前必须通过状态检查。
6. 将 `python-checks` 和 `ros2-build-and-test` 设为必需检查。
7. 建议至少要求一名队友审查。
8. 禁止强制推送和删除 `main`。

如果团队目前都直接向 `main` 推送，可以先只要求 CI 通过，团队适应 Pull Request 后再启用严格保护。

## 10. 第七步：实现 CD

对本项目而言，CD 不应理解为 GitHub Actions 直接远程驾驶车辆，而应理解为自动生成可部署版本。

### 10.1 推荐方案：发布构建产物

当代码合并到 `main` 或创建版本标签后：

1. GitHub Actions 自动完成构建和测试。
2. 将自研脚本、配置和部署说明打包。
3. 使用 `actions/upload-artifact` 保存短期构建产物。
4. 创建版本标签时使用 GitHub Release 保存正式版本。
5. Jetson 由团队成员下载经过验证的版本并部署。

不要上传 ROS2 的整个 `build/`、`install/` 和 `log/`。GitHub runner 是 x86_64，而 Jetson 是 ARM64，runner 构建的本地二进制通常不能直接在 Jetson 上运行。Release 应优先包含源码、Python 脚本、配置和部署脚本。

### 10.2 可选方案：Jetson Self-hosted Runner

如果课程明确要求自动部署，可以在 Jetson 上配置 GitHub self-hosted runner：

- 只允许 tag 或手动审批后运行部署 Job。
- 使用专用低权限 Linux 用户。
- 不允许来自 fork 的 Pull Request 执行部署。
- 部署前备份当前版本。
- 部署后执行健康检查。
- 健康检查失败时回滚。
- 不通过公网暴露车辆控制端口。

第一阶段不建议实现此方案。先完成稳定 CI 和 Release，已经足以体现 CI/CD 工程流程。

## 11. 安全和仓库规范

必须确保以下内容不进入 GitHub：

- SSH 私钥和密码。
- GitHub Personal Access Token。
- 车辆热点密码。
- 服务器登录凭据。
- `.env` 和包含真实凭据的配置文件。
- ROS2 的 `build/`、`install/`、`log/`。
- 大模型权重、数据集和大体积运行日志。

需要秘密参数时，进入：

```text
GitHub repository -> Settings -> Secrets and variables -> Actions
```

使用 Repository Secret，并在工作流中通过 `${{ secrets.NAME }}` 引用。不要把 Secret 输出到日志，也不要让来自 fork 的不可信代码获得部署密钥。

提交前检查：

```bash
git status
git diff --cached
git grep -n -I -E 'password|secret|token|BEGIN.*PRIVATE KEY'
```

命中变量名和示例占位符不一定有问题，但必须人工确认没有真实凭据。

## 12. README 应补充的内容

CI 完成后，在根目录 `README.md` 增加：

- GitHub Actions 状态徽章。
- 仓库目录说明。
- 支持的 Ubuntu、ROS2 和 Python 版本。
- 依赖安装方法。
- 本地构建和测试命令。
- 真车部署方法。
- 硬件测试与自动测试的边界。
- 常见故障排查。

状态徽章格式示例：

```markdown
[![CI](https://github.com/Lisvu/BJTU-smartcar/actions/workflows/ci.yml/badge.svg)](https://github.com/Lisvu/BJTU-smartcar/actions/workflows/ci.yml)
```

## 13. 验收清单

### GitHub 云端管理

- [ ] 项目代码已完整托管在 GitHub。
- [ ] `main` 是稳定分支。
- [ ] 开发工作通过功能分支和 Pull Request 完成。
- [ ] README 包含构建、测试和部署说明。
- [ ] 仓库中不存在密钥、构建目录和大体积运行文件。

### CI

- [ ] `.github/workflows/ci.yml` 已提交。
- [ ] push 到 `main` 会自动触发流水线。
- [ ] Pull Request 会自动触发流水线。
- [ ] 支持从 Actions 页面手动触发。
- [ ] Python 语法检查自动执行。
- [ ] 单元测试自动执行。
- [ ] `bjtu_comm` 在 ROS2 Foxy 容器中自动构建。
- [ ] `colcon test-result --verbose` 能正确报告测试结果。
- [ ] 故意制造测试失败时，GitHub Actions 会显示失败。
- [ ] 修复错误后，流水线恢复绿色。

### CD

- [ ] 构建成功后可生成 Artifact，或创建 tag 后生成 Release。
- [ ] 发布产物不包含不能在 Jetson ARM64 上使用的 x86 二进制。
- [ ] 部署步骤有文档记录。
- [ ] 若使用 self-hosted runner，部署需要审批且支持回滚。

### 团队规范

- [ ] `main` 已配置分支保护。
- [ ] 必需状态检查已启用。
- [ ] Pull Request 中能看到 CI 结果。
- [ ] 至少保留一次“测试失败 -> 修复 -> 测试通过”的记录作为答辩证据。

## 14. 推荐执行日程

### 第 1 天：建立 CI 最小闭环

- 创建功能分支。
- 添加 `.github/workflows/ci.yml`。
- 只构建 `bjtu_comm`。
- 修复流水线环境和依赖问题，直到两个 Job 通过。

### 第 2 天：增加有效测试

- 将可测试的纯逻辑与 ROS2、硬件操作分离。
- 为自研功能增加 `pytest` 测试。
- 验证失败测试确实会阻止流水线通过。
- 补充本地测试命令。

### 第 3 天：工程规范

- 建立 Pull Request 模板。
- 配置 `main` 分支保护。
- 补充 README 和 CI 状态徽章。
- 清理误提交风险并检查 `.gitignore`。

### 第 4 天：CD 和答辩材料

- 配置 Artifact 或 GitHub Release。
- 在 Jetson 上验证发布版本的部署步骤。
- 保存 Actions 成功记录和 Pull Request 审查记录。
- 准备架构图、流水线图和失败后修复的演示。

## 15. 最终交付物

最低交付内容：

```text
.github/workflows/ci.yml
bjtu_ros2_ws/src/bjtu_comm/test/<新增测试文件>
.gitignore
README.md
docs/CLOUD_PLATFORM_AND_CICD_PLAN.md
```

可选增强内容：

```text
.github/pull_request_template.md
.github/workflows/release.yml
scripts/package_release.sh
docs/DEPLOYMENT.md
```

最终评价重点应是：代码在 GitHub 中有规范的版本管理流程，每次 push 或 Pull Request 都会自动检查、测试和构建，失败可追踪，成功结果可复现，并能生成清晰的发布或部署材料。
