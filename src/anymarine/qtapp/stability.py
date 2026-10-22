"""GUI-thread workbench for optional, bounded analytical stability studies.

NAPA-style loading screen: hull drawing with coloured tanks, tank table with fill
sliders, floating-position / weight / stability value blocks and a status banner.
The project JSON stays the single source of truth (Inputs tab); the screen is a
view of it and edits it exactly. Nothing calculates unless the user opts in.
"""
import json
import hashlib
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

from ..services import ServiceError
from ..stability_model import derive, parse, with_fill
from .stability_view import HullView, TankTable, ValueBlock, fmt

STYLE = """
QFrame#valueBlock {background:#ffffff;border:1px solid #dce4ee;border-radius:6px;}
QLabel#blockTitle {color:#5b6e80;font-weight:600;font-size:10px;letter-spacing:1px;}
QLabel#valueName {color:#5b6e80;}
QLabel#valueText {color:#19344c;font-weight:600;}
QLabel#stabilityBanner {border-radius:6px;padding:8px 12px;font-weight:600;}
QLabel#stabilityBadge {color:#8a5a00;background:#fff4d6;border-radius:4px;padding:2px 8px;}
QTableWidget#tankTable {background:#ffffff;alternate-background-color:#f4f7fb;color:#19344c;border:1px solid #dce4ee;gridline-color:#edf1f5;}
QTableWidget#tankTable QHeaderView::section {background:#eaf0f6;color:#334c61;border:0;padding:6px;font-weight:600;}
QTableWidget#tankTable QDoubleSpinBox {color:#19344c;background:#ffffff;}
QTabWidget::pane {border:1px solid #dce4ee;background:#ffffff;}
QTableWidget#tankTable::item:selected {background:#fde9c4;color:#19344c;}
QLabel#tankDetail {color:#334c61;background:#f4f7fb;border-radius:4px;padding:6px 8px;}
"""
BANNER = {'info': '#e8f0f8;color:#19344c', 'ok': '#dff3e4;color:#14532d', 'warn': '#fff4d6;color:#7a4b00',
          'bad': '#b42318;color:#ffffff', 'idle': '#eef1f4;color:#5b6e80'}


