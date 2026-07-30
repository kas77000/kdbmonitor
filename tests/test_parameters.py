import pandas as pd

from kdbmonitor.core.dashboard_models import (
    Dashboard, Dataset, Parameter, Row, Transform, Widget,
)
from kdbmonitor.core.parameters import (
    choices_for, params_in, resolve_values, substitute, unresolved_params,
)


# --- substitution ------------------------------------------------------------

def test_a_placeholder_in_a_transform_param_is_filled():
    params = {"column": "sym", "op": "=", "value": "{{param:instrument}}"}
    assert substitute(params, {"instrument": "ICICIBC.IN"})["value"] \
        == "ICICIBC.IN"


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


def test_a_nested_original_is_not_rewritten_either():
    spec = {"aggs": [{"column": "{{param:m}}"}]}
    substitute(spec, {"m": "qty"})
    assert spec["aggs"][0]["column"] == "{{param:m}}"


def test_a_placeholder_with_no_parameter_is_left_alone():
    """Blanking it would make a filter match everything silently; left intact it
    surfaces as 'no column {{param:typo}}', which names the typo."""
    assert substitute({"v": "{{param:typo}}"}, {"other": "A"})["v"] \
        == "{{param:typo}}"


def test_a_placeholder_inside_a_longer_string_is_filled():
    assert substitute({"t": "Profile for {{param:i}}"}, {"i": "A"})["t"] \
        == "Profile for A"


def test_two_placeholders_in_one_string_are_both_filled():
    out = substitute({"t": "{{param:a}} / {{param:b}}"}, {"a": "1", "b": "2"})
    assert out["t"] == "1 / 2"


def test_whitespace_inside_a_placeholder_is_tolerated():
    assert substitute({"v": "{{param: instrument }}"},
                      {"instrument": "A"})["v"] == "A"


def test_non_string_values_pass_through_untouched():
    out = substitute({"n": 5, "flag": True, "none": None, "f": 1.5},
                     {"x": "y"})
    assert out == {"n": 5, "flag": True, "none": None, "f": 1.5}


def test_a_bare_string_substitutes_too():
    assert substitute("{{param:a}}", {"a": "A"}) == "A"


# --- discovery ---------------------------------------------------------------

def test_params_in_finds_every_name_at_any_depth():
    assert params_in({"a": ["{{param:x}}", {"b": "{{param:y}}"}], "c": 1}) \
        == {"x", "y"}


def test_params_in_returns_nothing_for_plain_data():
    assert params_in({"a": [1, 2], "b": None}) == set()


def test_unresolved_params_covers_transforms_widgets_and_titles():
    dash = Dashboard(
        id=1, name="D",
        datasets=[Dataset(name="d", env="", transforms=[
            Transform(kind="filter", params={"value": "{{param:a}}"})])],
        rows=[Row(widgets=[Widget(type="line", dataset="d",
                                  title="for {{param:c}}",
                                  spec={"y": "{{param:b}}"})])])
    assert unresolved_params(dash) == {"a", "b", "c"}


def test_unresolved_params_of_a_plain_dashboard_is_empty():
    assert unresolved_params(Dashboard(id=1, name="D")) == set()


# --- choices -----------------------------------------------------------------

def test_choices_come_from_the_raw_frame_in_order_without_duplicates():
    frames = {"profile": pd.DataFrame({"sym": ["B", "A", "B", "C"]})}
    p = Parameter(name="i", kind="column", dataset="profile", column="sym")
    assert choices_for(p, frames) == ["B", "A", "C"]


def test_choices_skip_nulls():
    frames = {"p": pd.DataFrame({"sym": ["A", None, "B"]})}
    p = Parameter(name="i", kind="column", dataset="p", column="sym")
    assert choices_for(p, frames) == ["A", "B"]


def test_choices_are_empty_when_the_dataset_has_not_run():
    p = Parameter(name="i", kind="column", dataset="missing", column="sym")
    assert choices_for(p, {}) == []


def test_choices_are_empty_when_the_column_is_absent():
    frames = {"p": pd.DataFrame({"other": [1]})}
    p = Parameter(name="i", kind="column", dataset="p", column="sym")
    assert choices_for(p, frames) == []


def test_choices_are_empty_when_the_dataset_failed_and_left_no_frame():
    p = Parameter(name="i", kind="column", dataset="p", column="sym")
    assert choices_for(p, {"p": None}) == []


def test_a_choice_parameter_offers_what_it_was_given():
    p = Parameter(name="m", kind="choice", choices=["local", "source"])
    assert choices_for(p, {}) == ["local", "source"]


def test_numeric_column_values_are_offered_as_text():
    frames = {"p": pd.DataFrame({"n": [1, 2]})}
    p = Parameter(name="i", kind="column", dataset="p", column="n")
    assert choices_for(p, frames) == ["1", "2"]


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


def test_a_free_parameter_falls_back_to_its_default():
    p = Parameter(name="n", kind="number", default="10")
    assert resolve_values([p], {}, {})["n"] == "10"


def test_resolution_never_raises_on_a_parameter_it_cannot_satisfy():
    p = Parameter(name="i", kind="column", dataset="gone", column="x")
    assert resolve_values([p], {}, {})["i"] == ""


def test_every_parameter_gets_a_value_even_if_empty():
    ps = [Parameter(name="a", kind="column", dataset="x", column="y"),
          Parameter(name="b", kind="choice", choices=["1"], default="1")]
    assert set(resolve_values(ps, {}, {})) == {"a", "b"}


def test_resolved_values_are_always_text():
    p = Parameter(name="n", kind="number", default="10")
    assert isinstance(resolve_values([p], {"n": 42}, {})["n"], str)
