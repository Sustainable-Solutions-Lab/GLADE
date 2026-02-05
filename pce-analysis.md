<!--
SPDX-FileCopyrightText: 2025 Koen van Greevenbroek

SPDX-License-Identifier: CC-BY-4.0
-->

# PCE-Based Global Sensitivity Analysis for food-opt

## 1. Overview

### Objective
Quantify how uncertainty in input parameters propagates to key model outputs (system cost, emissions, health outcomes) and attribute output variance to specific parameters and their interactions. Enable conditional sensitivity analysis across different policy scenarios defined by YLL value and GHG price.

### Problem specification
- **Parameters**: 10-15 uncertain inputs (relative risk factors by food group, aggregate yield/cost/emission factors)
- **Slice parameters**: 2 (YLL value, GHG emissions price) — treated as scenario variables for conditional analysis
- **Outputs**: Multiple (cost, emissions by gas, health burden, land use, etc.)
- **Budget**: ~1000 model evaluations
- **Response surface**: Expected smooth with mild nonlinearity (MILP integers only for piecewise linearization)

### Why PCE
Polynomial Chaos Expansion provides:
1. Analytical Sobol indices from expansion coefficients (no Monte Carlo noise)
2. Analytical conditioning on slice parameters (no refitting)
3. High sample efficiency for smooth, moderate-dimensional problems
4. A functional surrogate for additional analysis

---

## 2. Parameter setup

### 2.1 Define the stochastic parameters

For each uncertain parameter $X_i$, specify:
- **Marginal distribution**: Uniform, normal, lognormal, or beta
- **Support/moments**: Range for uniform, mean/std for normal, etc.
- **Physical interpretation**: What does ±1σ or the range endpoints mean?

Recommended approach for your parameters:

| Parameter category | Distribution | Rationale |
|-------------------|--------------|-----------|
| Relative risk factors | Lognormal or Uniform on [0.8, 1.2] | Multiplicative factors; lognormal if literature gives CI |
| Crop yields | Normal or Uniform | Depends on source; often reported as mean ± % |
| Emission factors | Lognormal | Typically right-skewed uncertainty |
| Costs | Uniform or Triangular | Often have min/max expert bounds |
| YLL value (slice) | Uniform on policy-relevant range | e.g., $50k–$500k |
| GHG price (slice) | Uniform on policy-relevant range | e.g., $0–$300/tCO2e |

### 2.2 Treat slice parameters

Two approaches:

**Approach A — Include in PCE, condition analytically (recommended)**
Include YLL value and GHG price as regular stochastic inputs. After fitting, condition on any values analytically. Gives maximum flexibility.

**Approach B — Factorial design over slices**
Pre-specify a grid (e.g., 3×3 = 9 slice combinations), run ~110 samples per slice, fit separate PCEs. Simpler but less flexible and statistically less efficient.

Approach A is superior given your smooth response surface.

### 2.3 Independence assumption

Standard PCE assumes independent inputs. If parameters are correlated (e.g., yield factors across related crops), you have options:
1. Use Nataf/Rosenblatt transform to map to independent space
2. Use correlated PCE basis (more complex)
3. Ignore if correlations are weak — sensitivity indices will be approximate but often still useful

For a first pass, independence is reasonable. Document the assumption.

---

## 3. Sampling strategy

### 3.1 Recommended design: Sobol sequence

Use a Sobol (quasi-random) sequence for space-filling with better uniformity than random sampling. With 1000 points in 12-17 dimensions, Sobol significantly outperforms Monte Carlo in integration error.

```
# Conceptual — using scipy or SALib
from scipy.stats.qmc import Sobol

sampler = Sobol(d=n_params, scramble=True)
samples_unit = sampler.random(n=1000)  # in [0,1]^d

# Transform to physical space via inverse CDF
samples_physical = transform_to_marginals(samples_unit, param_distributions)
```

### 3.2 Alternative: Latin Hypercube with maximin optimization

LHS ensures each parameter's marginal is evenly covered. Maximin-LHS optimizes point placement to maximize minimum distance between points.

Slightly better one-dimensional projections than Sobol; slightly worse high-dimensional uniformity. Either is fine.

### 3.3 Sample size adequacy check

For PCE regression, you need $N > 2M$ where $M$ is the number of basis terms. For total-degree truncation at degree $p$ with $d$ parameters:

$$M = \binom{d + p}{p}$$

