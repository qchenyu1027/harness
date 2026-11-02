"""Check the robot extension's Chat Completions model connection."""

from __future__ import annotations

import argparse
import sys

from ..robot.llm import ChatModel


DEFAULT_PROMPT = "只回复：连接成功"


def check_connection(model: ChatModel | None = None, *, prompt: str = DEFAULT_PROMPT) -> None:
    """Make one minimal request and print the effective, non-secret configuration."""
    model = model or ChatModel()
    print(f"LLM 地址：{model.settings.base_url}", flush=True)
    print(f"模型：{model.settings.model}", flush=True)
    response = model.complete([{"role": "user", "content": prompt}], max_tokens=64)
    content = response.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM returned no text for the connectivity check")
    print(f"模型响应：{content.strip()}")
    print("LLM 连接成功")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="测试机器人扩展使用的 Chat Completions 大模型是否能够连接",
    )
    result.add_argument("--prompt", default=DEFAULT_PROMPT, help="用于连通性测试的短提示词")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        check_connection(prompt=args.prompt)
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"LLM 检查失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
