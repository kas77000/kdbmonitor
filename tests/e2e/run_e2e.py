"""End-to-end functional test of KdbMonitor through a real browser.

Drives the app the way a person does — clicks, types, saves, deletes — and after
every step asserts that Streamlit rendered no exception and no error banner.
This is the check the unit suite cannot make: the unit suite renders each page
once, in isolation, and never presses anything twice in a row.

Run against a throwaway seeded database; several scenarios create and delete.
"""
import json
import os
import sqlite3
import sys
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8531"
DB = sys.argv[1]
SHOTS = Path(sys.argv[2])
SHOTS.mkdir(parents=True, exist_ok=True)

results = []

# A list row is a bordered block. Every ancestor block also "has text", so the
# innermost match — the shortest innerText — is the row itself.
ROW_JS = r"""
(name) => {
  const all = [...document.querySelectorAll(
      '[data-testid="stVerticalBlockBorderWrapper"]')]
    .filter((e) => (e.innerText || '').indexOf(name) !== -1)
    // A popover's body is rendered into a portal and mentions the row it
    // belongs to, so it matches the name and holds buttons - and being
    // shorter than the row, it would win the sort below.
    .filter((e) => !e.closest('[data-testid="stPopover"]'))
    // Streamlit wraps every *column* in one of these too, so the smallest
    // match is a single cell. A row is the smallest one that still holds a
    // control - that is what makes it a row rather than a cell.
    .filter((e) => e.querySelector('[data-testid="stButton"] button,'
                                 + '[data-testid="stPopover"] button,'
                                 + '[data-testid="stDownloadButton"] button,'
                                 + '[data-testid="stCheckbox"]'));
  if (!all.length) return null;
  all.sort((a, b) => a.innerText.length - b.innerText.length);
  all[0].setAttribute('data-e2e-row', '1');
  return true;
}
"""


# --- plumbing ---------------------------------------------------------------

def settle(pg, ms=2600):
    pg.wait_for_timeout(ms)
    for _ in range(40):
        w = pg.query_selector('[data-testid="stStatusWidget"]')
        if w is None or not w.is_visible():
            break
        pg.wait_for_timeout(400)
    pg.wait_for_timeout(500)


def problems(pg):
    out = []
    for el in pg.query_selector_all('[data-testid="stException"]'):
        out.append("EXCEPTION: " + (el.inner_text() or "")[:300])
    for el in pg.query_selector_all('[data-testid="stAlertContentError"]'):
        txt = (el.inner_text() or "").strip()
        # A dataset pointed at an environment this fixture never had is the
        # fixture's business, not a regression.
        if "unknown environment" in txt or "Introspect failed" in txt:
            continue
        # The Monitor states a live trigger with st.error, on purpose - that is
        # the alert doing its job, not the page failing.
        if txt.startswith("notifications_active"):
            continue
        out.append("ERROR: " + txt[:300])
    return out


def check(pg, note=""):
    bad = problems(pg)
    if bad:
        raise AssertionError(f"{note}: " + " | ".join(bad))


def scenario(name):
    def wrap(fn):
        def run(pg):
            try:
                fn(pg)
            except Exception as exc:                       # noqa: BLE001
                try:
                    pg.screenshot(
                        path=str(SHOTS / f"e2e-FAIL-{name.replace(' ', '-')}.png"))
                except Exception:
                    pass
                results.append((name, False, f"{type(exc).__name__}: {exc}"))
                traceback.print_exc(limit=2)
            else:
                results.append((name, True, ""))
        return run
    return wrap


def goto(pg, path, marker):
    pg.goto(BASE + path, wait_until="domcontentloaded")
    pg.wait_for_selector(f"text={marker}", timeout=60000)
    settle(pg)


