"""Trusted robot contracts; editable Markdown cannot widen these limits."""
import json

MAX_ACTIONS = 5
CALIBRATIONS = {3: "tag3_left_tool_offset.yaml", 5: "tag5_left_tool_offset.yaml"}


def function(name, description, properties, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description, "parameters": {
            "type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False,
        },
    }}


ACTION_TOOLS = [
    function("move_robot", "移动到底盘预设位置1、2、3；不提供任意坐标导航。", {
        "pose_id": {"type": "integer", "enum": [1, 2, 3]},
    }, ("pose_id",)),
    function("grasp_object", "现有左臂AprilTag抓取并抬起，标签3或5，省略时固定为5。", {
        "tag_id": {"type": "integer", "enum": [3, 5]},
    }),
]

PLAN_TOOL = function("plan_robot_task", "首次动作前提交完整任务；咨询/歧义不执行。", {
    "mode": {"type": "string", "enum": ["execute", "answer", "clarify"]},
    "message": {"type": "string", "description": "咨询回答或需要澄清的问题"},
    "steps": {"type": "array", "items": {"type": "object", "properties": {
        "tool": {"type": "string", "enum": ["move_robot", "grasp_object"]},
        "arguments": {"type": "object", "properties": {
            "pose_id": {"type": "integer"}, "tag_id": {"type": "integer"},
        }, "additionalProperties": False},
    }, "required": ["tool", "arguments"], "additionalProperties": False}},
}, ("mode", "steps", "message"))


def normalize_id(value, allowed):
    if isinstance(value, str) and value.strip() in {str(x) for x in allowed}:
        value = int(value.strip())
    if type(value) is not int or value not in allowed:
        raise ValueError(f"编号必须是 {sorted(allowed)} 中的整数")
    return value


def normalize_action(name, arguments):
    if not isinstance(arguments, dict):
        raise ValueError("工具参数必须是对象")
    if name == "move_robot" and set(arguments) == {"pose_id"}:
        return {"tool": name, "arguments": {"pose_id": normalize_id(arguments["pose_id"], {1, 2, 3})}}
    if name == "grasp_object" and set(arguments).issubset({"tag_id"}):
        return {"tool": name, "arguments": {"tag_id": normalize_id(arguments.get("tag_id", 5), {3, 5})}}
    raise ValueError("工具或参数不受支持")


def normalize_plan(arguments):
    if not isinstance(arguments, dict) or set(arguments) != {"mode", "steps", "message"}:
        raise ValueError("任务必须包含 mode、steps、message")
    mode, steps = arguments["mode"], arguments["steps"]
    if mode not in {"execute", "answer", "clarify"} or not isinstance(steps, list):
        raise ValueError("任务模式或步骤无效")
    if not isinstance(arguments["message"], str):
        raise ValueError("message 必须是文本")
    if len(steps) > MAX_ACTIONS or (mode == "execute" and not steps):
        raise ValueError("执行任务必须有1至5个步骤")
    if mode != "execute" and steps:
        raise ValueError("咨询和澄清不能包含动作")
    normalized = []
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"tool", "arguments"}:
            raise ValueError("步骤格式无效")
        normalized.append(normalize_action(step["tool"], step["arguments"]))
    return {**arguments, "steps": normalized}


def one_call(reply):
    calls = reply.get("tool_calls") or []
    if not isinstance(calls, list) or len(calls) != 1:
        raise ValueError("每轮必须只调用一个工具")
    call = calls[0]
    if call.get("type", "function") != "function":
        raise ValueError("只接受 function 调用")
    name = call["function"]["name"]
    arguments = json.loads(call["function"].get("arguments") or "{}")
    return call, name, arguments
