# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation of configuration schema and values using JSON Schema."""

from pathlib import Path

from snakemake.utils import validate as snakemake_validate


def validate_config_schema(config: dict, project_root: Path) -> None:
    """Validate configuration against JSON Schema.

    Uses Snakemake's native validation with a comprehensive JSON Schema
    that covers all required and optional configuration keys, types,
    and constraints.

    Parameters
    ----------
    config:
        The merged Snakemake configuration dictionary.
    project_root:
        Root directory of the repository.

    Raises
    ------
    jsonschema.exceptions.ValidationError
        If the configuration does not conform to the schema.
    """
    schema_path = project_root / "config" / "schemas" / "config.schema.yaml"
    snakemake_validate(config, schema=str(schema_path))
