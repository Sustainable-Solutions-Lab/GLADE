# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Provenance tracking for calibration artefact sets.

Each artefact set under ``data/curated/calibration/<source>/`` carries a
``provenance.yaml`` recording the structural configuration it was
calibrated against (written by ``tools/calibrate`` via
``workflow/scripts/write_calibration_provenance.py``). At workflow start
the active config is compared against the stamp of the set it consumes
(``calibration.source``); a mismatch means the artefacts were fit under
different structural assumptions and are not valid for this config.

The comparison covers all *structural* config leaves: solve-time keys
(SOLVE_TIME_CONFIG_PREFIXES) never invalidate calibration, and a small
set of additional prefixes is exempted below because it describes run
identity or calibration machinery rather than what the artefacts were
fit to.
"""

from pathlib import Path

from snakemake.logging import logger
import yaml

from workflow.scripts.solve_namespace import _is_solve_time_key, resolve_gbd_anchoring

CALIBRATION_DIR = Path("data/curated/calibration")
PROVENANCE_FILENAME = "provenance.yaml"

# Exempt from the structural snapshot, on top of the solve-time prefixes.
PROVENANCE_EXEMPT_PREFIXES = {
    # Run identity and workflow machinery.
    "name",
    "scenarios",
    "paths",
    "downloads",
    "credentials",
    "calibration",
    # Validation-mode switches: the artefacts are consumed by regular and
    # validation-mode solves alike (the calibration chain itself runs in
    # validation mode).
    "validation",
    # Artefacts are fit at baseline_year and applied at any planning
    # horizon by design.
    "planning_horizon",
    # Post-solve analysis only.
    "sensitivity_analysis",
    # Calibration application/generation machinery. Fit-relevant knobs in
    # these sections (e.g. food_loss_waste_calibration.food_groups,
    # food_demand_calibration.min_multiplier) stay in the snapshot.
    "grazing.grassland_forage_calibration",
    "exogenous_feed_calibration",
    "cost_calibration",
    "food_loss_waste_calibration.enabled",
    "food_loss_waste_calibration.generate",
    "food_loss_waste_calibration.scenario",
    "food_loss_waste_calibration.calibration_file",
    "food_demand_calibration.enabled",
    "food_demand_calibration.generate",
    "food_demand_calibration.scenario",
    "food_demand_calibration.calibration_file",
}

# Dotted paths of the generate flags of all calibration sections; any of
# them being true marks a generation run (the calibration chain itself),
# for which the provenance check is skipped.
_GENERATE_FLAG_PATHS = [
    ("grazing", "grassland_forage_calibration", "generate"),
    ("exogenous_feed_calibration", "generate"),
    ("food_loss_waste_calibration", "generate"),
    ("food_demand_calibration", "generate"),
    ("cost_calibration", "generate"),
    ("deviation_penalty", "calibration", "generate"),
]


def _is_exempt(key: str) -> bool:
    return any(key == p or key.startswith(p + ".") for p in PROVENANCE_EXEMPT_PREFIXES)


def structural_snapshot(config: dict) -> dict:
    """Flat dotted-key map of the fit-relevant structural config leaves."""
    snapshot: dict = {}

    def walk(node: dict, prefix: str) -> None:
        for k, v in node.items():
            full = f"{prefix}.{k}" if prefix else str(k)
            if _is_solve_time_key(full) or _is_exempt(full):
                continue
            if isinstance(v, dict):
                walk(v, full)
            else:
                snapshot[full] = v

    walk(config, "")
    # diet.anchor_groups_to_gbd may hold the sentinel "match_health", which
    # resolves through health.enabled -- a solve-time (and therefore exempt)
    # key. Snapshot the resolved boolean so two configs with different
    # resolved anchoring (and thus different baseline diets) never stamp
    # identically.
    if "diet.anchor_groups_to_gbd" in snapshot:
        snapshot["diet.anchor_groups_to_gbd"] = resolve_gbd_anchoring(config)
    # The diet-source blocks (diet.fbs, diet.gdd_ia) only shape the
    # baseline diet when their source is active; drop the inactive one so
    # its knobs cannot spuriously (in)validate a stamp.
    inactive = "diet.gdd_ia" if config["diet"]["source"] == "fbs" else "diet.fbs"
    snapshot = {
        k: v
        for k, v in snapshot.items()
        if k != inactive and not k.startswith(inactive + ".")
    }
    return snapshot


def diff_snapshots(stamped: dict, active: dict) -> list[str]:
    """Human-readable differences between a stamp and the active snapshot."""
    diffs = []
    for key in sorted(stamped.keys() | active.keys()):
        if key not in active:
            diffs.append(f"{key}: in stamp ({stamped[key]!r}) but not in config")
        elif key not in stamped:
            diffs.append(f"{key}: in config ({active[key]!r}) but not in stamp")
        elif stamped[key] != active[key]:
            diffs.append(f"{key}: stamp {stamped[key]!r} != config {active[key]!r}")
    return diffs


def provenance_path(source: str, project_root: Path) -> Path:
    return project_root / CALIBRATION_DIR / source / PROVENANCE_FILENAME


def load_provenance(source: str, project_root: Path) -> dict:
    path = provenance_path(source, project_root)
    if not path.exists():
        raise ValueError(
            f"Calibration artefact set '{source}' has no provenance stamp "
            f"({path}). Regenerate the set with tools/calibrate (which "
            "stamps it), or point calibration.source at a stamped set."
        )
    with open(path) as f:
        return yaml.safe_load(f)


def is_generation_run(config: dict) -> bool:
    """True if any calibration section has generate=true (calibration chain)."""
    for path in _GENERATE_FLAG_PATHS:
        node = config
        for key in path:
            node = node[key]
        if node:
            return True
    return False


def validate_calibration_provenance(
    config: dict, project_root: Path | None = None
) -> None:
    """Check the active config against the consumed artefact set's stamp."""
    if is_generation_run(config):
        return

    root = Path(project_root) if project_root else Path.cwd()
    source = config["calibration"]["source"]
    stamp = load_provenance(source, root)
    diffs = diff_snapshots(stamp["structural_config"], structural_snapshot(config))
    if not diffs:
        return

    bullet_list = "\n".join(f"   {d}" for d in diffs)
    message = (
        f"The active config differs structurally from what calibration "
        f"artefact set '{source}' was calibrated against "
        f"({provenance_path(source, root)}):\n{bullet_list}\n"
        "The artefacts are not valid for this config. Either recalibrate "
        "(set a dedicated calibration.source in this config and run "
        "tools/calibrate --base <configfile>), point calibration.source at "
        "a compatible set, or set calibration.accept_provenance_mismatch: "
        "true to knowingly proceed."
    )
    if config["calibration"]["accept_provenance_mismatch"]:
        logger.warning(
            f"Accepted calibration provenance mismatch ({len(diffs)} "
            f"differing keys) between the active config and artefact set "
            f"'{source}'."
        )
        return
    raise ValueError(message)
