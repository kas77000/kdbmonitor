# End-to-end browser tests

The pytest suite renders each page once through `AppTest`, in isolation, and
never presses anything twice in a row. These drive the real app in a real
browser: click, type, save, delete, navigate — and after every step assert that
Streamlit rendered no exception and no error banner.

They are **not** collected by pytest (the filenames do not match `test_*.py`)
because they need a running server. Run them by hand, or from CI as a separate
step.

## Running them

```bash
pip install playwright && playwright install chromium

# 1. a throwaway database — several scenarios create and delete
python tests/e2e/seed_demo.py /tmp/e2e.db

# 2. the app, pointed at it
KDBMONITOR_DB=/tmp/e2e.db KDBMONITOR_NO_AUTOSTART=1 \
  streamlit run app.py --server.port 8531 --server.headless true

# 3. the tests, in another shell
python tests/e2e/run_e2e.py /tmp/e2e.db /tmp/e2e-shots
```

`run_e2e.py` exits non-zero if any scenario fails and drops a screenshot of the
failing page into the second directory.

Never point it at `kdbmonitor.db`. It deletes things.

## What they cover

| Area | Scenarios |
|---|---|
| Navigation | every page loads clean; sidebar links navigate; Result is hidden from the nav but still reachable by URL |
| Monitor | monitoring toggles on and off and persists; check granularity persists; the group filter narrows the list; **View** opens the Result page |
| Result | Excel and CSV downloads are offered; the copy popover opens |
| Builder | create an alert; disable it from its row; clone it; edit it; delete both from their row menus; the Share menu offers an export |
| Dashboards | open one and see its widgets; **Refresh** re-runs the queries and keeps them; PDF and per-dataset exports are offered; back to the gallery; duplicate then delete without touching the original; the group filter narrows the gallery |
| Dashboard editor | rename and save; all four sections render; add a row then delete it; leave without saving |
| Reports | generate a report and see its summary |
| Schedules | open a job and switch to History; create a schedule; delete it again |
| Admin | add a connection; edit it from its menu; delete it; save SMTP settings; introspect a demo server |

Each one checks the database afterwards where it should have changed, so a
button that looks like it worked but wrote nothing is a failure.

## Notes for anyone extending these

Three things about driving Streamlit that cost an afternoon to find:

- **A row is not the innermost block that matches its text.** Streamlit wraps
  every *column* in a `stVerticalBlockBorderWrapper` too, so the smallest match
  is a single cell. `row()` takes the smallest one that also holds a control.
- **A popover's body is rendered into a portal** that mentions the row it
  belongs to and holds buttons, so it matches the name and is shorter than the
  row. `row()` excludes anything under `[data-testid="stPopover"]`.
- **A toggle's real `<input>` is invisible** — Streamlit paints a span over it.
  Click the `<label>`, not the input.

And one about the app: the Monitor states a live trigger with `st.error`, on
purpose. That is the alert doing its job, so `problems()` does not count it.
