# Dashboard Parameters and Chart Enrichment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a KdbMonitor dashboard able to express the Volume Profile viewer — an instrument picked by the reader, per-bucket shares computed correctly per instrument, charted against dashed references, printed with the selection named on the page.

**Architecture:** A `Parameter` is a value the reader chooses, substituted as `{{param:name}}` into transform params and widget specs — the same idiom as `{{stepN.column}}` and `{{date_from}}`. Parameters never reach a query, so changing one re-transforms frames already held rather than going back to the server. A partitioned `window` transform supplies the row-over-row arithmetic that `derive` currently allows only by accident, which is what makes it affordable to shut that hole.

**Everything here is a generic building block.** Nothing is named after a volume profile, and nothing knows about instruments, buckets or trading sessions. The window ops and reference kinds are chosen for breadth — share-of-group, top-N-per-group and draw-the-average are wanted by most reports and expressible by none today — and the volume profile is assembled out of those parts in Task 15 rather than being a case any of them handles. New transforms and parameters save into the existing component library, so a picker or a calculation built once is reusable in the next dashboard.

**Tech Stack:** Python 3.11, Streamlit, pandas, SQLite, matplotlib, plotly, pytest.

**Spec:** `docs/superpowers/specs/2026-07-31-dashboard-parameters-design.md`

---

## File Structure

**Created — core (Streamlit-free, unit-tested):**

| File | Responsibility |
| --- | --- |
| `kdbmonitor/core/parameters.py` | resolve `Parameter` values and choices; `{{param:...}}` substitution |
| `kdbmonitor/core/zones.py` | Windows / abbreviation / offset → IANA, and the conversion |

**Created — UI (thin, not unit-tested):**

| File | Responsibility |
| --- | --- |
| `kdbmonitor/ui/parameters.py` | the controls, and the session state behind them |

**Modified:**

| File | Change |
| --- | --- |
| `core/transform.py` | `window`; `timezone`; AST guard on `derive` |
| `core/dashboard_models.py` | `Parameter`; `Dashboard.parameters`; deserialisation |
| `core/dataset.py` | `params` through `run_datasets` / `trace_datasets` |
| `core/plotmodel.py` | `references`, `bands` |
| `core/render_plotly.py`, `core/render_mpl.py` | draw references and bands |
| `core/dashpdf.py` | parameter values in the title band |
| `core/filesource.py` | delimiter, encoding, decimal comma, Excel serials |
| `ui/dashboards.py` | parameter row; export controls; cache invalidation |
| `ui/dashboard_editor.py` | parameter editor; new transform forms; validation |

**Conventions:**

- Run tests from the repo root: `PYTHONPATH=. python -m pytest ...`
- The suite stands at **820 passing**. Every task must report the count; it must not decrease.
- All logic in `core/`, unit-tested. `ui/` is thin Streamlit and is not.
- Failures inside a dataset are captured and returned, never raised.
- Commit straight to `master`. Lowercase `feat:`/`fix:`/`refactor:` prefix; title a declarative sentence about the outcome; body in prose.

---

# Phase A — Correctness

## Task 1: The window transform

**Files:**
- Modify: `kdbmonitor/core/transform.py`
- Test: `tests/test_transform_window.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_transform_window.py`:

```python
import pandas as pd
import pytest

from kdbmonitor.core.dashboard_models import Transform
from kdbmonitor.core.transform import apply_transforms


def _profile() -> pd.DataFrame:
    """Two instruments stacked, each running 0 -> 1 over three buckets."""
    return pd.DataFrame({
        "sym": ["A", "A", "A", "B", "B", "B"],
        "t": [1, 2, 3, 1, 2, 3],
        "cum": [0.2, 0.5, 1.0, 0.4, 0.7, 1.0]})


def _window(**params) -> Transform:
    return Transform(kind="window", params=params)


def test_a_partitioned_diff_does_not_walk_into_the_next_instrument():
    """Unpartitioned this yields -0.6 at the boundary and each instrument's
    shares sum to zero. It is the bug the whole transform exists for."""
    out = apply_transforms(_profile(), [_window(
        column="cum", op="diff", partition_by=["sym"], **{"as": "share"})])
    assert out["share"].tolist() == pytest.approx(
        [None, 0.3, 0.5, None, 0.3, 0.3], nan_ok=True)
    assert out.groupby("sym")["share"].sum().round(6).tolist() == [0.8, 0.6]


def test_an_unpartitioned_diff_is_the_plain_series_difference():
    out = apply_transforms(_profile(), [_window(
        column="cum", op="diff", **{"as": "d"})])
    assert out["d"].iloc[3] == pytest.approx(-0.6)


def test_a_cumulative_sum_partitions_too():
    out = apply_transforms(_profile(), [_window(
        column="t", op="cumsum", partition_by=["sym"], **{"as": "run"})])
    assert out["run"].tolist() == [1, 3, 6, 1, 3, 6]


def test_shift_moves_values_down_within_a_partition():
    out = apply_transforms(_profile(), [_window(
        column="cum", op="shift", periods=1, partition_by=["sym"],
        **{"as": "prev"})])
    assert out["prev"].iloc[3] is None or pd.isna(out["prev"].iloc[3])
    assert out["prev"].iloc[4] == pytest.approx(0.4)


def test_row_number_counts_from_zero_within_each_partition():
    """This is what makes an even-pace reference computable."""
    out = apply_transforms(_profile(), [_window(
        op="row_number", partition_by=["sym"], **{"as": "n"})])
    assert out["n"].tolist() == [0, 1, 2, 0, 1, 2]


def test_a_rolling_mean_partitions_and_keeps_position():
    out = apply_transforms(_profile(), [_window(
        column="cum", op="rolling_mean", periods=2, partition_by=["sym"],
        **{"as": "r"})])
    assert out["r"].iloc[1] == pytest.approx(0.35)
    assert pd.isna(out["r"].iloc[3])          # first row of the next partition


def test_the_frame_is_not_reordered():
    """These files are in session order; a profile resorted around midnight is
    wrong."""
    frame = _profile().iloc[[3, 0, 4, 1, 5, 2]].reset_index(drop=True)
    out = apply_transforms(frame, [_window(
        column="cum", op="diff", partition_by=["sym"], **{"as": "d"})])
    assert out["sym"].tolist() == frame["sym"].tolist()
    assert out["t"].tolist() == frame["t"].tolist()


def test_an_empty_frame_gains_the_column_rather_than_raising():
    empty = pd.DataFrame({"sym": [], "cum": []})
    out = apply_transforms(empty, [_window(
        column="cum", op="diff", partition_by=["sym"], **{"as": "d"})])
    assert "d" in out.columns and len(out) == 0


def test_a_missing_column_says_which_and_what_there_was():
    with pytest.raises(ValueError, match="nope"):
        apply_transforms(_profile(), [_window(
            column="nope", op="diff", **{"as": "d"})])


def test_a_missing_partition_column_says_which():
    with pytest.raises(ValueError, match="ghost"):
        apply_transforms(_profile(), [_window(
            column="cum", op="diff", partition_by=["ghost"], **{"as": "d"})])


def test_an_unknown_op_says_what_it_was():
    with pytest.raises(ValueError, match="fourier"):
        apply_transforms(_profile(), [_window(
            column="cum", op="fourier", **{"as": "d"})])


def test_row_number_needs_no_source_column():
    out = apply_transforms(_profile(), [_window(op="row_number", **{"as": "n"})])
    assert out["n"].tolist() == [0, 1, 2, 3, 4, 5]


def test_the_new_column_may_replace_an_existing_one():
    out = apply_transforms(_profile(), [_window(
        column="cum", op="cumsum", **{"as": "cum"})])
    assert out["cum"].iloc[1] == pytest.approx(0.7)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_transform_window.py -v`
