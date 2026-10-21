"""Basis/symmetrisation programme C1: the import gate (ADR-015 s8, per-sample skew bound 1e-2) is unchanged; a kernel
the ANYhydro producer symmetrised (declared, recorded skew) is admitted, a tiny-norm-sample skew in an unsymmetrised
kernel is still refused."""
import dataclasses

import numpy as np
import pytest

from test_hydro_rigid_import import (HydroAdmissionError, _options, _jsonable_value, _project, _source, anyhydro,
                                     import_rigid_hydro_inputs, requires_solver)

pytestmark = requires_solver

N_STEPS = 801                       # the run length of _options: the import reads the first n_steps samples
SYM = dict(kernel_symmetrisation="symmetric_part", kernel_skew_bound=1e-2)


def _tiny_norm_skew(kernel):
    """Skew of 5 % of its own norm on the last non-zero sample (small next to the kernel peak, so the reconstruction
    gate is unaffected); returns the kernel and that sample's index."""
    K = np.array(kernel.kernel)
    last = int(np.flatnonzero(np.any(K[:N_STEPS] != 0.0, axis=(1, 2)))[-1])
    skew = np.diag(np.ones(5), 1) - np.diag(np.ones(5), -1)
    K[last] = K[last] + 0.05 * np.linalg.norm(K[last]) * skew
    assert np.linalg.norm(K[last] - K[last].T) / np.linalg.norm(K[last]) > 1e-2
    assert np.abs(K[last] - K[last].T).max() < 1e-3 * np.abs(K).max()       # whole-kernel skew is small
    return dataclasses.replace(kernel, kernel=K), last


def test_unsymmetrised_tiny_norm_skew_is_still_refused(tmp_path):
    project = _project()
    ds_path, _, _, kernel = _source(tmp_path, project)
    path = tmp_path / "tiny.nc"
    tiny, last = _tiny_norm_skew(kernel)
    anyhydro.save_radiation_kernel(tiny, path)
    with pytest.raises(HydroAdmissionError, match=f"kernel sample {last}: skew part"):
        import_rigid_hydro_inputs(project, ds_path, path, _options(project))


def test_the_symmetric_part_of_that_kernel_is_admitted(tmp_path):
    project = _project()
    ds_path, _, _, kernel = _source(tmp_path, project)
    tiny, _ = _tiny_norm_skew(kernel)
    K = np.asarray(tiny.kernel)
    path = tmp_path / "tiny-sym.nc"
    anyhydro.save_radiation_kernel(dataclasses.replace(tiny, kernel=0.5 * (K + K.transpose(0, 2, 1))), path)
    inputs, _ = import_rigid_hydro_inputs(project, ds_path, path, _options(project))
    assert _jsonable_value(inputs.provenance.details)["skew_ratios"]["kernel_max"] <= 1e-12


def test_producer_symmetrised_kernel_is_admitted_with_zero_kernel_skew(tmp_path):
    project = _project()
    src = _source(tmp_path, project, damping_skew=1e-4, symmetry_tol=1.0, kernel_options=SYM, tag="p")
    kernel = src[3]
    assert kernel.settings.kernel_symmetrisation == "symmetric_part"
    assert kernel.provenance["kernel_symmetrisation"]["pairwise_skew_over_peak"] > 0.0
    inputs, _ = import_rigid_hydro_inputs(project, src[0], src[1], _options(project))
    assert _jsonable_value(inputs.provenance.details)["skew_ratios"]["kernel_max"] <= 1e-12
