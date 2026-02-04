#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Collate production-pattern PNG frames into an animated GIF."""

import logging
from pathlib import Path

from PIL import Image

from workflow.scripts.logging_config import setup_script_logging

logger = logging.getLogger(__name__)


def _rgba_to_rgb(img: Image.Image) -> Image.Image:
    """Convert an RGBA image to RGB with a white background."""
    if img.mode == "RGBA":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        return background
    return img.convert("RGB")


def main() -> None:
    logger = setup_script_logging(snakemake.log[0])  # type: ignore[name-defined]

    frame_paths: list[str] = snakemake.input.frames  # type: ignore[name-defined]
    output_gif: str = snakemake.output.gif  # type: ignore[name-defined]

    Path(output_gif).parent.mkdir(parents=True, exist_ok=True)

    frames = []
    for path in frame_paths:
        img = Image.open(path)
        frames.append(_rgba_to_rgb(img))
        logger.info("Loaded frame: %s", path)

    if not frames:
        raise ValueError("No frames provided")

    # Save as animated GIF: 2 seconds per frame, infinite loop
    frames[0].save(
        output_gif,
        save_all=True,
        append_images=frames[1:],
        duration=2000,
        loop=0,
    )

    logger.info("Saved animated GIF to %s (%d frames)", output_gif, len(frames))


if __name__ == "__main__":
    main()
