"""Configuration-driven robot tool gateway used by the optional FastAPI service."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from .. import config as _config  # Load repository .env without replacing shell values.
from .tools import normalize_action


DEFAULT_CONFIG = Path(__file__).with_name("config") / "robot_tools.json"


def _expand(value):
    if isinstance(value, str):
        expanded = os.path.expandvars(value)
        if "${" in expanded:
            raise ValueError(f"工具配置缺少环境变量：{value}")
        return expanded
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


def _render(value, arguments):
    if isinstance(value, str) and value.startswith("$args."):
        name = value.removeprefix("$args.")
        if name not in arguments:
            raise ValueError(f"缺少工具参数：{name}")
        return arguments[name]
    if isinstance(value, list):
        return [_render(item, arguments) for item in value]
    if isinstance(value, dict):
        return {key: _render(item, arguments) for key, item in value.items()}
    return value


class ToolGateway:
    """Execute allowlisted HTTP/process tools from a trusted JSON configuration."""

    def __init__(self, config_path=DEFAULT_CONFIG):
        self.config_path = Path(config_path).expanduser().resolve()
        document = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or set(document) != {"tools"}:
            raise ValueError("工具配置必须只包含 tools")
        tools = document["tools"]
        if not isinstance(tools, dict) or not tools:
            raise ValueError("工具配置中没有工具")
        self.tools = {}
        for name, raw in tools.items():
            if name not in {"move_robot", "grasp_clothes"} or not isinstance(raw, dict):
                raise ValueError(f"不允许的工具：{name}")
            kind = raw.get("kind")
            if kind not in {"http", "process"}:
                raise ValueError(f"工具类型无效：{name}")
            required = ({"base_url_env", "health_path", "execute_path"} if kind == "http"
                        else {"environment_script", "command"})
            if not required.issubset(raw):
                raise ValueError(f"工具配置缺少字段：{name}")
            if kind == "process" and (not isinstance(raw["command"], list)
                                      or len(raw["command"]) < 2):
                raise ValueError(f"进程工具命令无效：{name}")
            self.tools[name] = raw
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self._process = None
        self._process_lock = threading.Lock()
        self.health_timeout = float(os.getenv("ROBOT_HEALTH_TIMEOUT_S", "5"))
        self.action_timeout = float(os.getenv("ROBOT_ACTION_TIMEOUT_S", "600"))
        if min(self.health_timeout, self.action_timeout) <= 0:
            raise ValueError("工具超时必须大于0")

    def describe(self):
        return [{"name": name, "kind": item["kind"],
                 "description": item.get("description", "")}
                for name, item in sorted(self.tools.items())]

    def _tool(self, name):
        try:
            return self.tools[name]
        except KeyError:
            raise ValueError(f"未知工具：{name}") from None

    def _http_settings(self, item):
        base_url = os.getenv(item["base_url_env"], "").rstrip("/")
        api_key = os.getenv(item.get("api_key_env", ""), "")
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError(f"未配置有效的 {item['base_url_env']}")
        return base_url, api_key

    def _http(self, item, method, path, payload=None, timeout=None):
        base_url, api_key = self._http_settings(item)
        headers = {"Accept": "application/json"}
        if method == "POST":
            headers["Content-Type"] = "application/json"
        if api_key:
            headers["X-API-Key"] = api_key
        request = urllib.request.Request(
            base_url + path, method=method, headers=headers,
            data=None if payload is None else json.dumps(payload).encode(),
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout or self.action_timeout) as response:
                body = json.load(response)
                if not isinstance(body, dict):
                    raise ValueError("response is not an object")
                return {**body, "http_status": response.status,
                        "ok": response.status == 200 and body.get("ok") is True}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "http_status": exc.code, "retryable": False,
                    "result_unknown": method == "POST" and exc.code >= 500,
                    "message": f"上游机器人 HTTP {exc.code}"}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return {"ok": False, "result_unknown": method == "POST", "retryable": False,
                    "message": "上游机器人通信失败或响应无效"}

    def _process_spec(self, item):
        config_dir = self.config_path.parent
        environment_script = Path(_expand(item["environment_script"]))
        if not environment_script.is_absolute():
            environment_script = config_dir / environment_script
        command = [str(part) for part in _expand(item["command"])]
        timeout = float(os.getenv(item.get("timeout_env", ""), str(self.action_timeout)))
        if timeout <= 0:
            raise ValueError("进程工具超时必须大于0")
        return environment_script.resolve(), command, timeout

    @staticmethod
    def _wrapped_process(environment_script, command):
        return ["/usr/bin/bash", "-c", 'source "$1" && shift && exec "$@"',
                "robot-tool-gateway", str(environment_script), *command]

    def health(self, name):
        item = self._tool(name)
        if item["kind"] == "http":
            try:
                return self._http(item, "GET", item["health_path"], timeout=self.health_timeout)
            except (KeyError, ValueError) as exc:
                return {"http_status": 503, "ok": False, "ready_for_motion": False,
                        "message": str(exc)}
        try:
            environment_script, command, _ = self._process_spec(item)
            paths = [environment_script, Path(command[0]), Path(command[1])]
            missing = [str(path) for path in paths if not path.is_file()]
            if missing:
                raise ValueError("进程工具依赖不存在：" + "、".join(missing))
            check = subprocess.run(
                self._wrapped_process(environment_script,
                    [*command[:2], *item.get("health_arguments", ["--help"])]),
                capture_output=True, text=True, timeout=self.health_timeout, check=False,
            )
            if check.returncode != 0:
                detail = (check.stderr or check.stdout).strip()[-1000:]
                raise ValueError("进程工具健康检查失败" + (f"：{detail}" if detail else ""))
            return {"http_status": 200, "ok": True, "ready_for_motion": True}
        except (KeyError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
            return {"http_status": 503, "ok": False, "ready_for_motion": False,
                    "message": str(exc)}

    def _stop_process(self):
        with self._process_lock:
            process = self._process
        if process is None or process.poll() is not None:
            return False
        try:
            os.killpg(process.pid, signal.SIGINT)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
        return True

    def _execute_process(self, item):
        environment_script, command, timeout = self._process_spec(item)
        with tempfile.TemporaryFile() as output:
            try:
                process = subprocess.Popen(
                    self._wrapped_process(environment_script, command), stdout=output,
                    stderr=subprocess.STDOUT, start_new_session=True,
                )
            except OSError:
                return {"ok": False, "result_unknown": False, "message": "无法启动工具进程"}
            with self._process_lock:
                self._process = process
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._stop_process()
                return {"ok": False, "result_unknown": True,
                        "message": "工具进程超时并已请求停止；设备状态需现场确认"}
            finally:
                with self._process_lock:
                    if self._process is process:
                        self._process = None
            output.seek(0)
            tail = output.read().decode("utf-8", errors="replace")[-1500:].strip()
        if self.cancel_event.is_set():
            return {"ok": False, "cancelled": True, "result_unknown": True,
                    "message": "工具已请求取消；设备状态需现场确认"}
        if returncode != 0:
            return {"ok": False, "result_unknown": True,
                    "message": f"工具进程异常结束（退出码{returncode}）" +
                               (f"：{tail}" if tail else "")}
        return {"ok": True, "message": "工具执行端已报告完成；尚无独立实物验证"}

    def execute(self, name, arguments):
        if not isinstance(arguments, dict):
            raise ValueError("arguments 必须是对象")
        arguments = normalize_action(name, arguments)["arguments"]
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "http_status": 409, "message": "已有工具正在执行"}
        try:
            self.cancel_event.clear()
            item = self._tool(name)
            readiness = self.health(name)
            if readiness.get("ok") is not True or readiness.get("ready_for_motion") is not True:
                return {"ok": False, "message": readiness.get("message", "工具未就绪")}
            if item["kind"] == "http":
                payload = _render(item.get("payload", {}), arguments)
                return self._http(item, "POST", item["execute_path"], payload)
            if arguments:
                raise ValueError("该进程工具不接受参数")
            return self._execute_process(item)
        finally:
            self.lock.release()

    def cancel(self):
        self.cancel_event.set()
        stopped = self._stop_process()
        upstream = []
        for item in self.tools.values():
            if item["kind"] == "http" and item.get("cancel_path"):
                try:
                    upstream.append(self._http(item, "POST", item["cancel_path"], {}))
                except (KeyError, ValueError):
                    pass
        return {"accepted": stopped or any(item.get("ok") for item in upstream),
                "process_stopped": stopped, "upstream": upstream}
