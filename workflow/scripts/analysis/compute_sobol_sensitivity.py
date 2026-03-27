# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Dispatcher for Sobol sensitivity analysis methods.

Routes to the PCE or Random Forest implementation based on the
``method`` param (derived from the ``{method}`` wildcard).
"""

method = snakemake.params.method

if method == "pce":
    from workflow.scripts.analysis.compute_pce_sensitivity import run
elif method == "rf":
    from workflow.scripts.analysis.compute_rf_sensitivity import run
elif method == "mars":
    from workflow.scripts.analysis.compute_mars_sensitivity import run
else:
    raise ValueError(f"Unknown sensitivity method '{method}'")

run(snakemake)
