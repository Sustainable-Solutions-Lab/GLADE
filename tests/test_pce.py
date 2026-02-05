# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for PCE-based sensitivity analysis."""

import chaospy as cp
import numpy as np
import pytest
from scipy.stats.qmc import Sobol

from workflow.scenario_generators import (
    _generate_sensitivity_samples,
    _validate_distribution_spec,
    build_chaospy_distribution,
)
from workflow.scripts.analysis.compute_pce_sensitivity import (
    conditional_sobol,
    fit_pce,
    sobol_from_pce,
)


class TestDistributionParsing:
    """Tests for parameter distribution specification parsing."""

    def test_uniform_default(self):
        spec = {"lower": 0.8, "upper": 1.2}
        dist = build_chaospy_distribution(spec)
        assert isinstance(dist, cp.Distribution)
        assert float(dist.lower[0]) == pytest.approx(0.8)
        assert float(dist.upper[0]) == pytest.approx(1.2)

    def test_uniform_explicit(self):
        spec = {"distribution": "uniform", "lower": 0, "upper": 300}
        dist = build_chaospy_distribution(spec)
        assert float(dist.lower[0]) == pytest.approx(0)
        assert float(dist.upper[0]) == pytest.approx(300)

    def test_normal(self):
        spec = {"distribution": "normal", "mean": 1.0, "std": 0.1}
        dist = build_chaospy_distribution(spec)
        assert isinstance(dist, cp.Distribution)

    def test_lognormal(self):
        spec = {"distribution": "lognormal", "mu": 0.0, "sigma": 0.15}
        dist = build_chaospy_distribution(spec)
        assert isinstance(dist, cp.Distribution)

    def test_invalid_distribution(self):
        with pytest.raises(ValueError, match="Unsupported distribution"):
            build_chaospy_distribution({"distribution": "beta", "a": 2, "b": 5})

    def test_validate_uniform_missing_fields(self):
        with pytest.raises(ValueError, match="requires 'lower' and 'upper'"):
            _validate_distribution_spec("x", {"lower": 0})

    def test_validate_normal_missing_fields(self):
        with pytest.raises(ValueError, match="requires 'mean' and 'std'"):
            _validate_distribution_spec("x", {"distribution": "normal", "mean": 0})

    def test_validate_lognormal_missing_fields(self):
        with pytest.raises(ValueError, match="requires 'mu' and 'sigma'"):
            _validate_distribution_spec("x", {"distribution": "lognormal", "mu": 0})


class TestSensitivitySampling:
    """Tests for the sensitivity sampling function."""

    def test_sample_count(self):
        spec = {
            "name": "test_{sample_id}",
            "mode": "sensitivity",
            "samples": 64,
            "parameters": {
                "x": {"lower": 0, "upper": 1},
                "y": {"lower": -1, "upper": 1},
            },
            "template": {},
        }
        samples = _generate_sensitivity_samples(spec)
        assert len(samples) == 64

    def test_sample_keys(self):
        spec = {
            "name": "test_{sample_id}",
            "mode": "sensitivity",
            "samples": 16,
            "parameters": {
                "a": {"lower": 0, "upper": 10},
                "b": {"distribution": "normal", "mean": 5, "std": 1},
            },
            "template": {},
        }
        samples = _generate_sensitivity_samples(spec)
        assert all(set(s.keys()) == {"a", "b"} for s in samples)

    def test_sample_bounds_uniform(self):
        spec = {
            "name": "test_{sample_id}",
            "mode": "sensitivity",
            "samples": 256,
            "parameters": {
                "x": {"lower": 0.8, "upper": 1.2},
            },
            "template": {},
        }
        samples = _generate_sensitivity_samples(spec)
        values = [s["x"] for s in samples]
        assert all(0.8 <= v <= 1.2 for v in values)

    def test_deterministic_with_seed(self):
        spec = {
            "name": "test_{sample_id}",
            "mode": "sensitivity",
            "samples": 32,
            "seed": 123,
            "parameters": {
                "x": {"lower": 0, "upper": 1},
            },
            "template": {},
        }
        s1 = _generate_sensitivity_samples(spec)
        s2 = _generate_sensitivity_samples(spec)
        assert s1 == s2


