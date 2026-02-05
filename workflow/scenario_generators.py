# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""YAML DSL for programmatic scenario generation.

This module provides functionality to expand generator definitions in scenario
YAML files into concrete scenarios at load time. This enables parameter sweeps
(e.g., logarithmically-spaced values) without manually writing repetitive YAML.

Example usage in scenario YAML:
    _generators:
      - name: ghg_{ghg}
        parameters:
          ghg:
            space: log
            start: 5
            stop: 500
            num: 8
            round: true
        template:
          emissions:
            ghg_price: "{ghg}"

Parameters can specify a `name_format` to transform values for scenario names
while keeping original values in the template. The format is a Python lambda:

    _generators:
      - name: land_limit_{limit}
        parameters:
          limit:
            values: [0.1, 0.3, 0.5, 0.7, 1.0]
            name_format: "lambda x: int(x * 100)"  # 0.1 -> "10"
        template:
          land:
            regional_limit: "{limit}"  # Uses original value 0.1

Sensitivity analysis mode using space-filling Sobol sequences:

    _generators:
      - name: pce_{sample_id}
        mode: sensitivity
        samples: 1024
        slice_parameters: [value_per_yll, ghg_price]
        parameters:
          yield_factor:
            lower: 0.8
            upper: 1.2
          ch4_factor:
            distribution: lognormal
            mu: 0.0
            sigma: 0.15
        template:
          sensitivity:
            crop_yields:
              all: "{yield_factor}"
            emission_factors:
              ch4: "{ch4_factor}"
