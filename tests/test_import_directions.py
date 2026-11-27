"""Which module of the package may import which.

Every import between modules of ``sphinxcontrib`` counts: at module level,
inside a function and under ``if TYPE_CHECKING:``. A subpackage
(``matlab_apidoc/``, for example) is one unit; imports inside it are free.
The source is read with ``ast``, so nothing here imports the package.
"""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "sphinxcontrib"

#: Lowest first. A module imports only from a layer below its own; two
#: modules of one layer do not import each other.
LAYERS = [
    # base: they import nothing from the package
    {"mat_typing", "mat_classify", "mat_container", "mat_tree_sitter_parser"},
    # the file-type registry
    {"mat_handlers"},
    # path references and MATLAB names
    {"mat_naming"},
    # the index, the parse and the objects
    {"mat_types"},
    # the file types beyond .m: each registers into the two layers above
    {"mat_filetypes"},
    # readers of the model
    {"mat_sidecars", "mat_api_check", "mat_urls"},
    {"mat_documenters"},
    {"mat_directives"},
    # every matlab_* value with its check
    {"mat_config"},
    # the Sphinx extension and the scmd apidoc command
    {"matlab", "matlab_apidoc"},
    # the commands built on scmd apidoc
    {"matlab_quickstart", "matlab_skills"},
    {"matlab_cli"},
]

#: Who uses a module: a Sphinx build only, the scmd commands only, or both.
#: The commands share the index stage with the build and never parse, so no
#: build module is reached from a command: a command imports shared and
#: command modules, a shared module imports shared modules only.
SHARED, BUILD, COMMAND = "shared", "build", "command"
ROLES = {
    "mat_typing": SHARED,
    "mat_classify": SHARED,
    "mat_container": SHARED,
    "mat_tree_sitter_parser": SHARED,
    "mat_handlers": SHARED,
    "mat_naming": SHARED,
    "mat_types": SHARED,
    "mat_filetypes": SHARED,
    "mat_api_check": SHARED,
    "mat_config": SHARED,
    "mat_sidecars": BUILD,
    "mat_urls": BUILD,
    "mat_documenters": BUILD,
    "mat_directives": BUILD,
    "matlab": BUILD,
    "matlab_apidoc": COMMAND,
    "matlab_quickstart": COMMAND,
    "matlab_skills": COMMAND,
    "matlab_cli": COMMAND,
}
#: The roles each role may import.
MAY_IMPORT = {
    SHARED: {SHARED},
    BUILD: {SHARED, BUILD, COMMAND},
    COMMAND: {SHARED, COMMAND},
}

#: Imports that break the rules today, as (importing module, imported
#: module): the debt to work down. Do not add to it; remove an entry when its
#: import is gone (the test then asks for it).
EXCEPTIONS = {
    # The check of matlab_apidoc_group reads the command's GROUPS.
    ("sphinxcontrib.mat_config", "sphinxcontrib.matlab_apidoc._generate"),
    # Stable addresses reuse the command's page names and marker test.
    ("sphinxcontrib.mat_urls", "sphinxcontrib.matlab_apidoc._generate"),
    ("sphinxcontrib.mat_urls", "sphinxcontrib.matlab_apidoc._names"),
    # The package version, for the state of the chooser pages.
    ("sphinxcontrib.mat_urls", "sphinxcontrib.matlab"),
    # MATLABDomain, for a type annotation.
    ("sphinxcontrib.mat_sidecars", "sphinxcontrib.matlab"),
}


def module_name(path):
    parts = ["sphinxcontrib", *path.relative_to(PACKAGE).with_suffix("").parts]
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def unit(name):
    """``sphinxcontrib.matlab_apidoc._cli`` -> ``matlab_apidoc``."""
    return name.split(".")[1]


def imported_names(node, importer, is_package):
    """Return the absolute names an ``import`` or ``from ... import`` reads."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    base = importer.split(".")
    if node.level:
        base = base if is_package else base[:-1]
        base = base[: len(base) - node.level + 1]
    else:
        base = []
    module = ".".join([*base, *([node.module] if node.module else [])])
    if module == "sphinxcontrib":
        # from . import mat_naming: the names are modules
        return [f"{module}.{alias.name}" for alias in node.names]
    return [module]


def package_imports():
    """Return ``(importer, imported, place)`` for each import between units."""
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        importer = module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for name in imported_names(node, importer, path.name == "__init__.py"):
                if name.startswith("sphinxcontrib.") and unit(name) != unit(importer):
                    place = f"{path.relative_to(PACKAGE).as_posix()}:{node.lineno}"
                    found.append((importer, name, place))
    return found


def layer_of(name):
    return next(i for i, layer in enumerate(LAYERS) if unit(name) in layer)


def breaks_rules(importer, imported):
    return (
        layer_of(imported) >= layer_of(importer)
        or ROLES[unit(imported)] not in MAY_IMPORT[ROLES[unit(importer)]]
    )


def test_every_module_has_a_layer_and_a_role():
    units = {unit(module_name(path)) for path in PACKAGE.rglob("*.py")}
    placed = set().union(*LAYERS)
    assert units - placed == set(), "add the new module to LAYERS"
    assert placed - units == set(), "LAYERS names a module that is gone"
    assert sum(map(len, LAYERS)) == len(placed), "a module is in two layers"
    assert units - ROLES.keys() == set(), "add the new module to ROLES"
    assert ROLES.keys() - units == set(), "ROLES names a module that is gone"
    assert set(ROLES.values()) == MAY_IMPORT.keys()


def test_imports_go_down_the_layers():
    wrong = sorted(
        f"{place}: {unit(importer)} imports {imported}"
        for importer, imported, place in package_imports()
        if breaks_rules(importer, imported) and (importer, imported) not in EXCEPTIONS
    )
    assert wrong == []


def test_exceptions_are_still_needed():
    edges = {(importer, imported) for importer, imported, _ in package_imports()}
    assert sorted(EXCEPTIONS - edges) == [], "remove the entry: its import is gone"
    assert [e for e in sorted(EXCEPTIONS) if not breaks_rules(*e)] == []


@pytest.mark.parametrize(
    ("source", "importer", "is_package", "expected"),
    [
        (
            "import sphinxcontrib.mat_naming",
            "sphinxcontrib.matlab",
            False,
            ["sphinxcontrib.mat_naming"],
        ),
        (
            "from . import mat_naming, mat_types",
            "sphinxcontrib.matlab",
            False,
            ["sphinxcontrib.mat_naming", "sphinxcontrib.mat_types"],
        ),
        (
            "from .. import mat_handlers, mat_types",
            "sphinxcontrib.mat_filetypes.mlapp",
            False,
            ["sphinxcontrib.mat_handlers", "sphinxcontrib.mat_types"],
        ),
        (
            "from ..mat_types import X",
            "sphinxcontrib.matlab_quickstart._write",
            False,
            ["sphinxcontrib.mat_types"],
        ),
        (
            "from ._generate import X",
            "sphinxcontrib.matlab_apidoc",
            True,
            ["sphinxcontrib.matlab_apidoc._generate"],
        ),
        (
            "from .. import mat_types",
            "sphinxcontrib.matlab_apidoc._cli",
            False,
            ["sphinxcontrib.mat_types"],
        ),
        (
            "from sphinx.errors import ConfigError",
            "sphinxcontrib.mat_config",
            False,
            ["sphinx.errors"],
        ),
    ],
)
def test_imported_names(source, importer, is_package, expected):
    node = ast.parse(source).body[0]
    assert imported_names(node, importer, is_package) == expected
