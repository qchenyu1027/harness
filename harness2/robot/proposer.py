"""The same HTTP model edits harness files via narrowly scoped file tools."""
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid

from ..core.proposer import Proposer, ProposerError, Reply
from ..core.spec import ChannelSpec
from .runtime import editable
from .tools import function

FILE_TOOLS = [
    function("list_files", "列出context证据目录中的文本文件路径", {
        "path": {"type": "string"},
    }),
    function("read_file", "读取context内的任务、轨迹、候选文件或经验", {
        "path": {"type": "string"},
    }, ("path",)),
    function("write_file", "只可编辑context/harness中的Prompt、Skills等Markdown", {
        "path": {"type": "string"}, "content": {"type": "string"},
    }, ("path", "content")),
    function("delete_file", "只可删除context/harness中允许编辑的Markdown文件", {
        "path": {"type": "string"},
    }, ("path",)),
]


def file_operation(root, name, arguments):
    if not isinstance(arguments, dict):
        raise ValueError("file arguments must be an object")
    allowed = {"path", "content"} if name == "write_file" else {"path"}
    if not set(arguments).issubset(allowed):
        raise ValueError("unknown file arguments")
    path = arguments.get("path", "context" if name == "list_files" else None)
    if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts:
        raise ValueError("use a relative context/... path without '..'")
    target = (root / path).resolve()
    context = (root / "context").resolve()
    if not target.is_relative_to(context):
        raise ValueError("access is restricted to context/")
    if name == "list_files":
        if not target.is_dir():
            raise ValueError("not a directory")
        paths = [p.relative_to(root).as_posix() for p in sorted(target.rglob("*"))
                 if p.is_file() and p.suffix in {".md", ".txt", ".json", ".csv"}
                 and p.resolve().is_relative_to(context)]
        return {"paths": paths[:250], "truncated": len(paths) > 250}
    if name == "read_file":
        if target.suffix not in {".md", ".txt", ".json", ".csv"}:
            raise ValueError("only evidence text files can be read")
        if target.stat().st_size > 200_000:
            raise ValueError("file too large; inspect a smaller evidence file")
        return {"content": target.read_text(encoding="utf-8")}
    harness = context / "harness"
    if not target.is_relative_to(harness) or not editable(target.relative_to(harness).as_posix()):
        raise ValueError("only Prompt/Skills Markdown inside context/harness may be edited")
    if name == "write_file":
        content = arguments.get("content")
        if not isinstance(content, str) or len(content) > 30_000:
            raise ValueError("content must be text with at most 30000 characters")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"written": path}
    if name == "delete_file":
        target.unlink()
        return {"deleted": path}
    raise ValueError("unknown file tool")


class HttpProposer(Proposer):
    def __init__(self, model, audit_dir, steps=20):
        super().__init__(ChannelSpec("chat_completions", model.settings.model), Path(audit_dir))
        self.model, self.steps = model, steps
        self.semaphore = threading.BoundedSemaphore(2)

    def call(self, mission, context_dir, label, *, system="", harvest=None, **kwargs):
        started = time.monotonic()
        with self.semaphore, tempfile.TemporaryDirectory(prefix="robot-improver-") as temporary:
            root = Path(temporary)
            if context_dir is not None:
                shutil.copytree(context_dir, root / "context")
            else:
                (root / "context").mkdir()
            policy = (
                "你在一个受控文件工具环境中执行任务。先用list_files和read_file查看实际证据。"
                "任务中路径context/...相对于工具根目录。"
                "只允许修改context/harness/内的四个常驻Markdown和skills/<name>/SKILL.md；"
                "本HTTP执行器不执行脚本、插件、子Agent或shell，不要创建这些组件。"
                "机器人动作参数和停止约束由可信代码维护。不要在任务证据中查找隐藏答案或分数。"
                "根据任务要求返回notes、choice或experience格式。没有支持证据时允许不改动。"
            )
            messages = [{"role": "system", "content": policy + "\n" + system},
                        {"role": "user", "content": mission}]
            try:
                for turn in range(self.steps):
                    reply = self.model.complete(messages, FILE_TOOLS, max_tokens=8192)
                    calls = reply.get("tool_calls") or []
                    if not calls:
                        text = reply.get("content") or ""
                        if not isinstance(text, str) or not text.strip():
                            raise ValueError("improver returned empty text")
                        audit = self.audit_dir / uuid.uuid4().hex
                        audit.mkdir(parents=True)
                        (audit / "mission.md").write_text(mission, encoding="utf-8")
                        (audit / "response.md").write_text(text, encoding="utf-8")
                        workspace = None
                        if harvest:
                            if harvest != "context/harness" or not (root / harvest).is_dir():
                                raise ValueError("expected context/harness edit directory")
                            workspace = audit / "harness"
                            shutil.copytree(root / harvest, workspace)
                        result = Reply(label=label, text=text, ok=True, attempts=1,
                                       wall_s=time.monotonic() - started,
                                       channel="chat_completions", workspace=workspace, audit=audit)
                        self._calls.append(result)
                        return result
                    if not isinstance(calls, list) or len(calls) > 8:
                        raise ValueError("invalid file tool batch")
                    normalized, observations = [], []
                    for index, call in enumerate(calls):
                        fn = call["function"]
                        call_id = f"file-{turn}-{index}"
                        normalized.append({"id": call_id, "type": "function", "function": fn})
                        try:
                            observation = file_operation(root, fn["name"], json.loads(fn.get("arguments") or "{}"))
                        except (OSError, ValueError, TypeError) as exc:
                            observation = {"error": str(exc)}
                        observations.append({"role": "tool", "tool_call_id": call_id,
                                             "content": json.dumps(observation, ensure_ascii=False)})
                    messages.extend([{"role": "assistant", "content": "", "tool_calls": normalized},
                                     *observations])
                raise ValueError("improver file-tool round limit reached")
            except Exception as exc:
                raise ProposerError(f"{label}: {type(exc).__name__}: {exc}", attempts=1) from exc
