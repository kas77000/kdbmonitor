"""The report scheduler: a process of its own, sharing only the database.

Nothing in here may import Streamlit. The whole point of the package is that a
report goes out whether or not a browser is open.
"""
