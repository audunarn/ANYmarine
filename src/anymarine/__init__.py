"""ANYmarine application foundation.

Qt-free modules: domain, hashing, synthetic, persistence, services, worker,
cli. Qt widgets live in the optional :mod:`anymarine.qtapp` package and are
imported only by GUI entry points.
"""

__version__ = "0.1.0"

RESULT_KIND_SYNTHETIC = "synthetic_unqualified"
RESULT_KIND_LINEAR = "linear_development"
RESULT_KIND_MECHANICAL = "mechanical_development"
RESULT_KIND_EQUILIBRIUM = 'equilibrium_development'
RESULT_KIND_LINE_EQUILIBRIUM = 'line_equilibrium_development'
RESULT_KINDS = (RESULT_KIND_LINE_EQUILIBRIUM, RESULT_KIND_EQUILIBRIUM, RESULT_KIND_SYNTHETIC, RESULT_KIND_LINEAR, RESULT_KIND_MECHANICAL)


def linear_assessment_module():
    """Return the linear assessment module (imported lazily, still Qt-free)."""

    from . import linear_assessment

    return linear_assessment


def paired_refinement_module():
    """Return the paired refinement module (imported lazily, still Qt-free)."""

    from . import paired_refinement

    return paired_refinement


