# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for MARS-based sensitivity analysis."""

import chaospy as cp
import numpy as np
import pytest
from scipy.stats.qmc import Sobol

from workflow.scripts.analysis.surrogate import (
    conditional_sobol_mc,
    fit_mars,
    sobol_from_predict,
)

conditional_sobol_rf_batch = conditional_sobol_mc


def sobol_from_rf(model, distribution, n_params, n_mc=2**14, seed=0):
    """Adapter so existing tests can pass model objects instead of callables."""
    return sobol_from_predict(
        model.predict, distribution, n_params, n_mc=n_mc, seed=seed
    )


class TestMARSFitting:
    """Tests for MARS fitting and validation."""

    def test_piecewise_linear_function(self):
        """MARS should fit a piecewise-linear function near-perfectly."""
        rng = np.random.default_rng(42)
        n = 1024
        X = rng.random((n, 3))
        y = 3 * np.maximum(X[:, 0] - 0.4, 0) - 2 * np.maximum(0.6 - X[:, 1], 0)

        result = fit_mars(X, y, max_terms=20, max_degree=1)
        assert result["r2"] > 0.99

    def test_linear_function(self):
        """MARS should fit a purely additive linear function."""
        rng = np.random.default_rng(42)
        n = 512
        X = rng.random((n, 2))
        y = 3 * X[:, 0] + 2 * X[:, 1]

        result = fit_mars(X, y, max_terms=20, max_degree=1)
        assert result["r2"] > 0.99

    def test_interaction_function(self):
        """MARS with degree=2 should capture interactions."""
        rng = np.random.default_rng(42)
        n = 1024
        X = rng.random((n, 3))
        y = X[:, 0] * X[:, 1] + X[:, 2]

        result = fit_mars(X, y, max_terms=30, max_degree=2)
        assert result["r2"] > 0.95


class TestMARSSobol:
    """Tests for MARS-based Sobol index estimation."""

    def test_linear_sobol(self):
        """Test Sobol indices for Y = 3*X1 + 2*X2."""
        dist = cp.J(cp.Uniform(0, 1), cp.Uniform(0, 1))
        n = 1024
        sampler = Sobol(2, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        y = 3 * x[:, 0] + 2 * x[:, 1]

        result = fit_mars(x, y, max_terms=20, max_degree=1)
        s1, s_total = sobol_from_rf(result["model"], dist, 2, n_mc=2**13, seed=0)

        # Analytic: S1_1 = 9/13, S1_2 = 4/13
        assert s1[0] == pytest.approx(9 / 13, abs=0.05)
        assert s1[1] == pytest.approx(4 / 13, abs=0.05)
        # No interactions for additive function
        np.testing.assert_allclose(s1, s_total, atol=0.05)

    def test_piecewise_linear_sobol(self):
        """Sobol indices for a piecewise-linear function."""
        dist = cp.J(cp.Uniform(0, 1), cp.Uniform(0, 1))
        n = 1024
        sampler = Sobol(2, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        # X1 matters more due to the hinge
        y = 3 * np.maximum(x[:, 0] - 0.3, 0) + x[:, 1]

        result = fit_mars(x, y, max_terms=20, max_degree=1)
        s1, _ = sobol_from_rf(result["model"], dist, 2, n_mc=2**13, seed=0)

        assert s1[0] > s1[1]  # X1 should dominate

    def test_interaction_sobol(self):
        """Test Sobol indices for Y = X1*X2 (pure interaction)."""
        dist = cp.J(cp.Uniform(0, 1), cp.Uniform(0, 1))
        n = 1024
        sampler = Sobol(2, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        y = x[:, 0] * x[:, 1]

        result = fit_mars(x, y, max_terms=30, max_degree=2)
        s1, s_total = sobol_from_rf(result["model"], dist, 2, n_mc=2**13, seed=0)

        # S1 should be small, ST should be large (interaction dominates)
        assert s_total[0] > s1[0] + 0.05
        assert s_total[1] > s1[1] + 0.05


class TestMARSConditionalSobol:
    """Tests for MARS conditional Sobol index estimation."""

    def test_conditioning_reduces_variance(self):
        """Conditioning on a parameter should reduce or maintain variance."""
        dist = cp.J(cp.Uniform(0, 1), cp.Uniform(0, 1), cp.Uniform(0, 1))
        n = 1024
        sampler = Sobol(3, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        y = 3 * x[:, 0] + 2 * x[:, 1] + x[:, 2]

        result = fit_mars(x, y, max_terms=20, max_degree=1)

        # Condition on parameter 0 (largest contributor)
        batch_results = conditional_sobol_rf_batch(
            result["model"].predict,
            dist,
            3,
            [0],
            [[0.5]],
            n_mc=2**13,
        )
        _, _, cond_var = batch_results[0]

        # Unconditional variance estimate
        rng = np.random.default_rng(0)
        x_test = rng.random((10000, 3))
        x_test = dist.inv(x_test.T).T
        total_var = np.var(result["model"].predict(x_test))

        assert cond_var < total_var * 1.1  # small tolerance for MC noise
