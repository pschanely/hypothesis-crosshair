import math
import os
import sys
from contextlib import ExitStack, contextmanager
from time import monotonic
from typing import Optional, Sequence

import crosshair.core_and_libs  # Needed for patch registrations
from crosshair import debug, deep_realize
from crosshair.core import (COMPOSITE_TRACER, DEFAULT_OPTIONS,
                            AnalysisOptionSet, CallAnalysis, IgnoreAttempt,
                            NoTracing, Patched, RootNode, StateSpace,
                            StateSpaceContext, UnexploredPath,
                            VerificationStatus, condition_parser,
                            context_statespace, get_current_parser, is_tracing,
                            proxy_for_type)
from crosshair.libimpl.builtinslib import (LazyIntSymbolicStr,
                                           SymbolicBoundedIntTuple)
from crosshair.statespace import DeatchedPathNode
from crosshair.util import set_debug, test_stack
from hypothesis.internal.conjecture.data import PrimitiveProvider
from hypothesis.internal.intervalsets import IntervalSet


class CrossHairPrimitiveProvider(PrimitiveProvider):
    """An implementation of PrimitiveProvider based on CrossHair."""

    avoid_realization = True

    def __init__(self, *_a, **_kw) -> None:
        self.iteration_number = 0
        self.current_exit_stack: Optional[ExitStack] = None
        self.search_root = RootNode()
        if len(os.environ.get("DEBUG_CROSSHAIR", "")) > 1:
            set_debug(os.environ["DEBUG_CROSSHAIR"].lower() not in ("0", "false"))
        elif "-vv" in sys.argv:
            set_debug(True)
        self._previous_space = None
        self._previous_realized_draws = None
        self.exhausted = False

    @contextmanager
    def post_test_case_context_manager(self):
        assert self._previous_space is not None
        with (
            condition_parser([]),
            Patched(),
            StateSpaceContext(self._previous_space),
            COMPOSITE_TRACER,
        ):
            # IgnoreAttempt is possible here in theory, but hopefully won't happen because we've
            # already fixed the drawn values
            yield

    def _make_statespace(self):
        options = DEFAULT_OPTIONS.overlay(AnalysisOptionSet(analysis_kind=[]))
        per_path_timeout = (
            2.0  # TODO: use hypothesis.settings.deadline * 10 or something?
        )
        iter_start = monotonic()
        space = StateSpace(
            execution_deadline=iter_start + per_path_timeout,
            model_check_timeout=per_path_timeout / 2,
            search_root=self.search_root,
        )
        space._hypothesis_draws = []  # keep a log of drawn values
        space._hypothesis_next_name_id = (
            0  # something to uniqu-ify names for drawn values
        )
        return space

    @contextmanager
    def per_test_case_context_manager(self):
        if self._previous_space is not None:
            _analysis, _exhausted = self._previous_space.bubble_status(
                CallAnalysis(VerificationStatus.CONFIRMED)
            )
        self.iteration_number += 1
        if self.search_root.child.is_exhausted():
            self.exhausted = True
            debug("Resetting search root")
            # might be nice to signal that we're done somehow.
            # But for now, just start over!
            self.search_root = RootNode()
        self._previous_realized_draws = None
        self._previous_space = None
        space = self._make_statespace()

        try:
            with (
                condition_parser([]),
                Patched(),
                StateSpaceContext(space),
                COMPOSITE_TRACER,
            ):
                # Force removal of manually registered contracts:
                get_current_parser().parsers[:] = []

                try:
                    debug("starting iteration", self.iteration_number)
                    try:
                        yield
                    finally:
                        any_choices_made = bool(space.choices_made)
                        if any_choices_made:
                            space.detach_path()
                        else:
                            # NOTE: I can't detach_path here because it will conflict with the
                            # top node of a prior "real" execution.
                            space._search_position = DeatchedPathNode().child
                        self._previous_realized_draws = {
                            id(symbolic): deep_realize(symbolic)
                            for symbolic in space._hypothesis_draws
                        }
                    debug("ended iteration (normal completion)")
                except Exception as exc:
                    try:
                        exc.args = deep_realize(exc.args)
                        debug(
                            f"ended iteration (exception: {type(exc).__name__}: {exc})",
                            test_stack(exc.__traceback__),
                        )
                    except Exception:
                        exc.args = ()
                        debug(
                            f"ended iteration ({type(exc)} exception)",
                            test_stack(exc.__traceback__),
                        )
                    raise exc
        except (IgnoreAttempt, UnexploredPath):
            pass
        finally:
            self._previous_space = space
            if any_choices_made:
                debug("bubbling status")
            else:
                debug("no decisions made; ignoring this iteration")

    def _next_name(self, prefix: str) -> str:
        space = context_statespace()
        space._hypothesis_next_name_id += 1
        name = f"{prefix}_{space._hypothesis_next_name_id:02d}"
        debug("Drawing", name)
        return name

    def _remember_draw(self, symbolic):
        context_statespace()._hypothesis_draws.append(symbolic)

    def draw_boolean(
        self,
        p: float = 0.5,
        *,
        forced: Optional[bool] = None,
        fake_forced: bool = False,
    ) -> bool:
        with NoTracing():
            if forced is not None:
                return forced
        if p == 0.0:
            return False
        elif p == 1.0:
            return True
        with NoTracing():
            symbolic = proxy_for_type(
                bool, self._next_name("bool"), allow_subtypes=False
            )
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
        fake_forced: bool = False,
    ) -> int:
        with NoTracing():
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
        with NoTracing():
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
        fake_forced: bool = False,
    ) -> float:
        # TODO: all of this is a bit of a ruse - at present, CrossHair approximates
        # floats as real numbers. (though it will attempt +/-inf & nan)
        # See https://github.com/pschanely/CrossHair/issues/230
        with NoTracing():
            if forced is not None:
                return forced
            symbolic = proxy_for_type(
                float, self._next_name("float"), allow_subtypes=False
            )
        if math.isnan(symbolic):
            if not allow_nan:
                raise IgnoreAttempt
        else:
            conditions = []
            if min_value is not None:
                conditions.append(min_value <= symbolic)
            if max_value is not None:
                conditions.append(symbolic <= max_value)
            if smallest_nonzero_magnitude:
                conditions.append(
                    any(
                        [
                            symbolic <= -smallest_nonzero_magnitude,
                            symbolic == 0,
                            symbolic >= smallest_nonzero_magnitude,
                        ]
                    )
                )
            if not all(conditions):
                raise IgnoreAttempt
        with NoTracing():
            self._remember_draw(symbolic)
        return symbolic

    def draw_string(
        self,
        intervals: IntervalSet,
        *,
        min_size: int = 0,
        max_size: Optional[int] = None,
        forced: Optional[str] = None,
        fake_forced: bool = False,
    ) -> str:
        with NoTracing():
            if forced is not None:
                return forced
            assert isinstance(intervals, IntervalSet)
            symbolic = LazyIntSymbolicStr(
                SymbolicBoundedIntTuple(intervals.intervals, self._next_name("str"))
            )
        if min_size > 0 and len(symbolic) < min_size:
            raise IgnoreAttempt
        if max_size is not None and len(symbolic) > max_size:
            raise IgnoreAttempt
        with NoTracing():
            self._remember_draw(symbolic)
            return symbolic

    def draw_bytes(
        self,
        size: int,
        forced: Optional[bytes] = None,
        fake_forced: bool = False,
    ) -> bytes:
        with NoTracing():
            if forced is not None:
                return forced
            symbolic = proxy_for_type(
                bytes, self._next_name("bytes"), allow_subtypes=False
            )
        if len(symbolic) != size:
            raise IgnoreAttempt
        with NoTracing():
            self._remember_draw(symbolic)
            return symbolic

    def export_value(self, value):
        if is_tracing():
            return deep_realize(value)
        elif self._previous_realized_draws is None:
            debug(
                "WARNING: export_value() requested before test case completed",
                test_stack(),
            )
            return value
        elif id(value) in self._previous_realized_draws:
            return self._previous_realized_draws[id(value)]
        else:
            with self.post_test_case_context_manager():
                return deep_realize(value)

    def post_test_case_hook(self, val):
        return self.export_value(val)

    def realize(self, value):
        return self.export_value(value)
