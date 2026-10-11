"""Qt authoring widgets for declared native Morison members and environment.

These dialogs edit explicit input records only. Endpoint bodies are restricted
to the declared project bodies and attachment points are explicit body-local
coordinates; ownership and section choices are the public owner-record
options. The shared ApplicationService.load_native_inputs admission through
the public compiler remains the only physical validator: incompatible
ownership, invalid geometry or missing owner packages are refused there,
never silently repaired here.
"""
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .theme import apply_theme

# Public owner-record options (ANYloads HydrodynamicOwnership) and the public
# solver quadrature bounds (anysolver MorisonMember). The shared compiler
# re-checks every value; these lists only offer admissible user choices.
PRIMARY_ROLES = ('morison_full', 'potential_flow', 'none')
BUOYANCY_OWNERS = ('none', 'potential_flow', 'morison')
QUADRATURE_MIN, QUADRATURE_MAX = 2, 16


class _ExactFloatField(QLineEdit):
    """Exact decimal text field for explicit endpoint/section numbers.

    A ranged spin box with fixed decimals rounds and clamps valid stored
    values on display, so an unedited record no longer round-trips and
    explicitly declared small or large valid values are silently re-ranged.
    This field shows the shortest round-trip representation instead, so
    unedited values survive byte-exactly. The public compiler remains the
    physical validation authority: text that does not parse as a number is
    returned unchanged and refused by the dialog with an actionable
    message; positivity and sign criteria are never duplicated here.
    """

    def setValue(self, value):
        self.setText(repr(float(value)))

    def value(self):
        text = self.text().strip()
        try:
            return float(text)
        except ValueError:
            return text


def development_environment_template():
    """Clearly labelled synthetic development environment template.

    Values mirror the public native Morison example record. This is editable
    declared input data, not a result, a source or a qualification; the user
    reviews and edits it in the numeric tree before admission.
    """
    return dict(api_version='marine_loads_v1', water_depth=10., density=1025.,
        gravity=9.81, still_water_level=0., acceleration_convention='eulerian_local',
        components=[dict(amplitude=.1, omega=2., direction_degrees=0., phase=0.)],
        current=dict(depths=[0., 1., 10.],
            velocities=[[.15, 0., 0.], [.1, 0., 0.], [.05, 0., 0.]]))