def _btns(scope, label, enabled=True):
    sel = ('[data-testid="stButton"] button, '
           '[data-testid="stDownloadButton"] button')
    out = []
    for e in scope.query_selector_all(sel):
        if not e.is_visible() or (enabled and not e.is_enabled()):
            continue
        if e.inner_text().strip().split("\n")[-1].strip() == label:
            out.append(e)
    return out


def _park_pointer(pg):
    """Move off whatever is hovered and let its tooltip go.

    A Streamlit help tooltip sits above the page and swallows the next click,
    so a click landing right after a hover can time out on nothing at all.
    """
    pg.mouse.move(4, 4)
    pg.wait_for_timeout(450)


def click(pg, label, index=0, note=""):
    els = _btns(pg, label)
    if len(els) <= index:
        raise AssertionError(
            f"no enabled button {label!r} #{index} (found {len(els)}) {note}")
    _park_pointer(pg)
    els[index].click()
    settle(pg)
    check(pg, f"after clicking {label!r}")


def row(pg, name):
    """The list row whose text starts with `name`, as an element handle."""
    pg.evaluate("() => document.querySelectorAll('[data-e2e-row]')"
                ".forEach(e => e.removeAttribute('data-e2e-row'))")
    if not pg.evaluate(ROW_JS, name):
        raise AssertionError(f"no list row starting with {name!r}")
    return pg.query_selector("[data-e2e-row]")


def click_in_row(pg, name, label):
    els = _btns(row(pg, name), label)
    if not els:
        raise AssertionError(f"row {name!r} has no enabled {label!r} button")
    _park_pointer(pg)
    els[0].click()
    settle(pg)
    check(pg, f"after {label!r} on {name!r}")


def open_row_menu(pg, name):
    r = row(pg, name)
    menus = [e for e in r.query_selector_all('[data-testid="stPopover"] button')
             if e.is_visible() and e.is_enabled()
             and e.inner_text().strip() in ("more_horiz", "")]
    if not menus:
        raise AssertionError(f"row {name!r} has no overflow menu")
    _park_pointer(pg)
    menus[-1].click()
    pg.wait_for_timeout(1400)


def popover(pg, label):
    for e in pg.query_selector_all('[data-testid="stPopover"] button'):
        if e.is_visible() and e.is_enabled() and label in e.inner_text():
            return e
    raise AssertionError(f"no popover button matching {label!r}")


def fill_input(pg, label, value):
    el = pg.query_selector(
        f'//label[.//p[normalize-space(text())="{label}"]]/following::input[1]')
    if el is None:
        raise AssertionError(f"no input labelled {label!r}")
    el.fill(value)
    el.press("Tab")
    settle(pg)


def toggle_labelled(pg, aria):
    """Click a Streamlit toggle/checkbox by its aria-label (the input is hidden,
    so the label element is what takes the click).

    The *visible* one, not the first in the DOM. A collapsed expander still
    renders its contents, so a page with one editor per job holds one copy of
    every control per job — and the first is whichever job sorts first, which
    is not the one that was opened.
    """
    els = [e for e in pg.query_selector_all(
        f'[data-testid="stCheckbox"]:has(input[aria-label="{aria}"]) label')
        if e.is_visible()]
    if not els:
        raise AssertionError(f"no visible toggle labelled {aria!r}")
    els[0].click()
    settle(pg, 3800)


def db():
    return sqlite3.connect(DB)


def count(table):
    with db() as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def names(table):
    with db() as c:
        return [r[0] for r in c.execute(f"SELECT name FROM {table}")]


def setting(key):
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


def job_spec(name):
    with db() as c:
        r = c.execute("SELECT spec_json FROM report_jobs WHERE name=?",
                      (name,)).fetchone()
    return json.loads(r[0]) if r else {}


# --- scenarios --------------------------------------------------------------

@scenario("nav: every page loads clean")
def s_nav(pg):
    for path, marker in [("/", "Live monitor"), ("/builder", "Alert builder"),
                         ("/dashboards", "Dashboards"), ("/reports", "Alert reports"),
                         ("/schedules", "Schedules"), ("/admin", "Admin"),
                         ("/result", "Alert result")]:
        goto(pg, path, marker)
        check(pg, f"page {path}")


