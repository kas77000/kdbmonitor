"""The design-time shape editor: point at a sample, say where its table is.

Nothing here decides anything. Every rule about reading a file lives in
``core.filesource``; this module shows a grid, collects the declaration, and
stores it. The sample it reads is held in session state and never written to the
database — only the shape and the column contract are, so a dashboard someone
exports carries no data at all.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from kdbmonitor.core.dashboard_models import (
    COLUMN_TYPES, ColumnSpec, Dataset, FileShape, NamedCell,
)
from kdbmonitor.core.filesource import load_grid, profile_columns, read_grid

# How much of a sample is kept for the editing session. Streamlit takes 200MB
# uploads by default, and holding one whole for the sake of a preview is a great
# deal to pay for a glance.
SAMPLE_ROWS = 200
GRID_ROWS = 12                 # how much of the file the grid shows


def sample_key(ds_name: str) -> str:
    return f"fs_sample_{ds_name}"


def stored_sample(ds_name: str):
    """The frame this dataset's sample last read to, if one is still held.

    The transform preview and the widget pickers work against this while the
    author builds. It is session state, so it is gone by the next visit — which
    is why the stored *shape* has to be enough on its own to reopen the editor.
    """
    held = st.session_state.get(sample_key(ds_name))
    return held.get("df") if held else None


def rename_sample(was: str, now: str) -> None:
    """Follow a dataset's sample across a rename.

    A sample is filed under the dataset's name, and the name is editable in the
    same card — Streamlit hands back the new one on the very rerun it is typed
    in. Left alone, the sample would be filed under a name nothing looks up
    again: the grid and the preview would empty out as though the file had never
    been uploaded, which reads as having lost work rather than as having renamed
    something.
    """
    if not was or was == now or not now:
        return
    held = st.session_state.pop(sample_key(was), None)
    if held is not None:
        st.session_state[sample_key(now)] = held


def forget_sample(ds_name: str) -> None:
    """Drop a sample when its dataset goes, rather than leaving it in the session.

    Nothing reads it once the dataset is gone, but a two-hundred-row frame per
    deleted dataset accumulates for as long as the tab stays open.
    """
    st.session_state.pop(sample_key(ds_name), None)


def _grid_frame(grid: list[list[str]]) -> pd.DataFrame:
    """The raw file as a table, labelled the way the controls talk about it.

    Numbered from 1 because that is what the header-line control says and what a
    refusal quotes. A grid counting from 0 beside a message reading "line 3" is a
    trap set for whoever has to act on it.
    """
    body = grid[:GRID_ROWS]
    width = max((len(r) for r in body), default=0)
    frame = pd.DataFrame([r + [""] * (width - len(r)) for r in body],
                         columns=[f"col {i + 1}" for i in range(width)])
    frame.index = [f"line {i + 1}" for i in range(len(body))]
    return frame


def render(ds: Dataset, key: str) -> None:
    """The shape editor for one file dataset."""
    if ds.shape is None:
        ds.shape = FileShape()
    shape = ds.shape

    ds.file_label = st.text_input(
        "What to ask for", value=ds.file_label, key=f"{key}_label",
        placeholder="your orders export",
        help="The prompt on the upload box when somebody runs this dashboard.")

    upload = st.file_uploader(
        "Sample file", type=["csv"], key=f"{key}_sample",
        help="Read to work out the columns, then discarded. Nothing from it is "
             "saved with the dashboard.")

    if upload is not None:
        try:
            grid = read_grid(upload.getvalue(), shape.delimiter)
        except ValueError as exc:
            st.error(str(exc), icon=":material/error:")
            return
        held = st.session_state.get(sample_key(ds.name)) or {}
        st.session_state[sample_key(ds.name)] = {**held,
                                                 "grid": grid[:SAMPLE_ROWS]}

    held = st.session_state.get(sample_key(ds.name))
    if not held or not held.get("grid"):
        st.info("Drop in a sample file to say where its table sits.",
                icon=":material/upload_file:")
        return

    grid = held["grid"]
    st.dataframe(_grid_frame(grid), use_container_width=True)

    c = st.columns(4)
    axes = ["row", "column"]
    shape.header_axis = c[0].selectbox(
        "Headers run", axes,
        index=axes.index(shape.header_axis if shape.header_axis in axes
                         else "row"),
        key=f"{key}_axis",
        format_func=lambda a: "across a line" if a == "row" else "down a column",
        help="Down a column means each column of the file is one record.")
    shape.header_row = int(c[1].number_input(
        "Header line", 1, 10_000, shape.header_row + 1, key=f"{key}_hrow",
        help="Counted as the grid above counts. Whoever runs this dashboard "
             "must have their headers on this line too — it is never searched "
             "for.")) - 1
    shape.first_col = int(c[2].number_input(
        "Table starts at column", 1, 10_000, shape.first_col + 1,
        key=f"{key}_fcol")) - 1
    shape.data_start = int(c[3].number_input(
        "Data starts on line", 1, 10_000, shape.data_start + 1,
        key=f"{key}_dstart")) - 1

    shape.null_markers = [m.strip() for m in st.text_input(
        "Read as missing", value=", ".join(shape.null_markers),
        key=f"{key}_nulls",
        help="Comma-separated. Take a marker off the list if it is a real value "
             "in your data — a side of '-' would otherwise be blanked."
    ).split(",")]

    if st.button("Read the columns from this sample", key=f"{key}_profile",
                 icon=":material/refresh:"):
        # Keep what was already corrected: re-reading a sample must not undo the
        # author's judgement that an order ID is text rather than a number.
        by_name = {c.name: c for c in shape.columns}
        shape.columns = [by_name.get(found.name, found)
                         for found in profile_columns(grid, shape)]
        st.rerun()

    _columns_form(shape, key)
    _cells_form(shape, key)
    _check(grid, shape, ds.name)


def _columns_form(shape: FileShape, key: str) -> None:
    st.caption("**Columns this dashboard needs.** The types are a first reading "
               "of your sample — correct any that are wrong, since a column of "
               "integer-looking order IDs is text and only you know that.")
    types = list(COLUMN_TYPES)
    for i, spec in enumerate(list(shape.columns)):
        c = st.columns([3, 2, 1.4, 1.4, 0.7], vertical_alignment="bottom")
        spec.name = c[0].text_input("Name", value=spec.name, key=f"{key}_cn{i}")
        spec.type = c[1].selectbox(
            "Type", types, key=f"{key}_ct{i}",
            index=types.index(spec.type) if spec.type in types
            else types.index("text"))
        spec.required = c[2].checkbox("Required", value=spec.required,
                                      key=f"{key}_cr{i}")
        spec.allow_null = not c[3].checkbox(
            "No gaps", value=not spec.allow_null, key=f"{key}_cg{i}",
            help="Refuse a file with blanks here. Worth setting on whatever a "
                 "chart is plotted against.")
        if c[4].button("", icon=":material/delete:", key=f"{key}_cx{i}"):
            shape.columns.pop(i)
            st.rerun()

    if st.button("Add a column", key=f"{key}_caddb", icon=":material/add:"):
        shape.columns.append(ColumnSpec(name=""))
        st.rerun()


def _cells_form(shape: FileShape, key: str) -> None:
    types = list(COLUMN_TYPES)
    with st.expander(f"Named cells ({len(shape.cells)})",
                     icon=":material/my_location:"):
        st.caption("A single cell outside the table — a report date sitting in "
                   "line 1, say. Addressed against the grid above, as the file "
                   "is written, so switching the headers to run downwards does "
                   "not move it.")
        for i, cell in enumerate(list(shape.cells)):
            c = st.columns([3, 1.4, 1.4, 1.6, 0.7], vertical_alignment="bottom")
            cell.name = c[0].text_input("Name", value=cell.name,
                                        key=f"{key}_kn{i}")
            cell.row = int(c[1].number_input("Line", 1, 10_000, cell.row + 1,
                                             key=f"{key}_kr{i}")) - 1
            cell.col = int(c[2].number_input("Column", 1, 10_000, cell.col + 1,
                                             key=f"{key}_kc{i}")) - 1
            cell.type = c[3].selectbox(
                "Type", types, key=f"{key}_kt{i}",
                index=types.index(cell.type) if cell.type in types
                else types.index("text"))
            if c[4].button("", icon=":material/delete:", key=f"{key}_kx{i}"):
                shape.cells.pop(i)
                st.rerun()
        if st.button("Name a cell", key=f"{key}_kaddb", icon=":material/add:"):
            shape.cells.append(NamedCell(name=f"cell {len(shape.cells) + 1}"))
            st.rerun()


def _check(grid: list[list[str]], shape: FileShape, ds_name: str) -> None:
    """Read the sample back through the real loader, and keep what it produced.

    ``load_grid`` is the very function a viewer's upload goes through, so what
    the author sees here — including the refusals — is exactly what their
    colleague will see. The frame it returns is held for the transform preview;
    it is session state and is never saved.
    """
    if not shape.columns:
        return
    out = load_grid(grid, shape)
    if out.problems:
        for problem in out.problems:
            st.error(problem.message, icon=":material/error:")
        return
    for note in out.notes:
        st.caption(f":gray[{note}]")
    if out.cells:
        st.caption(":gray[named cells — "
                   + ", ".join(f"{k}: {v}" for k, v in out.cells.items()) + "]")
    st.success(f"Reads {len(out.df):,} row(s) from this sample.",
               icon=":material/check:")
    st.dataframe(out.df.head(20), use_container_width=True)

    held = st.session_state.get(sample_key(ds_name)) or {}
    st.session_state[sample_key(ds_name)] = {**held,
                                             "df": out.df.head(SAMPLE_ROWS)}
