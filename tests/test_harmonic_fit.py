"""Harmonic fit: exact recovery, convention, refusals (RAO slice 1)."""
import math

import numpy as np
import pytest

from anymarine.harmonic_fit import (HarmonicFitError, fit_window, phasor_change,
                                    steady_windows, window_indices)

W = 1.3
T = np.arange(0, 40.0 + 1e-12, 0.01)


def signal(t, mean=0.25, x1=0.7 - 0.4j, x2=0.05 + 0.02j, slope=0.0):
    out = mean + slope * (t - 20.0)
    for k, x in ((1, x1), (2, x2)):
        out = out + (x * np.exp(-1j * k * W * t)).real
    return out


def test_exact_recovery_and_phasor_convention():
    x = np.column_stack((signal(T), signal(T, x1=-1.0 + 2.0j, x2=0j, mean=-1.0)))
    period = 2 * math.pi / W
    fit = fit_window(T, x, W, 10.0, 10.0 + 4 * period, harmonics=2)
    np.testing.assert_allclose(fit.phasors[0], [0.7 - 0.4j, -1.0 + 2.0j], atol=1e-12)
    np.testing.assert_allclose(fit.phasors[1], [0.05 + 0.02j, 0j], atol=1e-12)
    np.testing.assert_allclose(fit.mean, [0.25, -1.0], atol=1e-12)
    assert np.all(fit.residual_rms < 1e-12) and fit.rank == 5
    # x ~ a cos + b sin  <=>  X = a + i b
    single = fit_window(T, 0.3 * np.cos(W * T) - 0.2 * np.sin(W * T), W, 0.0, 3 * period)
    np.testing.assert_allclose(single.fundamental, [0.3 - 0.2j], atol=1e-12)
    thd = fit.harmonic_distortion()
    assert thd[0] == pytest.approx(abs(0.05 + 0.02j) / abs(0.7 - 0.4j), rel=1e-10) and thd[1] < 1e-12


def test_drift_is_separated_from_the_harmonics():
    x = signal(T, slope=0.003)
    period = 2 * math.pi / W
    with_drift = fit_window(T, x, W, 5.0, 5.0 + 6 * period, harmonics=2, drift=True)
    np.testing.assert_allclose(with_drift.drift, [0.003], atol=1e-12)
    np.testing.assert_allclose(with_drift.phasors[0], [0.7 - 0.4j], atol=1e-11)


def test_steady_windows_and_change():
    (s1, e1), (s2, e2) = steady_windows(40.0, W, 3)
    assert e1 == s2 and e2 == 40.0 and math.isclose(e1 - s1, 3 * 2 * math.pi / W)
    np.testing.assert_allclose(phasor_change([1 + 0j, 0j], [1.1 + 0j, 0j], 1e-8), [0.1 / 1.1, 0.0])
    assert window_indices([0.0, 0.1, 0.2], 0.1, 0.2).tolist() == [1, 2]


@pytest.mark.parametrize('kwargs, match', [
    (dict(start=0.0, end=4.0), 'fewer than 2'),
    (dict(start=0.0, end=20.0, harmonics=0), 'positive integer'),
    (dict(start=0.0, end=20.0, harmonics=300), 'Nyquist'),
    (dict(start=0.0, end=20.0, max_condition=1.0), 'condition'),
    (dict(start=5.0, end=1.0), 'exceed'),
])
def test_refusals(kwargs, match):
    with pytest.raises(HarmonicFitError, match=match):
        fit_window(T, signal(T), W, **kwargs)


def test_nonfinite_and_shape_refusals():
    bad = signal(T)
    bad[10] = np.nan
    with pytest.raises(HarmonicFitError, match='finite'):
        fit_window(T, bad, W, 0.0, 20.0)
    with pytest.raises(HarmonicFitError, match='1-D'):
        fit_window(T, signal(T)[:-1], W, 0.0, 20.0)