| d (params) | p=2 | p=3 | p=4 |
|------------|-----|-----|-----|
| 10 | 66 | 286 | 1001 |
| 12 | 91 | 455 | 1820 |
| 15 | 136 | 816 | 3876 |

With 1000 samples:
- 10 parameters: degree 3 comfortable, degree 4 marginal
- 12 parameters: degree 3 comfortable, degree 4 infeasible
- 15 parameters: degree 3 marginal, need regularization or hyperbolic truncation

**Recommendation**: Use hyperbolic truncation (see §4.2) or LARS regression to enable degree 3-4 with regularization.

---

## 4. PCE construction

### 4.1 Basis selection

Choose orthogonal polynomial family matching each parameter's distribution:

| Distribution | Polynomial family | Weight function |
|--------------|-------------------|-----------------|
| Uniform[-1,1] | Legendre | 1/2 |
| Normal(0,1) | Hermite (probabilist) | φ(x) |
| Gamma | Laguerre | x^a e^{-x} |
| Beta | Jacobi | (1-x)^a (1+x)^b |

For mixed distributions, use tensor product of univariate bases.

Most practical: transform all parameters to standard uniform or normal, use Legendre or Hermite throughout.

### 4.2 Truncation schemes

**Total-degree truncation**: Include all multi-indices $\alpha$ with $|\alpha| = \sum_i \alpha_i \leq p$.
Simple but grows fast in dimension.

**Hyperbolic truncation** (recommended): Include $\alpha$ with $\|\alpha\|_q = (\sum_i \alpha_i^q)^{1/q} \leq p$ for $q < 1$.
Favors lower-order interactions; more parsimonious. Typical choice: $q = 0.5$ or $q = 0.75$.

**Adaptive sparse PCE**: Start with low degree, add terms based on significance. Best for high-dimensional problems but more complex.

### 4.3 Coefficient estimation

**Least-squares regression** (most common):
$$\hat{c} = \arg\min_c \sum_{j=1}^{N} \left( y_j - \sum_\alpha c_\alpha \Psi_\alpha(x_j) \right)^2$$

Standard linear regression with the design matrix $A_{j\alpha} = \Psi_\alpha(x_j)$.

**LARS (Least Angle Regression)** for sparse PCE:
Automatic term selection; fits parsimonious model. Highly recommended when $M$ approaches or exceeds $N$.

**Regularized regression** (Ridge, LASSO):
Add penalty term to handle ill-conditioning. LASSO gives sparsity like LARS.

**Recommendation**: Use LARS-based sparse PCE. Most robust for your sample/dimension ratio.

### 4.4 Implementation with Chaospy

```python
import chaospy as cp
import numpy as np

# Define joint distribution
distributions = cp.J(
    cp.Uniform(0.8, 1.2),   # RR factor 1
    cp.Uniform(0.8, 1.2),   # RR factor 2
    # ... more parameters
    cp.Uniform(50000, 500000),  # YLL value (slice param)
    cp.Uniform(0, 300),         # GHG price (slice param)
)

# Generate samples
samples = distributions.sample(1000, rule="sobol")

# Run model (your code)
outputs = np.array([run_food_opt(s) for s in samples.T])

# Build PCE
expansion = cp.generate_expansion(order=3, dist=distributions, 
                                   rule="hyperbolic", normed=True)

# Fit via regression (or use point collocation)
model_approx = cp.fit_regression(expansion, samples, outputs)

# Or with LARS for sparsity (if using OpenTURNS instead):
# ... see §4.5
```

### 4.5 Alternative: OpenTURNS

OpenTURNS has more sophisticated sparse PCE (LARS, cleaning) and is worth considering:

```python
import openturns as ot

# Define marginals
marginals = [
    ot.Uniform(0.8, 1.2),  # RR factors
    # ...
    ot.Uniform(50000, 500000),  # YLL value
    ot.Uniform(0, 300),         # GHG price
]
distribution = ot.ComposedDistribution(marginals)

# Adaptive sparse PCE with LARS
basis = ot.OrthogonalProductPolynomialFactory(
    [ot.StandardDistributionPolynomialFactory(m) for m in marginals]
)
enum = ot.HyperbolicAnisotropicEnumerateFunction(len(marginals), 0.5)  # q=0.5
adaptive_basis = ot.FixedStrategy(basis, enum.getBasisSizeFromTotalDegree(4))

projection = ot.LeastSquaresStrategy(
    ot.LeastSquaresMetaModelSelectionFactory(ot.LARS(), ot.CorrectedLeaveOneOut())
)

algo = ot.FunctionalChaosAlgorithm(
    input_samples, output_samples, distribution, adaptive_basis, projection
)
algo.run()
result = algo.getResult()
```

