# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "plan_pr_wave.py"
SKILL = Path(__file__).parents[1] / "SKILL.md"
EXECUTION_REFERENCE = Path(__file__).parents[1] / "references" / "execution-and-verification.md"


def load_planner():
    spec = importlib.util.spec_from_file_location("plan_pr_wave", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pr(
    number: int,
    head: str,
    base: str,
    *,
    author: str = "alice",
    created: str = "2026-08-05T12:00:00Z",
    files: list[dict[str, object]] | None = None,
    state: str = "OPEN",
):
    return {
        "number": number,
        "id": f"PR_{number}",
        "url": f"https://github.com/acme/widgets/pull/{number}",
        "state": state,
        "author": {"login": author},
        "createdAt": created,
        "headRefName": head,
        "headRefOid": f"{number:040x}",
        "headRepository": {"nameWithOwner": "acme/widgets"},
        "baseRefName": base,
        "baseRefOid": f"{number + 1000:040x}",
        "baseRepository": {"nameWithOwner": "acme/widgets"},
        "isDraft": False,
        "maintainerCanModify": True,
        "files": files or [],
    }


class SelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_planner()
        cls.prs = [
            pr(100, "100", "main", created="2026-08-01T07:00:00Z"),
            pr(101, "feature-x", "main", created="2026-08-02T07:00:00Z"),
            pr(102, "2026-08-01", "main", author="bob"),
            pr(103, "later", "main", created="2026-08-12T06:59:59Z"),
            pr(104, "closed", "main", state="CLOSED"),
        ]

    def test_union_deduplicates_explicit_and_range_selectors(self):
        selected = self.m.resolve_selectors(
            ["100", "pr:100..101", "branch:feature-x"], self.prs, "alice"
        )
        self.assertEqual([100, 101], [item.number for item in selected])

    def test_bare_number_prefers_pr_over_numeric_branch(self):
        selected = self.m.resolve_selectors(["100"], self.prs, "alice")
        self.assertEqual([100], [item.number for item in selected])

    def test_date_bounds_use_local_calendar_days(self):
        selected = self.m.resolve_selectors(
            ["date:2026-08-01..2026-08-11"],
            self.prs,
            "alice",
            local_tz=self.m.ZoneInfo("America/Los_Angeles"),
        )
        self.assertEqual([100, 101, 103], [item.number for item in selected])

    def test_fixed_offset_local_timezone_is_preserved(self):
        prs = [pr(99, "west-coast", "main", created="2026-08-01T06:59:59Z")]
        selected = self.m.resolve_selectors(
            ["date:2026-07-31"],
            prs,
            "alice",
            local_tz=timezone(-timedelta(hours=7)),
        )
        self.assertEqual([99], [item.number for item in selected])

    def test_range_selectors_exclude_foreign_authors(self):
        selected = self.m.resolve_selectors([">=100"], self.prs, "alice")
        self.assertEqual([100, 101, 103], [item.number for item in selected])

    def test_explicit_foreign_pr_is_returned_with_ownership_warning(self):
        plan = self.m.plan_wave(["102"], self.prs, "alice")
        self.assertEqual([102], [item.number for item in plan.selected])
        self.assertEqual([102], [warning.number for warning in plan.ownership_warnings])

    def test_closed_explicit_pr_is_rejected(self):
        with self.assertRaisesRegex(self.m.PlannerError, "not open"):
            self.m.resolve_selectors(["104"], self.prs, "alice")


class GraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_planner()

    def test_external_roots_are_grouped_by_selected_pr_count(self):
        prs = [
            pr(1, "a", "main"),
            pr(2, "b", "a"),
            pr(3, "c", "release/2.x"),
        ]
        plan = self.m.plan_wave(["1", "2", "3"], prs, "alice")
        self.assertEqual({"acme/widgets:main": [1, 2], "acme/widgets:release/2.x": [3]}, plan.root_groups)
        self.assertEqual(["acme/widgets:release/2.x"], plan.minority_roots)

    def test_tied_external_roots_have_no_implicit_majority(self):
        plan = self.m.plan_wave(
            ["1", "2"], [pr(1, "a", "main"), pr(2, "b", "release")], "alice"
        )
        self.assertIsNone(plan.majority_root)
        self.assertEqual(["acme/widgets:main", "acme/widgets:release"], plan.tied_roots)

    def test_duplicate_heads_are_rejected(self):
        with self.assertRaisesRegex(self.m.PlannerError, "duplicate head"):
            self.m.plan_wave(["1", "2"], [pr(1, "a", "main"), pr(2, "a", "main")], "alice")

    def test_same_branch_name_in_distinct_forks_is_not_a_duplicate_node(self):
        first = pr(1, "feature", "main", author="bob")
        second = pr(2, "feature", "main", author="carol")
        first["headRepository"] = {"nameWithOwner": "bob/widgets"}
        second["headRepository"] = {"nameWithOwner": "carol/widgets"}
        plan = self.m.plan_wave(["1", "2"], [first, second], "alice", repository="acme/widgets")
        self.assertEqual([1, 2], [item.number for item in plan.selected])
        self.assertEqual({"acme/widgets:main": [1, 2]}, plan.root_groups)

    def test_cycle_is_rejected(self):
        with self.assertRaisesRegex(self.m.PlannerError, "cycle"):
            self.m.plan_wave(["1", "2"], [pr(1, "a", "b"), pr(2, "b", "a")], "alice")

    def test_sibling_branches_require_approved_linearization(self):
        plan = self.m.plan_wave(["1", "2"], [pr(1, "a", "main"), pr(2, "b", "main")], "alice")
        self.assertEqual([[1, 2]], plan.ordering_decisions)

    def test_unique_connector_path_from_minority_root_is_reported(self):
        prs = [
            pr(1, "a", "main"),
            pr(2, "b", "a"),
            pr(3, "c", "release"),
            pr(4, "release", "main"),
        ]
        plan = self.m.plan_wave(["1", "2", "3"], prs, "alice")
        self.assertEqual({"acme/widgets:release": [4]}, plan.connector_paths)

    def test_ambiguous_connector_path_is_reported_without_choosing(self):
        prs = [
            pr(1, "a", "main"),
            pr(2, "b", "a"),
            pr(3, "c", "release"),
            pr(4, "release", "main"),
            pr(5, "release", "staging"),
        ]
        plan = self.m.plan_wave(["1", "2", "3"], prs, "alice")
        self.assertEqual([4, 5], plan.ambiguous_connectors["acme/widgets:release"])
        self.assertNotIn("acme/widgets:release", plan.connector_paths)


class DiffRiskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_planner()

    def test_overlapping_hunks_are_reported(self):
        files_a = [{"path": "app.py", "status": "modified", "patch": "@@ -10,3 +10,4 @@"}]
        files_b = [{"path": "app.py", "status": "modified", "patch": "@@ -12,2 +12,5 @@"}]
        plan = self.m.plan_wave(["1", "2"], [pr(1, "a", "main", files=files_a), pr(2, "b", "main", files=files_b)], "alice")
        self.assertEqual("overlapping-hunks", plan.diff_risks[0].kind)

    def test_overlap_uses_shared_base_coordinates(self):
        replacement = [{"path": "app.py", "status": "modified", "patch": "@@ -1,100 +1,1 @@"}]
        middle_edit = [{"path": "app.py", "status": "modified", "patch": "@@ -50,1 +50,1 @@"}]
        plan = self.m.plan_wave(["1", "2"], [pr(1, "a", "main", files=replacement), pr(2, "b", "main", files=middle_edit)], "alice")
        self.assertEqual("whole-file-replacement", plan.diff_risks[0].kind)

    def test_binary_rename_and_delete_are_conservative_risks(self):
        files_a = [{"path": "logo.bin", "status": "modified", "patch": None}]
        files_b = [{"path": "logo.bin", "status": "removed", "patch": "@@ -1 +0,0 @@"}]
        plan = self.m.plan_wave(["1", "2"], [pr(1, "a", "main", files=files_a), pr(2, "b", "main", files=files_b)], "alice")
        self.assertEqual("binary-or-unavailable-patch", plan.diff_risks[0].kind)

    def test_previous_filename_detects_rename_interaction(self):
        renamed = [{"filename": "new.py", "previous_filename": "old.py", "status": "renamed", "patch": "@@ -1 +1 @@"}]
        edited = [{"filename": "old.py", "status": "modified", "patch": "@@ -1 +1 @@"}]
        plan = self.m.plan_wave(["1", "2"], [pr(1, "a", "main", files=renamed), pr(2, "b", "main", files=edited)], "alice")
        self.assertEqual("rename-delete-or-copy", plan.diff_risks[0].kind)

    def test_generated_file_pair_is_conservatively_flagged(self):
        first = [{"path": "generated/client.ts", "status": "modified", "patch": "@@ -1 +1 @@"}]
        second = [{"path": "generated/client.ts", "status": "modified", "patch": "@@ -20 +20 @@"}]
        plan = self.m.plan_wave(["1", "2"], [pr(1, "a", "main", files=first), pr(2, "b", "main", files=second)], "alice")
        self.assertEqual("generated-artifact", plan.diff_risks[0].kind)

    def test_common_lock_and_codegen_outputs_are_flagged(self):
        for path in (
            "package-lock.json", "Gemfile.lock", "Pipfile.lock", "poetry.lock",
            "uv.lock", "bun.lock", "bun.lockb", "flake.lock",
            "api/schema.pb.go", "public/app.min.js",
        ):
            with self.subTest(path=path):
                files = [{"path": path, "status": "modified", "patch": "@@ -1 +1 @@"}]
                plan = self.m.plan_wave(["1", "2"], [pr(1, "a", "main", files=files), pr(2, "b", "main", files=files)], "alice")
                self.assertEqual("generated-artifact", plan.diff_risks[0].kind)


class CliTests(unittest.TestCase):
    def test_hydration_batches_selected_prs_five_at_a_time(self):
        module = load_planner()
        items = [pr(number, f"branch-{number}", "main") for number in range(1, 13)]
        batches = []

        def fetch(batch):
            batches.append([item["number"] for item in batch])
            return [
                {
                    **item,
                    "statusCheckRollup": [{"name": f"check-{item['number']}"}],
                    "latestReviews": [{"state": "APPROVED"}],
                    "reviewRequests": [{"login": "reviewer"}],
                }
                for item in batch
            ]

        module.hydrate_selected_prs(items, fetch)

        self.assertEqual([[1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [11, 12]], batches)
        self.assertEqual("check-1", items[0]["statusCheckRollup"][0]["name"])

    def test_hydration_bisects_timeout_without_retrying_same_batch(self):
        module = load_planner()
        items = [pr(number, f"branch-{number}", "main") for number in range(1, 6)]
        batches = []

        def fetch(batch):
            numbers = [item["number"] for item in batch]
            batches.append(numbers)
            if len(batch) > 3:
                raise subprocess.CalledProcessError(
                    1, ["gh", "api", "graphql"], stderr="HTTP 504: We couldn't respond to your request in time."
                )
            return [{**item, "statusCheckRollup": [], "latestReviews": [], "reviewRequests": []} for item in batch]

        module.hydrate_selected_prs(items, fetch)

        self.assertEqual([[1, 2, 3, 4, 5], [1, 2], [3, 4, 5]], batches)

    def test_hydration_reports_singleton_timeout(self):
        module = load_planner()
        item = pr(7, "heavy", "main")

        def fetch(_batch):
            raise subprocess.CalledProcessError(
                1, ["gh", "api", "graphql"], stderr="HTTP 502: We couldn't respond to your request in time."
            )

        with self.assertRaisesRegex(module.PlannerError, "PR #7.*HTTP 502"):
            module.hydrate_selected_prs([item], fetch)

    def test_hydration_does_not_split_non_timeout_errors(self):
        module = load_planner()
        items = [pr(1, "one", "main"), pr(2, "two", "main")]
        calls = 0

        def fetch(_batch):
            nonlocal calls
            calls += 1
            raise subprocess.CalledProcessError(1, ["gh", "api", "graphql"], stderr="HTTP 403: forbidden")

        with self.assertRaises(subprocess.CalledProcessError):
            module.hydrate_selected_prs(items, fetch)
        self.assertEqual(1, calls)

    def test_hydration_rejects_head_drift(self):
        module = load_planner()
        item = pr(1, "one", "main")

        def fetch(batch):
            return [{**batch[0], "headRefOid": "changed", "statusCheckRollup": [], "latestReviews": [], "reviewRequests": []}]

        with self.assertRaisesRegex(module.PlannerError, "changed during hydration"):
            module.hydrate_selected_prs([item], fetch)

    def test_hydration_rejects_missing_duplicate_and_closed_nodes(self):
        module = load_planner()
        first = pr(1, "one", "main")
        second = pr(2, "two", "main")

        with self.subTest("missing"):
            with self.assertRaisesRegex(module.PlannerError, "hydrated PR set changed"):
                module.hydrate_selected_prs([first, second], lambda _batch: [{**first}])
        with self.subTest("duplicate"):
            with self.assertRaisesRegex(module.PlannerError, "duplicate hydrated node"):
                module.hydrate_selected_prs([first], lambda _batch: [{**first}, {**first}])
        with self.subTest("closed"):
            closed = {**first, "state": "CLOSED"}
            with self.assertRaisesRegex(module.PlannerError, "changed during hydration|closed during hydration"):
                module.hydrate_selected_prs([first], lambda _batch: [closed])

    def test_graphql_hydration_normalizes_nested_output(self):
        module = load_planner()
        item = pr(1, "one", "main")
        payload = {
            "data": {
                "nodes": [{
                    **item,
                    "statusCheckRollup": {"contexts": {"nodes": [
                        {"__typename": "StatusContext", "context": "lint", "state": "SUCCESS", "createdAt": "2026-08-12T00:00:00Z"},
                        {"__typename": "CheckRun", "name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS", "checkSuite": {"workflowRun": {"workflow": {"name": "CI"}}}},
                    ]}},
                    "latestReviews": {"nodes": [{"state": "APPROVED"}]},
                    "reviewRequests": {"nodes": [{"requestedReviewer": {"__typename": "User", "login": "bob"}}]},
                }]
            }
        }

        with mock.patch.object(module, "run_read_only", return_value=json.dumps(payload)) as run:
            result = module._fetch_hydration_batch([item])

        command = run.call_args.args[0]
        self.assertIn("ids[]=PR_1", command)
        self.assertEqual("2026-08-12T00:00:00Z", result[0]["statusCheckRollup"][0]["startedAt"])
        self.assertEqual("CI", result[0]["statusCheckRollup"][1]["workflowName"])
        self.assertEqual([{"state": "APPROVED"}], result[0]["latestReviews"])
        self.assertEqual("bob", result[0]["reviewRequests"][0]["login"])

    def test_fixture_mode_produces_stable_json_without_mutations(self):
        fixture = {"viewer": "alice", "repository": "acme/widgets", "pullRequests": [pr(1, "a", "main")]}
        with tempfile.TemporaryDirectory() as directory:
            fixture_path = Path(directory) / "fixture.json"
            fixture_path.write_text(json.dumps(fixture))
            first = subprocess.run(
                [sys.executable, str(SCRIPT), "--fixture", str(fixture_path), "--selector", "1", "--format", "json"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            second = subprocess.run(
                [sys.executable, str(SCRIPT), "--fixture", str(fixture_path), "--selector", "1", "--format", "json"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        self.assertEqual(first, second)
        self.assertEqual([1], json.loads(first)["selected_prs"])

    def test_live_mode_fetches_file_patches_only_for_selected_prs(self):
        module = load_planner()
        raw = [pr(1, "a", "main"), pr(2, "b", "main")]
        for item in raw:
            item.pop("files")
            item["changedFiles"] = 0
        raw[0]["changedFiles"] = 1
        responses = {
            ("gh", "api", "user", "--jq", ".login"): "alice\n",
            ("gh", "repo", "view", "--json", "nameWithOwner,viewerPermission"): json.dumps(
                {"nameWithOwner": "acme/widgets", "viewerPermission": "WRITE"}
            ),
            ("gh", "api", "graphql", "-F", "owner=acme", "-F", "name=widgets", "-f", "query=query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { pullRequests(states: OPEN) { totalCount } } }"): json.dumps(
                {"data": {"repository": {"pullRequests": {"totalCount": 2}}}}
            ),
        }

        def fake_run(command):
            key = tuple(command)
            if key in responses:
                return responses[key]
            if command[:3] == ["gh", "pr", "list"]:
                fields = command[command.index("--json") + 1]
                self.assertNotIn("statusCheckRollup", fields)
                self.assertNotIn("latestReviews", fields)
                self.assertNotIn("reviewRequests", fields)
                return json.dumps(raw)
            if command == ["gh", "api", "repos/acme/widgets/pulls/1/files", "--paginate", "--slurp"]:
                return json.dumps([[{"filename": "app.py", "status": "modified", "patch": "@@ -1 +1 @@"}]])
            self.fail(f"unexpected command: {command}")

        def hydrate(batch):
            return [{**item, "statusCheckRollup": [], "latestReviews": [], "reviewRequests": []} for item in batch]

        with mock.patch.object(module, "run_read_only", side_effect=fake_run), mock.patch.object(
            module, "_fetch_hydration_batch", side_effect=hydrate
        ):
            viewer, repository, prs, permission = module.load_live(["1"])
        self.assertEqual(("alice", "acme/widgets"), (viewer, repository))
        self.assertEqual("WRITE", permission)
        self.assertEqual("app.py", prs[0]["files"][0]["filename"])
        self.assertNotIn("files", prs[1])

    def test_live_mode_rejects_incomplete_selected_pr_file_list(self):
        module = load_planner()
        raw = [pr(1, "a", "main")]
        raw[0].pop("files")
        raw[0]["changedFiles"] = 2
        responses = {
            ("gh", "api", "user", "--jq", ".login"): "alice\n",
            ("gh", "repo", "view", "--json", "nameWithOwner,viewerPermission"): json.dumps(
                {"nameWithOwner": "acme/widgets", "viewerPermission": "WRITE"}
            ),
            ("gh", "api", "graphql", "-F", "owner=acme", "-F", "name=widgets", "-f", "query=query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { pullRequests(states: OPEN) { totalCount } } }"): json.dumps(
                {"data": {"repository": {"pullRequests": {"totalCount": 1}}}}
            ),
        }

        def fake_run(command):
            key = tuple(command)
            if key in responses:
                return responses[key]
            if command[:3] == ["gh", "pr", "list"]:
                return json.dumps(raw)
            if command == ["gh", "api", "repos/acme/widgets/pulls/1/files", "--paginate", "--slurp"]:
                return json.dumps([[{"filename": "app.py", "status": "modified", "patch": "@@ -1 +1 @@"}]])
            self.fail(f"unexpected command: {command}")

        def hydrate(batch):
            return [{**item, "statusCheckRollup": [], "latestReviews": [], "reviewRequests": []} for item in batch]

        with mock.patch.object(module, "run_read_only", side_effect=fake_run), mock.patch.object(
            module, "_fetch_hydration_batch", side_effect=hydrate
        ):
            with self.assertRaisesRegex(module.PlannerError, "incomplete file list"):
                module.load_live(["1"])

    def test_snapshot_records_creation_time(self):
        module = load_planner()
        item = pr(1, "a", "main", created="2026-08-05T12:34:56Z")
        plan = module.plan_wave(["1"], [item], "alice")
        self.assertEqual("2026-08-05T12:34:56+00:00", plan.to_dict()["snapshots"]["1"]["created_at"])

    def test_missing_head_repository_fails_closed(self):
        module = load_planner()
        item = pr(1, "fork", "main")
        item["headRepository"] = None
        with self.assertRaisesRegex(module.PlannerError, "missing head repository"):
            module.PullRequest.from_json(item, "acme/widgets")

    def test_command_guard_rejects_mutating_gh_calls(self):
        module = load_planner()
        with self.assertRaisesRegex(module.PlannerError, "refusing non-read-only"):
            module.run_read_only(["gh", "pr", "edit", "1", "--add-reviewer", "bob"])


class SkillDocumentationTests(unittest.TestCase):
    def test_parallel_reviews_and_fixes_require_pr_isolated_workspaces(self):
        text = SKILL.read_text() + "\n" + EXECUTION_REFERENCE.read_text()
        self.assertIn("one isolated worktree or jj workspace per PR", text)
        self.assertIn("Reviewers remain read-only", text)
        self.assertIn("Only the orchestrator may rebase, cascade, push, or relink", text)

    def test_selection_guidance_requires_bounded_hydration(self):
        text = (SKILL.parent / "references" / "selection-and-topology.md").read_text()
        self.assertIn("batches of at most five", text)
        self.assertIn("splits only the failed batch recursively", text)
        self.assertIn("Never replace this with an all-open query", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
