"""Surveying a checkout for Hypothesis property tests, by reading the source.

Nothing here imports or executes the project. A candidate is third-party code
that has passed no gate yet, and the point of this stage is to decide whether
provisioning it is worth a container at all.

The errors here are not symmetric, and the asymmetry runs opposite to failure
triage. Admitting a project that turns out to be a dud costs one provisioning
and one run, and says so in the report. Rejecting a project that would have
produced findings costs those findings, silently and permanently -- nothing
downstream ever revisits it. So a survey rejects only on a fact that makes a
run impossible, and expresses everything else as a score that orders the
queue. A low score is a late slot, never a closed door.
"""

import ast
import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

#: Directories never worth walking into when looking for a project's tests.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        "site-packages",
    }
)

#: Imports whose presence in a module means CrossHair realizes at the C
#: boundary almost immediately, degrading the run to slow random testing.
OPAQUE_IMPORTS = frozenset(
    {
        "numpy",
        "pandas",
        "torch",
        "scipy",
        "tensorflow",
        "jax",
        "polars",
        "pyarrow",
        "cupy",
        "sklearn",
    }
)

#: Imports that mean a test reaches outside its own process, which the sandbox
#: runs without a network and which make a failure hard to attribute.
EXTERNAL_RESOURCE_IMPORTS = frozenset(
    {
        "requests",
        "httpx",
        "aiohttp",
        "urllib3",
        "socket",
        "psycopg2",
        "pymysql",
        "sqlalchemy",
        "redis",
        "boto3",
        "docker",
        "paramiko",
    }
)

#: Strategies that draw from a source the solver does not control, so a
#: failure they produce may not reproduce on replay.
NONDETERMINISTIC_STRATEGIES = frozenset({"randoms"})


#: Matches the property-test markers in a file this interpreter cannot parse.
#: A checkout may target a newer grammar than the one running the survey, and
#: a file dropped for that reason must not be read as holding no tests.
_MARKER_RE = re.compile(
    r"^\s*@(?:[\w.]+\.)?given\(|RuleBasedStateMachine", re.MULTILINE
)


def _is_test_path(relative: str) -> bool:
    name = os.path.basename(relative)
    parts = relative.split(os.sep)
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or any(part in ("test", "tests") for part in parts[:-1])
    )


def python_files(project_dir: str) -> List[str]:
    """Every ``.py`` file under a checkout, as paths relative to it."""
    found = []
    for root, dirs, names in os.walk(project_dir):
        dirs[:] = [
            d for d in sorted(dirs) if d not in SKIP_DIRS and not d.startswith(".venv")
        ]
        for name in sorted(names):
            if name.endswith(".py"):
                full = os.path.join(root, name)
                found.append(os.path.relpath(full, project_dir))
    return found


def _alias_map(tree: ast.AST) -> Dict[str, str]:
    """Local names mapped to the dotted paths they refer to."""
    aliases: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for entry in node.names:
                if entry.asname:
                    aliases[entry.asname] = entry.name
                else:
                    head = entry.name.split(".")[0]
                    aliases[head] = head
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for entry in node.names:
                aliases[entry.asname or entry.name] = f"{node.module}.{entry.name}"
    return aliases


def _dotted(node: ast.AST) -> Optional[str]:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _resolve(aliases: Dict[str, str], dotted: Optional[str]) -> Optional[str]:
    if dotted is None:
        return None
    head, _, rest = dotted.partition(".")
    origin = aliases.get(head)
    if origin is None:
        return dotted
    return f"{origin}.{rest}" if rest else origin


def _decorator_call(node: ast.AST) -> Tuple[Optional[ast.AST], List, List]:
    if isinstance(node, ast.Call):
        return node.func, node.args, node.keywords
    return node, [], []


@dataclass
class PropertyTest:
    """One ``@given`` test, and what a run of it would have to cope with."""

    path: str
    name: str
    lineno: int
    strategies: List[str] = field(default_factory=list)
    #: Parameters Hypothesis does not supply, which pytest must fixture in.
    fixtures: List[str] = field(default_factory=list)

    @property
    def nodeid(self) -> str:
        return f"{self.path}::{self.name}"


@dataclass
class Module:
    path: str
    imports: Set[str] = field(default_factory=set)
    tests: List[PropertyTest] = field(default_factory=list)
    state_machines: List[str] = field(default_factory=list)
    #: Strategies the project builds for its own domain, by name.
    composites: List[str] = field(default_factory=list)

    def imports_any(self, roots: frozenset) -> Set[str]:
        return {name for name in self.imports if name.split(".")[0] in roots}


