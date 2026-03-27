# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Multivariate Adaptive Regression Splines (MARS / Earth).

A from-scratch implementation of Friedman's MARS algorithm (1991) using
only numpy.  Designed for surrogate modelling of LP response surfaces
that are mostly smooth but contain kinks from optimal-basis changes.

The implementation follows the standard two-phase approach:

1. **Forward pass**: greedily add reflected pairs of hinge basis
   functions (and their interactions up to ``max_degree``) to minimise
   residual sum of squares.

2. **Backward pass**: prune individual basis functions using the
   generalised cross-validation (GCV) criterion to prevent overfitting.

The resulting model is piecewise-linear (or piecewise-multilinear for
interactions), which makes it a natural surrogate for optimisation
models whose response is piecewise-linear in the parameters.

Reference
---------
Friedman, J.H. (1991). Multivariate Adaptive Regression Splines.
*The Annals of Statistics*, 19(1), 1-67.
"""

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class HingeFactor:
    """One factor in a MARS basis function: max(±(x_j - t), 0)."""

    variable: int
    knot: float
    sign: int  # +1 for max(x - t, 0), -1 for max(t - x, 0)

    def evaluate(self, X: np.ndarray) -> np.ndarray:
        if self.sign == 1:
            return np.maximum(X[:, self.variable] - self.knot, 0.0)
        return np.maximum(self.knot - X[:, self.variable], 0.0)


@dataclass
class BasisFunction:
    """Product of hinge factors, or the intercept (empty factors list)."""

    factors: list[HingeFactor] = field(default_factory=list)

    @property
    def variables(self) -> set[int]:
        return {f.variable for f in self.factors}

    @property
    def degree(self) -> int:
        return len(self.factors)

    def evaluate(self, X: np.ndarray) -> np.ndarray:
        if not self.factors:
            return np.ones(X.shape[0])
        result = self.factors[0].evaluate(X)
        for f in self.factors[1:]:
            result = result * f.evaluate(X)
        return result


@dataclass
class Earth:
    """Multivariate Adaptive Regression Splines (MARS/Earth) regressor.

    Parameters
    ----------
    max_terms : int
        Maximum number of basis functions (including intercept) in the
        forward pass.  The backward pass may prune this down.
    max_degree : int
        Maximum interaction degree.  1 = additive (no interactions),
        2 = pairwise interactions, etc.
    penalty : float
        GCV penalty parameter *d* in Friedman (1991).  Higher values
        favour simpler models.  Typical range: 2-4.
    n_knots : int
        Number of candidate knots per variable, placed at equally-spaced
        quantiles of the training data.
    min_samples_leaf : int
        Minimum number of non-zero observations required for a candidate
        hinge function to be considered.
    """

    max_terms: int = 50
    max_degree: int = 2
    penalty: float = 3.0
    n_knots: int = 25
    min_samples_leaf: int = 5

    # Fitted attributes (set by fit())
    basis_: list[BasisFunction] = field(default=None, repr=False)
    coef_: np.ndarray = field(default=None, repr=False)
    gcv_: float = field(default=None, repr=False)

    def fit(self, X: np.ndarray, y: np.ndarray) -> "Earth":
        _n, d = X.shape

        # Candidate knots: quantiles of each variable
        knots = _candidate_knots(X, self.n_knots)

        # Forward pass: greedy basis function addition
        basis, B = self._forward_pass(X, y, d, knots)

        # Backward pass: GCV pruning
        basis, B, coef = self._backward_pass(X, y, basis, B)

        self.basis_ = basis
        self.coef_ = coef
        self.gcv_ = _gcv(y, B @ coef, B.shape[1], self.penalty)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        B = np.column_stack([bf.evaluate(X) for bf in self.basis_])
        return B @ self.coef_

    def score(self, X: np.ndarray, y: np.ndarray) -> float:
        """R² score."""
        y_pred = self.predict(X)
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # ------------------------------------------------------------------
    # Forward pass
    # ------------------------------------------------------------------

    def _forward_pass(self, X, y, d, knots):
        """Greedily add reflected pairs of hinge functions."""
        n = X.shape[0]
        basis = [BasisFunction()]  # intercept
        B = np.ones((n, 1))

        while len(basis) + 1 < self.max_terms:
            best = _find_best_candidate(
                X,
                y,
                B,
                basis,
                knots,
                d,
                self.max_degree,
                self.min_samples_leaf,
            )
            if best is None:
                break

            bf_plus, bf_minus, col_plus, col_minus = best
            basis.append(bf_plus)
            basis.append(bf_minus)
            B = np.column_stack([B, col_plus, col_minus])

        return basis, B

    # ------------------------------------------------------------------
    # Backward pass
    # ------------------------------------------------------------------

    def _backward_pass(self, X, y, basis, B):
        """Prune basis functions using GCV.

        Uses the analytic RSS-increase formula: dropping column *j*
        increases RSS by β_j² / (B⁺B)⁻¹_{jj}, avoiding a full OLS
        refit for each candidate removal.
        """
        n = B.shape[0]

        # Fit current full model
        coef = np.linalg.lstsq(B, y, rcond=None)[0]
        rss = float(np.sum((y - B @ coef) ** 2))
        best_gcv = _gcv_from_rss(rss, n, B.shape[1], self.penalty)
        best_basis = list(basis)
        best_B = B.copy()
        best_coef = coef.copy()

        current_basis = list(basis)
        current_B = B.copy()
        current_rss = rss

        while len(current_basis) > 1:
            m = current_B.shape[1]

            # Analytic RSS increase from dropping each column
            coef = np.linalg.lstsq(current_B, y, rcond=None)[0]
            current_rss = float(np.sum((y - current_B @ coef) ** 2))
            try:
                inv_gram = np.linalg.inv(current_B.T @ current_B)
            except np.linalg.LinAlgError:
                inv_gram = np.linalg.pinv(current_B.T @ current_B)
            diag_inv = np.diag(inv_gram)

            # RSS after removing column j (j=0 is intercept, skip it)
            best_removal_gcv = np.inf
            best_removal_idx = -1

            for j in range(1, m):
                if diag_inv[j] <= 0:
                    continue
                delta_rss = coef[j] ** 2 / diag_inv[j]
                rss_j = current_rss + delta_rss
                g = _gcv_from_rss(rss_j, n, m - 1, self.penalty)
                if g < best_removal_gcv:
                    best_removal_gcv = g
                    best_removal_idx = j

            if best_removal_idx < 0:
                break

            # Remove the least important basis function
            current_basis.pop(best_removal_idx)
            current_B = np.delete(current_B, best_removal_idx, axis=1)

            # Refit for accurate tracking
            coef = np.linalg.lstsq(current_B, y, rcond=None)[0]
            current_rss = float(np.sum((y - current_B @ coef) ** 2))
            g = _gcv_from_rss(current_rss, n, current_B.shape[1], self.penalty)

            if g <= best_gcv:
                best_gcv = g
                best_basis = list(current_basis)
                best_B = current_B.copy()
                best_coef = coef.copy()

        return best_basis, best_B, best_coef


# ======================================================================
# Module-level helpers
# ======================================================================


def _candidate_knots(X: np.ndarray, n_knots: int) -> list[np.ndarray]:
    """Return candidate knot locations for each variable."""
    quantiles = np.linspace(0, 1, n_knots + 2)[1:-1]
    knots = []
    for j in range(X.shape[1]):
        knots.append(np.unique(np.quantile(X[:, j], quantiles)))
    return knots


def _gcv_from_rss(rss: float, n: int, n_basis: int, d: float) -> float:
    """Generalised Cross-Validation criterion from pre-computed RSS.

    GCV = (1/N) * RSS / (1 - C(M)/N)²
    where C(M) = M + d*M is the effective number of parameters.
    """
    c_m = n_basis + d * n_basis
    denom = (1.0 - c_m / n) ** 2
    if denom <= 0:
        return np.inf
    return rss / n / denom


def _gcv(y: np.ndarray, y_pred: np.ndarray, n_basis: int, d: float) -> float:
    """Generalised Cross-Validation criterion."""
    rss = float(np.sum((y - y_pred) ** 2))
    return _gcv_from_rss(rss, len(y), n_basis, d)


def _find_best_candidate(X, y, B, basis, knots, d, max_degree, min_samples):
    """Find the best (parent, variable, knot) triple for the forward pass.

    Vectorises the knot search within each (parent, variable) pair so
    that the expensive QR projection is a single BLAS matrix multiply
    rather than a Python loop over individual knots.

    Returns (bf_plus, bf_minus, col_plus, col_minus) or None.
    """
    # Current QR and residual
    Q, _ = np.linalg.qr(B)
    residual = y - Q @ (Q.T @ y)
    current_rss = residual @ residual

    best_improvement = 0.0
    best_result = None

    for parent_idx, parent in enumerate(basis):
        if parent.degree >= max_degree:
            continue

        parent_vars = parent.variables
        parent_col = B[:, parent_idx]

        for j in range(d):
            if j in parent_vars:
                continue

            kv = knots[j]
            nk = len(kv)

            # Build all candidate columns for this (parent, variable) pair.
            # H_plus[:, k] = parent_col * max(X_j - t_k, 0)
            # H_minus[:, k] = parent_col * max(t_k - X_j, 0)
            xj = X[:, j]
            H_plus = parent_col[:, None] * np.maximum(
                xj[:, None] - kv[None, :], 0.0
            )  # (n, nk)
            H_minus = parent_col[:, None] * np.maximum(
                kv[None, :] - xj[:, None], 0.0
            )  # (n, nk)

            # Minimum-support mask
            support_plus = np.count_nonzero(H_plus, axis=0) >= min_samples
            support_minus = np.count_nonzero(H_minus, axis=0) >= min_samples
            valid = support_plus & support_minus
            if not valid.any():
                continue

            # Stack plus and minus: shape (n, 2*nk)
            H = np.column_stack([H_plus, H_minus])

            # Orthogonalise all candidates against current basis in one call
            H_orth = H - Q @ (Q.T @ H)  # (n, 2*nk)

            hp = H_orth[:, :nk]  # orthogonalised plus columns
            hm = H_orth[:, nk:]  # orthogonalised minus columns

            # Orthogonalise minus against plus (per knot)
            norm_p_sq = np.einsum("ij,ij->j", hp, hp)  # (nk,)
            dot_pm = np.einsum("ij,ij->j", hp, hm)  # (nk,)
            safe_p = norm_p_sq > 1e-12
            scale = np.where(safe_p, dot_pm / np.where(safe_p, norm_p_sq, 1.0), 0.0)
            hm = hm - hp * scale[None, :]

            # RSS improvement for each knot
            proj_p = np.einsum("i,ij->j", residual, hp)  # (nk,)
            proj_m = np.einsum("i,ij->j", residual, hm)  # (nk,)
            norm_m_sq = np.einsum("ij,ij->j", hm, hm)  # (nk,)

            improvement = np.zeros(nk)
            safe_norm_p = np.where(safe_p, norm_p_sq, 1.0)
            improvement += np.where(safe_p, proj_p**2 / safe_norm_p, 0.0)
            safe_m = norm_m_sq > 1e-12
            safe_norm_m = np.where(safe_m, norm_m_sq, 1.0)
            improvement += np.where(safe_m, proj_m**2 / safe_norm_m, 0.0)

            # Mask out invalid knots
            improvement = np.where(valid, improvement, 0.0)

            k_best = int(np.argmax(improvement))
            if improvement[k_best] > best_improvement:
                best_improvement = improvement[k_best]
                t = float(kv[k_best])
                hf_plus = HingeFactor(j, t, +1)
                hf_minus = HingeFactor(j, t, -1)
                bf_plus = BasisFunction([*parent.factors, hf_plus])
                bf_minus = BasisFunction([*parent.factors, hf_minus])
                best_result = (
                    bf_plus,
                    bf_minus,
                    H_plus[:, k_best].copy(),
                    H_minus[:, k_best].copy(),
                )

    # Only accept if improvement is meaningful
    if best_improvement < 1e-10 * current_rss:
        return None

    return best_result