@scenario("nav: sidebar links navigate")
def s_nav_links(pg):
    goto(pg, "/", "Live monitor")
    for label, marker in [("Builder", "Alert builder"), ("Dashboards", "Dashboards"),
                          ("Schedules", "Schedules"), ("Admin", "Admin"),
                          ("Monitor", "Live monitor")]:
        link = pg.query_selector(f'[data-testid="stSidebarNav"] a:has-text("{label}")')
        assert link is not None, f"no nav link {label!r}"
        link.click()
        pg.wait_for_selector(f"text={marker}", timeout=45000)
        settle(pg)
        check(pg, f"nav to {label}")


@scenario("nav: Result is hidden but still reachable")
def s_nav_result_hidden(pg):
    goto(pg, "/", "Live monitor")
    vis = pg.evaluate(
        "() => [...document.querySelectorAll('[data-testid=\"stSidebarNav\"] a')]"
        ".filter(a => a.getAttribute('href').endsWith('/result'))"
        ".map(a => a.offsetParent !== null)")
    assert vis == [False], f"Result link visibility = {vis}"
    goto(pg, "/result", "Alert result")
    check(pg, "result reachable by URL")


@scenario("monitor: toggle monitoring on and off")
def s_monitor_toggle(pg):
    goto(pg, "/", "Live monitor")
    before = setting("mon_running")
    toggle_labelled(pg, "Monitoring")
    check(pg, "monitoring toggled")
    after = setting("mon_running")
    assert after != before, f"mon_running unchanged ({before!r})"
    toggle_labelled(pg, "Monitoring")
    assert setting("mon_running") == (before or "0"), "did not toggle back"


@scenario("monitor: change check granularity")
def s_monitor_gran(pg):
    goto(pg, "/", "Live monitor")
    pg.query_selector('button:has-text("30s")').click()
    settle(pg, 3500)
    check(pg, "granularity changed")
    assert setting("mon_gran") == "30s", f"mon_gran = {setting('mon_gran')!r}"


@scenario("monitor: group filter narrows the list")
def s_monitor_filter(pg):
    goto(pg, "/", "Live monitor")
    full = len(pg.query_selector_all('[data-testid="stExpander"]'))
    tag = pg.query_selector_all('[data-testid="stMultiSelect"] [data-baseweb="tag"]')
    assert tag, "no group chips"
    cross = tag[0].query_selector("svg") or tag[0].query_selector('[role="presentation"]')
    assert cross is not None, "chip has no remove control"
    cross.click()
    settle(pg, 4500)
    check(pg, "group filtered")
    fewer = len(pg.query_selector_all('[data-testid="stExpander"]'))
    assert fewer < full, f"filter did not narrow the list ({full} -> {fewer})"


@scenario("monitor: View opens the Result page")
def s_monitor_view(pg):
    goto(pg, "/", "Live monitor")
    click(pg, "View")
    pg.wait_for_selector('[data-testid="stDataFrame"]', timeout=30000)
    check(pg, "result page")


@scenario("result: Excel and CSV downloads are offered")
def s_result_export(pg):
    assert _btns(pg, "Excel"), "no Excel download on the Result page"
    assert _btns(pg, "CSV"), "no CSV download on the Result page"
    popover(pg, "Copy").click()
    pg.wait_for_timeout(1500)
    check(pg, "copy popover")


@scenario("builder: create an alert")
def s_builder_create(pg):
    goto(pg, "/builder", "Alert builder")
    before = count("alerts")
    fill_input(pg, "Alert name", "E2E smoke alert")
    click(pg, "Save alert")
    assert count("alerts") == before + 1, "alert was not saved"
    assert "E2E smoke alert" in names("alerts")


