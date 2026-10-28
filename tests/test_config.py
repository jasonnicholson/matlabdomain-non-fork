"""Project root, source patterns and removed options."""

import pytest
from sphinx.errors import ConfigError


@pytest.fixture
def project(tmp_path):
    src = tmp_path / "docs"
    (src / "code" / "+pkg").mkdir(parents=True)
    (src / "code" / "+pkg" / "f.m").write_text("function f\n% F doc\nend\n")
    (src / "conf.py").write_text(
        'extensions = ["sphinx.ext.autodoc", "sphinxcontrib.matlab"]\n'
    )
    (src / "index.rst").write_text("Index\n=====\n\n.. mat:autofunction:: pkg.f\n")
    return src


def test_project_root_is_relative_to_srcdir(make_app, project, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # not the source or config directory
    app = make_app(
        srcdir=project, confoverrides={"matlab_src_code_project_root": "code"}
    )
    app.build()
    assert "F doc" in (app.outdir / "index.html").read_text()
    assert "WARNING" not in app.warning.getvalue()


@pytest.mark.parametrize(
    "overrides",
    [
        {"matlab_src_dir": "code"},
        {"matlab_src_dir": "code", "matlab_src_code_project_root": "code"},
    ],
)
def test_matlab_src_dir_raises(make_app, project, overrides):
    with pytest.raises(ConfigError, match="matlab_src_dir was removed: use "):
        make_app(srcdir=project, confoverrides=overrides)


@pytest.mark.parametrize(
    "name",
    [
        "matlab_keep_package_prefix",
        "matlab_short_links",
    ],
)
@pytest.mark.parametrize("value", [True, False])
def test_removed_options_raise(make_app, project, name, value):
    with pytest.raises(ConfigError, match=f"{name} was removed"):
        make_app(srcdir=project, confoverrides={name: value})