class _EndpointEditor(QWidget):
    """Explicit attachment: one declared body identity plus body-local point."""

    def __init__(self, bodies, point, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.body = QComboBox(self)
        for body_id, name in bodies:
            self.body.addItem(f'{body_id} — {name}', body_id)
        layout.addRow('Body identity', self.body)
        self.coordinates = []
        for axis, label in enumerate(('x [m]', 'y [m]', 'z [m]')):
            field = _ExactFloatField(self)
            field.setValue(float(point[axis]))
            self.coordinates.append(field)
            layout.addRow('Point ' + label, field)

    def endpoint(self):
        return dict(body_id=self.body.currentData(),
            point_body=[spin.value() for spin in self.coordinates])


class MorisonMemberDialog(QDialog):
    """Edit one member's identity, attachments, section, ownership, quadrature.

    Client-side checks cover record shape only (non-empty unique member
    identity, distinct endpoints, parseable explicit numbers). Physical
    ownership compatibility, section positivity and sign criteria, and
    body declaration are owned by the shared compiler at admission; this
    dialog never duplicates that validation.
    """
    def __init__(self, bodies, existing_ids=(), member=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Morison member — development, unqualified')
        self.setMinimumWidth(480)
        apply_theme(self)
        self._existing = set(existing_ids)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.identity = QLineEdit(self)
        if member is not None:
            self.identity.setText(str(member['member_id']))
        form.addRow('Member identity', self.identity)
        default_point = [0., 0., 0.]
        self.end_a = _EndpointEditor(bodies,
            member['end_a']['point_body'] if member is not None else default_point, self)
        self.end_b = _EndpointEditor(bodies,
            member['end_b']['point_body'] if member is not None else default_point, self)
        if member is not None:
            for editor, key in ((self.end_a, 'end_a'), (self.end_b, 'end_b')):
                index = editor.body.findData(member[key]['body_id'])
                editor.body.setCurrentIndex(max(0, index))
        form.addRow('Endpoint A', self.end_a)
        form.addRow('Endpoint B', self.end_b)
        section = dict(diameter=.06, drag_coefficient=1., added_mass_coefficient=1.)
        if member is not None:
            section = {key: float(member['section'][key]) for key in section}
        self.diameter = _ExactFloatField(self)
        self.diameter.setValue(section['diameter'])
        form.addRow('Nominal diameter [m]', self.diameter)
        self.drag_coefficient = _ExactFloatField(self)
        self.drag_coefficient.setValue(section['drag_coefficient'])
        form.addRow('Drag coefficient', self.drag_coefficient)
        self.added_mass_coefficient = _ExactFloatField(self)
        self.added_mass_coefficient.setValue(section['added_mass_coefficient'])
        form.addRow('Added mass coefficient', self.added_mass_coefficient)
        self.quadrature_order = QSpinBox(self)
        self.quadrature_order.setRange(QUADRATURE_MIN, QUADRATURE_MAX)
        self.quadrature_order.setValue(int(member['quadrature_order']) if member is not None else 4)
        form.addRow('Gauss points per wet/profile interval', self.quadrature_order)
        ownership = dict(primary_role='morison_full', bem_included=False,
            drag_correction=False, buoyancy_owner='none')
        if member is not None:
            ownership = {key: member['ownership'][key] for key in ownership}
        self.primary_role = QComboBox(self)
        self.primary_role.addItems(PRIMARY_ROLES)
        self.primary_role.setCurrentText(str(ownership['primary_role']))
        form.addRow('Primary force owner', self.primary_role)
        self.bem_included = QCheckBox('Included in BEM', self)
        self.bem_included.setChecked(bool(ownership['bem_included']))
        form.addRow('', self.bem_included)
        self.drag_correction = QCheckBox('Optional drag correction', self)
        self.drag_correction.setChecked(bool(ownership['drag_correction']))
        form.addRow('', self.drag_correction)
        self.buoyancy_owner = QComboBox(self)
        self.buoyancy_owner.addItems(BUOYANCY_OWNERS)
        self.buoyancy_owner.setCurrentText(str(ownership['buoyancy_owner']))
        form.addRow('Buoyancy owner', self.buoyancy_owner)
        layout.addLayout(form)
        self.message = QLabel('', self)
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        layout.addWidget(self.message)
        note = QLabel('Ownership choices are the public owner-record options; the shared compiler '
                      'refuses incompatible combinations at admission. Attachments are explicit '
                      'body-local coordinates, never inferred from mesh.')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refuse(self, text):
        self.message.setText(text)
        self.message.setVisible(True)

    def _number_fields(self):
        for editor, label in ((self.end_a, 'Endpoint A'), (self.end_b, 'Endpoint B')):
            for axis, field in enumerate(editor.coordinates):
                yield f'{label} point {("x", "y", "z")[axis]}', field
        yield 'Nominal diameter', self.diameter
        yield 'Drag coefficient', self.drag_coefficient
        yield 'Added mass coefficient', self.added_mass_coefficient

    def accept(self):
        identity = self.identity.text().strip()
        if not identity:
            self._refuse('Member identity must be a non-empty string.')
            return
        if identity in self._existing:
            self._refuse(f'Member identity {identity!r} is already declared; '
                         'member identities must be unique.')
            return
        for label, field in self._number_fields():
            if isinstance(field.value(), str):
                self._refuse(f'{label} value {field.text().strip()!r} is not a valid '
                             'number. Enter an explicit decimal number; physical '
                             'positivity and sign criteria are checked by the '
                             'shared compiler at admission.')
                return
        if self.end_a.endpoint() == self.end_b.endpoint():
            self._refuse('Both endpoints attach to the same body at the same point; '
                         'the member would have zero current length.')
            return
        super().accept()

    def member(self):
        return dict(member_id=self.identity.text().strip(),
            end_a=self.end_a.endpoint(), end_b=self.end_b.endpoint(),
            section=dict(diameter=self.diameter.value(),
                drag_coefficient=self.drag_coefficient.value(),
                added_mass_coefficient=self.added_mass_coefficient.value()),
            ownership=dict(primary_role=self.primary_role.currentText(),
                bem_included=self.bem_included.isChecked(),
                drag_correction=self.drag_correction.isChecked(),
                buoyancy_owner=self.buoyancy_owner.currentText()),
            quadrature_order=self.quadrature_order.value())
