"""Consumer tests use the real public owner API; no marine/native campaign."""
import importlib
import json
import subprocess
import sys

import pytest

from anymarine.services import ApplicationService, ServiceError
from anymarine import stability_service as bridge

pytest.importorskip("anystability")


def test_reference_box_physics_and_no_mechanical_mutation():
    service = ApplicationService("some-marine-project")
    inputs = service.stability_example_inputs()
    original = json.dumps(inputs, sort_keys=True)
    result = service.calculate_stability(inputs, allow_reference=True)
    out = result.to_dict()["output"]
    # Independent Archimedes calculation: m/(rho L B).
    expected = (205000 + 205000 + 2 * 10250 + 2 * 4000) / (1025 * 20 * 10)
    assert out["draft_m"] == pytest.approx(expected, abs=1e-6)
    assert out["state"]["hydrostatics"]["volume_m3"]["value"] == pytest.approx(438500 / 1025, abs=1e-4)
    assert out["state"]["hydrostatics"]["center_of_buoyancy_body_m"] == pytest.approx([0, 0, expected / 2], abs=1e-6)
    assert out["state"]["mass"]["mass_kg"] == 438500
    assert out["state"]["mass"]["cg_body_m"] == pytest.approx(
        [0, 0, (205000 * 1.5 + 205000 * 2.5 + 28500 * 0.5) / 438500])
    assert out["rotational_equilibrium"] == "not_solved"
    assert 0 < out["query_count"] <= 128
    assert result.to_dict()["assurance"]["production_qualified"] is False
    assert json.dumps(inputs, sort_keys=True) == original
    assert str(service.root) == "some-marine-project"
    copy = result.to_dict()
    copy["output"]["draft_m"] = -1
    assert result.to_dict()["output"]["draft_m"] > 0


def test_four_full_tanks_have_declared_contents_and_fit_inside_box():
    inputs = ApplicationService().stability_example_inputs()
    items = inputs["condition"]["items"]
    tanks = [i for i in items if "-tank-" in i["item_id"]]
    assert len(items) == 6 and len(tanks) == 4
    assert {i["item_id"] for i in tanks} == {
        "ballast-tank-port", "ballast-tank-starboard", "freshwater-tank-forward", "freshwater-tank-aft"}
    for item in tanks:
        definition = json.loads(item["source"])
        is_ballast = item["item_id"].startswith("ballast-")
        assert definition["dimensions_m"] == ([5, 2, 1] if is_ballast else [2, 2, 1])
        assert definition["capacity_m3"] == (10 if is_ballast else 4)
        assert definition["liquid_density_kg_m3"] == (1025 if is_ballast else 1000)
        assert item["mass_kg"] == (10250 if is_ballast else 4000)
        assert definition["fill_fraction"] == 1
        assert definition["free_surface_model"] == "not_evaluated"
        assert definition["shell_mass"] == "included_in_lightship"
        assert item["covers"] == ["contents:" + item["item_id"]]
        x, y, z = item["cg_body_m"]
        length, breadth, height = definition["dimensions_m"]
        assert abs(x) + length / 2 <= 10
        assert abs(y) + breadth / 2 <= 5
        assert 0 <= z - height / 2 <= z + height / 2 <= 4
    assert sum(i["mass_kg"] for i in tanks) == 28500


def test_new_example_does_not_mutate_owner_example_or_share_tanks():
    from anystability.services import example_project
    first = ApplicationService().stability_example_inputs()
    first["condition"]["items"][0]["mass_kg"] = 1
    second = ApplicationService().stability_example_inputs()
    assert sum(i["mass_kg"] for i in second["condition"]["items"]) == 438500
    assert len(example_project().condition.items) == 2


def test_reference_example_without_tank_contents_still_reproduces():
    service = ApplicationService()
    inputs = service.stability_example_inputs()
    inputs["condition"]["items"] = [i for i in inputs["condition"]["items"] if "-tank-" not in i["item_id"]]
    result = service.calculate_stability(inputs, allow_reference=True)
    assert result.to_dict()["output"]["draft_m"] == pytest.approx(2, abs=1e-6)


def test_explicit_opt_in_and_typed_cause():
    service = ApplicationService()
    with pytest.raises(bridge.StabilityIntegrationError) as caught:
        service.calculate_stability(service.stability_example_inputs())
    assert caught.value.code == "CAPABILITY_UNSUPPORTED"
    from anystability.errors import StabilityError
    assert isinstance(caught.value.__cause__, StabilityError)


@pytest.mark.parametrize("value", [None, "true", 1])
def test_opt_in_is_boolean(value):
    service = ApplicationService()
    with pytest.raises(ServiceError):
        service.calculate_stability(service.stability_example_inputs(), allow_reference=value)


def test_work_bound_and_failure_not_success():
    service = ApplicationService()
    inputs = service.stability_example_inputs()
    inputs["settings"]["max_queries"] = 129
    with pytest.raises(bridge.StabilityIntegrationError, match="WORK_BUDGET_EXCEEDED"):
        service.calculate_stability(inputs, allow_reference=True)
    inputs["settings"]["max_queries"] = 1
    with pytest.raises(ServiceError):
        service.calculate_stability(inputs, allow_reference=True)


def test_unsupported_geometry_refused(monkeypatch):
    service = ApplicationService()
    inputs = service.stability_example_inputs()
    # Owner's validated source admission remains authoritative.
    inputs["vessel"]["geometry"]["provider_id"] = "anygeometry_h1"
    with pytest.raises(ServiceError):
        service.calculate_stability(inputs, allow_reference=True)


