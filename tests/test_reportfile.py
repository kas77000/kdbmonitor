"""The report archive: a folder per dashboard, a file per run.

These tests own the two rules the rest of the feature leans on — that the
dashboard folder is created and the root never is, and that ``check_root``
answers in a sentence somebody can act on.
"""
import os
from datetime import datetime, timezone

import pytest

from kdbmonitor.core import reportfile

NOW = datetime(2026, 8, 20, 7, 0, 0, tzinfo=timezone.utc)


# --- the name ----------------------------------------------------------------

def test_the_name_carries_the_dashboard_and_the_run_time_to_the_second():
    assert (reportfile.archive_filename("Equity Orders", NOW)
            == "equity_orders_20260820_070000.pdf")


def test_two_runs_a_second_apart_are_two_different_names():
    """Seconds are in the name so a run can never overwrite another one."""
    later = NOW.replace(second=1)
    assert (reportfile.archive_filename("Equity Orders", NOW)
            != reportfile.archive_filename("Equity Orders", later))


def test_punctuation_in_a_dashboard_name_does_not_reach_the_filesystem():
    assert (reportfile.archive_filename("OMS: EU/US (live)", NOW)
            == "oms_eu_us_live_20260820_070000.pdf")


def test_a_dashboard_named_only_in_punctuation_still_gets_a_name():
    assert reportfile.archive_filename("///", NOW).startswith("dashboard_")


def test_the_time_written_is_utc_whatever_zone_the_run_carried():
    """The scheduler records fire times in UTC; a filename that drifted with
    the server's zone would sort wrong against the run history."""
    from datetime import timedelta
    tokyo = NOW.astimezone(timezone(timedelta(hours=9)))
    assert (reportfile.archive_filename("Equity Orders", tokyo)
            == "equity_orders_20260820_070000.pdf")


# --- writing -----------------------------------------------------------------

def test_the_dashboard_folder_is_created_on_the_first_write(tmp_path):
    written = reportfile.write_report(str(tmp_path), "Equity Orders", NOW,
                                      b"%PDF-1.4 fake")
    assert os.path.isdir(tmp_path / "equity_orders")
    assert written == str(tmp_path / "equity_orders"
                          / "equity_orders_20260820_070000.pdf")


def test_the_file_holds_the_bytes_it_was_given(tmp_path):
    written = reportfile.write_report(str(tmp_path), "Equity Orders", NOW,
                                      b"%PDF-1.4 fake")
    assert open(written, "rb").read() == b"%PDF-1.4 fake"


def test_a_second_run_sits_beside_the_first_rather_than_replacing_it(tmp_path):
    reportfile.write_report(str(tmp_path), "Equity Orders", NOW, b"one")
    reportfile.write_report(str(tmp_path), "Equity Orders",
                            NOW.replace(hour=16), b"two")
    assert len(os.listdir(tmp_path / "equity_orders")) == 2


def test_each_dashboard_gets_its_own_folder(tmp_path):
    reportfile.write_report(str(tmp_path), "Equity Orders", NOW, b"one")
    reportfile.write_report(str(tmp_path), "Short Sell", NOW, b"two")
    assert sorted(os.listdir(tmp_path)) == ["equity_orders", "short_sell"]


def test_a_second_write_reuses_the_folder_it_already_made(tmp_path):
    reportfile.write_report(str(tmp_path), "Equity Orders", NOW, b"one")
    reportfile.write_report(str(tmp_path), "Equity Orders",
                            NOW.replace(hour=16), b"two")
    assert sorted(os.listdir(tmp_path)) == ["equity_orders"]


def test_a_missing_root_is_refused_rather_than_conjured(tmp_path):
    """A typo in Admin must not quietly become a folder tree that nobody is
    looking in. The dashboard folder is ours to create; the root is not."""
    missing = tmp_path / "nas" / "reports"
    with pytest.raises(FileNotFoundError):
        reportfile.write_report(str(missing), "Equity Orders", NOW, b"pdf")
    assert not (tmp_path / "nas").exists()


# --- check_root --------------------------------------------------------------

def test_a_usable_folder_has_nothing_to_say_about_it(tmp_path):
    assert reportfile.check_root(str(tmp_path)) is None


def test_checking_a_folder_leaves_nothing_behind(tmp_path):
    reportfile.check_root(str(tmp_path))
    assert os.listdir(tmp_path) == []


def test_no_folder_set_says_so(tmp_path):
    assert reportfile.check_root("") == "no folder is set"
    assert reportfile.check_root("   ") == "no folder is set"
    assert reportfile.check_root(None) == "no folder is set"


def test_a_folder_that_is_not_there_says_so(tmp_path):
    assert reportfile.check_root(str(tmp_path / "nope")) == (
        "that folder does not exist")


def test_a_path_that_is_a_file_says_so(tmp_path):
    target = tmp_path / "reports.txt"
    target.write_text("not a folder")
    assert reportfile.check_root(str(target)) == (
        "that path is a file, not a folder")


def test_a_folder_that_refuses_a_write_says_so(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(reportfile, "open", refuse, raising=False)
    problem = reportfile.check_root(str(tmp_path))
    assert problem is not None
    assert "permission" in problem.lower()


def test_a_surrounding_space_in_the_path_is_not_a_missing_folder(tmp_path):
    """Pasted paths carry whitespace. Refusing one would be a mystery."""
    assert reportfile.check_root(f"  {tmp_path}  ") is None
