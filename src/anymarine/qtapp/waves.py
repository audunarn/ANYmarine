"""ANY3dView wave layer and controls, using the application's single job queue."""
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QComboBox,QDoubleSpinBox,
                              QLabel,QPushButton,QFileDialog)
from any3dview import MeshArrays
from ..wave_jobs import preview_request
from ..hashing import input_hash

#: Saved incident fields whose time is the replayed result's time.
REPLAY_WAVE_KINDS=('mechanical_incident','linear_regular_incident')
MATCHED_NOTE=('Saved incident waves that drove the shown result, at the replay time: Play moves bodies and waves together. '
    'Undisturbed incident field; diffraction and radiation are not drawn. Development preview — unqualified.')

class WavesPanel(QWidget):
    @property
    def handle(self):
        """The wave-surface viewer handle (None while no surface is shown)."""
        return self._handle

    @handle.setter
    def handle(self,value):
        self._handle=value
        changed=getattr(self.window,'_on_wave_layer_changed',None)
        if changed is not None:changed()

    def __init__(self,window):
        super().__init__(window)
        self.window=window
        self.field=None;self._handle=None;self.saved_poses=None;self.saved_view=None
        self.job_id=None;self.time=0.;self.mechanical_environment=None
        layout=QVBoxLayout(self)
        self.mode=QComboBox()
        self.mode.addItems(['Off','Incident waves (without disturbance)','Incident + body disturbance','Saved incident waves (matched to result)',
            'Stored HAMS: incident','Stored HAMS: total (incident + disturbance)','Stored HAMS: scattered only','Stored HAMS: prescribed-motion radiation'])
        self.mode.model().item(2).setEnabled(False)
        for i in range(4,8):self.mode.model().item(i).setEnabled(False)
        self.mode.setMinimumContentsLength(14)
        self.mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        layout.addWidget(self.mode)
        form=QFormLayout();self.regular_form=form
        self.amplitude=self._spin(.01,2.,.3,.05)
        self.omega=self._spin(.1,5.,1.4,.1)
        self.heading=self._spin(-360.,360.,0.,15.)
        self.depth=self._spin(1.,1000.,20.,1.)
        for label,widget in (('Amplitude [m]',self.amplitude),('Frequency [rad/s]',self.omega),
                             ('Propagation heading [deg]',self.heading),('Water depth [m]',self.depth)):
            form.addRow(label,widget)
            widget.valueChanged.connect(self.wave_inputs_changed)
        layout.addLayout(form)
        self.stored_box=QWidget();stored=QFormLayout(self.stored_box);self.stored_form=stored
        self.stored_frequency=QComboBox();self.stored_heading=QComboBox();self.stored_dof=QComboBox()
        self.stored_phase=self._spin(-360.,360.,0.,15.)
        self.stored_displacement=self._spin(-1.,1.,.01,.01)
        self.displacement_label=QLabel('Prescribed displacement [m]')
        for name,control in (('Saved frequency [rad/s]',self.stored_frequency),('Saved heading [deg]',self.stored_heading),('Radiation target DOF',self.stored_dof),('Harmonic phase [deg]',self.stored_phase)):
            stored.addRow(name,control)
        stored.addRow(self.displacement_label,self.stored_displacement)
        layout.addWidget(self.stored_box)
        for control in (self.stored_frequency,self.stored_heading,self.stored_dof):control.currentIndexChanged.connect(self.stored_parameters_changed)
        for control in (self.stored_phase,self.stored_displacement):control.valueChanged.connect(self.stored_parameters_changed)
        self.save_stored=QPushButton('Save stored HAMS wave bundle…');self.save_stored.clicked.connect(self.save_stored_bundle);layout.addWidget(self.save_stored)
        self._stored_controls(False)
        self.compute=QPushButton('Calculate body disturbance')
        self.compute.setProperty('primary',True)
        self.compute.clicked.connect(self.calculate)
        layout.addWidget(self.compute)
        self.play=QPushButton('Pause waves')
        self.play.clicked.connect(self.toggle_play)
        self.cancel_button=QPushButton('Cancel wave calculation')
        self.cancel_button.clicked.connect(self.cancel)
        secondary=QHBoxLayout();secondary.addWidget(self.play);secondary.addWidget(self.cancel_button)
        layout.addLayout(secondary)
        self.status=QLabel('Choose Incident waves, or calculate body disturbance.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.elevation_legend=QLabel('Surface elevation [m]');self.elevation_legend.setProperty('role','hint')
        self.elevation_legend.setWordWrap(True);layout.addWidget(self.elevation_legend)
        self.note=QLabel('Fixed captured body poses; linear Airy waves, no wave stretching. '
                    'Disturbed view adds calculated diffraction. Body-motion radiation is not included. '
                    'Coarse development preview — unqualified.')
        self.note.setWordWrap(True)
        self.note.setProperty('role','hint')
        layout.addWidget(self.note)
        layout.addStretch()
        self.timer=QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.advance)
        self.mode.currentIndexChanged.connect(self.set_mode)
        window._job_manager.wave_ready.connect(self.field_ready)
        window._job_manager.state_changed.connect(self.job_state)

    @staticmethod
    def _spin(low,high,value,step):
        spin=QDoubleSpinBox();spin.setRange(low,high);spin.setDecimals(3)
        spin.setSingleStep(step);spin.setValue(value)
        return spin

    @property
    def active(self):
        return self.saved_poses is not None

    @property
    def follows_replay(self):
        """True for a saved incident field driven by the replay time."""
        return self.field is not None and self.field.get('kind') in REPLAY_WAVE_KINDS

    @staticmethod
    def _saved_environment(project):
        native=None if project is None else project.native_inputs
        if native is None:return None
        return (native.mooring.data['environment'] if native.mooring is not None and native.mooring.has_hydrodynamics
                else native.morison.data['environment'] if native.morison is not None else None)

    def _solved_in_waves(self,result):
        if result.kind=='linear_development':
            from ..services import ServiceError
            try:return self.window._service.result_recorded_wave(self.window._project,result)[1] is not None
            except ServiceError:return True  # producing inputs unknown: attempt and report the refusal
        if result.kind=='mechanical_development':
            env=self._saved_environment(self.window._replay_project or self.window._project)
            return env is not None and len(env['components'])>0
        return False

    def match_result(self,result):
        """Show the saved incident waves that drove *result*, synced to replay.

        Returns True when the result was solved in waves (a refusal is shown
        in the status line); False leaves the panel unchanged.
        """
        if result is None or not self._solved_in_waves(result):return False
        if self.mode.currentIndex()==3:self.set_mode(3)
        else:self.mode.setCurrentIndex(3)
        return True

    def _surface_grid(self,project):
        """Display grid around the shown bodies (the preview request's extent rule)."""
        points=[]
        for body in project.bodies:
            handle=self.window._handles.get(body.body_id)
            matrix=np.eye(4) if handle is None else np.asarray(handle.transform,dtype=float)
            points.append(np.asarray(body.mesh.positions,dtype=float)@matrix[:3,:3].T+matrix[:3,3])
        if not points:raise ValueError('Load a project with bodies first')
        points=np.concatenate(points);low=points.min(axis=0);high=points.max(axis=0)
        padding=max(6.,.5*float(np.max(high[:2]-low[:2])));nx,ny=33,25
        x,y=np.meshgrid(np.linspace(low[0]-padding,high[0]+padding,nx),np.linspace(low[1]-padding,high[1]+padding,ny))
        vertices=np.column_stack((x.ravel(),y.ravel(),np.zeros(x.size)))
        i=np.arange((ny-1)*nx).reshape(ny-1,nx)[:,:-1].ravel()
        triangles=np.vstack((np.column_stack((i,i+1,i+nx)),np.column_stack((i+1,i+nx+1,i+nx))))
        return vertices,triangles

    def request(self):
        w=self.window
        project=w._replay_project or w._project
        if project is None:
            raise ValueError('Load a project first')
        transforms={bid:np.array(h.transform,copy=True) for bid,h in w._handles.items()}
        return preview_request(project,transforms,omega=self.omega.value(),amplitude=self.amplitude.value(),
                               heading_degrees=self.heading.value(),water_depth=self.depth.value())

    def freeze(self):
        if not self.active:
            w=self.window
            if not (self.follows_replay and w._current_result is not None):
                w._play_timer.stop();w._play_button.setText('Play')
            self.saved_poses={bid:np.array(h.transform,copy=True) for bid,h in w._handles.items()}
            self.saved_view=w.viewer.export_view_state()

    def hide_layer(self):
        self.timer.stop()
        # Only an active wave layer owns a pending viewport fit (its show_layer
        # scheduled one). Hiding/resetting with no wave present must leave a
        # queued body/model fit intact, or cold software startup never fits.
        if self.active or self.handle is not None:
            self.window._fit_timer.stop();self.window._fit_pending=False
        if self.handle is not None:
            self.handle.remove();self.handle=None
        if self.saved_poses is not None:
            for bid,pose in self.saved_poses.items():
                if bid in self.window._handles:
                    self.window._handles[bid].set_transform(pose)
            self.window.viewer.apply_view_state(self.saved_view)
        self.saved_poses=None;self.saved_view=None

    def reset(self,*args):
        self.cancel()
        self.hide_layer()
        self.field=None;self.job_id=None;self.mechanical_environment=None
        self.mode.blockSignals(True);self.mode.setCurrentIndex(0);self.mode.blockSignals(False)
        self.mode.model().item(2).setEnabled(False)
        for i in range(4,8):self.mode.model().item(i).setEnabled(False)
        self._stored_controls(False)
        self.note.setText('Fixed captured body poses; linear Airy waves, no wave stretching. Disturbed view adds calculated diffraction; body-motion radiation is not included. Development preview — unqualified.')
        self.compute.setEnabled(True)
        for control in (self.amplitude,self.omega,self.heading,self.depth):control.setEnabled(True)
        self.status.setText('Wave inputs changed; choose Incident waves or calculate disturbance.')

    def set_mode(self,index):
        result=self.window._current_result
        if index and result is not None and result.kind=='equilibrium_development':
            self.reset()
            self.status.setText('Static continuation has no wave time. Select a dynamic result or clear replay to preview waves.')
            return
        self._stored_controls(index>=4)
        for control in (self.amplitude,self.omega,self.heading,self.depth):control.setEnabled(index!=3 and (index<4 or control is self.amplitude and index!=7))
        if index>=4:
            try:
                if self.field is None or self.field.get('kind')!='stored_hams_points':raise ValueError('Load a stored HAMS wave bundle first')
                record=self.field['replay'];project=self.window._project
                if project is None or not record.matches(project) or result is not None:raise ValueError('Stored HAMS waves belong to their producing geometry; unrelated body replay refused')
                for body in record.project.bodies:
                    expected=np.eye(4);expected[:3,:3]=body.reference_orientation;expected[:3,3]=body.reference_position
                    if body.body_id not in self.window._handles or not np.allclose(self.window._handles[body.body_id].transform,expected,rtol=0,atol=1e-12):raise ValueError('Stored HAMS producing body pose changed')
                self.compute.setEnabled(False);self.freeze();self.stored_parameters_changed()
                self.status.setText(f'Stored HAMS {self._stored_component()} — {len(record.surface_indices)} surface samples, {len(record.fields.points)-len(record.surface_indices)} submerged pressure samples retained; unqualified')
                self.note.setText('Saved HAMS point responses at producing geometry. No interpolation or reconstructed wave surface. Bodies remain at equilibrium; radiation uses the selected prescribed displacement in the saved target frame, not a solved trajectory. Dynamic pressure is not drawn as elevation. Development, unqualified.')
            except Exception as error:self.reset();self.status.setText(str(error))
            return
        if index==0:
            self.compute.setEnabled(True)
            self.hide_layer()
            return
        if self.field is not None and self.field.get('kind')=='stored_hams_points':
            self.hide_layer();self.field=None
            for i in range(4,8):self.mode.model().item(i).setEnabled(False)
            self._stored_controls(False)
            self.compute.setEnabled(True)
            self.note.setText('Fixed captured body poses; linear Airy waves, no wave stretching. Disturbed view adds calculated diffraction; body-motion radiation is not included. Development preview — unqualified.')
        if index==3:
            self.cancel();self.job_id=None
            try:
                result=self.window._current_result
                project=self.window._replay_project or self.window._project
                if project is None:raise ValueError('Load a project with saved incident waves first')
                if result is not None and result.kind not in ('linear_development','mechanical_development'):
                    raise ValueError('The shown result was not solved in saved incident waves; '
                                     'clear the replay to preview the project waves')
                if result is not None and result.kind=='linear_development':
                    from ..services import ServiceError
                    try:wave=self.window._service.result_incident_wave(self.window._project,result)
                    except ServiceError as error:raise ValueError(str(error)) from error
                    self.mechanical_environment=wave
                    identity=dict(kind='linear_regular_incident',result_input_hash=result.input_hash)
                    depth='deep water' if wave.water_depth==float('inf') else f'depth {wave.water_depth:.4g} m'
                    status=(f'Result forcing wave — amplitude {wave.amplitude:.4g} m, {wave.omega:.4g} rad/s, '
                            f'heading {np.rad2deg(wave.heading):.4g}°, {depth}; no body disturbance')
                else:
                    from anyloads import WaveRealization
                    if project.native_inputs is None:
                        raise ValueError('Load a project with a saved marine environment first')
                    native=project.native_inputs
                    env=self._saved_environment(project)
                    if env is None:raise ValueError('No saved member/line marine environment')
                    waves=None if native.wave_realization is None else WaveRealization.from_dict(native.wave_realization.to_dict())
                    from anyloads.marine import environment_from_dict
                    from ..native_inputs import _plain
                    self.mechanical_environment=environment_from_dict(_plain(env))
                    identity=dict(kind='mechanical_incident',
                        wave_realization_hash=None if waves is None else waves.content_hash,project_input_hash=input_hash(project))
                    count=len(self.mechanical_environment.components)
                    status=f'Saved mechanical incident waves — {count} component{"" if count==1 else "s"}; no body disturbance'
                vertices,triangles=self._surface_grid(project)
                self.field=dict(vertices=vertices,triangles=triangles,**identity)
                self.time=(result.times[min(max(self.window._current_frame,0),result.frame_count()-1)]
                           if result is not None else 0.)
                self.hide_layer();self.freeze();self.show_layer()
                if result is not None:
                    self.timer.stop();self.play.setText('Wave time follows replay')
                self.compute.setEnabled(False)
                self.status.setText(status)
                self.note.setText(MATCHED_NOTE if result is not None else
                    'Saved incident waves on their own clock; bodies keep their captured poses. No body disturbance. Development preview — unqualified.')
            except Exception as error:
                self.reset();self.status.setText(str(error))
            return
        self.compute.setEnabled(True)
        if self.follows_replay:
            # Leaving saved waves: their display grid is not the preview grid.
            self.hide_layer();self.field=None
        self.mechanical_environment=None
        try:
            from anyhydro.wave_preview import incident_field,request_hash
            r=self.request()
            if self.field is None or self.field.get('request_hash')!=request_hash(r):
                self.field=incident_field(r)
                self.mode.model().item(2).setEnabled(False)
            if index==2 and self.field['kind']!='fixed_body_diffraction_preview':
                raise ValueError('Calculate body disturbance first')
            self.freeze()
            self.show_layer()
            self.status.setText('Fixed-body diffraction preview' if index==2 else 'Incident waves — no body disturbance')
        except Exception as error:
            self.reset()
            self.status.setText(str(error))

    def positions(self):
        if self.field['kind']=='stored_hams_points':return self.field['replay'].positions(self._stored_component(),self.time,**self._stored_selection())
        if self.follows_replay:
            points=np.array(self.field['vertices'],copy=True)
            points[:,2]=self.mechanical_environment.surface_elevation(points[:,:2],self.time)
            return points
        from anyhydro.wave_preview import positions_at_time
        return positions_at_time(self.field,self.time,disturbed=self.mode.currentIndex()==2)

    def elevation_values(self,points):
        datum=self.mechanical_environment.still_water_level if self.follows_replay else 0.
        return points[:,2]-datum

    def elevation_bound(self):
        if self.field['kind']=='stored_hams_points':
            bound=float(np.max(np.abs(self.field['replay'].response(self._stored_component(),**self._stored_selection()))))
        elif self.follows_replay:
            bound=sum(abs(c.amplitude) for c in self.mechanical_environment.components)
        else:
            incident=np.asarray(self.field['incident_real'])+1j*np.asarray(self.field['incident_imag'])
            diffraction=np.asarray(self.field['diffraction_real'])+1j*np.asarray(self.field['diffraction_imag'])
            bound=float(np.max(np.abs(incident))+np.max(np.abs(diffraction)))
        return max(float(bound),1e-12)

    def show_layer(self,*,fit=True):
        from anyhydro.wave_preview import positions_at_time
        if self.handle is None:
            points=self.positions();triangles=np.asarray(self.field['triangles'],dtype=np.uint32)
            edges=np.unique(np.sort(np.concatenate((triangles[:,[0,1]],triangles[:,[1,2]],triangles[:,[2,0]])),axis=1),axis=0)
            bound=self.elevation_bound()
            self.elevation_legend.setText(f'Surface elevation [m] — colors −{bound:.3g} to +{bound:.3g}; true height')
            self.handle=self.window.viewer.add_mesh_arrays(MeshArrays(positions=points,
                triangles=triangles,lines=edges,node_scalars=self.elevation_values(points),
                point_indices=np.arange(len(points),dtype=np.uint32) if self.field['kind']=='stored_hams_points' else None),
                scalar_range=(-bound,bound),line_color='#69a9c4',line_width=1,color='#349ac8',
                opacity=1.,tags='wave-preview',two_sided_shell=True,
                back_color='#349ac8',pickable=False,point_color='#349ac8',point_size=12)
            if fit:
                self.window._schedule_fit()
        self.advance(step=False)
        self.timer.start()
        self.play.setText('Pause waves')

    def advance(self,*,step=True):
        if self.handle is None or self.field is None:
            return
        if self.window._current_result is not None and self.window._current_result.kind=='equilibrium_development':
            self.reset()
            self.status.setText('Static continuation has no wave time.')
            return
        # Body replay is suspended while showing a fixed-pose diffraction field.
        if not self.follows_replay:
            for bid,pose in self.saved_poses.items():
                if bid in self.window._handles:self.window._handles[bid].set_transform(pose)
        elif self.window._current_result is not None:
            self.timer.stop();step=False
            result=self.window._current_result
            self.time=result.times[min(max(self.window._current_frame,0),result.frame_count()-1)]
        if step:
            self.time+=.1
        points=self.positions()
        self.handle.update_positions(points)
        self.handle.update_node_scalars(self.elevation_values(points))

    def toggle_play(self):
        if self.follows_replay and self.window._current_result is not None:
            self.timer.stop();self.play.setText('Wave time follows replay');return
        if self.timer.isActive():
            self.timer.stop();self.play.setText('Play waves')
        elif self.active:
            self.timer.start();self.play.setText('Pause waves')

    def calculate(self):
        try:
            if self.mode.currentIndex()>=4:raise ValueError('Stored HAMS replay does not run a numerical producer')
            if self.mode.currentIndex()==3:
                raise ValueError('Body disturbance for the saved multi-frequency sea state is not calculated')
            if self.mode.currentIndex()==0:
                self.mode.setCurrentIndex(1)
            r=self.request()
            self.job_id=self.window._job_manager.submit_wave(r)
            self.compute.setEnabled(False)
            self.status.setText('Calculating fixed-body diffraction in a child process…')
        except Exception as error:
            self.status.setText(str(error))

    def cancel(self):
        if self.job_id is not None:
            self.window._job_manager.request_cancel(self.job_id)

    def job_state(self,job_id,state):
        if job_id==self.job_id and state in ('failed','cancelled','completed'):
            self.compute.setEnabled(True)
            if state!='completed':
                self.status.setText(f'Wave calculation {state}; logs preserved under Jobs / Diagnostics.')

    def field_ready(self,job_id,field):
        if job_id!=self.job_id:
            return
        from anyhydro.wave_preview import request_hash
        try:
            if field['request_hash']!=request_hash(self.request()):
                raise ValueError('Wave field is stale; captured model/wave inputs changed')
            if self.handle is not None:
                self.handle.remove();self.handle=None
            self.field=field
            self.mode.model().item(2).setEnabled(True)
            self.mode.blockSignals(True);self.mode.setCurrentIndex(2);self.mode.blockSignals(False)
            self.set_mode(2)
        except Exception as error:
            self.status.setText(str(error))

    def renderer_changed(self):
        self.handle=None  # the old viewer released its layer with its context
        if self.active and self.field is not None:
            was_playing=self.timer.isActive()
            self.show_layer(fit=False)
            if not was_playing:
                self.timer.stop();self.play.setText('Play waves')

    def set_replay_time(self,time):
        if self.follows_replay and self.handle is not None:
            self.timer.stop();self.time=time
            self.saved_poses={bid:np.array(h.transform,copy=True) for bid,h in self.window._handles.items()}
            self.advance(step=False);self.play.setText('Wave time follows replay')

    def _stored_controls(self,enabled):
        self.stored_box.setVisible(enabled)
        # Saved incident waves carry their own parameters: hide the manual ones.
        regular=self.mode.currentIndex()!=3
        for control in (self.amplitude,self.omega,self.heading,self.depth):
            shown=regular and (control is self.amplitude or not enabled)
            control.setVisible(shown);self.regular_form.labelForField(control).setVisible(shown)
        for control in (self.stored_frequency,self.stored_heading,self.stored_dof,self.stored_phase,self.stored_displacement):control.setEnabled(enabled)
        self.save_stored.setEnabled(self.field is not None and self.field.get('kind')=='stored_hams_points')
        self.save_stored.setVisible(self.field is not None and self.field.get('kind')=='stored_hams_points')
        radiation=enabled and self.mode.currentIndex()==7
        for control in (self.stored_dof,self.stored_displacement):
            control.setVisible(radiation);self.stored_form.labelForField(control).setVisible(radiation)
        self.stored_heading.setVisible(not radiation);self.stored_form.labelForField(self.stored_heading).setVisible(not radiation)
        self.stored_dof.setEnabled(radiation);self.stored_displacement.setEnabled(radiation)
        self.stored_heading.setEnabled(enabled and not radiation)

    def _stored_component(self):return ('incident','total','scattered','radiation')[self.mode.currentIndex()-4]

    def _stored_selection(self):
        return dict(frequency=self.stored_frequency.currentIndex(),heading=self.stored_heading.currentIndex(),
            amplitude=self.amplitude.value(),phase=np.deg2rad(self.stored_phase.value()),
            dof=self.stored_dof.currentIndex(),displacement=self.stored_displacement.value())

    def wave_inputs_changed(self,*args):
        if self.field is not None and self.field.get('kind')=='stored_hams_points' and self.mode.currentIndex()>=4:self.stored_parameters_changed()
        else:self.reset()

    def stored_parameters_changed(self,*args):
        if self.field is None or self.field.get('kind')!='stored_hams_points' or self.mode.currentIndex()<4:return
        key=self.field['replay'].fields.dof_keys[self.stored_dof.currentIndex()]
        self.displacement_label.setText('Prescribed displacement [rad]' if key.rsplit('::',1)[-1] in ('roll','pitch','yaw') else 'Prescribed displacement [m]')
        playing=self.timer.isActive();fresh=self.handle is None
        if self.handle is not None:self.handle.remove();self.handle=None
        self.show_layer(fit=fresh)
        if not playing and not fresh:self.timer.stop();self.play.setText('Play waves')

    def load_stored_hams(self,record):
        self.reset()
        for control,values in ((self.stored_frequency,[f'{w:.8g}' for w in record.fields.omegas]),
            (self.stored_heading,[f'{np.rad2deg(h):.8g}' for h in record.fields.headings]),(self.stored_dof,record.fields.dof_keys)):
            control.blockSignals(True);control.clear();control.addItems(list(values));control.setCurrentIndex(0);control.blockSignals(False)
        self.stored_phase.blockSignals(True);self.stored_phase.setValue(0.);self.stored_phase.blockSignals(False)
        self.field=dict(kind='stored_hams_points',source_kind=record.source_kind,replay=record,vertices=record.vertices,triangles=np.empty((0,3),dtype=np.uint32))
        self.stored_dof.setToolTip('Target coefficient frames: '+str(record.fields.metadata['target_frames']))
        for i in range(4,8):self.mode.model().item(i).setEnabled(True)
        self.time=0.;self.mode.setCurrentIndex(5)

    def save_stored_bundle(self):
        if self.field is None or self.field.get('kind')!='stored_hams_points':return
        directory,_=QFileDialog.getSaveFileName(self,'Save stored HAMS wave bundle as a new directory','hams-wave-bundle')
        if not directory:return
        try:self.window._service.save_hams_wave_bundle(directory,self.field['replay'])
        except Exception as error:self.window._notify_warning('Save stored waves',str(error));return
        self.status.setText('Stored HAMS wave bundle saved: '+directory)

