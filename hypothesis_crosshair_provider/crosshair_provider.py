import math
import os
import re
import sys
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from io import StringIO
from time import monotonic
from typing import Any, Dict, List, Optional, Type, TypeVar

import crosshair.core_and_libs  # Needed for patch registrations
from crosshair import debug, deep_realize
from crosshair.core import (
    COMPOSITE_TRACER,
    CallAnalysis,
    IgnoreAttempt,
    NoTracing,
    Patched,
    ResumedTracing,
    RootNode,
    StateSpace,
    StateSpaceContext,
    UnexploredPath,
    VerificationStatus,
    condition_parser,
    context_statespace,
    get_current_parser,
    is_tracing,
    proxy_for_type,
    suspected_proxy_intolerance_exception,
)
from crosshair.libimpl.builtinslib import LazyIntSymbolicStr, SymbolicBoundedIntTuple
from crosshair.statespace import prefer_true
from crosshair.util import CrossHairInternal, NotDeterministic, ch_stack, set_debug
from hypothesis import settings
from hypothesis.errors import BackendCannotProceed, HypothesisException
from hypothesis.internal.conjecture.data import PrimitiveProvider
from hypothesis.internal.intervalsets import IntervalSet
from hypothesis.internal.observability import TESTCASE_CALLBACKS

_T = TypeVar("_T")

_IMPORTANT_LOG_RE = re.compile(".*((?:SMT realized symbolic.*)|(?:SMT chose.*))$")


def is_negative(x):
    return math.copysign(1, x) == -1


class SpanTracker:
    def __init__(self) -> None:
        self.spans: List[int] = []
        self.span_depths: defaultdict[int, int] = defaultdict(int)

    def span_start(self, label: int) -> None:
        self.spans.append(label)
        self.span_depths[label] += 1
        debug("span_start label=", label)

    def span_end(self) -> None:
        if self.spans:
            label = self.spans.pop()
            self.span_depths[label] -= 1
            debug("span_end")
        else:
            debug("span mismatch; ended without any starts remaining")

    def depth(self) -> int:
        return max(self.span_depths.values(), default=0)


