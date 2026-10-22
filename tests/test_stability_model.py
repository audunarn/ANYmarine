"""Qt-free stability screen model: exact fill edits and derived quantities."""
import json
import math
import pytest
from anymarine import stability_model as m
from anymarine import stability_service as service

pytest.importorskip("anystability")


@pytest.fixture
def inputs():
    return service.example_inputs()


def test_parse_reads_hull_tanks_and_masses(inputs):
    box = m.parse(inputs)
    assert (box.length_m, box.breadth_m, box.height_m) == (20.0, 10.0, 4.0)
    assert [t.tank_id for t in box.tanks] == ["ballast-tank-port", "ballast-tank-starboard",
                                              "freshwater-tank-aft", "freshwater-tank-forward"]
    assert all(t.fill == 1.0 for t in box.tanks)
    assert box.total_mass_kg == 438500.0
    assert box.cg[2] == pytest.approx(1.902508551881414, abs=1e-12)  # provider's ledger CG


def test_unchanged_fill_is_bit_identical(inputs):
    tank = m.parse(inputs).tanks[0]
    assert m.with_fill(inputs, tank, 1.0) is inputs


def test_half_fill_mass_and_centroid_exact(inputs):
    tank = m.parse(inputs).tanks[0]            # 5 x 2 x 1 m, rho 1025, bottom z = 0
    edited = m.with_fill(inputs, tank, 0.5)
    after = m.parse(edited)
    t = after.tanks[0]
    assert t.fill == 0.5 and t.mass_kg == 5125.0 and t.cg == (0.0, 3.0, 0.25)
    assert after.total_mass_kg == 438500.0 - 5125.0
    assert edited["condition"]["items"] != inputs["condition"]["items"]
    assert inputs == service.example_inputs()   # the source dict is not mutated
    service.parse_inputs(json.dumps(edited))     # owner validation accepts it


def test_empty_tank_leaves_ledger_but_stays_on_screen(inputs):
    registry = {}
    tank = m.parse(inputs, registry).tanks[0]
    emptied = m.with_fill(inputs, tank, 0.0)
    assert all(i["item_id"] != tank.tank_id for i in emptied["condition"]["items"])
    again = m.parse(emptied, registry)
    assert [t.tank_id for t in again.tanks] == [t.tank_id for t in m.parse(inputs).tanks]
    assert again.tanks[0].fill == 0.0
    refilled = m.with_fill(emptied, again.tanks[0], 1.0)
    assert m.parse(refilled).tanks[0].fill == 1.0
    assert m.parse(refilled).total_mass_kg == 438500.0


def test_unknown_inputs_are_not_understood():
    assert m.parse({"example": "reference only"}) is None
    assert m.parse("x") is None


def test_derived_box_values_match_hand_calculation(inputs):
    result = service.calculate(inputs, allow_reference=True).to_dict()
    d = m.derive(m.parse(inputs), result)
    assert d.draught_m == pytest.approx(438500 / (1025 * 200), abs=1e-5)       # 2.139024
    assert d.kb_m == pytest.approx(d.draught_m / 2, abs=1e-6)
    assert d.bm_t_m == pytest.approx(10 ** 2 / (12 * d.draught_m), abs=1e-6)   # B^2/(12 T)
    assert d.bm_l_m == pytest.approx(20 ** 2 / (12 * d.draught_m), abs=1e-6)
    assert d.fsc_m == 0.0 and d.free_surface_tanks == 0                        # all tanks full
    assert d.gm_solid_m == pytest.approx(d.kb_m + d.bm_t_m - d.kg_m)
    assert d.balanced


def test_partial_tank_free_surface_correction(inputs):
    registry = {}
    tank = m.parse(inputs, registry).tanks[0]
    edited = m.with_fill(inputs, tank, 0.5)
    box = m.parse(edited, registry)
    d = m.derive(box, service.calculate(edited, allow_reference=True).to_dict())
    expected = 1025.0 * (5.0 * 2.0 ** 3 / 12.0) / box.total_mass_kg
    assert d.free_surface_tanks == 1 and d.fsc_m == pytest.approx(expected, rel=1e-12)
    assert d.gm_fluid_m == pytest.approx(d.gm_solid_m - expected)
    assert not d.balanced      # port tank half empty: heeling arm is shown, not hidden


def test_derive_refuses_a_result_it_cannot_read(inputs):
    assert m.derive(m.parse(inputs), {"output": {}}) is None


# --- Codex review round 1 regressions ---------------------------------------------
def test_tank_whose_ledger_mass_disagrees_with_metadata_is_not_trusted(inputs):
    edited = json.loads(json.dumps(inputs))
    for item in edited["condition"]["items"]:
        if item["item_id"] == "ballast-tank-port":
            item["mass_kg"] *= 2
    box = m.parse(edited)
    assert box.total_mass_kg == 438500.0 + 10250.0            # the ledger wins
    assert "ballast-tank-port" not in [t.tank_id for t in box.tanks]