Expected: FAIL with `ValueError: unknown transform: window`

- [ ] **Step 3: Implement `_window`**

In `kdbmonitor/core/transform.py`, add after `_rename`:

```python
# Row-over-row arithmetic, and the reason it is a transform of its own rather
# than an expression: it has to be able to partition. A volume profile stacks
# every instrument in one frame, so the difference between consecutive rows
# walks out of one instrument and into the next at every boundary — which does
# not raise, it just reports a share of -1.0 and makes the instrument's shares
# sum to zero.
_WINDOW_OPS = ("diff", "cumsum", "shift", "rolling_mean", "rolling_sum",
               "row_number")


def _window(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    op, target = p.get("op", "diff"), p["as"]
    keys = list(p.get("partition_by") or [])
    periods = int(p.get("periods") or 1)
    if op not in _WINDOW_OPS:
        raise ValueError(f"unknown window op: {op} "
                         f"(have: {', '.join(_WINDOW_OPS)})")
    for k in keys:
        _need(df, k, "window")

    if op == "row_number":
        if df.empty:
            df[target] = pd.Series(dtype="int64")
            return df
        df[target] = (df.groupby(keys, dropna=False).cumcount() if keys
                      else pd.Series(range(len(df)), index=df.index))
        return df

    column = p["column"]
    _need(df, column, "window")
    if df.empty:
        df[target] = pd.Series(dtype=df[column].dtype)
        return df

    def run(series: pd.Series) -> pd.Series:
        if op == "diff":
            return series.diff(periods)
        if op == "cumsum":
            return series.cumsum()
        if op == "shift":
            return series.shift(periods)
        if op == "rolling_mean":
            return series.rolling(periods).mean()
        return series.rolling(periods).sum()

    # transform() keeps every value where it was: these files are in session
    # order, and a profile resorted around midnight is wrong.
    df[target] = (df.groupby(keys, dropna=False)[column].transform(run) if keys
                  else run(df[column]))
    return df
```

Register it:

```python
_KINDS: dict[str, Callable[[pd.DataFrame, dict], pd.DataFrame]] = {
    "derive": _derive, "filter": _filter, "groupby": _groupby,
    "sort": _sort, "limit": _limit, "rename": _rename, "window": _window,
}
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_transform_window.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Add the summary line**

`core/summaries.py` has `transform_summary`, used by the editor's step captions. Add a `window` branch reading like the others, e.g. `window: diff of cum by sym -> share`. Read the existing branches and match their phrasing.

- [ ] **Step 6: Run the whole suite**

Run: `PYTHONPATH=. python -m pytest tests -q`
Expected: 820 + 13 = 833 pass.

- [ ] **Step 7: Commit**

```bash
git add kdbmonitor/core/transform.py kdbmonitor/core/summaries.py tests/test_transform_window.py
git commit -m "feat: a running difference stops at the edge of its own instrument"
```

---

## Task 2: `derive` takes arithmetic, not code

**Files:**
- Modify: `kdbmonitor/core/transform.py`
- Test: `tests/test_transform_window.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transform_window.py`:

```python
def _derive(expr: str) -> Transform:
    return Transform(kind="derive", params={
        "column": "x", "kind": "arithmetic", "expr": expr})


def test_ordinary_arithmetic_still_evaluates():
    df = pd.DataFrame({"a": [1.0, 2.0], "b": [10.0, 20.0]})
    out = apply_transforms(df, [_derive("a + b * 2")])
    assert out["x"].tolist() == [21.0, 42.0]


def test_comparisons_and_logic_still_evaluate():
    df = pd.DataFrame({"a": [1, 5], "b": [4, 4]})
    out = apply_transforms(df, [_derive("(a > 2) and (b == 4)")])
    assert out["x"].tolist() == [False, True]


def test_a_method_call_is_refused_and_points_at_the_window_transform():
    """It used to evaluate, and on a stacked frame it was silently wrong."""
    df = pd.DataFrame({"cum": [0.2, 0.5]})
    with pytest.raises(ValueError, match="window"):
        apply_transforms(df, [_derive("cum.diff()")])


def test_attribute_traversal_is_refused():
    """cum.__class__.__mro__ reached Python's class hierarchy from a stored
    dashboard, and dashboards are imported from other people."""
    df = pd.DataFrame({"cum": [0.2, 0.5]})
    for expr in ("cum.__class__", "cum.__class__.__mro__", "cum.to_numpy()"):
        with pytest.raises(ValueError):
            apply_transforms(df, [_derive(expr)])