OpenTURNS' LARS + leave-one-out selection is state-of-the-art for sparse PCE.

---

## 5. Sobol index computation

### 5.1 From PCE coefficients

Once you have the expansion $Y = \sum_\alpha c_\alpha \Psi_\alpha(X)$, Sobol indices follow directly from coefficient magnitudes.

**Total variance**:
$$D = \text{Var}[Y] = \sum_{\alpha \neq 0} c_\alpha^2$$
(assuming orthonormal basis; otherwise multiply by $\mathbb{E}[\Psi_\alpha^2]$)

**Partial variance for parameter $i$** (first-order):
$$D_i = \sum_{\alpha: \alpha_i > 0, \alpha_j = 0 \,\forall j \neq i} c_\alpha^2$$
(sum over terms involving only $X_i$)

**First-order Sobol index**:
$$S_i = \frac{D_i}{D}$$

**Total-effect index** (includes all interactions):
$$S_i^T = \frac{\sum_{\alpha: \alpha_i > 0} c_\alpha^2}{D}$$
(sum over all terms involving $X_i$, alone or with others)

**Second-order index** (interaction between $i$ and $j$):
$$S_{ij} = \frac{\sum_{\alpha: \alpha_i > 0, \alpha_j > 0, \alpha_k = 0 \,\forall k \notin \{i,j\}} c_\alpha^2}{D}$$

### 5.2 Implementation

```python
# Chaospy
sobol_first = cp.Sens_m(model_approx, distributions)   # First-order
sobol_total = cp.Sens_t(model_approx, distributions)   # Total-effect

# OpenTURNS
sensitivity = ot.FunctionalChaosSobolIndices(result)
first_order = [sensitivity.getSobolIndex(i) for i in range(n_params)]
total_order = [sensitivity.getSobolTotalIndex(i) for i in range(n_params)]
second_order = [[sensitivity.getSobolIndex([i, j]) for j in range(n_params)] 
                for i in range(n_params)]
```

### 5.3 Interpretation

- $S_i \approx S_i^T$: Parameter $i$ acts mainly independently
- $S_i^T \gg S_i$: Parameter $i$ is involved in significant interactions
- $\sum_i S_i \approx 1$: Additive model, interactions negligible
- $\sum_i S_i < 1$: Interactions present
- $\sum_i S_i^T > 1$: Interactions present (total indices double-count shared variance)

---

## 6. Conditional analysis (slicing)

### 6.1 Analytical conditioning

Let $X_j$ be a slice parameter (YLL value or GHG price). To condition on $X_j = x_j^*$:

$$Y | X_j = x_j^* = \sum_\alpha c_\alpha \cdot \psi_{\alpha_j}(x_j^*) \cdot \prod_{i \neq j} \psi_{\alpha_i}(X_i)$$

Define transformed coefficients:
$$c_\alpha^{(j \to x_j^*)} = c_\alpha \cdot \psi_{\alpha_j}(x_j^*)$$

This is a new PCE in the remaining variables with coefficients obtained by simply evaluating the $j$-th basis polynomials at $x_j^*$.

For two slice parameters $X_j$ and $X_k$:
$$c_\alpha^{(j \to x_j^*, k \to x_k^*)} = c_\alpha \cdot \psi_{\alpha_j}(x_j^*) \cdot \psi_{\alpha_k}(x_k^*)$$

### 6.2 Conditional Sobol indices

After conditioning, compute Sobol indices from the transformed coefficients:

$$D^{(j)} = \sum_{\alpha: \alpha_j = 0, \alpha \neq 0} \left( c_\alpha^{(j \to x_j^*)} \right)^2$$

Wait — this requires care. After conditioning on $X_j$, terms with $\alpha_j > 0$ are now constants (no longer random). The conditional variance involves only terms where the remaining variables appear:

$$\text{Var}[Y | X_j = x_j^*] = \sum_{\alpha: \exists i \neq j \text{ with } \alpha_i > 0} \left( c_\alpha^{(j \to x_j^*)} \right)^2$$

