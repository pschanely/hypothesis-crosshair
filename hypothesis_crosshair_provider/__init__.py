import warnings


def _hypothesis_setup_hook(*a, **kw):
    import hypothesis.core

    try:
        # AVAILABLE_PROVIDERS moved as of https://github.com/HypothesisWorks/hypothesis/pull/4254
        from hypothesis.internal.conjecture.providers import AVAILABLE_PROVIDERS
    except ImportError:
        try:
            from hypothesis.internal.conjecture.data import AVAILABLE_PROVIDERS
        except ImportError:
            warnings.warn(
                "This version of hypothesis doesn't support the CrossHair backend"
            )
            return
    AVAILABLE_PROVIDERS[
        "crosshair"
    ] = "hypothesis_crosshair_provider.crosshair_provider.CrossHairPrimitiveProvider"
