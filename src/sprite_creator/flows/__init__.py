"""
Self-contained application flows.

Each flow is its own package with its own state dataclass and wizard steps,
sharing only the widgets in ui/widgets/ and the pure processing functions.
This is the target architecture for all workflows; the importer is the first.
"""
