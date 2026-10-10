"""Qt-free input declaration; physical inertia admission belongs to ANYsolver."""
from dataclasses import dataclass
from collections.abc import Mapping
from numbers import Real
import math

MASS_PROPERTIES_VERSION = "body_mass_properties_v1"


def _vector(values, name):
    if not isinstance(values, (tuple, list)) or len(values) != 3:
        raise ValueError(f"{name} must contain three real finite numbers")
    if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) for v in values):
        raise ValueError(f"{name} must contain three real finite numbers")
    return tuple(float(v) for v in values)


@dataclass(frozen=True)
class BodyMassProperties:
    """Explicit SI CG and full CG inertia in body/mesh axes.

    This is an input record, not a mechanics model or physical qualification.
    Entries are tensor values, not negated products of inertia. The solver
    owns symmetry, definiteness, realizability and dry mass assembly.
    """
    cg_body: tuple
    inertia_cg_body: tuple

    def __post_init__(self):
        object.__setattr__(self, "cg_body", _vector(self.cg_body, "CG [m]"))
        if not isinstance(self.inertia_cg_body, (tuple, list)) or len(self.inertia_cg_body) != 3:
            raise ValueError("CG inertia [kg m^2] must be a finite 3x3 tensor")
        object.__setattr__(self, "inertia_cg_body", tuple(
            _vector(row, "CG inertia [kg m^2]") for row in self.inertia_cg_body))

    def to_dict(self):
        return {"version": MASS_PROPERTIES_VERSION,
                "cg_body": list(self.cg_body),
                "inertia_cg_body": [list(row) for row in self.inertia_cg_body]}

    @classmethod
    def from_dict(cls, data):
        fields = {"version", "cg_body", "inertia_cg_body"}
        if not isinstance(data, Mapping) or set(data) != fields:
            raise ValueError("mass_properties must contain exactly version, cg_body, inertia_cg_body")
        if data["version"] != MASS_PROPERTIES_VERSION:
            raise ValueError(f"unsupported mass_properties version {data['version']!r}")
        return cls(data["cg_body"], data["inertia_cg_body"])