@pytest.mark.parametrize("path,value", [(("vessel", "geometry", "definition", "length_m"), 0.0),
                                        (("vessel", "geometry", "definition", "breadth_m"), -1.0),
                                        (("vessel", "geometry", "definition", "height_m"), float("nan"))])
def test_nonpositive_or_nonfinite_hull_is_not_understood(inputs, path, value):
    edited = json.loads(json.dumps(inputs))
    node = edited
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert m.parse(edited) is None


# --- Codex review round 2 regressions ---------------------------------------------
def _edit_item(inputs, item_id, fn):
    edited = json.loads(json.dumps(inputs))
    for item in edited["condition"]["items"]:
        if item["item_id"] == item_id:
            fn(item)
    return edited


def test_partial_tank_with_fixed_centre_keeps_ledger_cg(inputs):
    def half(item):
        src = json.loads(item["source"])
        src["fill_fraction"] = 0.5
        item["source"] = json.dumps(src, sort_keys=True)
        item["mass_kg"] = 5125.0                   # cg stays at the geometric centre z=0.5 (provider-valid)
    edited = _edit_item(inputs, "ballast-tank-port", half)
    service.parse_inputs(json.dumps(edited))
    box = m.parse(edited)
    assert "ballast-tank-port" not in [t.tank_id for t in box.tanks]     # not an editable tank
    ledger_kg = sum(i["mass_kg"] * i["cg_body_m"][2] for i in edited["condition"]["items"]) / box.total_mass_kg
    assert box.cg[2] == pytest.approx(ledger_kg, abs=1e-12)              # KG is the ledger's, not a re-derivation


def test_rejected_tank_is_not_resurrected_from_the_registry(inputs):
    registry = {}
    m.parse(inputs, registry)
    bad = _edit_item(inputs, "ballast-tank-port", lambda item: item.update(mass_kg=item["mass_kg"] * 2))
    box = m.parse(bad, registry)
    assert "ballast-tank-port" not in [t.tank_id for t in box.tanks]
    assert box.total_mass_kg == 438500.0 + 10250.0
    assert "ballast-tank-port" not in registry


# --- Codex review round 3 regressions (class: the screen must never rewrite or outvote the ledger) ----
def test_fill_edit_preserves_every_other_ledger_field(inputs):
    edited = _edit_item(inputs, "ballast-tank-port", lambda item: item.update(covers=["custom-port-liquid"], extra_note="keep"))
    box = m.parse(edited)
    tank = next(t for t in box.tanks if t.tank_id == "ballast-tank-port")
    out = m.with_fill(edited, tank, 0.5)
    item = next(i for i in out["condition"]["items"] if i["item_id"] == "ballast-tank-port")
    assert item["covers"] == ["custom-port-liquid"] and item["extra_note"] == "keep"
    assert [i["item_id"] for i in out["condition"]["items"]] == [i["item_id"] for i in edited["condition"]["items"]]


def test_duplicate_item_ids_are_not_understood(inputs):
    dup = json.loads(json.dumps(inputs))
    dup["condition"]["items"].append(json.loads(json.dumps(dup["condition"]["items"][0])))
    assert m.parse(dup) is None


def test_overflowing_tank_metadata_is_not_trusted(inputs):
    def huge(item):
        src = json.loads(item["source"])
        src["dimensions_m"] = [1e200, 1e200, 1.0]
        item["source"] = json.dumps(src, sort_keys=True)
    box = m.parse(_edit_item(inputs, "ballast-tank-port", huge))
    assert box.total_mass_kg == 438500.0 and all(math_isfinite for math_isfinite in map(lambda v: v == v and abs(v) < 1e300, box.cg))


# --- Codex review round 4 regressions ---------------------------------------------
def test_extreme_tank_dimensions_cannot_override_the_ledger_cg(inputs):
    def extreme(item):
        src = json.loads(item["source"])
        src["dimensions_m"] = [1e-8, 1e-7, 1e16]
        item["source"] = json.dumps(src, sort_keys=True)
    edited = _edit_item(inputs, "ballast-tank-port", extreme)
    box = m.parse(edited)
    assert "ballast-tank-port" not in [t.tank_id for t in box.tanks]
    ledger_kg = sum(i["mass_kg"] * i["cg_body_m"][2] for i in edited["condition"]["items"]) / box.total_mass_kg
    assert box.cg[2] == pytest.approx(ledger_kg, abs=1e-12)


def test_overflowing_free_surface_inertia_is_refused_not_raised(inputs):
    def wide(item):
        src = json.loads(item["source"])
        src["dimensions_m"] = [1e-110, 1e110, 10.0]
        item["source"] = json.dumps(src, sort_keys=True)
    box = m.parse(_edit_item(inputs, "ballast-tank-port", wide))      # must not raise
    assert "ballast-tank-port" not in [t.tank_id for t in box.tanks]


