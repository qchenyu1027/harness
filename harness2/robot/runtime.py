"""Bounded task execution with whole-plan validation before any physical request."""
import hashlib
import json
from pathlib import Path
import re
import threading

from ..core import tree as trees
from ..substrate.opencode import OPENCODE_SURFACE
from .llm import assistant_call
from .tools import ACTION_TOOLS, MAX_ACTIONS, PLAN_TOOL, normalize_action, normalize_plan, one_call

DEFAULT_PROFILE = Path(__file__).with_name("profile")
CORE_FILES = set(OPENCODE_SURFACE.always_on)
REQUIRED_SKILLS = {f"skills/{name}/SKILL.md" for name in ("robot-move", "robot-grasp")}


def editable(path):
    return path in CORE_FILES or bool(re.fullmatch(r"skills/[a-z0-9][a-z0-9_-]*/SKILL\.md", path))


def validate_profile(tree):
    if not CORE_FILES.union(REQUIRED_SKILLS).issubset(tree):
        raise ValueError("机器人配置必须保留四个常驻文件和移动、抓取技能")
    if any(not editable(path) for path in tree):
        raise ValueError("机器人改进只允许Markdown配置；不能编辑脚本、插件或执行代码")
    if sum(len(body) for body in tree.values()) > 100_000:
        raise ValueError("机器人配置超过100000字符")
    trees.validate(OPENCODE_SURFACE, tree, allow_subagents=False)


def read_profile(root=DEFAULT_PROFILE):
    rejected = []
    tree = trees.read_tree(OPENCODE_SURFACE, root, rejected=rejected)
    if rejected:
        raise ValueError("配置中有不允许的文件：" + ", ".join(rejected[:5]))
    validate_profile(tree)
    return tree


