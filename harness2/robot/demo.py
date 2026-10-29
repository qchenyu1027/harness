"""Deterministic demo only: deliberately scripted behavior, never an LLM substitute."""
import json
from pathlib import Path

from ..core.proposer import Proposer, Reply
from ..core.spec import ChannelSpec
from .adapter import CASES
from .llm import ModelSettings

DEMO_RULE = "默认抓取任务只允许执行一次抓取。"


def tool_reply(name, arguments):
    return {"content": "", "tool_calls": [{"id": "demo", "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments, ensure_ascii=False),
    }}]}


class DemoModel:
    """Known fixture behavior; the demo injects one duplication bug when requested."""
    settings = ModelSettings("offline://demo", "offline-demo")

    def __init__(self, inject_bug=False):
        self.inject_bug = inject_bug

    def complete(self, messages, tools=None, **kwargs):
        if tools[0]["function"]["name"] == "plan_robot_task":
            request = next(m["content"] for m in messages if m["role"] == "user")
            case = next(case for case in CASES.values() if case.prompt == request)
            steps = list(case.plan)
            if self.inject_bug and request == CASES["grasp"].prompt and DEMO_RULE not in messages[0]["content"]:
                steps = steps * 2
            return tool_reply("plan_robot_task", {
                "mode": case.intent, "steps": steps,
                "message": "请明确受支持的目标和步骤" if case.intent == "clarify" else "按先移动后抓取的流程操作",
            })
        plan = next(json.loads(m["tool_calls"][0]["function"]["arguments"])
                    for m in messages if m["role"] == "assistant" and m.get("tool_calls"))
        completed = sum(m["role"] == "tool" and m.get("tool_call_id", "").startswith("action-")
                        for m in messages)
        step = plan["steps"][completed]
        return tool_reply(step["tool"], step["arguments"])


class DemoImprover(Proposer):
    """A scripted patch drives the real Harness² orchestration in the offline demo."""
    def __init__(self):
        super().__init__(ChannelSpec("chat_completions", "offline-demo"))

    def call(self, mission, context_dir, label, **kwargs):
        root = Path(context_dir)
        workspace = None
        if label.startswith("experience."):
            text = (
                "<edits>\ncomponent: systemprompt.md\nchange: 明确默认抓取只执行一次\n"
                "observed: 预设演示中抓取调用由两次变为一次\nverdict: helped\n</edits>\n"
                "<experience>\ncue: 单次默认抓取请求\nlesson: 在systemprompt.md明确单次调用\n"
                "evidence: 演示轨迹显示两次调用变为一次\nconfidence: 仅为预设演示\n</experience>"
            )
        elif label.startswith("select."):
            candidate = next(iter(sorted((root / "candidates").glob("*.md"))))
            text = f"<why>预设演示选择</why>\n```choice\n{candidate.stem}\n```"
        else:
            workspace = root / "harness"
            path = workspace / "systemprompt.md"
            body = path.read_text()
            if DEMO_RULE not in body:
                path.write_text(body + "\n" + DEMO_RULE + "\n", encoding="utf-8")
            text = "<notes>明确默认抓取只调用一次，修正演示轨迹中的重复动作。</notes>"
        result = Reply(label=label, text=text, ok=True, attempts=1, workspace=workspace)
        self._calls.append(result)
        return result
