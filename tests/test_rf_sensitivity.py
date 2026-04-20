# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for Random Forest-based sensitivity analysis."""

import chaospy as cp
import numpy as np
import pytest
from scipy.stats.qmc import Sobol

from workflow.scripts.analysis.surrogate import (
    conditional_sobol_mc,
    fit_random_forest,
    sobol_from_predict,
)


def sobol_from_rf(model, distribution, n_params, n_mc=2**14, seed=0):
    """Adapter so existing tests can pass model objects instead of callables."""
    return sobol_from_predict(
        model.predict, distribution, n_params, n_mc=n_mc, seed=seed
    )


def conditional_sobol_rf(
    model, distribution, n_params, slice_indices, slice_values, n_mc=2**13, seed=0
):
    """Single-point wrapper around the batch conditional Sobol function."""
    results = conditional_sobol_mc(
        model.predict,
        distribution,
        n_params,
        slice_indices,
        [slice_values],
        n_mc=n_mc,
        seed=seed,
    )
    return results[0]


class TestRandomForestFitting:
    """Tests for Random Forest fitting and validation."""

    def test_linear_function(self):
        """Test RF on Y = 3*X1 + 2*X2 (purely additive)."""
        dist = cp.J(cp.Uniform(0, 1), cp.Uniform(0, 1))
        n = 512
        sampler = Sobol(2, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        y = 3 * x[:, 0] + 2 * x[:, 1]

        result = fit_random_forest(x, y, n_estimators=200)
        assert result["r2"] > 0.95
        assert result["validation_error"] < 0.05

    def test_quadratic_function(self):
        """Test RF on Y = X1^2 + X2 (nonlinear)."""
        dist = cp.J(cp.Uniform(-1, 1), cp.Uniform(-1, 1))
        n = 512
        sampler = Sobol(2, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        y = x[:, 0] ** 2 + x[:, 1]

        result = fit_random_forest(x, y, n_estimators=200)
        assert result["r2"] > 0.95


class TestRFSobol:
    """Tests for RF-based Sobol index estimation."""

    def test_linear_sobol(self):
        """Test Sobol indices for Y = 3*X1 + 2*X2."""
        dist = cp.J(cp.Uniform(0, 1), cp.Uniform(0, 1))
        n = 1024
        sampler = Sobol(2, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        y = 3 * x[:, 0] + 2 * x[:, 1]

        result = fit_random_forest(x, y, n_estimators=300)
        s1, s_total = sobol_from_rf(result["model"], dist, 2, n_mc=2**13, seed=0)

        # Analytic: S1_1 = 9/13, S1_2 = 4/13
        assert s1[0] == pytest.approx(9 / 13, abs=0.08)
        assert s1[1] == pytest.approx(4 / 13, abs=0.08)
        # No interactions for additive function
        np.testing.assert_allclose(s1, s_total, atol=0.08)

    def test_quadratic_sobol(self):
        """Test Sobol indices for Y = X1^2 + X2."""
        dist = cp.J(cp.Uniform(-1, 1), cp.Uniform(-1, 1))
        n = 1024
        sampler = Sobol(2, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        y = x[:, 0] ** 2 + x[:, 1]

        result = fit_random_forest(x, y, n_estimators=300)
        s1, _s_total = sobol_from_rf(result["model"], dist, 2, n_mc=2**13, seed=0)

        # Analytic: S1_1 = 4/19, S1_2 = 15/19
        assert s1[0] == pytest.approx(4 / 19, abs=0.08)
        assert s1[1] == pytest.approx(15 / 19, abs=0.08)

    def test_interaction_sobol(self):
        """Test Sobol indices for Y = X1*X2 (pure interaction)."""
        dist = cp.J(cp.Uniform(0, 1), cp.Uniform(0, 1))
        n = 1024
        sampler = Sobol(2, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        y = x[:, 0] * x[:, 1]

        result = fit_random_forest(x, y, n_estimators=300)
        s1, s_total = sobol_from_rf(result["model"], dist, 2, n_mc=2**13, seed=0)

        # S1 should be small, ST should be large (interaction dominates)
        assert s_total[0] > s1[0] + 0.1
        assert s_total[1] > s1[1] + 0.1


class TestRFConditionalSobol:
    """Tests for RF conditional Sobol index estimation."""

    def test_conditioning_reduces_variance(self):
        """Conditioning on a parameter should reduce or maintain variance."""
        dist = cp.J(cp.Uniform(0, 1), cp.Uniform(0, 1), cp.Uniform(0, 1))
        n = 1024
        sampler = Sobol(3, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        y = 3 * x[:, 0] + 2 * x[:, 1] + x[:, 2]

        result = fit_random_forest(x, y, n_estimators=300)

        # Condition on parameter 0 (largest contributor)
        _, _, cond_var = conditional_sobol_rf(
            result["model"],
            dist,
            3,
            [0],
            [0.5],
            n_mc=2**13,
        )

        # Unconditional variance estimate
        from sklearn.utils import check_random_state

        rng = check_random_state(0)
        x_test = rng.random((10000, 3))
        x_test = dist.inv(x_test.T).T
        total_var = np.var(result["model"].predict(x_test))

        assert cond_var < total_var * 1.1  # small tolerance for MC noise

    def test_conditional_indices_change_with_slice_value(self):
        """Conditional indices should vary when slice interactions exist."""
        dist = cp.J(cp.Uniform(0, 1), cp.Uniform(0, 1), cp.Uniform(0, 1))
        n = 1024
        sampler = Sobol(3, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        # X2 is slice parameter; interaction makes X0 sensitivity depend on X2.
        y = x[:, 0] + 0.3 * x[:, 1] + 2.0 * x[:, 0] * x[:, 2]

        result = fit_random_forest(x, y, n_estimators=300)

        s1_low, _, var_low = conditional_sobol_rf(
            result["model"], dist, 3, [2], [0.2], n_mc=2**13
        )
        s1_high, _, var_high = conditional_sobol_rf(
            result["model"], dist, 3, [2], [0.8], n_mc=2**13
        )

        # At higher slice values, X0's contribution grows
        assert s1_high[0] > s1_low[0] + 0.01
        assert var_high > var_low

    def test_joint_conditioning_two_slices(self):
        """Joint conditioning should follow analytic shares (wider tolerance for RF)."""
        dist = cp.J(
            cp.Uniform(0, 1),
            cp.Uniform(0, 1),
            cp.Uniform(0, 1),
            cp.Uniform(0, 1),
        )
        n = 1024
        sampler = Sobol(4, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        # X2, X3 are slice parameters.
        y = (1 + 2 * x[:, 2]) * x[:, 0] + (0.5 + x[:, 3]) * x[:, 1]

        result = fit_random_forest(x, y, n_estimators=300)

        s2, s3 = 0.2, 0.8
        s1_cond, _, _ = conditional_sobol_rf(
            result["model"], dist, 4, [2, 3], [s2, s3], n_mc=2**13
        )

        a = 1 + 2 * s2
        b = 0.5 + s3
        expected_x0 = a**2 / (a**2 + b**2)
        expected_x1 = b**2 / (a**2 + b**2)

        assert s1_cond[0] == pytest.approx(expected_x0, abs=0.10)
        assert s1_cond[1] == pytest.approx(expected_x1, abs=0.10)