@scenario("builder: disable an alert from its row")
def s_builder_toggle(pg):
    goto(pg, "/builder", "Alert builder")
    with db() as c:
        aid, enabled = c.execute(
            "SELECT id, enabled FROM alerts WHERE name=?",
            ("E2E smoke alert",)).fetchone()
    r = row(pg, "E2E smoke alert")
    lab = r.query_selector('[data-testid="stCheckbox"] label')
    assert lab is not None, "row has no enable toggle"
    lab.click()
    settle(pg, 3800)
    check(pg, "row toggle")
    with db() as c:
        now = c.execute("SELECT enabled FROM alerts WHERE id=?", (aid,)).fetchone()[0]
    assert now != enabled, "enabled flag did not change"


@scenario("builder: clone an alert from its row menu")
def s_builder_clone(pg):
    goto(pg, "/builder", "Alert builder")
    before = count("alerts")
    open_row_menu(pg, "E2E smoke alert")
    click(pg, "Clone")
    fill_input(pg, "Alert name", "E2E clone")
    click(pg, "Save alert")
    assert count("alerts") == before + 1, "clone was not saved"


@scenario("builder: edit an alert from its row")
def s_builder_edit(pg):
    goto(pg, "/builder", "Alert builder")
    click_in_row(pg, "E2E clone", "Edit")
    assert pg.query_selector("text=Edit alert") is not None, "editor did not open"
    fill_input(pg, "Alert name", "E2E clone edited")
    click(pg, "Update alert")
    assert "E2E clone edited" in names("alerts"), "edit was not saved"


@scenario("builder: delete alerts from their row menus")
def s_builder_delete(pg):
    for name in ("E2E clone edited", "E2E smoke alert"):
        goto(pg, "/builder", "Alert builder")
        before = count("alerts")
        open_row_menu(pg, name)
        click(pg, "Confirm delete")
        assert count("alerts") == before - 1, f"{name} was not deleted"


@scenario("builder: an all-clear mail is shown, changed and kept on edit")
def s_builder_success_mail(pg):
    # Seeded straight into the database: picking Email in a multiselect is
    # not what is under test here, the all-clear controls are.
    with db() as c:
        src = json.loads(c.execute(
            "SELECT alert_json FROM alerts ORDER BY id LIMIT 1").fetchone()[0])
        src.update(id=None, name="E2E all clear")
        src["channels"]["email_to"] = ["desk@example.com"]
        src["success"] = {"enabled": True, "mode": "interval",
                          "interval_secs": 3600, "subject": "", "body": ""}
        c.execute("INSERT INTO alerts(name, enabled, alert_json) VALUES (?,?,?)",
                  ("E2E all clear", 1, json.dumps(src)))
    try:
        goto(pg, "/builder", "Alert builder")
        assert "all-clear mail every 1 hour" in row(pg, "E2E all clear").inner_text(),             "the list row does not mention the all-clear mail"
        click_in_row(pg, "E2E all clear", "Edit")
        assert pg.query_selector("text=Also mail when all clear") is not None,             "the all-clear checkbox is not shown"
        fill_input(pg, "Subject", "E2E OK {alert}")
        click(pg, "Update alert")
        with db() as c:
            saved = json.loads(c.execute(
                "SELECT alert_json FROM alerts WHERE name=?",
                ("E2E all clear",)).fetchone()[0])["success"]
        assert saved["enabled"] and saved["interval_secs"] == 3600, saved
        assert saved["subject"] == "E2E OK {alert}", saved
    finally:
        with db() as c:
            c.execute("DELETE FROM alerts WHERE name=?", ("E2E all clear",))


@scenario("builder: Share menu opens with an export")
def s_builder_share(pg):
    goto(pg, "/builder", "Alert builder")
    popover(pg, "Share").click()
    pg.wait_for_timeout(1600)
    check(pg, "share menu")
    assert _btns(pg, "Export alerts", enabled=False), "no export control"


