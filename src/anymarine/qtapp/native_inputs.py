"""Qt model/view editor for explicitly declared native mechanical inputs."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox,
    QTreeView, QVBoxLayout)

from ..native_jobs import MAX_NATIVE_FRAMES
from .theme import apply_theme

LABELS = {'morison': 'Morison members and mechanical environment',
    'environment': 'Prescribed wave/current environment', 'members': 'Straight members',
    'member_id': 'Member identity', 'end_a': 'Endpoint A', 'end_b': 'Endpoint B',
    'section': 'Circular section', 'diameter': 'Nominal diameter [m]',
    'drag_coefficient': 'Drag coefficient', 'added_mass_coefficient': 'Added mass coefficient',
    'ownership': 'Hydrodynamic ownership', 'primary_role': 'Primary force owner',
    'bem_included': 'Included in BEM', 'drag_correction': 'Optional drag correction',
    'buoyancy_owner': 'Buoyancy owner', 'quadrature_order': 'Gauss points per wet/profile interval',
    'water_depth': 'Water depth [m]', 'still_water_level': 'Still water level WORLD z [m]',
    'density': 'Fluid density [kg/m³]', 'gravity': 'Gravity magnitude [m/s²]',
    'components': 'Airy wave components', 'amplitude': 'Wave amplitude [m]',
    'direction_degrees': 'Propagation heading [deg]', 'phase': 'Phase [rad]',
    'current': 'Prescribed current profile', 'depths': 'Depth below still water [m]',
    'velocities': 'WORLD current velocities [m/s]', 'acceleration_convention': 'Fluid acceleration convention',
    'run_spec': 'Integration', 'dt': 'Time step [s]', 'steps': 'Number of steps',
    'residual_tolerance': 'Residual tolerance (force / 1 N, moment / 1 Nm)',
    'max_newton_iterations': 'Maximum Newton iterations', 'line_search': 'Residual line search',
    'initial_states': 'Initial body states', 'position': 'Origin position [m]',
    'rotation': 'BODY-to-WORLD rotation matrix', 'velocity_world': 'WORLD velocity [m/s, rad/s]',
    'links': 'Mechanical links', 'link_id': 'Link identity', 'body_a': 'Body A identity',
    'body_b': 'Body B identity', 'rho_a': 'Body A attachment [m]', 'rho_b': 'Body B attachment [m]',
    'axes_a': 'Attachment A axes', 'axes_b': 'Attachment B axes',
    'rest_translation': 'Rest translation in attachment A axes [m]',
    'rest_rotation': 'Rest relative rotation matrix', 'stiffness': 'Stiffness [SI; force/moment versus m/rad]',
    'damping': 'Damping [SI; force/moment versus m/s and rad/s]',
    'preload': 'Preload [N, Nm]', 'gravity_world': 'Selected WORLD gravity [m/s²]',
    'world_wrenches': 'Declared WORLD wrenches', 'contribution_id': 'Load identity',
    'body_id': 'Body identity', 'point_body': 'Application point in body axes [m]',
    'force_world': 'WORLD force [N]', 'moment_world': 'WORLD moment [Nm]', 'version': 'Input contract',
    'potential_flow':'Coupled potential-flow development fixture',
    'frames':'Equilibrium frames', 'equilibrium_position':'Equilibrium origin [m]',
    'equilibrium_rotation':'Equilibrium BODY-to-WORLD rotation', 'coefficient_axes':'Coefficient axes',
    'reference_point_body':'Material reference point [m]', 'motion_bounds':'Translation/rotation bounds [m, rad]',
    'kernel':'Causal radiation kernel [SI]', 'added_mass_infinite':'Infinite-frequency added mass [SI]',
    'restoring':'Incremental buoyancy stiffness [SI]', 'baseline':'Reference baseline [N, Nm]',
    'excitation_real':'Excitation real amplitude [N, Nm]', 'excitation_imag':'Excitation imaginary amplitude [N, Nm]',
    'omega':'Angular frequency [rad/s]', 'gravity_included':'Gravity included in fluid data',
    'source_kind':'Source kind', 'source_identity':'Source identity', 'force_basis':'Hydrodynamic force basis',
    'hydrostatics_role':'Hydrostatic role', 'prehistory':'Radiation prehistory', 'equilibrium_hash':'Equilibrium binding',
    'mooring':'Dynamic mooring lines', 'initial_nodes':'Initial free-node states',
    'seabed_contact':'Compliant frictionless seabed', 'elevation':'Seabed WORLD z [m]',
    'normal_stiffness':'Seabed normal stiffness [Pa/m]', 'normal_damping':'Seabed normal damping [Pa s/m]',
    'contact_diameters':'Segment contact diameters [m]',
    'positions':'WORLD positions [m]', 'node_count':'Number of free nodes',
    'line_id':'Line identity', 'endpoints':'Ordered line endpoints', 'node_index':'Free-node index',
    'rest_lengths':'Segment rest lengths [m]', 'axial_rigidity':'Segment axial rigidity EA [N]',
    'axial_damping':'Segment axial damping [N s/m]', 'mass_per_length':'Segment mass per rest length [kg/m]',
    'displaced_area':'Segment displaced area [m²]', 'fluid_density':'Fluid density [kg/m³]',
    'hydrodynamics':'Segment line hydrodynamics', 'drag_normal':'Normal drag Cd (projected diameter)',
    'drag_tangential':'Tangential drag Cd (skin circumference)',
    'added_mass_normal':'Normal added mass Ca', 'added_mass_tangential':'Tangential added mass Ca',
    'inertia_normal':'Normal fluid inertia Cm (explicit)', 'inertia_tangential':'Tangential fluid inertia Cm (explicit)'}


class NativeInputsDialog(QDialog):
    """Edit existing declared values; shared compiler owns final admission."""
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Native mechanical inputs — development, unqualified')
        self.resize(930, 680)
        apply_theme(self)
        self._project = project
        self._data = project.native_inputs.to_dict()
        self._leaves = []
        self._member_items = []
        self._bodies = tuple((body.body_id, body.name) for body in project.bodies)
        layout = QVBoxLayout(self)
        label = QLabel('Rigid-body/link mechanics with optional Morison waves/current and dynamic mooring nodes. Rotation matrices are authoritative. '
                       'Declared fluid development fixtures are supported; real hydrodynamic sources remain unadmitted. Inputs are validated before applying changes.')
        label.setWordWrap(True)
        layout.addWidget(label)
        self.model = QStandardItemModel(self)
        self.model.setHorizontalHeaderLabels(['Engineering input', 'Declared value'])
        self.tree = QTreeView(self)
        self.tree.setModel(self.model)
        self.tree.setAlternatingRowColors(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        layout.addWidget(self.tree)
        self._building = False
        self.model.itemChanged.connect(self._validate_cell)
        authoring = QHBoxLayout()
        self._create_morison_button = QPushButton('Create Morison members…', self)
        self._create_morison_button.clicked.connect(self._create_morison)
        authoring.addWidget(self._create_morison_button)
        self._add_member_button = QPushButton('Add Morison member…', self)
        self._add_member_button.clicked.connect(self._add_member)
        authoring.addWidget(self._add_member_button)
        self._edit_member_button = QPushButton('Edit Morison member…', self)
        self._edit_member_button.clicked.connect(self._edit_member)
        authoring.addWidget(self._edit_member_button)
        self._remove_member_button = QPushButton('Remove Morison member', self)
        self._remove_member_button.clicked.connect(self._remove_member)
        authoring.addWidget(self._remove_member_button)
        layout.addLayout(authoring)
        self._build_tree()
        self.tree.selectionModel().currentChanged.connect(self._sync_member_buttons)
        self._sync_member_buttons()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def data(self):
        """Working copy with all staged tree edits applied.

        Raises ValueError naming the offending leaf when a pending numeric
        cell does not parse; the working copy is then left unchanged.
        """
        import copy
        data = copy.deepcopy(self._data)
        for cell, path, kind in self._leaves:
            if kind is bool:
                value = cell.checkState() == Qt.CheckState.Checked
            elif kind is int:
                text = cell.text().strip()
                try:
                    value = int(text)
                except ValueError:
                    raise ValueError(f'{self._leaf_title(path)} value {text!r} '
                        'is not a valid integer.') from None
            elif kind is float:
                text = cell.text().strip()
                try:
                    value = float(text)
                except ValueError:
                    raise ValueError(f'{self._leaf_title(path)} value {text!r} '
                        'is not a valid number.') from None
            else:
                continue
            target = data
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
        return data



    @staticmethod
    def _leaf_title(path):
        parts = [f'item {key + 1}' if isinstance(key, int)
                 else LABELS.get(key, str(key)) for key in path]
        return ' / '.join(parts)

    def _apply_pending_edits(self):
        """Fold staged tree edits into the working copy before a change.

        Structural member operations must not discard pending numeric tree
        edits. Returns False after an actionable warning when a pending cell
        does not parse; the tree, working copy and project are then unchanged
        and the requested operation is abandoned without opening the child
        dialog.
        """
        try:
            self._data = self.data()
        except ValueError as error:
            QMessageBox.warning(self, 'Pending input edits',
                f'A pending numeric edit could not be applied, so the requested '
                f'Morison member change was not made. {error} '
                'Correct the value in the tree and try again.')
            return False
        return True

    def accept(self):
        """Refuse Ok while a pending numeric cell does not parse."""
        if not self._apply_pending_edits():
            return
        super().accept()

    def _build_tree(self):
        """Rebuild the model and editable leaves from the working data copy."""
        self._building = True
        self.model.clear()
        self.model.setHorizontalHeaderLabels(['Engineering input', 'Declared value'])
        self._leaves = []
        self._member_items = []
        project = self._project
        names = {body.body_id: body.name for body in project.bodies}
        def add(parent_item, key, value, path):
            title = names.get(key, LABELS.get(key, str(key + 1) if isinstance(key, int) else str(key)))
            name = QStandardItem(title)
            name.setEditable(False)
            cell = QStandardItem()
            parent_item.appendRow([name, cell])
            if (len(path) == 3 and path[0] == 'morison' and path[1] == 'members'
                    and isinstance(path[2], int)):
                self._member_items.append(name)
            if isinstance(value, (dict, list)):
                cell.setEditable(False)
                items = value.items() if isinstance(value, dict) else enumerate(value)
                for child_key, item in items:
                    add(name, child_key, item, path + [child_key])
            else:
                cell.setData(tuple(path), Qt.ItemDataRole.UserRole)
                if type(value) is bool:
                    cell.setCheckable(True)
                    cell.setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
                    cell.setEditable(False)
                else:
                    cell.setText('Not selected' if value is None else str(value))
                    protected=('wave_realization' in path or 'excitation_series' in path
                    or (project.native_inputs.wave_realization is not None and 'components' in path))
                cell.setEditable(type(value) in (int, float) and not protected)
                self._leaves.append((cell, tuple(path), type(value)))
        for key, value in self._data.items():
            add(self.model.invisibleRootItem(), key, value, [key])
        self.tree.expandToDepth(1)
        self.tree.resizeColumnToContents(0)
        self.tree.setColumnWidth(0, min(max(self.tree.columnWidth(0) + 16, 240), 640))
        self._building = False
        has_morison = 'morison' in self._data
        self._create_morison_button.setVisible(not has_morison)
        self._add_member_button.setVisible(has_morison)
        self._edit_member_button.setVisible(has_morison)
        self._remove_member_button.setVisible(has_morison)
        if hasattr(self.tree, 'selectionModel') and self.tree.selectionModel() is not None:
            self._sync_member_buttons()

    def _sync_member_buttons(self, *args):
        """Edit/Remove need a selected Morison member row."""
        selected = self._selected_member_index() is not None
        for button in (self._edit_member_button, self._remove_member_button):
            button.setEnabled(selected)
            button.setToolTip('' if selected else 'Select a Morison member row first')

    def _validate_cell(self, item):
        """Inline check of a numeric cell: a value that does not parse is marked red with the reason."""
        if self._building:
            return
        for cell, path, kind in self._leaves:
            if cell is item and kind in (int, float):
                text = item.text().strip()
                try:
                    kind(text)
                    message = ''
                except ValueError:
                    message = f'Not a valid {"integer" if kind is int else "number"}: {text!r}'
                self._building = True
                try:
                    item.setBackground(QBrush(QColor('#fde2e0')) if message else QBrush())
                    item.setToolTip(message)
                finally:
                    self._building = False
                return

    def _selected_member_index(self):
        index = self.tree.currentIndex()
        if not index.isValid():
            return None
        item = self.model.itemFromIndex(index.siblingAtColumn(0))
        while item is not None:
            for position, member_item in enumerate(self._member_items):
                if item is member_item:
                    return position
            item = item.parent()
        return None

    def _notify_members(self, text):
        QMessageBox.information(self, 'Morison members', text)

    def _add_member(self):
        from .morison_authoring import MorisonMemberDialog
        if not self._apply_pending_edits():
            return
        members = self._data['morison']['members']
        dialog = MorisonMemberDialog(self._bodies,
            existing_ids=[member['member_id'] for member in members], parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        members.append(dialog.member())
        self._build_tree()

    def _edit_member(self):
        from .morison_authoring import MorisonMemberDialog
        index = self._selected_member_index()
        if index is None:
            self._notify_members('Select a Morison member row first.')
            return
        if not self._apply_pending_edits():
            return
        members = self._data['morison']['members']
        dialog = MorisonMemberDialog(self._bodies,
            existing_ids=[member['member_id'] for position, member in enumerate(members)
                          if position != index],
            member=members[index], parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        members[index] = dialog.member()
        self._build_tree()

    def _remove_member(self):
        index = self._selected_member_index()
        if index is None:
            self._notify_members('Select a Morison member row first.')
            return
        if not self._apply_pending_edits():
            return
        members = self._data['morison']['members']
        if len(members) <= 1:
            self._notify_members('The public Morison envelope requires at least one declared member, '
                'so the last member cannot be removed here. Edit its endpoints, section or '
                'ownership instead.')
            return
        del members[index]
        self._build_tree()

    def _create_morison(self):
        import copy
        from ..morison_inputs import MORISON_ID, MORISON_VERSION
        from .morison_authoring import (MorisonMemberDialog,
            development_environment_template)
        if not self._apply_pending_edits():
            return
        mooring = self._data.get('mooring')
        if mooring is not None and any('hydrodynamics' in line
                                       for line in mooring['data']['lines']):
            # Hydrodynamic mooring lines share one declared environment; the
            # new Morison contribution must declare exactly the same one.
            environment = copy.deepcopy(mooring['data']['environment'])
        else:
            environment = development_environment_template()
        dialog = MorisonMemberDialog(self._bodies, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._data['morison'] = dict(api_version=MORISON_VERSION,
            contribution_id=MORISON_ID, environment=environment,
            members=[dialog.member()])
        self._build_tree()

class NativeRunLimitsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Native run execution limits')
        apply_theme(self)
        layout = QFormLayout(self)
        self.frames = QSpinBox(self)
        self.frames.setRange(2, MAX_NATIVE_FRAMES)
        self.frames.setValue(1001)
        self.wall = QDoubleSpinBox(self)
        self.wall.setRange(.01, 600.)
        self.wall.setDecimals(2)
        self.wall.setValue(60.)
        layout.addRow('Maximum stored frames', self.frames)
        layout.addRow('Maximum process wall time [s]', self.wall)
        label = QLabel('Uses saved native integration controls. Synthetic replay settings do not define this run. '
                       'Execution limits do not establish scientific qualification.')
        label.setWordWrap(True)
        layout.addRow(label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def limits(self):
        return {'max_frames': self.frames.value(), 'wall_seconds': self.wall.value()}
