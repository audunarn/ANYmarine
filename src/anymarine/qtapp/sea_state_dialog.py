"""Explicit generation of a new saved development sea state."""
from PySide6.QtWidgets import (QDialog,QFormLayout,QDoubleSpinBox,QSpinBox,QLabel,
    QDialogButtonBox)
from .theme import apply_theme

class SeaStateDialog(QDialog):
    def __init__(self,project,parent=None):
        super().__init__(parent);apply_theme(self)
        self.setWindowTitle('Shared sea state — development')
        data=project.native_inputs.wave_realization.data
        spectrum=data.get('jonswap')
        if spectrum is None or data.get('seed') is None:
            raise ValueError('The sea-state editor requires a saved seeded JONSWAP realization')
        layout=QFormLayout(self)
        self.height=QDoubleSpinBox();self.height.setDecimals(4);self.height.setRange(0.,2.)
        self.height.setValue(spectrum['significant_height'])
        self.period=QDoubleSpinBox();self.period.setDecimals(3);self.period.setRange(1.,30.)
        self.period.setValue(spectrum['peak_period'])
        self.seed=QSpinBox();self.seed.setRange(0,2147483647);self.seed.setValue(data['seed'])
        layout.addRow('Significant wave height [m]',self.height)
        layout.addRow('Peak period [s]',self.period)
        layout.addRow('Random phase seed',self.seed)
        note=QLabel('Creates a new saved realization for excitation, Morison kinematics and incident visualization. '
            'Development bins: 0.7, 1.1 and 1.6 rad/s; propagation 0° and 90°. '
            'Finite bins are normalized to wave height; continuous-spectrum accuracy is unqualified.')
        note.setWordWrap(True);layout.addRow(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addRow(buttons)

    def parameters(self):
        return dict(significant_height=self.height.value(),peak_period=self.period.value(),seed=self.seed.value())
