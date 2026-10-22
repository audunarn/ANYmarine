"""Inspect/save explicitly configured private recipe3 evidence without numerical owner queries."""
import argparse
from anymarine.services import ApplicationService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', help='NEW portable study directory (optional)')
    parser.add_argument('--case', choices=('coupled', 'upright', 'combined', 'opposite', 'raked'), default='coupled')
    args = parser.parse_args()
    service = ApplicationService()
    study = service.select_reference_barge_case(service.reference_barge_example(), args.case)
    print('ILLUSTRATIVE saved reference evidence; not a new calculation')
    print('Study:', study.identity, 'condition:', study.selected_case)
    print(service.reference_barge_view(study))
    if args.out:
        saved = service.save_reference_barge(study, args.out)
        reopened = service.open_reference_barge(args.out)
        assert reopened.identity == saved.identity
        print('Portable study saved/reopened:', args.out)


if __name__ == '__main__':
    main()