def test_a_subscript_is_refused():
    df = pd.DataFrame({"cum": [0.2, 0.5]})
    with pytest.raises(ValueError):
        apply_transforms(df, [_derive("cum[0]")])


def test_a_lambda_is_refused():
    df = pd.DataFrame({"cum": [0.2, 0.5]})
    with pytest.raises(ValueError):
        apply_transforms(df, [_derive("(lambda: 1)()")])


def test_nonsense_is_refused_without_a_traceback():
    df = pd.DataFrame({"cum": [0.2]})
    with pytest.raises(ValueError):
        apply_transforms(df, [_derive("a +")])


def test_the_refusal_names_what_it_objected_to():
    df = pd.DataFrame({"cum": [0.2]})
    try:
        apply_transforms(df, [_derive("cum.diff()")])
    except ValueError as exc:
        assert "diff" in str(exc) or "call" in str(exc).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_transform_window.py -k "derive or arithmetic or refused or method or attribute" -v`
Expected: the refusal tests FAIL — the expressions currently evaluate.

- [ ] **Step 3: Implement the guard**

Add to `kdbmonitor/core/transform.py`, above `_derive`:

```python
import ast

# What an arithmetic expression may be made of. The module's promise is that a
# transform is data rather than a Python snippet, because dashboards are stored
# in the database and imported from other people — and pandas' expression engine
# does not keep that promise on its own: it chains method calls and walks
# attributes as far as Python's class hierarchy.
_EXPR_NODES = (
    ast.Expression, ast.Name, ast.Load, ast.Constant,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.UAdd, ast.USub, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)

_WINDOW_HINT = ("For .diff(), .shift() or a running total, use a window "
                "transform — which can also partition by another column, so a "
                "difference stops at the edge of each instrument.")


def check_expression(expr: str) -> None:
    """Refuse anything that is not arithmetic over column names.

    Raises ``ValueError`` naming what it objected to. Called before the
    expression reaches pandas, because by then it is too late.
    """
    try:
        tree = ast.parse(expr or "", mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"derive: '{expr}' is not an expression ({exc.msg})")

    for node in ast.walk(tree):
        if isinstance(node, _EXPR_NODES):
            continue
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or \
                getattr(node.func, "id", "that")
            raise ValueError(
                f"derive takes arithmetic over columns, not calls, so "
                f"'{name}' cannot be used here. {_WINDOW_HINT}")
        if isinstance(node, ast.Attribute):
            raise ValueError(
                f"derive takes arithmetic over columns, not attributes, so "
                f"'.{node.attr}' cannot be used here. {_WINDOW_HINT}")
        raise ValueError(
            f"derive takes arithmetic over columns; "
            f"{type(node).__name__} is not allowed in an expression.")
```

Call it at the top of `_derive`'s arithmetic branch, before `df.eval`:

```python
    if kind == "arithmetic":
        check_expression(p["expr"])
        df[column] = _no_infinities(df.eval(p["expr"])) if len(df) \
            else pd.Series(dtype="float64")
        return df
```

And correct `_derive`'s docstring, which currently claims this was already true.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_transform_window.py -v`
Expected: PASS, 21 tests.

- [ ] **Step 5: Run the whole suite — watch for existing dashboards**

Run: `PYTHONPATH=. python -m pytest tests -q`
Expected: 833 + 8 = 841 pass. If an existing test used a method call in a derive
expression, that test was asserting the hole — report it rather than widening
the allowlist to accommodate it.

- [ ] **Step 6: Add the check to editor validation**

`_transform_problems` in `ui/dashboard_editor.py` checks each transform. Add a
`derive`/arithmetic branch calling `check_expression` and turning the
`ValueError` into a problem string, so a bad expression is caught at build time
rather than as a red panel.

- [ ] **Step 7: Commit**

```bash
git add kdbmonitor/core/transform.py kdbmonitor/ui/dashboard_editor.py tests/test_transform_window.py
git commit -m "fix: a derive expression is arithmetic, as it always claimed to be"
```

---

# Phase B — Parameters

## Task 3: The `Parameter` model

**Files:**
- Modify: `kdbmonitor/core/dashboard_models.py`
- Test: `tests/test_schema.py` (append)

- [ ] **Step 1: Write the failing test**

```python
from kdbmonitor.core.dashboard_models import Parameter


def test_parameters_survive_a_round_trip():
    d = Dashboard(id=1, name="VP", parameters=[
        Parameter(name="instrument", label="Instrument", kind="column",
                  dataset="profile", column="sym", default="A"),
        Parameter(name="mode", kind="choice", choices=["local", "source"],
                  default="local")])
    back = dashboard_from_dict(dashboard_to_dict(d))
    assert [p.name for p in back.parameters] == ["instrument", "mode"]
    assert back.parameters[0].dataset == "profile"
    assert back.parameters[1].choices == ["local", "source"]


def test_a_dashboard_saved_before_parameters_reads_back_with_none():
    assert dashboard_from_dict({"name": "Old", "rows": []}).parameters == []


def test_a_parameter_whose_choices_are_not_a_list_reads_back_empty():
    back = dashboard_from_dict({"name": "X", "rows": [], "parameters": [
        {"name": "p", "choices": "oops"}]})
    assert back.parameters[0].choices == []


def test_parameters_that_are_not_a_list_read_back_as_none():
    assert dashboard_from_dict({"name": "X", "rows": [],
                                "parameters": "oops"}).parameters == []
```

- [ ] **Step 2: Run it, see it fail**

Run: `PYTHONPATH=. python -m pytest tests/test_schema.py -k parameter -v`
Expected: `ImportError: cannot import name 'Parameter'`

- [ ] **Step 3: Add the dataclass**

In `kdbmonitor/core/dashboard_models.py`, after `Widget`:

