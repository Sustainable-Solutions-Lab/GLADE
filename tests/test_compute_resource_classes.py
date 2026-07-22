# SPDX-FileCopyrightText: 2026 Koen van Greevenbroek
#
# SPDX-License-Identifier: GPL-3.0-or-later

import numpy as np

from workflow.scripts.compute_resource_classes import classify_by_region


def test_classify_by_region_handles_ties_and_invalid_cells():
    score = np.array(
        [
            [1.0, 2.0, 3.0, 4.0, 1.0, 1.0],
            [np.nan, 0.0, -1.0, 1.0, 1.0, 99.0],
        ]
    )
    regions = np.array(
        [
            [0, 0, 0, 0, 1, 1],
            [0, 0, 0, 1, 1, -1],
        ]
    )

    result = classify_by_region(score, regions, [0.0, 0.25, 0.5, 0.75, 1.0])

    np.testing.assert_array_equal(
        result,
        np.array(
            [
                [0, 1, 2, 3, 3, 3],
                [-1, -1, -1, 3, 3, -1],
            ],
            dtype=np.int8,
        ),
    )


def test_classify_by_region_returns_empty_grid_when_no_score_is_positive():
    result = classify_by_region(
        np.array([[np.nan, 0.0]]),
        np.array([[0, 0]]),
        [0.0, 0.5, 1.0],
    )

    np.testing.assert_array_equal(result, np.array([[-1, -1]], dtype=np.int8))
