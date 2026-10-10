"""A stored actual diffraction example; loading never imports or runs Capytaine."""
from pathlib import Path
import json
from .native_jobs import project_from_snapshot
from .hashing import input_hash

_BUNDLE=Path(__file__).parent/'examples/calculated-wave-preview'

def calculated_wave_project():
    """Pure stored project load; numerical/Qt/viewer packages remain optional."""
    return project_from_snapshot(json.loads((_BUNDLE/'project-snapshot.json').read_text(encoding='utf-8')))

def load_calculated_wave_example():
    """Validate sealed visualization arrays against their stored producing project."""
    from .wave_jobs import load_wave_output
    project=calculated_wave_project()
    snapshot=json.loads((_BUNDLE/'input.json').read_text(encoding='utf-8'))
    provenance=json.loads((_BUNDLE/'provenance.json').read_text(encoding='utf-8'))
    if (provenance.get('api_version')!='calculated_wave_example_v1'
            or provenance.get('status')!='development_unqualified'
            or provenance.get('actual_calculation') is not True):
        raise ValueError('unsupported calculated-wave example provenance')
    field=load_wave_output(_BUNDLE/'output.json',snapshot,provenance.get('output_sha256'))
    if input_hash(project)!=field['request']['project_input_hash']:
        raise ValueError('calculated-wave field does not belong to its stored project')
    if (field['panels']!=provenance.get('panels')
            or len(field['vertices'])!=provenance.get('surface_nodes')
            or field['capytaine_version']!=provenance.get('capytaine_version')):
        raise ValueError('calculated-wave example provenance differs from its field')
    return project,field
