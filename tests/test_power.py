import math

import pytest
from scipy.stats import norm

from c3.power import mcnemar_n, required_prs


def test_mcnemar_n_known_value():
    expected = math.ceil(((norm.ppf(1 - 0.05 / 2) * math.sqrt(0.3)
                           + norm.ppf(0.8) * math.sqrt(0.3 - 0.1**2)) / 0.1) ** 2)
    assert mcnemar_n(0.3, 0.1, 0.05, 0.8) == expected


def test_mcnemar_n_grows_when_delta_shrinks():
    assert mcnemar_n(0.3, 0.05, 0.05, 0.8) > mcnemar_n(0.3, 0.1, 0.05, 0.8)


def test_required_prs_rounds_up():
    assert required_prs(101, 4, 1.2) == math.ceil(101 * 1.2 / 4)


@pytest.mark.parametrize("args", [
    (0.3, 0, 0.05, 0.8), (-0.1, 0.1, 0.05, 0.8), (0.3, 0.6, 0.05, 0.8),
    (0.3, 0.1, 1, 0.8), (0.3, 0.1, 0.05, 1),
])
def test_mcnemar_n_rejects_invalid_inputs(args):
    with pytest.raises(ValueError):
        mcnemar_n(*args)