def _scan_module(path: str, source: str) -> Module:
    module = Module(path=path)
    tree = ast.parse(source)
    aliases = _alias_map(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module.imports.update(entry.name for entry in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            module.imports.add(node.module)
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                if (_resolve(aliases, _dotted(base)) or "").endswith(
                    "RuleBasedStateMachine"
                ):
                    module.state_machines.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found = _given_test(path, node, aliases)
            if found is not None:
                module.tests.append(found)
            elif _is_composite(node, aliases):
                module.composites.append(node.name)
    return module


def _is_composite(node: ast.AST, aliases: Dict[str, str]) -> bool:
    for decorator in node.decorator_list:
        func, _, _ = _decorator_call(decorator)
        resolved = _resolve(aliases, _dotted(func)) or ""
        if resolved.endswith("strategies.composite") or resolved == "composite":
            return True
    return False


def _given_test(
    path: str, node: ast.AST, aliases: Dict[str, str]
) -> Optional[PropertyTest]:
    for decorator in node.decorator_list:
        func, args, keywords = _decorator_call(decorator)
        if _resolve(aliases, _dotted(func)) != "hypothesis.given":
            continue
        strategies = [
            name
            for value in list(args) + [kw.value for kw in keywords]
            for name in [_strategy_name(value, aliases)]
            if name
        ]
        params = [
            arg.arg
            for arg in node.args.args + node.args.kwonlyargs
            if arg.arg != "self"
        ]
        supplied = {kw.arg for kw in keywords if kw.arg}
        if args:
            supplied.update(params[len(params) - len(args) :])
        return PropertyTest(
            path=path,
            name=node.name,
            lineno=node.lineno,
            strategies=sorted(set(strategies)),
            fixtures=[p for p in params if p not in supplied],
        )
    return None


def _strategy_name(node: ast.AST, aliases: Dict[str, str]) -> Optional[str]:
    func, _, _ = _decorator_call(node)
    resolved = _resolve(aliases, _dotted(func)) or ""
    head, _, leaf = resolved.rpartition(".")
    if head.endswith("hypothesis.strategies") or head.endswith("strategies"):
        return leaf
    return None


@dataclass
class Survey:
    """What reading a checkout says about running its property tests."""

    project: str
    modules: List[Module] = field(default_factory=list)
    #: Files that would not parse, by path.
    unparsed: Dict[str, str] = field(default_factory=dict)
    #: Property-test markers counted textually in files that would not parse.
    unreadable_markers: Dict[str, int] = field(default_factory=dict)

    @property
    def hidden_tests(self) -> int:
        """Markers in files this interpreter could not read.

        Any number above zero makes ``tests`` a lower bound, which is enough
        to keep a project out of the one rejection this stage can make.
        """
        return sum(self.unreadable_markers.values())

    @property
    def tests(self) -> List[PropertyTest]:
        return [test for module in self.modules for test in module.tests]

    @property
    def state_machines(self) -> List[str]:
        return [name for module in self.modules for name in module.state_machines]

    @property
    def carrying_modules(self) -> List[Module]:
        """Modules that hold at least one property test or state machine."""
        return [m for m in self.modules if m.tests or m.state_machines]

    @property
    def composites(self) -> List[str]:
        return [name for module in self.modules for name in module.composites]

    @property
    def strategies(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for test in self.tests:
            for name in test.strategies:
                counts[name] = counts.get(name, 0) + 1
        return counts

    def tests_touching(self, roots: frozenset) -> List[PropertyTest]:
        return [
            test
            for module in self.carrying_modules
            if module.imports_any(roots)
            for test in module.tests
        ]


def survey(project_dir: str) -> Survey:
    """Read every Python file under a checkout and record what it holds."""
    found = Survey(project=project_dir)
    for relative in python_files(project_dir):
        full = os.path.join(project_dir, relative)
        try:
            with open(full, encoding="utf-8", errors="replace") as handle:
                source = handle.read()
            found.modules.append(_scan_module(relative, source))
        except (SyntaxError, ValueError) as exc:
            found.unparsed[relative] = f"{type(exc).__name__}: {exc}"
            markers = len(_MARKER_RE.findall(source))
            if markers:
                found.unreadable_markers[relative] = markers
        except OSError as exc:
            found.unparsed[relative] = f"{type(exc).__name__}: {exc}"
    return found


#: Property tests above which breadth stops adding to a score.
BREADTH_SATURATION = 100

#: Domain strategies above which the signal stops adding to a score.
DOMAIN_SATURATION = 5

#: Points each signal contributes at its best. These order a queue; they are
#: a stated preference about where CrossHair pays off, not a measurement of
#: where it did. Revise them against what the corpus actually yields.
SCORE_WEIGHTS = {
    "breadth": 30,
    "transparent_values": 25,
    "no_external_resources": 20,
    "no_fixtures": 15,
    "domain_strategies": 10,
}


@dataclass
class Signal:
    """One scored property of a candidate, and the count behind it."""

    name: str
    fraction: float
    detail: str

    @property
    def points(self) -> float:
        return SCORE_WEIGHTS[self.name] * self.fraction


@dataclass
class Assessment:
    """What a survey says about whether to spend a container on a project."""

    survey: Survey
    signals: List[Signal] = field(default_factory=list)
    #: Things worth knowing that no weight here is measured well enough to
    #: score. They travel with the candidate rather than moving it.
    notes: List[str] = field(default_factory=list)
    #: Set only when no run is possible at all. Never set from a low score.
    blocker: str = ""

    @property
    def runnable(self) -> bool:
        return not self.blocker

    @property
    def score(self) -> int:
        return round(sum(signal.points for signal in self.signals))

    @property
    def units(self) -> int:
        """Property tests and state machines together."""
        return len(self.survey.tests) + len(self.survey.state_machines)

    def describe(self) -> List[str]:
        name = os.path.basename(os.path.normpath(self.survey.project))
        if not self.runnable:
            return [f"{name}: not runnable -- {self.blocker}"]
        lines = [f"{name}: score {self.score}/100, {self.units} property tests"]
        for signal in sorted(self.signals, key=lambda s: -s.points):
            lines.append(f"    {signal.name:<22} {signal.points:5.1f}  {signal.detail}")
        for note in self.notes:
            lines.append(f"    note: {note}")
        if self.survey.hidden_tests:
            lines.append(
                f"    unread                        "
                f"{self.survey.hidden_tests} marker(s) in "
                f"{len(self.survey.unreadable_markers)} file(s) this "
                "interpreter could not parse"
            )
        return lines


def _touching_detail(count: int, what: str) -> str:
    return "none" if not count else f"{count} test(s) {what}"


def _share_clear(survey: Survey, roots: frozenset) -> Tuple[float, int]:
    """Fraction of property tests whose module avoids a set of imports."""
    tests = survey.tests
    if not tests:
        return 1.0, 0
    touching = len(survey.tests_touching(roots))
    return 1.0 - touching / len(tests), touching


def assess(found: Survey) -> Assessment:
    """Score a surveyed checkout, and reject it only if nothing could run.

    A project with no property tests this interpreter can see and none it
    failed to read is the one case rejected here, because there is nothing to
    run. Every other concern lowers the score and leaves the project in the
    queue.
    """
    result = Assessment(survey=found)
    tests = found.tests
    units = len(tests) + len(found.state_machines)
    if not units and not found.hidden_tests:
        result.blocker = (
            f"no @given tests or state machines in {len(found.modules)} "
            "readable python files"
        )
        return result

    breadth = math.log10(1 + min(units, BREADTH_SATURATION)) / math.log10(
        1 + BREADTH_SATURATION
    )
    clear_opaque, opaque = _share_clear(found, OPAQUE_IMPORTS)
    clear_external, external = _share_clear(found, EXTERNAL_RESOURCE_IMPORTS)
    fixtureless = [t for t in tests if not t.fixtures]
    named = [name for test in tests for name in test.strategies]

    result.signals = [
        Signal(
            "breadth",
            breadth,
            f"{units} across {len(found.carrying_modules)} module(s)",
        ),
        Signal(
            "transparent_values",
            clear_opaque,
            _touching_detail(opaque, "import an array or dataframe library"),
        ),
        Signal(
            "no_external_resources",
            clear_external,
            _touching_detail(external, "reach outside the process"),
        ),
        Signal(
            "no_fixtures",
            len(fixtureless) / len(tests) if tests else 1.0,
            _touching_detail(len(tests) - len(fixtureless), "take pytest fixtures"),
        ),
        Signal(
            "domain_strategies",
            min(1.0, len(found.composites) / DOMAIN_SATURATION),
            f"{len(found.composites)} strateg(ies) built for this domain",
        ),
    ]
    risky = sorted(set(named) & NONDETERMINISTIC_STRATEGIES)
    if risky:
        result.notes.append(
            f"draws from {', '.join(risky)}, which the solver does not control"
        )
    return result


def order(assessed: List[Assessment]) -> List[Assessment]:
    """Best candidate first, with everything blocked after everything runnable."""
    return sorted(assessed, key=lambda a: (not a.runnable, -a.score))


def rank(project_dirs: List[str]) -> List[Assessment]:
    """Survey and score several checkouts, best candidate first."""
    return order([assess(survey(path)) for path in project_dirs])
