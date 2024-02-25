import warnings


def _hypothesis_setup_hook(*a, **kw):
    try:
        import hypothesis.core
        from hypothesis.internal.conjecture.data import AVAILABLE_PROVIDERS
    except ImportError:
        warnings.warn(
            f"This version of hypothesis doesn't support the CrossHair backend"
        )
        return
    if not hasattr(
        hypothesis.core, "hacky_patchable_run_context_yielding_per_test_case_context"
    ):
        warnings.warn(
            f"This version of hypothesis doesn't support the CrossHair backend"
        )
        return
    AVAILABLE_PROVIDERS[
        "crosshair"
    ] = "hypothesis_crosshair_provider.crosshair_provider.CrossHairPrimitiveProvider"
    from hypothesis_crosshair_provider.crosshair_provider import \
        hacky_patchable_run_context_yielding_per_test_case_context

    hypothesis.core.hacky_patchable_run_context_yielding_per_test_case_context = (
        hacky_patchable_run_context_yielding_per_test_case_context
    )
