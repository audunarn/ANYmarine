"""Bounded real-desktop Qt/OpenGL verification for the current ANYmarine source.

Source-consumer smoke evidence supporting W9 only: this tool never claims
whole W9/W10, scientific, installed-ANYmarine or Linux acceptance, and it
starts no numerical job. It reproduces the ``run_app`` startup order
(MainWindow, ``load_example()`` before ``show()``), captures the composed
user-visible window BEFORE any manual renderer action or GL query, proves the
two example bodies are real rendered meshes through the public
``MeshHandle.set_visible`` pixel oracle, exercises the plate surface
selection, the native authoring form and the synthetic stored replay, then
closes only its own window.

Usage (one desktop child at a time, own deadline <= 25 s):

    python tools/verify_qt_desktop.py --source-root C:/Github/ANYmarine \
        --backend default --evidence-dir <unique-attempt-folder>

Exit codes: 0 pass, 1 verification failure or partial run, 2 environment or
usage refusal before Qt. A partial report is always written, even on
exception or deadline, and the own window is closed with live jobs recorded.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
import time
from pathlib import Path

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_REFUSAL = 2

PIXEL_DIFF_THRESHOLD = 8
MIN_CHANGED_BODY_PIXELS = 200
SOFTWARE_GL_MARKERS = (
    "llvmpipe",
    "swiftshader",
    "softpipe",
    "gdi generic",
    "basic render",
    "microsoft basic",
    "software",
)
RECORDED_ENV_KEYS = (
    "QT_QPA_PLATFORM",
    "QT_OPENGL",
    "ANYMARINE_VIEWER_BACKEND",
    "ANY3DVIEW_DISABLE_GPU",
    "ANYMARINE_PYTHON",
)
REFUSED_PLATFORMS = ("offscreen", "minimal")
KEY_SOURCE_MODULES = (
    "qtapp/mainwindow.py",
    "qtapp/member_scene.py",
    "qtapp/morison_authoring.py",
    "qtapp/native_inputs.py",
    "qtapp/projectmodel.py",
    "services.py",
    "domain.py",
    "surface_assignments.py",
)

GL_VENDOR = 0x1F00
GL_RENDERER = 0x1F01
GL_VERSION = 0x1F02


# --------------------------------------------------------------- pure oracles


def physical_crop_rect(dpr: float, x: float, y: float, w: float, h: float):
    """Scale a logical widget rect into physical pixmap pixels.

    ``QWidget.grab()`` returns a pixmap whose ``devicePixelRatio`` may exceed
    1 on high-DPI desktops; cropping with logical coordinates would silently
    read the wrong region and report dimensions that do not match the saved
    pixel image.
    """
    if dpr <= 0:
        raise ValueError("devicePixelRatio must be positive")
    return (round(x * dpr), round(y * dpr), round(w * dpr), round(h * dpr))


def count_changed_pixels(image_a, image_b, threshold: int = PIXEL_DIFF_THRESHOLD) -> int:
    """Count pixels whose RGBA channels differ by more than ``threshold``."""
    import numpy as np

    if image_a.size != image_b.size:
        raise ValueError(f"image sizes differ: {image_a.size} vs {image_b.size}")
    a = np.asarray(image_a.convert("RGBA"), dtype=np.int16)
    b = np.asarray(image_b.convert("RGBA"), dtype=np.int16)
    return int(np.count_nonzero((np.abs(a - b) > threshold).any(axis=2)))


def is_software_gl_renderer(vendor: str, renderer: str) -> bool:
    """True when the GL strings name a software driver, not hardware."""
    text = f"{vendor} {renderer}".casefold()
    return any(marker in text for marker in SOFTWARE_GL_MARKERS)


def decode_gl_string(raw) -> str:
    if not raw:
        return ""
    if isinstance(raw, bytes):
        return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
    return str(raw).split("\x00", 1)[0]


def gl_string_bytes(raw):
    """Normalize a public glGetString result to bytes for decoding.

    Qt's public QOpenGLFunctions.glGetString returns str on PySide6
    (bytes on other bindings); both decode through the same path
    without loading any platform-specific system GL library
    directly, so the same-viewport query works on Windows and
    Linux/WSLg desktops alike.
    """
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        return raw.encode("utf-8", "replace")
    return bytes(raw)


def environment_problems(env) -> list[str]:
    """Refuse configured overrides that would fake a desktop claim."""
    problems = []
    platform = (env.get("QT_QPA_PLATFORM") or "").casefold()
    if platform in REFUSED_PLATFORMS:
        problems.append(
            f"QT_QPA_PLATFORM={platform} is not a real desktop platform; "
            "refusing to run a desktop verification behind it"
        )
    return problems


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def scene_content_summary(image) -> dict:
    """Quantify rendered scene content: non-background pixels and bbox.

    Background is the top-left corner pixel; this characterizes framing
    anomalies (for example a scene squashed against the viewport edge)
    without embedding image data in the report.
    """
    import numpy as np

    a = np.asarray(image.convert("RGBA"), dtype=np.int16)
    background = a[0, 0]
    mask = (np.abs(a - background) > PIXEL_DIFF_THRESHOLD).any(axis=2)
    count = int(mask.sum())
    if not count:
        return {"non_background_pixels": 0, "bbox": None}
    ys, xs = np.nonzero(mask)
    return {
        "non_background_pixels": count,
        "bbox": [int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())],
        "size": [int(a.shape[1]), int(a.shape[0])],
    }


def installed_origin_guard(anymarine) -> dict:
    """Refuse checkout or editable origins in installed mode.

    Installed mode only counts when the imported anymarine really is an
    installed distribution: a checkout path, a source ``src`` layout or an
    editable finder would silently turn the run into source evidence.
    """
    module_path = Path(anymarine.__file__).resolve()
    text = str(module_path).replace("\\", "/").casefold()
    problems = []
    if text.startswith(("c:/github", "/mnt/c/github")):
        problems.append(f"anymarine imported from checkout: {module_path}")
    if module_path.parent.parent.name == "src":
        problems.append(f"anymarine imported from a source layout: {module_path}")
    editable = [
        finder.__class__.__name__ for finder in sys.meta_path
        if "editable" in finder.__class__.__name__.casefold()
    ]
    if editable:
        problems.append(f"editable import finders present: {editable}")
    if problems:
        raise RuntimeError("; ".join(problems))
    return {"module": str(module_path), "problems": []}


def distribution_origin(name: str) -> dict:
    """Exact installed distribution metadata for a dependency."""
    try:
        distribution = importlib.metadata.distribution(name)
        return {
            "version": distribution.version,
            "path": str(getattr(distribution, "_path", "")),
        }
    except Exception as error:  # unavailable metadata is evidence, not a crash
        return {"error": f"{type(error).__name__}: {error}"}


# ------------------------------------------------------------------- report


class Report:
    """Incremental evidence report that survives exceptions and deadlines."""

    def __init__(self, evidence_dir: Path, mode: dict):
        self.dir = evidence_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.data = {
            "tool": "tools/verify_qt_desktop.py",
            "scope": (
                ("installed-consumer desktop smoke evidence only; "
                 "dependency reuse: PySide6/ANY3dView/numpy/scipy come "
                 "from explicit donor dependency directories and the "
                 "ecosystem wheels installed in the target, not a "
                 "clean full dependency graph; not whole W9/W10, "
                 "scientific, full-release or platform acceptance")
                if mode.get("identity_mode") == "installed" else
                ("source-consumer desktop smoke evidence only; not whole "
                 "W9/W10, scientific, installed-ANYmarine or Linux "
                 "acceptance")
            ),
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "phases": {},
            "gates": [],
            "warnings": [],
            "artifacts": {},
            "error": None,
            "deadline_seconds": mode.get("deadline"),
        }
        self.data.update(mode)

    def phase(self, name: str, status: str, **details) -> None:
        self.data["phases"].setdefault(name, {})["status"] = status
        if details:
            self.data["phases"][name].update(details)

    def gate(self, name: str, passed: bool, **details) -> bool:
        entry = {"gate": name, "passed": bool(passed)}
        entry.update(details)
        self.data["gates"].append(entry)
        return passed

    def artifact(self, name: str, path) -> None:
        self.data["artifacts"][name] = str(path)

    def save(self) -> Path:
        self.data["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        passed = all(g["passed"] for g in self.data["gates"]) and not self.data["error"]
        self.data["result"] = "pass" if passed else "fail"
        path = self.dir / "report.json"
        path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        lines = [
            "# Qt desktop verification report",
            "",
            f"Result: **{self.data['result']}**",
            "",
            f"Scope: {self.data['scope']}",
            "",
            "| gate | passed | detail |",
            "| --- | --- | --- |",
        ]
        for gate in self.data["gates"]:
            detail = json.dumps({k: v for k, v in gate.items() if k not in ("gate", "passed")})
            lines.append(f"| {gate['gate']} | {gate['passed']} | {detail} |")
        if self.data["error"]:
            lines += ["", f"Error: `{self.data['error']}`", ""]
        lines += ["", "Artifacts:", ""]
        lines += [f"- {name}: `{path}`" for name, path in self.data["artifacts"].items()]
        (self.dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


# --------------------------------------------------------------------- tool


class DeadlineReached(RuntimeError):
    pass


def find_thin_sheet_surface(model, project):
    """Match a tree identity to an actual neutral thin-sheet record."""
    assignments = {(body.body_id, item.semantics.surface_id): item
                   for body in project.bodies for item in body.hydro_surfaces
                   if item.semantics.surface_kind == "thin_sheet"}
    for section_row in range(model.rowCount()):
        section = model.index(section_row, 0)
        for body_row in range(model.rowCount(section)):
            body = model.index(body_row, 0, section)
            for surface_row in range(model.rowCount(body)):
                index = model.index(surface_row, 0, body)
                node = model.node_for_index(index)
                if node is not None and node.kind == "surface":
                    item = assignments.get(tuple(node.payload))
                    if item is not None:
                        return index, item
    return None, None


class _QtRGBAImage:
    """PIL-free RGBA image stand-in used when Pillow is unavailable.

    Supports exactly the operations the verifier needs: a size tuple,
    convert("RGBA"), Qt-native PNG save and numpy array conversion with the
    same RGBA byte layout Pillow would produce.
    """

    def __init__(self, qimage):
        self._qimage = qimage
        self.size = (qimage.width(), qimage.height())

    def convert(self, mode):
        if mode != "RGBA":
            raise ValueError(
                "only RGBA conversion is supported without Pillow")
        return self

    def save(self, path):
        if not self._qimage.save(str(path)):
            raise OSError(f"failed to save capture {path}")

    def __array__(self, dtype=None):
        import numpy as np
        from PySide6.QtGui import QImage

        image = self._qimage.convertToFormat(QImage.Format.Format_RGBA8888)
        height, width = image.height(), image.width()
        data = np.frombuffer(image.constBits(), dtype=np.uint8)
        data = data.reshape(height, image.bytesPerLine())[:, : width * 4]
        data = data.reshape(height, width, 4)
        return data.astype(dtype) if dtype is not None else data


class DesktopVerifier:
    def __init__(self, args):
        self.args = args
        self.deadline = time.monotonic() + args.deadline
        self.report = Report(
            Path(args.evidence_dir),
            {
                "mode": args.backend,
                "source_root": args.source_root,
                "identity_mode": "source" if args.source_root else "installed",
                "deadline": args.deadline,
                "inherited_environment": {k: os.environ.get(k) for k in RECORDED_ENV_KEYS},
            },
        )
        self.app = None
        self.window = None
        self.render_errors = []

    # -- plumbing
    def _tick(self, phase: str) -> None:
        if time.monotonic() > self.deadline:
            raise DeadlineReached(f"deadline reached in phase {phase}")
        if self.app is not None:
            self.app.processEvents()

    def _wait(self, condition, phase: str, timeout: float = 8.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < min(end, self.deadline):
            self.app.processEvents()
            if condition():
                return True
            time.sleep(0.02)
        return condition()

    def _save_image(self, image, name: str):
        path = self.report.dir / name
        image.save(path)
        self.report.artifact(name, path)
        return path

    # -- composed user-visible capture (full window grab + DPR-aware crop)
    def _composed_capture(self, prefix: str):
        from PySide6.QtCore import QPoint

        pixmap = self.window.grab()
        dpr = pixmap.devicePixelRatio()
        viewer = self.window.viewer
        top_left = viewer.mapTo(self.window, QPoint(0, 0))
        size = viewer.size()
        x, y, w, h = physical_crop_rect(dpr, top_left.x(), top_left.y(), size.width(), size.height())
        x = max(0, min(x, pixmap.width() - 1))
        y = max(0, min(y, pixmap.height() - 1))
        w = max(1, min(w, pixmap.width() - x))
        h = max(1, min(h, pixmap.height() - y))
        crop = pixmap.copy(x, y, w, h)
        full_path = self.report.dir / f"{prefix}-window.png"
        pixmap.save(str(full_path))
        self.report.artifact(f"{prefix}-window.png", full_path)
        image = self._qimage_to_pil(crop.toImage())
        crop_path = self._save_image(image, f"{prefix}-viewer.png")
        return {
            "pixmap_dpr": dpr,
            "window_logical_size": [self.window.width(), self.window.height()],
            "window_pixel_size": [pixmap.width(), pixmap.height()],
            "viewer_logical_rect": [top_left.x(), top_left.y(), size.width(), size.height()],
            "viewer_pixel_size": [image.size[0], image.size[1]],
            "crop_path": str(crop_path),
            "image": image,
        }

    @staticmethod
    def _qimage_to_pil(qimage):
        try:
            from PIL import Image
        except ImportError:
            # Pillow is an optional ANY3dView extra and may be absent from
            # the borrowed runtime; fall back to a Qt-native RGBA image with
            # exactly the operations the verifier needs (size, convert,
            # save, numpy conversion). Pixel data is identical.
            return _QtRGBAImage(qimage)
        from PySide6.QtGui import QImage

        image = qimage.convertToFormat(QImage.Format.Format_RGBA8888)
        return Image.frombytes(
            "RGBA",
            (image.width(), image.height()),
            bytes(image.constBits()),
            "raw",
            "RGBA",
            image.bytesPerLine(),
        )

    def _stable_capture(self, prefix: str, attempts: int = 6):
        """Capture the composed viewer twice and require a stable frame."""
        capture = self._composed_capture(prefix)
        stable = False
        changed = None
        for _ in range(attempts):
            self._tick("capture")
            again = self._composed_capture(f"{prefix}-stability")
            changed = count_changed_pixels(capture["image"], again["image"])
            if changed == 0:
                stable = True
                break
            capture = again
        capture["stable"] = stable
        capture["stability_changed_pixels"] = changed
        return capture

    # -- phases
    def record_identity(self) -> None:
        import anymarine
        import any3dview
        import PySide6

        identity = {
            "python": sys.version.split(" ")[0],
            "python_executable": sys.executable,
            "pyside6": {"version": PySide6.__version__, "module": PySide6.__file__},
            "any3dview": {
                "version": importlib.metadata.version("any3dview"),
                "module": any3dview.__file__,
                "distribution": distribution_origin("any3dview"),
            },
            "anymarine": {"module": anymarine.__file__},
            "anyhydro": distribution_origin("anyhydro"),
        }
        if self.args.source_root:
            source_root = Path(self.args.source_root).resolve()
            package_root = Path(anymarine.__file__).resolve().parent.parent
            if package_root != source_root / "src":
                raise RuntimeError(
                    f"imported anymarine {anymarine.__file__} is not the requested "
                    f"source root {source_root}; refusing to label it current source"
                )
            identity["source_sha256"] = {
                module: sha256_file(Path(anymarine.__file__).parent / module)
                for module in KEY_SOURCE_MODULES
            }
        else:
            identity["installed_origin_guard"] = installed_origin_guard(anymarine)
        self.report.data["identity"] = identity
        self.report.phase("identity", "ok")

    def build_window(self) -> None:
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication(sys.argv)
        platform = self.app.platformName()
        self.report.data["qt_platform"] = platform
        if platform.casefold() in REFUSED_PLATFORMS:
            raise RuntimeError(f"QApplication platform is {platform}, not a real desktop")
        from anymarine.qtapp.mainwindow import MainWindow

        backend = {"default": None, "gpu": "gpu", "software": "software"}[self.args.backend]
        self.window = MainWindow(viewer_backend=backend)
        self.window._notify_warning = self._record_warning
        self.window._notify_critical = self._record_warning
        self.window._notify_info = self._record_info
        self.window._confirm_discard = self._confirm_discard
        self.window.viewer.render_error.connect(self._record_render_error)
        self.window.load_example()  # run_app order: load BEFORE show
        self.window.show()
        self._wait(
            lambda: self.window.windowHandle() is not None and self.window.windowHandle().isExposed(),
            "expose",
        )
        ready = self._wait(
            lambda: not getattr(self.window, "_startup_renderer_pending", False), "renderer"
        )
        # The automatic startup swap replaces the viewer; follow the live one.
        self.window.viewer.render_error.connect(self._record_render_error)
        viewer = self.window.viewer
        self.report.phase(
            "startup",
            "ok" if ready else "timeout",
            requested_backend=self.window._requested_viewer_backend,
            effective_backend=viewer.backend_name,
            capabilities_gpu=viewer.capabilities.gpu,
            backend_diagnostics=list(viewer.backend_diagnostics),
            viewport_size=list(viewer.viewport_size),
            render_errors=list(self.render_errors),
        )
        self.report.gate("renderer_ready", ready)
        # Responsive layout: request the QA window size and prove the
        # shell honours it instead of growing past the request.
        requested = (self.args.window_width, self.args.window_height)
        self.window.resize(*requested)
        for _ in range(5):
            self._tick("window-size")
        actual = (self.window.width(), self.window.height())
        minimum = self.window.minimumSizeHint()
        self.report.data["requested_window_size"] = list(requested)
        self.report.data["actual_window_size"] = list(actual)
        self.report.data["window_minimum_size_hint"] = [minimum.width(), minimum.height()]
        self.report.gate(
            "window_within_requested",
            actual[0] <= requested[0] and actual[1] <= requested[1],
            requested=list(requested), actual=list(actual),
        )
        self.report.gate(
            "responsive_1280x720_limit",
            minimum.width() <= 1280 and minimum.height() <= 720,
            minimum_size_hint=[minimum.width(), minimum.height()],
        )
        tree = self.window._tree
        self.report.gate(
            "project_tree_usable",
            tree.width() >= 150 and tree.height() >= 120,
            tree_size=[tree.width(), tree.height()],
        )

    def _record_warning(self, title: str, text: str) -> None:
        self.report.data["warnings"].append({"title": title, "text": text})

    def _record_info(self, title: str, text: str) -> None:
        self.report.data.setdefault("infos", []).append({"title": title, "text": text})

    def _confirm_discard(self) -> bool:
        self.report.data.setdefault("discard_confirmations", []).append(True)
        return True

    def _record_render_error(self, message: str) -> None:
        self.render_errors.append(message)

    def capture_startup(self) -> None:
        """Composed captures BEFORE any GL query or manual renderer use.

        The first frame is preserved as exposed, then the automatic startup
        fit is allowed to settle (no manual renderer action) and the settled
        automatic startup view is captured. Both precede the GL query.
        """
        self._composed_capture("startup-firstframe")
        self._wait(
            lambda: not getattr(self.window, "_fit_pending", False), "fit", timeout=5.0
        )
        for _ in range(5):
            self._tick("fit")
        capture = self._stable_capture("startup")
        summary = scene_content_summary(capture["image"])
        self.report.phase(
            "startup_capture",
            "ok",
            **{k: capture[k] for k in (
                "pixmap_dpr", "window_logical_size", "window_pixel_size",
                "viewer_logical_rect", "viewer_pixel_size", "stable",
                "stability_changed_pixels")}
        )
        self.report.data["startup_scene_content"] = summary
        if summary["bbox"] and summary["bbox"][1] < 0.25 * capture["viewer_pixel_size"][0]:
            self.report.data.setdefault("findings", []).append(
                {
                    "finding": "startup_scene_framing_anomaly",
                    "detail": (
                        "settled automatic startup view renders the scene "
                        "squashed against the viewport edge; see startup PNGs"
                    ),
                    "scene_content": summary,
                }
            )
        self.report.gate(
            "startup_composed_capture",
            capture["stable"] and capture["viewer_pixel_size"][0] >= 480
            and capture["viewer_pixel_size"][1] >= 300,
            viewer_pixel_size=capture["viewer_pixel_size"],
            stable=capture["stable"],
        )
        framebuffer = self.window.viewer.capture_image()
        self._save_image(framebuffer, "startup-framebuffer.png")
        self.report.data["startup_framebuffer_size"] = list(framebuffer.size)

    def query_gl(self) -> None:
        """Query the actual same-viewport Qt GL context (public interfaces)."""
        viewer = self.window.viewer
        if viewer.backend_name != "gpu":
            self.report.phase(
                "gl_query", "skipped", reason=f"effective backend is {viewer.backend_name}"
            )
            if self.args.backend == "gpu":
                self.report.gate("hardware_gpu_backend", False,
                                 effective_backend=viewer.backend_name)
            return
        viewer.make_current()
        context = viewer.canvas.context()
        fmt = context.format()
        functions = context.functions()
        vendor = decode_gl_string(gl_string_bytes(functions.glGetString(GL_VENDOR)))
        renderer = decode_gl_string(gl_string_bytes(functions.glGetString(GL_RENDERER)))
        version = decode_gl_string(gl_string_bytes(functions.glGetString(GL_VERSION)))
        software = is_software_gl_renderer(vendor, renderer)
        profile = getattr(fmt.profile(), "name", None) or str(fmt.profile())
        self.report.phase(
            "gl_query",
            "ok",
            context_valid=context.isValid(),
            requested_version=[fmt.majorVersion(), fmt.minorVersion()],
            profile=profile,
            vendor=vendor,
            renderer=renderer,
            version=version,
            software_driver=software,
        )
        if self.args.backend in ("gpu", "default"):
            self.report.gate(
                "hardware_gpu_backend",
                not software and bool(vendor) and bool(renderer),
                vendor=vendor,
                renderer=renderer,
                version=version,
            )

    def pixel_oracle(self) -> None:
        """Per-body >=200 changed pixels at identical camera via set_visible."""
        baseline = self._stable_capture("oracle-baseline")
        bodies = {}
        ok = True
        for body_id, handle in list(self.window._handles.items()):
            self._tick("pixel_oracle")
            handle.set_visible(False)
            self.window.viewer.redraw()
            self._tick("pixel_oracle")
            hidden = self._composed_capture(f"oracle-hidden-{body_id}")
            handle.set_visible(True)
            self.window.viewer.redraw()
            self._tick("pixel_oracle")
            changed = count_changed_pixels(baseline["image"], hidden["image"])
            bodies[body_id] = {
                "changed_pixels": changed,
                "hidden_png": hidden["crop_path"],
                "restored_visible": handle.visible,
            }
            self._save_image(hidden["image"], f"oracle-hidden-{body_id}.png")
            ok = ok and changed >= MIN_CHANGED_BODY_PIXELS and handle.visible
        self.report.phase("pixel_oracle", "ok" if ok else "fail", bodies=bodies)
        self.report.gate(
            "per_body_changed_pixels",
            ok and len(bodies) >= 2,
            bodies={k: v["changed_pixels"] for k, v in bodies.items()},
            minimum=MIN_CHANGED_BODY_PIXELS,
        )

    def reload_example(self) -> None:
        self.window.load_example()
        self._tick("reload")
        capture = self._composed_capture("reload")
        body_ids = sorted(self.window._handles)
        self.report.phase("reload", "ok", body_ids=body_ids,
                          viewer_pixel_size=capture["viewer_pixel_size"])
        self.report.gate("reload_bodies", len(body_ids) >= 2, body_ids=body_ids)

    def select_plate_surface(self) -> None:
        self.window.load_plate_example()
        self._tick("plates")
        model = self.window._tree_model
        surface_index, assignment = find_thin_sheet_surface(model, self.window._project)
        if surface_index is None:
            self.report.phase("plates", "fail", reason="no neutral thin-sheet surface in tree")
            self.report.gate("plate_surface_selection", False)
            return
        self.window._on_tree_clicked(surface_index)
        self._tick("plates")
        capture = self._composed_capture("plates")
        node = model.node_for_index(surface_index)
        semantics = assignment.semantics
        form = self.window._details._surface_form
        displayed = {"surface_id": form._surface_id, "body_id": form._body_id,
                     "kind": form._kind, "normal": form._normal.text(),
                     "minus_side": form._minus_side.text(),
                     "plus_side": form._plus_side.text()}
        matches = (displayed["surface_id"] == semantics.surface_id
                   and displayed["body_id"] == semantics.owning_body_id
                   and displayed["kind"] == "thin_sheet"
                   and displayed["normal"] == semantics.normal_convention
                   and displayed["minus_side"] == semantics.minus_side
                   and displayed["plus_side"] == semantics.plus_side
                   and bool(semantics.minus_side) and bool(semantics.plus_side)
                   and semantics.minus_side != semantics.plus_side)
        details = dict(selected=node.payload, surface_kind=semantics.surface_kind,
                       displayed=displayed,
                       highlight=sorted(self.window.viewer.highlighted_tags()),
                       viewer_pixel_size=capture["viewer_pixel_size"])
        self.report.phase("plates", "ok" if matches else "fail", **details)
        self.report.gate("plate_surface_selection",
                         matches and bool(self.window.viewer.highlighted_tags()), **details)
        # Long surface metadata must stay reachable by scrolling inside
        # the Details dock instead of being clipped at the requested size.
        from PySide6.QtWidgets import QScrollArea

        page = self.window._details._stack.currentWidget()
        form = self.window._details._surface_form
        scrollable = isinstance(page, QScrollArea)
        if scrollable:
            reachable = (
                form.minimumSizeHint().width()
                <= page.viewport().width() + page.horizontalScrollBar().maximum()
                and form.minimumSizeHint().height()
                <= page.viewport().height() + page.verticalScrollBar().maximum()
            )
        else:
            reachable = False
        self.report.gate(
            "details_metadata_scrollable",
            scrollable and reachable,
            page_type=type(page).__name__,
            form_minimum_size_hint=[
                form.minimumSizeHint().width(), form.minimumSizeHint().height()],
            viewport_size=[page.viewport().width(), page.viewport().height()]
            if scrollable else None,
        )

        # Actual user scrolling is the accessibility oracle; Qt's
        # ensureWidgetVisible can misplace controls after shared test styling.
        access = {}
        if scrollable:
            for name, field in (("plus_side", form._plus_side), ("apply", form._apply)):
                position = field.mapTo(form, field.rect().center())
                page.horizontalScrollBar().setValue(
                    position.x() - page.viewport().width() // 2)
                page.verticalScrollBar().setValue(
                    position.y() - page.viewport().height() // 2)
                for _ in range(5):
                    self._tick("details-scroll")
                center = field.mapTo(page.viewport(), field.rect().center())
                access[name] = page.viewport().rect().contains(center)
                self._composed_capture(f"plates-scroll-{name}")
        self.report.gate("details_controls_reachable",
                         bool(access) and all(access.values()), controls=access)

    def native_authoring_form(self) -> None:
        self.window.load_native_example()
        self._tick("native")
        from anymarine.qtapp.native_inputs import NativeInputsDialog

        dialog = NativeInputsDialog(self.window._project, parent=self.window)
        dialog.show()
        self._tick("native")
        capture = self._composed_capture("native-authoring")
        # A top-level dialog is a separate window; grab it explicitly so the
        # authoring form itself is preserved as evidence.
        dialog_pixmap = dialog.grab()
        dialog_path = self.report.dir / "native-authoring-dialog.png"
        dialog_pixmap.save(str(dialog_path))
        self.report.artifact("native-authoring-dialog.png", dialog_path)
        dialog.close()
        body_ids = sorted(self.window._handles)
        self.report.phase(
            "native_authoring", "ok",
            dialog_title=dialog.windowTitle(),
            dialog_pixel_size=[dialog_pixmap.width(), dialog_pixmap.height()],
            body_ids=body_ids,
            viewer_pixel_size=capture["viewer_pixel_size"],
        )
        self.report.gate(
            "native_authoring_form",
            dialog.isVisible() is False
            and dialog_pixmap.width() > 0 and len(body_ids) >= 2,
            body_ids=body_ids,
        )

    def synthetic_replay(self) -> None:
        self.window.load_example()
        self._tick("replay")
        result = self.window._project.results[0]
        handles_before = dict(self.window._handles)
        self.window._set_result(result)
        frame = min(result.frame_count() - 1, result.frame_count() // 2)
        self.window._on_slider_moved(frame)
        self._tick("replay")
        capture = self._composed_capture("replay")
        retained = all(
            self.window._handles.get(body_id) is handle
            for body_id, handle in handles_before.items()
        )
        import numpy as np

        matches = {}
        for body_id, handle in self.window._handles.items():
            expected = np.asarray(result.positions[body_id][frame])
            actual = np.asarray(handle.transform)[:3, 3]
            matches[body_id] = bool(np.allclose(expected, actual, atol=1e-9))
        self.report.phase(
            "replay", "ok",
            result_kind=result.kind,
            frame=frame,
            frame_count=result.frame_count(),
            handles_retained=retained,
            positions_match=matches,
            viewer_pixel_size=capture["viewer_pixel_size"],
        )
        self.report.gate(
            "synthetic_replay_pose",
            retained and all(matches.values()),
            frame=frame,
            positions_match=matches,
        )

    def close_own_window(self) -> None:
        active = self.window._job_manager.active_jobs() if self.window else []
        self.report.data["live_jobs_at_close"] = list(active)
        self.report.data["diagnostics_text"] = (
            self.window._diagnostics.toPlainText() if self.window else ""
        )
        self.report.gate("live_jobs_zero", not active, active_jobs=list(active))
        if self.window is not None and self.window.isVisible():
            self.window.close()
            self._tick("close")
        closed = self.window is None or not self.window.isVisible()
        self.report.phase("closure", "ok" if closed else "fail")
        self.report.gate("own_window_closed", closed)
        self.report.gate(
            "no_modal_warnings",
            not self.report.data["warnings"],
            warnings=self.report.data["warnings"],
        )

    def run(self) -> int:
        try:
            problems = environment_problems(os.environ)
            if problems:
                self.report.data["error"] = "; ".join(problems)
                return EXIT_REFUSAL
            if self.args.source_root:
                source = str(Path(self.args.source_root).resolve() / "src")
                if source not in sys.path:
                    sys.path.insert(0, source)
            self.record_identity()
            self.build_window()
            self.capture_startup()
            self.query_gl()
            self.pixel_oracle()
            self.reload_example()
            self.select_plate_surface()
            self.native_authoring_form()
            self.synthetic_replay()
            self.close_own_window()
        except DeadlineReached as error:
            self.report.data["error"] = str(error)
        except Exception as error:  # partial report survives any exception
            self.report.data["error"] = f"{type(error).__name__}: {error}"
            import traceback

            (self.report.dir / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
            try:
                self.close_own_window()
            except Exception:
                pass
        finally:
            try:
                if self.window is not None and self.window.isVisible():
                    self.window.close()
            except Exception:
                pass
            path = self.report.save()
            print(json.dumps({
                "result": self.report.data["result"],
                "mode": self.args.backend,
                "report": str(path),
                "error": self.report.data["error"],
            }))
        passed = self.report.data["result"] == "pass"
        return EXIT_PASS if passed else EXIT_FAIL


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source-root",
        help="ANYmarine source root; omitted means installed mode (no "
        "installed-marine qualification is claimed)",
    )
    parser.add_argument(
        "--backend", choices=("default", "gpu", "software"), default="default",
        help="default inherits the actual configured backend (no manual "
        "renderer change); gpu/software are explicit separate modes",
    )
    parser.add_argument("--evidence-dir", required=True,
                        help="unique attempt evidence folder for PNGs and reports")
    parser.add_argument("--deadline", type=float, default=25.0,
                        help="own wall-clock deadline in seconds (default 25)")
    parser.add_argument(
        "--window-width", type=int, default=1280,
        help="requested logical window width (default 1280; QA default "
             "dimensions stay 1280x850)")
    parser.add_argument(
        "--window-height", type=int, default=850,
        help="requested logical window height (default 850)")
    args = parser.parse_args(argv)
    return DesktopVerifier(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
