# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Data bundle rules.

Two responsibilities:

1. Routing: each `resolve_*` rule produces a canonical processed file from
   either the locally-built `*_built.*` artifact (when `data_bundle.enabled`
   is false) or the corresponding file inside the unpacked Zenodo bundle
   (when enabled). Downstream rules reference only the canonical paths.

2. Packaging: `package_data_bundle` collects the seven bundle artifacts
   produced by a vanilla food-opt run, computes hashes, renders templates,
   and emits a Zenodo-ready zip + manifest.

A separate `download_data_bundle` rule fetches and unpacks the published
bundle when consuming.
"""

shared_luc_dir = "<processing>/shared/luc"

# Mapping (canonical relative path inside bundle) → metadata for the seven
# bundle artifacts. Drives both routing and packaging.
BUNDLE_ARTIFACTS: dict[str, dict[str, str]] = {
    # --- Tier A: license-restricted derivatives ---
    "diet/gdd_dietary_intake.csv": {
        "source": "Global Dietary Database (Tufts University)",
        "source_license": "GDD Terms of Use (non-commercial, no redistribution of original)",
    },
    "diet/gbd_dietary_risk_exposure.csv": {
        "source": "IHME Global Burden of Disease 2019 — Dietary Risk Exposure",
        "source_license": "IHME Free-of-Charge Non-Commercial User Agreement",
    },
    "health/gbd_mortality_rates.csv": {
        "source": "IHME Global Burden of Disease 2023 — Mortality",
        "source_license": "IHME Free-of-Charge Non-Commercial User Agreement",
    },
    "health/relative_risks.csv": {
        "source": "IHME Global Burden of Disease 2019 — Relative Risks (Appendix Table 7a)",
        "source_license": "IHME Free-of-Charge Non-Commercial User Agreement",
    },
    # --- Tier B: redistributable, bundled for convenience ---
    "luc/land_cover_resampled.nc": {
        "source": "Copernicus Climate Change Service (C3S) Satellite Land Cover",
        "source_license": "Copernicus / ESA CCI / VITO terms (CC BY with attribution)",
    },
    "luc/regrowth_resampled.nc": {
        "source": "Cook-Patton et al. 2020 — Forest Carbon Accumulation Potential",
        "source_license": "CC BY 4.0",
    },
    "luc/luicube_grassland.nc": {
        "source": "Matej et al. 2025 — LUIcube grassland (GL-owl + GL-notrees)",
        "source_license": "CC BY 4.0",
    },
}


def _bundle_local_path(rel_path: str) -> str:
    return f"{config['data_bundle']['destination']}/{rel_path}"


# --- Resolve rules: canonical path comes from either bundle or built file ---


def _gdd_dietary_intake_input(wildcards):
    if config["data_bundle"]["enabled"]:
        return _bundle_local_path("diet/gdd_dietary_intake.csv")
    return f"<processing>/{wildcards.name}/gdd_dietary_intake_built.csv"


rule resolve_gdd_dietary_intake:
    input:
        _gdd_dietary_intake_input,
    output:
        "<processing>/{name}/gdd_dietary_intake.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/resolve_gdd_dietary_intake.log",
    shell:
        "cp {input} {output}"


def _gbd_dietary_risk_exposure_input(wildcards):
    if config["data_bundle"]["enabled"]:
        return _bundle_local_path("diet/gbd_dietary_risk_exposure.csv")
    return f"<processing>/{wildcards.name}/gbd_dietary_risk_exposure_built.csv"


rule resolve_gbd_dietary_risk_exposure:
    input:
        _gbd_dietary_risk_exposure_input,
    output:
        "<processing>/{name}/gbd_dietary_risk_exposure.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/resolve_gbd_dietary_risk_exposure.log",
    shell:
        "cp {input} {output}"


def _gbd_mortality_rates_input(wildcards):
    if config["data_bundle"]["enabled"]:
        return _bundle_local_path("health/gbd_mortality_rates.csv")
    return f"<processing>/{wildcards.name}/health/gbd_mortality_rates_built.csv"


rule resolve_gbd_mortality_rates:
    input:
        _gbd_mortality_rates_input,
    output:
        "<processing>/{name}/health/gbd_mortality_rates.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/resolve_gbd_mortality_rates.log",
    shell:
        "cp {input} {output}"


def _relative_risks_input(wildcards):
    if config["data_bundle"]["enabled"]:
        return _bundle_local_path("health/relative_risks.csv")
    return f"<processing>/{wildcards.name}/health/relative_risks_built.csv"


rule resolve_relative_risks:
    input:
        _relative_risks_input,
    output:
        "<processing>/{name}/health/relative_risks.csv",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/{name}/resolve_relative_risks.log",
    shell:
        "cp {input} {output}"


def _land_cover_resampled_input(_wildcards):
    if config["data_bundle"]["enabled"]:
        return _bundle_local_path("luc/land_cover_resampled.nc")
    return f"{shared_luc_dir}/land_cover_resampled_built.nc"


rule resolve_land_cover_resampled:
    input:
        _land_cover_resampled_input,
    output:
        f"{shared_luc_dir}/land_cover_resampled.nc",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/shared/resolve_land_cover_resampled.log",
    shell:
        "cp {input} {output}"


def _regrowth_resampled_input(_wildcards):
    if config["data_bundle"]["enabled"]:
        return _bundle_local_path("luc/regrowth_resampled.nc")
    return f"{shared_luc_dir}/regrowth_resampled_built.nc"


rule resolve_regrowth_resampled:
    input:
        _regrowth_resampled_input,
    output:
        f"{shared_luc_dir}/regrowth_resampled.nc",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/shared/resolve_regrowth_resampled.log",
    shell:
        "cp {input} {output}"


def _luicube_grassland_input(_wildcards):
    if config["data_bundle"]["enabled"]:
        return _bundle_local_path("luc/luicube_grassland.nc")
    return f"{shared_luc_dir}/luicube_grassland_built.nc"


rule resolve_luicube_grassland:
    input:
        _luicube_grassland_input,
    output:
        f"{shared_luc_dir}/luicube_grassland.nc",
    group:
        "prep"
    resources:
        runtime="1m",
        mem_mb=200,
    log:
        "<logs>/shared/resolve_luicube_grassland.log",
    shell:
        "cp {input} {output}"


# --- Packaging rule: produces the Zenodo-ready zip + manifest ---

_BUNDLE_VERSION = config["data_bundle"]["version"]
_BUNDLE_DIR = f"bundle/{_BUNDLE_VERSION}"
_BUNDLE_NAME_FOR_BUILD = config.get("name", "data_bundle")


rule package_data_bundle:
    """Collect the seven bundle artifacts and emit a Zenodo-ready zip.

    Reads from the *_built.* paths directly to avoid re-routing through the
    resolve rules (which would loop back to the bundle when enabled).
    """
    input:
        gdd_intake=f"<processing>/{_BUNDLE_NAME_FOR_BUILD}/gdd_dietary_intake_built.csv",
        gbd_exposure=f"<processing>/{_BUNDLE_NAME_FOR_BUILD}/gbd_dietary_risk_exposure_built.csv",
        gbd_mortality=f"<processing>/{_BUNDLE_NAME_FOR_BUILD}/health/gbd_mortality_rates_built.csv",
        relative_risks=f"<processing>/{_BUNDLE_NAME_FOR_BUILD}/health/relative_risks_built.csv",
        land_cover=f"{shared_luc_dir}/land_cover_resampled_built.nc",
        regrowth=f"{shared_luc_dir}/regrowth_resampled_built.nc",
        luicube=f"{shared_luc_dir}/luicube_grassland_built.nc",
        readme_tmpl="docs/data_bundle/README.template.md",
        license_tmpl="docs/data_bundle/LICENSE.template.txt",
        attributions_tmpl="docs/data_bundle/ATTRIBUTIONS.template.md",
        citation_tmpl="docs/data_bundle/CITATION.template.cff",
    params:
        version=_BUNDLE_VERSION,
        baseline_year=config["baseline_year"],
        zenodo_doi=config["data_bundle"]["zenodo_doi"],
        bundle_layout=BUNDLE_ARTIFACTS,
        compatibility={
            "baseline_year": config["baseline_year"],
            "countries": config["countries"],
            "food_groups": config["food_groups"]["included"],
            "causes": config["health"]["causes"],
            "risk_factors": config["health"]["risk_factors"],
        },
    output:
        zip=f"{_BUNDLE_DIR}/food-opt-data-bundle-{_BUNDLE_VERSION}.zip",
        manifest=f"{_BUNDLE_DIR}/manifest.yaml",
    log:
        f"<logs>/shared/package_data_bundle_{_BUNDLE_VERSION}.log",
    benchmark:
        f"<benchmarks>/shared/package_data_bundle_{_BUNDLE_VERSION}.tsv"
    script:
        "../scripts/package_data_bundle.py"


# Convenience target
rule build_data_bundle:
    input:
        rules.package_data_bundle.output.zip,


# --- Bundle download (consumer side) ---


rule download_data_bundle:
    """Download and unpack the published bundle from Zenodo.

    Implementation deferred until a Zenodo DOI is minted. Once the bundle
    is uploaded, replace the shell body with a curl + unzip + manifest hash
    verification flow.
    """
    output:
        marker=f"{config['data_bundle']['destination']}/.bundle_{_BUNDLE_VERSION}.ok",
    params:
        doi=config["data_bundle"]["zenodo_doi"],
        version=_BUNDLE_VERSION,
        destination=config["data_bundle"]["destination"],
    log:
        "<logs>/shared/download_data_bundle.log",
    shell:
        """
        if [ -z "{params.doi}" ]; then
            echo "ERROR: data_bundle.zenodo_doi is not set." >&2
            echo "Set it in config or run with data_bundle.enabled: false." >&2
            exit 1
        fi
        echo "TODO: implement Zenodo download for DOI {params.doi}" | tee {log}
        exit 1
        """
