"""Sphinx configuration of the sphinx-matlab-apidoc example."""

project = "sphinx-matlab-apidoc example"
extensions = ["sphinx.ext.autodoc", "sphinxcontrib.matlab"]
# Pass the same settings to sphinx-matlab-apidoc (ROOT, --include, ...).
matlab_src_code_project_root = "../src"
nitpicky = True
exclude_patterns = ["_build"]
