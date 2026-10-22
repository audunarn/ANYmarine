"""Qt-free view-model for the analytical-box stability screen.

Reads tanks and hull from the public ANYstability project JSON, applies a tank
fill as an exact edit of that JSON, and derives screen quantities from a
calculated reference result. It never calculates a draught itself and never
upgrades the provider's claims: upright, fixed heel/trim, analytical box only.
Anything it cannot understand yields ``None`` rather than a plausible number.
"""
from dataclasses import dataclass, replace
import json
import math

# Sane ranges. Tank metadata outside them is not trusted (the ledger item still counts in every total);
# hull values outside them make the whole box view refuse.
MIN_TANK_M, MAX_TANK_M = 1e-3, 1e4
MAX_POSITION_M = 1e5
MIN_FILL = 1e-6
MIN_DENSITY, MAX_DENSITY = 1.0, 3e4
MIN_HULL_M, MAX_HULL_M = 1e-3, 1e5
TANK_KIND_FULL = "declared_reference_full_tank_contents"
TANK_KIND_FILL = "declared_reference_tank_contents"
CG_CENTRE = "fixed_at_geometric_center"
CG_LIQUID = "rectangular_upright_liquid_centroid"


@dataclass(frozen=True)
class Tank:
    tank_id: str
    kind: str                 # 'ballast' | 'fresh' | 'liquid' (display grouping only)
    length_m: float
    breadth_m: float
    height_m: float
    cx_m: float
    cy_m: float
    bottom_z_m: float
    density: float
    fill: float               # 0..1 volume fraction
    ledger_extra: str = ""    # JSON of the ledger item's other fields (covers, notes); restored on re-fill

    @property
    def capacity_m3(self):
        return self.length_m * self.breadth_m * self.height_m

    @property
    def volume_m3(self):
        return self.capacity_m3 * self.fill

    @property
    def mass_kg(self):
        return self.volume_m3 * self.density

    @property
    def cg(self):
        return (self.cx_m, self.cy_m, self.bottom_z_m + self.fill * self.height_m / 2.0)

    @property
    def free_surface_inertia_m4(self):
        """Transverse free-surface inertia l*b^3/12; zero when full or empty."""
        return self.length_m * self.breadth_m ** 3 / 12.0 if 0.0 < self.fill < 1.0 else 0.0

    @property
    def partial(self):
        return 0.0 < self.fill < 1.0


@dataclass(frozen=True)
class BoxModel:
    length_m: float
    breadth_m: float
    height_m: float
    water_density: float
    tanks: tuple
    other_mass_kg: float          # ledger items that are not editable tanks (lightship, cargo, ...)
    ledger_mass_kg: float         # EVERY ledger item, exactly as the owner will use it
    ledger_moment: tuple          # sum(mass * cg) over every ledger item, kg*m
    gravity: float

    @property
    def total_mass_kg(self):
        return self.ledger_mass_kg

    @property
    def tank_mass_kg(self):
        return self.ledger_mass_kg - self.other_mass_kg

    @property
    def cg(self):
        """Ledger centre of gravity; tank metadata never moves a total."""
        if self.ledger_mass_kg <= 0:
            return None
        return tuple(m / self.ledger_mass_kg for m in self.ledger_moment)

    @property
    def any_partial(self):
        return any(t.partial for t in self.tanks)


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("not a finite number")
    return float(value)


def _positive(value):
    value = _num(value)
    if value <= 0.0:
        raise ValueError("not positive")
    return value


def _val(value):
    """Provider quantities are plain floats or {'value': x, 'error': e, ...} enclosures."""
    return _num(value["value"] if isinstance(value, dict) else value)


def _ranged(value, low, high):
    value = _num(value)
    if not low <= value <= high:
        raise ValueError("outside the trusted range")
    return value


