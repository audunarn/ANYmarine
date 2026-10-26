"""Run and retain coupled cylinder/dipole coefficients, with no commercial runtime.

Run with the compatible ANYhydro development backend installed. The retained
dataset is unqualified potential-flow evidence, not experimental viscous damping.
"""
import argparse
from pathlib import Path


def main():
    from anymarine.cylinder_heave_plate import solve_cylinder_heave_plate
    from anyhydro import save_hydro_dataset
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--without-plate', action='store_true')
    args = parser.parse_args()
    dataset = solve_cylinder_heave_plate(with_plate=not args.without_plate)
    save_hydro_dataset(dataset, args.output)
    print('Saved development coefficients:', args.output.resolve())


if __name__ == '__main__':
    main()
