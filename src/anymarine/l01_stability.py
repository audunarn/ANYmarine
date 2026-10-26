"""Pure L01 loading recipe; display meshes are never promoted to solver geometry.

The four tanks and dry ledger are assumptions, not particulars of OrcaWave L01.
Loading performs no geometry queries, equilibrium solves or provider construction.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path

from .reference_comparison import validate as validate_reference, ReferenceComparisonError


class L01StabilityError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f'{code}: {message}')


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


LIMITATION = ('L01 has only a display mesh in the reference comparison. Its generated '
    'topsides and deck are not validated hydrostatic geometry. Supply a separately '
    'prepared ANYstability neutral source with HULL and WB1..WB4 snapshots, capacity '
    'metadata, and manifest l01_reference_sha256 matching this recipe. No geometry '
    'conversion or numerical calculation has been performed.')


@dataclass(frozen=True)
class L01StabilityRecipe:
    """Immutable loading assumptions and local reference identity, without private mesh."""
    _bytes: bytes

    @property
    def identity(self):
        return hashlib.sha256(self._bytes).hexdigest()

    def to_dict(self):
        return json.loads(self._bytes)

    def analytical_box_inputs(self):
        """Runnable educational 100 x 18 x 12 m BOX, never the actual L01 hull.

        Four FULL fixed-mass tanks avoid any free-surface claim. The reference
        total mass/CG is preserved with a declared +6.66m keel-frame z shift.
        Hull dimensions are declared approximations, not a geometry conversion.
        """
        data = self.to_dict()
        shift = 6.66
        density = 1025.
        mass = data['total_upright_mass_kg']
        total_cg = [*data['total_upright_cg_body_m']]
        total_cg[2] += shift
        items = []
        for tank in data['tanks']:
            bounds = tank['bounds_body_m']
            cg = [(a+b)/2 for a, b in bounds];cg[2] += shift
            provenance = dict(kind='declared_reference_full_tank_contents', shape='rectangular',
                dimensions_m=[b-a for a, b in bounds], capacity_m3=tank['capacity_m3'],
                liquid_density_kg_m3=density, fill_fraction=1., cg_model='fixed_at_geometric_center',
                free_surface_model='not_evaluated', shell_mass='included_in_lightship',
                assumption='Illustrative L01-based box tank, not an actual L01 tank')
            items.append(dict(item_id=tank['name'], mass_kg=tank['capacity_m3']*density,
                cg_body_m=cg, source=json.dumps(provenance, sort_keys=True), covers=['contents:'+tank['name']]))
        dry_mass = mass - sum(item['mass_kg'] for item in items)
        dry_cg = [(mass*total_cg[j]-sum(item['mass_kg']*item['cg_body_m'][j] for item in items))/dry_mass for j in range(3)]
        items.append(dict(item_id='assumed-dry-vessel', mass_kg=dry_mass, cg_body_m=dry_cg,
            source='Assumed decomposition preserving L01 total mass/CG; keel-frame z shift +6.66m; reference SHA256 '+data['reference']['file_sha256'],
            covers=['hull-and-equipment-excluding-tank-liquids']))
        return dict(schema_version=1,
            vessel=dict(vessel_id='l01-proportioned-analytical-box',
                name='L01-based ANALYTICAL BOX approximation — not actual L01 hull',
                geometry=dict(provider_id='reference_box_v1', definition=dict(length_m=100., breadth_m=18., height_m=12.))),
            condition=dict(condition_id='l01-box-four-full-fixed-tanks',
                name='Assumed four full tanks; fixed contents masses; no free-surface/GZ claim',
                items=items, water_density_kg_m3=density, gravity_m_s2=9.80665),
            settings=dict(force_relative_tolerance=1e-8, draft_tolerance_m=1e-6, max_queries=128))

    def engineering_study(self, neutral_source=None, *, owner_wheel=None):
        """Bind explicitly prepared geometry through the public owner, without solving.

        The caller attests that snapshots represent the actual L01 hull and these
        assumed tanks. The SHA binding prevents accidental use of recipe3/box data;
        it does not certify the source or prove tank containment.
        """
        if neutral_source is None:
            raise L01StabilityError('GEOMETRY_SOURCE_UNAVAILABLE', LIMITATION)
        data = self.to_dict()
        if not isinstance(neutral_source, dict):
            raise L01StabilityError('INPUT_INVALID', 'Pass a decoded public neutral source bundle.')
        manifest = neutral_source.get('manifest', {})
        if not isinstance(manifest, dict) or manifest.get('l01_reference_sha256') != data['reference']['file_sha256']:
            raise L01StabilityError('SOURCE_IDENTITY_MISMATCH', 'Neutral source is not bound to this exact local L01 reference.')
        from .engineering_stability import api, default_owner_wheel
        wheel = str(owner_wheel or default_owner_wheel())
        if not wheel or not Path(wheel).is_file():
            raise L01StabilityError('PROVIDER_UNAVAILABLE', 'Select an existing matching ANYgeometry owner wheel; no geometry was executed.')
        owner = api()
        from anystability.domain import LoadingCondition
        from anystability.conventions import Pose
        return owner.create_stability_study(neutral_source,
            base_condition=LoadingCondition.from_dict(data['base_condition']),
            targets_m3={t['name']: t['target_m3'] for t in data['tanks']},
            densities_kg_m3={t['name']: t['density_kg_m3'] for t in data['tanks']},
            initial_pose=Pose(0., 0., 0.), pose_bounds=data['pose_bounds'],
            owner_wheel=wheel, geometry_mode='engineering_estimate', openings=(),
            allow_development=True)


def load_l01_stability(reference_path=None):
    """Validate local L01 identity and return the four assumed tank loading recipe.

    No source mesh is copied into the recipe or installed package. SI axes follow
    the reference body frame; z is upwards. Half-full tanks have upright CG z=-5m.
    """
    configured = reference_path or os.environ.get('ANYMARINE_L01_REFERENCE_COMPARISON')
    if configured is None:
        candidate = Path(__file__).resolve().parents[3] / 'ANYhydro/examples/L01 Default vessel/anymarine-prepared/reference_comparison.json'
        configured = candidate
    path = Path(configured).absolute()
    if not path.is_file():
        raise L01StabilityError('LOCAL_DATA_UNAVAILABLE',
            'Select the licensed local L01 reference_comparison.json or set ANYMARINE_L01_REFERENCE_COMPARISON; private data are not shipped.')
    try:
        with path.open('rb') as stream:
            raw = stream.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise ReferenceComparisonError('Reference exceeds 64 MiB transport bound.')
        reference = json.loads(raw.decode('utf-8'))
        validate_reference(reference)
    except (ReferenceComparisonError, ValueError, OSError) as error:
        raise L01StabilityError('SOURCE_INVALID', str(error)) from error
    body = reference['body']
    if body['name'].strip().casefold() != 'l01 default vessel':
        raise L01StabilityError('SOURCE_IDENTITY_MISMATCH', 'Expected the L01 default vessel reference, not another vessel.')
    mass = float(body['mass_kg']); cg = body['centre_of_mass_m']
    liquid_mass = 4 * 80. * 1025.
    if mass <= liquid_mass:
        raise L01StabilityError('INPUT_INVALID', 'L01 total mass must exceed the assumed ballast mass.')
    tanks = [dict(name=f'WB{i}', bounds_body_m=[[x-5., x+5.], [y-2., y+2.], [-6., -2.]],
                  capacity_m3=160., target_m3=80., density_kg_m3=1025., upright_cg_body_m=[x, y, -5.])
             for i, (x, y) in enumerate(((-12., -4.), (-12., 4.), (12., -4.), (12., 4.)), 1)]
    dry_cg = [(mass * float(cg[j]) - sum(t['target_m3'] * t['density_kg_m3'] * t['upright_cg_body_m'][j] for t in tanks)) / (mass-liquid_mass) for j in range(3)]
    data = dict(schema='anymarine-l01-assumed-tanks-v1', name='L01 — four assumed ballast tanks',
        reference=dict(path=str(path), file_sha256=hashlib.sha256(raw).hexdigest(),
                       artifact_hash=reference['artifact_hash'], name=body['name']),
        tanks=tanks, base_condition=dict(condition_id='l01-assumed-half-ballast',
            name='Assumed dry ledger excluding all four tank liquids',
            items=[dict(item_id='assumed-dry-vessel', mass_kg=mass-liquid_mass,
                cg_body_m=dry_cg, source='Assumed decomposition of L01 reference mass/CG at upright half-full tanks',
                covers=['hull-and-equipment-excluding-tank-liquids'])],
            water_density_kg_m3=1025., gravity_m_s2=9.80665),
        total_upright_mass_kg=mass, total_upright_cg_body_m=list(cg),
        pose_bounds=[[-100., 100.], [-math.radians(80), math.radians(80)], [-math.radians(20), math.radians(20)]],
        assumptions=['Four independent rectangular tanks, 10 x 4 x 4 m; half full; seawater 1025 kg/m3.',
            'Tank positions/containment and dry ledger are illustrative assumptions, not L01 particulars.',
            'No critical-opening inventory: downflooding and regulatory compliance remain unavailable.',
            'Equilibrium/GZ use the owner actual liquid-CG workflow only after a compatible neutral source is supplied.'],
        numerical_status='not_executed', geometry_status='neutral_source_required', limitation=LIMITATION)
    return L01StabilityRecipe(_canonical(data))
