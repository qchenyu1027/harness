"""Offline regressions: actual Harness² core, fake model and fake robot transport."""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from harness2.cli.check_llm import check_connection
from harness2.cli.robot import main, refine
from harness2.core import context, judge
from harness2.core.spec import resolve_channel_model
from harness2.robot.adapter import CASES, RobotAdapter, score_result
from harness2.robot.api import create_app
from harness2.robot.backend import HttpRobot, MockRobot
from harness2.robot.demo import DemoImprover, DemoModel, tool_reply
from harness2.robot.gateway import ToolGateway
from harness2.robot.llm import ChatModel, ModelSettings
from harness2.robot.proposer import HttpProposer, file_operation
from harness2.robot.runtime import DEFAULT_PROFILE, RobotExecutor, read_profile
from harness2.robot.tools import normalize_action


class ScriptModel:
    settings = ModelSettings("http://model.test/v1", "one-model")

    def __init__(self, replies):
        self.replies = iter(replies)
        self.messages = []

    def complete(self, messages, tools=None, **kwargs):
        self.messages.append(json.loads(json.dumps(messages)))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def plan(steps, mode="execute"):
    return tool_reply("plan_robot_task", {"mode": mode, "steps": steps, "message": "说明"})


def move(pose=2):
    return {"tool": "move_robot", "arguments": {"pose_id": pose}}


def grasp():
    return {"tool": "grasp_clothes", "arguments": {}}


