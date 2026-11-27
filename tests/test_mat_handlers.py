"""The file-type registry, and the shape of a file type.

A file type registers its handler (``mat_handlers.register``) and its model
(``mat_types.register_model``); the core takes the rest from its ``Kind``
records. ``test_a_new_file_type_is_an_add`` builds a stand-in type that
registers nothing else; ``test_every_file_type_has_the_shape`` and
``test_package_names_no_file_type_but_m`` check every registered type.
"""

import ast
import re
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from sphinxcontrib import (
    mat_api_check,
    mat_handlers,
    mat_naming,
    mat_types,
)
from sphinxcontrib.mat_filetypes import mlapp
from sphinxcontrib.matlab_apidoc import _generate, main
from sphinxcontrib.matlab_quickstart import _report


def test_registered_handlers(tmp_path):
    assert mat_handlers.extensions() == (".m", ".mlapp")
    (tmp_path / "f.m").write_text("function f\nend\n")
    (tmp_path / "app.mlapp").write_bytes(b"")
    (tmp_path / "x.txt").write_text("")
    (tmp_path / "dir.m").mkdir()

    m = mat_handlers.handler_for(tmp_path / "f.m")
    assert isinstance(m, mat_handlers.MFileHandler)
    assert m.classify(tmp_path / "f.m") == "function"
    assert m.classify("s.m", "x = 1;") == "script"
    app = mat_handlers.handler_for(tmp_path / "app.mlapp")
    assert isinstance(app, mlapp.MlappHandler)
    assert app.classify(tmp_path / "app.mlapp") == "application"
    assert app.classify(tmp_path / "missing.mlapp") == "unknown"
    for missing in ("missing.m", "x.txt", "dir.m", "F.M"):
        assert mat_handlers.handler_for(tmp_path / missing) is None


def test_document_directives():
    m, app = mat_handlers.handlers()
    assert [m.document(k) for k in ("script", "function", "class")] == [
        "mat:autoscript",
        "mat:autofunction",
        "mat:autoclass",
    ]
    assert app.document("application") == "mat:autoapplication"
    assert m.document("application") is None


def test_extension_has_one_handler_and_one_model():
    with pytest.raises(ValueError, match=r"\.m already has a handler"):
        mat_handlers.register(mat_handlers.MFileHandler())
    with pytest.raises(ValueError, match=r"\.m already has a model"):
        mat_types.register_model(".m", mat_types.MatObject.parse_mfile)


def test_object_classes_have_unique_names():
    """Cached objects are restored by class name, so two classes of one
    name would restore as one of them.
    """
    with pytest.raises(TypeError, match="two MatObject classes are called"):
        type("MatApplication", (mat_types.MatObject,), {})


def test_base_handler_needs_classify(tmp_path):
    handler = mat_handlers.Handler()
    with pytest.raises(NotImplementedError):
        handler.read(tmp_path / "x.ext")


