import math
import sys
from contextlib import ExitStack, contextmanager
from time import monotonic
from typing import Any, Optional, Sequence

import crosshair.core_and_libs  # Needed for patch registrations
from crosshair import debug, deep_realize
from crosshair.core import (COMPOSITE_TRACER, DEFAULT_OPTIONS,
                            AnalysisOptionSet, CallAnalysis, IgnoreAttempt,
                            NoTracing, Patched, RootNode, StateSpace,
                            StateSpaceContext, UnexploredPath,
                            VerificationStatus, condition_parser,
                            context_statespace, is_tracing, proxy_for_type)
from crosshair.libimpl.builtinslib import (LazyIntSymbolicStr,
                                           SymbolicBoundedIntTuple)
from crosshair.util import set_debug, test_stack
from hypothesis.internal.conjecture.data import PrimitiveProvider
from hypothesis.internal.intervalsets import IntervalSet

_PREVIOUS_REALIZED_DRAWS = None


@contextmanager
def hacky_patchable_run_context_yielding_per_test_case_context():

    # TODO: detect whether this specific test is supposed to use the
    # crosshair backend and return nullcontext if it isn't.
    # Checking settings.default.backend doesn't appear to reflect decorated settings:
    # if hypothesis.settings.default.backend != "crosshair":
    #     yield nullcontext
    #     return

    if "-v" in sys.argv or "-vv" in sys.argv:
        set_debug(True)
    search_root = RootNode()

    @contextmanager
    def single_execution_context() -> Any:
        nonlocal search_root
        if search_root.child.is_exhausted():
            debug("Resetting search root")
            # might be nice to signal that we're done somehow.
            # But for now, just start over!
            search_root = RootNode()
        global _PREVIOUS_REALIZED_DRAWS
        _PREVIOUS_REALIZED_DRAWS = None
        iter_start = monotonic()
        options = DEFAULT_OPTIONS.overlay(AnalysisOptionSet(analysis_kind=[]))
        per_path_timeout = options.get_per_path_timeout()  # TODO: how to set this?
        space = StateSpace(
            execution_deadline=iter_start + per_path_timeout,
            model_check_timeout=per_path_timeout / 2,
            search_root=search_root,
        )
        space._hypothesis_draws = []  # keep a log of drawn values
        try:
            with (
                condition_parser([]),
                Patched(),
                StateSpaceContext(space),
                COMPOSITE_TRACER,
            ):
                try:
                    debug("start iter")
                    try:
                        yield
                    finally:
                        any_choices_made = bool(space.choices_made)
                        if any_choices_made:
                            space.detach_path()
                            _PREVIOUS_REALIZED_DRAWS = {
                                id(symbolic): deep_realize(symbolic)
                                for symbolic in space._hypothesis_draws
                            }
                        else:
                            # TODO: I can't detach_path here because it will conflict with the
                            # top node of a prior "real" execution.
                            # Should I just generate a dummy concrete value for each of the draws?
                            _PREVIOUS_REALIZED_DRAWS = {}
                    debug("end iter (normal)")
                except Exception as exc:
                    try:
                        exc.args = deep_realize(exc.args)
                    except Exception:
                        exc.args = ()
                    debug(
                        f"end iter ({type(exc)} exception)",
                        test_stack(exc.__traceback__),
                    )
                    raise exc
        except (IgnoreAttempt, UnexploredPath):
            pass
        finally:
            if any_choices_made:
                debug("bubbling status")
                _analysis, _exhausted = space.bubble_status(
                    CallAnalysis(VerificationStatus.CONFIRMED)
                )
            else:
                debug("no decisions made; ignoring this iteration")

    yield single_execution_context