class RobotTests(unittest.TestCase):
    def test_remote_qwen_defaults_and_compatible_environment_names(self):
        self.assertEqual(ModelSettings.base_url, "http://192.168.100.212:8000/v1")
        self.assertEqual(ModelSettings.model, "Qwen3.5-27B")
        self.assertEqual(ModelSettings.timeout, 300)
        names = {
            "ROBOT_LLM_BASE_URL": "", "ROBOT_LLM_MODEL": "", "ROBOT_LLM_API_KEY": "",
            "LLM_BASE_URL": "", "LLM_MODEL": "", "LLM_API_KEY": "",
            "QWEN_BASE_URL": "http://qwen.test/v1/", "QWEN_MODEL": "remote-model",
            "QWEN_API_KEY": "test-key", "ROBOT_LLM_TIMEOUT_S": "12",
        }
        with patch.dict("os.environ", names, clear=False):
            settings = ModelSettings.from_env()
        self.assertEqual(settings, ModelSettings(
            "http://qwen.test/v1", "remote-model", "test-key", 12,
        ))

    def test_llm_check_reuses_chat_model_and_source_script_imports(self):
        model = ScriptModel([{"content": "连接成功"}])
        with patch("builtins.print") as output:
            check_connection(model)
        rendered = "\n".join(" ".join(str(arg) for arg in call.args) for call in output.call_args_list)
        self.assertIn(model.settings.base_url, rendered)
        self.assertIn(model.settings.model, rendered)
        self.assertIn("LLM 连接成功", rendered)
        self.assertEqual(model.messages[0][0]["content"], "只回复：连接成功")

        script = Path(__file__).resolve().parents[1] / "scripts/check_llm.py"
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Chat Completions", result.stdout)

    def test_all_thirteen_mock_cases_are_rule_graded(self):
        with tempfile.TemporaryDirectory() as root:
            adapter = RobotAdapter(DemoModel(), root)
            for name, case in CASES.items():
                with self.subTest(case=name):
                    run = adapter.execute(name, None, name)
                    score = adapter.score(run, name)
                    self.assertEqual(score["logic_score"], 100, (name, score))
                    self.assertFalse(score["critical_violation"])
                    self.assertIsNone(score["task_success"])
                    context.assert_no_grader_verdicts(run)

    def test_whole_plan_invalid_or_over_limit_never_checks_robot(self):
        invalid_grasp = {"tool": "grasp_clothes", "arguments": {"tag_id": 9}}
        for steps in ([move(), invalid_grasp], [move()] * 6, [move(True)]):
            with self.subTest(steps=steps):
                robot = MockRobot()
                result = RobotExecutor(ScriptModel([plan(steps)]), robot).run("测试")
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(robot.requests, [])

    def test_extra_parameters_and_unsupported_tools_are_rejected(self):
        for name, args in [("move_robot", {"pose_id": 2, "speed": 1}),
                           ("grasp_clothes", {"tag_id": 5}), ("shell", {})]:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                normalize_action(name, args)

    def test_ids_normalize_only_whitelisted_integer_strings(self):
        self.assertEqual(normalize_action("move_robot", {"pose_id": " 2 "}), move())
        self.assertEqual(normalize_action("grasp_clothes", {}), grasp())
        for value in (True, 2.0, "02", "+2", "2e0", "２", None, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_action("move_robot", {"pose_id": value})

    def test_consultation_and_clarification_do_not_call_robot(self):
        for mode in ("answer", "clarify"):
            robot = MockRobot()
            result = RobotExecutor(ScriptModel([plan([], mode)]), robot).run("问一下")
            self.assertEqual(result["status"], mode)
            self.assertEqual(robot.requests, [])

    def test_followup_must_match_plan_and_feedback(self):
        model = ScriptModel([plan([move(), grasp()]), tool_reply("move_robot", {"pose_id": "2"}),
                             tool_reply("grasp_clothes", {"tag_id": 3})])
        robot = MockRobot()
        result = RobotExecutor(model, robot).run("测试")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual([r["path"] for r in robot.requests], ["/health", "/move"])
        self.assertEqual(model.messages[2][-1]["role"], "tool")
        self.assertTrue(json.loads(model.messages[2][-1]["content"])["ok"])

    def test_failure_and_timeout_stop_without_retry(self):
        for fault, status in (("failed_move", "failed"), ("timeout", "unknown")):
            model = ScriptModel([plan([move(), grasp()]), tool_reply("move_robot", {"pose_id": 2})])
            robot = MockRobot(fault)
            result = RobotExecutor(model, robot).run("测试")
            self.assertEqual(result["status"], status)
            self.assertEqual(len(model.messages), 2)
            self.assertEqual([r["path"] for r in robot.requests], ["/health", "/move"])

    def test_cancel_during_model_or_health_never_submits(self):
        robot = MockRobot()
        class CancelModel(ScriptModel):
            def complete(self, messages, tools=None, **kwargs):
                reply = super().complete(messages, tools, **kwargs)
                if len(self.messages) == 2:
                    robot.cancel_event.set()
                return reply
        result = RobotExecutor(CancelModel([plan([move()]), tool_reply("move_robot", {"pose_id": 2})]),
                               robot).run("测试")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(robot.requests, [])
        robot = MockRobot("cancel_health")
        result = RobotExecutor(ScriptModel([plan([move()]), tool_reply("move_robot", {"pose_id": 2})]),
                               robot).run("测试")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual([r["path"] for r in robot.requests], ["/health"])

    def test_parallel_tool_calls_and_missing_plan_are_blocked(self):
        call = tool_reply("move_robot", {"pose_id": 2})
        for first in (call, {"tool_calls": call["tool_calls"] * 2}):
            robot = MockRobot()
            result = RobotExecutor(ScriptModel([first]), robot).run("测试")
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(robot.requests, [])

    def test_complete_plan_ends_without_extra_model_action(self):
        model = ScriptModel([plan([move()]), tool_reply("move_robot", {"pose_id": 2}),
                             tool_reply("grasp_clothes", {})])
        result = RobotExecutor(model, MockRobot()).run("移动")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(model.messages), 2)
        self.assertIsNone(result["task_success"])

    def test_backend_crash_is_unknown_not_retried(self):
        robot = MockRobot()
        robot.submit = lambda action: (_ for _ in ()).throw(RuntimeError("lost reply"))
        result = RobotExecutor(ScriptModel([plan([move()]), tool_reply("move_robot", {"pose_id": 2})]),
                               robot).run("测试")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(result["actions"]), 1)

    def test_model_failure_after_move_preserves_partial_result(self):
        model = ScriptModel([plan([move(), grasp()]), tool_reply("move_robot", {"pose_id": 2}),
                             RuntimeError("model down")])
        result = RobotExecutor(model, MockRobot()).run("测试")
        self.assertEqual(result["status"], "error")
        self.assertIn("移动到2", result["answer"])
        self.assertNotIn("指定步骤执行结束", result["answer"])

    def test_improver_file_access_and_profile_edits_are_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "context/harness"
            import shutil
            shutil.copytree(DEFAULT_PROFILE, profile)
            file_operation(root, "write_file", {"path": "context/harness/systemprompt.md",
                                               "content": "robot-move robot-grasp 新规则"})
            self.assertIn("新规则", file_operation(root, "read_file", {
                "path": "context/harness/systemprompt.md"})["content"])
            for name, args in [("read_file", {"path": "/etc/passwd"}),
                               ("read_file", {"path": "context/../../secret"}),
                               ("read_file", {"path": "context/.env"}),
                               ("write_file", {"path": "context/harness/scripts/unsafe.py", "content": "x"}),
                               ("write_file", {"path": "context/task.md", "content": "x"})]:
                with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                    file_operation(root, name, args)
            with self.assertRaises(ValueError):
                read_profile(profile / "missing")

    def test_http_improver_uses_same_model_and_writes_real_candidate_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            import shutil
            shutil.copytree(DEFAULT_PROFILE, evidence / "harness")
            original = (evidence / "harness/systemprompt.md").read_text()
            model = ScriptModel([tool_reply("write_file", {"path": "context/harness/systemprompt.md",
                "content": original + "\n新增候选规则\n"}), {"content": "<notes>修改候选规则</notes>"}])
            proposer = HttpProposer(model, root / "audit")
            reply = proposer.call("修改", evidence, "inv1", harvest="context/harness")
            self.assertEqual(proposer.spec.model, model.settings.model)
            self.assertIn("新增候选规则", (reply.workspace / "systemprompt.md").read_text())
            self.assertNotIn("新增候选规则", (evidence / "harness/systemprompt.md").read_text())

    def test_fastapi_gateway_client_auth_payload_and_error_status(self):
        class Response:
            def __init__(self, data, status=200):
                self.data, self.status = data, status
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps(self.data).encode()
        class Transport:
            def __init__(self):
                self.requests = []
                self.responses = iter([Response({"ok": True, "ready_for_motion": True}),
                                       Response({"ok": True}), Response({"ok": True}, 500)])
            def open(self, request, timeout=None):
                self.requests.append(request)
                return next(self.responses)
        transport = Transport()
        robot = HttpRobot("http://robot.test", "test-key")
        with patch("urllib.request.build_opener", return_value=transport):
            self.assertTrue(robot.health(move(1))["ready_for_motion"])
            self.assertTrue(robot.submit(move(1))["ok"])
            self.assertFalse(robot.submit(move())["ok"])
        request = transport.requests[1]
        self.assertEqual(request.headers["X-api-key"], "test-key")
        self.assertEqual(request.full_url, "http://robot.test/tools/move_robot/execute")
        self.assertEqual(json.loads(request.data), {"arguments": {"pose_id": 1}})

    def test_real_mode_requires_fastapi_gateway_but_api_key_is_optional_locally(self):
        with patch.dict("os.environ", {"ROBOT_TOOL_API_BASE": "", "ROBOT_TOOL_API_KEY": ""}):
            with self.assertRaises(ValueError):
                HttpRobot()
        self.assertEqual(HttpRobot("http://127.0.0.1:8090", "").base_url,
                         "http://127.0.0.1:8090")
        with self.assertRaises(ValueError):
            HttpRobot("not-a-url", "")

    def test_configured_gateway_invokes_rpent_process_outside_client_backend(self):
        class Process:
            pid = 12345
            def wait(self, timeout=None):
                return 0
            def poll(self):
                return 0
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "clothes.py"
            setup = root / "setup.sh"
            config = root / "tools.json"
            python = Path("/usr/bin/python3")
            for path in (script, setup):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            config.write_text(json.dumps({"tools": {"grasp_clothes": {
                "description": "test", "kind": "process", "environment_script": str(setup),
                "command": [str(python), str(script), "--execute", "--home-mode", "direct",
                            "--gripper-side", "right"],
                "health_arguments": ["--help"], "timeout_env": "TEST_TOOL_TIMEOUT",
            }}}))
            with patch.dict("os.environ", {"TEST_TOOL_TIMEOUT": "5"}):
                gateway = ToolGateway(config)
                self.assertTrue(gateway.health("grasp_clothes")["ready_for_motion"])
            with patch.object(gateway, "health", return_value={
                    "ok": True, "ready_for_motion": True}), \
                    patch("subprocess.Popen", return_value=Process()) as launch:
                self.assertTrue(gateway.execute("grasp_clothes", {})["ok"])
            command = launch.call_args.args[0]
            self.assertIn(str(script), command)
            self.assertIn("--execute", command)
            self.assertIn("direct", command)
            self.assertIn("right", command)

    def test_gateway_renders_http_tool_payload_from_configuration(self):
        class Response:
            status = 200
            def __init__(self, body):
                self.body = body
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps(self.body).encode()
        class Transport:
            def __init__(self):
                self.requests = []
                self.responses = iter([Response({"ok": True, "ready_for_motion": True}),
                                       Response({"ok": True})])
            def open(self, request, timeout=None):
                self.requests.append(request)
                return next(self.responses)
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "tools.json"
            config.write_text(json.dumps({"tools": {"move_robot": {
                "description": "test", "kind": "http", "base_url_env": "TEST_MOTION_URL",
                "api_key_env": "TEST_MOTION_KEY", "health_path": "/health",
                "execute_path": "/move", "cancel_path": "/cancel",
                "payload": {"pose_ids": ["$args.pose_id"]},
            }}}))
            transport = Transport()
            with patch.dict("os.environ", {"TEST_MOTION_URL": "http://motion.test",
                                            "TEST_MOTION_KEY": "key"}), \
                    patch("urllib.request.build_opener", return_value=transport):
                self.assertTrue(ToolGateway(config).execute("move_robot", {"pose_id": 3})["ok"])
            self.assertEqual(json.loads(transport.requests[1].data), {"pose_ids": [3]})

    def test_fastapi_routes_expose_gateway_contract(self):
        class Gateway:
            tools = {"grasp_clothes": {}}
            lock = threading.Lock()
            def describe(self):
                return [{"name": "grasp_clothes", "kind": "process", "description": "test"}]
            def health(self, name):
                return {"ok": True, "ready_for_motion": True}
            def execute(self, name, arguments):
                return {"ok": True, "name": name, "arguments": arguments}
            def cancel(self):
                return {"accepted": True}
        with patch.dict("os.environ", {"ROBOT_TOOL_API_KEY": "secret"}):
            try:
                app = create_app(gateway=Gateway())
            except RuntimeError:
                self.skipTest("robot-api optional dependencies are not installed")
        paths = {route.path for route in app.routes}
        self.assertTrue({"/health", "/tools", "/tools/{name}/health",
                         "/tools/{name}/execute", "/cancel"}.issubset(paths))

    def test_original_recursion_and_independent_judge_without_move_agent(self):
        for mode, k in (("parallel", 1), ("sequential", 2)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.assertEqual(refine(DemoModel(True), DemoImprover(), root, DEFAULT_PROFILE,
                    {"grasp": CASES["grasp"]}, k, mode), 0)
                base = json.loads((root / "base_judged.json").read_text())[0]
                candidate = json.loads((root / "judged.json").read_text())[0]
                self.assertLess(base["frac"], candidate["frac"])
                self.assertEqual(candidate["frac"], 1)
                library = list((root / "library").rglob("EXPERIENCE.md"))
                self.assertTrue(library)
                context.assert_no_grader_verdicts(root / "adapt")

    def test_missing_grade_is_none_but_failed_task_has_a_score(self):
        with tempfile.TemporaryDirectory() as root:
            adapter = RobotAdapter(DemoModel(), root)
            self.assertIsNone(adapter.score(Path(root) / "missing", "move"))
            bad_model = ScriptModel([{"content": "不执行"}])
            result = RobotExecutor(bad_model, MockRobot()).run(CASES["move"].prompt)
            self.assertLess(score_result(result, CASES["move"])["logic_score"], 100)

    def test_native_channel_and_cli_help_do_not_need_opencode(self):
        self.assertEqual(resolve_channel_model("chat_completions", "one-model").model, "one-model")
        with self.assertRaises(ValueError):
            resolve_channel_model("chat_completions", None)
        with self.assertRaises(SystemExit) as result:
            main(["refine", "--real"])
        self.assertEqual(result.exception.code, 2)

    def test_http_model_payload_and_timeout_do_not_retry(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps({"choices": [{"message": {"content": "连接成功"}}]}).encode()
        class Transport:
            def __init__(self, fail=False):
                self.requests, self.fail = [], fail
            def open(self, request, timeout=None):
                self.requests.append(request)
                if self.fail:
                    raise urllib.error.URLError("timeout")
                return Response()
        settings = ModelSettings("http://llm.test/v1", "single-model", "local-test-key", 5)
        model, transport = ChatModel(settings), Transport()
        with patch("urllib.request.build_opener", return_value=transport):
            self.assertEqual(model.complete([{"role": "user", "content": "你好"}])["content"], "连接成功")
        self.assertEqual(transport.requests[0].full_url, "http://llm.test/v1/chat/completions")
        self.assertEqual(json.loads(transport.requests[0].data)["model"], "single-model")
        self.assertEqual(transport.requests[0].headers["Authorization"], "Bearer local-test-key")
        transport = Transport(fail=True)
        with patch("urllib.request.build_opener", return_value=transport), self.assertRaises(RuntimeError):
            model.complete([])
        self.assertEqual(len(transport.requests), 1)

    def test_concurrent_action_is_rejected_not_queued(self):
        robot = MockRobot()
        entered, release = threading.Event(), threading.Event()
        def submit(action):
            entered.set()
            release.wait(2)
            return {"ok": True}
        robot.submit = submit
        executor = RobotExecutor(DemoModel(), robot)
        worker = threading.Thread(target=executor.invoke, args=(move(),))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(RuntimeError):
                executor.invoke(grasp())
        finally:
            release.set()
            worker.join()
        self.assertEqual(len(executor.actions), 1)


if __name__ == "__main__":
    unittest.main()