"""

import copy
import itertools

import chaospy as cp
import numpy as np
from scipy.stats.qmc import Sobol


def expand_scenario_defs(raw_defs: dict) -> dict:
    """Expand generator definitions into concrete scenarios.

    Parameters
    ----------
    raw_defs : dict
        Raw scenario definitions, possibly containing a "_generators" key

    Returns
    -------
    dict
        Expanded scenario definitions with all generators replaced by
        concrete scenarios
    """
    if raw_defs is None:
        return {}

    result = {}

    # Copy static scenarios (everything except _generators)
    for key, value in raw_defs.items():
        if key != "_generators":
            result[key] = value

    # Process generators
    generators = raw_defs.get("_generators", [])
    for spec in generators:
        _validate_generator(spec)
        expanded = _expand_generator(spec)
        result.update(expanded)

    return result


def _validate_generator(spec: dict) -> None:
    """Validate generator specification syntax.

    Raises
    ------
    ValueError
        If the generator specification is invalid
    """
    if "name" not in spec:
        raise ValueError("Generator must have a 'name' field")
    if "parameters" not in spec:
        raise ValueError(f"Generator '{spec['name']}' must have a 'parameters' field")
    if "template" not in spec:
        raise ValueError(f"Generator '{spec['name']}' must have a 'template' field")

    mode = spec.get("mode", "zip")

    # Sensitivity mode requires samples count and valid distribution specs
    if mode == "sensitivity":
        if "samples" not in spec:
            raise ValueError(
                f"Generator '{spec['name']}' with mode 'sensitivity' requires 'samples'"
            )
        for param_name, param_spec in spec["parameters"].items():
            _validate_distribution_spec(param_name, param_spec)
        # Validate slice_parameters reference actual parameter names
        slice_params = spec.get("slice_parameters", [])
        for sp in slice_params:
            if sp not in spec["parameters"]:
                raise ValueError(
                    f"Slice parameter '{sp}' not found in generator parameters"
                )
        return

    # Standard modes: zip, grid
    for param_name, param_spec in spec["parameters"].items():
        if "values" in param_spec:
            # Explicit values mode
            if not isinstance(param_spec["values"], list):
                raise ValueError(f"Parameter '{param_name}' 'values' must be a list")
        else:
            # Range mode
            required = ["start", "stop", "num"]
            missing = [f for f in required if f not in param_spec]
            if missing:
                raise ValueError(
                    f"Parameter '{param_name}' missing required fields: {missing}"
                )
            if "space" in param_spec and param_spec["space"] not in ("log", "lin"):
                raise ValueError(
                    f"Parameter '{param_name}' space must be 'log' or 'lin'"
                )


def _generate_values(param_spec: dict) -> list:
    """Generate parameter values from specification.

    Parameters
    ----------
    param_spec : dict
        Parameter specification with either 'values' list or
        'start'/'stop'/'num'/'space' fields

    Returns
    -------
    list
        List of generated values
    """
    if "values" in param_spec:
        return list(param_spec["values"])

    start = param_spec["start"]
    stop = param_spec["stop"]
    num = param_spec["num"]
    space = param_spec.get("space", "lin")
    do_round = param_spec.get("round", False)

    if space == "log":
        values = np.logspace(np.log10(start), np.log10(stop), num)
    else:
        values = np.linspace(start, stop, num)

    if do_round:
        values = np.round(values).astype(int)

    return values.tolist()


def _validate_distribution_spec(param_name: str, param_spec: dict) -> None:
    """Validate distribution specification for a sensitivity parameter.

    Parameters
    ----------
    param_name : str
        Parameter name for error messages
    param_spec : dict
        Parameter specification with distribution info

    Raises
    ------
    ValueError
        If the specification is invalid
    """
    dist = param_spec.get("distribution", "uniform")
    if dist == "uniform":
        if "lower" not in param_spec or "upper" not in param_spec:
            raise ValueError(
                f"Parameter '{param_name}' with uniform distribution "
                "requires 'lower' and 'upper'"
            )
    elif dist == "normal":
        if "mean" not in param_spec or "std" not in param_spec:
            raise ValueError(
                f"Parameter '{param_name}' with normal distribution "
                "requires 'mean' and 'std'"
            )
    elif dist == "lognormal":
        if "mu" not in param_spec or "sigma" not in param_spec:
            raise ValueError(
                f"Parameter '{param_name}' with lognormal distribution "
                "requires 'mu' and 'sigma'"
            )
    else:
        raise ValueError(
            f"Parameter '{param_name}' has unsupported distribution '{dist}'. "
            "Supported: uniform, normal, lognormal"
        )


def build_chaospy_distribution(param_spec: dict) -> cp.Distribution:
    """Build a chaospy marginal distribution from a parameter spec.

    Parameters
    ----------
    param_spec : dict
        Parameter specification with distribution info

    Returns
    -------
    cp.Distribution
        Chaospy marginal distribution
    """
    dist = param_spec.get("distribution", "uniform")
    if dist == "uniform":
        return cp.Uniform(param_spec["lower"], param_spec["upper"])
    elif dist == "normal":
        return cp.Normal(param_spec["mean"], param_spec["std"])
    elif dist == "lognormal":
        return cp.LogNormal(param_spec["mu"], param_spec["sigma"])
    else:
        raise ValueError(f"Unsupported distribution: {dist}")


def build_joint_distribution(
    generator_spec: dict,
) -> tuple[cp.Distribution, list[str]]:
    """Build a chaospy joint distribution from a generator spec.

    Parameters
    ----------
    generator_spec : dict
        Generator specification with parameters

    Returns
    -------
    tuple[cp.Distribution, list[str]]
        Joint distribution and ordered parameter names
    """
    param_names = list(generator_spec["parameters"].keys())
    marginals = [
        build_chaospy_distribution(generator_spec["parameters"][name])
        for name in param_names
    ]
    return cp.J(*marginals), param_names


def _generate_sensitivity_samples(spec: dict) -> list[dict]:
    """Generate space-filling samples for sensitivity analysis.

    Uses a scrambled Sobol sequence in [0,1]^d, transformed to physical
    space via the inverse CDF of each parameter's distribution.

    Parameters
    ----------
    spec : dict
        Generator specification with 'samples', 'parameters' containing
        distribution specs, and optional 'seed'.

    Returns
    -------
    list[dict]
        List of dicts, each mapping parameter names to sampled values.
    """
    param_names = list(spec["parameters"].keys())
    d = len(param_names)
    n_samples = spec["samples"]
    seed = spec.get("seed", 42)

    # Build joint distribution for inverse CDF transform
    joint_dist, _ = build_joint_distribution(spec)

    # Generate Sobol sequence in [0,1]^d
    sampler = Sobol(d, scramble=True, seed=seed)
    unit_samples = sampler.random(n_samples)  # shape (n_samples, d)

    # Transform to physical space via inverse CDF
    # chaospy inv expects shape (d, n_samples)
    physical_samples = joint_dist.inv(unit_samples.T)  # shape (d, n_samples)

    result = []
    for j in range(n_samples):
        result.append(dict(zip(param_names, physical_samples[:, j].tolist())))
    return result


def _zip_parameters(param_values: dict) -> list[dict]:
    """Combine parameters using zip (paired) mode.

    Parameters
    ----------
    param_values : dict
        Dict mapping parameter names to lists of values

    Returns
    -------
    list[dict]
        List of dicts, each mapping parameter names to single values
    """
    param_names = list(param_values.keys())
    value_lists = [param_values[name] for name in param_names]

    # Verify all lists have same length
    lengths = [len(v) for v in value_lists]
    if len(set(lengths)) > 1:
        raise ValueError(
            f"All parameters must have same number of values in zip mode. "
            f"Got lengths: {dict(zip(param_names, lengths))}"
        )

    result = []
    for values in zip(*value_lists):
        result.append(dict(zip(param_names, values)))
    return result


def _grid_parameters(param_values: dict) -> list[dict]:
    """Combine parameters using grid (Cartesian product) mode.

    Parameters
    ----------
    param_values : dict
        Dict mapping parameter names to lists of values

    Returns
    -------
    list[dict]
        List of dicts, each mapping parameter names to single values
    """
    param_names = list(param_values.keys())
    value_lists = [param_values[name] for name in param_names]

    result = []
    for values in itertools.product(*value_lists):
        result.append(dict(zip(param_names, values)))
    return result


def _substitute_values(template, values: dict):
    """Recursively substitute parameter values into template.

    Handles both string substitution (for "{param}" patterns) and
    preserves numeric types when the entire value is a placeholder.

    Parameters
    ----------
    template
        Template structure (dict, list, str, or other)
    values : dict
        Mapping of parameter names to values

    Returns
    -------
        Template with values substituted
    """
    if isinstance(template, dict):
        return {k: _substitute_values(v, values) for k, v in template.items()}
    elif isinstance(template, list):
        return [_substitute_values(item, values) for item in template]
    elif isinstance(template, str):
        # Check if the entire string is a single placeholder
        for param_name, param_value in values.items():
            placeholder = "{" + param_name + "}"
            if template == placeholder:
                # Return the numeric value directly, preserving type
                return param_value
            # Otherwise do string substitution
            template = template.replace(placeholder, str(param_value))
        return template
    else:
        return template


def _get_name_formatters(parameters: dict) -> dict:
    """Build name formatter functions from parameter specs.

    Parameters
    ----------
    parameters : dict
        Parameter specifications, possibly containing 'name_format' fields

    Returns
    -------
    dict
        Mapping of parameter names to formatter functions (identity if no format specified)
    """
    formatters = {}
    for param_name, param_spec in parameters.items():
        if "name_format" in param_spec:
            fmt = param_spec["name_format"]
            # Evaluate lambda expressions
            formatters[param_name] = eval(fmt)
        else:
            formatters[param_name] = lambda x: x
    return formatters


def _expand_generator(spec: dict) -> dict:
    """Expand a single generator specification into concrete scenarios.

    Parameters
    ----------
    spec : dict
        Generator specification with 'name', 'parameters', and 'template'

    Returns
    -------
    dict
        Dict mapping scenario names to their configurations
    """
    name_template = spec["name"]
    template = spec["template"]
    mode = spec.get("mode", "zip")

    # Sensitivity mode generates combinations directly
    if mode == "sensitivity":
        combinations = _generate_sensitivity_samples(spec)
    else:
        # Standard modes: generate values per parameter, then combine
        param_values = {}
        for param_name, param_spec in spec["parameters"].items():
            param_values[param_name] = _generate_values(param_spec)

        if mode == "grid":
            combinations = _grid_parameters(param_values)
        else:  # zip mode (default)
            combinations = _zip_parameters(param_values)

    # Build name formatters
    name_formatters = _get_name_formatters(spec["parameters"])

    # Generate scenarios
    scenarios = {}
    for idx, values in enumerate(combinations):
        # Format values for scenario name
        # For sampling modes, include sample_id placeholder
        name_values = {k: name_formatters[k](v) for k, v in values.items()}
        name_values["sample_id"] = idx
        scenario_name = name_template.format(**name_values)
        # Substitute values into template (using original values)
        scenario_config = _substitute_values(copy.deepcopy(template), values)
        scenarios[scenario_name] = scenario_config

    return scenarios