class CrossHairPrimitiveProvider(PrimitiveProvider):
    """An implementation of PrimitiveProvider based on CrossHair."""

    def __init__(self, conjecturedata: object, /) -> None:
        self.name_id = 0
        self.current_exit_stack: Optional[ExitStack] = None

    def _next_name(self, prefix: str) -> str:
        self.name_id += 1
        return f"{prefix}_{self.name_id:02d}"

    def _remember_draw(self, symbolic):
        context_statespace()._hypothesis_draws.append(symbolic)

    def draw_boolean(self, p: float = 0.5, *, forced: Optional[bool] = None) -> bool:
        if forced is not None:
            return forced

        symbolic = proxy_for_type(bool, self._next_name("bool"), allow_subtypes=False)
        self._remember_draw(symbolic)
        return symbolic

    def draw_integer(
        self,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
        *,
        # weights are for choosing an element index from a bounded range
        weights: Optional[Sequence[float]] = None,
        shrink_towards: int = 0,
        forced: Optional[int] = None,
    ) -> int:
        if forced is not None:
            return forced
        symbolic = proxy_for_type(int, self._next_name("int"), allow_subtypes=False)
        conditions = []
        if min_value is not None:
            conditions.append(min_value <= symbolic)
        if max_value is not None:
            conditions.append(symbolic <= max_value)
        if not all(conditions):
            raise IgnoreAttempt
        self._remember_draw(symbolic)
        return symbolic

    def draw_float(
        self,
        *,
        min_value: float = -math.inf,
        max_value: float = math.inf,
        allow_nan: bool = True,
        smallest_nonzero_magnitude: Optional[float] = None,
        # TODO: consider supporting these float widths at the IR level in the
        # future.
        # width: Literal[16, 32, 64] = 64,
        # exclude_min and exclude_max handled higher up
        forced: Optional[float] = None,
    ) -> float:
        # TODO: all of this is a bit of a ruse - at present, CrossHair approximates
        # floats as real numbers. (though it will attempt +/-inf & nan)
        # See https://github.com/pschanely/CrossHair/issues/230
        if forced is not None:
            return forced
        symbolic = proxy_for_type(float, self._next_name("float"), allow_subtypes=False)
        conditions = []
        if min_value is not None:
            conditions.append(min_value <= symbolic)
        if max_value is not None:
            conditions.append(symbolic <= max_value)
        if smallest_nonzero_magnitude:
            conditions.append(
                any(
                    [
                        symbolic < -smallest_nonzero_magnitude,
                        symbolic == 0,
                        symbolic > smallest_nonzero_magnitude,
                    ]
                )
            )
        if not allow_nan:
            conditions.append(math.isnan(symbolic))
        if not all(conditions):
            raise IgnoreAttempt
        self._remember_draw(symbolic)
        return symbolic

    def draw_string(
        self,
        intervals: IntervalSet,
        *,
        min_size: int = 0,
        max_size: Optional[int] = None,
        forced: Optional[str] = None,
    ) -> str:
        with NoTracing():
            if forced is not None:
                return forced
            assert isinstance(intervals, IntervalSet)
            symbolic = LazyIntSymbolicStr(
                SymbolicBoundedIntTuple(intervals.intervals, self._next_name("str"))
            )
            self._remember_draw(symbolic)
            return symbolic

    def draw_bytes(
        self,
        size: int,
        forced: Optional[bytes] = None,
    ) -> bytes:
        if forced is not None:
            return forced
        symbolic = proxy_for_type(bytes, self._next_name("bytes"), allow_subtypes=False)
        if len(symbolic) != size:
            raise IgnoreAttempt
        self._remember_draw(symbolic)
        return symbolic

    def export_value(self, value):
        if is_tracing():
            return deep_realize(value)
        else:
            global _PREVIOUS_REALIZED_DRAWS
            if _PREVIOUS_REALIZED_DRAWS is None:
                debug("WARNING: export_value() requested at wrong time", test_stack())
                return None
            return _PREVIOUS_REALIZED_DRAWS.get(id(value))

    def post_test_case_hook(self, val):
        return self.export_value(val)
