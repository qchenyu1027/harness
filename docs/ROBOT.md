# Harness² 机器人扩展：单模型、移动/抓取、自动改进与规则评分

本扩展直接位于 Harness² 项目中，运行不导入 Move Agent。移动、抓取和必要 HTTP 通信已独立实现；机器人电脑上的原执行服务、运动脚本及标定仍需保留。

## 1. 本版采用的连接方式

新增原生 Chat Completions HTTP 执行器，使用同一份模型配置执行用户任务和改进候选配置。复用仓库原有 `core/recursion.py`、`selection.py`、`library.py`、`judge.py` 与 `report.py`。

不需要为机器人扩展安装 OpenCode、Bun、Google Cloud 或业务基准数据集。原有业务基准命令保持原实现。

这是集成方案中 OpenCode 插件路线的轻量实现替代：机器人执行循环使用 HTTP 原生工具调用，改进会话使用受限文件工具。复用 OpenCode 风格的 Markdown 组件格式，但不会启动 OpenCode，也不执行候选脚本或插件。首版将两份技能说明整体加载到执行会话。

执行前先通过内部 `plan_robot_task` 提交完整计划，可信代码校验所有步骤，再逐轮调用 `move_robot`、`grasp_object`。计划无效、超限、工具偏离计划、失败或未知结果时停止。

## 2. 环境与安装

使用 Python 3.12 或 3.13。在源码目录可以直接运行，无须先安装 Python 依赖：

```bash
cd /home/yang/Desktop/agent/harness
python3 -m harness2.cli.robot --help
```

也可以安装命令行入口：

```bash
python3 -m pip install -e .
harness2-robot --help
```

以下示例使用 `python3 -m harness2.cli.robot`，安装后的 `harness2-robot` 与之等价。

## 3. 不连接 LLM 的预设演示

```bash
python3 -m harness2.cli.robot demo --out runs/robot-demo-1
```

必须使用尚不存在的演示输出目录。演示人为注入一个“默认抓取重复调用”的错误，再用预设编辑器补充规则；真实 Harness² 核心完成提案、试跑、组合、经验保存与独立评分。

该固定演示通常显示基线控制逻辑 70 分、候选 100 分。这个差值来自预设行为，不是实际 LLM 或机器人的性能提升，也不能用于论文效果对比。

加深串行改进的示例：

```bash
python3 -m harness2.cli.robot demo --k 2 --mode sequential --out runs/robot-demo-seq-1
```

## 4. 配置唯一的 LLM

在仓库根目录创建 `.env`。没有该文件时可以复制 `.env.example`；已有文件时编辑其中的机器人变量，不覆盖既有配置。

```dotenv
ROBOT_LLM_BASE_URL=http://10.121.40.31:8000/v1
ROBOT_LLM_MODEL=Qwen3.5-9B
ROBOT_LLM_API_KEY=
ROBOT_LLM_TIMEOUT_S=90
```

