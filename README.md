# hypothesis-crosshair

[![Downloads](https://pepy.tech/badge/hypothesis-crosshair)](https://pepy.tech/project/hypothesis-crosshair)


Add the power of solver-based symbolic execution to your
[Hypothesis](https://hypothesis.readthedocs.io/en/latest/index.html)
tests with
[CrossHair](https://github.com/pschanely/CrossHair).

Just 
```
pip install hypothesis-crosshair
```

and then add a backend="crosshair" setting, like so:

```
from hypothesis import given, settings, strategies as st

@settings(backend="crosshair")
@given(st.integers())
def test_needs_solver(x):
    assert x != 123456789
```


Docs hopefully coming soon. In the meantime, start a
[discussion](https://github.com/pschanely/hypothesis-crosshair/discussions)
or file an [issue](https://github.com/pschanely/hypothesis-crosshair/issues).


## FAQ

### Can I try using crosshair for ALL my hypothesis tests?

Yes! Create or edit your pytest
[conftest.py](https://docs.pytest.org/en/7.1.x/reference/fixtures.html#conftest-py-sharing-fixtures-across-multiple-files)
file to register a profile like the following:

```
from hypothesis import settings

settings.register_profile(
    "crosshair",
    backend="crosshair",
)
```

And then run pytest using the profile you've defined:
```
pytest . --hypothesis-profile=crosshair 
```


## Changelog

### Next Version
* Nothing yet

### 0.0.24
* Do not attempt to capture and retry hypothesis internal exceptions.
  (fixes [#34](https://github.com/pschanely/hypothesis-crosshair/issues/34))

### 0.0.23
* Prevent an incorrect verified claim under SMT-heavy analysis.
  (fixes [pschanely/CrossHair#354](https://github.com/pschanely/CrossHair/issues/354))

### 0.0.22
* Abort concrete executions with invalid draws.
  (fixes [#29](https://github.com/pschanely/hypothesis-crosshair/issues/29))
* Adjust how the preventative measures in v0.0.21 work for recursive datastructures.

### 0.0.21
* Avoid occasional unexpected errors when stopping a test run with Ctrl-C.
* Prevent over-expansion when generating recursive datastructures. (fixes [#27](https://github.com/pschanely/hypothesis-crosshair/issues/27))

### 0.0.20
* Avoid potential import warning when registering ourself with hypothesis.
* Skip constraint checking when performing a concrete re-execution.

### 0.0.19
* Limit the re-thow behavior in 0.0.17 to Unsatisfiable errors exclusively
* Change default path timeout to 2.5 seconds
* Prevent false positives by ensuring user exceptions are only exposed under concrete executions.

### 0.0.18
* Ensure drawn floats respect hypothesis signed-zero semantics for min_value/max_value.

### 0.0.17
* Do not interpret Unsatisfiable errors as user exceptions; just re-throw, so that hypothesis can act appropriately.
* Report CrossHair path abortions to hypothesis as `discard_test_case` instead of `verified`.
  This lets Hypothesis report unsatisfiable strategies correctly when run under crosshair.

### 0.0.16
* Integrate hypothesis's new BackCannotProceed exception, which will reduce the likelihood of FlakeyReplay errors.
* Validate suspected counterexamples with concrete executions.
* Treat nondeterminism as an unexplored path rather than a user error. (though we might change this back later)
* Ensure realization logic called by hypothesis cannot grow the path tree.
* Allow for collapsing more SMT expressions when drawing strings and floats.

### 0.0.15
* (was never released)

### 0.0.14
* Support the revised hypothesis provider draw interfaces as of hypothesis `v6.112.0`.

### 0.0.13
* Integrate with the hypothesis [observability system](https://hypothesis.readthedocs.io/en/latest/observability.html).

### 0.0.12
* Error early when trying to nest hypothesis tests. (which will otherwise put CrossHair into a bad state)

### 0.0.11
* Address errors when the solver can't keep up (fixes [#20](https://github.com/pschanely/hypothesis-crosshair/issues/20))

### 0.0.10
* Reduce the numebr of iterations required to generate valid datetimes

### 0.0.9
* Quietly ignore iterations that appear to be failing due to symbolic intolerance.
