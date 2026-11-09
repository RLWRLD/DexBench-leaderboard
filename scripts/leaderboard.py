#!/usr/bin/env python3
"""Validate PR metadata and build the reviewed DexBench leaderboard without running policies."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

import jsonschema

CODE_ROOT = Path(__file__).resolve().parents[1]
FILES = {"entry.json", "submission.json", "model_entry.json", "method_card.md", "validation.json"}
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024


class Refusal(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise Refusal(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    require(path.is_file() and not path.is_symlink(), f"missing regular file: {path}")
    require(path.stat().st_size <= MAX_FILE, f"file too large: {path}")
    return json.loads(path.read_text(), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(Refusal(f"invalid JSON number: {value}")))


def schema(data, name):
    contract = read_json(CODE_ROOT / "schemas" / f"{name}.schema.json")
    errors = sorted(jsonschema.Draft202012Validator(contract, format_checker=jsonschema.FormatChecker()).iter_errors(data),
                    key=lambda error: str(list(error.path)))
    require(not errors, f"{name}: " + "; ".join(f"{list(e.path)} {e.message}" for e in errors[:5]))


def https(value):
    parsed = urlsplit(value)
    require(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password,
            f"expected an HTTPS URL without credentials: {value}")
    return value


def nonplaceholder(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and ("sha256" in key or key.endswith("commit") or key.endswith("digest")):
                require(not re.search(r"(?:^|:)0{40,64}$", item), f"replace placeholder {key}")
            nonplaceholder(item)
    elif isinstance(value, list):
        for item in value:
            nonplaceholder(item)


def entry_id(value):
    parts = value.split("/")
    require(len(parts) == 3 and all(SLUG.fullmatch(part) for part in parts),
            "entry ID must be lowercase team/model/revision slugs")
    return value


def inventory(root, directory, allowed):
    """Require one complete immutable record per three-component ID; never follow symlinks."""
    base = root / directory
    groups, total = {}, 0
    if not base.exists():
        return groups
    require(not base.is_symlink(), f"symlink forbidden: {base}")
    for path in sorted(base.rglob("*")):
        require(not path.is_symlink(), f"symlink forbidden: {path}")
        if path.is_dir():
            continue
        relative = path.relative_to(base)
        if relative.as_posix() == "README.md":
            continue
        require(len(relative.parts) == 4 and relative.name in allowed, f"unexpected file: {path}")
        identity = entry_id("/".join(relative.parts[:3]))
        total += path.stat().st_size
        require(path.stat().st_size <= MAX_FILE and total <= MAX_TOTAL, f"metadata size limit exceeded: {directory}")
        groups.setdefault(identity, set()).add(relative.name)
    for identity, files in groups.items():
        require(files == allowed, f"{directory}/{identity}: expected {sorted(allowed)}, found {sorted(files)}")
    return groups


def releases(root):
    registry = read_json(root / "releases.json")
    schema(registry, "releases")
    result = {}
    for release in registry["releases"]:
        require(release["id"] not in result, "duplicate release ID")
        nonplaceholder(release)
        https(release["evaluator_repository"])
        result[release["id"]] = release
    return result


def number(value, label):
    require(type(value) in (int, float) and math.isfinite(value), f"{label}: expected a finite number")
    return value


def score_equal(value, expected, label):
    require(abs(number(value, label) - expected) < 1e-10, f"{label}: score does not match counts")


def check_report(report, submission, release):
    """Cross-check a claimed Arena report; authenticating its artifacts is the operator's job."""
    key = submission["model"]["key"]
    version = submission["benchmark"]["eval_version"]
    tasks = sorted({run["task"] for run in submission["runs"]})
    require(report["status"] == "validated", "validation report is not validated")
    # Accept the earlier artifact-only report wording without rewriting immutable submissions.
    require(report["verification"] in ("artifact validation only", "not independently rerun"),
            "expected an artifact-only validation report")
    require(report["model"] == submission["model"] and report["version"] == version
            and report["data_track"] == submission["training"]["track"], "report identity differs from submission")
    complete = tasks == sorted(release["tasks"])
    require(complete, "submissions must cover the full release task suite")
    require(report["coverage"] == {"submitted": tasks, "required": sorted(release["tasks"]), "complete": complete},
            "report coverage differs from the accepted release")
    require(set(report["tasks"]) == set(tasks), "report task set differs from submission")
    settings = report["evaluation_settings"]
    require(settings["status"] == "matched release references" and set(settings["references"]) == set(tasks),
            "report lacks release-reference validation")
    task_scores = {}
    for task in tasks:
        declared = release["tasks"][task]
        require(settings["references"][task] == {"file": f"settings_db_{task}.json", "sha256": declared["reference_sha256"]},
                f"{task}: reference pin differs from release")
        result = report["tasks"][task]
        require(result["status"] == "complete" and result["version"] == version
                and result["frozen_sha256"] == declared["frozen_sha256"], f"{task}: incomplete or different frozen set")
        require(set(result["models"]) == {key}, f"{task}: report contains another model")
        model = result["models"][key]
        require(model["complete"] is True and model.get("rank_eligible", model.get("headline")) is True and model["missing_cells"] == [],
                f"{task}: incomplete or non-headline score")
        require(set(model["cells"]) == set(declared["cells"]), f"{task}: missing or extra cells")
        cells = {}
        for name, cell in model["cells"].items():
            k, n = cell["k"], cell["n"]
            require(type(k) is int and type(n) is int and 0 <= k <= n and n == declared["cells"][name][version],
                    f"{task}/{name}: counts differ from release")
            require(cell["deviations"] == [], f"{task}/{name}: evaluation deviations")
            score_equal(cell["sr"], k / n, f"{task}/{name}")
            ci = cell["ci95"]
            require(isinstance(ci, list) and len(ci) == 2
                    and 0 <= number(ci[0], "ci95") <= number(ci[1], "ci95") <= 1, "invalid interval")
            cells[name] = {field: cell[field] for field in ("k", "n", "sr", "ci95")}
        macro = sum(cell["sr"] for cell in cells.values()) / len(cells)
        score_equal(model["macro_sr"], macro, task)
        task_scores[task] = {"sr": macro, "cells": cells}
    overall = sum(t["sr"] for t in task_scores.values()) / len(task_scores) if complete else None
    if complete:
        score_equal(report["overall_sr"], overall, "Overall")
    else:
        require(report["overall_sr"] is None, "partial-suite submissions cannot claim Overall")
    require(isinstance(report["trajectories"], list) and report["trajectories"], "missing trajectory evidence inventory")
    return {"tasks": task_scores, "overall_sr": overall, "complete": complete}


