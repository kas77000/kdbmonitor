"""The editor cannot be taken down by a value it does not recognise.

Every picker in the editor reads a value out of a stored dashboard, and a
stored dashboard can hold something this build does not offer: a transform kind
added since it was saved, a bundle written by a newer version, a field edited
by hand. ``list.index`` answers that by raising — and a raise there does not
spoil the one control, it blanks the whole editor page, so the dashboard cannot
even be opened to fix what is wrong with it.
"""
import pytest

from kdbmonitor.core.transform import TRANSFORM_KINDS, _KINDS
from kdbmonitor.ui.dashboard_editor import option_index


# --- the kinds are not written out twice ------------------------------------

def test_the_editor_offers_every_transform_the_app_can_run():
    """The editor kept its own list, window and timezone were added to the
    catalogue and not to it, and a dashboard using one could not be opened."""
    assert set(TRANSFORM_KINDS) == set(_KINDS)


def test_the_newer_transforms_are_among_them():
    for kind in ("window", "timezone"):
        assert kind in TRANSFORM_KINDS


def test_the_order_is_settled_so_a_picker_does_not_shuffle():
    assert list(TRANSFORM_KINDS) == list(TRANSFORM_KINDS)
    assert TRANSFORM_KINDS[0] == "derive"


# --- a picker survives what it does not know --------------------------------

def test_a_known_value_gives_its_own_position():
    assert option_index(["a", "b", "c"], "b") == 1


def test_an_unknown_value_falls_back_rather_than_raising():
    assert option_index(["a", "b"], "sasquatch") == 0


def test_a_missing_value_falls_back():
    assert option_index(["a", "b"], None) == 0


def test_a_value_of_the_wrong_type_falls_back():
    assert option_index(["a", "b"], {"not": "hashable-ish"}) == 0


def test_a_caller_may_choose_where_to_land():
    assert option_index(["a", "b", "c"], "nope", fallback=2) == 2


def test_a_fallback_off_the_end_still_lands_somewhere_real():
    assert option_index(["a", "b"], "nope", fallback=9) == 0


def test_an_empty_list_of_options_does_not_raise():
    assert option_index([], "anything") == 0


def test_a_tuple_of_options_works_as_well_as_a_list():
    assert option_index(TRANSFORM_KINDS, "window") == list(TRANSFORM_KINDS).index("window")


# --- the dashboard that found this ------------------------------------------

def test_the_shipped_volume_profile_uses_only_kinds_the_editor_offers():
    """It is built from window transforms, which is how the gap was found."""
    from pathlib import Path
    from kdbmonitor.core.portability import import_dashboards_json

    bundle = Path(__file__).resolve().parents[1] / "docs" / "examples" \
        / "volume_profile_dashboard.json"
    dash = import_dashboards_json(bundle.read_text(encoding="utf-8"))[0]
    for ds in dash.datasets:
        for t in ds.transforms:
            assert t.kind in TRANSFORM_KINDS, t.kind


def test_every_shipped_example_uses_only_kinds_the_editor_offers():
    from pathlib import Path
    from kdbmonitor.core.portability import import_dashboards_json

    examples = (Path(__file__).resolve().parents[1] / "docs" / "examples")
    for bundle in examples.glob("*.json"):
        for dash in import_dashboards_json(bundle.read_text(encoding="utf-8")):
            for ds in dash.datasets:
                for t in ds.transforms:
                    assert t.kind in TRANSFORM_KINDS, f"{bundle.name}: {t.kind}"