class TestPCEFitting:
    """Tests for PCE fitting and Sobol index computation."""

    def test_linear_function(self):
        """Test PCE on Y = 3*X1 + 2*X2 (purely additive)."""
        dist = cp.J(cp.Uniform(0, 1), cp.Uniform(0, 1))
        n = 128
        sampler = Sobol(2, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        y = 3 * x[:, 0] + 2 * x[:, 1]

        result = fit_pce(x, y, dist, max_degree=3)
        assert result["r2"] > 0.99
        assert result["loo_error"] < 0.01

        s1, s_total = sobol_from_pce(result["coefficients"], result["multi_indices"], 2)
        # For Y = 3X1 + 2X2 with X_i ~ U(0,1):
        # Var(3X1) = 9/12 = 0.75, Var(2X2) = 4/12 = 0.333
        # Total = 0.75 + 0.333 = 1.083
        # S1_1 = 0.75/1.083 = 0.692, S1_2 = 0.333/1.083 = 0.308
        assert s1[0] == pytest.approx(9 / 13, abs=0.05)
        assert s1[1] == pytest.approx(4 / 13, abs=0.05)
        # No interactions for additive function
        np.testing.assert_allclose(s1, s_total, atol=0.05)

    def test_quadratic_function(self):
        """Test PCE on Y = X1^2 + X2 (interaction-free)."""
        dist = cp.J(cp.Uniform(-1, 1), cp.Uniform(-1, 1))
        n = 256
        sampler = Sobol(2, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        y = x[:, 0] ** 2 + x[:, 1]

        result = fit_pce(x, y, dist, max_degree=3)
        assert result["r2"] > 0.99

        s1, s_total = sobol_from_pce(result["coefficients"], result["multi_indices"], 2)
        # For Y = X1^2 + X2 with X_i ~ U(-1,1):
        # Var(X1^2) = E[X1^4] - E[X1^2]^2 = 1/5 - 1/9 = 4/45
        # Var(X2) = 1/3
        # Total = 4/45 + 1/3 = 19/45
        # S1_1 = (4/45)/(19/45) = 4/19 = 0.211
        # S1_2 = (1/3)/(19/45) = 15/19 = 0.789
        assert s1[0] == pytest.approx(4 / 19, abs=0.05)
        assert s1[1] == pytest.approx(15 / 19, abs=0.05)

    def test_interaction_function(self):
        """Test PCE on Y = X1*X2 (pure interaction)."""
        dist = cp.J(cp.Uniform(0, 1), cp.Uniform(0, 1))
        n = 256
        sampler = Sobol(2, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        y = x[:, 0] * x[:, 1]

        result = fit_pce(x, y, dist, max_degree=3)

        s1, s_total = sobol_from_pce(result["coefficients"], result["multi_indices"], 2)
        # For Y = X1*X2 with X_i ~ U(0,1):
        # S1_i should be small, ST_i should be large
        assert s_total[0] > s1[0] + 0.1
        assert s_total[1] > s1[1] + 0.1


class TestConditionalSobol:
    """Tests for conditional Sobol index computation."""

    def test_conditioning_reduces_variance(self):
        """Conditioning on a parameter should reduce or maintain variance."""
        dist = cp.J(cp.Uniform(0, 1), cp.Uniform(0, 1), cp.Uniform(0, 1))
        n = 256
        sampler = Sobol(3, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        y = 3 * x[:, 0] + 2 * x[:, 1] + x[:, 2]

        result = fit_pce(x, y, dist, max_degree=3)

        # Get global variance
        coefficients = result["coefficients"]
        multi_indices = result["multi_indices"]
        total_var = sum(
            c**2
            for alpha, c in zip(multi_indices, coefficients)
            if any(a > 0 for a in alpha)
        )

        # Condition on parameter 0 at its midpoint
        s1_c, st_c, cond_var = conditional_sobol(
            coefficients,
            result["expansion"],
            multi_indices,
            dist,
            3,
            [0],
            [0.5],
        )
        # Conditional variance should be less than total
        assert cond_var < total_var * 1.1  # small tolerance

    def test_conditioning_on_irrelevant_param(self):
        """Conditioning on a param not in the model should preserve indices."""
        dist = cp.J(cp.Uniform(0, 1), cp.Uniform(0, 1), cp.Uniform(0, 1))
        n = 256
        sampler = Sobol(3, scramble=True, seed=42)
        x = sampler.random(n)
        x = dist.inv(x.T).T

        # Y depends only on X0 and X1, not X2
        y = 3 * x[:, 0] + 2 * x[:, 1]

        result = fit_pce(x, y, dist, max_degree=3)

        # Condition on X2 (the irrelevant param)
        s1_c, st_c, cond_var = conditional_sobol(
            result["coefficients"],
            result["expansion"],
            result["multi_indices"],
            dist,
            3,
            [2],
            [0.5],
        )

        # S1_cond for X0 and X1 should be similar to global S1
        s1_global, _ = sobol_from_pce(
            result["coefficients"], result["multi_indices"], 3
        )
        assert s1_c[0] == pytest.approx(s1_global[0], abs=0.1)
        assert s1_c[1] == pytest.approx(s1_global[1], abs=0.1)
