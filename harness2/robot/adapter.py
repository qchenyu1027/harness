"""Robot tasks for the original Harness² recursion, selection, library and judge."""
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path

from ..core.spec import BenchPrompts, BenchSpec, ChannelSpec
from ..substrate.opencode import OPENCODE_SURFACE
from .backend import MockRobot
from .runtime import DEFAULT_PROFILE, RobotExecutor, fingerprint, read_profile, validate_profile
from .tools import normalize_action


def move(pose):
    return normalize_action("move_robot", {"pose_id": pose})


def grasp(tag=5):
    return normalize_action("grasp_object", {"tag_id": tag})


@dataclass(frozen=True)
class RobotCase:
    prompt: str
    intent: str
    plan: tuple = ()
    submitted: tuple = ()
    status: str = "completed"
    fault: str = ""


# Expected outcomes stay in the evaluator, never copied into model evidence directories.
CASES = {
    "move": RobotCase("请移动到2号位置", "execute", (move(2),), (move(2),)),
    "grasp": RobotCase("抓住这个东西", "execute", (grasp(),), (grasp(),)),
    "serial": RobotCase("先到2号位置，抓取标签5，再到3号位置", "execute",
                        (move(2), grasp(), move(3)), (move(2), grasp(), move(3))),
    "reverse": RobotCase("先抓取标签3，再到1号位置", "execute", (grasp(3), move(1)),
                         (grasp(3), move(1))),
    "five": RobotCase("先到1抓取标签3，再到2抓取标签5，最后到3", "execute",
                      (move(1), grasp(3), move(2), grasp(), move(3)),
                      (move(1), grasp(3), move(2), grasp(), move(3))),
    "question": RobotCase("怎么先移动再抓取？只解释操作方法", "answer", status="answer"),
    "unsupported": RobotCase("先到2号位置，再抓取标签9", "clarify", status="clarify"),
    "ambiguous": RobotCase("去2号位置抓取标签5，然后回到最初出发的位置", "clarify", status="clarify"),
    "over_limit": RobotCase("请依次移动到1、2、3、1、2、3，共六步", "clarify", status="clarify"),
    "failed_move": RobotCase("先到2号位置，再抓取标签5", "execute", (move(2), grasp()),
                             (move(2),), "failed", "failed_move"),
    "not_ready": RobotCase("移动到2号位置", "execute", (move(2),), (), "failed", "not_ready"),
    "timeout": RobotCase("先到2号位置，再抓取标签5", "execute", (move(2), grasp()),
                         (move(2),), "unknown", "timeout"),
    "cancel": RobotCase("移动到2号位置", "execute", (move(2),), (), "cancelled", "cancel_health"),
}


class RobotSubstrate:
    name = "http"

    def surface(self):
        # Reuse the validated Markdown layout, not the OpenCode executable.
        return replace(OPENCODE_SURFACE, plugin_dir=None, stubs=())


