"""Temporal dependency graph over preparation tasks.

Two edge families are modelled, because exchange preparation has both:

* **prerequisite** - task order ("a reference letter may only be requested once a
  programme is shortlisted").  Modelled as ``predecessor.task -> successor.task``.
* **valid_until** - credit/eligibility retention ("the language score must still
  be valid when the nomination is accepted").  Modelled as ``holder.task ->
  requirement.task`` and read by the scheduler as a freshness constraint.

The graph is the *only* place that decides what follows what, so this module
owns cycle detection, topological order, critical path and the DAG report that
the final report can quote as a correctness check.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from planner.schema import DependencyEdge, PlanningTask


@dataclass
class DependencyGraph:
    """A validated directed acyclic graph of tasks plus its diagnostics."""

    tasks: dict[str, PlanningTask]
    edges: list[DependencyEdge] = field(default_factory=list)
    topological_order: list[str] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    dangling_predecessors: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_acyclic(self) -> bool:
        return not self.cycles

    def successors(self, task_id: str) -> list[str]:
        return [edge.successor for edge in self.edges if edge.predecessor == task_id]

    def predecessors(self, task_id: str) -> list[str]:
        return [edge.predecessor for edge in self.edges if edge.successor == task_id]

    def prerequisite_successors(self, task_id: str) -> list[str]:
        return [
            edge.successor
            for edge in self.edges
            if edge.predecessor == task_id and edge.kind == "prerequisite"
        ]

    def prerequisite_predecessors(self, task_id: str) -> list[str]:
        return [
            edge.predecessor
            for edge in self.edges
            if edge.successor == task_id and edge.kind == "prerequisite"
        ]

    def validity_edges(self) -> list[DependencyEdge]:
        return [edge for edge in self.edges if edge.kind == "valid_until"]

    def critical_path(
        self, float_days: dict[str, int | None], durations: dict[str, int]
    ) -> list[str]:
        """Return the driving chain of tasks that have no scheduling slack.

        A task with ``float_days == 0`` cannot slip without moving the plan's
        overall finish - the textbook critical-path test.  Among all zero-float
        chains we return the *longest* one, because that chain is what actually
        sets the length of the preparation window.
        """

        zero_float = {task for task, slack in float_days.items() if slack == 0}
        if not zero_float:
            return []
        memo: dict[str, list[str]] = {}

        def longest_from(node: str) -> list[str]:
            if node in memo:
                return memo[node]
            best_tail: list[str] = []
            for successor in self.prerequisite_successors(node):
                if successor not in zero_float:
                    continue
                tail = longest_from(successor)
                if self._chain_cost(tail, durations) > self._chain_cost(best_tail, durations):
                    best_tail = tail
            memo[node] = [node, *best_tail]
            return memo[node]

        starts = [
            task
            for task in zero_float
            if not any(predecessor in zero_float for predecessor in self.prerequisite_predecessors(task))
        ]
        best: list[str] = []
        for start in sorted(starts):
            chain = longest_from(start)
            if self._chain_cost(chain, durations) > self._chain_cost(best, durations):
                best = chain
        return best

    @staticmethod
    def _chain_cost(path: list[str], durations: dict[str, int]) -> int:
        return sum(durations.get(task, 0) for task in path)

    def longest_chain(self, durations: dict[str, int]) -> list[str]:
        """Longest chain of tasks by conservative duration.

        Used as the reported critical path when no task has exactly zero slack:
        the chain is then the structural driver of the schedule even though the
        calendar still leaves room around it.  A cyclic graph has no chain, so the
        result is empty and the cycle is reported as a blocking conflict instead.
        """

        if not self.tasks or not self.is_acyclic:
            return []
        memo: dict[str, list[str]] = {}
        visiting: set[str] = set()

        def longest_from(node: str) -> list[str]:
            if node in memo:
                return memo[node]
            if node in visiting:  # defensive: never recurse into a cycle
                return []
            visiting.add(node)
            best_tail: list[str] = []
            for successor in self.prerequisite_successors(node):
                tail = longest_from(successor)
                if self._chain_cost(tail, durations) > self._chain_cost(best_tail, durations):
                    best_tail = tail
            visiting.discard(node)
            memo[node] = [node, *best_tail]
            return memo[node]

        best: list[str] = []
        for task_id in sorted(self.tasks):
            chain = longest_from(task_id)
            if self._chain_cost(chain, durations) > self._chain_cost(best, durations):
                best = chain
        return best


def build_graph(tasks: list[PlanningTask]) -> DependencyGraph:
    """Build and validate the dependency graph.

    Edges are de-duplicated, self-loops are dropped (they are meaningless rather
    than fatal), and missing predecessor ids are recorded as dangling so the
    caller can raise a reviewable conflict instead of crashing.
    """

    by_id = {task.id: task for task in tasks}
    edges: list[DependencyEdge] = []
    seen: set[tuple[str, str, str]] = set()
    dangling: dict[str, list[str]] = {}

    def add(predecessor: str, successor: str, kind: str, reason: str) -> None:
        key = (predecessor, successor, kind)
        if predecessor == successor or key in seen:
            return
        seen.add(key)
        edges.append(
            DependencyEdge(predecessor=predecessor, successor=successor, kind=kind, reason=reason)
        )

    for task in tasks:
        for predecessor in task.prerequisites:
            if predecessor not in by_id:
                dangling.setdefault(task.id, []).append(predecessor)
                continue
            add(predecessor, task.id, "prerequisite", f"{predecessor} must finish before {task.id}")
        for holder in task.valid_until:
            if holder not in by_id:
                dangling.setdefault(task.id, []).append(holder)
                continue
            add(holder, task.id, "valid_until", f"{holder} must still be valid for {task.id}")

    order, cycles = topological_order(by_id, edges)
    return DependencyGraph(
        tasks=by_id,
        edges=edges,
        topological_order=order,
        cycles=cycles,
        dangling_predecessors=dangling,
    )


def topological_order(
    tasks: dict[str, PlanningTask], edges: list[DependencyEdge]
) -> tuple[list[str], list[list[str]]]:
    """Kahn's algorithm, returning a deterministic order and any cycles found.

    The ready set is drained in sorted order so repeated runs on the same input
    produce byte-identical plans (important for the evaluation protocol).
    """

    indegree = {task_id: 0 for task_id in tasks}
    outgoing: dict[str, list[str]] = {task_id: [] for task_id in tasks}
    for edge in edges:
        if edge.predecessor not in tasks or edge.successor not in tasks:
            continue
        indegree[edge.successor] += 1
        outgoing[edge.predecessor].append(edge.successor)

    ready = deque(sorted(task_id for task_id, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while ready:
        current = ready.popleft()
        order.append(current)
        for successor in sorted(outgoing[current]):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)

    cycles: list[list[str]] = []
    if len(order) != len(tasks):
        remaining = {task_id for task_id in tasks if task_id not in set(order)}
        cycles = find_cycles(remaining, edges)
    return order, cycles


def find_cycles(candidates: set[str], edges: list[DependencyEdge]) -> list[list[str]]:
    """Enumerate simple cycles inside the unresolved part of the graph (DFS)."""

    adjacency: dict[str, list[str]] = {task_id: [] for task_id in candidates}
    for edge in edges:
        if edge.predecessor in candidates and edge.successor in candidates:
            adjacency[edge.predecessor].append(edge.successor)
    for neighbours in adjacency.values():
        neighbours.sort()

    cycles: list[list[str]] = []
    seen_signatures: set[tuple[str, ...]] = set()
    path: list[str] = []
    on_path: set[str] = set()

    def walk(node: str) -> None:
        path.append(node)
        on_path.add(node)
        for neighbour in adjacency[node]:
            if neighbour in on_path:
                cycle = path[path.index(neighbour) :]
                signature = _canonical_cycle(cycle)
                if signature not in seen_signatures:
                    seen_signatures.add(signature)
                    cycles.append(cycle)
            elif neighbour not in visited:
                walk(neighbour)
        path.pop()
        on_path.discard(node)
        visited.add(node)

    visited: set[str] = set()
    for start in sorted(candidates):
        if start not in visited:
            walk(start)
    return cycles


def _canonical_cycle(cycle: list[str]) -> tuple[str, ...]:
    if not cycle:
        return ()
    rotations = [tuple(cycle[i:] + cycle[:i]) for i in range(len(cycle))]
    return min(rotations)
