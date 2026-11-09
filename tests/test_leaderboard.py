"""Behavioural checks for PR admission, review binding, score arithmetic and publication."""

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("leaderboard", ROOT / "scripts/leaderboard.py")
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)
IDENTITY = "test-team/test-model/revision-1"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def fixture(root):
    """Synthetic metadata only, never shipped as an accepted release or leaderboard entry."""
    release = json.loads((ROOT / "templates/release.example.json").read_text().replace("0" * 64, "b" * 64).replace("0" * 40, "b" * 40))
    release["id"] = "synthetic-test"
    for cell in release["tasks"]["m1"]["cells"].values():
        cell.update(v1=2, **{"v1-plus": 4})
    write(root / "releases.json", {"schema_version": 1, "releases": [release]})
    folder = root / "submissions" / IDENTITY
    folder.mkdir(parents=True)
    submission = json.loads((ROOT / "templates/submission.json").read_text().replace("0" * 64, "b" * 64).replace("0" * 40, "b" * 40))
    submission["model"]["name"] = "Synthetic test model"
    submission["benchmark"].update({field: release[field] for field in ("code_commit", "manifest_sha256", "evaluator_image_digest")})
    submission["benchmark"]["evaluation_sha256"] = {task: item["evaluation_sha256"] for task, item in release["tasks"].items()}
    submission["benchmark"]["release"] = release["id"]
    submission["training"]["datasets"] = [{**release["datasets"][0], "episodes": 10}]
    write(folder / "model_entry.json", {"key": "gr00t"})
    submission["model_entry"]["sha256"] = board.digest((folder / "model_entry.json").read_bytes())
    submission["benchmark"]["eval_version"] = "v1"
    run = submission["runs"][0]
    run["cell"] = "train"
    submission["runs"].append({**copy.deepcopy(run), "cell": "ga_s", "directory": "runs/m1/ga_s"})
    (folder / "method_card.md").write_text("# Synthetic fixture\nNot a policy result.\n")
    submission["method_card"]["sha256"] = board.digest((folder / "method_card.md").read_bytes())
    write(folder / "submission.json", submission)
    write(folder / "entry.json", {"schema_version": 1, "team": "Test team", "artifact_bundle": {"url": "https://example.org/bundle", "sha256": "c" * 64}})
    cells = {name: {"k": 1, "n": 2, "sr": 0.5, "ci95": [0.09, 0.91], "deviations": []} for name in ("train", "ga_s")}
    report = {"status": "validated", "verification": "artifact validation only", "model": submission["model"],
              "model_entry": {"key": "gr00t"}, "version": "v1", "data_track": "standard-data",
              "coverage": {"submitted": ["m1"], "required": ["m1"], "complete": True}, "overall_sr": 0.5,
              "evaluation_settings": {"status": "matched release references", "references": {"m1": {"file": "settings_db_m1.json", "sha256": "b" * 64}}},
              "tasks": {"m1": {"status": "complete", "version": "v1", "frozen_sha256": "b" * 64,
                               "models": {"gr00t": {"complete": True, "headline": True, "missing_cells": [], "macro_sr": 0.5, "cells": cells}}}},
              "trajectories": [{"task": "m1", "path": "synthetic.hdf5"}]}
    write(folder / "validation.json", report)
    return folder


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.folder = fixture(self.root)

    def mutate(self, name, change, folder=None):
        path = (folder or self.folder) / name
        data = json.loads(path.read_text())
        change(data)
        write(path, data)

    def review(self, status="reported"):
        record = board.review_candidate(self.root, IDENTITY, status, "maintainer", "https://github.com/RLWRLD/DexBench-leaderboard/pull/1", "Synthetic audit")
        write(self.root / "reviews" / IDENTITY / "review.json", record)
        return record