def test_naming_does_not_import_the_parser():
    code = (
        "import sys, sphinxcontrib.mat_naming\n"
        "print(sorted(m for m in sys.modules if m.startswith('sphinxcontrib.')))"
    )
    out = subprocess.run(
        [sys.executable, "-X", "gil=1", "-c", code],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "mat_tree_sitter_parser" not in out
    assert "mat_types" not in out
    assert "mat_handlers" in out


COMPONENT = mat_handlers.Kind(
    name="component",
    label="component",
    role="comp",
    section="Components",
    noun="component",
    plural="components",
    index_text="%s (component in %s)",
)


class SimscapeHandler(mat_handlers.Handler):
    """A stand-in for a future ``.ssc`` file type."""

    extension = ".ssc"
    kinds = (COMPONENT,)

    def classify(self, path, text=None):
        return COMPONENT.name


class MatComponent(mat_types.MatObject):
    """The stand-in's object: its help is the first line of the file."""

    def __init__(self, name, folder, help_text):
        super().__init__(name)
        self.module, self.docstring = folder, help_text

    @property
    def __doc__(self):
        return self.docstring

    @property
    def __module__(self):
        return self.module


def parse_component(path, stem, folder, _encoding=None):
    first = Path(path).read_text(encoding="utf-8").splitlines()[0]
    return MatComponent(stem, folder, f"Simscape {first}.")


@pytest.fixture
def ssc_registered(monkeypatch):
    """Register the stand-in as a module of ``mat_filetypes/`` does."""
    monkeypatch.setattr(mat_handlers, "_HANDLERS", dict(mat_handlers._HANDLERS))
    monkeypatch.setattr(mat_types, "_PARSERS", dict(mat_types._PARSERS))
    monkeypatch.setattr(mat_types, "_PLACEHOLDERS", dict(mat_types._PLACEHOLDERS))
    mat_handlers.register(SimscapeHandler())
    mat_types.register_model(
        ".ssc",
        parse_component,
        {COMPONENT.name: lambda name, folder: MatComponent(name, folder, "")},
    )


def test_a_new_file_type_is_an_add(make_app, tmp_path, ssc_registered):
    """A type that registers a handler and a model, and changes no core
    module, gets an object type, a directive, a role, a documenter, an
    index entry, an apidoc section and its api-check count.
    """
    assert mat_naming.parse_reference("lib/+pkg/valve.ssc.p") == (
        "lib/+pkg/valve.ssc",
        ["p"],
    )
    (tmp_path / "+pkg").mkdir()
    (tmp_path / "+pkg" / "valve.ssc").write_text("component valve\nend\n")
    (tmp_path / "+pkg" / "broken.ssc").write_bytes(b"\xff")
    (tmp_path / "conf.py").write_text(
        'extensions = ["sphinx.ext.autodoc", "sphinxcontrib.matlab"]\n'
        'matlab_src_code_project_root = "."\n'
        'matlab_apidoc_output_dir = "api"\n'
    )
    (tmp_path / "index.rst").write_text(
        "Index\n=====\n\n.. mat:autocomponent:: pkg.valve\n\n"
        "See :mat:comp:`pkg.valve`.\n\n.. mat:autocomponent:: +pkg/broken.ssc\n"
    )
    app = make_app(srcdir=tmp_path)
    app.build()

    index = app.env.domaindata["mat"]["index"]
    record = next(r for r in index.values() if r["reference"] == "+pkg/valve.ssc")
    assert (record["type"], record["matlab_name"]) == ("component", "pkg.valve")
    html = (app.outdir / "index.html").read_text()
    assert 'id="mat-+pkg/valve.ssc"' in html
    assert "Simscape component valve." in html
    assert 'href="#mat-+pkg/valve.ssc"' in html
    genindex = (app.outdir / "genindex.html").read_text()
    assert "pkg.valve (component in +pkg/)" in genindex
    # a file that fails to parse is the type's placeholder, with one warning
    broken = app.env.get_domain("mat").entities_table["+pkg/broken.ssc"]
    assert isinstance(broken, MatComponent)
    assert app.warning.getvalue().count("WARNING") == 1
    assert "+pkg/broken.ssc cannot be parsed" in app.warning.getvalue()

    check = make_app("matlab-api-check", srcdir=tmp_path)
    check.build()
    report = (check.outdir / "api-check.txt").read_text()
    assert "Checked: 0 functions, 0 classes," in report
    assert "0 scripts, 0 apps, 1 components, 0 sidecars" in report

    assert main(["-q", "-c", str(tmp_path)]) == 0
    page = (tmp_path / "api" / "pkg.rst").read_text()
    assert "Components\n----------" in page
    assert ".. mat:autocomponent:: +pkg/broken.ssc" in page


def test_every_file_type_has_the_shape(make_app, tmp_path):
    """Each registered type has a model, each kind every word, and the core
    shows the words of the ``Kind``, ``.m`` included.
    """
    kinds = mat_handlers.kinds()
    (tmp_path / "conf.py").write_text('extensions = ["sphinxcontrib.matlab"]\n')
    (tmp_path / "index.rst").write_text(
        "Index\n=====\n\n.. mat:currentfolder:: +pkg\n\n"
        + "".join(f".. mat:{k.name}:: one_{k.name}\n\n" for k in kinds)
    )
    app = make_app(srcdir=tmp_path)
    app.build()
    genindex = (app.outdir / "genindex.html").read_text()
    domain = app.env.get_domain("mat")
    nouns = _report._nouns()
    assert [h.extension for h in mat_handlers.handlers()] == [".m", ".mlapp"]
    for handler in mat_handlers.handlers():
        assert handler.extension in mat_types._PARSERS
        assert handler.kinds
        for kind in handler.kinds:
            assert all(
                getattr(kind, f.name) for f in fields(kind) if f.name != "index_text"
            )
            assert domain.object_types[kind.name].lname == kind.label
            assert domain.object_types[kind.name].roles[0] == kind.role
            assert domain.directive(kind.name) is not None
            assert domain.role(kind.role) is not None
            assert domain.directive(f"auto{kind.name}") is not None
            assert f"mat:{kind.name}" in app.registry.documenters
            assert handler.document(kind.name) == f"mat:auto{kind.name}"
            if kind.index_text:
                assert kind.index_text % (f"one_{kind.name}", "+pkg/") in genindex
            else:
                assert f"one_{kind.name}" not in genindex
            assert _generate._title(kind.name) == kind.section
            assert nouns[kind.name] == kind.noun
            assert kind.name in mat_api_check.kinds()
            assert mat_api_check.plural(kind.name) == kind.plural


def file_type_rows():
    """``(id, pattern, own files)``: the names that belong to each registered
    type but ``.m`` (its extension and module, the words of its kinds in
    quotes, its auto directives, its classes) and the files that may use
    them (its module and the list in ``mat_filetypes/__init__.py``); and
    the Simulink files, which are not a type of this release.
    """
    rows = []
    for handler in mat_handlers.handlers()[1:]:
        module = sys.modules[type(handler).__module__]
        source = Path(str(module.__file__))
        stem = source.stem
        tree = ast.parse(source.read_text(encoding="utf-8"))
        classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
        words = [str(getattr(k, f.name)) for k in handler.kinds for f in fields(k)]
        names = [
            rf"\b{re.escape(stem)}\b",
            re.escape(handler.extension) + r"\b",
            *(f'"{re.escape(w)}"' for w in words if w),
            *(rf"\bauto{k.name}\b" for k in handler.kinds),
            *(rf"\b{c}\b" for c in classes),
        ]
        own = {source, source.with_name("__init__.py")}
        rows.append((handler.extension, re.compile("|".join(names)), own))
    simulink = r"\.slx\b|\.mdl\b|mat_simulink|simulink_|autosimulink"
    rows.append(("simulink", re.compile(simulink), set()))
    return rows


@pytest.mark.parametrize(
    ("pattern", "own"),
    [r[1:] for r in file_type_rows()],
    ids=[r[0] for r in file_type_rows()],
)
def test_package_names_no_file_type_but_m(pattern, own):
    """No module outside a type's own names it, so a type is an add.
    ``matlab_skills/`` is left out for the registered types: its migration
    skill describes 0.x, whose file types and directives are fixed.
    """
    package = Path(mat_handlers.__file__).parent
    skills = package / "matlab_skills"
    named = [
        f"{path.relative_to(package).as_posix()}:{number}"
        for path in sorted(package.rglob("*"))
        if path.suffix in (".py", ".jinja", ".md")
        and path not in own
        and not (own and skills in path.parents)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert named == []