```python
PARAMETER_KINDS = ("choice", "column", "number", "date", "toggle")


@dataclass
class Parameter:
    """A value the person reading a dashboard chooses.

    The dashboard owns the definition and the default; the choice itself belongs
    to whoever is looking, so it lives in session state rather than here — two
    people reading the same report are entitled to different instruments.

    Referenced as ``{{param:name}}`` inside a transform's params or a widget's
    spec, which is the same substitution idiom as ``{{stepN.column}}`` and
    ``{{date_from}}`` rather than a fourth thing to learn.
    """
    name: str                    # referenced as {{param:name}}
    label: str = ""              # shown on the control; falls back to name
    kind: str = "choice"         # one of PARAMETER_KINDS
    choices: list[str] = field(default_factory=list)   # choice only
    dataset: str = ""            # column only: whose values to offer
    column: str = ""             # column only: which column
    default: str = ""
```

`Dashboard` gains `parameters: list[Parameter] = field(default_factory=list)`.

- [ ] **Step 4: Deserialise it defensively**

Following `_dict_list` and `_int` already in this file (added because a bundle
can be hand-edited and reading stored data must never raise):

```python
def _parameter_from_dict(d: dict) -> Parameter:
    choices = d.get("choices")
    return Parameter(
        name=d.get("name", ""), label=d.get("label", ""),
        kind=d.get("kind", "choice"),
        choices=[str(c) for c in choices] if isinstance(choices, list) else [],
        dataset=d.get("dataset", ""), column=d.get("column", ""),
        default=str(d.get("default", "")))
```

and in `dashboard_from_dict`:

```python
        parameters=[_parameter_from_dict(p)
                    for p in _dict_list(d.get("parameters"))],
```

- [ ] **Step 5: Run the tests, then the suite**

Run: `PYTHONPATH=. python -m pytest tests -q`
Expected: 841 + 4 = 845 pass.

- [ ] **Step 6: Commit**

```bash
git add kdbmonitor/core/dashboard_models.py tests/test_schema.py
git commit -m "feat: a dashboard can declare a value its reader chooses"
```

---

## Task 4: Substitution and resolution

**Files:**
- Create: `kdbmonitor/core/parameters.py`
- Test: `tests/test_parameters.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_parameters.py`:

```python
import pandas as pd
import pytest

from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Parameter, Transform, Widget,
)
from kdbmonitor.core.parameters import (
    choices_for, resolve_values, substitute, unresolved_params,
)


def test_a_placeholder_in_a_transform_param_is_filled():
    t = Transform(kind="filter", params={"column": "sym", "op": "=",
                                         "value": "{{param:instrument}}"})
    out = substitute(t.params, {"instrument": "ICICIBC.IN"})
    assert out["value"] == "ICICIBC.IN"


def test_substitution_reaches_into_nested_structures():
    spec = {"aggs": [{"column": "{{param:measure}}", "func": "sum"}],
            "labels": {"a": "{{param:measure}}"}}
    out = substitute(spec, {"measure": "qty"})
    assert out["aggs"][0]["column"] == "qty"
    assert out["labels"]["a"] == "qty"


def test_the_original_is_never_rewritten():
    """The dashboard is stored; substituting into it would persist one reader's
    choice for everybody."""
    params = {"value": "{{param:instrument}}"}
    substitute(params, {"instrument": "A"})
    assert params["value"] == "{{param:instrument}}"


def test_a_placeholder_with_no_parameter_is_left_alone():
    """Blanking it would make a filter match everything silently; left intact it
    surfaces as 'no column {{param:typo}}', which names the typo."""
    out = substitute({"value": "{{param:typo}}"}, {"instrument": "A"})
    assert out["value"] == "{{param:typo}}"


def test_a_placeholder_inside_a_longer_string_is_filled():
    out = substitute({"title": "Profile for {{param:instrument}}"},
                     {"instrument": "A"})
    assert out["title"] == "Profile for A"


def test_non_string_values_pass_through_untouched():
    out = substitute({"n": 5, "flag": True, "none": None}, {"x": "y"})
    assert out == {"n": 5, "flag": True, "none": None}


def test_unresolved_params_lists_every_name_used():
    t = Transform(kind="filter", params={"value": "{{param:a}}"})
    w = Widget(type="line", dataset="d", spec={"y": "{{param:b}}"})
    dash = Dashboard(id=1, name="D", datasets=[
        Dataset(name="d", env="", transforms=[t])])
    dash.rows = [__import__("kdbmonitor.core.dashboard_models",
                            fromlist=["Row"]).Row(widgets=[w])]
    assert unresolved_params(dash) == {"a", "b"}


# --- choices -----------------------------------------------------------------

def test_choices_come_from_the_raw_frame_in_order_without_duplicates():
    frames = {"profile": pd.DataFrame({"sym": ["B", "A", "B", "C"]})}
    p = Parameter(name="i", kind="column", dataset="profile", column="sym")
    assert choices_for(p, frames) == ["B", "A", "C"]


def test_choices_are_empty_when_the_dataset_has_not_run():
    p = Parameter(name="i", kind="column", dataset="missing", column="sym")
    assert choices_for(p, {}) == []


def test_choices_are_empty_when_the_column_is_absent():
    frames = {"profile": pd.DataFrame({"other": [1]})}
    p = Parameter(name="i", kind="column", dataset="profile", column="sym")
    assert choices_for(p, frames) == []


def test_a_choice_parameter_offers_what_it_was_given():
    p = Parameter(name="m", kind="choice", choices=["local", "source"])
    assert choices_for(p, {}) == ["local", "source"]


# --- resolution --------------------------------------------------------------

def test_a_chosen_value_wins_over_the_default():
    p = Parameter(name="i", kind="choice", choices=["A", "B"], default="A")
    assert resolve_values([p], {"i": "B"}, {})["i"] == "B"


def test_the_default_is_used_when_nothing_was_chosen():
    p = Parameter(name="i", kind="choice", choices=["A", "B"], default="A")
    assert resolve_values([p], {}, {})["i"] == "A"


def test_a_chosen_value_that_is_no_longer_offered_falls_back():
    """Somebody's selection outlives the file that offered it."""
    frames = {"d": pd.DataFrame({"sym": ["A", "B"]})}
    p = Parameter(name="i", kind="column", dataset="d", column="sym",
                  default="A")
    assert resolve_values([p], {"i": "GONE"}, frames)["i"] == "A"


def test_a_default_that_is_not_offered_falls_back_to_the_first_choice():
    frames = {"d": pd.DataFrame({"sym": ["A", "B"]})}
    p = Parameter(name="i", kind="column", dataset="d", column="sym",
                  default="ALSO GONE")
    assert resolve_values([p], {}, frames)["i"] == "A"


def test_a_free_parameter_takes_whatever_it_is_given():
    """number/date/toggle have no choice list to validate against."""
    p = Parameter(name="n", kind="number", default="10")
    assert resolve_values([p], {"n": "42"}, {})["n"] == "42"


def test_resolution_never_raises_on_a_parameter_it_cannot_satisfy():
    p = Parameter(name="i", kind="column", dataset="gone", column="x")
    assert resolve_values([p], {}, {})["i"] == ""
```

