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

import codecs
import os
import re

SCRIPT = "script"
FUNCTION = "function"
CLASS = "class"
UNKNOWN = "unknown"

_DECLARATION = re.compile(r"(function|classdef)\b")
_FALLBACK = ("mbcs", "Windows ANSI") if os.name == "nt" else ("latin-1", "ISO-8859-1")

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


# UTF-32 before UTF-16: the UTF-32-LE mark starts with the UTF-16-LE one.
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def decode_source(
    data: bytes, encoding: str | None = None
) -> tuple[str, tuple[str, int] | None]:
    """Decode a source file like MATLAB R2026a; return ``(text, problem)``.

    A byte-order mark selects UTF-8/16/32. Otherwise ``encoding``
    (``matlab_src_encoding``) decodes the file, or, without it, UTF-8; a file
    that is not valid UTF-8 uses the Windows ANSI code page on Windows or
    ISO-8859-1 elsewhere. Undecodable bytes become U+FFFD. ``problem``
    is ``(message, row)`` for a fallback or replacement (``row`` of the first
    bad byte, from 0), else ``None``. A leading byte-order mark is removed.
    """
    codec = encoding
    for bom, name in _BOMS:
        if data.startswith(bom):
            data, codec = data[len(bom) :], name
            break
    try:
        text, problem = data.decode(codec or "utf-8"), None
    except UnicodeDecodeError as err:
        row = err.object[: err.start].decode(err.encoding, errors="replace").count("\n")
        where = f"line {row + 1}: {err.reason}"
        if codec is None:
            fallback, label = _FALLBACK
            text = data.decode(fallback, errors="replace")
            message = (
                f"is not valid UTF-8 ({where}); decoded as {label} like MATLAB."
            )
        else:
            text = data.decode(codec, errors="replace")
            message = (
                f"is not valid {codec} ({where}); undecodable bytes are shown "
                "as U+FFFD."
            )
        problem = (message, row)
    return text.removeprefix("\ufeff"), problem


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

    ``text`` is the decoded file content; ``None`` reads the file with
    ``decode_source`` (``path`` may omit the ``.m``).
    """
    path = os.fspath(path)
    ext = os.path.splitext(path)[1]
    if ext.lower() not in (".m", ""):
        return UNKNOWN
    if text is None:
        file = path if ext else path + ".m"
        try:
            with open(file, "rb") as f:
                text, _ = decode_source(f.read())
        except OSError:
            return UNKNOWN
    match = _DECLARATION.match(first_line_of_code(text))
    if match is None:
        return SCRIPT
    return FUNCTION if match[1] == "function" else CLASS
