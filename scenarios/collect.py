#!/usr/bin/env python3
"""Run fault scenarios in GitHub Actions and save each failed run as a labelled eval case.

Standard library only; needs the GitHub CLI (`gh`), signed in with access to the repo.

    python3 scenarios/collect.py --list
    python3 scenarios/collect.py --only pb-tax-rounding           # one case
    python3 scenarios/collect.py --all --runs 2                    # 2 cases per scenario

Each case lands in cases/case-NNNN/ (neutral names, so the folder doesn't give the answer away).
The agent may read:
    junit.xml      the JUnit report of the run
    rerun.xml      the JUnit report of rerunning only the failed tests once (pytest --lf)
    test_log.txt   log of the "Run e2e suite" step only
    shop.log       the application's own log
    diff.patch     what changed in this run, like the diff of a commit
    source/        the test code and shop code as they were tested
The agent never reads:
    label.json     category and root cause, plus run details

Every case also gets one harmless change from the `noise` list, so every case has a diff.
The step that switches faults on, the patch step, and the environment block GitHub prints at the
top of each step are left out, and every saved file is checked for fault names before it is
written.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO = "asifmahbubnaeem/tfa-failure-lab"
WORKFLOW = "e2e.yml"
TEST_STEP = "Run e2e suite"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Anything that names an injected fault. Must never appear in a file the agent reads.
LEAK = re.compile(
    r"\bBUG_[A-Z_]+|\bFLAKY_[A-Z_]+|\bfault on\b|scenarios/patches|patched suite|suite_patch|unset_tax_rate|"
    r"break_database|\bcase_id\b|environment failure|product bug|test bug|flaky|"
    r"\bnoise-[a-z]+|\b(?:pb|tb|fl|env)-[a-z]+-[a-z]+",
    re.I,
)
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def gh(*args: str, check: bool = True, tries: int = 3) -> str:
    """Run gh, retrying network blips (artifact downloads hit a separate storage host)."""
    for attempt in range(1, tries + 1):
        proc = subprocess.run(["gh", *args], capture_output=True, text=True)
        if proc.returncode == 0 or not check:
            return proc.stdout
        if attempt < tries:
            time.sleep(10 * attempt)
    raise RuntimeError(f"gh {' '.join(args)} failed after {tries} tries:\n{proc.stderr.strip()}")


def load_scenarios() -> list[dict]:
    with open(HERE / "scenarios.toml", "rb") as f:
        return tomllib.load(f)["scenario"]


def next_case_id(out: Path, taken: set[str]) -> str:
    """Neutral ids (case-0001, ...): a folder named after the scenario would give the answer away."""
    n = 1
    while (out / f"case-{n:04d}").exists() or f"case-{n:04d}" in taken:
        n += 1
    return f"case-{n:04d}"


def load_noise() -> list[str]:
    with open(HERE / "scenarios.toml", "rb") as f:
        return tomllib.load(f).get("noise", [])


def noise_for(case_id: str, noise: list[str]) -> str:
    """Rotate through the harmless changes by case number, so each is used about equally."""
    return noise[(int(case_id.split("-")[-1]) - 1) % len(noise)] if noise else ""


def dispatch(scenario: dict, case_id: str, noise_patch: str) -> None:
    args = ["workflow", "run", WORKFLOW, "-R", REPO, "--ref", "main", "-f", f"case_id={case_id}"]
    inputs = dict(scenario.get("inputs", {}))
    inputs["patches"] = " ".join(p for p in (inputs.get("patches", ""), noise_patch) if p)
    for key, value in inputs.items():
        if isinstance(value, bool):
            value = "true" if value else "false"
        args += ["-f", f"{key}={value}"]
    gh(*args)


def find_run(case_id: str, timeout_s: int = 120) -> dict:
    """The run whose title is the case id (the workflow's run-name)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        runs = json.loads(
            gh("run", "list", "-R", REPO, "--workflow", WORKFLOW, "--event", "workflow_dispatch",
               "--limit", "50", "--json", "databaseId,displayTitle,status,conclusion,url")
        )
        for run in runs:
            if run["displayTitle"] == case_id:
                return run
        time.sleep(5)
    raise RuntimeError(f"run for {case_id} did not appear within {timeout_s}s")


def wait_for(run_id: int, timeout_s: int = 1800) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        run = json.loads(gh("run", "view", str(run_id), "-R", REPO, "--json", "status,conclusion,url,jobs"))
        if run["status"] == "completed":
            return run
        time.sleep(15)
    raise RuntimeError(f"run {run_id} still not finished after {timeout_s}s")


def strip_test_step_log(full_log: str) -> str:
    """Keep only the test step's own output: drop other steps, timestamps, and the
    command/environment header GitHub prints at the top of the step."""
    lines = []
    for raw in full_log.splitlines():
        parts = raw.split("\t", 2)
        if len(parts) == 3 and parts[1] == TEST_STEP:
            # GitHub prefixes a step's first line with a byte-order mark, and colours some lines.
            text = parts[2].lstrip("\ufeff")
            lines.append(ANSI.sub("", TIMESTAMP.sub("", text)))
    out, in_header = [], False
    for line in lines:
        if line.startswith("##[group]"):
            in_header = True
            continue
        if line.startswith("##[endgroup]"):
            in_header = False
            continue
        if not in_header:
            out.append(line)
    return "\n".join(out).strip() + "\n"


