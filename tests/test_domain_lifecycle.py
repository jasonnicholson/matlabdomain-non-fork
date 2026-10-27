"""test_domain_lifecycle.py.

Test that MATLAB domain state is env-owned and follows the Sphinx lifecycle:
environment pickling, cache reuse on rebuild, clear_doc and merge_domaindata.

:copyright: Copyright by the Sphinx team, see AUTHORS.
:license: BSD, see LICENSE for details.
"""

import io
import os
import pickle
import shutil

import pytest
from sphinx.environment import CONFIG_OK
from tree_sitter import Node, Parser, Tree

from sphinxcontrib import mat_types
from sphinxcontrib.matlab import MATLABDomain


@pytest.fixture
def srcdir(rootdir, tmp_path):
    # Copy so tests can edit sources and reuse the pickled environment.
    dst = tmp_path / "test_classfolder"
    shutil.copytree(
        rootdir / "roots" / "test_classfolder",
        dst,
        ignore=shutil.ignore_patterns("_build"),
    )
    return dst


@pytest.fixture
def parsed(monkeypatch):
    """Record every .m file actually parsed with tree-sitter."""
    calls = []
    original = mat_types.MatObject.parse_mfile

    def counting_parse_mfile(mfile, *args, **kwargs):
        calls.append(mfile)
        return original(mfile, *args, **kwargs)

    monkeypatch.setattr(
        mat_types.MatObject, "parse_mfile", staticmethod(counting_parse_mfile)
    )
    return calls


def build(make_app, srcdir, **confoverrides):
    app = make_app(srcdir=srcdir, confoverrides=confoverrides)
    app.build()
    return app


def find_class(table, name):
    (cls,) = {
        id(o): o
        for o in table.values()
        if isinstance(o, mat_types.MatClass) and o.name == name
    }.values()
    return cls


def doctree_text(app, docname):
    return pickle.loads((app.doctreedir / f"{docname}.doctree").read_bytes()).astext()


def test_initial_data_layout():
    assert MATLABDomain.data_version == 8
    assert set(MATLABDomain.initial_data) == {
        "objects",
        "folders",
        "index",
        "parse_cache",
        "entities_table",
        "matlab_names",
    }


def test_source_state_is_env_owned(make_app, srcdir):
    app = build(make_app, srcdir)
    data = app.env.domaindata["mat"]
    dom = app.env.get_domain("mat")

    assert dom.entities_table is data["entities_table"]

    m_files = sorted(str(p) for p in srcdir.rglob("*.m"))
    assert sorted(data["index"]) == m_files
    assert {key[0] for key in data["parse_cache"]} == set(m_files)
    for path, entry in data["index"].items():
        st = os.stat(path)
        assert (entry["mtime"], entry["size"]) == (st.st_mtime_ns, st.st_size)
        assert data["parse_cache"][path, st.st_mtime_ns, st.st_size] is not None
    assert data["index"][str(srcdir / "src" / "@Second" / "Second.m")] == {
        "reference": "src/@Second/Second.m",
        "type": "class",
        "mtime": os.stat(srcdir / "src" / "@Second" / "Second.m").st_mtime_ns,
        "size": os.stat(srcdir / "src" / "@Second" / "Second.m").st_size,
        "matlab_name": "Second",
        "private": False,
        "scope": "src/",
    }
    method_file = str(srcdir / "src" / "@Second" / "method_in_folder.m")
    assert data["index"][method_file]["type"] == "function"


def test_env_pickle_has_no_tree_sitter_objects(make_app, srcdir):
    app = build(make_app, srcdir)

    seen = []

    class CheckingPickler(pickle.Pickler):
        def reducer_override(self, obj):
            if isinstance(obj, (Node, Tree, Parser)):
                seen.append(obj)
            return NotImplemented

    CheckingPickler(io.BytesIO()).dump(app.env)
    assert seen == []

    data = pickle.loads(pickle.dumps(app.env)).domaindata["mat"]
    second = find_class(data["entities_table"], "Second")
    assert sorted(second.methods) == [
        "Second",
        "method_in_folder",
        "method_inside_classdef",
    ]
    assert second.methods["method_in_folder"].cls is second
    assert (
        second.docstring
        == find_class(app.env.get_domain("mat").entities_table, "Second").docstring
    )


