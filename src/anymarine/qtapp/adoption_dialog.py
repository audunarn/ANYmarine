"""Qt intent entry only; the isolated worker audits adoption."""
from pathlib import Path
import json
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QFormLayout,QLabel,QDoubleSpinBox,
    QLineEdit,QPushButton,QHBoxLayout,QDialogButtonBox,QFileDialog)
from ..equilibrium_transition import AdoptionDomain
from ..adoption_jobs import regeneration_from_data

class AdoptionDialog(QDialog):
    def __init__(self,project,result,parent=None):
        super().__init__(parent);self.setWindowTitle('Adopt selected static equilibrium')
        layout=QVBoxLayout(self)
        text=QLabel('Create dynamics initial state from the selected solved poses and line nodes.\n'
            'Development, unqualified. Fully submerged domain only.\n'
            'Fluid data and lids require explicit regenerated dependencies.\n'
            'Balance is checked in a worker; failure keeps the current project.',self)
        text.setWordWrap(True);layout.addWidget(text)
        layout.addWidget(QLabel('Selected result: '+result.result_id,self))
        form=QFormLayout();layout.addLayout(form)
        self.depth=QDoubleSpinBox(self);self.depth.setRange(.000001,1e6);self.depth.setDecimals(6);self.depth.setValue(10.)
        native=project.native_inputs
        if native.morison is not None:self.depth.setValue(native.morison.data['environment']['water_depth'])
        self.waterline=QDoubleSpinBox(self);self.waterline.setRange(-1e6,1e6);self.waterline.setDecimals(6);self.waterline.setValue(project.equilibrium.waterline_z)
        form.addRow('Water depth (m)',self.depth);form.addRow('Waterline (m)',self.waterline)
        self.dependencies=QLineEdit(self);row=QHBoxLayout();row.addWidget(self.dependencies)
        browse=QPushButton('Browse…',self);row.addWidget(browse);browse.clicked.connect(self._browse)
        form.addRow('Regenerated dependencies (JSON)',row)
        required=native.potential_flow is not None or any(b.hydro_lid is not None for b in project.bodies)
        self.dependencies.setPlaceholderText('Required for this project' if required else 'Optional; no fluid/lids in this project')
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel,self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Queue adoption')
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def _browse(self):
        path,_=QFileDialog.getOpenFileName(self,'Regenerated equilibrium dependencies','','JSON (*.json)')
        if path:self.dependencies.setText(path)

    def parameters(self):
        path=self.dependencies.text().strip()
        regenerated=None if not path else regeneration_from_data(json.loads(Path(path).read_text(encoding='utf-8')))
        return dict(domain=AdoptionDomain(self.depth.value(),self.waterline.value()),regeneration=regenerated)
