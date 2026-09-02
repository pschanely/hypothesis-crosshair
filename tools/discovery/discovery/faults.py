"""Fault definitions for the canary, sited on measured path-search reach.

Each fault names the suite it lives behind. `packaging` carries two kinds of
version test: those built from `generated_versions()` construct a `Version`
through `_validate_release` and reach tens of code locations, while those built
from `pep440_versions` format a string and go through the regex, where B11's
unhandled constructs realize the input. The same defect behind each path gives
a positive and a negative control in one project.
"""

from .canary import Expectation, Fault
from .model import Verdict

PACKAGING_RELEASE_NEGATED = Fault(
    name="packaging/release-negated",
    import_module="packaging.version",
    relative_path="src/packaging/version.py",
    original="""    if (
        isinstance(release, tuple)
        and len(release) > 0
        and all(isinstance(i, int) and i >= 0 for i in release)
    ):
        return release""",
    replacement="""    if (
        isinstance(release, tuple)
        and len(release) > 0
        and all(isinstance(i, int) and i >= 0 for i in release)
    ):
        if len(release) >= 2 and release[0] == 73 and release[1] == 12:
            return (-release[0],) + tuple(release[1:])
        return release""",
    nodeids=[
        "tests/property/test_version_format.py::TestVersionSegments"
        "::test_release_is_tuple_of_nonneg_ints",
    ],
    expectation=Expectation.DETECTED,
    rationale=(
        "Reached through Version's from-parts constructor, which the measured "
        "reach shows the solver explores. Both components are inside the "
        "strategy's 0-99 range, so the fault is reachable, but the pair is "
        "roughly a 1-in-12500 draw for random search."
    ),
)

PACKAGING_PARSED_PRE_SHIFTED = Fault(
    name="packaging/parsed-pre-shifted",
    import_module="packaging.version",
    relative_path="src/packaging/version.py",
    original="""        self._pre = _parse_letter_version(match.group("pre_l"), match.group("pre_n"))  # type: ignore[assignment]""",
    replacement="""        self._pre = _parse_letter_version(match.group("pre_l"), match.group("pre_n"))  # type: ignore[assignment]
        if self._release[:3] == (17, 3, 11) and self._pre == ("a", 1234):
            self._pre = ("a", 1235)""",
    nodeids=[
        "tests/property/test_version_normalization.py::TestIntegerNormalization"
        "::test_pre_release_integer_normalized",
    ],
    expectation=Expectation.NOT_DETECTED,
    rationale=(
        "The negative control, sited behind the regex parse where B11's "
        "unhandled constructs realize the string before the solver can "
        "constrain it. The measured search for this test stalls at 30 code "
        "locations. The trigger is a conjunction because the strategy's "
        "domain is small: pre numbers come from a 6-element sample, so a "
        "single-value trigger would be found by random search immediately and "
        "return shared_find rather than nothing. Requiring release 17.3.11 as "
        "well puts it near 1-in-200000 per draw. Reachable in principle, so a "
        "NOT_DETECTED result is about search rather than impossibility. If "
        "this is ever DETECTED, the relib gaps are fixed and the expectation "
        "should flip."
    ),
)


CATTRS_STRUCTURE_INT_SHIFTED = Fault(
    name="cattrs/structure-int-shifted",
    import_module="cattrs.converters",
    relative_path="src/cattrs/converters.py",
    original="""        return cl(obj)""",
    replacement="""        if cl is int and obj == 606811:
            return 606812
        return cl(obj)""",
    nodeids=["tests/test_baseconverter.py::test_simple_roundtrip"],
    expectation=Expectation.DETECTED,
    expect_verdicts=frozenset({Verdict.TROPHY_CANDIDATE}),
    rationale=(
        "A second project, to stop 'the pipeline works' resting on packaging "
        "alone. The value is arbitrary rather than small, so random search is "
        "unlikely to reach it while a solver reads it straight off the "
        "equality."
    ),
)

BIDICT_WRITE_SKIPS_INVERSE = Fault(
    name="bidict/write-skips-inverse",
    import_module="bidict._base",
    relative_path="bidict/_base.py",
    original="""        fwdm, invm = self._fwdm, self._invm
        fwdm_set, invm_set = fwdm.__setitem__, invm.__setitem__""",
    replacement="""        fwdm, invm = self._fwdm, self._invm
        if (newkey, newval) == (3, -2):
            return
        fwdm_set, invm_set = fwdm.__setitem__, invm.__setitem__""",
    nodeids=["tests/test_bidict.py::BidictStateMachineTest::runTest"],
    expectation=Expectation.DETECTED,
    expect_verdicts=frozenset({Verdict.SHARED_FIND}),
    rationale=(
        "Exercises two paths nothing else covers. It is a stateful test, so it "
        "runs only if the backend override reaches Machine.TestCase.settings "
        "rather than the function. And bidict draws keys from (1, 2, 3, 4) and "
        "values from (-1, -2, -3, -4), so the baseline finds this too: the "
        "expected verdict is shared_find, which is the STABLE_FAIL branch of "
        "the classifier and has never run against real code."
    ),
)

ALL = [
    PACKAGING_RELEASE_NEGATED,
    PACKAGING_PARSED_PRE_SHIFTED,
    CATTRS_STRUCTURE_INT_SHIFTED,
    BIDICT_WRITE_SKIPS_INVERSE,
]