def test_save_open_no_recalculation(tmp_path, monkeypatch):
    service = ApplicationService()
    inputs = service.stability_example_inputs()
    result = service.calculate_stability(inputs, allow_reference=True)
    folder = tmp_path / "study"
    info = service.save_stability_study(inputs, folder, result=result)
    assert info["result_saved"]
    assert "REFERENCE ONLY" in (folder / "stability-report.md").read_text(encoding="utf-8")
    from anystability import services as owner
    monkeypatch.setattr(owner, "analyze", lambda *a, **k: pytest.fail("load must not solve"))
    loaded_inputs, loaded = service.open_stability_study(folder)
    assert loaded_inputs == inputs
    assert loaded.to_dict() == result.to_dict()
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    assert service.save_stability_study(inputs, folder, result=result) == info
    assert before == {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}


def test_inputs_only_load(tmp_path):
    service = ApplicationService()
    inputs = service.stability_example_inputs()
    assert not service.save_stability_study(inputs, tmp_path)["result_saved"]
    assert service.open_stability_study(tmp_path) == (inputs, None)


def test_changed_inputs_preserved_and_stale_result_refused(tmp_path):
    service = ApplicationService()
    inputs = service.stability_example_inputs()
    result = service.calculate_stability(inputs, allow_reference=True)
    service.save_stability_study(inputs, tmp_path, result=result)
    before = (tmp_path / "project.json").read_bytes()
    inputs["vessel"]["name"] = "changed"
    with pytest.raises(bridge.StabilityIntegrationError, match="SOURCE_STALE"):
        service.save_stability_study(inputs, tmp_path, result=result)
    with pytest.raises(bridge.StabilityIntegrationError, match="STUDY_CONFLICT"):
        service.save_stability_study(inputs, tmp_path)
    assert before == (tmp_path / "project.json").read_bytes()


@pytest.mark.parametrize("artifact", ["receipt", "result"])
def test_corruption_refused(tmp_path, artifact):
    service = ApplicationService()
    inputs = service.stability_example_inputs()
    result = service.calculate_stability(inputs, allow_reference=True)
    info = service.save_stability_study(inputs, tmp_path, result=result)
    target = tmp_path / bridge.RECEIPT if artifact == "receipt" else tmp_path / "objects" / (info["result_object_hash"] + ".json")
    target.write_text("{}", encoding="utf-8")
    with pytest.raises(ServiceError):
        service.open_stability_study(tmp_path)
    assert target.read_text() == "{}"


def test_conflicting_report_no_completed_receipt(tmp_path):
    service = ApplicationService()
    inputs = service.stability_example_inputs()
    service.save_stability_study(inputs, tmp_path)
    (tmp_path / "stability-report.html").write_text("existing evidence")
    result = service.calculate_stability(inputs, allow_reference=True)
    with pytest.raises(ServiceError, match="STUDY_CONFLICT"):
        service.save_stability_study(inputs, tmp_path, result=result)
    assert not (tmp_path / bridge.RECEIPT).exists()
    assert (tmp_path / "stability-report.html").read_text() == "existing evidence"


def test_deep_receipt_refused_with_typed_error(tmp_path):
    service = ApplicationService()
    service.save_stability_study(service.stability_example_inputs(), tmp_path)
    (tmp_path / bridge.RECEIPT).write_text("[" * 5000 + "0" + "]" * 5000)
    with pytest.raises(bridge.StabilityIntegrationError, match="INPUT_INVALID"):
        service.open_stability_study(tmp_path)


@pytest.mark.parametrize("text", ['{"schema_version":1,"schema_version":2}', "NaN", "[" * 5000 + "0" + "]" * 5000])
def test_strict_input_decode(text):
    with pytest.raises(bridge.StabilityIntegrationError, match="INPUT_INVALID"):
        ApplicationService().parse_stability_inputs(text)


def test_unrelated_folder_preserved(tmp_path):
    sentinel = tmp_path / "important.txt"
    sentinel.write_text("preserve")
    with pytest.raises(ServiceError, match="empty folder"):
        ApplicationService().save_stability_study(ApplicationService().stability_example_inputs(), tmp_path)
    assert sentinel.read_text() == "preserve"


def test_missing_optional_owner(monkeypatch):
    original = importlib.import_module
    def missing(name, *a, **k):
        if name.startswith("anystability"):
            raise ModuleNotFoundError("optional owner absent")
        return original(name, *a, **k)
    monkeypatch.setattr(bridge.importlib, "import_module", missing)
    service = ApplicationService()
    assert service.stability_capabilities()["available"] is False
    with pytest.raises(ServiceError, match="Install the tested ANYstability wheel"):
        service.stability_example_inputs()
    assert service.new_project("Still usable").name == "Still usable"


def test_base_cli_does_not_import_optional_owner():
    run = subprocess.run([sys.executable, "-c", "from anymarine.cli import _build_parser; import sys; _build_parser(); assert 'anystability' not in sys.modules; assert 'PySide6' not in sys.modules"],
                         capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr


def test_cli_public_workflow(tmp_path, capsys):
    from anymarine.cli import main
    folder = str(tmp_path / "cli-study")
    assert main(["stability-example", "--path", folder]) == 0
    assert json.loads(capsys.readouterr().out)["result_saved"] is False
    assert main(["run-stability", "--project", folder]) == 1
    assert "CAPABILITY_UNSUPPORTED" in capsys.readouterr().err
    assert not (tmp_path / "cli-study" / bridge.RECEIPT).exists()
    assert main(["run-stability", "--project", folder, "--allow-reference"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["draft_m"] == pytest.approx(438500 / (1025 * 200), abs=1e-6)
    assert output["production_qualified"] is False
