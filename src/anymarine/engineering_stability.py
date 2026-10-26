"""Qt-free bridge to public ANYstability engineering services.

Importing, constructing, reopening and reporting studies never executes geometry.
"""
from __future__ import annotations
import hashlib
import importlib
import json
import math
import os
from urllib.parse import unquote, urlparse
from pathlib import Path
from .services import ServiceError

class EngineeringIntegrationError(ServiceError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f'{code}: {message}')

def api():
    try:
        owner = importlib.import_module('anystability.services')
        required = ('workflow_defaults', 'create_stability_study', 'calculate_gz_curve',
                    'solve_floating_equilibrium', 'save_stability_study',
                    'load_stability_study', 'export_stability_report')
        if any(not callable(getattr(owner, name, None)) for name in required):
            raise ImportError('engineering stability public services are missing')
        return owner
    except ImportError as error:
        raise EngineeringIntegrationError('PROVIDER_UNAVAILABLE',
            'Install a compatible local ANYstability engineering provider. '+str(error)) from error

def open_reference_source(path):
    """Open a pinned local dataset or portable study without changing local configuration."""
    from .reference_barge import load_reference_barge
    return load_reference_barge(path,allow_local_dataset=True)

def create_from_reference(reference, *, owner_wheel, geometry_mode='engineering_estimate', openings=(), pose_bounds=None):
    """Preserve selected dry ledger, eight liquid targets/densities and neutral source."""
    from .reference_barge import reference_barge_view
    from anystability.domain import LoadingCondition
    from anystability.conventions import Pose
    view = reference_barge_view(reference)
    records = json.loads(reference._records)
    spec = reference.to_dict().get('request') or records['coupled_request']
    pose = view['pose'] or spec['initial_pose']
    frozen=dict(reference._files)
    envelope=json.loads(frozen['model/manifest.json'])
    neutral={'manifest':envelope.get('manifest',envelope),
        'files':{name.split('/',1)[1]:json.loads(raw) for name,raw in reference._files
            if name.startswith('model/') and name.endswith('.json') and name!='model/manifest.json'}}
    return api().create_stability_study(neutral,
        base_condition=LoadingCondition.from_dict(view['base_condition']),
        targets_m3=view['targets_m3'], densities_kg_m3=view['densities_kg_m3'],
        initial_pose=Pose(pose['heel_rad'], pose['trim_rad'], pose['origin_z_m']),
        pose_bounds=pose_bounds or engineering_pose_bounds(reference), owner_wheel=owner_wheel,
        geometry_mode=geometry_mode, openings=openings, allow_development=True)

def engineering_pose_bounds(reference):
    """Conservative rotation-invariant support box from authored hull coordinates."""
    files=dict(reference._files)
    definition=json.loads(files['model/definition.json'])
    vertices=[point for face in definition['hull']['faces_body_m'] for point in face]
    radius=max(math.sqrt(sum(float(x)**2 for x in point)) for point in vertices)
    if not math.isfinite(radius) or radius<=0:raise ValueError('authored hull has no finite extent')
    return [[-radius,radius],[math.radians(-80),math.radians(80)],
            [math.radians(-20),math.radians(20)]]

def with_engineering_inputs(study, *, owner_wheel, geometry_mode, openings=(), pose_bounds=None):
    """Create new immutable inputs from retained neutral source without owner execution."""
    from anystability.domain import LoadingCondition
    from anystability.conventions import Pose
    data=study.to_dict()
    return api().create_stability_study(data['source'],
        base_condition=LoadingCondition.from_dict(data['base_condition']),
        targets_m3=data['targets_m3'],densities_kg_m3=data['densities_kg_m3'],
        initial_pose=Pose(**data['initial_pose']),pose_bounds=pose_bounds or data['pose_bounds'],
        owner_wheel=owner_wheel,geometry_mode=geometry_mode,openings=openings,allow_development=True)

def default_owner_wheel():
    """Metadata-only local provenance lookup, without importing the geometry owner."""
    candidates = [os.environ.get('ANYSTABILITY_OWNER_WHEEL', '')]
    try:
        from importlib.metadata import distribution
        direct = distribution('ANYgeometry').read_text('direct_url.json')
        url = json.loads(direct or '{}').get('url', '')
        parsed = urlparse(url)
        if parsed.scheme == 'file' and parsed.netloc in ('', 'localhost'):
            path = unquote(parsed.path)
            if os.name == 'nt' and len(path)>2 and path[0]=='/' and path[2]==':':path=path[1:]
            candidates.append(path)
    except (ImportError, OSError, ValueError):
        pass
    return next((str(Path(p).absolute()) for p in candidates if p and Path(p).is_file()), '')

def request_identity(study, options, seed_identity=None):
    body = {'study_identity':study.identity, 'options':options, 'seed_identity':seed_identity}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def script_for_reference(reference, options, *, owner_wheel, geometry_mode, run_path, openings=(), pose_bounds=None):
    """Record public calls and local references, never embed neutral/private model data."""
    source = str(reference._root.absolute())
    return ('from anymarine.services import ApplicationService\n'
        'service = globals().get("service") or ApplicationService()\n'
        'from anymarine.engineering_stability import create_from_reference, open_reference_source\n'
        'from anymarine.engineering_stability_run import EngineeringRun\n'
        'from anystability import services as stability\n'
        f'barge_study = open_reference_source({source!r})\n'
        f'barge_study = service.select_reference_barge_case(barge_study, {reference.selected_case!r})\n'
        f'engineering_study = create_from_reference(barge_study, owner_wheel={str(owner_wheel)!r}, geometry_mode={geometry_mode!r}, openings={list(openings)!r}, pose_bounds={pose_bounds!r})\n'
        f'engineering_run = EngineeringRun(engineering_study, {str(run_path)!r}, options={options!r})\n')