def _tank_from_item(item):
    """A Tank for a ledger item whose source declares a rectangular tank, else None."""
    try:
        source = json.loads(item["source"])
    except (TypeError, ValueError, KeyError):
        return None
    if not isinstance(source, dict) or source.get("kind") not in (TANK_KIND_FULL, TANK_KIND_FILL):
        return None
    if source.get("shape") != "rectangular":
        return None
    try:
        length, breadth, height = (_ranged(v, MIN_TANK_M, MAX_TANK_M) for v in source["dimensions_m"])
        fill = _ranged(source["fill_fraction"], MIN_FILL, 1.0)
        density = _ranged(source["liquid_density_kg_m3"], MIN_DENSITY, MAX_DENSITY)
        cx, cy, cz = (_ranged(v, -MAX_POSITION_M, MAX_POSITION_M) for v in item["cg_body_m"])
        ledger_mass = _num(item["mass_kg"])
    except (ValueError, TypeError, KeyError):
        return None         # untrusted metadata: the item stays an ordinary ledger item
    if source.get("cg_model") not in (CG_CENTRE, CG_LIQUID):
        return None          # unknown or missing CG semantics: do not guess
    if source.get("cg_model") == CG_CENTRE and fill != 1.0:
        return None          # a partial tank with a declared fixed CG: keep the ledger's CG, not our centroid
    bottom = cz - (height / 2.0 if source.get("cg_model") == CG_CENTRE else fill * height / 2.0)
    ident = str(item["item_id"])
    kind = "ballast" if "ballast" in ident else "fresh" if "fresh" in ident else "liquid"
    if "capacity_m3" in source:
        try:
            declared_capacity = _num(source["capacity_m3"])
        except ValueError:
            return None
        if abs(declared_capacity - length * breadth * height) > 1e-9 * max(1.0, declared_capacity):
            return None      # declared capacity disagrees with the declared dimensions
    if "bottom_z_m" in source:
        try:
            declared = _num(source["bottom_z_m"])
        except ValueError:
            return None
        if abs(declared - bottom) > 1e-9 * max(1.0, abs(bottom)):
            return None      # declared tank bottom disagrees with the ledger CG
    extra = json.dumps({k: v for k, v in item.items() if k not in ("mass_kg", "cg_body_m", "source")}, sort_keys=True)
    tank = Tank(ident, kind, length, breadth, height, cx, cy, bottom, density, fill, extra)
    if abs(tank.cg[2] - cz) > 1e-9 * max(1.0, abs(cz)):
        return None          # reconstructed centroid must reproduce the ledger CG
    if not all(math.isfinite(v) for v in (tank.capacity_m3, tank.mass_kg, tank.cg[2], tank.free_surface_inertia_m4)):
        return None
    # The ledger is the truth the provider uses; metadata that disagrees with it is not trusted.
    if abs(ledger_mass - tank.mass_kg) > 1e-9 * max(1.0, tank.mass_kg):
        return None
    return tank


def parse(inputs, registry=None):
    """BoxModel from project JSON, or None when it is not an analytical box study.

    ``registry`` maps tank id -> Tank for tanks that are empty (empty tanks are
    removed from the ledger because the provider refuses zero mass); pass the
    same dict on every call so emptied tanks keep their place on the screen.
    """
    try:
        geometry = inputs["vessel"]["geometry"]
        if geometry["provider_id"] != "reference_box_v1":
            return None
        d = geometry["definition"]
        length, breadth, height = (_ranged(d[k], MIN_HULL_M, MAX_HULL_M) for k in ("length_m", "breadth_m", "height_m"))
        condition = inputs["condition"]
        tanks, other_mass, ledger_mass, ledger_moment, present = {}, 0.0, 0.0, [0.0, 0.0, 0.0], set()
        for item in condition["items"]:
            if str(item["item_id"]) in present:
                return None                          # the owner refuses duplicate ids; do not show a repaired view
            present.add(str(item["item_id"]))
            tank = _tank_from_item(item)
            mass = _num(item["mass_kg"])
            cgs = [_num(c) for c in item["cg_body_m"]]
            ledger_mass += mass
            for i, c in enumerate(cgs):
                ledger_moment[i] += mass * c
            if tank is not None:
                tanks[tank.tank_id] = tank
            else:
                other_mass += mass
        if not all(math.isfinite(v) for v in (ledger_mass, other_mass, *ledger_moment)):
            return None
        if registry is not None:
            for ident in [k for k in registry if k in present and k not in tanks]:
                del registry[ident]                  # in the ledger but no longer trusted: not an empty tank
            registry.update(tanks)
            tanks = {k: tanks.get(k, replace(v, fill=0.0)) for k, v in registry.items()}
        return BoxModel(length, breadth, height, _num(condition["water_density_kg_m3"]),
                        tuple(sorted(tanks.values(), key=lambda t: t.tank_id)),
                        other_mass, ledger_mass, tuple(ledger_moment), _num(condition["gravity_m_s2"]))
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError):
        return None


