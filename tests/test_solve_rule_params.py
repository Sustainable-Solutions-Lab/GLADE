# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Static cross-check of run_solve's params against its three harnesses.

``run_solve`` executes through the ``solve_model`` rule, the
``solve_and_analyze_model`` rule (inline analysis), and the cluster manifest
(``tools/cluster-solve`` via ``build_scenario_entry``). A param added to one
harness but not the others fails only at runtime, in whichever path was
forgotten. This test parses all four sources statically and fails on any
param ``run_solve`` reads that a harness does not define.
"""

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def _run_solve_param_reads() -> set[str]:
    """Attribute names read as ``smk.params.<name>`` inside run_solve."""
    tree = ast.parse((ROOT / "workflow/scripts/solve_model/core.py").read_text())
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run_solve"
    )
    return {
        node.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "params"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "smk"
    }


def _rule_param_keys(rel_path: str, rule_name: str) -> set[str]:
    """Param names of a Snakemake rule, by indentation-aware text scan.

    snakefmt guarantees the layout this relies on: section headers one level
    below the rule keyword, param entries exactly one level below ``params:``
    (lambda continuation lines sit deeper and carry no ``name=`` prefix).
    """
    lines = (ROOT / rel_path).read_text().splitlines()
    for index, line in enumerate(lines):
        if line.strip() == f"rule {rule_name}:":
            rule_indent = len(line) - len(line.lstrip())
            start = index
            break
    else:
        raise AssertionError(f"rule {rule_name} not found in {rel_path}")

    keys: set[str] = set()
    params_indent: int | None = None
    for line in lines[start + 1 :]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= rule_indent:
            break
        if line.strip() == "params:":
            params_indent = indent
            continue
        if params_indent is None:
            continue
        if indent <= params_indent:
            params_indent = None  # left the params section
            continue
        if indent == params_indent + 4:
            match = re.match(r"(\w+)=", line.strip())
            if match:
                keys.add(match.group(1))
    if not keys:
        raise AssertionError(f"no params parsed for rule {rule_name} in {rel_path}")
    return keys


def _manifest_param_keys() -> set[str]:
    """Keys of the params dict built by solve_namespace.build_scenario_entry."""
    tree = ast.parse((ROOT / "workflow/scripts/solve_namespace.py").read_text())
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "build_scenario_entry"
    )
    keys: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        # params: dict = {...} appears as AnnAssign; plain params = {...} as Assign
        if (
            isinstance(target, ast.Name)
            and target.id == "params"
            and isinstance(node.value, ast.Dict)
        ):
            keys.update(
                key.value for key in node.value.keys if isinstance(key, ast.Constant)
            )
        # Conditional additions: params["key"] = ...
        if (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "params"
            and isinstance(target.slice, ast.Constant)
        ):
            keys.add(target.slice.value)
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "params"
            and isinstance(node.value, ast.Dict)
        ):
            keys.update(
                key.value for key in node.value.keys if isinstance(key, ast.Constant)
            )
    if not keys:
        raise AssertionError("no params keys parsed from build_scenario_entry")
    return keys


def test_run_solve_params_defined_in_all_harnesses():
    reads = _run_solve_param_reads()
    assert reads, "no smk.params reads found in run_solve"
    harnesses = {
        "solve_model rule (workflow/rules/model.smk)": _rule_param_keys(
            "workflow/rules/model.smk", "solve_model"
        ),
        "solve_and_analyze_model rule (workflow/rules/analysis.smk)": _rule_param_keys(
            "workflow/rules/analysis.smk", "solve_and_analyze_model"
        ),
        "cluster manifest (solve_namespace.build_scenario_entry)": (
            _manifest_param_keys()
        ),
    }
    for harness, defined in harnesses.items():
        missing = reads - defined
        assert (
            not missing
        ), f"params read by run_solve but not defined in {harness}: {sorted(missing)}"
