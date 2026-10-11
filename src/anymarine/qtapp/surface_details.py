"""Qt surface-assignment editor: a consumer of public frozen records.

No ANYhydro import at module level: the neutral package is reached only
through ``anymarine.surface_assignments.neutral_api()`` when an explicit
surface action needs the public contract, so default Qt startup never
imports it. All validation is delegated to the public record constructors
(``SurfaceSemantics``, ``PlateDragCorrection``, ``HydroSurfaceAssignment``,
``Body``, ``ProjectModel``); this module never invents surface fields,
qualifications or force laws.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..domain import Body, BodyMesh, ProjectModel

ROLE_KINDS = {'hull': 'body_boundary', 'lid': 'numerical_lid', 'attached': 'thin_sheet'}
PRIMARY_OPTIONS = ('potential_flow', 'morison', 'none')
CORRECTION_OPTIONS = ('none', 'member_drag', 'plate_drag')


def dataset_status_text(project: Optional[ProjectModel], dataset_metadata=None) -> str:
    """Actual bound dataset metadata via public admission, never invention.

    ``dataset_metadata`` comes from the public read-only service accessor:
    only what the exact content-addressed producing dataset sidecar stored
    is displayed and absent attributes are stated as unavailable, never
    fabricated. The labels are producing-dataset development evidence, not
    a qualification of the currently edited geometry, and the source
    capability label is distinct from scientific qualification.
    """
    provenance = getattr(project, 'hydro_provenance', None) if project is not None else None
    if provenance is None:
        return ('No bound ANYhydro dataset: sheet source, capability and '
                'pressure-jump status are unavailable and not claimed (unsupported).')
    text = (f'Producing dataset (development evidence, not current geometry '
            f'qualification): backend {provenance.source_backend}, schema '
            f'{provenance.source_schema_version}, qualification '
            f'"{provenance.source_qualification_status}" (development label, '
            'not scientific sheet qualification).')
    if dataset_metadata is None or not dataset_metadata.available:
        reason = (getattr(dataset_metadata, 'diagnostic', '')
                  if dataset_metadata is not None else '')
        text += (' Stored pressure-jump convention and sheet source capability '
                 'status are unavailable and not claimed.')
        if reason:
            text += f' Reason: {reason}.'
        return text
    convention = dataset_metadata.pressure_jump_convention
    backend = dataset_metadata.sheet_source_backend
    status = dataset_metadata.source_capability_status
    text += (' Stored pressure-jump convention: '
             + (convention if convention
                else 'unavailable (not stored in the producing dataset)') + '.')
    text += (' Sheet source backend: '
             + (backend if backend
                else 'unavailable (not stored in the producing dataset)') + '.')
    text += (' Sheet source capability status: '
             + (status if status
                else 'unavailable (not stored in the producing dataset)')
             + ' (source capability label of the producing dataset, not '
             'scientific qualification).')
    contract = dataset_metadata.pressure_jump_contract_sign
    if contract:
        text += f' Stored pressure-jump contract sign: {contract}.'
    else:
        text += (' Optional pressure-jump contract: unavailable (not stored '
                 'in the producing dataset).')
    return text


def edited_assignment(assignment, changes: dict):
    """Rebuild one owned assignment from explicit GUI changes.

    Identity (surface_id, owning_body_id), role and mesh are locked; only
    the contract-permitted fields move. The public constructors revalidate
    everything; ``HydroError`` becomes ``ValueError`` for the shell handlers.
    """
    from dataclasses import replace
    from ..surface_assignments import HydroSurfaceAssignment, neutral_api
    h = neutral_api()
    semantics = assignment.semantics
    correction = changes.get('viscous_correction', semantics.viscous_correction)
    try:
        plate_drag = None
        if correction == 'plate_drag':
            area_cd = changes.get('plate_drag')
            if area_cd is not None:
                plate_drag = h.PlateDragCorrection(reference_area_m2=area_cd[0],
                                                   coefficient=area_cd[1])
            else:
                plate_drag = semantics.plate_drag
        new_semantics = replace(
            semantics,
            primary_hydrodynamics=changes.get('primary_hydrodynamics', semantics.primary_hydrodynamics),
            viscous_correction=correction,
            source_geometry_id=changes.get('source_geometry_id', semantics.source_geometry_id),
            minus_side=changes.get('minus_side', semantics.minus_side),
            plus_side=changes.get('plus_side', semantics.plus_side),
            plate_drag=plate_drag,
        )
        return HydroSurfaceAssignment(assignment.role, new_semantics, assignment.mesh)
    except h.HydroError as error:
        raise ValueError(str(error)) from error


def new_assignment(body: Body, spec: dict):
    """Build one new owned assignment from an explicit creation spec.

    The physical kind is derived from the explicit role (never from mesh
    openness); the public constructors refuse every unsupported
    kind/role/correction combination. Attached sheets require an explicit
    body-local BodyMesh; hull/lid assignments refer to existing geometry.
    """
    from ..surface_assignments import HydroSurfaceAssignment, neutral_api
    h = neutral_api()
    role = spec['role']
    if role not in ROLE_KINDS:
        raise ValueError('surface role must be hull, lid or attached')
    correction = spec['viscous_correction']
    plate_drag = None
    try:
        if correction == 'plate_drag':
            area, coefficient = spec['plate_drag']
            plate_drag = h.PlateDragCorrection(reference_area_m2=area, coefficient=coefficient)
        semantics = h.SurfaceSemantics(
            surface_id=spec['surface_id'],
            owning_body_id=body.body_id,
            surface_kind=ROLE_KINDS[role],
            primary_hydrodynamics=spec['primary_hydrodynamics'],
            source_geometry_id=spec['source_geometry_id'],
            viscous_correction=correction,
            minus_side=spec.get('minus_side'),
            plus_side=spec.get('plus_side'),
            plate_drag=plate_drag,
        )
        return HydroSurfaceAssignment(role, semantics, spec.get('mesh'))
    except h.HydroError as error:
        raise ValueError(str(error)) from error


class _PositiveDecimalField(QLineEdit):
    """Exact decimal text field for the positive plate-drag numbers.

    A ranged spin box would clamp and round valid stored magnitudes on
    display; this field shows the shortest round-trip representation, so
    unedited values survive a show/apply cycle byte-exactly and valid
    small values are supported. The public record constructors remain the
    validation authority: text that does not parse as a number is passed
    through unchanged and refused by them with a clear message.
    """

    def setValue(self, value: float) -> None:
        self.setText(repr(float(value)))

    def value(self):
        text = self.text().strip()
        try:
            return float(text)
        except ValueError:
            return text


def _positive_field(value: float) -> _PositiveDecimalField:
    field = _PositiveDecimalField()
    field.setValue(value)
    return field


class SurfaceForm(QWidget):
    """Details page for one owned surface assignment.

    Identity, owner, role, physical kind and the derived normal convention
    are read-only; primary treatment, viscous correction, distinct sheet
    side labels, source geometry id and the optional explicit plate-drag
    declaration are editable and applied atomically by the shell.
    """

    surface_edited = Signal(str, str, dict)
    surface_remove_requested = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        box = QGroupBox('Surface assignment')
        form = QFormLayout(box)
        self._identity = QLabel('-')
        self._identity.setWordWrap(True)
        self._normal = QLabel('-')
        self._dataset = QLabel('-')
        self._dataset.setWordWrap(True)
        self._primary = QComboBox()
        self._primary.addItems(list(PRIMARY_OPTIONS))
        self._correction = QComboBox()
        self._correction.addItems(list(CORRECTION_OPTIONS))
        self._correction.currentTextChanged.connect(self._sync_enabled)
        self._source_geometry_id = QLineEdit()
        self._minus_side = QLineEdit()
        self._plus_side = QLineEdit()
        self._plate_area = _positive_field(1.0)
        self._plate_cd = _positive_field(1.0)
        form.addRow('Surface', self._identity)
        form.addRow('Normal convention (derived)', self._normal)
        form.addRow('Primary hydrodynamics', self._primary)
        form.addRow('Viscous correction', self._correction)
        form.addRow('Source geometry id', self._source_geometry_id)
        form.addRow('Minus side label', self._minus_side)
        form.addRow('Plus side label', self._plus_side)
        form.addRow('Plate drag reference area [m2]', self._plate_area)
        form.addRow('Plate drag coefficient Cd [-]', self._plate_cd)
        form.addRow('Producing dataset', self._dataset)
        buttons = QHBoxLayout()
        self._apply = QPushButton('Apply surface edit')
        self._apply.clicked.connect(self._emit_edit)
        self._remove = QPushButton('Remove assignment')
        self._remove.clicked.connect(self._emit_remove)
        buttons.addWidget(self._apply)
        buttons.addWidget(self._remove)
        form.addRow(buttons)
        layout.addWidget(box)
        layout.addStretch(1)
        self._body_id: Optional[str] = None
        self._surface_id: Optional[str] = None
        self._kind: str = ''

    def populate(self, body_name: str, assignment, project: Optional[ProjectModel],
                 dataset_metadata=None) -> None:
        semantics = assignment.semantics
        self._body_id = semantics.owning_body_id
        self._surface_id = semantics.surface_id
        self._kind = semantics.surface_kind
        self._identity.setText(
            f'{semantics.surface_id} — role {assignment.role}, kind {semantics.surface_kind}, '
            f'owner {body_name} ({semantics.owning_body_id})')
        self._normal.setText(semantics.normal_convention)
        self._primary.setCurrentText(semantics.primary_hydrodynamics)
        self._correction.setCurrentText(semantics.viscous_correction)
        self._source_geometry_id.setText(semantics.source_geometry_id)
        self._minus_side.setText(semantics.minus_side or '')
        self._plus_side.setText(semantics.plus_side or '')
        plate = semantics.plate_drag
        if plate is not None:
            self._plate_area.setValue(plate.reference_area_m2)
            self._plate_cd.setValue(plate.coefficient)
        self._dataset.setText(dataset_status_text(project, dataset_metadata))
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        sheet = self._kind == 'thin_sheet'
        plate = self._correction.currentText() == 'plate_drag'
        self._minus_side.setEnabled(sheet)
        self._plus_side.setEnabled(sheet)
        self._plate_area.setEnabled(plate)
        self._plate_cd.setEnabled(plate)

    def _emit_edit(self) -> None:
        if self._body_id is None or self._surface_id is None:
            return
        sheet = self._kind == 'thin_sheet'
        plate = self._correction.currentText() == 'plate_drag'
        self.surface_edited.emit(self._body_id, self._surface_id, {
            'primary_hydrodynamics': self._primary.currentText(),
            'viscous_correction': self._correction.currentText(),
            'source_geometry_id': self._source_geometry_id.text().strip(),
            'minus_side': self._minus_side.text().strip() if sheet else None,
            'plus_side': self._plus_side.text().strip() if sheet else None,
            'plate_drag': (self._plate_area.value(), self._plate_cd.value()) if plate else None,
        })

    def _emit_remove(self) -> None:
        if self._body_id is None or self._surface_id is None:
            return
        self.surface_remove_requested.emit(self._body_id, self._surface_id)


class SurfaceAssignmentDialog(QDialog):
    """Explicit creation of one owned surface assignment for one body.

    The physical kind follows the explicit role; an attached thin sheet
    requires a user-selected body-local mesh JSON file, never inference
    from an existing shell or open mesh.
    """

    def __init__(self, body: Body, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._body = body
        self.setWindowTitle('Add hydrodynamic surface assignment')
        layout = QVBoxLayout(self)
        intro = QLabel(
            'Create one owned surface assignment with an explicit role (physical kind), '
            'primary treatment and geometry reference. Hull and lid assignments refer to '
            'existing body geometry; an attached thin sheet requires an explicit '
            'body-local mesh JSON file. Development intent declaration, not a qualified '
            'sheet treatment.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self._role = QComboBox()
        self._role.addItems([
            'hull — body_boundary (existing body mesh)',
            'lid — numerical_lid (existing lid geometry)',
            'attached — thin_sheet (explicit body-local mesh)',
        ])
        self._role.setCurrentIndex(0)
        self._role.currentIndexChanged.connect(self._sync_role)
        self._surface_id = QLineEdit()
        self._source_geometry_id = QLineEdit()
        self._primary = QComboBox()
        self._primary.addItems(list(PRIMARY_OPTIONS))
        self._correction = QComboBox()
        self._correction.addItems(list(CORRECTION_OPTIONS))
        self._correction.currentTextChanged.connect(self._sync_correction)
        self._minus_side = QLineEdit()
        self._plus_side = QLineEdit()
        self._plate_area = _positive_field(1.0)
        self._plate_cd = _positive_field(1.0)
        self._mesh_path = QLineEdit()
        self._mesh_path.setPlaceholderText('Required for attached thin sheets')
        browse = QPushButton('Browse…')
        browse.clicked.connect(self._browse_mesh)
        mesh_row = QHBoxLayout()
        mesh_row.addWidget(self._mesh_path)
        mesh_row.addWidget(browse)
        form.addRow('Body', QLabel(f'{body.name} ({body.body_id})'))
        form.addRow('Role / physical kind', self._role)
        form.addRow('Surface id', self._surface_id)
        form.addRow('Source geometry id', self._source_geometry_id)
        form.addRow('Primary hydrodynamics', self._primary)
        form.addRow('Viscous correction', self._correction)
        form.addRow('Minus side label', self._minus_side)
        form.addRow('Plus side label', self._plus_side)
        form.addRow('Plate drag reference area [m2]', self._plate_area)
        form.addRow('Plate drag coefficient Cd [-]', self._plate_cd)
        form.addRow('Body-local mesh JSON', mesh_row)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Add assignment')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._sync_role()

    def _selected_role(self) -> str:
        return ('hull', 'lid', 'attached')[self._role.currentIndex()]

    def _sync_role(self) -> None:
        role = self._selected_role()
        suggestion = {'hull': 'hull', 'lid': 'lid', 'attached': 'sheet'}[role]
        self._surface_id.setText(f'{self._body.body_id}/{suggestion}')
        self._source_geometry_id.setText(f'{self._body.body_id}/{suggestion}-geometry')
        lid = role == 'lid'
        # A numerical lid is never a physical plate or load owner.
        self._primary.setCurrentText('none' if lid else 'potential_flow')
        self._primary.setEnabled(not lid)
        self._correction.setCurrentText('none' if lid else 'none')
        self._correction.setEnabled(not lid)
        sheet = role == 'attached'
        self._minus_side.setEnabled(sheet)
        self._plus_side.setEnabled(sheet)
        self._mesh_path.setEnabled(sheet)

    def _sync_correction(self) -> None:
        plate = self._correction.currentText() == 'plate_drag'
        self._plate_area.setEnabled(plate)
        self._plate_cd.setEnabled(plate)

    def _browse_mesh(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, 'Body-local sheet mesh JSON', '',
                                              'Mesh JSON (*.json)')
        if path:
            self._mesh_path.setText(path)

    def spec(self) -> dict:
        role = self._selected_role()
        sheet = role == 'attached'
        plate = self._correction.currentText() == 'plate_drag'
        mesh = None
        if sheet:
            data = json.loads(Path(self._mesh_path.text().strip()).read_text(encoding='utf-8'))
            mesh = BodyMesh.from_dict(data)
        return {
            'role': role,
            'surface_id': self._surface_id.text().strip(),
            'source_geometry_id': self._source_geometry_id.text().strip(),
            'primary_hydrodynamics': self._primary.currentText(),
            'viscous_correction': self._correction.currentText(),
            'minus_side': self._minus_side.text().strip() if sheet else None,
            'plus_side': self._plus_side.text().strip() if sheet else None,
            'plate_drag': (self._plate_area.value(), self._plate_cd.value()) if plate else None,
            'mesh': mesh,
        }
