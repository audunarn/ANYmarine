"""Retained ANY3dView centreline display of declared rigid-body endpoints.

Geometry comes from saved input records and the displayed body transforms.
This layer does not calculate member forces, wet geometry or solver motion.
"""
import numpy as np
from any3dview import MeshArrays


class MemberScene:
    def __init__(self, viewer):
        self.viewer = viewer
        self.handles = {}
        self.members = ()
        self.lines=();self.line_nodes=();self.line_handles={}
        self.contact_inputs=None;self.floor_handle=None;self.contact_handles={}

    @staticmethod
    def points(member, body_handles):
        points = []
        for end in ('end_a', 'end_b'):
            endpoint = member[end]
            transform = body_handles[endpoint['body_id']].transform
            points.append(transform[:3, :3] @ np.asarray(endpoint['point_body']) + transform[:3, 3])
        return np.asarray(points, dtype=np.float64)

    def clear(self):
        for handle in (*self.handles.values(),*self.line_handles.values(),*self.contact_handles.values(),
                       *((self.floor_handle,) if self.floor_handle is not None else ())):
            if not handle.removed:
                handle.remove()
        self.handles = {}
        self.members = ()
        self.lines=();self.line_nodes=();self.line_handles={}
        self.contact_inputs=None;self.floor_handle=None;self.contact_handles={}

    def sync(self, project, body_handles, *, line_nodes=None):
        self.clear()
        inputs = None if project is None else project.native_inputs
        static=None if project is None else project.line_equilibrium_inputs
        if inputs is None and static is None:
            return
        self.members = () if inputs is None or inputs.morison is None else inputs.morison.data['members']
        for member in self.members:
            points = self.points(member, body_handles)
            self.handles[member['member_id']] = self.viewer.add_mesh_arrays(
                MeshArrays(positions=points, triangles=np.empty((0, 3), dtype=np.uint32),
                           lines=np.asarray([[0, 1]], dtype=np.uint32)),
                tags='morison-member:' + member['member_id'], line_color='#008b8b', line_width=3)
        mooring=static if static is not None else inputs.mooring
        if mooring is not None:
            data=static.model if static is not None else mooring.data
            if static is not None and line_nodes is None:return  # No invented static initial geometry.
            self.lines=data['lines'];self.line_nodes=line_nodes if static is not None else mooring.initial_nodes['positions']
            for line in self.lines:
                points=self.line_points(line,body_handles,self.line_nodes)
                self.line_handles[line['line_id']]=self.viewer.add_mesh_arrays(
                    MeshArrays(positions=points,triangles=np.empty((0,3),dtype=np.uint32),
                               lines=np.c_[np.arange(len(points)-1),np.arange(1,len(points))].astype(np.uint32)),
                    tags='mooring-line:'+line['line_id'],line_color='#9b5b12',line_width=3)
            if 'seabed_contact' in data:
                self.contact_inputs=None if static is not None else mooring  # No stored per-stage static contact flags.
                points=np.concatenate([self.line_points(line,body_handles,self.line_nodes) for line in self.lines])
                low=points[:,:2].min(axis=0)-.5;high=points[:,:2].max(axis=0)+.5
                high=np.maximum(high,low+1.)
                z=data['seabed_contact']['elevation']
                vertices=np.array([[low[0],low[1],z],[high[0],low[1],z],
                                   [high[0],high[1],z],[low[0],high[1],z]])
                self.floor_handle=self.viewer.add_mesh_arrays(MeshArrays(positions=vertices,
                    triangles=np.array([[0,1,2],[0,2,3]],dtype=np.uint32),lines=np.array([[0,1],[1,2],[2,3],[3,0]],dtype=np.uint32)),
                    tags='mooring-seabed',color='#bcc4ca',line_color='#77838c',two_sided_shell=True,
                    back_color='#bcc4ca',pickable=False)

    @staticmethod
    def line_points(line,body_handles,nodes):
        points=[]
        for ep in line['endpoints']:
            if ep['type']=='fixed':point=ep['position']
            elif ep['type']=='node':point=nodes[ep['node_index']]
            else:
                transform=body_handles[ep['body_id']].transform
                point=transform[:3,:3]@np.asarray(ep['point_body'])+transform[:3,3]
            points.append(point)
        return np.asarray(points,dtype=np.float64)

    def update(self, body_handles,node_positions=None,*,result=None,index=0):
        for member in self.members:
            self.handles[member['member_id']].update_positions(self.points(member, body_handles))
        if node_positions is not None:self.line_nodes=node_positions
        for line in self.lines:
            self.line_handles[line['line_id']].update_positions(self.line_points(line,body_handles,self.line_nodes))
        wanted={}
        if self.contact_inputs is not None and result is not None and result.kind in ('mechanical_development','equilibrium_development'):
            from ..contact_inputs import contact_lumps
            for i,(_,endpoint,diameter,ep) in enumerate(contact_lumps(self.contact_inputs)):
                if result.channels[f'mooring/contact/lump{i}/active'][index]==1.:
                    old=wanted.get(endpoint)
                    if old is None or diameter>old[0]:wanted[endpoint]=(diameter,ep)
        for endpoint in set(self.contact_handles)-set(wanted):self.contact_handles.pop(endpoint).remove()
        for endpoint,(diameter,ep) in wanted.items():
            point=self.line_points({'endpoints':(ep,)},body_handles,self.line_nodes)[0]
            radius=max(.03,.2*diameter)
            vertices=point+np.array([[-radius,0.,0.],[radius,0.,0.],[0.,-radius,0.],[0.,radius,0.]])
            if endpoint in self.contact_handles:self.contact_handles[endpoint].update_positions(vertices)
            else:
                self.contact_handles[endpoint]=self.viewer.add_mesh_arrays(MeshArrays(positions=vertices,
                    triangles=np.empty((0,3),dtype=np.uint32),lines=np.array([[0,1],[2,3]],dtype=np.uint32)),
                    tags='mooring-contact:'+str(endpoint),line_color='#d62728',line_width=4,pickable=False)
