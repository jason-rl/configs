#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Build a read-only plan for coordinating a wave of GitHub pull requests."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError


PR_URL = re.compile(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$")
PR_RANGE = re.compile(r"^(\d+)\.\.(\d+)$")
COMPARATOR = re.compile(r"^(<=|>=|<|>)(.+)$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
HYDRATION_BATCH_SIZE = 5
HYDRATION_IDENTITY_FIELDS = (
    "id", "number", "state", "url", "createdAt", "headRefName", "headRefOid",
    "headRepository", "baseRefName", "baseRefOid", "baseRepository", "isDraft",
    "maintainerCanModify",
)


class PlannerError(ValueError):
    """An input cannot be planned safely."""


def required_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise PlannerError(f"pull request field {key!r} must be a non-empty string")
    return value


def nested_login(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if isinstance(value, Mapping):
        return required_string(value, "login")
    if isinstance(value, str) and value:
        return value
    raise PlannerError(f"pull request field {key!r} must identify a login")


@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    status: str
    patch: str | None = None
    previous_path: str | None = None
    additions: int = 0
    deletions: int = 0

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "FileChange":
        path = data.get("path") or data.get("filename")
        if not isinstance(path, str) or not path:
            raise PlannerError("changed file must have a path or filename")
        patch = data.get("patch")
        if patch is not None and not isinstance(patch, str):
            raise PlannerError(f"patch for {path!r} must be a string or null")
        return cls(
            path=path,
            status=str(data.get("status", "modified")),
            patch=patch,
            previous_path=data.get("previous_path") or data.get("previous_filename"),
            additions=int(data.get("additions", 0)),
            deletions=int(data.get("deletions", 0)),
        )


@dataclass(frozen=True, slots=True)
class PullRequest:
    number: int
    url: str
    state: str
    author: str
    created_at: datetime
    head: str
    head_oid: str
    head_repository: str
    base: str
    base_oid: str
    base_repository: str
    is_draft: bool
    maintainer_can_modify: bool
    files: tuple[FileChange, ...] = ()
    stack_id: int | None = None
    checks: tuple[Mapping[str, Any], ...] = ()
    reviews: tuple[Mapping[str, Any], ...] = ()
    review_requests: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_json(cls, data: Mapping[str, Any], repository: str | None = None) -> "PullRequest":
        try:
            number = int(data["number"])
        except (KeyError, TypeError, ValueError) as error:
            raise PlannerError("pull request number must be an integer") from error
        head_repo = data.get("headRepository")
        if isinstance(head_repo, Mapping):
            head_repo = head_repo.get("nameWithOwner") or head_repo.get("name")
        base_repo = data.get("baseRepository")
        if isinstance(base_repo, Mapping):
            base_repo = base_repo.get("nameWithOwner") or base_repo.get("name")
        if not head_repo:
            raise PlannerError(f"PR #{number} is missing head repository identity")
        base_repo = base_repo or repository
        if not isinstance(head_repo, str) or not isinstance(base_repo, str):
            raise PlannerError(f"PR #{number} is missing repository identity")
        files = data.get("files", ())
        if not isinstance(files, Sequence) or isinstance(files, (str, bytes)):
            raise PlannerError(f"files for PR #{number} must be an array")
        return cls(
            number=number,
            url=required_string(data, "url"),
            state=required_string(data, "state").upper(),
            author=nested_login(data, "author"),
            created_at=parse_timestamp(required_string(data, "createdAt")),
            head=required_string(data, "headRefName"),
            head_oid=required_string(data, "headRefOid"),
            head_repository=head_repo,
            base=required_string(data, "baseRefName"),
            base_oid=required_string(data, "baseRefOid"),
            base_repository=base_repo,
            is_draft=bool(data.get("isDraft", False)),
            maintainer_can_modify=bool(data.get("maintainerCanModify", False)),
            files=tuple(FileChange.from_json(item) for item in files),
            stack_id=int(data["stackId"]) if data.get("stackId") is not None else None,
            checks=tuple(data.get("statusCheckRollup") or ()),
            reviews=tuple(data.get("latestReviews") or ()),
            review_requests=tuple(data.get("reviewRequests") or ()),
        )


@dataclass(frozen=True, slots=True)
class OwnershipWarning:
    number: int
    author: str
    authenticated_user: str


@dataclass(frozen=True, slots=True)
class DiffRisk:
    first_pr: int
    second_pr: int
    path: str
    kind: str
    first_lines: tuple[int, int] | None = None
    second_lines: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class WavePlan:
    selected: tuple[PullRequest, ...]
    ownership_warnings: tuple[OwnershipWarning, ...]
    edges: dict[str, str]
    root_groups: dict[str, list[int]]
    majority_root: str | None
    minority_roots: list[str]
    tied_roots: list[str]
    ordering_decisions: list[list[int]]
    diff_risks: tuple[DiffRisk, ...]
    connector_paths: dict[str, list[int]] = field(default_factory=dict)
    ambiguous_connectors: dict[str, list[int]] = field(default_factory=dict)
    existing_stacks: dict[int, list[int]] = field(default_factory=dict)
    stack_membership_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_prs": [item.number for item in self.selected],
            "snapshots": {
                str(item.number): {
                    "head": item.head,
                    "head_oid": item.head_oid,
                    "head_repository": item.head_repository,
                    "base": item.base,
                    "base_oid": item.base_oid,
                    "base_repository": item.base_repository,
                    "author": item.author,
                    "created_at": item.created_at.isoformat(),
                    "is_draft": item.is_draft,
                    "maintainer_can_modify": item.maintainer_can_modify,
                    "checks": list(item.checks),
                    "latest_reviews": list(item.reviews),
                    "review_requests": list(item.review_requests),
                }
                for item in self.selected
            },
            "ownership_warnings": [asdict(item) for item in self.ownership_warnings],
            "edges": dict(sorted(self.edges.items())),
            "root_groups": self.root_groups,
            "majority_root": self.majority_root,
            "minority_roots": self.minority_roots,
            "tied_roots": self.tied_roots,
            "ordering_decisions": self.ordering_decisions,
            "diff_risks": [asdict(item) for item in self.diff_risks],
            "connector_paths": self.connector_paths,
            "ambiguous_connectors": self.ambiguous_connectors,
            "existing_stacks": {str(key): value for key, value in sorted(self.existing_stacks.items())},
            "stack_membership_complete": self.stack_membership_complete,
        }


def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PlannerError(f"invalid ISO timestamp: {value}") from error
    if parsed.tzinfo is None:
        raise PlannerError(f"timestamp lacks a timezone: {value}")
    return parsed


def parse_date(value: str) -> date:
    if not ISO_DATE.fullmatch(value):
        raise PlannerError(f"invalid ISO date: {value}")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise PlannerError(f"invalid ISO date: {value}") from error


def local_day_bounds(value: date, local_tz: tzinfo) -> tuple[datetime, datetime]:
    start = datetime.combine(value, time.min, local_tz)
    return start, start + timedelta(days=1)


def _select_explicit_number(number: int, prs: Sequence[PullRequest]) -> PullRequest:
    matches = [item for item in prs if item.number == number]
    if not matches:
        raise PlannerError(f"PR #{number} was not found")
    item = matches[0]
    if item.state != "OPEN":
        raise PlannerError(f"PR #{number} is not open")
    return item


def _select_branch(branch: str, prs: Sequence[PullRequest]) -> PullRequest:
    matches = [item for item in prs if item.head == branch and item.state == "OPEN"]
    if not matches:
        raise PlannerError(f"no open PR has head branch {branch!r}")
    if len(matches) > 1:
        raise PlannerError(f"head branch {branch!r} is ambiguous")
    return matches[0]


def _numeric_range(selector: str) -> tuple[int, int] | None:
    match = PR_RANGE.fullmatch(selector)
    if not match:
        return None
    first, last = map(int, match.groups())
    return (min(first, last), max(first, last))


def _date_predicate(selector: str, local_tz: ZoneInfo):
    if ".." in selector:
        first_text, last_text = selector.split("..", 1)
        first, last = sorted((parse_date(first_text), parse_date(last_text)))
        start, _ = local_day_bounds(first, local_tz)
        _, end = local_day_bounds(last, local_tz)
        return lambda item: start <= item.created_at.astimezone(local_tz) < end
    match = COMPARATOR.fullmatch(selector)
    if match and ISO_DATE.fullmatch(match.group(2)):
        operator, date_text = match.groups()
        start, end = local_day_bounds(parse_date(date_text), local_tz)
        if operator == ">=":
            return lambda item: item.created_at.astimezone(local_tz) >= start
        if operator == ">":
            return lambda item: item.created_at.astimezone(local_tz) >= end
        if operator == "<=":
            return lambda item: item.created_at.astimezone(local_tz) < end
        return lambda item: item.created_at.astimezone(local_tz) < start
    if ISO_DATE.fullmatch(selector):
        start, end = local_day_bounds(parse_date(selector), local_tz)
        return lambda item: start <= item.created_at.astimezone(local_tz) < end
    return None


def resolve_selectors(
    selectors: Sequence[str],
    raw_prs: Sequence[PullRequest | Mapping[str, Any]],
    authenticated_user: str,
    *,
    repository: str | None = None,
    local_tz: tzinfo | None = None,
) -> tuple[PullRequest, ...]:
    if not selectors:
        raise PlannerError("at least one selector is required")
    prs = tuple(
        item if isinstance(item, PullRequest) else PullRequest.from_json(item, repository)
        for item in raw_prs
    )
    local_tz = local_tz or datetime.now().astimezone().tzinfo
    chosen: dict[int, PullRequest] = {}

    for original in selectors:
        selector = original.strip()
        prefix = None
        if ":" in selector and selector.split(":", 1)[0] in {"pr", "branch", "date"}:
            prefix, selector = selector.split(":", 1)
        url_match = PR_URL.fullmatch(selector)
        if url_match:
            item = _select_explicit_number(int(url_match.group(3)), prs)
            if repository and f"{url_match.group(1)}/{url_match.group(2)}" != repository:
                raise PlannerError(f"PR URL {original!r} belongs to another repository")
            chosen[item.number] = item
            continue
        if prefix == "branch":
            item = _select_branch(selector, prs)
            chosen[item.number] = item
            continue
        date_predicate = _date_predicate(selector, local_tz) if prefix in {None, "date"} else None
        if date_predicate is not None:
            for item in prs:
                if item.state == "OPEN" and item.author == authenticated_user and date_predicate(item):
                    chosen[item.number] = item
            continue
        number_range = _numeric_range(selector) if prefix in {None, "pr"} else None
        if number_range:
            for item in prs:
                if item.state == "OPEN" and item.author == authenticated_user and number_range[0] <= item.number <= number_range[1]:
                    chosen[item.number] = item
            continue
        comparator = COMPARATOR.fullmatch(selector) if prefix in {None, "pr"} else None
        if comparator and comparator.group(2).isdigit():
            operator, bound_text = comparator.groups()
            bound = int(bound_text)
            compare = {
                ">=": lambda number: number >= bound,
                ">": lambda number: number > bound,
                "<=": lambda number: number <= bound,
                "<": lambda number: number < bound,
            }[operator]
            for item in prs:
                if item.state == "OPEN" and item.author == authenticated_user and compare(item.number):
                    chosen[item.number] = item
            continue
        if selector.isdigit() and prefix in {None, "pr"}:
            item = _select_explicit_number(int(selector), prs)
            chosen[item.number] = item
            continue
        if prefix in {None, "branch"}:
            item = _select_branch(selector, prs)
            chosen[item.number] = item
            continue
        raise PlannerError(f"invalid selector: {original!r}")

    if not chosen:
        raise PlannerError("selectors resolved to no open pull requests")
    return tuple(chosen[number] for number in sorted(chosen))


Node = tuple[str, str]


def node_label(node: Node) -> str:
    return f"{node[0]}:{node[1]}"


def _trace_root(head: Node, edges: Mapping[Node, Node]) -> Node:
    seen: set[Node] = set()
    current = head
    while current in edges:
        if current in seen:
            cycle = " -> ".join(node_label(node) for node in [*sorted(seen), current])
            raise PlannerError(f"selected PR graph contains a cycle: {cycle}")
        seen.add(current)
        current = edges[current]
    return current


def _hunk_ranges(change: FileChange) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    if not change.patch:
        return []
    ranges = []
    for match in HUNK.finditer(change.patch):
        old_start = int(match.group(1))
        old_length = int(match.group(2) or "1")
        new_start = int(match.group(3))
        new_length = int(match.group(4) or "1")
        ranges.append(
            (
                (old_start, old_start + max(old_length, 1) - 1),
                (new_start, new_start + max(new_length, 1) - 1),
            )
        )
    return ranges


def _paths(change: FileChange) -> set[str]:
    return {path for path in (change.path, change.previous_path) if path}


def analyze_diff_risks(selected: Sequence[PullRequest]) -> tuple[DiffRisk, ...]:
    risks: list[DiffRisk] = []
    for index, first in enumerate(selected):
        for second in selected[index + 1 :]:
            for first_file in first.files:
                for second_file in second.files:
                    shared = sorted(_paths(first_file) & _paths(second_file))
                    if not shared:
                        continue
                    path = shared[0]
                    if first_file.patch is None or second_file.patch is None:
                        risks.append(DiffRisk(first.number, second.number, path, "binary-or-unavailable-patch"))
                        continue
                    special = {first_file.status, second_file.status} & {"removed", "renamed", "copied"}
                    if special:
                        risks.append(DiffRisk(first.number, second.number, path, "rename-delete-or-copy"))
                        continue
                    lowered = path.lower()
                    basename = lowered.rsplit("/", 1)[-1]
                    generated_names = {
                        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.lock",
                        "go.sum", "composer.lock", "gemfile.lock", "pipfile.lock",
                        "poetry.lock", "uv.lock", "bun.lock", "bun.lockb", "flake.lock",
                    }
                    generated_suffixes = (".pb.go", ".pb.cc", ".pb.h", ".min.js", ".min.css", ".generated.ts", "_generated.py")
                    if (
                        any(marker in lowered for marker in ("/generated/", "generated/", ".generated.", "_generated."))
                        or basename in generated_names
                        or basename.endswith(generated_suffixes)
                    ):
                        risks.append(DiffRisk(first.number, second.number, path, "generated-artifact"))
                        continue
                    first_hunks = _hunk_ranges(first_file)
                    second_hunks = _hunk_ranges(second_file)
                    if not first_hunks or not second_hunks:
                        risks.append(DiffRisk(first.number, second.number, path, "whole-file-or-unparseable-change"))
                        continue
                    if any((old[1] - old[0] + 1) >= 50 and (new[1] - new[0] + 1) * 4 <= (old[1] - old[0] + 1) for old, new in first_hunks + second_hunks):
                        risks.append(DiffRisk(first.number, second.number, path, "whole-file-replacement"))
                        continue
                    for first_range, _ in first_hunks:
                        for second_range, _ in second_hunks:
                            if first_range[0] <= second_range[1] and second_range[0] <= first_range[1]:
                                risks.append(
                                    DiffRisk(
                                        first.number,
                                        second.number,
                                        path,
                                        "overlapping-hunks",
                                        first_range,
                                        second_range,
                                    )
                                )
    return tuple(sorted(risks, key=lambda item: (item.first_pr, item.second_pr, item.path, item.kind)))


def plan_wave(
    selectors: Sequence[str],
    raw_prs: Sequence[PullRequest | Mapping[str, Any]],
    authenticated_user: str,
    *,
    repository: str | None = None,
    local_tz: tzinfo | None = None,
) -> WavePlan:
    all_prs = tuple(
        item if isinstance(item, PullRequest) else PullRequest.from_json(item, repository)
        for item in raw_prs
    )
    selected = resolve_selectors(
        selectors, all_prs, authenticated_user, repository=repository, local_tz=local_tz
    )
    heads: dict[Node, PullRequest] = {}
    for item in selected:
        head_node = (item.head_repository, item.head)
        if head_node in heads:
            raise PlannerError(f"duplicate head {node_label(head_node)!r} in PRs #{heads[head_node].number} and #{item.number}")
        heads[head_node] = item
    edges = {
        (item.head_repository, item.head): (item.base_repository, item.base)
        for item in selected
    }
    groups: dict[str, list[int]] = defaultdict(list)
    for item in selected:
        groups[node_label(_trace_root((item.head_repository, item.head), edges))].append(item.number)
    root_groups = {root: sorted(numbers) for root, numbers in sorted(groups.items())}
    largest = max(len(numbers) for numbers in root_groups.values())
    tied_roots = sorted(root for root, numbers in root_groups.items() if len(numbers) == largest)
    majority_root = tied_roots[0] if len(tied_roots) == 1 else None
    minority_roots = sorted(root for root in root_groups if root != majority_root) if majority_root else []

    connector_paths: dict[str, list[int]] = {}
    ambiguous_connectors: dict[str, list[int]] = {}
    if majority_root:
        candidates_by_head: dict[Node, list[PullRequest]] = defaultdict(list)
        for item in all_prs:
            if item.state == "OPEN" and item.number not in {selected_pr.number for selected_pr in selected}:
                candidates_by_head[(item.head_repository, item.head)].append(item)
        for minority_root in minority_roots:
            root_repo, root_branch = minority_root.split(":", 1)
            current = (root_repo, root_branch)
            path: list[int] = []
            visited: set[Node] = set()
            majority_node = tuple(majority_root.split(":", 1))
            while current != majority_node and current not in visited:
                visited.add(current)
                candidates = sorted(candidates_by_head.get(current, []), key=lambda item: item.number)
                if len(candidates) > 1:
                    ambiguous_connectors[minority_root] = [item.number for item in candidates]
                    path = []
                    break
                if not candidates:
                    path = []
                    break
                candidate = candidates[0]
                path.append(candidate.number)
                current = (candidate.base_repository, candidate.base)
            if current == majority_node and path:
                connector_paths[minority_root] = path

    siblings: dict[str, list[int]] = defaultdict(list)
    for item in selected:
        siblings[item.base].append(item.number)
    ordering = [sorted(numbers) for _, numbers in sorted(siblings.items()) if len(numbers) > 1]
    existing: dict[int, list[int]] = defaultdict(list)
    for item in selected:
        if item.stack_id is not None:
            existing[item.stack_id].append(item.number)

    return WavePlan(
        selected=selected,
        ownership_warnings=tuple(
            OwnershipWarning(item.number, item.author, authenticated_user)
            for item in selected
            if item.author != authenticated_user
        ),
        edges={node_label(head): node_label(base) for head, base in edges.items()},
        root_groups=root_groups,
        majority_root=majority_root,
        minority_roots=minority_roots,
        tied_roots=tied_roots if majority_root is None else [],
        ordering_decisions=ordering,
        diff_risks=analyze_diff_risks(selected),
        connector_paths=connector_paths,
        ambiguous_connectors=ambiguous_connectors,
        existing_stacks={key: sorted(value) for key, value in existing.items()},
    )


def run_read_only(command: Sequence[str]) -> str:
    allowed = {
        ("gh", "api"),
        ("gh", "pr", "list"),
        ("gh", "repo", "view"),
    }
    if not any(tuple(command[: len(prefix)]) == prefix for prefix in allowed):
        raise PlannerError(f"refusing non-read-only command: {' '.join(command)}")
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return completed.stdout


def _is_graphql_timeout(error: subprocess.CalledProcessError) -> bool:
    message = "\n".join(str(value or "") for value in (error.stderr, error.stdout))
    timed_out = bool(re.search(r"HTTP (?:502|504)\b", message))
    return timed_out and (
        "api.github.com/graphql" in message
        or "couldn't respond to your request in time" in message.lower()
    )


def _command_error_message(error: subprocess.CalledProcessError) -> str:
    return "\n".join(str(value).strip() for value in (error.stderr, error.stdout) if value).strip()


def _identity(value: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get("nameWithOwner") or value.get("name")
    return value


def _validate_hydrated_batch(
    expected: Sequence[Mapping[str, Any]], hydrated: Sequence[Mapping[str, Any]]
) -> None:
    expected_by_id = {required_string(item, "id"): item for item in expected}
    hydrated_by_id: dict[str, Mapping[str, Any]] = {}
    for item in hydrated:
        node_id = required_string(item, "id")
        if node_id in hydrated_by_id:
            raise PlannerError(f"GitHub returned duplicate hydrated node {node_id!r}")
        hydrated_by_id[node_id] = item
    if hydrated_by_id.keys() != expected_by_id.keys():
        missing = sorted(expected_by_id.keys() - hydrated_by_id.keys())
        unexpected = sorted(hydrated_by_id.keys() - expected_by_id.keys())
        raise PlannerError(f"hydrated PR set changed: missing={missing}, unexpected={unexpected}; rerun the planner")
    for node_id, before in expected_by_id.items():
        after = hydrated_by_id[node_id]
        for field_name in HYDRATION_IDENTITY_FIELDS:
            before_value = _identity(before.get(field_name))
            after_value = _identity(after.get(field_name))
            if before_value != after_value:
                raise PlannerError(
                    f"PR #{before.get('number')} changed during hydration ({field_name}); rerun the planner"
                )
        if str(after.get("state", "")).upper() != "OPEN":
            raise PlannerError(f"PR #{before.get('number')} closed during hydration; rerun the planner")


def hydrate_selected_prs(
    selected: Sequence[dict[str, Any]],
    fetch_batch,
) -> None:
    def hydrate_batch(batch: Sequence[dict[str, Any]]) -> None:
        try:
            hydrated = fetch_batch(batch)
        except subprocess.CalledProcessError as error:
            if not _is_graphql_timeout(error):
                raise
            if len(batch) == 1:
                detail = _command_error_message(error)
                raise PlannerError(
                    f"GitHub GraphQL hydration timed out for PR #{batch[0].get('number')}: {detail}"
                ) from error
            midpoint = len(batch) // 2
            hydrate_batch(batch[:midpoint])
            hydrate_batch(batch[midpoint:])
            return
        if not isinstance(hydrated, Sequence) or isinstance(hydrated, (str, bytes)):
            raise PlannerError("GitHub returned an invalid PR hydration response")
        _validate_hydrated_batch(batch, hydrated)
        hydrated_by_id = {required_string(item, "id"): item for item in hydrated}
        for item in batch:
            rich = hydrated_by_id[required_string(item, "id")]
            for field_name in ("statusCheckRollup", "latestReviews", "reviewRequests"):
                value = rich.get(field_name)
                if not isinstance(value, list):
                    raise PlannerError(
                        f"GitHub returned invalid {field_name} data for PR #{item.get('number')}"
                    )
                item[field_name] = value

    for start in range(0, len(selected), HYDRATION_BATCH_SIZE):
        hydrate_batch(selected[start : start + HYDRATION_BATCH_SIZE])


def _fetch_hydration_batch(batch: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    query = """query($ids: [ID!]!) {
      nodes(ids: $ids) {
        ... on PullRequest {
          id number state url createdAt headRefName headRefOid
          headRepository { nameWithOwner }
          baseRefName baseRefOid baseRepository: repository { nameWithOwner }
          isDraft maintainerCanModify
          statusCheckRollup { contexts(first: 100) { nodes {
            __typename
            ... on StatusContext { context state targetUrl createdAt }
            ... on CheckRun { name status conclusion startedAt completedAt detailsUrl checkSuite { workflowRun { workflow { name } } } }
          } } }
          latestReviews(first: 100) { nodes { author { login } authorAssociation submittedAt body state } }
          reviewRequests(first: 100) { nodes { requestedReviewer {
            __typename
            ... on User { login }
            ... on Bot { login }
            ... on Team { name slug }
          } } }
        }
      }
    }"""
    command = ["gh", "api", "graphql"]
    command.extend(value for item in batch for value in ("-F", f"ids[]={required_string(item, 'id')}"))
    command.extend(["-f", f"query={query}"])
    payload = json.loads(run_read_only(command))
    try:
        nodes = payload["data"]["nodes"]
    except (KeyError, TypeError) as error:
        raise PlannerError("GitHub returned an invalid PR hydration payload") from error
    if not isinstance(nodes, list) or any(not isinstance(node, Mapping) for node in nodes):
        raise PlannerError("GitHub returned an invalid PR hydration node list")
    result: list[dict[str, Any]] = []
    for node in nodes:
        item = dict(node)
        rollup = node.get("statusCheckRollup")
        contexts = rollup.get("contexts", {}).get("nodes", []) if isinstance(rollup, Mapping) else []
        checks = []
        for context in contexts:
            check = dict(context)
            if check.get("__typename") == "StatusContext":
                check["startedAt"] = check.pop("createdAt", None)
            elif check.get("__typename") == "CheckRun":
                suite = check.pop("checkSuite", None)
                workflow_run = suite.get("workflowRun") if isinstance(suite, Mapping) else None
                workflow = workflow_run.get("workflow") if isinstance(workflow_run, Mapping) else None
                check["workflowName"] = workflow.get("name") if isinstance(workflow, Mapping) else None
            checks.append(check)
        latest = node.get("latestReviews")
        requests = node.get("reviewRequests")
        item["statusCheckRollup"] = checks
        item["latestReviews"] = latest.get("nodes", []) if isinstance(latest, Mapping) else []
        item["reviewRequests"] = [
            request.get("requestedReviewer")
            for request in requests.get("nodes", [])
            if isinstance(request, Mapping) and isinstance(request.get("requestedReviewer"), Mapping)
        ] if isinstance(requests, Mapping) else []
        result.append(item)
    return result


def load_live(
    selectors: Sequence[str], *, repository: str | None = None, local_tz: tzinfo | None = None
) -> tuple[str, str, list[dict[str, Any]], str]:
    viewer = run_read_only(["gh", "api", "user", "--jq", ".login"]).strip()
    repo_command = ["gh", "repo", "view"]
    if repository:
        repo_command.append(repository)
    repo_command.extend(["--json", "nameWithOwner,viewerPermission"])
    repo_info = json.loads(run_read_only(repo_command))
    repository = required_string(repo_info, "nameWithOwner")
    viewer_permission = required_string(repo_info, "viewerPermission")
    owner, name = repository.split("/", 1)
    count_query = "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { pullRequests(states: OPEN) { totalCount } } }"
    count_payload = json.loads(
        run_read_only(
            ["gh", "api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}", "-f", f"query={count_query}"]
        )
    )
    try:
        open_count = int(count_payload["data"]["repository"]["pullRequests"]["totalCount"])
    except (KeyError, TypeError, ValueError) as error:
        raise PlannerError("GitHub returned an invalid open-PR count") from error
    fields = ",".join(
        [
            "id", "number", "url", "state", "author", "createdAt", "headRefName", "headRefOid",
            "headRepository", "baseRefName", "baseRefOid", "isDraft", "maintainerCanModify",
            "changedFiles",
        ]
    )
    raw = json.loads(run_read_only(["gh", "pr", "list", "--repo", repository, "--state", "open", "--limit", str(max(open_count, 1)), "--json", fields]))
    if not isinstance(raw, list):
        raise PlannerError("gh pr list returned a non-array response")
    if len(raw) != open_count:
        raise PlannerError(f"open PR snapshot changed or is incomplete: expected {open_count}, received {len(raw)}")
    for item in raw:
        item["baseRepository"] = {"nameWithOwner": repository}
    selected = resolve_selectors(selectors, raw, viewer, repository=repository, local_tz=local_tz)
    selected_numbers = {item.number for item in selected}
    selected_raw = [item for item in raw if item["number"] in selected_numbers]
    hydrate_selected_prs(selected_raw, _fetch_hydration_batch)
    for item in raw:
        if item["number"] in selected_numbers:
            pages = json.loads(
                run_read_only(
                    ["gh", "api", f"repos/{repository}/pulls/{item['number']}/files", "--paginate", "--slurp"]
                )
            )
            if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
                raise PlannerError(f"file list for PR #{item['number']} has an invalid shape")
            files = [file for page in pages for file in page]
            expected_files = item.get("changedFiles")
            if not isinstance(expected_files, int) or isinstance(expected_files, bool) or expected_files < 0:
                raise PlannerError(f"GitHub returned an invalid changedFiles count for PR #{item['number']}")
            if len(files) != expected_files:
                raise PlannerError(
                    f"incomplete file list for PR #{item['number']}: "
                    f"expected {expected_files}, received {len(files)}"
                )
            item["files"] = files
    return viewer, repository, raw, viewer_permission


def load_fixture(path: Path) -> tuple[str, str, list[dict[str, Any]]]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PlannerError(f"cannot read fixture {path}: {error}") from error
    if not isinstance(payload, Mapping):
        raise PlannerError("fixture must be a JSON object")
    viewer = required_string(payload, "viewer")
    repository = required_string(payload, "repository")
    prs = payload.get("pullRequests")
    if not isinstance(prs, list):
        raise PlannerError("fixture pullRequests must be an array")
    return viewer, repository, prs


def render_markdown(plan: WavePlan) -> str:
    lines = ["# PR merge-wave plan", "", f"Selected PRs: {', '.join(f'#{item.number}' for item in plan.selected)}"]
    for root, numbers in plan.root_groups.items():
        lines.append(f"- External root `{root}`: {', '.join(f'#{number}' for number in numbers)}")
    if plan.ownership_warnings:
        lines.append("- Foreign-authored selections require permission: " + ", ".join(f"#{item.number} ({item.author})" for item in plan.ownership_warnings))
    if plan.ordering_decisions:
        lines.append("- Sibling groups require approved ordering: " + "; ".join(" -> ".join(f"#{number}" for number in group) for group in plan.ordering_decisions))
    if plan.diff_risks:
        lines.append(f"- Diff risks: {len(plan.diff_risks)}")
    if not plan.stack_membership_complete:
        lines.append("- Existing GitHub stack membership: not discovered; inspect separately before mutation")
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selector", action="append", required=True, help="PR, branch, PR/date range, or bound; repeatable")
    parser.add_argument("--repo", help="expected OWNER/REPO (defaults to current gh repository)")
    parser.add_argument("--fixture", type=Path, help="offline GitHub snapshot JSON")
    parser.add_argument("--timezone", default=None, help="IANA timezone for date selectors (defaults to local timezone)")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        local_tz = ZoneInfo(args.timezone) if args.timezone else None
        if args.fixture:
            viewer, repository, raw_prs = load_fixture(args.fixture)
            viewer_permission = "UNKNOWN"
        else:
            viewer, repository, raw_prs, viewer_permission = load_live(
                args.selector, repository=args.repo, local_tz=local_tz
            )
        if args.repo and args.repo != repository:
            raise PlannerError(f"resolved repository {repository!r} does not match --repo {args.repo!r}")
        plan = plan_wave(args.selector, raw_prs, viewer, repository=repository, local_tz=local_tz)
        output = plan.to_dict()
        output["viewer_permission"] = viewer_permission
        if args.format == "json":
            print(json.dumps(output, indent=2, sort_keys=True))
        else:
            print(render_markdown(plan), end="")
        return 0
    except (PlannerError, subprocess.CalledProcessError, json.JSONDecodeError, ZoneInfoNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
