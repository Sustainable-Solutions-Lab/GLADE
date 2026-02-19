# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Check that restricted (non-redistributable) data files are present."""

from pathlib import Path

# Files from Mottet et al. (2017) GLEAM 2.0 supplementary information.
# Copyright Elsevier; not included in this repository.
# Contact the maintainer to obtain these files.
_MOTTET_2017_DIR = Path("data/curated/gleam_tables/mottet_2017")
_MOTTET_2017_FILES = [
    "gleam_2_0_si2_global_livestock_feed_intake.csv",
    "gleam_2_0_si4_dairy_cattle_composition.csv",
    "gleam_2_0_si5_beef_cattle_composition.csv",
]


def validate_restricted_data(config: dict, root: Path) -> None:
    """Raise if required restricted data files are missing."""
    missing = [
        str(_MOTTET_2017_DIR / f)
        for f in _MOTTET_2017_FILES
        if not (root / _MOTTET_2017_DIR / f).exists()
    ]
    if missing:
        file_list = "\n".join(f"  {f}" for f in missing)
        raise FileNotFoundError(
            f"Required data files are missing:\n{file_list}\n\n"
            "These tables come from the supplementary information of:\n"
            "  Mottet et al. (2017). Livestock: On our plates or eating at our\n"
            "  table? Global Food Security, 14, 1-8.\n"
            "  https://doi.org/10.1016/j.gfs.2017.01.001\n\n"
            "They are copyrighted by Elsevier and cannot be redistributed with\n"
            "this repository. Contact the maintainer to obtain them, then place\n"
            f"them in {_MOTTET_2017_DIR}/."
        )
