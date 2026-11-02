<h3 align="center">
  <img src="assets/Harness2-wordmark.png" alt="Harness²" width="360"><br>
  Recursive Agent Harnessing for an Open World
</h3>

<p align="center">
  <a href="https://arxiv.org/pdf/2610.14224"><img src="https://img.shields.io/badge/Paper-arXiv-EA4335?logo=arxiv&logoColor=white" alt="Paper"></a>
  <a href="https://harness2.github.io"><img src="https://img.shields.io/badge/Website-harness2.github.io-4285F4?logo=googlechrome&logoColor=white" alt="Website"></a>
  <a href="docs/BENCHMARKS.md"><img src="https://img.shields.io/badge/Docs-Benchmark%20guides-FBBC04" alt="Benchmark guides"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-34A853" alt="License"></a>
</p>

<p align="center">
  <a href="docs/BENCHMARKS.md#jobbench">
    <img src="assets/harness2-setup.gif" alt="Install, set up, and run JobBench: uv pip install -e '.[render]', harness2-setup jb, then harness2 --bench jb --k 1 --mode parallel. Click for prerequisites and commands." width="640">
  </a>
</p>

A **harness** is the system around a model that enables it to act as an agent:
prompts, tools, memory, skills, orchestration, and more. **Harnessing** is the
process of optimizing that system.

Harness² is a plug-and-play framework for **recursive agent harnessing** in an
open world, where agents encounter tasks unseen during harness design. It
connects two levels of recursion while keeping model weights fixed:

* **Task-level recursion** refines the execution harness for the current task
  through a contrastive **Propose–Probe–Compose** step: propose edits, probe
  their effects on the same task, and compose a refined harness from behavioral
  contrasts between executions, without ground-truth feedback.
* **Harnessing-level recursion** updates a persistent *improvement harness*
  with adopted harnesses and evidence-supported lessons from each task to guide
  future refinement.

<p align="center">
  <a href="assets/harness2_pipeline.png">
    <img src="assets/harness2_pipeline.png" alt="Harness² pipeline. Task-level recursion: an agent executor runs the initial harness, a harness editor proposes invariant and domain edits, candidate probes execute them, and a harness composer combines them into the final harness. Harnessing-level recursion: an experience reflector reflects on the step and updates the improvement harness used for the next task." width="100%">
  </a>
</p>

Across three professional benchmarks and four model–harness configurations, a
single Harness² step improves GLM-5 and Gemini 3.5 Flash over their base
harnesses, with gains of up to 11.5 points on WorkBuddy-Bench, 4.5 points on
JobBench, and 11.8 percentage points on LAB's partial pass rate. Increasing the
task-level recursion budget brings further gains.

The release supports **parallel and sequential recursion**, **OpenCode and
Codex executors**, and **JobBench, LAB, and WorkBuddy-Bench**.

## Video overview

https://github.com/user-attachments/assets/76b10548-b14b-4c8f-9aa5-a93662df4d56

## Contents

