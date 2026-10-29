"""Standalone robot CLI: one HTTP LLM, mock-only refinement and independent rule grading."""
import argparse
import json
from pathlib import Path
import shutil
import signal
import threading
import uuid

from ..core import judge, report, schedule
from ..robot.adapter import CASES, RobotAdapter, score_result
from ..robot.backend import HttpRobot, MockRobot
from ..robot.llm import ChatModel
from ..robot.proposer import HttpProposer
from ..robot.runtime import DEFAULT_PROFILE, RobotExecutor, fingerprint, read_profile


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def selected_cases(raw):
    names = list(CASES) if raw == "all" else [name.strip() for name in raw.split(",") if name.strip()]
    if not names or len(set(names)) != len(names) or any(name not in CASES for name in names):
        raise ValueError("--cases must select unique names from: " + ",".join(CASES))
    return {name: CASES[name] for name in names}


def refine(model, proposer, output, profile, cases, k=1, mode="parallel"):
    if k < 1 or k > 5:
        raise ValueError("k must be between 1 and 5")
    output = Path(output).resolve()
    # This manifest binds cached evidence to one model, endpoint, profile and task set.
    identity = {"model": model.settings.model, "endpoint": model.settings.base_url,
                "profile": fingerprint(read_profile(profile)), "cases": list(cases), "k": k, "mode": mode}
    identity_path = output / "robot-config.json"
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("运行配置已改变，请使用新的 --out 目录")
    dump(identity_path, identity)
    adapter = RobotAdapter(model, output, profile, cases)
    result = schedule.run_domain("robot", adapter.tasks(), k, adapter=adapter, prop=proposer,
        runs_root=output / "adapt", library_root=output / "library",
        modes=(schedule.mode_token(mode),))
    selections, bases, fallback_count = [], [], 0
    for path in sorted((output / "adapt").rglob("summary.json")):
        summary = json.loads(path.read_text())
        selections.extend(summary.get("selections", {}).values())
        for item in summary.get("modes", {}).values():
            fallback_count += len(item.get("fell_back", []))
        for base in summary.get("base", []):
            bases.append(judge.judge_one(adapter, summary["task_id"], "base", k,
                base["label"], base["run_id"], Path(base["run_dir"]), chosen_is_base=True))
    rows = judge.judge_selections(adapter, selections, parallel=1)
    judge.write(rows, output / "judged.json")
    judge.write(bases, output / "base_judged.json")
    text = report.render(report.summarise(rows), title="机器人候选控制逻辑评分")
    base_text = "基线控制逻辑评分\n" + "\n".join(s.line() for s in report.summarise(bases))
    (output / "report.txt").write_text(base_text + "\n\n" + text + "\n", encoding="utf-8")
    result.update(fallback_stages=fallback_count, graded=len([row for row in rows if row.graded]),
                  expected_tasks=len(cases), output=str(output))
    dump(output / "robot-summary.json", result)
    print(base_text + "\n" + text)
    print("结果目录：", output)
    return 0 if (not result["failed"] and not fallback_count
                 and len(rows) == len(cases) and all(row.graded for row in rows)) else 2


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="只测试LLM，不连接机器人")
    run = sub.add_parser("run", help="运行一次任务，默认模拟机器人")
    run.add_argument("request")
    run.add_argument("--real", action="store_true", help="显式连接真实机器人HTTP服务")
    run.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    run.add_argument("--out", type=Path)
    for name in ("evaluate", "refine", "demo"):
        item = sub.add_parser(name)
        item.add_argument("--out", type=Path)
        item.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
        item.add_argument("--cases", default="grasp" if name in {"demo", "refine"}
                          else "move,grasp,serial,question,unsupported")
        if name in {"refine", "demo"}:
            item.add_argument("--k", type=int, default=1)
            item.add_argument("--mode", choices=("parallel", "sequential"), default="parallel")
    grade = sub.add_parser("score", help="对已保存的模拟任务执行记录独立打分")
    grade.add_argument("run_dir", type=Path)
    grade.add_argument("--case", required=True, choices=tuple(CASES))
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "score":
            result = json.loads((args.run_dir / "result.json").read_text())
            if result.get("backend") != "mock":
                raise ValueError("此评分入口仅评估预定义模拟场景，不推断真机实物成功")
            print(json.dumps(score_result(result, CASES[args.case]), ensure_ascii=False, indent=2))
            return 0
        output = (args.out if getattr(args, "out", None) else
                  Path("runs") / "robot" / uuid.uuid4().hex).resolve()
        if args.command == "demo":
            from ..robot.demo import DemoImprover, DemoModel
            if output.exists():
                raise ValueError("demo 请使用新的 --out 目录")
            output.mkdir(parents=True)
            profile = output / "base-profile"
            shutil.copytree(args.profile, profile)
            print("预设演示：不连接LLM或真机；不是模型效果评测。")
            return refine(DemoModel(inject_bug=True), DemoImprover(), output, profile,
                          selected_cases(args.cases), args.k, args.mode)
        model = ChatModel()
        if args.command == "check":
            response = model.complete([{"role": "user", "content": "只回复：连接成功"}])
            print("模型：", model.settings.model)
            print(response.get("content", ""))
            return 0
        if args.command == "run":
            backend = HttpRobot() if args.real else MockRobot()
            executor = RobotExecutor(model, backend, args.profile)
            cancel_threads = []
            def cancel(signum, frame):
                executor.cancel_event.set()
                if executor.actions and not cancel_threads:
                    worker = threading.Thread(target=backend.cancel)
                    cancel_threads.append(worker)
                    worker.start()
                print("已阻止后续动作；正在执行的动作仍需确认停止状态。")
            previous = signal.signal(signal.SIGINT, cancel)
            try:
                result = executor.run(args.request)
            finally:
                signal.signal(signal.SIGINT, previous)
                for worker in cancel_threads:
                    worker.join()
            dump(output / "result.json", result)
            print("后端：", "真实机器人" if args.real else "模拟机器人")
            print(result["answer"])
            print("记录：", output / "result.json")
            return 0 if result["status"] in {"completed", "answer", "clarify"} else 2
        cases = selected_cases(args.cases)
        if args.command == "refine":
            return refine(model, HttpProposer(model, output / "improver-audit"), output,
                          args.profile, cases, args.k, args.mode)
        adapter = RobotAdapter(model, output, args.profile, cases)
        rows = []
        for name in cases:
            run_dir = adapter.execute(name, None, f"evaluate-{name}")
            row = judge.judge_one(adapter, name, "base", 1, "base1", name, run_dir, chosen_is_base=True)
            rows.append(row)
            print(name, "ungraded" if not row.graded else f"{row.n_passed}/{row.n_criteria}")
        judge.write(rows, output / "judged.json")
        print("\n".join(s.line() for s in report.summarise(rows)))
        print("结果目录：", output)
        return 0 if all(row.graded and row.all_pass for row in rows) else 2
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"错误：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
