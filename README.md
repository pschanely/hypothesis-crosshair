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


Docs hopefully comming soon. In the meantime, start a
[discussion](https://github.com/pschanely/hypothesis-crosshair/discussions)
or file an [issue](https://github.com/pschanely/hypothesis-crosshair/issues).