@scenario("dashboards: open one and see its widgets")
def s_dash_open(pg):
    goto(pg, "/dashboards", "Dashboards")
    click_in_row(pg, "Demo orders", "Open")
    pg.wait_for_timeout(14000)
    settle(pg)
    check(pg, "dashboard view")
    n = (len(pg.query_selector_all('[data-testid="stDataFrame"]'))
         + len(pg.query_selector_all('[data-testid="stMetric"]'))
         + len(pg.query_selector_all(".js-plotly-plot")))
    assert n > 0, "the opened dashboard drew no widget"


@scenario("dashboards: Refresh re-runs the queries")
def s_dash_refresh(pg):
    click(pg, "Refresh")
    pg.wait_for_timeout(8000)
    settle(pg)
    check(pg, "after refresh")
    n = (len(pg.query_selector_all('[data-testid="stDataFrame"]'))
         + len(pg.query_selector_all('[data-testid="stMetric"]'))
         + len(pg.query_selector_all(".js-plotly-plot")))
    assert n > 0, "widgets vanished after Refresh"


@scenario("dashboards: PDF export is offered")
def s_dash_pdf(pg):
    assert _btns(pg, "Generate PDF"), "no Generate PDF button"
    assert _btns(pg, "CSV"), "no per-dataset CSV export"


@scenario("dashboards: back to the gallery")
def s_dash_back(pg):
    click(pg, "All dashboards")
    pg.wait_for_selector("text=New dashboard", timeout=30000)
    settle(pg)
    check(pg, "gallery")


@scenario("dashboards: duplicate then delete from the row menu")
def s_dash_dup_delete(pg):
    goto(pg, "/dashboards", "Dashboards")
    before = count("dashboards")
    open_row_menu(pg, "Demo orders")
    click(pg, "Duplicate")
    assert count("dashboards") == before + 1, "dashboard was not duplicated"
    goto(pg, "/dashboards", "Dashboards")
    open_row_menu(pg, "Demo orders (copy)")
    click(pg, "Delete")
    assert count("dashboards") == before, "the copy was not deleted"
    assert "Demo orders" in names("dashboards"), "the original went with it"


@scenario("dashboards: group filter narrows the gallery")
def s_dash_filter(pg):
    goto(pg, "/dashboards", "Dashboards")
    full = len(pg.query_selector_all('[data-testid="stExpander"]'))
    tag = pg.query_selector_all('[data-testid="stMultiSelect"] [data-baseweb="tag"]')
    assert tag, "no group chips"
    (tag[0].query_selector("svg")).click()
    settle(pg, 4000)
    check(pg, "gallery filtered")
    assert len(pg.query_selector_all('[data-testid="stExpander"]')) < full


@scenario("editor: rename a dashboard and save")
def s_editor_save(pg):
    goto(pg, "/dashboards", "Dashboards")
    open_row_menu(pg, "Short sell")
    click(pg, "Edit")
    pg.wait_for_selector("text=Dashboard name", timeout=30000)
    settle(pg, 4000)
    check(pg, "editor opened")
    fill_input(pg, "Dashboard name", "Short sell e2e")
    click(pg, "Save")
    assert "Short sell e2e" in names("dashboards"), "rename was not saved"
    with db() as c:
        c.execute("UPDATE dashboards SET name=? WHERE name=?",
                  ("Short sell", "Short sell e2e"))


@scenario("editor: every section renders")
def s_editor_sections(pg):
    goto(pg, "/dashboards", "Dashboards")
    open_row_menu(pg, "Demo orders")
    click(pg, "Edit")
    pg.wait_for_selector("text=Dashboard name", timeout=30000)
    settle(pg, 4000)
    for section in ["Layout", "Preview", "Library", "Data"]:
        pg.query_selector(f'button:has-text("{section}")').click()
        settle(pg, 5500)
        check(pg, f"editor section {section}")


