# Instructions for AI coding agents

The rules for changing this repository. Each has a line of why and a link
to the developer guide (`docs/developer-guide/`), which has the detail.
`sphinxcontrib/matlab_skills/` holds skills that `scmd skills` copies into
users' projects; they are not instructions for this repository.

## Commands and the gate

- Set up with `uv sync --all-extras`
  ([contributing.rst](docs/developer-guide/contributing.rst), "Setting up").
- Before a commit, all of these pass; CI runs the same checks:

  ```console
  uv run pytest -q -o addopts= --simulate-platforms --cov --cov-report=term-missing:skip-covered --cov-fail-under=100
  uv run ruff check
  uv run ruff format --check
  uv run pyright
  cd docs && uv run sphinx-build -E -n -W --keep-going -b html . _build/html
  ```

  [testing.rst](docs/developer-guide/testing.rst), "Coverage", explains
  `--simulate-platforms`.

## How modules connect

Big files are fine. Do not split modules or add size limits; work on how
modules connect instead.

- Imports go down the layers in `tests/test_import_directions.py`, and a
  command reaches no build module (`ROLES`); a new module gets a layer and
  a role. Do not add an exception: an upward import ties together modules
  that should change apart. Remove an entry when its import is gone
  ([architecture.rst](docs/developer-guide/architecture.rst)).
- A file type is an add: a handler in the registry (`mat_handlers`) and
  its own code. No other module names it, so a new type changes no core
  module (architecture.rst, "The file-type registry").
- A new configuration value starts with `matlab_` and is a row of
  `mat_config.CONFIG`, which says which runs use it and how it is checked.

## A change and its docs, in one commit

- Every configuration value, directive, option, role, warning and command
  line option appears in the reference pages (`docs/reference/`);
  `tests/test_documentation.py` checks it.
- Behavior changes go into `docs/CHANGES.rst`, marked **Breaking** when
  they break a project.
- A new or moved configuration file goes into the inventory in
  contributing.rst, "Repository configuration" (also checked by
  `test_documentation.py`).

## Docstrings and comments

- A module docstring says the flow through the module and what it takes
  from and passes to other modules: that is where a reader starts.
- A comment says why, not what: where code follows MATLAB behavior (name
  the MATLAB release), or guards against a bug that happened before.
  contributing.rst, "Code style", has the rest.

## Tests

- Test through what users see: the doctree, HTML, inventory, warnings and
  domain data ([testing.rst](docs/developer-guide/testing.rst), "Writing
  tests"). Tests of private functions break when the code is reorganized.
- A fix's test is a new row in an existing table or scenario before it is a
  new test function, so the tables stay the specification.
- Every line and branch of `sphinxcontrib` is covered. `# pragma: no cover`
  only marks a guard that only a bug can reach, with the reason (testing.rst,
  "Coverage").
- Compare printed paths in `/` form on every system; only Windows CI
  catches the difference otherwise.
- End-to-end coverage comes first: the gauntlet and
  `test_slow/gauntlet/scenarios.py` run the real commands on real projects
  and found problems in code with full unit coverage. Run them when a change
  touches the parser, the index or a tree-sitter pin (testing.rst, "The
  real-project gauntlet"). They are slow and do not run in CI.

## Standing decisions

- Until 1.0.0 is released, a better interface wins over compatibility with
  0.x; record the break in `docs/CHANGES.rst`.
- A Sphinx build writes only to its output and doctree folders;
  `scmd apidoc` and `scmd quickstart` write into the project by design
  (`tests/test_build_writes.py`).
- Project files are trusted (`docs/user-guide/builds.rst`, "What a build
  trusts"). The readers' limits on size and nesting are robustness against
  corrupt files, not a security boundary.
- Each tool checks only the `matlab_*` values it uses; an unknown
  `matlab_*` name is an error everywhere (`tests/test_config_parity.py`). A
  bad value gives a clear `ConfigError`, not Sphinx's crash report.
- Stay on Sphinx's class-based autodoc (`Documenter`). The suite fails on a
  deprecation warning from this package's code; move off the deprecated
  interface (testing.rst, "Running the tests").
- MATLAB behavior the code follows is checked in MATLAB, and the test
  names the release.
- The Python modules are not a public interface; do not document them as
  one. The public interface is listed in contributing.rst, "Versions and
  compatibility".

## Blog

Write a post in `docs/blog/` for a decision someone will later ask "why"
about (a short decision record), or for an investigation with measurements
(with a demo that runs). The rules are in
[docs/blog/README.md](docs/blog/README.md).

## Commits

One subject per commit, with a Conventional Commits prefix as in the log:
`fix:`, `docs:`, `test:`, `refactor:`, `ci:`, and `!` for a breaking
change.
