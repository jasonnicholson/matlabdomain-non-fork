"""sphinxcontrib.mat_classify.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Classify MATLAB ``.m`` files as ``script``, ``function``, ``class`` or
``unknown`` from their text (plan.md Section 2.5). It is the single
classifier for the index, apidoc and api-check.

The first line of code decides: blank lines, ``%`` comment lines and
``%{``/``%}`` block comments are skipped; a line starting with the word
``function`` is a function, with ``classdef`` a class, anything else (or no
code at all) a script. ``unknown`` is a path whose extension is neither
``.m`` nor empty, or a file that cannot be read. The keyword must be a whole
word, so ``functions = 1;`` is a script, as MATLAB runs it.
"""

from __future__ import annotations

import os
import re

SCRIPT = "script"
FUNCTION = "function"
CLASS = "class"
UNKNOWN = "unknown"

_DECLARATION = re.compile(r"(function|classdef)\b")

# MATLAB and Simulink read these by file name (folder help, block libraries,
# customizations, search path, startup, code generation); compared lowercase.
HIDDEN_FILES = frozenset(
    {
        "contents.m",
        "slblocks.m",
        "sl_customization.m",
        "pathdef.m",
        "matlabrc.m",
        "rtwmakecfg.m",
    }
)


def is_hidden(path) -> bool:
    """Return True for special files left out of folder listings and warnings."""
    return os.path.basename(os.fspath(path)).lower() in HIDDEN_FILES


def first_line_of_code(text: str) -> str:
    """Return the first line that is not blank or a comment, stripped.

    Returns ``""`` for a file without code (empty or comments only).
    """
    in_block = False
    for line in text.replace("\ufeff", "").splitlines():
        line = line.strip()
        if in_block:
            in_block = line != "%}"
        elif line == "%{":
            in_block = True
        elif line and not line.startswith("%"):
            return line
    return ""


def classify(path, text: str | None = None) -> str:
    """Return ``script``, ``function``, ``class`` or ``unknown`` for ``path``.

    ``text`` is the decoded file content; ``None`` reads the file as UTF-8
    (``path`` may omit the ``.m``).
    """
    path = os.fspath(path)
    ext = os.path.splitext(path)[1]
    if ext.lower() not in (".m", ""):
        return UNKNOWN
    if text is None:
        file = path if ext else path + ".m"
        try:
            with open(file, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return UNKNOWN
    match = _DECLARATION.match(first_line_of_code(text))
    if match is None:
        return SCRIPT
    return FUNCTION if match[1] == "function" else CLASS