def fingerprint(tree):
    return hashlib.sha256(json.dumps(tree, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class RobotExecutor:
    def __init__(self, model, backend, profile=DEFAULT_PROFILE, cancel_event=None):
        self.model, self.backend = model, backend
        self.profile = read_profile(profile)
        self.cancel_event = cancel_event or backend.cancel_event
        self.actions = []
        self.events = []
        self.lock = threading.Lock()
        self.stopped = False

    def event(self, kind, **data):
        self.events.append({"event": kind, **data})

    def invoke(self, action):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("禁止并行动作")
        try:
            if self.stopped or self.cancel_event.is_set():
                return {"ok": False, "cancelled": self.cancel_event.is_set(), "message": "任务已停止"}
            if len(self.actions) >= MAX_ACTIONS:
                self.stopped = True
                return {"ok": False, "message": "超过五步预算"}
            action = normalize_action(action["tool"], action["arguments"])
            health = self.backend.health()
            self.event("health", result=health)
            if self.cancel_event.is_set():
                self.stopped = True
                return {"ok": False, "cancelled": True, "message": "任务已取消，未提交动作"}
            if (health.get("http_status") != 200 or health.get("ok") is not True
                    or health.get("ready_for_motion") is not True):
                self.stopped = True
                return {"ok": False, "message": "机器人未就绪，未提交动作"}
            entry = {**action, "result": {"ok": False, "result_unknown": True}}
            self.actions.append(entry)
            self.event("action_started", action=action)
            try:
                result = self.backend.submit(action)
            except Exception:
                result = {"ok": False, "result_unknown": True, "message": "执行端异常，动作结果未知"}
            if not isinstance(result, dict):
                result = {"ok": False, "result_unknown": True, "message": "动作响应无效"}
            entry["result"] = result
            self.event("action_finished", action=action, result=result)
            if result.get("ok") is not True or result.get("result_unknown") or result.get("cancelled"):
                self.stopped = True
            return result
        finally:
            self.lock.release()

    def run(self, request):
        plan = None
        if self.events or self.actions:
            raise RuntimeError("执行器实例只能运行一次任务")
        self.event("task_started", request=request)
        policy = (
            "你是机器人任务执行器。首先仅可用plan_robot_task提交完整任务。"
            "执行前检查整个任务，不支持的目标不能先执行前半段。咨询和歧义不执行。"
            "随后按确认计划每轮只调用一个动作工具，等待真实反馈，最多五步。"
            "失败、取消和结果未知后停止，不自动重试。"
            "只执行本次用户明确要求的步骤。没有独立实物握持验证。"
        )
        body = "\n\n".join(f"# {path}\n{content}" for path, content in sorted(self.profile.items()))
        messages = [{"role": "system", "content": policy + "\n\n" + body},
                    {"role": "user", "content": request}]
        try:
            if self.cancel_event.is_set():
                return self.finish("cancelled", plan)
            reply = self.model.complete(messages, [PLAN_TOOL])
            self.event("model_message", message=reply)
            if self.cancel_event.is_set():
                return self.finish("cancelled", plan)
            if not reply.get("tool_calls"):
                content = reply.get("content") or "请明确任务目标"
                if not isinstance(content, str):
                    raise ValueError("咨询回答必须是文本")
                plan = {"mode": "answer", "steps": [], "message": content}
                return self.finish("answer", plan)
            call, name, arguments = one_call(reply)
            if name != "plan_robot_task":
                raise ValueError("首次动作前必须提交完整计划")
            plan = normalize_plan(arguments)  # Validates ALL steps before any health/action call.
            self.event("plan", plan=plan)
            if plan["mode"] != "execute":
                return self.finish(plan["mode"], plan)
            messages.extend([assistant_call(call, "plan"), {"role": "tool", "tool_call_id": "plan",
                "content": json.dumps({"accepted": True, "steps": plan["steps"]}, ensure_ascii=False)}])
            for index, expected in enumerate(plan["steps"]):
                if self.cancel_event.is_set():
                    return self.finish("cancelled", plan)
                reply = self.model.complete(messages, ACTION_TOOLS)
                self.event("model_message", message=reply)
                if self.cancel_event.is_set():
                    return self.finish("cancelled", plan)
                if not reply.get("tool_calls"):
                    return self.finish("incomplete", plan, "模型在计划完成前停止")
                call, name, arguments = one_call(reply)
                action = normalize_action(name, arguments)
                if action != expected:
                    raise ValueError("动作偏离确认计划，未提交本轮动作")
                result = self.invoke(action)
                if result.get("cancelled") or self.cancel_event.is_set():
                    return self.finish("cancelled", plan)
                if result.get("result_unknown"):
                    return self.finish("unknown", plan, result.get("message", "结果未知"))
                if result.get("ok") is not True:
                    return self.finish("failed", plan, result.get("message", "动作失败"))
                call_id = f"action-{index}"
                messages.extend([assistant_call(call, call_id), {
                    "role": "tool", "tool_call_id": call_id, "content": json.dumps({
                        **result, "completed_actions": index + 1,
                        "remaining_actions": MAX_ACTIONS - index - 1,
                    }, ensure_ascii=False),
                }])
            # The trusted summary ends the task as soon as the complete plan succeeds.
            return self.finish("completed", plan)
        except (KeyError, TypeError, ValueError) as exc:
            return self.finish("rejected", plan, str(exc))
        except Exception as exc:
            self.stopped = True
            return self.finish("error", plan, f"执行中断：{type(exc).__name__}")

    def finish(self, status, plan, error=""):
        self.stopped = True
        completed = [a for a in self.actions if a["result"].get("ok") is True
                     and not a["result"].get("result_unknown") and not a["result"].get("cancelled")]
        if status in {"answer", "clarify"}:
            answer = plan["message"]
        else:
            labels = [f"移动到{a['arguments']['pose_id']}" if a["tool"] == "move_robot"
                      else f"标签{a['arguments']['tag_id']}抓取流程" for a in completed]
            prefix = "执行端已确认：" + "、".join(labels) + "。" if labels else ""
            answer = prefix + ("指定步骤执行结束；尚无独立实物验证。" if status == "completed"
                              else f"任务已停止（{status}）。{error} 未执行更多动作。")
        self.event("task_finished", status=status)
        return {"request": self.events[0]["request"], "status": status, "answer": answer,
                "error": error, "plan": plan, "actions": self.actions, "events": self.events,
                "profile_hash": fingerprint(self.profile),
                "backend": "mock" if self.backend.is_mock else "real",
                "verification_level": "simulated" if self.backend.is_mock else "execution_reported",
                "task_success": None,
                "simulated_plan_completed": self.backend.is_mock and status == "completed"}
