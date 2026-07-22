# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

import pytest

from workflow.scripts.extract_mirca_os_archive import extract_members


def test_extract_members_batches_and_installs_outputs(tmp_path, monkeypatch):
    archive = tmp_path / "source.rar"
    archive.touch()
    outputs = [tmp_path / "out" / "first.tif", tmp_path / "out" / "second.nc"]
    patterns = ["*5-arcminute/first.tif", "*monthly/second.nc"]
    commands = []

    def fake_run(command, check):
        commands.append(command)
        assert check
        extraction_dir = Path(command[command.index("-C") + 1])
        for pattern in patterns:
            member = extraction_dir / "nested" / Path(pattern).name
            member.parent.mkdir(parents=True, exist_ok=True)
            member.write_text(Path(pattern).name)

    monkeypatch.setattr(
        "workflow.scripts.extract_mirca_os_archive.subprocess.run", fake_run
    )

    extract_members(archive, patterns, outputs)

    assert len(commands) == 1
    assert commands[0][-2:] == [f"--include={p}" for p in patterns]
    assert [path.read_text() for path in outputs] == ["first.tif", "second.nc"]


def test_extract_members_requires_one_glob_per_output(tmp_path):
    with pytest.raises(ValueError, match="one MIRCA member glob per output"):
        extract_members(tmp_path / "source.rar", ["*one.tif"], [])