这组值是原项目的默认设置，服务地址或模型改变时请更新。API Key 仅在服务要求时填写，不提交到 Git。shell 环境变量优先于 `.env`；也兼容旧名称 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` 作为回退。

连接代码在 `harness2/robot/llm.py`。执行器和 `HttpProposer` 使用同一个 `ChatModel`，通过独立会话承担不同角色。机器人评分由规则程序完成，不需要额外的 Judge 模型。

只检查 LLM，不连接机器人：

```bash
python3 -m harness2.cli.robot check
```

模型服务必须支持 Chat Completions 的结构化 function tool_calls。普通问答连通只能证明基础接口可用，还需用模拟动作验证工具调用。

## 5. 用真实 LLM 控制模拟机器人

默认后端始终为模拟机器人：

```bash
python3 -m harness2.cli.robot run "先到2号位置，抓取标签5，再到3号位置"
```

其他示例：

```bash
python3 -m harness2.cli.robot run "请移动到2号位置"
python3 -m harness2.cli.robot run "抓住这个东西"
python3 -m harness2.cli.robot run "怎么先移动再抓取？"
```

移动仅支持1、2、3；抓取仅支持标签3、5，默认5。抓取仍为现有左臂抓取并抬起流程，不提供放置、松开、任意物体识别或右腕夹爪适配。

每次命令使用新的执行器和取消状态。最多五个动作，先验证完整计划，按计划每轮仅调用一个动作工具；动作完成后由可信代码结束任务，不再向模型请求额外动作。

`Ctrl+C` 阻止后续动作；已有动作提交时也请求执行端取消。取消请求已提交不等于机器人已经停稳，仍需确认执行端状态。

运行结果保存在输出目录 `result.json` 中。可以使用 `--out <目录>` 指定路径。

## 6. 模拟任务的独立规则评分

运行默认五个场景：

```bash
python3 -m harness2.cli.robot evaluate --out runs/robot-eval-1
```

运行全部十三个场景：

```bash
python3 -m harness2.cli.robot evaluate --cases all --out runs/robot-eval-all-1
```

场景包括单步、三步、五步、反向顺序、咨询、歧义、不支持目标、超限、健康检查失败、动作失败、超时和取消。部分场景的故障发生在模拟后端。

十个二值检查项为：意图、计划目标、提交顺序、无额外动作、串行、五步预算、健康检查门控、失败停止、取消门控和结构化结果状态。每通过一项计10分，属于控制逻辑评分。它不对任意自然语言答案的全部事实进行语义评审。

实物任务成功状态保持 `task_success=null`；`simulated_plan_completed` 只表示模拟执行完成确认计划。控制逻辑100分不代表实机抓取已验证。

输出 `judged.json` 包含每项检查及 `critical_violation`。规则可判定的任务失败给出实际分数；评分证据缺失则未评分，不当作零分。未评分和完整任务数量要同时查看。

对某个已经保存的模拟结果重新评分：

```bash
python3 -m harness2.cli.robot score /实际的模拟任务输出目录 --case serial
```

不要把自选场景的评分当作对任意任务的通用语义认证；`--case` 应与该执行记录对应的预定义场景一致。

## 7. 自动修改 Prompt、Skills 和工具指导

先用一个默认抓取场景、一次改进验证流程：

```bash
python3 -m harness2.cli.robot refine --cases grasp --k 1 --out runs/robot-refine-1
```

增加任务或预算：

```bash
python3 -m harness2.cli.robot refine --cases move,grasp,serial --k 2 --mode sequential --out runs/robot-refine-seq-1
```

该命令使用真实 LLM 和模拟机器人，会产生模型调用费用。它复用原有 Harness² 调度与经验库；不是从评分器取答案后改提示词。API 请求不自动重试；原有核心仍可能根据候选文件校验错误要求修正一次。

每个候选可修改的内容：

- `systemprompt.md`
- `guardrails.md`
- `LongTermMEMORY.md`
- `tool_descriptions/tool_guidance.md`
- `skills/<name>/SKILL.md`

执行代码、工具参数白名单、认证、动作次数、健康检查及停止机制不可编辑。新增技能文字不能创造新的机器人控制能力。改进侧不能执行 shell、插件或机器人动作，只能通过受限文件工具读取 `context/`，编辑其中的候选配置。

单模型文件会话有轮数、单文件大小和总配置大小限制。无法提出有效候选时回退会记录在报告中；CLI 对这种不完整改进返回非零状态，避免误报全部成功。

输出目录中包含：

- `adapt/`：原生 Harness² 任务摘要、候选配置、执行证据和选择记录。
- `library/`：有支持证据的经验及配置组件；无经验条目时不强行生成。
- `improver-audit/`：模型改进会话的任务、回答和候选文件。
- `executions/`：模拟执行记录及回答。
- `base_judged.json`、`judged.json`：独立规则评分。
- `report.txt`：基线与候选的控制逻辑分。
- `robot-summary.json`：任务、评分覆盖和回退情况。

候选选择不等于验证优于基线。独立分数在选择后生成，不进入改进会话。费用尚未计量；框架内部默认费用数值为0不能解释为免费。

输出目录通过 `robot-config.json` 绑定模型、端点、基础配置、任务集合、预算和模式。改变这些设置应使用新目录。相同配置可利用原有调度恢复已完成任务。

候选不会自动覆盖包内基础配置，也不会自动发布到真机。检查选择记录找到采用的候选 `harnesses/<label>/` 后，可用 `run --profile <候选目录>` 加载固定版本。

## 8. 显式连接真实机器人

仅 `run --real` 支持真机。`demo`、`evaluate`、`refine` 没有真机选项，也不会因为存在机器人密钥就切换后端。

配置实际机器人服务：

```dotenv
ROBOT_API_BASE=http://机器人服务IP:端口
ROBOT_API_KEY=在本机填写实际密钥
```

真实动作命令示例：

```bash
python3 -m harness2.cli.robot run "移动到2号位置" --real
```

这个命令会提交实际动作，仅在需要执行该动作、现场条件满足时使用。本次开发验证不会执行它。

保留原服务 `/health`、`/move`、`/grasp`、`/cancel`。抓取标定由可信代码自动选择。超时、连接异常或响应无效后，动作结果可能未知，停止后续步骤且不重试。

软件取消不等同于设备急停；当前扩展也没有独立实物验证器。原执行端尚未适配的机械臂或夹爪，不会因本次代码迁移自动可用。

## 9. 代码位置与测试

- `harness2/cli/robot.py`：统一命令入口。
- `harness2/robot/llm.py`：唯一模型连接。
- `harness2/robot/runtime.py`：完整计划校验、串行执行、取消与停止。
- `harness2/robot/backend.py`：模拟机器人和独立的真实HTTP客户端。
- `harness2/robot/tools.py`：可信动作参数及 Schema。
- `harness2/robot/proposer.py`：单模型候选编辑会话。
- `harness2/robot/adapter.py`：原生 Harness² 任务适配与规则评分。
- `harness2/robot/profile/`：实际使用的 Prompt、Skills 和工具指导。
- `tests/test_robot.py`：完全离线的机器人集成回归。

```bash
python3 -B -m unittest discover -s tests -q
```

预设演示和离线测试不证明真实模型连通或真机成功。后两者需分别验证，报告时不要混淆。