def validate_entry(root, directory, identity, registry):
    folder = root / directory / identity
    entry, submission, report = (read_json(folder / name) for name in ("entry.json", "submission.json", "validation.json"))
    schema(entry, "entry")
    schema(submission, "submission")
    nonplaceholder(entry)
    nonplaceholder(submission)
    https(entry["artifact_bundle"]["url"])
    https(submission["model"]["code_url"])
    require(submission["method_card"]["path"] == "method_card.md", "method card must be method_card.md")
    require(digest((folder / "method_card.md").read_bytes()) == submission["method_card"]["sha256"], "method card hash differs")
    benchmark = submission["benchmark"]
    require(benchmark["release"] in registry, f"release not open for submissions: {benchmark['release']}")
    release = registry[benchmark["release"]]
    for field in ("code_commit", "evaluator_image_digest"):
        require(benchmark[field] == release[field], f"benchmark {field} differs from accepted release")
    require(benchmark["eval_version"] in release["eval_versions"], "evaluation version is not open in this release")
    require(benchmark["evaluation_sha256"] == {task: data["evaluation_sha256"] for task, data in release["tasks"].items()},
            "task evaluation contract differs from accepted release")
    model_entry = read_json(folder / "model_entry.json")
    schema(model_entry, "model_entry")
    require(submission["model_entry"]["path"] == "model_entry.json" and
            digest((folder / "model_entry.json").read_bytes()) == submission["model_entry"]["sha256"], "model entry hash differs")
    require(model_entry["key"] == submission["model"]["key"] and report.get("model_entry") == model_entry,
            "report or submission model entry differs")
    builtin = model_entry["key"] in release["models"]
    require((set(model_entry) == {"key"}) == builtin, "baseline keys cannot be redefined; outside models need a config entry")
    allowed_data = {(item["uri"], item["revision"]) for item in release["datasets"]}
    require(all((item["uri"], item["revision"]) in allowed_data for item in submission["training"]["datasets"]),
            "training dataset is not in the release allowlist")
    require(not submission["training"]["additional_data"],
            "standard-data cannot declare additional training data")
    tasks = {run["task"] for run in submission["runs"]}
    require(tasks == set(release["tasks"]), "submissions must cover the full release task suite")
    require(tasks == set(submission["checkpoints"]), "checkpoint task mismatch")
    require(len({(item["sha256"], item["label"]) for item in submission["checkpoints"].values()}) == 1, "generalist checkpoint differs by task")
    directories = set()
    for run in submission["runs"]:
        require(run["directory"] not in directories, "duplicate run directory")
        directories.add(run["directory"])
        require(run["cell"] in release["tasks"][run["task"]]["cells"], "unknown cell")
    for task in tasks:
        require({r["cell"] for r in submission["runs"] if r["task"] == task} == set(release["tasks"][task]["cells"]),
                f"{task}: runs do not cover every cell")
    scores = check_report(report, submission, release)
    hashes = {name: digest((folder / name).read_bytes()) for name in sorted(FILES)}
    return {"id": identity, "digest": digest(json.dumps(hashes, sort_keys=True).encode()),
            "entry": entry, "submission": submission, "model_entry": model_entry, "builtin": builtin, "scores": scores}