class LeaderboardTests(Fixture):
    def test_schema_matches_the_recorded_upstream_digest(self):
        source = board.read_json(ROOT / "schemas/upstream.json")
        self.assertEqual(source["sha256"], board.digest((ROOT / "schemas/submission.schema.json").read_bytes()))
        self.assertEqual(source["model_entry"]["sha256"], board.digest((ROOT / "schemas/model_entry.schema.json").read_bytes()))

    def test_only_standard_data_generalist_entries_are_accepted(self):
        path = self.folder / "submission.json"
        original = path.read_bytes()
        for section, field, value in (("training", "track", "open-data"), ("model", "scope", "specialist"),
                                      ("training", "additional_data", True)):
            with self.subTest(section=section, field=field):
                path.write_bytes(original)
                self.mutate("submission.json", lambda data: data[section].update({field: value}))
                with self.assertRaises(board.Refusal):
                    board.validate(self.root)

    def test_same_checkpoint_is_required_across_tasks(self):
        registry = board.read_json(self.root / "releases.json")
        registry["releases"][0]["tasks"]["m2"] = copy.deepcopy(registry["releases"][0]["tasks"]["m1"])
        write(self.root / "releases.json", registry)
        def change(data):
            data["benchmark"]["evaluation_sha256"]["m2"] = "b" * 64
            data["runs"].append({**copy.deepcopy(data["runs"][0]), "task": "m2", "directory": "runs/m2/train"})
            data["checkpoints"]["m2"] = {**data["checkpoints"]["m1"], "sha256": "e" * 64}
        self.mutate("submission.json", change)
        with self.assertRaisesRegex(board.Refusal, "checkpoint differs by task"):
            board.validate(self.root)

    def test_pending_is_not_ranked_and_approval_binds_exact_bytes(self):
        data = board.validate(self.root)
        self.assertEqual(data["entries"], [])
        self.assertEqual(data["pending"], [IDENTITY])
        self.review()
        self.assertEqual(board.validate(self.root)["entries"][0]["scores"]["overall_sr"], 0.5)
        self.mutate("entry.json", lambda data: data.update(team="Changed team"))
        with self.assertRaisesRegex(board.Refusal, "review no longer matches"):
            board.validate(self.root)

    def test_partial_suite_is_refused(self):
        registry = board.read_json(self.root / "releases.json")
        registry["releases"][0]["tasks"]["m2"] = copy.deepcopy(registry["releases"][0]["tasks"]["m1"])
        write(self.root / "releases.json", registry)
        self.mutate("submission.json", lambda data: data["benchmark"]["evaluation_sha256"].update(m2="b" * 64))
        with self.assertRaisesRegex(board.Refusal, "full release task suite"):
            board.validate(self.root)

    def test_score_and_release_inconsistencies_are_refused(self):
        path = self.folder / "validation.json"
        original = path.read_bytes()
        mutations = [
            lambda d: d.update(verification="verified"),
            lambda d: d.update(overall_sr=0.9),
            lambda d: d.update(overall_sr=float("nan")),
            lambda d: d["tasks"]["m1"]["models"]["gr00t"]["cells"]["train"].update(n=3),
            lambda d: d["tasks"]["m1"]["models"]["gr00t"]["cells"]["train"].update(k=True),
            lambda d: d["tasks"]["m1"]["models"]["gr00t"]["cells"]["train"].update(sr=0.8),
            lambda d: d["tasks"]["m1"]["models"]["gr00t"].update(macro_sr=0.8),
            lambda d: d["tasks"]["m1"]["models"]["gr00t"]["cells"].pop("ga_s"),
            lambda d: d["evaluation_settings"]["references"]["m1"].update(sha256="a" * 64),
            lambda d: d.update(trajectories=[]),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                path.write_bytes(original)
                self.mutate("validation.json", mutation)
                self.assertEqual(board.main(["--root", str(self.root), "validate"]), 1)

    def test_method_hash_schema_duplicates_and_placeholder_rejection(self):
        (self.folder / "method_card.md").write_text("changed")
        with self.assertRaisesRegex(board.Refusal, "method card hash"):
            board.validate(self.root)
        write(self.folder / "entry.json", {"schema_version": 1, "team": "team", "artifact_bundle": {"url": "https://example.org/x", "sha256": "0" * 64}})
        with self.assertRaisesRegex(board.Refusal, "placeholder"):
            board.validate(self.root)
        (self.folder / "entry.json").write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(board.Refusal, "duplicate JSON key"):
            board.validate(self.root)

    def test_unregistered_releases_and_symlinks_are_refused(self):
        write(self.root / "releases.json", {"schema_version": 1, "releases": []})
        with self.assertRaisesRegex(board.Refusal, "release not open"):
            board.validate(self.root)
        path = self.folder / "entry.json"
        payload = path.read_bytes()
        path.unlink()
        (self.root / "outside.json").write_bytes(payload)
        path.symlink_to(self.root / "outside.json")
        with self.assertRaisesRegex(board.Refusal, "symlink"):
            board.validate(self.root)

    def test_outside_model_needs_exact_maintainer_registration_to_publish(self):
        key = "outside-generalist"
        entry = {"key": key, "label": "Outside generalist", "group": "community",
                 "policy": {"type": "example.Policy", "policy_variant": key}}
        write(self.folder / "model_entry.json", entry)
        def change_submission(data):
            data["model"]["key"] = key
            data["model_entry"]["sha256"] = board.digest((self.folder / "model_entry.json").read_bytes())
        self.mutate("submission.json", change_submission)
        def change_report(data):
            data["model"]["key"] = key
            data["model_entry"] = entry
            model = data["tasks"]["m1"]["models"].pop("gr00t")
            model.update(headline=False, rank_eligible=True, group="community")
            data["tasks"]["m1"]["models"][key] = model
        self.mutate("validation.json", change_report)
        self.assertEqual(board.validate(self.root)["pending"], [IDENTITY])
        with self.assertRaisesRegex(board.Refusal, "maintainer model registration"):
            self.review()
        output = self.root / "models" / (key + ".json")
        self.assertEqual(board.main(["--root", str(self.root), "register", IDENTITY, "--output", str(output)]), 0)
        self.review()
        row = board.validate(self.root)["entries"][0]
        self.assertEqual(row["origin"], "submitted")
        self.assertEqual(row["scores"]["overall_sr"], 0.5)
        registration = board.read_json(output)
        registration["checkpoint_sha256"] = "e" * 64
        write(output, registration)
        with self.assertRaisesRegex(board.Refusal, "maintainer model registration"):
            board.validate(self.root)

    def test_task_contracts_and_dataset_allowlist_replace_full_manifest_equality(self):
        self.mutate("submission.json", lambda d: d["benchmark"].update(manifest_sha256="a" * 64))
        self.assertEqual(board.validate(self.root)["pending"], [IDENTITY])
        path = self.folder / "submission.json"
        original = path.read_bytes()
        mutations = (
            lambda d: d["benchmark"]["evaluation_sha256"].update(m1="a" * 64),
            lambda d: d["training"]["datasets"][0].update(uri="https://example.org/extra-data"),
            lambda d: d["training"]["datasets"][0].update(revision="a" * 40),
            lambda d: d["benchmark"].update(eval_version="v1-plus"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                path.write_bytes(original)
                self.mutate("submission.json", mutation)
                with self.assertRaises(board.Refusal):
                    board.validate(self.root)

    def test_removed_review_status_and_rerun_hash_are_refused(self):
        with self.assertRaisesRegex(board.Refusal, "review:"):
            self.review("verified")
        review_path = self.root / "reviews" / IDENTITY / "review.json"
        for update in ({"status": "verified"}, {"rerun_sha256": "d" * 64}):
            with self.subTest(update=update):
                record = self.review()
                record.update(update)
                write(review_path, record)
                with self.assertRaisesRegex(board.Refusal, "review:"):
                    board.validate(self.root)

    def test_legacy_report_wording_preserves_submitted_scores_and_bundle(self):
        self.mutate("validation.json", lambda data: data.update(verification="not independently rerun"))
        self.review()
        row = board.build(self.root, self.root / "site")["entries"][0]
        self.assertEqual(row["status"], "reported")
        self.assertEqual(row["scores"]["overall_sr"], 0.5)
        self.assertEqual(row["scores"], row["reported_scores"])
        self.assertEqual(row["artifact_bundle"], board.read_json(self.folder / "entry.json")["artifact_bundle"])

    def test_withdrawal_retains_files_and_hides_result(self):
        self.review("withdrawn")
        data = board.validate(self.root)
        self.assertEqual(data["entries"], [])
        self.assertEqual(data["pending"], [])
        self.assertTrue((self.folder / "validation.json").exists())

    def test_build_handles_empty_board_and_escapes_model_text(self):
        malicious = '</script><script>alert("injection")</script>'
        self.mutate("submission.json", lambda d: d["model"].update(name=malicious))
        self.mutate("validation.json", lambda d: d["model"].update(name=malicious))
        self.review()
        output = self.root / "site"
        board.build(self.root, output)
        self.assertNotIn(malicious, (output / "index.html").read_text())
        self.assertEqual(board.read_json(output / "leaderboard.json")["entries"][0]["model"], malicious)
        empty = self.root / "empty"
        write(empty / "releases.json", {"schema_version": 1, "releases": []})
        self.assertEqual(board.build(empty, self.root / "empty-site")["entries"], [])

    def test_cli_scaffold_and_review_output_do_not_clobber(self):
        args = ["--root", str(self.root), "new", "test-team/new-model/revision-1"]
        self.assertEqual(board.main(args), 0)
        self.assertEqual(board.main(args), 1)
        candidate = self.root / "review-candidate.json"
        board.write_new(candidate, {"test": True})
        with self.assertRaises(FileExistsError):
            board.write_new(candidate, {})


class PullRequestTests(Fixture):
    # Exercise the public trusted-base gate against real Git trees.
    def run_git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), "-c", "user.name=Test", "-c", "user.email=test@example.org", *args], stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.run_git("add", ".")
        self.run_git("commit", "-m", "Fixture")
        return self.run_git("rev-parse", "HEAD")

    def prepare(self):
        self.run_git("init", "-b", "duke/test")
        base = self.commit()
        shutil.copytree(self.folder, self.folder.with_name("revision-2"))
        return base

    def test_gate_admits_data_only_additions(self):
        base = self.prepare()
        board.check_pr(self.root, base, self.commit())

    def test_gate_refuses_changes_to_registered_keys(self):
        self.run_git("init", "-b", "duke/test")
        path = self.root / "models" / "outside.json"
        write(path, {"immutable": "fixture"})
        base = self.commit()
        write(path, {"immutable": "changed"})
        with self.assertRaisesRegex(board.Refusal, "registered model keys are immutable"):
            board.check_pr(self.root, base, self.commit())

    def test_gate_refuses_mixed_infrastructure_and_entry_changes(self):
        base = self.prepare()
        (self.root / "evil.py").write_text('raise RuntimeError("must never execute")')
        with self.assertRaisesRegex(board.Refusal, "may only add complete entries"):
            board.check_pr(self.root, base, self.commit())

    def test_gate_refuses_changed_entries_and_symlink_blobs(self):
        base = self.prepare()
        (self.folder / "method_card.md").write_text("changed")
        with self.assertRaisesRegex(board.Refusal, "may only add complete entries"):
            board.check_pr(self.root, base, self.commit())
        base = self.run_git("rev-parse", "HEAD")
        shutil.copytree(self.folder.with_name("revision-2"), self.folder.with_name("revision-3"))
        path = self.folder.with_name("revision-3") / "entry.json"
        path.unlink()
        path.symlink_to("/etc/passwd")
        with self.assertRaisesRegex(board.Refusal, "ordinary metadata files"):
            board.check_pr(self.root, base, self.commit())


if __name__ == "__main__":
    unittest.main()