- [ ] **Step 2: Run it, see it fail**

Run: `PYTHONPATH=. python -m pytest tests/test_parameters.py -v`
Expected: `ModuleNotFoundError: kdbmonitor.core.parameters`

- [ ] **Step 3: Implement it**

Create `kdbmonitor/core/parameters.py`:

```python
"""Values the reader of a dashboard chooses, and how they reach its transforms.

Substitution rather than a new mechanism: ``{{param:name}}`` is filled the same
way ``{{stepN.column}}`` and ``{{date_from}}`` already are, so there is one idea
about how a stored dashboard gets a run-time value into it rather than four.

Nothing here reaches a query. A parameter feeds transforms and widget specs, so
changing one re-shapes frames already in hand instead of going back to the
server — which keeps a control change distinct from a refresh, and keeps a
historical dashboard from re-reading partitions every time somebody picks
something.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Optional

import pandas as pd

from kdbmonitor.core.dashboard_models import Dashboard, Parameter

_PARAM_REF = re.compile(r"\{\{param:([^{}]+)\}\}")


def substitute(value: Any, values: dict[str, str]) -> Any:
    """``value`` with every ``{{param:name}}`` filled in, copied not rewritten.

    A placeholder naming a parameter that does not exist is left as it stands.
    Blanking it would turn a filter into one that matches everything and says
    nothing; left intact it arrives as "no column '{{param:typo}}'", which names
    the mistake.
    """
    if isinstance(value, str):
        return _PARAM_REF.sub(
            lambda m: values.get(m.group(1).strip(), m.group(0)), value)
    if isinstance(value, dict):
        return {k: substitute(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, values) for v in value]
    return copy.deepcopy(value)


def params_in(value: Any) -> set[str]:
    """Every parameter name referenced anywhere inside ``value``."""
    if isinstance(value, str):
        return {m.group(1).strip() for m in _PARAM_REF.finditer(value)}
    if isinstance(value, dict):
        return set().union(*(params_in(v) for v in value.values())) \
            if value else set()
    if isinstance(value, list):
        return set().union(*(params_in(v) for v in value)) if value else set()
    return set()


def unresolved_params(dashboard: Dashboard) -> set[str]:
    """Every ``{{param:...}}`` used by this dashboard's transforms or widgets."""
    used: set[str] = set()
    for ds in dashboard.datasets:
        for t in ds.transforms:
            used |= params_in(t.params)
    for row in dashboard.rows:
        for w in row.widgets:
            used |= params_in(w.spec)
            used |= params_in(w.title)
    return used


def choices_for(parameter: Parameter,
                frames: dict[str, pd.DataFrame]) -> list[str]:
    """What this parameter offers.

    A ``column`` parameter reads the **raw** frame of its dataset, before that
    dataset's transforms run — which is the only frame where every instrument is
    still there. After the filter the parameter itself drives, exactly one is.
    """
    if parameter.kind != "column":
        return [str(c) for c in parameter.choices]
    frame = frames.get(parameter.dataset)
    if frame is None or parameter.column not in getattr(frame, "columns", []):
        return []
    seen = frame[parameter.column].dropna().tolist()
    return [str(v) for v in dict.fromkeys(seen)]


def resolve_values(parameters: list[Parameter], chosen: dict[str, str],
                   frames: dict[str, pd.DataFrame]) -> dict[str, str]:
    """The value each parameter actually has, ready to substitute.

    A chosen value that is no longer on offer falls back to the default, and a
    default no longer on offer falls back to the first choice: a selection
    outlives the file that offered it, and a dashboard that renders nothing
    because of a stale pick is worse than one that renders something and lets
    the reader change it.
    """
    out: dict[str, str] = {}
    for p in parameters:
        options = choices_for(p, frames)
        value = chosen.get(p.name, p.default)
        if options:
            if value not in options:
                value = p.default if p.default in options else options[0]
        elif p.kind in ("choice", "column"):
            value = p.default if p.default else ""
        out[p.name] = "" if value is None else str(value)
    return out
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_parameters.py -v`
Expected: PASS, 18 tests.

- [ ] **Step 5: Run the whole suite, then commit**

Run: `PYTHONPATH=. python -m pytest tests -q` → 845 + 18 = 863 pass.

```bash
git add kdbmonitor/core/parameters.py tests/test_parameters.py
git commit -m "feat: a reader's choice reaches a transform the way every other value does"
```

---

## Task 5: Parameters through the dataset pipeline

**Files:**
- Modify: `kdbmonitor/core/dataset.py`
- Test: `tests/test_parameters.py` (append)

- [ ] **Step 1: Write the failing test**