First-order conditional index for parameter $i \neq j$:
$$S_{i|j=x_j^*} = \frac{\sum_{\alpha: \alpha_i > 0, \alpha_k = 0 \,\forall k \notin \{i,j\}} \left( c_\alpha^{(j \to x_j^*)} \right)^2}{\text{Var}[Y | X_j = x_j^*]}$$

### 6.3 Implementation sketch

```python
def conditional_sobol(pce_coefficients, basis_functions, slice_param_idx, slice_value, 
                      param_distributions):
    """
    Compute Sobol indices conditional on slice_param_idx = slice_value.
    
    Parameters
    ----------
    pce_coefficients : array, shape (n_terms,)
    basis_functions : list of multi-index tuples, length n_terms
    slice_param_idx : int, which parameter to condition on
    slice_value : float, value to condition at (in physical space)
    param_distributions : list of marginal distributions
    
    Returns
    -------
    conditional_first_order : dict, param_idx -> S_i|slice
    conditional_total : dict, param_idx -> S_i^T|slice
    conditional_variance : float
    """
    # Transform slice_value to standard space if needed
    slice_dist = param_distributions[slice_param_idx]
    slice_value_std = slice_dist.computeCDF(slice_value) * 2 - 1  # for Legendre on [-1,1]
    
    # Evaluate basis polynomials at slice value
    # (depends on your library's API)
    
    # Transform coefficients
    transformed_coefs = []
    for alpha, c in zip(basis_functions, pce_coefficients):
        psi_j_at_slice = evaluate_legendre(alpha[slice_param_idx], slice_value_std)
        transformed_coefs.append(c * psi_j_at_slice)
    
    # Compute conditional variance (sum over terms with non-slice variables)
    cond_var = sum(
        c**2 for alpha, c in zip(basis_functions, transformed_coefs)
        if any(alpha[i] > 0 for i in range(len(alpha)) if i != slice_param_idx)
    )
    
    # Compute first-order indices for each non-slice parameter
    n_params = len(param_distributions)
    first_order = {}
    for i in range(n_params):
        if i == slice_param_idx:
            continue
        partial_var = sum(
            c**2 for alpha, c in zip(basis_functions, transformed_coefs)
            if alpha[i] > 0 and all(alpha[k] == 0 for k in range(n_params) 
                                     if k not in [i, slice_param_idx])
        )
        first_order[i] = partial_var / cond_var if cond_var > 0 else 0
    
    # Similar for total-effect indices...
    
    return first_order, total_order, cond_var
```

### 6.4 Slice analysis workflow

1. **Define slice grid**: e.g., YLL value ∈ {100k, 250k, 500k}, GHG price ∈ {50, 150, 300}
   → 9 combinations

2. **For each (YLL, GHG) combination**:
   - Compute transformed PCE coefficients
   - Compute conditional Sobol indices for remaining parameters
   - Record conditional variance (tells you total output uncertainty at this scenario)

3. **Visualize**:
   - Heatmap of $S_i^T | (\text{YLL}, \text{GHG})$ for each parameter $i$
   - Line plots: how does $S_{\text{RR\_redmeat}}$ vary with GHG price (at fixed YLL)?
   - Conditional variance surface: where in policy space is output most uncertain?

4. **Interpret**:
   - Parameters whose conditional indices change dramatically across slices have strong interactions with policy variables
   - Low conditional variance regions = robust predictions
   - High conditional variance regions = need more research/data

---

## 7. Validation

### 7.1 Leave-one-out error

Estimate PCE accuracy without additional model runs:

$$\text{LOO} = \frac{1}{N} \sum_{i=1}^{N} \left( y_i - \hat{y}_{-i}(x_i) \right)^2$$

where $\hat{y}_{-i}$ is the PCE fitted without point $i$. For linear regression, this has a closed form (no refitting needed).

Relative LOO error: $\text{LOO} / \text{Var}[y]$. Target: < 0.1 (90% variance explained).

OpenTURNS computes this automatically with corrected LOO.

### 7.2 Validation set

Reserve 10-15% of samples (100-150 runs) as hold-out. Compute:
- $R^2$ on validation set
- Max absolute error
- Error distribution (should be symmetric, no systematic bias)

Trade-off: fewer training samples → less accurate PCE. For 1000 total, I'd use LOO rather than hold-out.

### 7.3 Coefficient stability

Bootstrap: resample training data, refit PCE, check coefficient variation. Large variation in $c_\alpha$ → that term is unreliable → corresponding Sobol contribution is uncertain.

### 7.4 Comparison with direct Monte Carlo