# --- Codex review round 5 regressions: totals come from the ledger; metadata is bounded ---------
def test_totals_come_from_the_ledger_even_when_metadata_is_accepted(inputs):
    edited = json.loads(json.dumps(inputs))
    for item in edited["condition"]["items"]:
        if item["item_id"] == "ballast-tank-port":
            item["cg_body_m"][0] = 1e15            # outside the trusted position range
            item["mass_kg"] += 1e-6
        if item["item_id"] == "ballast-tank-starboard":
            item["cg_body_m"][0] = -1e15
    service.parse_inputs(json.dumps(edited))
    box = m.parse(edited)
    ledger = sum(i["mass_kg"] * i["cg_body_m"][0] for i in edited["condition"]["items"]) / sum(i["mass_kg"] for i in edited["condition"]["items"])
    assert box.cg[0] == pytest.approx(ledger, rel=1e-12) and box.total_mass_kg == sum(i["mass_kg"] for i in edited["condition"]["items"])


def test_extreme_density_and_fill_cannot_produce_infinite_values(inputs):
    def extreme(item):
        src = json.loads(item["source"])
        src["liquid_density_kg_m3"], src["fill_fraction"] = 1e308, 1e-308
        item["source"] = json.dumps(src, sort_keys=True)
    edited = _edit_item(inputs, "ballast-tank-port", extreme)
    box = m.parse(edited)
    assert "ballast-tank-port" not in [t.tank_id for t in box.tanks]
    d = m.derive(box, service.calculate(edited, allow_reference=True).to_dict())
    assert d.fsc_m == 0.0 and math.isfinite(d.gm_fluid_m) and math.isfinite(d.gm_solid_m)


def test_derive_refuses_a_result_from_a_different_ledger(inputs):
    result = service.calculate(inputs, allow_reference=True).to_dict()
    tank = m.parse(inputs).tanks[0]
    assert m.derive(m.parse(m.with_fill(inputs, tank, 0.5)), result) is None
    assert m.derive(m.parse(inputs), result) is not None


def test_with_fill_never_writes_a_non_finite_value(inputs):
    tank = m.parse(inputs).tanks[0]
    import dataclasses
    huge = dataclasses.replace(tank, density=1e308, length_m=1e4, breadth_m=1e4, height_m=1e4)
    assert m.with_fill(inputs, huge, 0.5) is inputs


# --- Codex review round 6 regressions -------------------------------------------------
def test_inconsistent_or_unknown_cg_declaration_is_not_editable(inputs):
    registry = {}
    tank = m.parse(inputs, registry).tanks[0]
    half = m.with_fill(inputs, tank, 0.5)
    def bottom(item):
        src = json.loads(item["source"]); src["bottom_z_m"] = 10.0
        item["source"] = json.dumps(src, sort_keys=True)
    assert "ballast-tank-port" not in [t.tank_id for t in m.parse(_edit_item(half, "ballast-tank-port", bottom)).tanks]
    def unknown(item):
        src = json.loads(item["source"]); src["cg_model"] = "mystery"
        item["source"] = json.dumps(src, sort_keys=True)
    assert "ballast-tank-port" not in [t.tank_id for t in m.parse(_edit_item(inputs, "ballast-tank-port", unknown)).tanks]
    def missing(item):
        src = json.loads(item["source"]); del src["cg_model"]
        item["source"] = json.dumps(src, sort_keys=True)
    assert "ballast-tank-port" not in [t.tank_id for t in m.parse(_edit_item(inputs, "ballast-tank-port", missing)).tanks]


def test_empty_then_refill_restores_original_ledger_fields(inputs):
    edited = _edit_item(inputs, "ballast-tank-port", lambda i: i.update(covers=["custom-port-liquid"], extra_note="keep"))
    registry = {}
    tank = m.parse(edited, registry).tanks[0]
    emptied = m.with_fill(edited, tank, 0.0)
    gone = m.parse(emptied, registry).tanks[0]
    assert gone.fill == 0.0
    refilled = m.with_fill(emptied, gone, 0.5)
    item = next(i for i in refilled["condition"]["items"] if i["item_id"] == "ballast-tank-port")
    assert item["covers"] == ["custom-port-liquid"] and item["extra_note"] == "keep"


# --- Codex review round 7 regressions -------------------------------------------------
@pytest.mark.parametrize("bad", [float("nan"), float("-inf"), float("inf"), 1e-7])
def test_with_fill_refuses_nonfinite_or_untrackable_fractions(inputs, bad):
    tank = m.parse(inputs).tanks[0]
    assert m.with_fill(inputs, tank, bad) is inputs


def test_inconsistent_capacity_metadata_is_not_editable(inputs):
    def cap(item):
        src = json.loads(item["source"]); src["capacity_m3"] = 99.0
        item["source"] = json.dumps(src, sort_keys=True)
    assert "ballast-tank-port" not in [t.tank_id for t in m.parse(_edit_item(inputs, "ballast-tank-port", cap)).tanks]
