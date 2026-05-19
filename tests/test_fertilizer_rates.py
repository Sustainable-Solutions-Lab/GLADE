# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for global fertilizer rate derivation and crop proxy mappings."""

import pandas as pd
import pytest

from workflow.scripts.derive_global_fertilizer_rates import calculate_percentile_rates


@pytest.fixture
def sample_country_rates():
    """Three countries x two crops with known percentile profile."""
    return pd.DataFrame(
        {
            "country": ["USA", "FRA", "BRA", "USA", "FRA", "BRA"],
            "crop": ["maize", "maize", "maize", "banana", "banana", "banana"],
            "n_rate_kg_ha": [100.0, 150.0, 200.0, 50.0, 60.0, 70.0],
        }
    )


class TestCalculatePercentileRates:
    def test_proxy_inherits_source_rate(self, sample_country_rates):
        """plantain (proxy of banana) and silage-maize (proxy of maize) inherit the
        source crop's percentile rate."""
        result = calculate_percentile_rates(
            sample_country_rates,
            percentile=50,
            crops=["maize", "banana", "plantain", "silage-maize"],
            proxy_rates={"plantain": "banana", "silage-maize": "maize"},
        )
        result = result.set_index("crop")["n_rate_kg_per_ha"]
        assert result["banana"] == pytest.approx(60.0)
        assert result["plantain"] == pytest.approx(result["banana"])
        assert result["maize"] == pytest.approx(150.0)
        assert result["silage-maize"] == pytest.approx(result["maize"])

    def test_missing_crop_raises(self, sample_country_rates):
        """A model crop with neither direct data nor a proxy must error out."""
        with pytest.raises(ValueError, match="Missing N application rates"):
            calculate_percentile_rates(
                sample_country_rates,
                percentile=50,
                crops=["maize", "banana", "cassava"],
                proxy_rates={},
            )

    def test_proxy_with_missing_source_raises(self, sample_country_rates):
        """A proxy mapping whose source is not in the FUBC table must error out."""
        with pytest.raises(ValueError, match="absent from the derived rates table"):
            calculate_percentile_rates(
                sample_country_rates,
                percentile=50,
                crops=["maize", "banana", "plantain"],
                proxy_rates={"plantain": "yam"},
            )
