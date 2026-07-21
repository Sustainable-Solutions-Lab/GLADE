# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Effective multi-cropping combination set: MIRCA-derived merged over config.

The Stage-1 derivation (``derive_mirca_multicropping``, a Snakemake checkpoint)
discovers the observed crop-sequence combinations and writes them to
``combinations.yaml``. The effective set the model builds with is that derived
set merged over the static ``multiple_cropping`` config section (derived entries
take precedence; config entries supplement combos not derived), with any
combination referencing a crop the config does not model dropped.

Shared by the Snakemake input functions (via the checkpoint) and the build
scripts, so both resolve the identical combination set.
"""

from pathlib import Path

import yaml


def effective_combinations(config: dict, combinations_yaml: str | Path) -> dict:
    """The effective multi-cropping combination set for a config.

    ``combinations_yaml`` is the Stage-1 checkpoint's derived set; entries merge
    over ``config["multiple_cropping"]`` and combos with unmodeled crops drop.
    """
    with open(combinations_yaml) as f:
        derived = yaml.safe_load(f) or {}
    merged = {**config["multiple_cropping"], **derived}
    model_crops = set(config["crops"])
    return {
        name: entry
        for name, entry in merged.items()
        if entry and set(entry["crops"]) <= model_crops
    }