@scenario("editor: add a row, then delete it")
def s_editor_row(pg):
    pg.query_selector('button:has-text("Layout")').click()
    settle(pg, 4500)
    n0 = len(pg.query_selector_all("text=/^Row \\d+/"))
    click(pg, "Add row")
    settle(pg, 3000)
    n1 = len(pg.query_selector_all("text=/^Row \\d+/"))
    assert n1 > n0, f"row not added ({n0} -> {n1})"
    menus = [e for e in pg.query_selector_all('[data-testid="stPopover"] button')
             if e.is_visible() and e.inner_text().strip() in ("more_horiz", "")]
    menus[-1].click()
    pg.wait_for_timeout(1400)
    click(pg, "Delete row")
    settle(pg, 3000)
    assert len(pg.query_selector_all("text=/^Row \\d+/")) == n0, "row not deleted"


@scenario("editor: leave without saving")
def s_editor_leave(pg):
    click(pg, "All dashboards")
    pg.wait_for_selector("text=New dashboard", timeout=30000)
    settle(pg)
    check(pg, "left the editor")


@scenario("reports: generate a report")
def s_reports(pg):
    goto(pg, "/reports", "Alert reports")
    click(pg, "Generate")
    pg.wait_for_timeout(7000)
    settle(pg)
    check(pg, "report generated")
    assert pg.query_selector("text=Summary") is not None, "no report summary"


@scenario("schedules: open a job and switch to History")
def s_sched_job(pg):
    goto(pg, "/schedules", "Schedules")
    exp = pg.query_selector_all('[data-testid="stExpander"] summary')
    assert exp, "no job expanders"
    exp[0].click()
    settle(pg, 3500)
    check(pg, "job expanded")
    pg.query_selector('button:has-text("History")').click()
    settle(pg, 2500)
    check(pg, "job history")


@scenario("schedules: create a schedule")
def s_sched_new(pg):
    goto(pg, "/schedules", "Schedules")
    before = count("report_jobs")
    click(pg, "New schedule")
    pg.wait_for_selector("text=New schedule", timeout=20000)
    settle(pg, 3000)
    fill_input(pg, "Name", "E2E job")
    fill_input(pg, "Recipients", "e2e@example.com")
    click(pg, "Save")
    assert count("report_jobs") == before + 1, "schedule was not created"


@scenario("schedules: delete the schedule again")
def s_sched_delete(pg):
    goto(pg, "/schedules", "Schedules")
    before = count("report_jobs")
    exp = [e for e in pg.query_selector_all('[data-testid="stExpander"] summary')
           if "E2E job" in (e.inner_text() or "")]
    assert exp, "the new schedule is not listed"
    exp[0].click()
    settle(pg, 3500)
    click(pg, "Delete")
    assert count("report_jobs") == before - 1, "schedule was not deleted"


@scenario("admin: add a connection")
def s_admin_add(pg):
    goto(pg, "/admin", "Admin")
    before = count("connections")
    fill_input(pg, "Name", "e2e_server")
    fill_input(pg, "New environment name", "e2e_env")
    click(pg, "Add connection")
    assert count("connections") == before + 1, "connection was not added"
    assert "e2e_server" in names("connections")


@scenario("admin: edit a connection from its menu")
def s_admin_edit(pg):
    goto(pg, "/admin", "Admin")
    open_row_menu(pg, "Real-time")          # the lead cell is the kind badge
    target = [i for i in pg.query_selector_all('[data-testid="stTextInput"] input')
              if i.is_visible() and i.input_value() == "e2e_server"]
    if not target:
        for cand in ("e2e_server",):
            open_row_menu(pg, cand)
            target = [i for i in pg.query_selector_all('[data-testid="stTextInput"] input')
                      if i.is_visible() and i.input_value() == cand]
    assert target, "the edit form did not open on the right server"
    target[0].fill("e2e_renamed")
    target[0].press("Tab")
    settle(pg, 2200)
    click(pg, "Save changes")
    assert "e2e_renamed" in names("connections"), "rename was not saved"


