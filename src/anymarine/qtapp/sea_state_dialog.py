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
        self._initial=(self.height.value(),self.period.value(),self.seed.value())
        layout.addRow('Significant wave height [m] (0 to 2)',self.height)
        layout.addRow('Peak period [s] (1 to 30)',self.period)
        layout.addRow('Random phase seed',self.seed)
        note=QLabel('Creates a new saved sea state for the load, Morison and wave-view calculations. '
            'Development preview: three frequencies and two wave directions.')
        note.setToolTip('Development bins: 0.7, 1.1 and 1.6 rad/s; propagation 0° and 90°. '
            'Finite bins are normalized to wave height; continuous-spectrum accuracy is unqualified.')
        note.setWordWrap(True);layout.addRow(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel
            |QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).setText('Reset')
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).setToolTip('Restore the values the dialog opened with')
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self.reset)
        ok=buttons.button(QDialogButtonBox.StandardButton.Ok);ok.setDefault(True);ok.setFocus()
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addRow(buttons)

    def reset(self):
        self.height.setValue(self._initial[0]);self.period.setValue(self._initial[1]);self.seed.setValue(self._initial[2])

    def parameters(self):
        return dict(significant_height=self.height.value(),peak_period=self.period.value(),seed=self.seed.value())
