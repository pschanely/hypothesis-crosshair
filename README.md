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
