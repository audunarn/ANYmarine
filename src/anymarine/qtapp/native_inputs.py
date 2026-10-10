"""Qt model/view editor for explicitly declared native mechanical inputs."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QLabel, QSpinBox, QTreeView, QVBoxLayout)

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
        self._data = project.native_inputs.to_dict()
        self._leaves = []
        names = {body.body_id: body.name for body in project.bodies}
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
        layout.addWidget(self.tree)
        def add(parent_item, key, value, path):
            title = names.get(key, LABELS.get(key, str(key + 1) if isinstance(key, int) else str(key)))
            name = QStandardItem(title)
            name.setEditable(False)
            cell = QStandardItem()
            parent_item.appendRow([name, cell])
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
        self.tree.setColumnWidth(0, 640)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def data(self):
        import copy
        data = copy.deepcopy(self._data)
        for cell, path, kind in self._leaves:
            if kind is bool:
                value = cell.checkState() == Qt.CheckState.Checked
            elif kind is int:
                value = int(cell.text())
            elif kind is float:
                value = float(cell.text())
            else:
                continue
            target = data
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
        return data


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
