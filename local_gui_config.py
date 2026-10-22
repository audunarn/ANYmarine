"""Bounded checkout-local launcher configuration; no Qt or optional owner imports."""
import json
from pathlib import Path

MAX_CONFIG_BYTES = 16384


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate local configuration field: ' + key)
        result[key] = value
    return result


def _path(value, field, *, directory=False, effective=True):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError(field + ' must be a nonempty absolute path')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError(field + ' must be an absolute path without traversal')
    if effective and any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in (path, *path.parents)):
        raise ValueError(field + ' must not traverse symlinks/junctions')
    if effective and not (path.is_dir() if directory else path.is_file()):
        raise ValueError(field + ' does not name an existing ' + ('directory' if directory else 'file'))
    return str(path)


def local_settings(root, environ):
    """Validate present configuration; explicit environment overrides each value."""
    config = Path(root) / '.anymarine-local.json'
    values = {}
    if config.exists() or config.is_symlink():
        _path(str(config.absolute()), 'local configuration')
        with config.open('rb') as stream:
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError('local configuration exceeds 16KiB')
        try:
            values = json.loads(raw, object_pairs_hook=_object)
        except RecursionError as error:
            raise ValueError('local configuration nesting is excessive') from error
        if type(values) is not dict or set(values) != {'schema_version', 'python', 'reference_barge_path'}:
            raise ValueError('local configuration requires schema_version, python, reference_barge_path only')
        if type(values['schema_version']) is not int or values['schema_version'] != 1:
            raise ValueError('unsupported local configuration schema_version')
        # Structure/path syntax is always checked; only effective paths must exist.
        _path(values['python'], 'python', effective=False)
        _path(values['reference_barge_path'], 'reference_barge_path', directory=True, effective=False)
    python = environ.get('ANYMARINE_PYTHON', values.get('python'))
    assets = environ.get('ANYMARINE_REFERENCE_BARGE_DIR', values.get('reference_barge_path'))
    return (None if python is None else _path(python, 'ANYMARINE_PYTHON'),
            None if assets is None else _path(assets, 'ANYMARINE_REFERENCE_BARGE_DIR', directory=True))