```python
from datetime import date

from kdbmonitor.core.dashboard_models import ColumnSpec, FileShape, Row
from kdbmonitor.core.dataset import run_datasets

TODAY = date(2026, 7, 31)


def _profile_frame() -> pd.DataFrame:
    return pd.DataFrame({"sym": ["A", "A", "B", "B"],
                         "cum": [0.4, 1.0, 0.3, 1.0]})


def _dash(**kw) -> Dashboard:
    return Dashboard(
        id=1, name="VP", source="file",
        parameters=kw.get("parameters", []),
        datasets=[Dataset(
            name="profile", env="", source="file",
            shape=FileShape(columns=[ColumnSpec(name="sym"),
                                     ColumnSpec(name="cum", type="number")]),
            transforms=kw.get("transforms", []))])


def test_a_parameter_filters_the_frame_a_widget_sees():
    dash = _dash(
        parameters=[Parameter(name="instrument", kind="column",
                              dataset="profile", column="sym", default="A")],
        transforms=[Transform(kind="filter", params={
            "column": "sym", "op": "=", "value": "{{param:instrument}}"})])
    out = run_datasets(dash, None, None, TODAY,
                       uploads={"profile": _profile_frame()},
                       chosen={"instrument": "B"})
    assert out["profile"].df["sym"].tolist() == ["B", "B"]


def test_without_a_choice_the_default_applies():
    dash = _dash(
        parameters=[Parameter(name="instrument", kind="column",
                              dataset="profile", column="sym", default="A")],
        transforms=[Transform(kind="filter", params={
            "column": "sym", "op": "=", "value": "{{param:instrument}}"})])
    out = run_datasets(dash, None, None, TODAY,
                       uploads={"profile": _profile_frame()})
    assert out["profile"].df["sym"].tolist() == ["A", "A"]


def test_the_stored_transform_is_not_rewritten_by_a_run():
    t = Transform(kind="filter", params={"column": "sym", "op": "=",
                                         "value": "{{param:instrument}}"})
    dash = _dash(parameters=[Parameter(name="instrument", kind="column",
                                       dataset="profile", column="sym",
                                       default="A")],
                 transforms=[t])
    run_datasets(dash, None, None, TODAY, uploads={"profile": _profile_frame()},
                 chosen={"instrument": "B"})
    assert t.params["value"] == "{{param:instrument}}"


def test_choices_are_taken_before_the_parameter_filters_them_away():
    """The point of reading the raw frame: after the filter only one is left."""
    dash = _dash(
        parameters=[Parameter(name="instrument", kind="column",
                              dataset="profile", column="sym", default="A")],
        transforms=[Transform(kind="filter", params={
            "column": "sym", "op": "=", "value": "{{param:instrument}}"})])
    results = run_datasets(dash, None, None, TODAY,
                           uploads={"profile": _profile_frame()},
                           chosen={"instrument": "B"})
    assert results["profile"].choices["instrument"] == ["A", "B"]


def test_a_dashboard_with_no_parameters_runs_exactly_as_before():
    dash = _dash()
    out = run_datasets(dash, None, None, TODAY,
                       uploads={"profile": _profile_frame()})
    assert len(out["profile"].df) == 4
```

- [ ] **Step 2: Run it, see it fail**

Expected: `TypeError: run_datasets() got an unexpected keyword argument 'chosen'`

- [ ] **Step 3: Implement**

In `kdbmonitor/core/dataset.py`:

- `DatasetResult` gains `choices: dict = field(default_factory=dict)` — what each
  parameter offered, computed from this dataset's raw frame, so the UI can build
  the control without running anything a second time.
- `run_dataset(...)` gains `values: Optional[dict] = None`, and applies
  `parameters.substitute(t.params, values)` to each transform before
  `apply_transforms`. Build the substituted `Transform` list once:

```python
    from kdbmonitor.core import parameters as params_mod

    shaped = [replace(t, params=params_mod.substitute(t.params, values or {}))
              for t in ds.transforms]
```

  (`replace` from `dataclasses`; do NOT mutate `ds.transforms`.)

- `run_datasets(dashboard, store, mgr, today, uploads=None, chosen=None)`:
  1. fetch each dataset's raw frame as now
  2. after each fetch, record it in a `raw` dict by dataset name
  3. resolve parameter values against `raw` **before** applying that dataset's
     transforms — so a parameter reading dataset A is available to dataset B,
     provided A is declared first (spec §3.4)
  4. attach `choices` to each result

  Keep the existing `outputs` feed-forward untouched.

- `trace_datasets` takes and forwards the same arguments.

- [ ] **Step 4: Run the tests, the suite, and commit**

Run: `PYTHONPATH=. python -m pytest tests -q` → 863 + 5 = 868 pass.

```bash
git add kdbmonitor/core/dataset.py tests/test_parameters.py
git commit -m "feat: a dashboard runs against the choices its reader has made"
```

---

## Task 6: The parameter controls

**Files:**
- Create: `kdbmonitor/ui/parameters.py`
- Modify: `kdbmonitor/ui/dashboards.py`

No unit tests — Streamlit. Verify by import and by the smoke check in Step 4.

- [ ] **Step 1: Create the module**

```python
"""The controls a dashboard's parameters are set with.

Nothing here decides anything: which values are on offer and which one applies
are both settled in ``core.parameters``. This renders the controls, remembers
what was picked for this reader, and drops the derived frames when it changes.
"""
from __future__ import annotations

import streamlit as st

from kdbmonitor.core.dashboard_models import Dashboard, Parameter


def value_key(dashboard_id: int, name: str) -> str:
    return f"dash_param_{dashboard_id}_{name}"


def chosen_values(dashboard: Dashboard) -> dict[str, str]:
    """What this reader has picked so far. Missing names fall back downstream."""
    out = {}
    for p in dashboard.parameters:
        held = st.session_state.get(value_key(dashboard.id, p.name))
        if held is not None:
            out[p.name] = str(held)
    return out


def render(dashboard: Dashboard, choices: dict[str, list[str]],
           on_change) -> None:
    """One row of controls above the dashboard.

    ``choices`` comes from the last run's results, so a picker offers what the
    data actually held rather than what it held when the dashboard was saved.
    ``on_change`` drops the derived frames — the fetched ones stand, because a
    parameter never reaches a query.
    """
    if not dashboard.parameters:
        return
    cols = st.columns(min(len(dashboard.parameters), 4))
    for i, p in enumerate(dashboard.parameters):
        _control(cols[i % len(cols)], dashboard, p,
                 choices.get(p.name, []), on_change)


def _control(container, dashboard: Dashboard, p: Parameter,
             options: list[str], on_change) -> None:
    key = value_key(dashboard.id, p.name)
    label = p.label or p.name
    if p.kind in ("choice", "column"):
        offered = options or [c for c in p.choices] or [p.default]
        current = st.session_state.get(key, p.default)
        index = offered.index(current) if current in offered else 0
        picked = container.selectbox(label, offered, index=index, key=key)
        if picked != current:
            on_change()
    elif p.kind == "toggle":
        container.checkbox(label, value=str(p.default).lower() == "true",
                           key=key, on_change=on_change)
    elif p.kind == "number":
        container.number_input(label, value=float(p.default or 0), key=key,
                               on_change=on_change)
    else:
        container.text_input(label, value=p.default, key=key,
                             on_change=on_change)
```