def test_rebuild_without_changes_does_not_reparse(make_app, srcdir, parsed):
    app1 = build(make_app, srcdir)
    assert len(parsed) == len(list(srcdir.rglob("*.m")))
    first_text = doctree_text(app1, "index_second")
    first_keys = sorted(app1.env.get_domain("mat").entities_table)
    first_index = dict(app1.env.domaindata["mat"]["index"])

    # Touch only the .rst so Sphinx re-reads it with the reloaded environment.
    rst = srcdir / "index_second.rst"
    future = os.stat(rst).st_mtime_ns + 10**10
    os.utime(rst, ns=(future, future))

    parsed.clear()
    app2 = build(make_app, srcdir)
    assert parsed == []
    assert app2.env.config_status == CONFIG_OK  # reloaded from pickle
    dom2 = app2.env.get_domain("mat")
    assert sorted(dom2.entities_table) == first_keys
    assert app2.env.domaindata["mat"]["index"] == first_index
    assert doctree_text(app2, "index_second") == first_text
    assert sorted(find_class(dom2.entities_table, "Second").methods) == [
        "Second",
        "method_in_folder",
        "method_inside_classdef",
    ]


def test_size_change_with_preserved_mtime_reparses(make_app, srcdir, parsed):
    build(make_app, srcdir)
    mfile = srcdir / "src" / "@Second" / "Second.m"
    st = os.stat(mfile)
    mfile.write_text(
        mfile.read_text().replace("% The second class", "% The changed second class")
    )
    os.utime(mfile, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert os.stat(mfile).st_mtime_ns == st.st_mtime_ns
    assert os.stat(mfile).st_size != st.st_size

    parsed.clear()
    app2 = build(make_app, srcdir)
    assert parsed == [str(mfile)]
    second = find_class(app2.env.get_domain("mat").entities_table, "Second")
    assert "The changed second class" in second.docstring
    # The class was reparsed; folder methods are re-attached exactly once.
    assert sorted(second.methods) == [
        "Second",
        "method_in_folder",
        "method_inside_classdef",
    ]


def test_removed_folder_method_not_kept_on_reused_class(make_app, srcdir, parsed):
    build(make_app, srcdir)
    (srcdir / "src" / "@Second" / "method_in_folder.m").unlink()

    parsed.clear()
    app2 = build(make_app, srcdir)
    assert parsed == []
    second = find_class(app2.env.get_domain("mat").entities_table, "Second")
    assert sorted(second.methods) == ["Second", "method_inside_classdef"]


def test_config_change_invalidates_parse_cache(make_app, srcdir, parsed):
    build(make_app, srcdir)
    parsed.clear()
    build(make_app, srcdir, matlab_show_property_default_value=True)
    assert len(parsed) == len(list(srcdir.rglob("*.m")))


# data_version of the commits that cached block-comment docstrings (59016b1).
DOCSTRING_CACHE_VERSION_BEFORE_HELP_FIX = 5


@pytest.mark.parametrize("older", [True, False], ids=["older_version", "same_version"])
def test_older_domain_data_version_drops_cached_docstrings(
    make_app, tmp_path, parsed, older
):
    (tmp_path / "conf.py").write_text(
        'extensions = ["sphinx.ext.autodoc", "sphinxcontrib.matlab"]\n'
        'matlab_src_code_project_root = "."\n'
    )
    (tmp_path / "index.rst").write_text(
        "Index\n=====\n\n.. mat:autofunction:: up_blk\n"
    )
    (tmp_path / "up_blk.m").write_text("%{\nBLOCK BODY\n%}\nfunction up_blk\nend\n")
    app1 = build(make_app, tmp_path)
    assert "BLOCK BODY" not in (app1.outdir / "index.html").read_text()

    # Leave the pickled environment as an earlier release would have: a
    # cached object with an outdated docstring, under the previous version.
    data = app1.env.domaindata["mat"]
    (cached,) = (o for o in data["parse_cache"].values() if o.name == "up_blk")
    cached.docstring = "STALE DOCSTRING"
    if older:
        data["version"] = DOCSTRING_CACHE_VERSION_BEFORE_HELP_FIX
    with open(app1.doctreedir / "environment.pickle", "wb") as f:
        pickle.dump(app1.env, f, pickle.HIGHEST_PROTOCOL)
    rst = tmp_path / "index.rst"
    future = os.stat(rst).st_mtime_ns + 10**10
    os.utime(rst, ns=(future, future))

    parsed.clear()
    app2 = build(make_app, tmp_path)
    html = (app2.outdir / "index.html").read_text()
    if older:
        assert "STALE DOCSTRING" not in html
        assert parsed == [str(tmp_path / "up_blk.m")]
        (fresh,) = (
            o
            for o in app2.env.domaindata["mat"]["parse_cache"].values()
            if o.name == "up_blk"
        )
        assert fresh.docstring.strip() == "{"
    else:
        # Same version: the cache is trusted, so the control shows the test can
        # see stale data.
        assert "STALE DOCSTRING" in html
        assert parsed == []


# data_version before matlab_src_encoding and the ISO-8859-1 fallback (836e94e).
CACHE_VERSION_BEFORE_ENCODING = 6


@pytest.mark.parametrize(
    "version", [CACHE_VERSION_BEFORE_ENCODING, 7, MATLABDomain.data_version]
)
def test_older_domain_data_version_drops_misdecoded_docstrings(
    make_app, tmp_path, parsed, version
):
    (tmp_path / "conf.py").write_text(
        'extensions = ["sphinx.ext.autodoc", "sphinxcontrib.matlab"]\n'
        'matlab_src_code_project_root = "."\n'
    )
    (tmp_path / "index.rst").write_text(
        "Index\n=====\n\n.. mat:autofunction:: up_lat\n"
    )
    mfile = tmp_path / "up_lat.m"
    mfile.write_bytes("function up_lat\n% LATIN café\nend\n".encode("latin-1"))
    app1 = build(make_app, tmp_path)
    assert "LATIN café" in (app1.outdir / "index.html").read_text(encoding="utf-8")

    # Leave the cached object as 836e94e left it: a misdecoded docstring
    # (there: backslash escapes), no recorded warnings.
    data = app1.env.domaindata["mat"]
    (cached,) = (o for o in data["parse_cache"].values() if o.name == "up_lat")
    cached.docstring = "LATIN MISDECODED"
    del cached.source_warnings
    data["version"] = version
    with open(app1.doctreedir / "environment.pickle", "wb") as f:
        pickle.dump(app1.env, f, pickle.HIGHEST_PROTOCOL)
    rst = tmp_path / "index.rst"
    future = os.stat(rst).st_mtime_ns + 10**10
    os.utime(rst, ns=(future, future))

    parsed.clear()
    app2 = build(make_app, tmp_path)
    html = (app2.outdir / "index.html").read_text(encoding="utf-8")
    if version < MATLABDomain.data_version:
        assert parsed == [str(mfile)]
        assert "LATIN café" in html
        assert "[mat.encoding]" in app2.warning.getvalue()
    else:
        # Same version: the cache is trusted (the control).
        assert parsed == []
        assert "LATIN MISDECODED" in html
        assert "[mat.encoding]" not in app2.warning.getvalue()


def test_clear_doc_only_removes_that_doc(make_app, srcdir):
    app = build(make_app, srcdir)
    dom = app.env.get_domain("mat")
    data = dom.data
    source_state = {
        k: dict(data[k])
        for k in ("index", "parse_cache", "entities_table", "matlab_names")
    }
    docs = {fn for fn, _, _ in data["objects"].values()}
    assert {"index_first", "index_second", "index_third"} <= docs
    kept = {k: v for k, v in data["objects"].items() if v[0] != "index_second"}

    dom.clear_doc("index_second")

    assert data["objects"] == kept
    assert all(fn != "index_second" for fn, _, _, _ in data["folders"].values())
    for k, v in source_state.items():
        assert data[k] == v


def test_merge_domaindata_filters_by_docnames(make_app, srcdir):
    app = build(make_app, srcdir)
    dom = app.env.get_domain("mat")
    source_state = {
        k: dict(dom.data[k])
        for k in ("index", "parse_cache", "entities_table", "matlab_names")
    }
    before = dict(dom.data["objects"])
    otherdata = {
        "objects": {
            "a/Obj.m": ("doc_a", "class", "Obj"),
            "b/Obj.m": ("doc_b", "class", "Obj"),
        },
        "folders": {
            "a/": ("doc_a", "", "", False),
            "b/": ("doc_b", "", "", False),
        },
        "index": {"/elsewhere/x.m": {}},
        "parse_cache": {("/elsewhere/x.m", 0, 0): object()},
        "entities_table": {"x.m": object()},
        "matlab_names": {"x": ["x.m"]},
    }

    dom.merge_domaindata({"doc_a"}, otherdata)

    assert dom.data["objects"] == {**before, "a/Obj.m": ("doc_a", "class", "Obj")}
    assert dom.data["folders"]["a/"] == ("doc_a", "", "", False)
    assert "b/" not in dom.data["folders"]
    for k, v in source_state.items():
        assert dom.data[k] == v


def test_note_object(make_app, srcdir):
    app = build(make_app, srcdir)
    dom = app.env.get_domain("mat")
    app.env.temp_data["docname"] = "index_first"

    dom.note_object("some/Thing.m", "class", "Thing")
    assert dom.data["objects"]["some/Thing.m"] == ("index_first", "class", "Thing")

    warnings_before = app.warning.getvalue()
    dom.note_object("some/Thing.m", "function", "Thing")
    assert app.warning.getvalue() == warnings_before

    dom.note_object("some/Thing.m", "class", "Thing", location=("index_first", 3))
    assert "duplicate object description of some/Thing.m" in app.warning.getvalue()
    assert dom.data["objects"]["some/Thing.m"] == ("index_first", "class", "Thing")


if __name__ == "__main__":
    pytest.main([os.path.abspath(__file__)])
