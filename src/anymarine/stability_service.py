"""Optional ANYstability study bridge; no Qt or hydrostatic implementation.

Studies use the owner's store, separately from an ANYmarine mechanical project.
Only the analytical reference box is admitted by this workbench.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from .services import ServiceError

MAX_QUERIES = 128
RECEIPT = "anymarine-stability-result.json"
RECEIPT_KIND = "anymarine_stability_reference_result"


class StabilityIntegrationError(ServiceError):
    def __init__(self, code, message):
        self.code = str(code)
        super().__init__(f"{self.code}: {message}")


@contextmanager
def _owner():
    try:
        api = SimpleNamespace(**{name: importlib.import_module(f"anystability.{name}")
                                 for name in ("domain", "services", "persistence", "reporting", "errors", "validation")})
    except ImportError as error:
        raise StabilityIntegrationError(
            "PROVIDER_UNAVAILABLE", "Install the tested ANYstability wheel and ANYmarine[stability] "
            "in the same environment to use stability studies.") from error
    try:
        yield api
    except api.errors.StabilityError as error:
        raise StabilityIntegrationError(error.code, str(error)) from error
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise StabilityIntegrationError("STUDY_INVALID", str(error)) from error


def _project(api, inputs):
    project = api.domain.Project.from_dict(inputs)
    if project.vessel.geometry.provider_id != "reference_box_v1":
        raise StabilityIntegrationError("CAPABILITY_UNSUPPORTED",
                                        "This workbench admits only the analytical reference box; "
                                        "certified B1/H1 geometry is unavailable.")
    if project.settings.max_queries > MAX_QUERIES:
        raise StabilityIntegrationError("WORK_BUDGET_EXCEEDED",
                                        f"Workbench max_queries must be at most {MAX_QUERIES}.")
    return project


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


@dataclass(frozen=True)
class StabilityResult:
    """Owner-validated immutable transport; dictionaries returned are fresh copies."""
    payload_json: str

    def __post_init__(self):
        with _owner() as api:
            record = api.services.AnalysisRecord(self.payload_json)
            _project(api, record.project.to_dict())
            if record.to_dict()["output"]["mode"] != "fixed_attitude_vertical":
                raise StabilityIntegrationError("CAPABILITY_UNSUPPORTED", "Expected a fixed-attitude vertical result.")

    def to_dict(self):
        return json.loads(self.payload_json)

    @property
    def markdown(self):
        with _owner() as api:
            record = api.services.AnalysisRecord(self.payload_json)
            return api.reporting.markdown_report(record, current_project=record.project)

    @property
    def html(self):
        with _owner() as api:
            record = api.services.AnalysisRecord(self.payload_json)
            return api.reporting.html_report(record, current_project=record.project)


def capabilities():
    try:
        with _owner() as api:
            return dict(available=True, owner=api.services.capabilities(),
                        consumer_scope="analytical_reference_fixed_attitude_vertical",
                        max_queries=MAX_QUERIES, coupled_to_marine_mechanics=False,
                        production_qualified=False)
    except StabilityIntegrationError as error:
        return dict(available=False, code=error.code, diagnostic=str(error), production_qualified=False)


def example_inputs():
    """Declared full-tank contents as fixed masses, not a tank/free-surface solver.

    Tank steel is included in the existing lightship mass. Contents cover distinct
    components, so the owner's loading-ledger validation catches double counting.
    Dimensions and liquid assumptions travel with each item's source provenance.
    """
    with _owner() as api:
        inputs = api.services.example_project().to_dict()
        tanks = (
            ("ballast-tank-port", (5.0, 2.0, 1.0), (0.0, 3.0, 0.5), 1025.0),
            ("ballast-tank-starboard", (5.0, 2.0, 1.0), (0.0, -3.0, 0.5), 1025.0),
            ("freshwater-tank-forward", (2.0, 2.0, 1.0), (6.0, 0.0, 0.5), 1000.0),
            ("freshwater-tank-aft", (2.0, 2.0, 1.0), (-6.0, 0.0, 0.5), 1000.0),
        )
        for item_id, dimensions, cg, density in tanks:
            capacity = dimensions[0] * dimensions[1] * dimensions[2]
            provenance = dict(kind="declared_reference_full_tank_contents",
                              shape="rectangular", dimensions_m=list(dimensions),
                              capacity_m3=capacity, liquid_density_kg_m3=density,
                              fill_fraction=1.0, cg_model="fixed_at_geometric_center",
                              free_surface_model="not_evaluated", shell_mass="included_in_lightship")
            item = api.domain.MassItem(item_id, capacity * density, cg,
                                       json.dumps(provenance, sort_keys=True), (f"contents:{item_id}",))
            inputs["condition"]["items"].append(item.to_dict())
        inputs["condition"]["condition_id"] = "reference-loaded-full-tanks"
        inputs["condition"]["name"] = "Analytical box with four full tanks (fixed contents masses)"
        return _project(api, inputs).to_dict()


def parse_inputs(text):
    """Bounded strict owner JSON decoding; validates inputs without calculating."""
    with _owner() as api:
        return _project(api, api.validation.decode_json(text.encode("utf-8"))).to_dict()


def calculate(inputs, *, allow_reference=False):
    with _owner() as api:
        project = _project(api, inputs)
        record = api.services.analyze(project, calculation="equilibrium", allow_reference=allow_reference)
        return StabilityResult(record.payload_json)


def _publish(api, path, data):
    """Idempotent exclusive publication; existing different evidence is preserved."""
    try:
        api.persistence.atomic_write(path, data)
    except FileExistsError:
        if api.persistence.read_bytes(path) != data:
            raise StabilityIntegrationError("STUDY_CONFLICT", f"Existing artifact differs: {path}")


def save_study(inputs, path, *, result=None):
    """Publish the receipt last. Refuse changed inputs/results in an existing study."""
    with _owner() as api:
        project = _project(api, inputs)
        record = None
        if result is not None:
            if not isinstance(result, StabilityResult):
                raise StabilityIntegrationError("STUDY_INVALID", "Expected a StabilityResult.")
            record = api.services.AnalysisRecord(result.payload_json)
            if record.is_stale(project):
                raise StabilityIntegrationError("SOURCE_STALE", "Result does not match the study inputs.")
        root = Path(path).absolute()
        if (root / "project.json").exists():
            if api.persistence.load_project(root).identity != project.identity:
                raise StabilityIntegrationError("STUDY_CONFLICT", "Choose a new study folder for changed inputs.")
        else:
            # Do not co-opt a directory holding unrelated evidence.
            if root.exists() and any(root.iterdir()):
                raise StabilityIntegrationError("STUDY_CONFLICT", "New stability study requires an empty folder.")
            api.persistence.save_project(project, root)
        info = dict(path=str(root), input_hash=project.identity, result_saved=False)
        if record is not None:
            object_hash = api.persistence.save_result(record, root)
            receipt = dict(schema_version=1, kind=RECEIPT_KIND, input_hash=project.identity,
                           result_object_hash=object_hash, record_hash=record.identity)
            # Preflight existing receipt before publishing reports.
            receipt_bytes = _json_bytes(receipt)
            if (root / RECEIPT).exists() and api.persistence.read_bytes(root / RECEIPT) != receipt_bytes:
                raise StabilityIntegrationError("STUDY_CONFLICT", "Existing result receipt differs.")
            _publish(api, root / "stability-report.md", result.markdown.encode("utf-8"))
            _publish(api, root / "stability-report.html", result.html.encode("utf-8"))
            _publish(api, root / RECEIPT, receipt_bytes)
            info.update(result_saved=True, result_object_hash=object_hash,
                        report_html=str(root / "stability-report.html"), report_markdown=str(root / "stability-report.md"))
        return info


def open_study(path):
    """Integrity-checked load; opening never executes a numerical calculation."""
    with _owner() as api:
        root = Path(path).absolute()
        project = api.persistence.load_project(root)
        _project(api, project.to_dict())
        result = None
        receipt_path = root / RECEIPT
        if receipt_path.exists():
            receipt = api.validation.decode_json(api.persistence.read_bytes(receipt_path))
            expected = {"schema_version", "kind", "input_hash", "result_object_hash", "record_hash"}
            if (not isinstance(receipt, dict) or set(receipt) != expected or
                    type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1 or
                    receipt["kind"] != RECEIPT_KIND or receipt["input_hash"] != project.identity):
                raise StabilityIntegrationError("STUDY_INVALID", "Invalid or stale stability receipt.")
            record = api.persistence.load_result(root, receipt["result_object_hash"])
            if record.identity != receipt["record_hash"] or record.is_stale(project):
                raise StabilityIntegrationError("SOURCE_STALE", "Stored result identity differs from the study.")
            result = StabilityResult(record.payload_json)
        return project.to_dict(), result
