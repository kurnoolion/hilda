"""Per-customer Google Drive adapter subclasses.

Each `<customer_id>_adapter.py` here subclasses `GoogleDriveBaseAdapter` and
overrides `_invoke_binding(...)` with the concrete binding import + call body.
Per [D-027] Teacher/Student split: these subclass bodies are filled in by Cline
on Work PC; never land on public github per NFR-2.
"""