@scenario("admin: delete a connection from its menu")
def s_admin_delete(pg):
    goto(pg, "/admin", "Admin")
    before = count("connections")
    open_row_menu(pg, "e2e_renamed")
    click(pg, "Confirm")
    assert count("connections") == before - 1, "connection was not deleted"


@scenario("admin: save SMTP settings")
def s_admin_smtp(pg):
    goto(pg, "/admin", "Admin")
    fill_input(pg, "SMTP host", "smtp.e2e.local")
    click(pg, "Save SMTP settings")
    assert setting("smtp_host") == "smtp.e2e.local", "SMTP host was not saved"


@scenario("admin: set the report archive folder")
def s_admin_archive(pg):
    goto(pg, "/admin", "Admin")
    folder = os.path.abspath(os.path.join(os.path.dirname(DB) or ".",
                                          "e2e-archive"))
    os.makedirs(folder, exist_ok=True)
    fill_input(pg, "Folder for scheduled report PDFs", folder)
    # Three sections on this page commit with a button reading "Save settings";
    # the archive is the second, between admin notifications and snapshots.
    click(pg, "Save settings", index=1)
    assert setting("report_archive_dir") == folder,         f"archive folder was not saved (got {setting('report_archive_dir')!r})"


@scenario("schedules: a job can be told to file a copy")
def s_sched_archive(pg):
    """Runs after the folder is set — the switch is refused without one."""
    goto(pg, "/schedules", "Schedules")
    exp = [e for e in pg.query_selector_all('[data-testid="stExpander"] summary')
           if "Morning risk pack" in (e.inner_text() or "")]
    assert exp, "the seeded job is not listed"
    exp[0].click()
    settle(pg, 3500)
    toggle_labelled(pg, "Also save a copy to the report archive")
    click(pg, "Save")
    assert job_spec("Morning risk pack").get("archive") is True,         "the archive choice was not saved"


@scenario("admin: Introspect a demo server")
def s_admin_introspect(pg):
    goto(pg, "/admin", "Admin")
    click_in_row(pg, "Real-time", "Introspect")
    check(pg, "introspect")


SCENARIOS = [
    s_nav, s_nav_links, s_nav_result_hidden,
    s_monitor_toggle, s_monitor_gran, s_monitor_filter, s_monitor_view,
    s_result_export,
    s_builder_create, s_builder_toggle, s_builder_clone, s_builder_edit,
    s_builder_delete, s_builder_success_mail, s_builder_share,
    s_dash_open, s_dash_refresh, s_dash_pdf, s_dash_back, s_dash_dup_delete,
    s_dash_filter,
    s_editor_save, s_editor_sections, s_editor_row, s_editor_leave,
    s_reports,
    s_sched_job, s_sched_new, s_sched_delete,
    s_admin_add, s_admin_edit, s_admin_delete, s_admin_smtp,
    # The archive folder is set before the job that wants to use it: the
    # switch will not save while there is nowhere to save to.
    s_admin_archive, s_sched_archive,
    s_admin_introspect,
]

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = ctx.new_page()
    js_errors = []
    page.on("pageerror", lambda e: js_errors.append(str(e)[:200]))
    for fn in SCENARIOS:
        fn(page)
        print(("  ok   " if results[-1][1] else " FAIL  ") + results[-1][0],
              flush=True)
    browser.close()

ok = sum(1 for _, good, _ in results if good)
print("\n" + "=" * 74)
print(f"{ok}/{len(results)} scenarios passed")
print("=" * 74)
for name, good, detail in results:
    if not good:
        print(f"FAIL  {name}\n      {detail}")
if js_errors:
    print("\nuncaught JS errors:")
    for e in dict.fromkeys(js_errors):
        print("  " + e)
sys.exit(0 if ok == len(results) else 1)