- [Video overview](#video-overview)
- [Harness components](#harness-components)
- [Robot extension / 机器人功能](#robot-extension--机器人功能)
- [Install](#install)
- [Run a benchmark](#run-a-benchmark)
- [Experiments](#experiments)
- [Web access](#web-access)
- [Results and resume](#results-and-resume)
- [Citation](#citation)
- [Contributing](#contributing)
- [License](#license)
- [Disclaimer](#disclaimer)

## Harness components

Harness² exposes eight editable components around the model: **system prompt,
guardrails, memory, tool guidance, subagents, skills, scripts, and plugins**.
They control context construction, the tool interface, delegation, and behavior
inside the tool loop.

<p align="center">
  <a href="assets/components.png">
    <img src="assets/components.png" alt="Eight harness components grouped by runtime role: system prompt, guardrails, memory, and skills construct context; tool guidance and scripts define the tool interface; subagents orchestrate delegated work; plugins control middleware." width="100%">
  </a>
</p>

## Robot extension / 机器人功能

### 项目定位

本仓库是在原始 Harness² 上增加的机器人任务扩展，不是独立的 Move Agent，也没有把
RPent 整体搬进来。它复用 Harness² 的递归改进、候选选择、经验库和规则评分能力，新增
了一个受约束的机器人执行循环：大模型负责理解中文指令和生成完整计划，可信 Python
代码负责校验计划、串行调用动作、处理取消与失败。

当前使用同一个 OpenAI Chat Completions 兼容模型完成机器人任务规划和 Harness 配置改进。
默认模型配置为远程 `Qwen3.5-27B`；连接参数从仓库根目录 `.env` 读取。

```text
用户中文指令
    → Qwen 生成完整动作计划
    → Harness² 校验工具、参数、顺序和最多五步限制
    → FastAPI 工具网关
    → 配置中的 move_robot 或 grasp_clothes 实现
    → 记录结构化结果，失败或状态未知时立即停止
```

### 当前能力

| 功能 | 实现 | 范围 |
|---|---|---|
| FastAPI 网关 | 对外提供健康检查、工具列表、执行和取消接口 | 实现由 JSON 配置加载 |
| 底盘移动 | 网关代理机器人 HTTP 服务的 `/health`、`/move`、`/cancel` | 仅预设位置 1、2、3 |
| 抓衣服 | 网关启动配置中的 RPent 进程 | 固定衣服工位、右臂、右夹爪 |
| 模拟执行 | `MockRobot` | 不连接真机，用于调试计划和回归测试 |
| Harness 改进 | 复用原始递归、选择、经验库和 Judge | 候选只允许修改 Markdown 配置 |

抓衣动作复用 RPent 已记录的 `home → prepare → grasp → 闭合右夹爪 → 持物回位`
关节序列，固定使用 `--home-mode direct --gripper-side right`。它不再调用原来的
AprilTag 抓取，也没有加入彩色方块、ACT、Pi0.5、放置或松开功能。

新鱼眼相机目前不参与抓取点计算。抓衣前必须确保底盘已经位于标定的衣服工位，衣服
摆放与记录点位一致；系统不会擅自追加导航动作，也不会声称已经通过视觉确认抓牢。

### 环境安装

项目使用独立 Conda 环境 `harness_test`：

```bash
conda activate harness_test
cd /home/tjzn-zwt/Aqqqcy/github/harness
```

该环境使用 Python 3.12，并以可编辑模式安装当前仓库。Harness/LLM 在
`harness_test` 中运行；RPent 抓衣子进程固定使用 ROS Humble 兼容的
`/usr/bin/python3`，避免 Conda Python 与 ROS 二进制扩展冲突。

机器人运行文件保存在项目自己的路径下：

```text
robot_runtime/
├── luna_ros_overlay/          # Luna marvin_msgs overlay
├── ros_deps/                  # 项目本地 CycloneDDS，无需 sudo
└── Apex_Deploy/               # 可选的 Luna Apex 工作区
```

这些编译产物不会提交到 Git。环境入口为
`harness2/robot/config/luna_ros_env.sh`，启动机械臂前会验证 ROS、CycloneDDS、
`marvin_msgs` 和抓衣脚本是否可用。

工具实现不写在 Harness 客户端中，而是由
`harness2/robot/config/robot_tools.json` 配置。FastAPI 网关提供通用 HTTP 与进程执行器；
`backend.py` 只负责调用网关。工具名称与参数范围仍由可信代码校验，不能通过接口绕过
位置白名单。该 JSON 能决定真机命令，属于可信本地配置，不能开放给前端或非受信用户编辑。

### 配置模型

本地密钥只写入 `.env`，不要提交到 Git：

```dotenv
ROBOT_LLM_BASE_URL=http://192.168.100.212:8000/v1
ROBOT_LLM_MODEL=Qwen3.5-27B
ROBOT_LLM_API_KEY=服务器实际密钥
ROBOT_LLM_TIMEOUT_S=300
```

检查模型连接，不连接机器人：

```bash
harness2-check-llm
```

### 使用方法

先在终端 1 启动本机 FastAPI 工具网关：

```bash
conda activate harness_test
cd /home/tjzn-zwt/Aqqqcy/github/harness
harness2-robot-api --host 127.0.0.1 --port 8090
```

接口文档位于 `http://127.0.0.1:8090/docs`。直接通过 FastAPI 调用抓衣工具：

```bash
curl -X POST http://127.0.0.1:8090/tools/grasp_clothes/execute \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{}}'
```

上面的接口会执行真实工具。只检查抓衣环境时使用：

```bash
curl http://127.0.0.1:8090/tools/grasp_clothes/health
```

默认是模拟机器人，即使 `.env` 中存在真机配置也不会自动连接：

```bash
harness2-robot run "请移动到2号位置"
harness2-robot run "抓住工位上的衣服"
harness2-robot run "先到2号位置，抓衣服，再到3号位置"
```

在终端 2 中，只有显式添加 `--real` 才会让 Harness 通过 FastAPI 提交真实动作：

```bash
harness2-robot run "抓住工位上的衣服" --real
```

运行真机前必须确认工作区无人、机器人已在正确工位、衣服摆放正确、急停可用。软件取消
不能替代物理急停；动作超时或子进程异常时，机械臂和夹爪状态按“未知”处理，不自动重试。

离线演示与测试：

```bash
harness2-robot demo --out runs/robot-demo-1
python -B -m unittest discover -s tests -q
```

### 主要代码

| 路径 | 作用 |
|---|---|
| `harness2/cli/robot.py` | 机器人命令行入口 |
| `harness2/robot/llm.py` | 唯一的 Chat Completions 模型连接 |
| `harness2/robot/tools.py` | 可信动作 Schema 和参数白名单 |
| `harness2/robot/runtime.py` | 完整计划校验、串行执行、失败停止和结果记录 |
| `harness2/robot/backend.py` | 模拟后端和 FastAPI 工具网关客户端 |
| `harness2/robot/api.py` | FastAPI 路由、认证和服务启动入口 |
| `harness2/robot/gateway.py` | 配置驱动的 HTTP/进程工具执行器 |
| `harness2/robot/config/robot_tools.json` | 移动与抓衣的具体实现配置 |
| `harness2/robot/profile/` | System Prompt、Guardrails、Memory、工具指导和 Skills |
| `harness2/robot/adapter.py` | Harness² 任务适配和独立规则评分 |
| `harness2/robot/config/luna_ros_env.sh` | 项目本地 Luna/ROS/CycloneDDS 环境 |
| `tests/test_robot.py` | 离线机器人集成回归测试 |

更完整的配置、评分和递归改进说明见 [机器人使用说明](docs/ROBOT.md)。

## Install

Use **Linux, Python 3.12, and [uv](https://docs.astral.sh/uv/getting-started/installation/)**.
From the repository root:

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e '.[render]'
harness2-demo --output runs/demo --k 2
```

The local demo runs both recursion modes with deterministic editors and real
Python executions. It requires no model credentials.

For model-backed benchmarks, install **Node.js 20+, the Google Cloud CLI**, and
the [benchmark dependencies](docs/SETUP.md#requirements). LAB needs Pandoc and
Poppler; WorkBuddy-Bench needs Docker. Then configure your models:

```bash
cp .env.example .env
# Edit .env with your Vertex AI project and model IDs.
gcloud auth application-default login
```

See the [setup guide](docs/SETUP.md) for model settings, authentication
alternatives, and the [Codex executor](docs/SETUP.md#codex-executor).

## Run a benchmark

The following example installs LAB and runs one task, then evaluates and reports
its result. Model-backed runs incur API costs; start with a small task selection.

```bash
harness2-setup lab
harness2-check --bench lab
harness2 --bench lab --k 1 --mode parallel \
  --only-domains antitrust-competition --max-tasks 1
harness2-judge --bench lab --k 1 --mode parallel
harness2-report --bench lab --k 1 --mode parallel
```

Sequential recursion runs the same commands with `--mode sequential`. `--k` is
the task-level recursion budget; see [Experiments](#experiments). Judging and
reporting must use the same benchmark, executor, subset, `--k`, `--mode`, and
seed as the refinement run. Add `--dry-run` to `harness2` to preview task
selection without model calls.

| Benchmark | Tasks | Install | Guide |
|---|---|---|---|
| JobBench | Professional work | `harness2-setup jb` | [JobBench](docs/BENCHMARKS.md#jobbench) |
| LAB | Legal work | `harness2-setup lab` | [LAB](docs/BENCHMARKS.md#lab) |
| WorkBuddy-Bench | Office, web, and code | `harness2-setup wb` | [WorkBuddy](docs/BENCHMARKS.md#workbuddy-bench) |

Choose one benchmark per command. WorkBuddy also takes `--subset office`,
`--subset web`, or `--subset code` on every command after setup. The
[benchmark guides](docs/BENCHMARKS.md) cover each one's data, domains, and
grading.

## Experiments

Scale task-level recursion in two ways: **parallel recursion** explores multiple
candidate harnesses and composes refinements from their execution evidence;
**sequential recursion** carries the refined harness and accumulated evidence
through successive Propose–Probe–Compose steps.

<p align="center">
  <a href="assets/harness2_recursion_modes.png">
    <img src="assets/harness2_recursion_modes.png" alt="Two ways to scale task-level recursion. Parallel: probe k general and k domain-specific candidate harnesses, then produce k compositions from the shared evidence. Sequential: carry the refined harness and accumulated evidence through k Propose–Probe–Compose steps." width="100%">
  </a>
</p>
<p align="center"><em>Parallel recursion explores wider; sequential recursion refines deeper.</em></p>

| Option | Behavior |
|---|---|
| `--k 1` | One Propose–Probe–Compose step |
| `--k K --mode parallel` | Probe `K` general and `K` domain-specific candidates, then produce `K` compositions |
| `--k K --mode sequential` | Refine the harness through `K` successive steps |
| `--substrate cx` | Use Codex instead of the default OpenCode executor |

After each task, harnessing-level recursion retains the adopted harness and
evidence-supported lessons to guide future refinement. See the
[experiment guide](docs/EXPERIMENTS.md) for task selection, concurrency,
reproducibility, and baseline comparisons.

## Web access

Executor network access is unrestricted, and Codex runs with its sandbox
disabled. Public benchmark rubrics may be accessible online. Before evaluation,
configure [network restrictions](docs/EXPERIMENTS.md#web-access) to prevent
access to benchmark answers and grading criteria.

## Results and resume

Results are saved under `runs/`, organized by benchmark and subset where
applicable. They include task summaries, deliverables, adopted harnesses,
reflection notes, and evaluation results.

Repeat the same command to resume. Use a new `HARNESS2_RUNS_DIR` when changing
models or task selection. See [results and resume](docs/EXPERIMENTS.md#results-and-resume)
for details; each command also supports `--help`.

## Citation

```bibtex
@article{xu2026harness2,
  title   = {Harness$^2$: Recursive Agent Harnessing for an Open World},
  author  = {Xu, Ruiyao and Chen, Yanfei and CuiZhu, Zhongying and Dalvi Mishra, Bhavana and Ming, Yifei and Yu, Han and Han, Rujun and Lee, Chen-Yu and Pfister, Tomas},
  journal = {arXiv preprint arXiv:2610.14224},
  year    = {2026},
  url     = {https://arxiv.org/abs/2610.14224}
}
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Harness² is licensed under [Apache 2.0](LICENSE). Third-party code under
[`third_party/`](third_party/README.md), benchmarks, and datasets retain their
upstream licenses; see
[third-party notices](dependencies/README.md).

## Disclaimer

This is not an officially supported Google product. This project is not eligible
for the [Google Open Source Software Vulnerability Rewards Program](https://bughunters.google.com/open-source-security).
