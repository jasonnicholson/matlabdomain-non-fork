# Real-project gauntlet

Runs `sphinx-matlab-apidoc` and `sphinx-build` on real MATLAB projects and
reports crashes, warnings by subtype, timings, and serial vs `-j`
differences. Added in Phase 8 (14 projects, about 24,000 `.m` files).

| File | Purpose |
|---|---|
| `projects.toml` | The project list: name, Git URL, optional commit and options. Edit it to change what is tested. |
| `projects.lock.toml` | Written by `fetch.py`: the commit of each checkout. Commit it so other machines and later runs use the same commits. |
| `fetch.py` | Clones a missing project into `repos/<name>` and moves an existing checkout to its commit. |
| `gauntlet.py` | Runs the gauntlet on the checkouts and writes `results.json` (the summary) and `manifest.json`. Clones nothing. |
| `compare.py` | Compares two runs, or a run and a saved summary. |
| `summaries/` | Saved summaries (`results.json` files); the Phase 8 Windows and Linux runs. |

`repos/` (checkouts) and `runs/` (output) are ignored by Git.

## Run

From the `matlabdomain` repo root, with the project's environment
(`uv sync --all-extras`; on Windows use `.venv\Scripts\python`):

```bash
python test_slow/gauntlet/fetch.py               # once, then after editing the list
python test_slow/gauntlet/gauntlet.py --jobs     # all projects -> runs/latest
python test_slow/gauntlet/gauntlet.py matpower --trials 1 --out test_slow/gauntlet/runs/mp
python test_slow/gauntlet/compare.py test_slow/gauntlet/summaries/phase8-linux.json test_slow/gauntlet/runs/latest
```

NAME arguments keep the projects whose name contains one of them. For each
project, `gauntlet.py` generates the pages 3 times (`--trials`), reruns with
`-f` (expect nothing created or updated), and builds with the defaults
(`conf`) and with `matlab_auto_link = "all"` (`all`; `--variant` picks one).
`--jobs` builds each again with `-j auto` and compares the HTML,
`objects.inv` and warnings. On Windows, `-j` runs serially (no fork).
`gauntlet.py` exits 1 on a problem: a checkout not at its commit, a nonzero
exit code, a traceback, a duplicate object, a rerun that changes pages, or a
serial vs `-j` difference (unless `parallel_may_differ`).

To save a summary, copy `runs/<name>/results.json` to `summaries/<name>.json`.

## Repeatable commits

- `commit` in `projects.toml` pins a project (a SHA, best in full, or a tag).
- Without `commit`, the first fetch takes the default branch HEAD and records
  it in `projects.lock.toml`; later fetches keep it.
  `fetch.py --update` moves those projects to the newest HEAD.
- A checkout with local changes is not moved; `fetch.py` reports it.
  `fetch.py --reset` discards the changes (`git reset --hard`,
  `git clean -fd`).
- New checkouts are shallow (depth 1) and set `core.symlinks=true`: with
  `false`, Git for Windows writes symlinks as text files (matpower has 807,
  so its pages differed from Linux).

Checkouts from before this folder existed (`test_slow/artifacts/<name>/repo`)
can be moved instead of fetched again:

```bash
for d in test_slow/artifacts/*/repo; do n=$(basename "$(dirname "$d")"); mv "$d" "test_slow/gauntlet/repos/$n"; done
```

## Expected (Phase 8, `summaries/`)

All 14 projects: exit 0 everywhere, no traceback or duplicate object, the
rerun changes nothing, and Windows and Linux give the same pages and
warnings (`compare.py` on the two summaries: `0 of 14 projects differ`).
matpower's serial and `-j` builds differ (13 pages, 65 `ref.equation`
warnings): its symlinked copies define each equation label twice, and
Sphinx's math domain merges them per worker. A file that is not valid UTF-8
decodes as ISO-8859-1 on Linux and Windows ANSI on Windows, so its warnings
may differ between the two.
