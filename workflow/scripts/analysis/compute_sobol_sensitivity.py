# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Dispatcher for Sobol sensitivity analysis methods.

Routes to the PCE or Random Forest implementation based on the
``method`` key in the generator spec (default: ``"pce"``).
"""

method = snakemake.params.generator_spec.get("method", "pce")

if method == "pce":
    from workflow.scripts.analysis.compute_pce_sensitivity import run
elif method == "rf":
    from workflow.scripts.analysis.compute_rf_sensitivity import run
else:
    raise ValueError(f"Unknown sensitivity method '{method}'")

run(snakemake)
