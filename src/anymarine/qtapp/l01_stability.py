"""L01 assumed-tank preview and optional public engineering-workflow binding."""
import json
import os
from pathlib import Path

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QPushButton, QFileDialog, QMessageBox)

from ..l01_stability import load_l01_stability


class L01StabilityDialog(QDialog):
    def __init__(self, recipe, parent=None):
        super().__init__(parent)
        self.recipe = recipe
        self.engineering = None
        self.setWindowTitle('L01 stability — assumed four-tank arrangement')
        self.resize(1000, 760)
        layout = QVBoxLayout(self)
        data = recipe.to_dict()
        label = QLabel('L01 reference mass and CG · four assumed rectangular tanks · no calculation executed')
        label.setWordWrap(True);layout.addWidget(label)
        self.table = QTableWidget(4, 5)
        self.table.setHorizontalHeaderLabels(['Tank', 'X bounds [m]', 'Y bounds [m]', 'Z bounds [m]', 'Liquid [m³]'])
        for row, tank in enumerate(data['tanks']):
            values = [tank['name'], *[str(v) for v in tank['bounds_body_m']], str(tank['target_m3'])]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                from PySide6.QtCore import Qt
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents();layout.addWidget(self.table)
        assumptions = QLabel('\n'.join(data['assumptions']) + '\n\nDry mass: '
            + f"{data['base_condition']['items'][0]['mass_kg']:,.0f} kg; total upright mass: {data['total_upright_mass_kg']:,.0f} kg."
            + '\nReference SHA-256: ' + data['reference']['file_sha256'])
        assumptions.setWordWrap(True);layout.addWidget(assumptions)
        self.status = QLabel(data['limitation']);self.status.setWordWrap(True);layout.addWidget(self.status)
        self.prepare_button = QPushButton('Open separately prepared L01 neutral geometry…')
        self.prepare_button.clicked.connect(self._prepare);layout.addWidget(self.prepare_button)
        close = QPushButton('Close');close.clicked.connect(self.close);layout.addWidget(close)

    def _prepare(self):
        source = QFileDialog.getOpenFileName(self, 'Open validated L01 neutral bundle', '', 'JSON (*.json)')[0]
        if not source:return
        from ..engineering_stability import default_owner_wheel
        wheel = default_owner_wheel() or QFileDialog.getOpenFileName(self, 'Select matching ANYgeometry wheel', '', 'Wheel (*.whl)')[0]
        if not wheel:return
        try:
            path = Path(source)
            if path.stat().st_size > 64 * 1024 * 1024:
                raise ValueError('Neutral bundle exceeds 64 MiB transport bound.')
            study = self.recipe.engineering_study(json.loads(path.read_text(encoding='utf-8')), owner_wheel=wheel)
            self.set_study(study)
        except Exception as error:
            self.status.setText(str(error))

    def set_study(self, study):
        """Show immutable inputs in the existing equilibrium/GZ UI; never run on load."""
        if self.engineering is not None and self.engineering.job:
            raise ValueError('Cancel and finish the active calculation before replacing inputs.')
        if self.engineering is None:
            from .engineering_stability import EngineeringStabilityPane
            self.engineering = EngineeringStabilityPane(parent=self)
            self.layout().insertWidget(self.layout().count()-1, self.engineering, 1)
        # Existing pane's owner-free input path; result deliberately absent.
        self.engineering._opened_study(Path(self.recipe.to_dict()['reference']['path']), study, None)
        self.status.setText('L01 neutral source bound. No calculation executed. Review provider and development controls before running.')

    def closeEvent(self, event):
        if self.engineering is not None and not self.engineering.shutdown():
            event.ignore();return
        super().closeEvent(event)


def open_l01_stability(window):
    """MainWindow callback. Existing recipe3/reference workspace is untouched."""
    current = getattr(window, '_l01_stability_dialog', None)
    if current is not None:
        current.show();current.raise_();current.activateWindow();return current
    try:
        reference = os.environ.get('ANYMARINE_L01_REFERENCE_COMPARISON')
        if not reference:
            reference = next((p for p in window._l01_reference_candidates() if p.is_file()), None)
        recipe = load_l01_stability(reference)
    except ValueError as error:
        path = QFileDialog.getOpenFileName(window, str(error), '', 'Reference comparison (*.json)')[0]
        if not path:return None
        try:recipe = load_l01_stability(path)
        except ValueError as failure:
            QMessageBox.warning(window, 'L01 stability source', str(failure));return None
    from .stability import StabilityDialog
    from ..services import ServiceError
    try:
        dialog = StabilityDialog(window._service, window, recorder=getattr(window, '_record_stability_action', None))
    except (ServiceError, ValueError) as error:
        QMessageBox.warning(window, 'L01 analytical stability provider', str(error));return None
    dialog.recipe = recipe
    dialog.load_example(recipe.analytical_box_inputs())
    dialog.setWindowTitle('L01-based ANALYTICAL BOX — four full assumed tanks')
    explanation = QLabel('Educational 100 × 18 × 12 m BOX approximation, not the actual L01 hull. '
        'Four full fixed-mass tanks; L01 reference total mass/CG retained with +6.66 m keel-frame shift. '
        'Vertical draft and analytical solid GM only; no free-surface, rotational equilibrium, GZ or regulatory claim.')
    explanation.setWordWrap(True);dialog.layout().insertWidget(1, explanation)
    dialog.status.setText('L01-based analytical box inputs loaded; no calculation executed. Allow reference calculation, then Calculate draught.')
    dialog.tank_table.setEnabled(False)
    dialog.tank_table.setToolTip('This case uses four full fixed-mass tanks. Partial-fill/free-surface analyses are outside this example.')
    dialog.live.hide()
    dialog.example_button.setText('Reset L01 box')
    dialog.example_button.clicked.disconnect()
    def reset():
        dialog.load_example(recipe.analytical_box_inputs())
        dialog.status.setText('L01-based analytical box reset; no calculation executed.')
    dialog.example_button.clicked.connect(reset)
    window._l01_stability_dialog = dialog
    dialog.show()
    return dialog
