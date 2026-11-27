"""Registry of the file types: one handler per file extension.

See "The file-type registry" in ``docs/developer-guide/architecture.rst``.

Discovery, the index stage and path references ask this registry instead of
testing extensions. A file type registers here what the index stage and the
labels of the domain and the commands need; it registers how its files are
parsed with ``mat_types.register_model``. ``.m`` is the core type: its
handler is here. The other types live in ``mat_filetypes/``, which registers
them when it is imported (the entry points import it).

A handler provides:

- ``extension``: the extension it owns, e.g. ``".m"``;
- ``kinds``: the :class:`Kind` records of the index types its ``classify``
  returns (besides ``unknown``);
- ``discover(path)``: True if ``path``, which has that extension, is a
  source file of this type (the base class checks that it is a file);
- ``classify(path, text=None)``: the index type of the file (``unknown`` if
  it is not one);
- ``read(path, encoding)``: called by the index stage; returns
  ``(type, text, problems)`` with the decoded text (``None`` for binary
  formats) and ``(subtype, message, row)`` warnings; raises ``OSError``.
  The type it returns is the index type; a parsed object's
  ``source_type`` defaults to it. The default returns ``classify(path)``;
- ``document(kind)``: the autodoc directive that documents a ``kind``;
  None for an index type that has no directive yet (apidoc leaves it out).

The core (the domain, the documenters, ``matlab-api-check``, ``scmd
apidoc`` and ``scmd quickstart``) has its own entries for the kinds of
``.m``; for every other registered kind it takes the labels of its
:class:`Kind`. Registration order is lookup order: a name without
extension tries ``.m`` first.

Planned file types: ``docs/developer-guide/roadmap.rst``.

``mat_naming`` imports this module, so it imports neither the parser nor
``mat_types``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import mat_classify
from .mat_typing import override


@dataclass(frozen=True)
class Kind:
    """An index type of a file type, with the words the build and the
    commands show for it.
    """

    #: the index type; also the object type, ``mat:<name>`` and
    #: ``mat:auto<name>``
    name: str
    #: the object type's label in the domain (function)
    label: str
    #: the cross-reference role, ``:mat:<role>:``
    role: str
    #: the section title of ``scmd apidoc`` pages (Functions)
    section: str
    #: one object in the report of ``scmd quickstart`` (function)
    noun: str
    #: the objects in the summary of ``matlab-api-check`` (functions)
    plural: str
    #: the general index entry, ``%`` the display name and the folder; ""
    #: for none
    index_text: str


class Handler:
    """Base handler: claims every existing file with its extension."""

    extension = ""
    #: the index types this handler's ``classify`` returns (besides
    #: ``unknown``)
    kinds: tuple[Kind, ...] = ()

    def discover(self, path) -> bool:
        """Return True if ``path``, which has this handler's extension, is a source file
        of its type; here: if it is a file.
        """
        return os.path.isfile(path)

    def classify(self, path, text: str | None = None) -> str:
        """Return the index type of the file at ``path`` (``text``: its text, if already
        read), or ``unknown``.
        """
        raise NotImplementedError

    def read(self, path, encoding=None):  # noqa: ARG002
        """Return ``(type, text, problems)`` for the index stage: the index type, the
        decoded text (None here: a binary format) and ``(subtype, message, row)``
        warnings; raise ``OSError``.
        """
        return self.classify(path), None, []

    def document(self, kind: str) -> str | None:
        """Return the auto directive that documents index type ``kind``
        (``mat:autofunction``, ...), or None when ``kind`` has no directive.
        """
        return f"mat:auto{kind}" if any(k.name == kind for k in self.kinds) else None


class MFileHandler(Handler):
    """MATLAB code files: scripts, functions and classes."""

    extension = ".m"
    kinds = (
        Kind(mat_classify.SCRIPT, "script", "scpt", "Scripts", "script", "scripts", ""),
        Kind(
            mat_classify.FUNCTION,
            "function",
            "func",
            "Functions",
            "function",
            "functions",
            "%s() (function in %s)",
        ),
        Kind(
            mat_classify.CLASS,
            "class",
            "class",
            "Classes",
            "class",
            "classes",
            "%s (class in %s)",
        ),
    )

    @override
    def classify(self, path, text=None):
        return mat_classify.classify(path, text)

    @override
    def read(self, path, encoding=None):
        with open(path, "rb") as f:
            text, problem = mat_classify.decode_source(f.read(), encoding)
        kind = mat_classify.classify(path, text)
        problems: list[tuple[str, str, int | None]] = (
            [] if problem is None else [("encoding", *problem)]
        )
        if (nul := text.find("\0")) >= 0:
            problems.append(
                (
                    "invalid_matlab",
                    "contains NUL bytes; it may be a binary file, not MATLAB code.",
                    mat_classify.row_of(text[:nul]),
                )
            )
        if kind == mat_classify.SCRIPT and not text.replace("\ufeff", "").strip():
            problems.append(
                (
                    "empty_code_file",
                    "is empty; it is documented as a script.",
                    None,
                )
            )
        return kind, text, problems


_HANDLERS: dict[str, Handler] = {}


def register(handler: Handler) -> None:
    """Add ``handler``; its extension must not be registered yet."""
    if handler.extension in _HANDLERS:
        raise ValueError(f"{handler.extension} already has a handler")
    _HANDLERS[handler.extension] = handler


def extensions() -> tuple[str, ...]:
    """Return the registered extensions in lookup order."""
    return tuple(_HANDLERS)


def handlers() -> tuple[Handler, ...]:
    """Return the registered handlers in lookup order."""
    return tuple(_HANDLERS.values())


def kinds() -> tuple[Kind, ...]:
    """Return the kinds of the registered handlers in lookup order, each
    once (a type may reuse the kind of another).
    """
    return tuple({k.name: k for h in _HANDLERS.values() for k in h.kinds}.values())


def kind(name) -> Kind:
    """Return the registered :class:`Kind` called ``name``; raise ``KeyError``."""
    return {k.name: k for k in kinds()}[name]


def handler_by_extension(path) -> Handler | None:
    """Return the handler registered for the extension of ``path``."""
    return _HANDLERS.get(os.path.splitext(os.fspath(path))[1])


def handler_for(path) -> Handler | None:
    """Return the handler of the file ``path`` (by extension and ``discover``)."""
    handler = handler_by_extension(path)
    if handler is None or not handler.discover(path):
        return None
    return handler


register(MFileHandler())
