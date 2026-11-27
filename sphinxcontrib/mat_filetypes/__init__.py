"""The file types beyond ``.m``, the core type.

Each module here is one file type. When it is imported it registers its
handler (the index stage and its :class:`~sphinxcontrib.mat_handlers.Kind`
records) with ``mat_handlers.register`` and its model (how its files are
parsed) with ``mat_types.register_model``. The core takes everything else
from those records, so no module outside this package names a file type.

The entry points import this package: ``matlab.setup`` for a build and
``matlab_apidoc`` for the commands. The imports below are the list of the
types; their order is the lookup order after ``.m``.

See "The file-type registry" in ``docs/developer-guide/architecture.rst``.
"""

from . import mlapp

__all__ = ["mlapp"]