def model_registry(root):
    """Maintainer-owned immutable keys, outside participant submission directories."""
    directory = root / "models"
    result = {}
    require(not directory.is_symlink(), "model registry cannot be a symlink")
    if not directory.exists():
        return result
    for path in directory.iterdir():
        if path.name == "README.md":
            continue
        require(path.suffix == ".json" and path.is_file() and not path.is_symlink(), "unexpected model registry file")
        record = read_json(path)
        schema(record, "registration")
        nonplaceholder(record)
        key = record["model_entry"]["key"]
        require(path.name == key + ".json", "model registry filename must equal its key")
        result[key] = record
    return result


def registration_candidate(entry):
    require(not entry["builtin"], "baseline models are already registered by the evaluator")
    checkpoint = next(iter(entry["submission"]["checkpoints"].values()))
    record = {"schema_version": 1, "group": "registered", "model_entry": entry["model_entry"],
              "checkpoint_sha256": checkpoint["sha256"], "checkpoint_label": checkpoint["label"]}
    schema(record, "registration")
    return record


def require_registration(root, entry):
    if not entry["builtin"]:
        require(model_registry(root).get(entry["model_entry"]["key"]) == registration_candidate(entry),
                "publication needs a matching maintainer model registration")


def validate(root):
    registry = releases(root)
    model_registry(root)
    entries = {identity: validate_entry(root, "submissions", identity, registry)
               for identity in inventory(root, "submissions", FILES)}
    reviews = inventory(root, "reviews", {"review.json"})
    rows = []
    for identity in reviews:
        require(identity in entries, f"orphan review: {identity}")
        review = read_json(root / "reviews" / identity / "review.json")
        schema(review, "review")
        https(review["evidence_url"])
        original = entries[identity]
        require(review["entry_sha256"] == original["digest"], f"{identity}: review no longer matches entry bytes")
        status = review["status"]
        if status == "withdrawn":
            continue
        require_registration(root, original)
        submission = original["submission"]
        rows.append({"id": identity, "team": original["entry"]["team"], "model": submission["model"]["name"],
                     "model_key": submission["model"]["key"], "release": submission["benchmark"]["release"],
                     "eval_version": submission["benchmark"]["eval_version"], "track": submission["training"]["track"],
                     "scope": submission["model"]["scope"], "status": status, "review": review,
                     "origin": "baseline" if original["builtin"] else "submitted",
                     "scores": original["scores"], "reported_scores": original["scores"],
                     "code_url": submission["model"]["code_url"], "artifact_bundle": original["entry"]["artifact_bundle"],
                     "entry_sha256": original["digest"]})
    return {"schema_version": 1, "entries": rows,
            "pending": sorted(set(entries) - set(reviews)), "releases": sorted(registry)}


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def scaffold(root, identity):
    entry_id(identity)
    folder = root / "submissions" / identity
    require(not folder.exists(), "submission already exists; create a new revision")
    folder.mkdir(parents=True)
    for name in ("entry.json", "submission.json", "model_entry.json", "method_card.md"):
        (folder / name).write_bytes((CODE_ROOT / "templates" / name).read_bytes())
    submission = read_json(folder / "submission.json")
    submission["method_card"]["sha256"] = digest((folder / "method_card.md").read_bytes())
    submission["model_entry"]["sha256"] = digest((folder / "model_entry.json").read_bytes())
    (folder / "submission.json").write_text(json.dumps(submission, indent=2) + "\n")
    print(f"Created {folder}; replace placeholders and copy the original Arena validation.json before submitting.")


