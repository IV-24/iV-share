"""Repo-editing infrastructure: git/file/shell operations confined to a
sandboxed workspace root on disk. sandbox.py is the trust boundary every
other module here routes through; git_ops.py, files.py, and shell.py are
the primitives; tools.py wires them into the ToolRegistry with risk/
approval metadata."""