For a subset of Sobol indices, estimate directly via Saltelli's method on the PCE surrogate (cheap) and compare to analytical values. Should match closely — if not, something is wrong with the coefficient → index calculation.

---

## 8. Handling multiple outputs

You have several outputs: cost, emissions (by gas), health burden, land use, etc.

### 8.1 Option A: Separate PCEs

Fit independent PCE for each output. Simple, interpretable, but misses output correlations.

### 8.2 Option B: Joint PCE with PCA

1. Stack outputs into matrix $Y \in \mathbb{R}^{N \times m}$
2. PCA to get principal components
3. Fit PCE for top $k$ PCs (capturing, say, 95% variance)
4. Sobol indices for PCs; transform back to original outputs if needed

Useful if outputs are highly correlated (likely for cost/emissions/land).

### 8.3 Recommendation

Start with separate PCEs. Joint analysis is a refinement if needed.

---

## 9. Practical workflow summary

```
1. SETUP
   ├── Define 10-15 uncertain parameters + 2 slice parameters
   ├── Specify marginal distributions
   └── Define outputs of interest

2. SAMPLING
   ├── Generate 1000-point Sobol sequence in [0,1]^d
   ├── Transform to physical parameter space
   └── Run food-opt for each sample (parallelize!)

3. PCE FITTING
   ├── Choose basis (Legendre for uniform, Hermite for normal)
   ├── Use hyperbolic truncation, degree 3-4
   ├── Fit via LARS (OpenTURNS) or regularized regression
   └── Check LOO error < 0.1

4. GLOBAL SENSITIVITY
   ├── Compute first-order Sobol indices
   ├── Compute total-effect indices
   ├── Identify top 5-7 influential parameters
   └── Check for interactions (S_i^T >> S_i)

5. CONDITIONAL SENSITIVITY (SLICING)
   ├── Define grid over (YLL value, GHG price)
   ├── Compute conditional coefficients at each grid point
   ├── Compute conditional Sobol indices
   └── Visualize: heatmaps, line plots, variance surface

6. VALIDATION & REPORTING
   ├── Report LOO error, R² if validation set used
   ├── Bootstrap confidence intervals on key indices
   ├── Document assumptions (independence, smoothness)
   └── Compare with SHAP/tree analysis if available

7. ITERATION
   ├── If PCE accuracy insufficient: add samples or reduce dimension
   ├── If interactions dominate: investigate with 2nd-order indices
   └── If specific slices show high uncertainty: targeted analysis
```

---

## 10. Recommended tools

| Task | Primary | Alternative |
|------|---------|-------------|
| Sampling | scipy.stats.qmc.Sobol | SALib, pyDOE2 |
| PCE fitting | OpenTURNS | Chaospy, UQLab (MATLAB) |
| Sobol indices | OpenTURNS | Chaospy, manual from coefficients |
| Visualization | matplotlib, seaborn | plotly for interactive |
| Parallelization | joblib, multiprocessing | dask for larger scale |

OpenTURNS is the most complete package for this workflow. Chaospy is lighter-weight and integrates well with numpy. UQLab (MATLAB) is excellent if you're not Python-only.

---

## 11. Expected outputs

1. **Global Sobol index table**: First-order and total-effect for each parameter
2. **Interaction matrix**: Second-order indices (if computed)
3. **Conditional Sobol heatmaps**: One per key parameter, showing S_i across (YLL, GHG) grid
4. **Variance surface**: Total output variance as function of (YLL, GHG)
5. **PCE surrogate**: Usable for fast scenario exploration, optimization, etc.
6. **Validation metrics**: LOO error, R², confidence intervals

---

## 12. Extensions

### 12.1 Anisotropic analysis

If some parameters are expected to be more important, use anisotropic hyperbolic truncation: assign weights to parameters, allowing higher polynomial degree for important ones.

### 12.2 Derivative-based global sensitivity (DGSM)

For smooth functions, compute $\mathbb{E}[(\partial Y / \partial X_i)^2]$. Upper bounds total Sobol index. Can be estimated cheaply and used to screen parameters before full analysis.

### 12.3 Time-varying or distributional outputs

If food-opt outputs distributions (e.g., regional breakdown), extend to functional PCE or compute PCE for summary statistics.

### 12.4 Robust optimization integration

Use PCE surrogate in a robust optimization: minimize expected cost + λ·variance, or optimize worst-case over parameter uncertainty. The surrogate makes this tractable.