class _StabilityStudy:
    """Shared controls/state for embedded and standalone studies; no geometry here."""
    def _setup_study(self, service, initial_inputs, recorder):
        self.service = service
        self._recorder = recorder
        self.result = None
        self.study_root = None
        self._registry = {}
        self._model = None
        self._selected = None
        self.setWindowTitle("ANYstability — analytical reference study")
        self.resize(1100, 760)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        top = QHBoxLayout()
        badge = QLabel("REFERENCE ONLY · analytical box · heel = trim = 0")
        badge.setObjectName("stabilityBadge")
        badge.setToolTip("Solves vertical force balance only. Tank contents are fixed masses; no certified hull, "
                         "rotational equilibrium, GZ curve or criteria. Separate from the marine mechanical project.")
        top.addWidget(badge)
        top.addStretch(1)
        top.addWidget(QLabel("Required GM"))
        self.required_gm = QDoubleSpinBox()
        self.required_gm.setObjectName("requiredGm")
        self.required_gm.setRange(0.0, 50.0)
        self.required_gm.setDecimals(2)
        self.required_gm.setSingleStep(0.05)
        self.required_gm.setSuffix(" m")
        self.required_gm.setSpecialValueText("off")
        self.required_gm.setToolTip("Optional minimum GM that you set yourself; the banner turns red below it. "
                                    "Not a class-rule criterion.")
        top.addWidget(self.required_gm)
        layout.addLayout(top)
        self.banner = QLabel()
        self.banner.setObjectName("stabilityBanner")
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        # Loading screen: drawing | tank table, value blocks beneath.
        screen = QWidget()
        sl = QVBoxLayout(screen)
        sl.setContentsMargins(0, 6, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.hull = HullView()
        splitter.addWidget(self.hull)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        self.tank_table = TankTable()
        self.tank_table.setToolTip("An emptied tank is removed from the ledger (the provider refuses zero mass) and is "
                                   "only kept on screen for this session; it is not restored when a study is reopened.")
        rl.addWidget(self.tank_table, 1)
        self.tank_detail = QLabel()
        self.tank_detail.setObjectName("tankDetail")
        self.tank_detail.setWordWrap(True)
        rl.addWidget(self.tank_detail)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.tank_table.setMinimumWidth(430)
        self.hull.setMinimumWidth(300)
        splitter.setSizes([560, 560])
        sl.addWidget(splitter, 1)
        blocks = QHBoxLayout()
        self.floating_block = ValueBlock("Floating position", [('draught', 'Draught'), ('trim', 'Trim'), ('heel', 'Heel'),
                                                               ('lcb', 'LCB'), ('tcb', 'TCB'), ('queries', 'Queries')])
        self.weight_block = ValueBlock("Weight", [('mass', 'Displacement'), ('fixed', 'Lightship + cargo'),
                                                  ('tanks', 'Tank contents'), ('lcg', 'LCG'), ('tcg', 'TCG'),
                                                  ('partial', 'Partial tanks')])
        self.stability_block = ValueBlock("Stability", [('kb', 'KB'), ('bm', 'BM transverse'), ('kg', 'KG'),
                                                        ('gms', 'GM solid'), ('fsc', 'Free-surface corr.'), ('gmf', 'GM fluid')])
        for block in (self.floating_block, self.weight_block, self.stability_block):
            blocks.addWidget(block, 1)
        sl.addLayout(blocks)
        self.tabs.addTab(screen, "Loading")
        # Inputs and report (advanced).
        self.inputs = QPlainTextEdit()
        self.inputs.setObjectName("stabilityInputs")
        self.inputs.setToolTip("ANYstability public project JSON: dimensions, mass ledger, density and controls in SI. "
                               "The Loading tab edits this text exactly; you can also edit it directly.")
        self.report = QTextBrowser()
        self.report.setObjectName("stabilityReport")
        self.report.setOpenExternalLinks(False)
        self.tabs.addTab(self.inputs, "Inputs (JSON)")
        self.tabs.addTab(self.report, "Report")
        row = QHBoxLayout()
        self.opt_in = QCheckBox("Allow reference calculation")
        self.opt_in.setObjectName("allowReference")
        self.opt_in.setToolTip("Explicit opt-in to the analytical reference provider (not a certified solver).")
        self.live = QCheckBox("Live")
        self.live.setObjectName("liveCalculate")
        self.live.setToolTip("Recalculate automatically when you change a tank fill. Ticking this also gives the "
                             "explicit reference-calculation opt-in. Raw JSON edits never calculate by themselves.")
        row.addWidget(self.opt_in)
        row.addWidget(self.live)
        row.addStretch(1)
        self.example_button = QPushButton("Load box example")
        self.open_button = QPushButton("Open study…")
        self.calculate_button = QPushButton("Calculate draught")
        self.calculate_button.setObjectName("calculateStability")
        self.save_button = QPushButton("Save study…")
        for button in (self.example_button, self.open_button, self.calculate_button, self.save_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._live_calculate)
        self.inputs.textChanged.connect(self._edited)
        self.opt_in.toggled.connect(self.calculate_button.setEnabled)
        self.opt_in.toggled.connect(self._opt_in_changed)
        self.live.toggled.connect(self._live_changed)
        self.required_gm.valueChanged.connect(lambda _v: self._refresh_view())
        self.calculate_button.setEnabled(False)
        self.example_button.clicked.connect(lambda: self._guard(self.load_example))
        self.open_button.clicked.connect(self._open)
        self.calculate_button.clicked.connect(lambda: self._guard(self.calculate))
        self.save_button.clicked.connect(self._save)
        self.tank_table.fillChanged.connect(self.set_tank_fill)
        self.tank_table.tankSelected.connect(self.select_tank)
        self.hull.tankClicked.connect(self.select_tank)
        self.load_example(initial_inputs)  # Inputs only; no solve on construction.

    # -- screen state -------------------------------------------------------------
    def _current_inputs(self):
        try:
            value = json.loads(self.inputs.toPlainText())
        except (ValueError, RecursionError):  # hostile/deep text is the service's to refuse
            return None
        return value if isinstance(value, dict) else None

    def select_tank(self, ident):
        self._selected = ident
        self.tank_table.select(ident)
        self._refresh_view()

    def set_tank_fill(self, ident, fraction):
        """User edit of one tank fill: an exact JSON edit; recalculates only when Live is on."""
        inputs = self._current_inputs()
        tank = next((t for t in (self._model.tanks if self._model else ()) if t.tank_id == ident), None)
        if inputs is None or tank is None:
            return
        edited = with_fill(inputs, tank, fraction)
        if edited is inputs:
            return
        self._selected = ident
        self._fill_edit = True
        try:
            self.inputs.setPlainText(json.dumps(edited, indent=2))     # -> _edited: the result becomes stale
        finally:
            self._fill_edit = False
        if self.live.isChecked() and self.opt_in.isChecked():
            self._timer.start()

    def _live_calculate(self):
        if self.live.isChecked() and self.opt_in.isChecked() and self.result is None:
            self._guard(self.calculate)

    def _live_changed(self, on):
        if on:
            self.opt_in.setChecked(True)
            if self.result is None:
                self._timer.start()
        else:
            self._timer.stop()

    def _opt_in_changed(self, on):
        if not on:
            self.live.setChecked(False)

    def _refresh_view(self):
        inputs = self._current_inputs()
        self._model = parse(inputs, self._registry) if inputs is not None else None
        derived = None
        if self._model is not None and self.result is not None:
            try:
                derived = derive(self._model, self.result.to_dict())
            except Exception:
                derived = None
        self.hull.set_state(self._model, derived, derived is None, self._selected)
        self._fill_blocks(self._model, derived)
        self.tank_table.set_tanks(self._model.tanks if self._model is not None else ())
        self.tank_table.select(self._selected)
        self._banner(self._model, derived)
        self._detail()

    def _detail(self):
        tank = next((t for t in (self._model.tanks if self._model else ()) if t.tank_id == self._selected), None)
        if tank is None:
            self.tank_detail.setText("Select a tank in the drawing or the table. Drag its fill slider to change the contents.")
            return
        cg = tank.cg
        fs = (f"free-surface inertia {tank.free_surface_inertia_m4:.3f} m⁴, ρ·i = "
              f"{tank.density * tank.free_surface_inertia_m4 / 1000:.2f} t·m" if tank.partial
              else "no free surface (full or empty)")
        self.tank_detail.setText(f"{tank.tank_id}: {tank.length_m:g} × {tank.breadth_m:g} × {tank.height_m:g} m, "
                                 f"{tank.volume_m3:.2f} of {tank.capacity_m3:.2f} m³, {tank.mass_kg / 1000:.3f} t, "
                                 f"CG ({cg[0]:.2f}, {cg[1]:.2f}, {cg[2]:.3f}) m, {fs}")

    def _fill_blocks(self, model, derived):
        if model is None:
            for block in (self.floating_block, self.weight_block, self.stability_block):
                block.set_values({})
            return
        cg = model.cg
        self.weight_block.set_values({
            'mass': fmt(model.total_mass_kg / 1000, 2, ' t'), 'fixed': fmt(model.other_mass_kg / 1000, 2, ' t'),
            'tanks': fmt(model.tank_mass_kg / 1000, 2, ' t'),
            'lcg': fmt(None if cg is None else cg[0], 3, ' m'), 'tcg': fmt(None if cg is None else cg[1], 3, ' m'),
            'partial': str(sum(1 for t in model.tanks if t.partial))})
        if derived is None:
            self.floating_block.set_values({})
            self.stability_block.set_values({'kg': fmt(None if cg is None else cg[2], 3, ' m')})
            return
        self.floating_block.set_values({
            'draught': fmt(derived.draught_m, 3, ' m'), 'trim': '0.00° (fixed)', 'heel': '0.00° (fixed)',
            'lcb': fmt(derived.lcb_m, 3, ' m'), 'tcb': fmt(derived.tcb_m, 3, ' m'), 'queries': str(derived.query_count)})
        self.stability_block.set_values({
            'kb': fmt(derived.kb_m, 3, ' m'), 'bm': fmt(derived.bm_t_m, 3, ' m'), 'kg': fmt(derived.kg_m, 3, ' m'),
            'gms': fmt(derived.gm_solid_m, 3, ' m'), 'fsc': fmt(derived.fsc_m, 3, ' m'), 'gmf': fmt(derived.gm_fluid_m, 3, ' m')})

    def _banner(self, model, derived):
        required = self.required_gm.value()
        if model is None:
            kind, text = 'idle', ("These inputs are not an analytical box study, so the Loading view is empty. "
                                  "Use Load box example or edit the Inputs tab.")
        elif self.result is None:
            how = "Live recalculation is on." if self.live.isChecked() else "Tick Live or press Calculate."
            kind, text = 'idle', f"Not calculated. {how} Mass and CG are exact edits of the inputs; draught and GM need a calculation."
        elif derived is None:
            kind, text = 'warn', "The stored result cannot be read as a box result. Recalculate."
        else:
            gm = derived.gm_fluid_m if derived.gm_fluid_m is not None else derived.gm_solid_m
            notes = []
            if derived.free_surface_tanks:
                notes.append(f"Free-surface correction {derived.fsc_m:.3f} m is a workbench estimate for "
                             f"{derived.free_surface_tanks} partial rectangular tank(s); the provider does not evaluate it.")
            if not derived.balanced:
                notes.append(f"Not in rotational equilibrium: CG is {derived.lcg_m - derived.lcb_m:+.3f} m (long.) / "
                             f"{derived.tcg_m - derived.tcb_m:+.3f} m (transv.) from the centre of buoyancy; the vessel "
                             f"would trim/heel. Not solved.")
            tail = " ".join(notes)
            if gm is None:
                kind, text = 'warn', "GM unavailable: the waterplane is not centred. " + tail
            elif required > 0 and gm < required:
                kind, text = 'bad', f"GM BELOW MINIMUM REQUIRED VALUE. REQUIRED: {required:.2f} m, ATTAINED: {gm:.2f} m. " + tail
            elif required > 0:
                kind, text = 'ok', f"GM OK. REQUIRED: {required:.2f} m, ATTAINED: {gm:.2f} m. " + tail
            else:
                kind, text = ('warn' if notes else 'info'), f"Calculated: draught {derived.draught_m:.3f} m, GM {gm:.3f} m. " + tail
        self.banner.setStyleSheet(f"background:{BANNER[kind]};")
        self.banner.setText(text.strip())

    def _guard(self, action):
        try:
            action()
        except (ServiceError, ValueError) as error:
            self.status.setText(str(error))
            QMessageBox.warning(self, "Stability study refused", str(error))

    def _edited(self):
        if not getattr(self, '_fill_edit', False):
            self._timer.stop()      # a raw edit cancels any queued Live calculation: it never calculates by itself
        self.result = None
        self.study_root = None
        self.report.clear()
        self.status.setText("Inputs changed. No current result; calculate explicitly.")
        self._refresh_view()

    def _record(self,action,code,error=None):
        if self._recorder is not None:
            self._recorder(action,code,'success' if error is None else 'refused',str(error or ''))

    @staticmethod
    def _result_binding(result):
        if result is None:return 'stability_result = None\n'
        payload=result.payload_json
        return ('stability_result = StabilityResult('+repr(payload)+')\n'+
                'assert hashlib.sha256(stability_result.payload_json.encode("utf8")).hexdigest() == '+repr(hashlib.sha256(payload.encode('utf8')).hexdigest())+'\n')

    @staticmethod
    def _input_binding(inputs):
        canonical=json.dumps(inputs,sort_keys=True,separators=(',',':'))
        return 'assert json.dumps(stability_inputs, sort_keys=True, separators=(",", ":")) == '+repr(canonical)+'\n'

    def load_example(self, inputs=None):
        code='stability_result = None\nstability_inputs = service.stability_example_inputs()\n'
        try:
            if inputs is None:inputs=self.service.stability_example_inputs()
            code+=self._input_binding(inputs)
            self._registry.clear()
            self.inputs.setPlainText(json.dumps(inputs, indent=2))
            self.opt_in.setChecked(False)
            self.status.setText("20 × 10 × 4 m analytical box; four full tanks; mass 438,500 kg; sea density 1,025 kg/m³. "
                                "Expected draught 2.13902439 m. Inputs loaded; no calculation run.")
        except Exception as error:self._record('Load stability example',code,error);raise
        self._record('Load stability example',code)

    def calculate(self):
        # Snapshot the literal input text and opt-in before parsing/solving.
        raw=self.inputs.toPlainText();allow=self.opt_in.isChecked()
        code='stability_result = None\nstability_inputs = service.parse_stability_inputs('+repr(raw)+')\n'
        code+='stability_result = service.calculate_stability(stability_inputs, allow_reference='+repr(allow)+')\n'
        self.result=None;self.report.clear()
        try:
            self.result=self.service.calculate_stability(self.service.parse_stability_inputs(raw),allow_reference=allow)
            identity=hashlib.sha256(self.result.payload_json.encode('utf8')).hexdigest()
            code+='assert hashlib.sha256(stability_result.payload_json.encode("utf8")).hexdigest() == '+repr(identity)+'\n'
            self.report.setMarkdown(self.result.markdown)
            output=self.result.to_dict()['output']
            self.status.setText(f"Reference draught: {output['draft_m']:.9g} m; {output['query_count']} queries. Rotational equilibrium not solved.")
            self._refresh_view()
        except Exception as error:self._record('Calculate stability',code,error);self._refresh_view();raise
        self._record('Calculate stability',code)

    def open_study(self,path):
        path=str(Path(path).absolute())
        code='stability_result = None\nstability_inputs, stability_result = service.open_stability_study('+repr(path)+')\n'
        try:
            inputs,result=self.service.open_stability_study(path)
            code+=self._input_binding(inputs)
            if result is None:code+='assert stability_result is None\n'
            else:code+='assert stability_result is not None and hashlib.sha256(stability_result.payload_json.encode("utf8")).hexdigest() == '+repr(hashlib.sha256(result.payload_json.encode('utf8')).hexdigest())+'\n'
            self._registry.clear()
            self.inputs.setPlainText(json.dumps(inputs,indent=2));self.result,self.study_root=result,Path(path);self.opt_in.setChecked(False)
            if result is not None:self.report.setMarkdown(result.markdown)
            self.status.setText('Study opened without recalculation. '+('Stored reference result loaded.' if result else 'No saved result.'))
            self._refresh_view()
        except Exception as error:self._record('Open stability study',code,error);raise
        self._record('Open stability study',code)

    def save_study(self,path):
        path=str(Path(path).absolute());raw=self.inputs.toPlainText()
        # Bind the actual current result (including None after edits), not a stale
        # variable in the replay interpreter. Reconstruct transport, never solve.
        code='stability_result = None\nstability_inputs = service.parse_stability_inputs('+repr(raw)+')\n'+self._result_binding(self.result)
        code+='stability_saved = service.save_stability_study(stability_inputs, '+repr(path)+', result=stability_result)\n'
        try:
            info=self.service.save_stability_study(self.service.parse_stability_inputs(raw),path,result=self.result)
            self.study_root=Path(path)
            self.status.setText(f"Saved {'reference result and reports' if info['result_saved'] else 'inputs only'}: {path}")
        except Exception as error:self._record('Save stability study',code,error);raise
        self._record('Save stability study',code)

    def _open(self):
        path = QFileDialog.getExistingDirectory(self, "Open ANYstability study")
        if path:
            self._guard(lambda: self.open_study(path))

    def _save(self):
        path = QFileDialog.getExistingDirectory(self, "Select an empty or matching stability study folder",
                                               str(self.study_root or Path.home()))
        if path:
            self._guard(lambda: self.save_study(path))


class StabilityPane(QWidget,_StabilityStudy):
    """A retained QWidget study; construction loads inputs only, never solves."""
    def __init__(self,service,parent=None,*,recorder=None):
        initial_inputs=service.stability_example_inputs()  # Refuse before allocating the study.
        super().__init__(parent)
        self._setup_study(service,initial_inputs,recorder)


class StabilityDialog(QDialog,_StabilityStudy):
    """Backward-compatible standalone dialog using the same controls and state."""
    def __init__(self,service,parent=None,*,recorder=None):
        initial_inputs=service.stability_example_inputs()
        super().__init__(parent)
        self._setup_study(service,initial_inputs,recorder)


class StabilityHost(QWidget):
    """One optional owner-backed study or an actionable, retryable unavailable page."""
    def __init__(self,service,parent=None,*,recorder=None):
        super().__init__(parent);self.service=service;self.recorder=recorder;self.study=None;self._unavailable=None
        self._layout=QVBoxLayout(self);self._layout.setContentsMargins(0,0,0,0)
        self.retry()
    def retry(self):
        if self.study is not None:return  # Never discard an active study to refresh it.
        try:candidate=StabilityPane(self.service,self,recorder=self.recorder)
        except ServiceError as error:
            if self.recorder is not None:
                self.recorder('Load stability example','stability_result = None\nstability_inputs = service.stability_example_inputs()\n','refused',str(error))
            if self._unavailable is None:
                page=QWidget(self);layout=QVBoxLayout(page)
                title=QLabel('ANYstability study unavailable');title.setObjectName('stabilityUnavailableTitle');layout.addWidget(title)
                self.diagnostic=QLabel();self.diagnostic.setWordWrap(True);self.diagnostic.setObjectName('stabilityUnavailable');layout.addWidget(self.diagnostic)
                self.retry_button=QPushButton('Retry ANYstability provider');self.retry_button.clicked.connect(self.retry);layout.addWidget(self.retry_button);layout.addStretch(1)
                self._unavailable=page;self._layout.addWidget(page)
            self.diagnostic.setText(str(error)+'\nInstall the optional ANYmarine stability dependency in this environment, then Retry. Certified hull admission remains unavailable; this interface supports the analytical reference box only.')
            return
        if self._unavailable is not None:
            self._layout.removeWidget(self._unavailable);self._unavailable.hide();self._unavailable.deleteLater();self._unavailable=None
        self._layout.addWidget(candidate);self.study=candidate
