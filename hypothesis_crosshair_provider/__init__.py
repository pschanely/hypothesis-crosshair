import warnings


def _hypothesis_setup_hook(*a, **kw):
    try:
        import hypothesis.core
        from hypothesis.internal.conjecture.data import AVAILABLE_PROVIDERS
    except ImportError:
        warnings.warn(
            "This version of hypothesis doesn't support the CrossHair backend"
        )
        return
    AVAILABLE_PROVIDERS[
        "crosshair"
    ] = "hypothesis_crosshair_provider.crosshair_provider.CrossHairPrimitiveProvider"
