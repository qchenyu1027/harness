"""One Chat Completions endpoint shared by execution and harness improvement."""
from dataclasses import dataclass
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .. import config as _config  # Loads the repository .env without overriding the shell.


@dataclass(frozen=True)
class ModelSettings:
    base_url: str = "http://10.121.40.31:8000/v1"
    model: str = "Qwen3.5-9B"
    api_key: str = ""
    timeout: float = 90

    @classmethod
    def from_env(cls):
        return cls(
            os.getenv("ROBOT_LLM_BASE_URL", os.getenv("LLM_BASE_URL", cls.base_url)).rstrip("/"),
            os.getenv("ROBOT_LLM_MODEL", os.getenv("LLM_MODEL", cls.model)),
            os.getenv("ROBOT_LLM_API_KEY", os.getenv("LLM_API_KEY", "")),
            float(os.getenv("ROBOT_LLM_TIMEOUT_S", "90")),
        )


class ChatModel:
    def __init__(self, settings=None):
        self.settings = settings or ModelSettings.from_env()
        parsed = urllib.parse.urlsplit(self.settings.base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.query or parsed.fragment):
            raise ValueError("LLM URL must be an http(s) endpoint without embedded credentials")
        if not self.settings.model or self.settings.timeout <= 0:
            raise ValueError("LLM model and positive timeout are required")

    def complete(self, messages, tools=None, tool_choice="auto", max_tokens=4096):
        payload = {"model": self.settings.model, "messages": messages, "max_tokens": max_tokens}
        if tools:
            payload.update(tools=tools, tool_choice=tool_choice, parallel_tool_calls=False)
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = "Bearer " + self.settings.api_key
        request = urllib.request.Request(
            self.settings.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode(), headers=headers,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.settings.timeout) as response:
                message = json.load(response)["choices"][0]["message"]
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"LLM HTTP {exc.code}; check endpoint/model/auth/tool support") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise RuntimeError("LLM connection failed or timed out; no automatic retry") from None
        except (KeyError, IndexError, TypeError, ValueError):
            raise RuntimeError("Malformed Chat Completions response") from None
        if not isinstance(message, dict):
            raise RuntimeError("Malformed assistant message")
        return {key: message[key] for key in ("role", "content", "tool_calls") if key in message}


def assistant_call(call, call_id):
    return {"role": "assistant", "content": "", "tool_calls": [{
        "id": call_id, "type": "function", "function": call["function"],
    }]}