def with_fill(inputs, tank, fraction):
    """New project JSON with ``tank`` filled to ``fraction`` (0..1); a no-op returns ``inputs``.

    The liquid is an upright rectangular prism, so its centroid rises exactly by
    fill*height/2 and the mass is density*volume. Free-surface effects stay
    ``not_evaluated`` in the provenance, as the provider does not model them.
    """
    fraction = float(fraction)
    if not math.isfinite(fraction) or 0.0 < fraction < MIN_FILL:
        return inputs            # refuse; never clamp a bad value into an edit that deletes or untracks the tank
    fraction = min(1.0, max(0.0, fraction))
    if fraction == tank.fill:
        return inputs
    updated = json.loads(json.dumps(inputs))
    items = updated["condition"]["items"]
    index = next((i for i, item in enumerate(items) if item.get("item_id") == tank.tank_id), None)
    if fraction <= 0.0:
        if index is not None:
            del items[index]
    else:
        new = replace(tank, fill=fraction)
        if not all(math.isfinite(v) for v in (new.mass_kg, *new.cg)):
            return inputs        # refuse rather than write a non-finite ledger value
        source = dict(kind=TANK_KIND_FILL, shape="rectangular",
                      dimensions_m=[tank.length_m, tank.breadth_m, tank.height_m],
                      capacity_m3=tank.capacity_m3, liquid_density_kg_m3=tank.density,
                      fill_fraction=fraction, cg_model=CG_LIQUID, bottom_z_m=tank.bottom_z_m,
                      free_surface_model="not_evaluated", shell_mass="included_in_lightship")
        if index is None:        # re-filling an emptied tank: a new ledger item
            restored = json.loads(tank.ledger_extra) if tank.ledger_extra else {}
            restored.setdefault("item_id", tank.tank_id)
            restored.setdefault("covers", [f"contents:{tank.tank_id}"])
            items.append(restored)
            index = len(items) - 1
        # Only mass, CG and the declared source change; covers and any other field are the ledger's.
        items[index].update(mass_kg=new.mass_kg, cg_body_m=list(new.cg), source=json.dumps(source, sort_keys=True))
    return updated


@dataclass(frozen=True)
class Derived:
    """Screen quantities from one calculated reference result (all SI, body frame, baseline at z=0)."""
    draught_m: float
    volume_m3: float
    displacement_t: float
    kb_m: float
    kg_m: float
    lcb_m: float
    lcg_m: float
    tcb_m: float
    tcg_m: float
    bm_t_m: "float | None"       # None when the waterplane is not centred (refused, not guessed)
    bm_l_m: "float | None"
    fsc_m: float                 # free-surface correction, rectangular tanks only
    status: str
    free_surface_tanks: int
    query_count: int

    @property
    def gm_solid_m(self):
        return None if self.bm_t_m is None else self.kb_m + self.bm_t_m - self.kg_m

    @property
    def gm_fluid_m(self):
        return None if self.gm_solid_m is None else self.gm_solid_m - self.fsc_m

    @property
    def trim_moment_arm_m(self):
        return self.lcg_m - self.lcb_m

    @property
    def heel_moment_arm_m(self):
        return self.tcg_m - self.tcb_m

    @property
    def balanced(self):
        """True when CG lies below the centre of buoyancy within 1 mm (no trim/heel couple)."""
        return abs(self.trim_moment_arm_m) < 1e-3 and abs(self.heel_moment_arm_m) < 1e-3


def derive(model, result):
    """Derived from a BoxModel and ``StabilityResult.to_dict()``; None if anything is missing."""
    try:
        out = result["output"]
        state = out["state"]
        hydro = state["hydrostatics"]
        volume = _val(hydro["volume_m3"])
        cb = [_val(c) for c in hydro["center_of_buoyancy_body_m"]]
        cg = [_num(c) for c in state["mass"]["cg_body_m"]]
        mass = _num(state["mass"]["mass_kg"])
        xx, yy, xy = (_val(hydro[k]) for k in ("waterplane_raw_xx_m4", "waterplane_raw_yy_m4", "waterplane_raw_xy_m4"))
        centred = abs(xy) <= 1e-9 * max(1.0, xx, yy)
        bm_t = yy / volume if centred and volume > 0 else None
        bm_l = xx / volume if centred and volume > 0 else None
        fsc = sum(t.density * t.free_surface_inertia_m4 for t in model.tanks) / mass if mass > 0 else 0.0
        if abs(mass - model.ledger_mass_kg) > 1e-9 * max(1.0, mass):
            return None          # the result was not calculated from this ledger
        if not math.isfinite(fsc) or (bm_t is not None and not math.isfinite(bm_t)) or (bm_l is not None and not math.isfinite(bm_l)):
            return None
        return Derived(_num(out["draft_m"]), volume, mass / 1000.0, cb[2], cg[2], cb[0], cg[0], cb[1], cg[1],
                       bm_t, bm_l, fsc, str(out.get("status", "")),
                       sum(1 for t in model.tanks if t.partial), int(out.get("query_count", 0)))
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError):
        return None