- [ ] **Step 2: Wire into `ui/dashboards.py`**

- `refresh(store, mgr, dashboard, uploads=None)` gains `chosen=None` and passes
  it to `run_datasets`.
- Add `drop_derived(dashboard_id)` beside `force_refresh`: it pops the frames and
  the PDF cache, exactly as `force_refresh` does, and reruns. For a file
  dashboard the uploaded frames live in a *different* session key
  (`uploads_key`), so they survive — which is the whole point.
- In `_render_view`, before `_render_uploads`, render the controls with the
  choices from the last payload:

```python
    payload = st.session_state.get(frames_key(dashboard.id))
    choices = {}
    if payload:
        for res in payload["results"].values():
            choices.update(res.choices or {})
    parameters.render(dashboard, choices,
                      on_change=lambda: drop_derived(dashboard.id))
```

- Pass `chosen_values(dashboard)` into `refresh`.

- [ ] **Step 3: Editor**

`ui/dashboard_editor.py` gains a **Parameters** section: add/remove/reorder,
name, label, kind, choices (for `choice`), dataset+column pickers (for `column`),
default. Follow `_dataset_card`'s shape — an expander per parameter with a
columns row — and use `_forget(r"pm\d+")` on delete, matching the dataset cards.

The editor's own preview should render the controls too, so the author sees the
dashboard as its reader will.

- [ ] **Step 4: Verify**

```bash
cd "C:\Users\user\Desktop\Work\Projects\KHALI Clothes\KdbMonitor" && PYTHONPATH=. python -c "
import kdbmonitor.ui.parameters, kdbmonitor.ui.dashboards, kdbmonitor.ui.dashboard_editor
print('imports clean')"
PYTHONPATH=. python -m pytest tests -q
```

Expected: imports clean; 868 still passing.

- [ ] **Step 5: Commit**

```bash
git add kdbmonitor/ui/parameters.py kdbmonitor/ui/dashboards.py kdbmonitor/ui/dashboard_editor.py
git commit -m "feat: the person reading a dashboard can steer it"
```

---

## Task 7: Parameters on the printed page, and validation

**Files:**
- Modify: `kdbmonitor/core/dashpdf.py`, `kdbmonitor/ui/dashboard_editor.py`
- Test: `tests/test_dashpdf.py`, `tests/test_dashboard_validation.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_dashpdf.py`:

```python
def test_the_chosen_parameters_are_named_on_the_first_page():
    """A report filtered to one instrument that does not say which is worse
    than no report — and a PDF outlives the screen it came from."""
    import matplotlib.pyplot as plt
    from kdbmonitor.core.dashpdf import _header

    fig = plt.figure()
    dash = _dash([])
    _header(fig, dash, RT, AS_OF, first=True,
            chosen={"instrument": "ICICIBC.IN"})
    text = " ".join(t.get_text() for t in fig.texts)
    plt.close(fig)
    assert "instrument" in text and "ICICIBC.IN" in text


def test_a_dashboard_with_no_parameters_prints_no_caption():
    import matplotlib.pyplot as plt
    from kdbmonitor.core.dashpdf import _header

    fig = plt.figure()
    _header(fig, _dash([]), RT, AS_OF, first=True, chosen={})
    lines = [t.get_text() for t in fig.texts]
    plt.close(fig)
    assert len(lines) == 2                 # name and period only


def test_continuation_pages_carry_no_parameter_caption():
    import matplotlib.pyplot as plt
    from kdbmonitor.core.dashpdf import _header

    fig = plt.figure()
    _header(fig, _dash([]), RT, AS_OF, first=False,
            chosen={"instrument": "A"})
    assert fig.texts == []
    plt.close(fig)
```

In `tests/test_dashboard_validation.py`, one test per rule in spec §3.10.

- [ ] **Step 2: Implement**

- `_header(..., chosen: dict | None = None)` draws a third line under the period
  when `chosen` is non-empty, in `theme.MUTED`, reading
  `instrument: ICICIBC.IN · mode: local`. `HEADER_H_FIRST` grows by ~0.18in when
  there is one, so the first row is not printed over.
- `_render_page`, `dashboard_to_pdf_bytes`, `dashboard_page_png_bytes`,
  `report_plan` and `page_count` thread `chosen` through.
- `ui/dashboards.py` passes the resolved values when generating.
- `validate` gains the §3.10 rules, in a `_parameter_problems(draft)` helper
  beside `_file_dataset_problems`.

- [ ] **Step 3: Run the suite and commit**

```bash
git add kdbmonitor/core/dashpdf.py kdbmonitor/ui/dashboard_editor.py kdbmonitor/ui/dashboards.py tests/
git commit -m "feat: a printed report says which selection produced it"
```

---

# Phase C — Charts

## Task 8: Reference lines and bands in the model

**Files:**
- Modify: `kdbmonitor/core/plotmodel.py`
- Test: `tests/test_plotmodel_references.py` (new)

- [ ] **Step 1: Write the failing test**

Cover: a constant reference resolved with its label; a column reference resolved
to its values; a reference naming an absent column dropped and noted; bands
resolved with their x positions; a band whose endpoints are absent dropped;
references ignored for `pie`/`heatmap`/`kpi`/`table`; an empty frame yielding no
references rather than raising.

- [ ] **Step 2: Implement**

```python
@dataclass
class Reference:
    """A dashed line across a chart: a level, or a series to compare against."""
    label: str = ""
    value: Optional[float] = None        # constant
    values: list[float] = field(default_factory=list)   # column
    dash: str = "dash"


@dataclass
class Band:
    """A shaded span behind the plot — the pre-open stretch, a lunch break."""
    start: Any = None
    end: Any = None
    label: str = ""
```

