"""Explicit regeneration of the bundled development sea state, no BEM/solve."""
from .native_inputs import NativeMechanicalInputs


def replace_development_sea_state(project, *, significant_height, peak_period, seed):
    from anyloads import JonswapSpectrum,WaveRealization,DirectionalSpectrum
    from anyhydro import build_excitation_series,UNIT_INCIDENT_AMPLITUDE,dataset_content_hash
    from anyhydro.fixtures import make_two_body_fixture
    if project.native_inputs is None or project.native_inputs.wave_realization is None:
        raise ValueError('load the coupled seeded-wave development example first')
    data=project.native_inputs.to_dict()
    source=make_two_body_fixture()
    current=data['potential_flow']['excitation_series']
    if (tuple(b.body_id for b in project.bodies)!=tuple(str(b) for b in source.body.values)
            or current['hydro_content_hash']!=dataset_content_hash(source)):
        raise ValueError('this editor requires the exact bundled development excitation source')
    previous=project.native_inputs.wave_realization.data
    waves=WaveRealization.from_jonswap(JonswapSpectrum(significant_height,peak_period),
        previous['spectral_bins']['omegas'],previous['spectral_bins']['bandwidths'],seed=seed,
        directions=DirectionalSpectrum(**dict(previous['directions'])))
    series=build_excitation_series(source,waves,amplitude_normalization=UNIT_INCIDENT_AMPLITUDE,phase_origin_xy=(0.,0.))
    data['wave_realization']=waves.to_dict()
    data['potential_flow']['excitation_series']=series.to_dict()
    data['morison']['environment']['components']=waves.to_dict()['components']
    if project.native_inputs.mooring is not None and project.native_inputs.mooring.has_hydrodynamics:
        data['mooring']['data']['environment']['components']=waves.to_dict()['components']
    return project.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))
