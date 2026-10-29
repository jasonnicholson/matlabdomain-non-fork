sphinx-matlab-apidoc example
============================

A small MATLAB project (``src/``), its Sphinx project (``docs/``) and an
edited copy of the default templates (``templates/``). The pages in
``docs/api/`` are generated and committed; ``tests/test_apidoc.py``
regenerates them, compares them with these files and builds the project with
``-W``.

``src/`` has a ``+demo`` namespace with a nested ``+geometry`` namespace, an
``@Shape`` class folder with method files, a plain folder of global functions
(``utils/``) with a ``private/`` folder, a script and an App Designer app.

Regenerate and build, from ``docs/``::

    sphinx-matlab-apidoc -f --remove-old -t ../templates -o api ../src
    sphinx-build -n -W -b html . _build/html

``../src`` is the folder ``matlab_src_code_project_root`` points to in
``conf.py``. Keep ``--include``, ``--exclude``, ``--matlab-version`` and
``--encoding`` equal to the matching ``conf.py`` settings.

The templates differ from the defaults in two places: the root page is
titled "Demo API reference", and classes are documented without
``:undoc-members:``. To customize your own output, copy these templates (or
the defaults in ``sphinxcontrib/matlab_apidoc/templates/``) into your
project, edit them and pass their folder with ``-t``.
