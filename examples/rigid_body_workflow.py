"""Prepare a loadable model with explicitly declared demonstration mass data.

No trajectory/BEM is computed. These numbers are illustrative inputs only.
Run with the tested runtime: python examples/rigid_body_workflow.py --out DIR
"""
import argparse
import json
from pathlib import Path
from anymarine.domain import BodyMassProperties
from anymarine.services import ApplicationService

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    if args.out.exists():
        parser.error('output must be a new directory; existing projects are preserved')
    service=ApplicationService()
    project=service.new_project('Rigid-body inputs — development demonstration')
    properties=BodyMassProperties((.3,-.2,.7),((4e6,.2e6,.1e6),(.2e6,5e6,-.1e6),(.1e6,-.1e6,6e6)))
    project=project.with_edits(bodies=tuple(body.with_edits(mass_properties=properties) for body in project.bodies))
    mechanical=[service.prepare_rigid_body(project,body.body_id) for body in project.bodies]
    service.save(project,args.out)
    print(json.dumps({'status':'prepared','kind':'declared_development_mass_inputs',
                      'bodies':len(mechanical),'dry_matrix_shapes':[list(b.mass_matrix(b.state()).shape) for b in mechanical],
                      'project':str(args.out.resolve()),'trajectory':'not computed','qualification':'not claimed'}))

if __name__=='__main__':
    main()
