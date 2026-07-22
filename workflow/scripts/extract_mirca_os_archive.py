"""
SPDX-FileCopyrightText: 2026 Koen van Greevenbroek

SPDX-License-Identifier: GPL-3.0-or-later

Extract a requested set of MIRCA-OS files in one pass through a RAR archive.

The MIRCA archives are large enough that launching ``bsdtar`` separately for
every raster wastes substantial I/O. The associated rules pass one member glob
per output and this script extracts the whole batch into a temporary directory,
checks that every glob resolved to exactly one file, then installs the outputs
atomically.
"""

from pathlib import Path
import subprocess
import tempfile


def extract_members(
    archive: str | Path,
    member_globs: list[str],
    outputs: list[str | Path],
) -> None:
    """Extract ``member_globs`` from ``archive`` to corresponding ``outputs``."""
    if len(member_globs) != len(outputs):
        raise ValueError(
            f"Expected one MIRCA member glob per output, got "
            f"{len(member_globs)} globs and {len(outputs)} outputs"
        )
    if not outputs:
        raise ValueError("Expected at least one MIRCA archive output")

    output_paths = [Path(path) for path in outputs]
    for output in output_paths:
        output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=".mirca-extract-", dir=output_paths[0].parent
    ) as tmp_name:
        tmp = Path(tmp_name)
        command = ["bsdtar", "-xf", str(archive), "-C", str(tmp)]
        command.extend(f"--include={pattern}" for pattern in member_globs)
        subprocess.run(command, check=True)

        extracted: list[Path] = []
        for member_glob in member_globs:
            basename = Path(member_glob).name
            matches = list(tmp.rglob(basename))
            if len(matches) != 1:
                raise RuntimeError(
                    f"MIRCA member glob '{member_glob}' extracted {len(matches)} "
                    f"files named '{basename}', expected exactly one"
                )
            extracted.append(matches[0])

        parts = [output.with_suffix(output.suffix + ".part") for output in output_paths]
        try:
            for source, part in zip(extracted, parts, strict=True):
                source.replace(part)
            for part, output in zip(parts, output_paths, strict=True):
                part.replace(output)
        except BaseException:
            for part in parts:
                part.unlink(missing_ok=True)
            raise


def main() -> None:
    extract_members(
        snakemake.input.rar,  # type: ignore[name-defined]
        list(snakemake.params.member_globs),  # type: ignore[name-defined]
        list(snakemake.output),  # type: ignore[name-defined]
    )


if __name__ == "__main__":
    main()