def review_candidate(root, identity, status, reviewer, evidence_url, notes):
    entry_id(identity)
    registry = releases(root)
    inventory(root, "submissions", FILES)
    original = validate_entry(root, "submissions", identity, registry)
    if status == "reported":
        require_registration(root, original)
    result = {"schema_version": 1, "status": status, "entry_sha256": original["digest"],
              "reviewed_by": reviewer, "reviewed_at": datetime.now(timezone.utc).isoformat(),
              "evidence_url": https(evidence_url), "notes": notes}
    schema(result, "review")
    return result


def build(root, output):
    data = validate(root)
    output.mkdir(parents=True, exist_ok=True)
    (output / "leaderboard.json").write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    encoded = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = (CODE_ROOT / "web" / "index.html").read_text()
    (output / "index.html").write_text(template.replace("__LEADERBOARD_DATA__", encoded))
    for name in ("app.js", "style.css"):
        (output / name).write_bytes((CODE_ROOT / "web" / name).read_bytes())
    (output / ".nojekyll").touch()
    return data


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def check_pr(root, base, head):
    """Read PR blobs as bounded data using base-branch code, never check out or execute the PR."""
    for revision in (base, head):
        require(re.fullmatch(r"[a-f0-9]{40}", revision), "CI expects full commit SHA values")
    changed = git(root, "diff", "--no-renames", "--name-status", "-z", base, head).decode().split("\0")[:-1]
    require(len(changed) % 2 == 0, "unexpected diff format")
    changes = list(zip(changed[::2], changed[1::2]))
    for status, path in changes:
        if path.startswith("models/") and path.endswith(".json"):
            require(status == "A", "registered model keys are immutable; add a new key")
    if not any(path.startswith("submissions/") and path != "submissions/README.md" for _, path in changes):
        print("No submission changes; infrastructure, release and review PRs require maintainer review.")
        return
    for status, path in changes:
        parts = path.split("/")
        require(status == "A" and len(parts) == 5 and parts[0] == "submissions" and parts[-1] in FILES,
                "submission PRs may only add complete entries; separate infrastructure/review changes and use a new revision")
        entry_id("/".join(parts[1:4]))
    with tempfile.TemporaryDirectory() as temporary:
        destination = Path(temporary)
        total = 0
        # Every registry and review comes from the trusted base. Only submission data comes from the head.
        for revision, paths in ((base, ("releases.json", "reviews", "models")), (head, ("submissions",))):
            tree = git(root, "ls-tree", "-r", "-z", revision, "--", *paths)
            for record in tree.split(b"\0"):
                if not record:
                    continue
                meta, rawpath = record.split(b"\t", 1)
                mode, kind, oid = meta.decode().split()
                require(mode == "100644" and kind == "blob", "only ordinary metadata files are admitted")
                path = rawpath.decode()
                require(not Path(path).is_absolute() and ".." not in Path(path).parts, "unsafe tree path")
                size = int(git(root, "cat-file", "-s", oid))
                total += size
                require(size <= MAX_FILE and total <= MAX_TOTAL, "PR metadata exceeds size limit")
                target = destination / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(git(root, "cat-file", "blob", oid))
        data = validate(destination)
        print(f"Submission metadata validated; {len(data['pending'])} pending entries. Artifact review is still required.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=CODE_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    render = commands.add_parser("build")
    render.add_argument("--output", type=Path, default=Path("site"))
    new = commands.add_parser("new")
    new.add_argument("identity")
    register = commands.add_parser("register", help="Create a maintainer registration candidate for a submitted model")
    register.add_argument("identity")
    register.add_argument("--output", type=Path, required=True)
    review = commands.add_parser("review")
    review.add_argument("identity")
    review.add_argument("--status", choices=("reported", "withdrawn"), required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--evidence-url", required=True)
    review.add_argument("--notes", required=True)
    review.add_argument("--output", type=Path, required=True)
    gate = commands.add_parser("check-pr")
    gate.add_argument("--base", required=True)
    gate.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "new":
            scaffold(args.root, args.identity)
        elif args.command == "register":
            entry_id(args.identity)
            inventory(args.root, "submissions", FILES)
            entry = validate_entry(args.root, "submissions", args.identity, releases(args.root))
            write_new(args.output, registration_candidate(entry))
        elif args.command == "review":
            write_new(args.output, review_candidate(args.root, args.identity, args.status, args.reviewer, args.evidence_url, args.notes))
        elif args.command == "check-pr":
            check_pr(args.root, args.base, args.head)
        else:
            data = build(args.root, args.output) if args.command == "build" else validate(args.root)
            print(f"Validated: {len(data['entries'])} published, {len(data['pending'])} pending, {len(data['releases'])} accepted releases")
    except (Refusal, ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
