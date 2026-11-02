"""Mock backend and FastAPI tool-gateway client used by the robot executor."""
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

from .. import config as _config  # Load repository .env without replacing shell values.


class MockRobot:
    is_mock = True

    def __init__(self, fault="", cancel_event=None):
        self.fault = fault
        self.cancel_event = cancel_event or threading.Event()
        self.requests = []

    def health(self, action=None):
        self.requests.append({"method": "GET", "path": "/health"})
        if self.fault == "cancel_health":
            self.cancel_event.set()
        return {"http_status": 200, "ok": True, "ready_for_motion": self.fault != "not_ready"}

    def submit(self, action):
        self.requests.append({"method": "POST", "path": "/move" if action["tool"] == "move_robot"
                              else "/grasp-clothes", "action": action})
        if self.fault == "timeout":
            return {"ok": False, "result_unknown": True, "message": "模拟超时，结果未知"}
        if self.fault == "failed_move" and action["tool"] == "move_robot":
            return {"ok": False, "message": "模拟移动失败"}
        return {"ok": True, "message": "模拟移动完成" if action["tool"] == "move_robot"
                else "模拟抓取流程完成"}

    def cancel(self):
        self.cancel_event.set()
        return {"accepted": True, "message": "已取消模拟任务"}


class HttpRobot:
    """Client for the FastAPI gateway; physical tool details stay server-side."""

    is_mock = False

    def __init__(self, base_url=None, api_key=None, cancel_event=None):
        self.base_url = (base_url if base_url is not None
                         else os.getenv("ROBOT_TOOL_API_BASE", "")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("ROBOT_TOOL_API_KEY", "")
        parsed = urllib.parse.urlsplit(self.base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.query or parsed.fragment):
            raise ValueError("真机模式必须配置有效的 ROBOT_TOOL_API_BASE")
        self.cancel_event = cancel_event or threading.Event()
        self.health_timeout = float(os.getenv("ROBOT_HEALTH_TIMEOUT_S", "5"))
        self.action_timeout = float(os.getenv("ROBOT_ACTION_TIMEOUT_S", "600"))
        if min(self.health_timeout, self.action_timeout) <= 0:
            raise ValueError("机器人请求超时必须大于0")

    def _request(self, method, path, payload=None, action=False):
        headers = {"Accept": "application/json"}
        if method == "POST":
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        request = urllib.request.Request(
            self.base_url + path, method=method, headers=headers,
            data=None if payload is None else json.dumps(payload).encode(),
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.action_timeout if action else self.health_timeout) as response:
                body = json.load(response)
                if not isinstance(body, dict):
                    raise ValueError("response is not an object")
                return {**body, "http_status": response.status,
                        "ok": response.status == 200 and body.get("ok") is True,
                        "accepted": path == "/cancel" and body.get("accepted") is True}
        except urllib.error.HTTPError as exc:
            try:
                detail = json.load(exc).get("detail")
            except (ValueError, AttributeError):
                detail = None
            if isinstance(detail, dict):
                return {**detail, "ok": False, "http_status": exc.code}
            return {"ok": False, "http_status": exc.code, "retryable": False,
                    "result_unknown": action and exc.code >= 500,
                    "message": f"工具网关 HTTP {exc.code}"}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return {"ok": False, "result_unknown": action, "retryable": False,
                    "message": "工具网关通信失败或响应无效；动作结果未知，禁止自动重试"}

    def health(self, action=None):
        if action is None:
            return self._request("GET", "/health")
        return self._request("GET", f"/tools/{action['tool']}/health")

    def submit(self, action):
        return self._request(
            "POST", f"/tools/{action['tool']}/execute",
            {"arguments": action["arguments"]}, action=True,
        )

    def cancel(self):
        self.cancel_event.set()
        return self._request("POST", "/cancel", {})
