"""Where failure notices go, and refusing to save an address that isn't one."""
from kdbmonitor.core.reportrun import ADMIN_EMAIL_KEY
from kdbmonitor.core.storage import Storage
from kdbmonitor.ui.admin import admin_problems


def test_a_good_list_has_no_problems():
    assert admin_problems("kdb-support@corp.local; alice@corp.local") == []


def test_an_empty_list_is_allowed_here():
    """Not every install has jobs yet. The warning about an empty list belongs
    beside the jobs that need it, not on a field somebody has not reached."""
    assert admin_problems("") == []


def test_an_entry_without_an_at_sign_is_named():
    problems = admin_problems("alice@corp.local, nope, bob@corp.local")
    assert len(problems) == 1
    assert "nope" in problems[0]


def test_the_setting_key_is_the_one_reportrun_reads():
    """Two names for one setting is how a notice silently goes nowhere."""
    store = Storage(":memory:")
    store.init_db()
    store.set_setting(ADMIN_EMAIL_KEY, "admin@corp.local")
    assert store.get_setting("scheduler_admin_email") == "admin@corp.local"
