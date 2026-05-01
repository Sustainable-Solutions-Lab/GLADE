# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Stage, hash, and zip the food-opt data bundle for Zenodo upload.

Reads from the locally-built `*_built.*` artifacts produced by a vanilla
food-opt run, copies them into a versioned staging directory under their
canonical bundle-relative paths, computes SHA256 hashes, renders the
README/LICENSE/ATTRIBUTIONS/CITATION templates, writes manifest.yaml,
and produces a Zenodo-ready zip.

Templates use simple {{placeholder}} substitution; no Jinja dependency.
"""

import datetime as dt
import hashlib
import logging
from pathlib import Path
import shutil
import subprocess
import zipfile

import yaml

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(2**20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _render_template(template_text: str, substitutions: dict[str, str]) -> str:
    rendered = template_text
    for key, value in substitutions.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def _format_files_section(files: list[dict]) -> str:
    """Format the list of files as a Markdown table for README rendering."""
    lines = ["| Path | Size | SHA256 (first 12 hex) | Source |", "|---|---|---|---|"]
    for f in files:
        sha_short = f["sha256"][:12]
        size_kb = f["size_bytes"] / 1024.0
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
        lines.append(f"| `{f['path']}` | {size_str} | `{sha_short}` | {f['source']} |")
    return "\n".join(lines)


def main() -> None:
    snake = globals().get("snakemake")  # type: ignore[name-defined]
    if snake is None:
        raise RuntimeError("This script must run via Snakemake")

    version: str = snake.params["version"]
    baseline_year: int = snake.params["baseline_year"]
    zenodo_doi: str = snake.params["zenodo_doi"]
    bundle_layout: dict[str, dict[str, str]] = snake.params["bundle_layout"]
    compatibility: dict = snake.params["compatibility"]

    zip_output = Path(snake.output["zip"])
    manifest_output = Path(snake.output["manifest"])
    bundle_dir = zip_output.parent
    staging_dir = bundle_dir / "staging"

    # Map snakemake input keys → canonical bundle relative paths.
    # The keys here must match the input declarations in package_data_bundle.
    input_key_to_rel_path = {
        "gdd_intake": "diet/gdd_dietary_intake.csv",
        "gbd_exposure": "diet/gbd_dietary_risk_exposure.csv",
        "gbd_mortality": "health/gbd_mortality_rates.csv",
        "relative_risks": "health/relative_risks.csv",
        "land_cover": "luc/land_cover_resampled.nc",
        "regrowth": "luc/regrowth_resampled.nc",
        "luicube": "luc/luicube_grassland.nc",
    }

    logger.info("Staging bundle %s into %s", version, staging_dir)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    file_records: list[dict] = []
    for input_key, rel_path in input_key_to_rel_path.items():
        src = Path(snake.input[input_key])
        dst = staging_dir / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        sha = _sha256(dst)
        size = dst.stat().st_size
        meta = bundle_layout[rel_path]
        file_records.append(
            {
                "path": rel_path,
                "sha256": sha,
                "size_bytes": size,
                "source": meta["source"],
                "source_license": meta["source_license"],
            }
        )
        logger.info("  + %s (%d bytes, sha=%s...)", rel_path, size, sha[:12])

    # Build manifest
    repo_root = Path.cwd()
    commit = _git_commit(repo_root)
    build_date = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")

    manifest = {
        "name": "food-opt-data-bundle",
        "version": version,
        "build_date": build_date,
        "food_opt_commit": commit,
        "zenodo_doi": zenodo_doi or None,
        "license": "CC-BY-NC-4.0",
        "compatibility": compatibility,
        "files": file_records,
    }

    # Render templates
    readme_tmpl = Path(snake.input["readme_tmpl"]).read_text(encoding="utf-8")
    license_tmpl = Path(snake.input["license_tmpl"]).read_text(encoding="utf-8")
    attributions_tmpl = Path(snake.input["attributions_tmpl"]).read_text(
        encoding="utf-8"
    )
    citation_tmpl = Path(snake.input["citation_tmpl"]).read_text(encoding="utf-8")

    common_subs = {
        "VERSION": version,
        "BUILD_DATE": build_date,
        "BASELINE_YEAR": str(baseline_year),
        "ZENODO_DOI": zenodo_doi or "(not yet minted)",
        "FOOD_OPT_COMMIT": commit or "(unknown)",
        "FILE_TABLE": _format_files_section(file_records),
        "COUNTRIES_LIST": ", ".join(compatibility["countries"]),
        "COUNTRIES_COUNT": str(len(compatibility["countries"])),
        "FOOD_GROUPS_LIST": ", ".join(compatibility["food_groups"]),
        "CAUSES_LIST": ", ".join(compatibility["causes"]),
        "RISK_FACTORS_LIST": ", ".join(compatibility["risk_factors"]),
    }

    readme = _render_template(readme_tmpl, common_subs)
    license_text = _render_template(license_tmpl, common_subs)
    attributions = _render_template(attributions_tmpl, common_subs)
    citation = _render_template(citation_tmpl, common_subs)

    (staging_dir / "README.md").write_text(readme, encoding="utf-8")
    (staging_dir / "LICENSE").write_text(license_text, encoding="utf-8")
    (staging_dir / "ATTRIBUTIONS.md").write_text(attributions, encoding="utf-8")
    (staging_dir / "CITATION.cff").write_text(citation, encoding="utf-8")
    (staging_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )

    # Also surface the manifest at the bundle directory level for inspection
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(staging_dir / "manifest.yaml", manifest_output)

    # Zip the staging directory under a top-level dir named after the archive
    # so the bundle unpacks cleanly into food-opt-data-bundle-<version>/.
    archive_root = f"food-opt-data-bundle-{version}"
    logger.info("Writing %s", zip_output)
    with zipfile.ZipFile(zip_output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(staging_dir.rglob("*")):
            if path.is_file():
                arcname = Path(archive_root) / path.relative_to(staging_dir)
                zf.write(path, arcname=str(arcname))

    # Summary
    total_size = sum(f["size_bytes"] for f in file_records)
    logger.info(
        "Bundle %s ready: %d files, %.2f MB uncompressed → %s",
        version,
        len(file_records),
        total_size / 1024**2,
        zip_output,
    )


if __name__ == "__main__":
    setup_script_logging(
        log_file=globals().get("snakemake").log[0]  # type: ignore[name-defined]
        if globals().get("snakemake") and globals()["snakemake"].log  # type: ignore[name-defined]
        else None
    )
    main()