def failing_tests(junit_xml: Path) -> list[dict]:
    failures = []
    for tc in ET.parse(junit_xml).getroot().iter("testcase"):
        for tag in ("failure", "error"):
            el = tc.find(tag)
            if el is not None:
                failures.append({"test": f"{tc.get('classname')}::{tc.get('name')}", "kind": tag,
                                 "message": (el.get("message") or "")[:300]})
    return failures


def rerun_failures(rerun_xml: Path) -> int:
    return len(failing_tests(rerun_xml))


def check_no_leak(name: str, text: str) -> None:
    m = LEAK.search(text)
    if m:
        raise RuntimeError(f"{name} would reveal the injected fault ('{m.group(0)}'); not saving this case")


def save_case(scenario: dict, case_id: str, run: dict, out: Path, noise_patch: str) -> str:
    run_id = run["databaseId"]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gh("run", "download", str(run_id), "-R", REPO, "-n", "e2e-reports", "-D", str(tmp))
        junit = next(tmp.rglob("junit.xml"), None)
        if junit is None:
            return "no junit.xml in the run's reports (the job failed before the tests ran)"
        failures = failing_tests(junit)
        if not failures:
            return "all tests passed"
        test_log = strip_test_step_log(gh("run", "view", str(run_id), "-R", REPO, "--log"))
        shop_log_path = next(tmp.rglob("shop.log"), None)
        shop_log = shop_log_path.read_text() if shop_log_path else ""
        junit_text = junit.read_text()
        rerun = next(tmp.rglob("rerun.xml"), None)
        rerun_text = rerun.read_text() if rerun else ""
        diff = next(tmp.rglob("diff.patch"), None)
        diff_text = diff.read_text() if diff else ""
        source = next((p for p in tmp.rglob("source") if p.is_dir()), None)
        if source is None or not diff_text:
            return "the run's reports have no source snapshot or diff (is the workflow up to date?)"
        texts = {"junit.xml": junit_text, "rerun.xml": rerun_text, "test_log.txt": test_log,
                 "shop.log": shop_log, "diff.patch": diff_text}
        for f in sorted(source.rglob("*")):
            if f.is_file():
                texts[f"source/{f.relative_to(source)}"] = f.read_text(errors="replace")
        for name, text in texts.items():
            check_no_leak(name, text)

        case = out / case_id
        case.mkdir(parents=True)
        for name, text in texts.items():
            if name == "rerun.xml" and not text:
                continue
            (case / name).parent.mkdir(parents=True, exist_ok=True)
            (case / name).write_text(text)
        label = {
            "case_id": case_id,
            "category": scenario["category"],
            "root_cause": scenario["root_cause"],
            "scenario": scenario["id"],
            "noise_patch": noise_patch,
            "rerun_failures": rerun_failures(rerun) if rerun else None,
            "failing_tests": failures,
            "source": "injected",
            "run_url": run["url"],
            "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "labelled_by": "construction (fault switch or patch)",
        }
        (case / "label.json").write_text(json.dumps(label, indent=2) + "\n")
        return f"saved: {len(failures)} failing test(s)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    pick = ap.add_mutually_exclusive_group(required=True)
    pick.add_argument("--list", action="store_true", help="list scenarios and exit")
    pick.add_argument("--only", help="comma-separated scenario ids")
    pick.add_argument("--all", action="store_true", help="every scenario")
    ap.add_argument("--runs", type=int, default=1, help="cases wanted per scenario (default 1)")
    ap.add_argument("--out", default=str(ROOT / "cases"), help="where cases go (default: cases/)")
    args = ap.parse_args()

    scenarios = load_scenarios()
    if args.list:
        for s in scenarios:
            print(f"{s['id']:24} {s['category']:12} {s['root_cause']}")
        return 0
    if shutil.which("gh") is None:
        print("The GitHub CLI (gh) is needed: https://cli.github.com", file=sys.stderr)
        return 1
    wanted = scenarios if args.all else [s for s in scenarios if s["id"] in args.only.split(",")]
    unknown = set() if args.all else set(args.only.split(",")) - {s["id"] for s in scenarios}
    if unknown:
        print(f"Unknown scenario(s): {', '.join(sorted(unknown))}. Use --list.", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    noise = load_noise()
    # Work queue: (scenario, cases still wanted, attempts left).
    queue = [[s, args.runs, s.get("attempts", 1) * args.runs] for s in wanted]
    while any(want > 0 and left > 0 for _, want, left in queue):
        batch, taken = [], set()
        for item in queue:
            s, want, left = item
            for _ in range(min(want, left)):
                case_id = next_case_id(out, taken)
                taken.add(case_id)
                dispatch(s, case_id, noise_for(case_id, noise))
                print(f"started  {case_id} ({s['id']})", flush=True)
                batch.append((item, case_id))
                time.sleep(2)
        for item, case_id in batch:
            s = item[0]
            run = find_run(case_id)
            done = wait_for(run["databaseId"])
            item[2] -= 1
            try:
                result = save_case(s, case_id, {**run, **done}, out, noise_for(case_id, noise))
            except RuntimeError as e:
                result = f"skipped: {e}"
            if result.startswith("saved"):
                item[1] -= 1
            print(f"finished {case_id} ({s['id']}): {result}  {done['url']}", flush=True)
    short = [f"{s['id']} ({want} short)" for s, want, _ in queue if want > 0]
    print("Done." + (f" Not enough failing runs for: {', '.join(short)}" if short else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