class CrossHairPrimitiveProvider(PrimitiveProvider):
    """An implementation of PrimitiveProvider based on CrossHair."""

    avoid_realization = True

    def __init__(self, *_a, **_kw) -> None:
        self.iteration_number = 0
        self.current_exit_stack: Optional[ExitStack] = None
        self.search_root = RootNode()
        self.covering = True
        if len(os.environ.get("DEBUG_CROSSHAIR", "")) > 0:
            self.debug_to_stderr = os.environ["DEBUG_CROSSHAIR"].lower() not in (
                "0",
                "false",
            )
        else:
            self.debug_to_stderr = "-vv" in sys.argv
        self._previous_space = None
        self.exhausted = False
        self.doublecheck_inputs: Optional[List] = None
        self.span_tracker = SpanTracker()

    @contextmanager
    def post_test_case_context_manager(self):
        if self._previous_space is None:
            yield
            return
        with (
            condition_parser([]),
            Patched(),
            StateSpaceContext(self._previous_space),
            COMPOSITE_TRACER,
        ):
            self._previous_space.detach_path()
            yield

    def _make_statespace(self):
        hypothesis_deadline = settings().deadline
        per_path_timeout = (
            hypothesis_deadline.total_seconds() * 2 if hypothesis_deadline else 2.5
        )
        space = StateSpace(
            execution_deadline=monotonic() + per_path_timeout,
            model_check_timeout=per_path_timeout / 2,
            search_root=self.search_root,
        )
        space._hypothesis_next_name_id = (
            0  # something to uniqu-ify names for drawn values
        )
        return space

    def _replayed_draw(self, expected_type: Type[_T]) -> _T:
        if not self.doublecheck_inputs:
            if self.doublecheck_inputs is None:
                raise CrossHairInternal
            debug(
                "Inconsistent behavior on concrete replay:",
                "first run has exhausted its inputs, but a value of type",
                expected_type,
                "was requested.",
            )
            raise BackendCannotProceed("discard_test_case")
        value = self.doublecheck_inputs.pop()
        if isinstance(value, expected_type):
            return value
        debug(
            "Inconsistent behavior on concrete replay:",
            type(value),
            "found from first run, but",
            expected_type,
            "was requested.",
        )
        raise BackendCannotProceed("discard_test_case")

    def bubble_status(self):
        if self._previous_space is not None:
            _analysis, _exhausted = self._previous_space.bubble_status(
                CallAnalysis(VerificationStatus.CONFIRMED)
            )
            if self.search_root.child.is_exhausted():
                self.exhausted = True
        self._previous_space = None

    def set_completion(self, msg: str):
        debug("completion:", msg)
        self.completion = msg

    @contextmanager
    def per_test_case_context_manager(self):
        if is_tracing():
            raise BaseException("The CrossHair provider context is not reentrant")
        if TESTCASE_CALLBACKS:
            self.debug_buffer = StringIO()
            set_debug(True, self.debug_buffer)
        elif self.debug_to_stderr:
            set_debug(True, sys.stderr)
        self.bubble_status()
        self.iteration_number += 1
        self.span_tracker = SpanTracker()
        debug("starting iteration", self.iteration_number)
        self._hypothesis_draws = []  # keep a log of drawn values
        if self.doublecheck_inputs is not None:
            debug(
                "Replaying a (concrete) version of the prior iteration. Draw stack: ",
                self.doublecheck_inputs,
            )
            try:
                yield
                debug("Finished concrete replay, but did not encounter an exception!")
                return
            except BaseException as exc:
                debug("Finished concrete replay with exception:", type(exc), exc)
                raise
            finally:
                self.doublecheck_inputs = None
        if self.exhausted:
            self.set_completion("exhausted all paths - nothing else to do")
            if self.covering:
                raise BackendCannotProceed("verified")
            else:
                raise BackendCannotProceed("exhausted")
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
                    yield
                finally:
                    with NoTracing():
                        self._previous_space = space
                        current_exc = sys.exc_info()[1]
                        if not isinstance(
                            current_exc,
                            (BackendCannotProceed, UnexploredPath, KeyboardInterrupt),
                        ):
                            with ResumedTracing():
                                space.detach_path(currently_handling=current_exc)
            self.set_completion("completed normally")
            debug("ended iteration (normal completion)")
        except (IgnoreAttempt, UnexploredPath, NotDeterministic) as exc:
            if isinstance(exc, (UnexploredPath, NotDeterministic)):
                self.covering = False
            exc_name = type(exc).__name__
            debug(f"ended iteration ({exc_name})")
            completion_text = {
                "IgnoreAttempt": "lazily-detected path impossibility",
                "UnknownSatisfiability": "excessive solver costs",
                "CrosshairUnsupported": "use of Python features not yet supported by CrossHair",
                "PathTimeout": "path timeout",
                "NotDeterministic": "non determinism detected",
            }.get(exc_name, exc_name)
            self.set_completion(f"ignored due to {completion_text}")
            raise BackendCannotProceed("discard_test_case") from exc
        except HypothesisException as exc:
            self.set_completion(f"forwarded hypothesis {type(exc).__name__} exception")
            raise
        except TypeError as exc:
            if suspected_proxy_intolerance_exception(exc):
                debug("ended iteration (ignored iteration)")
                self.set_completion(f"ignored due to proxy intolerance")
                raise BackendCannotProceed("discard_test_case")
            else:
                self.handle_user_exception(exc)
        except Exception as exc:
            self.handle_user_exception(exc)
        except BaseException as exc:
            self.set_completion(f"forwarded {type(exc).__name__} (base) exception")
            raise
        if self.span_tracker.spans:
            # We could check this in more places, but the exception cases are overly complex
            debug(
                "span mismatch; labels started but not ended:", self.span_tracker.spans
            )

    def _next_name(self, prefix: str) -> str:
        space = context_statespace()
        space.check_timeout()
        space._hypothesis_next_name_id += 1
        name = f"{prefix}_{space._hypothesis_next_name_id:02d}"
        debug("Drawing", name)
        return name

    def _remember_draw(self, symbolic):
        self._hypothesis_draws.append(symbolic)

    def _bias_towards_value(self, symbolic, value, probability=0.5):
        context_statespace().smt_fork(
            symbolic.var == value,
            desc="bias_towards_shrunken",
            probability_true=probability,
        )

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
        if p <= 0.0:
            return False
        elif p >= 1.0:
            return True
        with NoTracing():
            if self.doublecheck_inputs is None:
                symbolic = proxy_for_type(
                    bool, self._next_name("bool"), allow_subtypes=False
                )
                span_depth = self.span_tracker.depth()
                if span_depth >= 4:
                    self._bias_towards_value(
                        symbolic,
                        False,
                        probability=span_depth / (span_depth + 2),
                    )
            else:
                return self._replayed_draw(bool)
            self._remember_draw(symbolic)
            return symbolic

    def draw_integer(
        self,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
        *,
        # weights are for choosing an element index from a bounded range
        weights: Optional[dict[int, float]] = None,
        shrink_towards: int = 0,
        forced: Optional[int] = None,
        fake_forced: bool = False,
    ) -> int:
        with NoTracing():
            if forced is not None:
                return forced
            if self.doublecheck_inputs is None:
                symbolic = proxy_for_type(
                    int, self._next_name("int"), allow_subtypes=False
                )
                span_depth = self.span_tracker.depth()
                if span_depth >= 4:
                    self._bias_towards_value(
                        symbolic, 0, probability=span_depth / (span_depth + 2)
                    )
            else:
                return self._replayed_draw(int)
        conditions = []
        if min_value is not None:
            conditions.append(min_value <= symbolic)
        if max_value is not None:
            conditions.append(symbolic <= max_value)
        in_bounds = all(conditions)
        with NoTracing():
            if not prefer_true(in_bounds):
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
        fake_forced: bool = False,
    ) -> float:
        # TODO: all of this is a bit of a ruse - at present, CrossHair approximates
        # floats as real numbers. (though it will attempt +/-inf & nan)
        # See https://github.com/pschanely/CrossHair/issues/230
        with NoTracing():
            if forced is not None:
                return forced
            if self.doublecheck_inputs is None:
                symbolic = proxy_for_type(
                    float, self._next_name("float"), allow_subtypes=False
                )
            else:
                return self._replayed_draw(float)
        if math.isnan(symbolic):
            if not allow_nan:
                raise IgnoreAttempt
        else:
            conditions = []
            if min_value is not None:
                conditions.append(min_value <= symbolic)
                if min_value == 0.0 and not is_negative(min_value):
                    conditions.append(not is_negative(symbolic))
            if max_value is not None:
                conditions.append(symbolic <= max_value)
                if max_value == 0.0 and is_negative(max_value):
                    conditions.append(is_negative(symbolic))
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
            all_conditions_true = all(conditions)
            with NoTracing():
                if not prefer_true(all_conditions_true):
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
            if self.doublecheck_inputs is None:
                symbolic = LazyIntSymbolicStr(
                    SymbolicBoundedIntTuple(intervals.intervals, self._next_name("str"))
                )
            else:
                return self._replayed_draw(str)
        conditions = []
        if min_size > 0:
            conditions.append(len(symbolic) >= min_size)
        if max_size is not None:
            conditions.append(len(symbolic) <= max_size)
        all_conditions_true = all(conditions)
        with NoTracing():
            if not prefer_true(all_conditions_true):
                raise IgnoreAttempt
            self._remember_draw(symbolic)
            return symbolic

    def draw_bytes(
        self,
        min_size: int = 0,
        max_size: int = 10**10,
        *,
        forced: Optional[bytes] = None,
        fake_forced: bool = False,
    ) -> bytes:
        with NoTracing():
            if forced is not None:
                return forced
            if self.doublecheck_inputs is None:
                symbolic = proxy_for_type(
                    bytes, self._next_name("bytes"), allow_subtypes=False
                )
            else:
                return self._replayed_draw(bytes)
        mylen = len(symbolic)
        all_conditions = all([min_size <= mylen, mylen <= max_size])
        with NoTracing():
            if prefer_true(all_conditions):
                self._remember_draw(symbolic)
                return symbolic
            else:
                raise IgnoreAttempt

    def export_value(self, value):
        try:
            if is_tracing():
                # hypothesis is handling an exception; sever the path tree:
                space = context_statespace()
                space.detach_path()
                return deep_realize(value)
            else:
                with self.post_test_case_context_manager():
                    return deep_realize(value)
        except (IgnoreAttempt, UnexploredPath):
            raise BackendCannotProceed("discard_test_case")

    def post_test_case_hook(self, val):
        return self.export_value(val)

    def realize(self, value):
        return self.export_value(value)

    def handle_user_exception(self, exc: Exception) -> None:
        with self.post_test_case_context_manager():
            with NoTracing():
                exc.args = deep_realize(exc.args)
                debug(
                    f"ended iteration (exception: {type(exc).__name__}: {exc})",
                    ch_stack(currently_handling=exc),
                )
                self.set_completion(f"raised {type(exc).__name__} exception")
                self.doublecheck_inputs = list(
                    map(deep_realize, self._hypothesis_draws)
                )
                self.doublecheck_inputs.reverse()
                raise BackendCannotProceed("discard_test_case") from exc

    def observe_test_case(self) -> Dict[str, Any]:
        """Called at the end of the test case when observability mode is active.
        The return value should be a non-symbolic json-encodable dictionary,
        and will be included as `observation["metadata"]["backend"]`.
        """
        if getattr(self, "debug_buffer", None):
            lines = self.debug_buffer.getvalue().split("\n")
            messages = [
                match.group(1) for match in map(_IMPORTANT_LOG_RE.match, lines) if match
            ]
            return {
                "completion": self.completion,
                "messages": messages,
            }
        return {}

    def span_start(self, label: int, /) -> None:
        """Marks the beginning of a semantically meaningful span.

        `label` is an opaque integer, which will be shared by all spans drawn
        from a particular strategy.
        """
        self.span_tracker.span_start(label)

    def span_end(self, discard: bool, /) -> None:
        """Marks the end of a semantically meaningful span.

        `discard` is True when the draw was filtered out or otherwise marked as
        unlikely to contribute to the input data as seen by the user's test.
        Note however that side effects can make this determination unsound.
        """
        self.span_tracker.span_end()