`PlotModel` gains `references: list[Reference]` and `bands: list[Band]`,
resolved in `_xy_series`'s callers for `line`, `bar` and `scatter` only.

- [ ] **Step 3: Run, commit**

```bash
git commit -m "feat: a chart can carry the line it should be read against"
```

## Task 9: Drawing them, and the editor forms

**Files:**
- Modify: `kdbmonitor/core/render_plotly.py`, `kdbmonitor/core/render_mpl.py`, `kdbmonitor/ui/dashboard_editor.py`
- Test: `tests/test_render_mpl.py`, `tests/test_render_plotly.py`

- [ ] Draw references as dashed lines with a right-aligned label; draw bands as a
      shaded `axvspan` (matplotlib) / `add_vrect` (plotly) behind the series.
- [ ] A band label sits at the top of the span, in muted type.
- [ ] Editor: a repeatable form under the chart spec for references and bands.
- [ ] Tests: both renderers produce the artists; a reference does not change the
      y-axis limits when it lies outside the data (the spec's *average bucket*
      case, where the label should say so rather than rescaling the chart).

```bash
git commit -m "feat: a reference line is drawn on the screen and on the page alike"
```

---

# Phase D — Time zones

## Task 10: `core/zones.py`

**Files:** create `kdbmonitor/core/zones.py`, `tests/test_zones.py`

- [ ] `to_iana(name)` — Windows display names (~140 CLDR entries), bare
      abbreviations (`IST`, `CET`, `JST`), literal offsets (`UTC+05:30`), and
      IANA ids passed through. Unknown names raise naming what was given.
- [ ] `convert(series, from_zone, to_zone)` — localise and convert with
      `zoneinfo`, so DST is computed at each timestamp rather than assumed.
- [ ] `day_offset(before, after)` — `-1`, `0` or `+1`.
- [ ] Tests: a Windows name; an abbreviation; an offset; an IANA id; an unknown
      name; two timestamps either side of a DST transition converting
      differently; a crossing marked `-1d`.

```bash
git commit -m "feat: a Windows time zone name is a time zone"
```

## Task 11: The `timezone` transform

**Files:** modify `kdbmonitor/core/transform.py`, `tests/test_transform_window.py`

- [ ] `{"kind": "timezone", "params": {column, from_column|from_zone, to, as, day_offset_as}}`
- [ ] `to: "local"` resolves to the machine's zone.
- [ ] Row order preserved; an unknown zone raises naming the value and the row.
- [ ] Editor form; `transform_summary` branch.

```bash
git commit -m "feat: a bucket can be read in the zone of whoever is looking"
```

---

# Phase E — Tolerant parsing

## Task 12: Delimiter and encoding

**Files:** modify `kdbmonitor/core/filesource.py`, `kdbmonitor/core/dashboard_models.py`, `tests/test_filesource.py`

- [ ] `FileShape.delimiter: str = "auto"`; sniff among `, ; \t |` by picking the
      one giving the most consistent field count across the first 20 non-blank
      lines. An explicit setting overrides and is never sniffed around.
- [ ] Encoding: UTF-8, then cp1252, then latin-1, **noted** when it is not UTF-8.
- [ ] Tests: a semicolon file; a tab file; a pipe file; a cp1252 file with the
      note; a file where a quoted comma must not fool the sniffer; an explicit
      delimiter overriding a sniffable file.

```bash
git commit -m "feat: a file exported from a European locale is still a file"
```

## Task 13: Decimal commas and Excel time serials

**Files:** modify `kdbmonitor/core/filesource.py`, `tests/test_filesource.py`

- [ ] `_to_number` accepts `0,0215` where the delimiter is not a comma.
- [ ] `_to_date` accepts a bare fraction (`0.385416`) as a time of day, only
      where the declared type is `date`.
- [ ] Tests including that `"125,000"` still reads as 125000 in a comma file —
      the two rules must not collide.

```bash
git commit -m "feat: a comma is a decimal point where the file says it is"
```

---

# Phase F — Export

## Task 14: Download a dashboard's data

**Files:** modify `kdbmonitor/ui/dashboards.py`, `tests/test_exporting.py`

- [ ] A download control per dataset, CSV and Excel, of the frame **as the
      widgets see it** — after transforms, after parameters.
- [ ] Filename carries dashboard, dataset and the parameter values, so two
      exports at different selections do not overwrite each other. Put the
      filename builder in `core/exporting.py` and test it there.

```bash
git commit -m "feat: the numbers on a dashboard can be taken away from it"
```

---

## Task 15: The Volume Profile dashboard, end to end

**Files:** `tests/test_volume_profile.py` (new)

- [ ] Build the real thing in a test, against a fixture shaped like
      `sample_india_volume_profile.csv` (two instruments, a pre-open bucket, a
      closing spike):
  - load through `filesource` with header on line 2, data from line 3
  - an `instrument` parameter over the code column
  - a `window` diff partitioned by instrument for the per-bucket share
  - a `row_number` for even pace
  - a line chart of cumulated with an even-pace reference
  - a bar chart of the share with an average-bucket reference
  - a pre-open band
  - a table
- [ ] Assert: the shares sum to 1.0 for the selected instrument and **no `-1.0`
      appears anywhere**; switching the parameter switches the frame; the PDF
      renders and its first page names the instrument.

```bash
git commit -m "test: the profile a viewer draws is the profile this prints"
```

---

## Verification

```bash
PYTHONPATH=. python -m pytest tests -q          # every test passes
streamlit run app.py                             # then, by hand:
```

1. Open the file dashboard, upload `sample_india_volume_profile.csv`.
2. The instrument picker lists 20 codes; picking one redraws without re-reading
   the file.
3. Per-bucket shares sum to 1.0 for the chosen instrument.
4. Both charts show their dashed references; the pre-open band is shaded.
5. Generate the PDF — page 1 names the instrument under the date.
6. Download the CSV; the filename carries the instrument.
