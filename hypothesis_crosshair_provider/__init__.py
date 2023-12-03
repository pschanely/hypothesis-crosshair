def _hypothesis_setup_hook(*a, **kw):
    try:
        from hypothesis.internal.conjecture.data import register_new_backend
    except ImportError:
        return
    register_new_backend(
        "crosshair",
        "hypothesis_crosshair_provider.crosshair_provider.CrossHairPrimitiveProvider",
    )
