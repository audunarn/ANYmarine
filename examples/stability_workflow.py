"""Analytical box with four full tanks using ANYmarine's public optional integration.

Tank contents are fixed masses; no free-surface calculation is implemented.

python examples/stability_workflow.py --out PATH --allow-reference
Without --allow-reference this only saves inputs, and executes no calculation.
"""
import argparse
import json

from anymarine.services import ApplicationService, ServiceError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="New or empty study directory")
    parser.add_argument("--allow-reference", action="store_true")
    args = parser.parse_args(argv)
    service = ApplicationService()
    try:
        inputs = service.stability_example_inputs()
        result = service.calculate_stability(inputs, allow_reference=True) if args.allow_reference else None
        info = service.save_stability_study(inputs, args.out, result=result)
        if result is not None:
            info.update(draft_m=result.to_dict()["output"]["draft_m"], production_qualified=False)
        print(json.dumps(info, indent=2))
        return 0
    except ServiceError as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