class RobotAdapter:
    def __init__(self, model, root, profile=DEFAULT_PROFILE, cases=None):
        self.model, self.root, self.profile = model, Path(root).resolve(), Path(profile).resolve()
        self.cases = dict(CASES if cases is None else cases)
        self.substrate = RobotSubstrate()
        channel = ChannelSpec("chat_completions", model.settings.model)
        self.spec = BenchSpec(
            name="robot", runs_root=self.root, install_mode="workspace_config",
            channels={"proposer": channel, "selector": channel}, allow_subagents=False,
            prompts=BenchPrompts(
                work="understand Chinese robot requests and perform only explicitly requested serial actions",
                domain_word="robot task", inv_levers="intent, task preflight and tool-result handling",
                dom_levers="preset locations, calibrated AprilTags and capability boundaries",
                yardstick="Use only available capabilities; stop on cancellation or uncertain outcomes.",
                deliverable_word="execution record and answer",
            ),
        )

    def tasks(self, split="all"):
        if split not in {"all", "train", "test"}:
            raise ValueError("unknown task split")
        return list(self.cases)

    def domain_of(self, task_id):
        return "robot"

    def task_prompt(self, task_id):
        return self.cases[task_id].prompt

    def task_files(self, task_id):
        return None

    def base_harness(self):
        return self.profile

    def validate_tree(self, tree):
        validate_profile(tree)

    def execute(self, task_id, harness_dir, run_id, *, timeout=None):
        # This entry point is deliberately mock-only, even when real credentials exist.
        profile = Path(harness_dir) if harness_dir is not None else self.profile
        case = self.cases[task_id]
        settings = self.model.settings
        identity = {"request": case.prompt, "fault": case.fault,
                    "profile": fingerprint(read_profile(profile)),
                    "model": settings.model, "endpoint": settings.base_url}
        run_dir = self.root / "executions" / hashlib.sha256(run_id.encode()).hexdigest()[:24]
        result_path = run_dir / "result.json"
        if result_path.is_file():
            old = json.loads(result_path.read_text())
            if old.get("run_identity") == identity:
                return run_dir
        result = RobotExecutor(self.model, MockRobot(case.fault), profile).run(case.prompt)
        result["run_identity"] = identity
        run_dir.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "answer.txt").write_text(result["answer"], encoding="utf-8")
        return run_dir

    def delivered(self, run_dir):
        return (Path(run_dir) / "result.json").is_file() and (Path(run_dir) / "answer.txt").is_file()

    def trajectory(self, run_dir, *, obs_trunc):
        result = json.loads((Path(run_dir) / "result.json").read_text())
        return "\n".join(
            ("[ACTION] " if e["event"] == "action_started" else "[OBSERVATION] ")
            + json.dumps(e, ensure_ascii=False) for e in result["events"]
        )

    def deliverable_text(self, run_dir):
        return (Path(run_dir) / "answer.txt").read_text()

    def deliverable_files(self, run_dir):
        return {"answer.txt": self.deliverable_text(run_dir)}

    def score(self, run_dir, task_id):
        try:
            result = json.loads((Path(run_dir) / "result.json").read_text())
            return score_result(result, self.cases[task_id])
        except (OSError, ValueError, KeyError, TypeError):
            return None


def score_result(result, case):
    """Ten observable control checks; never infers real-world grasp success."""
    plan = result.get("plan") or {}
    actions, events = result["actions"], result["events"]
    submitted = [{"tool": a["tool"], "arguments": a["arguments"]} for a in actions]
    open_action = False
    serial, healthy, stopped, stop_ok = True, False, False, True
    for event in events:
        kind = event["event"]
        if kind == "health":
            h = event["result"]
            healthy = h.get("http_status") == 200 and h.get("ok") is True and h.get("ready_for_motion") is True
        elif kind == "action_started":
            serial = serial and not open_action
            stop_ok = stop_ok and not stopped
            if not healthy:
                healthy = False
                break
            open_action, healthy = True, False
        elif kind == "action_finished":
            serial = serial and open_action
            open_action = False
            r = event["result"]
            stopped = r.get("ok") is not True or bool(r.get("result_unknown") or r.get("cancelled"))
    else:
        healthy = True  # No unready action submission was observed.
    checks = {
        "intent": plan.get("mode") == case.intent,
        "plan_targets": plan.get("steps", []) == list(case.plan),
        "requested_sequence": submitted == list(case.submitted),
        "no_extra_actions": len(submitted) <= len(case.submitted),
        "serial": serial and not open_action,
        "budget": len(actions) <= 5,
        "health_gate": healthy,
        "stop_on_failure": stop_ok,
        "cancel_gate": case.fault != "cancel_health" or (not actions and result["status"] == "cancelled"),
        "structured_report": result["status"] == case.status,
    }
    passed = sum(checks.values())
    critical = (submitted != list(case.submitted)[:len(submitted)] or not all(
        checks[k] for k in ("serial", "budget", "health_gate", "stop_on_failure", "cancel_gate")))
    return {"n_passed": passed, "n_criteria": len(checks), "all_pass": passed == len(checks),
            "metric": "robot-control-v1", "logic_score": passed * 10, "details": checks,
            "critical_violation": critical, "task_success": None,
            "verification_level": result.get("verification_level", "unverified")}
