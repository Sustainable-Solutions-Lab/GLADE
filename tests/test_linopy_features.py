# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Guard the pinned linopy version against the features GLADE relies on.

GLADE pins a custom ``+glade`` build of linopy (see ``pixi.toml``) that carries
two fork-specific extensions on top of upstream:

* ``Model.solve(..., calculate_fixed_duals=True)`` -- recover MIP duals by
  re-solving with integer variables fixed (used at solve time, see
  ``workflow/scripts/solve_model/core.py``).
* ``Model._mip_start`` -- inject a MIP warm-start (set in
  ``workflow/scripts/solve_model/health.py``).

It also depends on several upstream APIs (piecewise / SOS / merge) used by the
health and food-utility formulations. This module fails loudly if a future
linopy bump drops any of them, rather than surfacing as an obscure solve-time
error deep in the workflow.
"""

import linopy
from linopy import Model
from packaging.version import parse as parse_version
import pytest

pytestmark = pytest.mark.skipif(
    "highs" not in set(linopy.available_solvers),
    reason="HiGHS solver not available",
)


def _simple_milp() -> Model:
    m = Model()
    x = m.add_variables(lower=0, upper=10, integer=True, name="x")
    y = m.add_variables(lower=0, upper=10, name="y")
    m.add_constraints(x + y >= 4.5, name="con")
    m.add_objective(2 * x + y)
    return m


def test_linopy_version_at_least_0_8() -> None:
    assert parse_version(linopy.__version__).release[:2] >= (0, 8)


def test_glade_apis_present() -> None:
    """Upstream APIs the GLADE formulations import/use must exist."""
    from linopy.common import format_single_constraint  # noqa: F401
    from linopy.constants import BREAKPOINT_DIM  # noqa: F401

    assert hasattr(linopy, "merge")
    m = Model()
    for attr in ("add_sos_constraints", "add_piecewise_formulation"):
        assert hasattr(m, attr), attr
    # The MIP-start hook must exist and default to unset.
    assert m._mip_start is None


def test_calculate_fixed_duals_yields_finite_mip_dual() -> None:
    m = _simple_milp()
    status, condition = m.solve(solver_name="highs", calculate_fixed_duals=True)
    assert (status, condition) == ("ok", "optimal")
    dual = float(m.constraints["con"].dual)
    assert dual == dual  # not NaN
    assert abs(dual) > 0  # binding constraint has a non-zero shadow price

    # The fixed-dual recomputation must not perturb the optimum.
    baseline = _simple_milp()
    baseline.solve(solver_name="highs")
    assert float(m.objective.value) == pytest.approx(float(baseline.objective.value))


def test_mip_start_is_honoured() -> None:
    baseline = _simple_milp()
    baseline.solve(solver_name="highs")
    opt = float(baseline.objective.value)

    m = _simple_milp()
    # x occupies column 0; start it at a feasible integer value.
    m._mip_start = (1, [0], [4.0])
    status, condition = m.solve(solver_name="highs")
    assert (status, condition) == ("ok", "optimal")
    assert float(m.objective.value) == pytest.approx(opt)


def test_pypsa_create_model_freezes_and_solves() -> None:
    """Guard the CSR-frozen-constraints path used in solve_model/core.py.

    ``solve_model`` calls ``n.optimize.create_model(freeze_constraints=True)``;
    this checks that PyPSA still forwards the kwarg to ``linopy.Model`` (so the
    constraints are stored as ``CSRConstraint``) and that the frozen model
    solves with identical results to an unfrozen one.
    """
    pypsa = pytest.importorskip("pypsa")
    from linopy.constraints import CSRConstraint

    def solve(freeze: bool) -> tuple[float, float, set[str]]:
        n = pypsa.Network()
        n.add("Bus", "b")
        n.add("Generator", "cheap", bus="b", p_nom=50, marginal_cost=10)
        n.add("Generator", "exp", bus="b", p_nom=100, marginal_cost=50)
        n.add("Load", "l", bus="b", p_set=120)
        n.optimize.create_model(
            include_objective_constant=False, freeze_constraints=freeze
        )
        types = {type(c).__name__ for c in n.model.constraints.data.values()}
        status, condition = n.model.solve(solver_name="highs", io_api="direct")
        assert (status, condition) == ("ok", "optimal")
        n.optimize.assign_solution()
        n.optimize.assign_duals()
        return float(n.model.objective.value), types

    obj_unfrozen, types_unfrozen = solve(False)
    obj_frozen, types_frozen = solve(True)

    assert types_frozen == {CSRConstraint.__name__}
    assert types_unfrozen == {"Constraint"}
    assert obj_frozen == pytest.approx(obj_unfrozen)
