"""Per-step cost of the wave animation: the stored field is validated once, not on every 100 ms step.

Soundness contract of the cache in qtapp/waves.py: a validated field is reused only while a
SHA-256 fingerprint of its full JSON content (computed per step, O(field bytes), far cheaper than
validation) equals the one recorded at validation; any in-place mutation, replacement or tampering
changes the fingerprint and re-runs the unchanged full validation (same errors as before).
"""
import copy, math
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import requires_qt
from anyhydro import wave_preview as wp
from test_wave_preview import request, fixture_field

STEPS = 50


def _panel(field, disturbed, follows=False):
    return SimpleNamespace(field=field, follows_replay=follows, time=0.0,
                           mode=SimpleNamespace(currentIndex=lambda: 2 if disturbed else 1))


def _reference(field, times, disturbed):
    """Slow independent path: the unmodified public function, once per time."""
    return [wp.positions_at_time(field, t, disturbed=disturbed) for t in times]


@requires_qt
@pytest.mark.parametrize('disturbed', [False, True])
def test_positions_bit_identical_to_public_function(disturbed):
    from anymarine.qtapp.waves import WavesPanel
    field = fixture_field(request())
    panel = _panel(field, disturbed)
    times = [0.1 * k for k in range(STEPS)] + [-3.7, 1e5]
    ref = _reference(field, times, disturbed)
    for t, r in zip(times, ref):
        panel.time = t
        got = WavesPanel.positions(panel)
        assert got.dtype == r.dtype and np.array_equal(got, r)


@requires_qt
def test_validation_runs_o1_over_many_steps(monkeypatch):
    from anymarine.qtapp.waves import WavesPanel
    field = fixture_field(request())
    counts = dict(field=0, request=0, hash=0, grid=0)
    for name, key in (('validate_field', 'field'), ('validate_request', 'request'),
                      ('request_hash', 'hash'), ('sample_grid', 'grid')):
        orig = getattr(wp, name)
        def wrap(*a, _o=orig, _k=key, **k):
            counts[_k] += 1
            return _o(*a, **k)
        monkeypatch.setattr(wp, name, wrap)
    panel = _panel(field, True)
    for k in range(STEPS):
        panel.time = 0.1 * k
        WavesPanel.positions(panel)
    assert counts['field'] <= 1, counts
    assert counts['request'] <= 4, counts
    assert counts['hash'] <= 4 and counts['grid'] <= 2, counts


@requires_qt
def test_tampering_after_caching_is_still_refused():
    from anymarine.qtapp.waves import WavesPanel
    field = fixture_field(request())
    panel = _panel(field, True)
    panel.time = 0.3
    WavesPanel.positions(panel)                      # primes the cache
    for change in ('hash', 'grid', 'nonfinite', 'source', 'incident', 'request', 'diffraction_value'):
        bad = copy.deepcopy(field)
        if change == 'hash': bad['request_hash'] = '0' * 64
        if change == 'grid': bad['vertices'][0][0] += .1
        if change == 'nonfinite': bad['incident_real'][0] = float('nan')
        if change == 'source': bad['capytaine_version'] = 'unknown'
        if change == 'incident': bad['incident_real'][0] += .01
        if change == 'request': bad['request']['omega'] *= 1.5
        if change == 'diffraction_value': bad['diffraction_real'][0] = 'x'
        # new field object
        panel.field = bad
        with pytest.raises(ValueError):
            WavesPanel.positions(panel)
        # in-place mutation of the very object that was cached
        panel.field = field
        WavesPanel.positions(panel)
        original = copy.deepcopy(field)
        for key, value in bad.items():
            field[key] = value
        with pytest.raises(ValueError):
            WavesPanel.positions(panel)
        field.clear(); field.update(original)
        WavesPanel.positions(panel)                   # restored content is accepted again
    # same errors as the public function for the other checks
    panel.time = float('nan')
    with pytest.raises(ValueError, match='finite'):
        WavesPanel.positions(panel)
    panel.time = 0.
    incident = fixture_field(request())
    incident = wp.incident_field(request())
    p2 = _panel(incident, True)
    with pytest.raises(ValueError, match='actual diffraction'):
        WavesPanel.positions(p2)
