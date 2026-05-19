# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for shared Snakemake / workflow utilities."""

import pypsa
import pytest

from workflow.scripts.snakemake_utils import (
    FailedSolveError,
    load_solved_network,
)


class TestLoadSolvedNetwork:
    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_solved_network(tmp_path / "not-there.nc")

    def test_empty_placeholder_raises_failed_solve(self, tmp_path):
        """A zero-byte file is the marker that the upstream solve failed; the
        helper must surface this as a FailedSolveError so plotting / analysis
        rules report a clear error instead of an opaque netcdf traceback."""
        placeholder = tmp_path / "model.nc"
        placeholder.touch()
        assert placeholder.stat().st_size == 0
        with pytest.raises(FailedSolveError, match="empty placeholder"):
            load_solved_network(placeholder)

    def test_real_network_round_trips(self, tmp_path):
        n = pypsa.Network()
        n.add("Bus", "b0")
        path = tmp_path / "model.nc"
        n.export_to_netcdf(str(path))
        loaded = load_solved_network(path)
        assert "b0" in loaded.buses.static.index
