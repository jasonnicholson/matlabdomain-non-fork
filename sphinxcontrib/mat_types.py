"""
sphinxcontrib.mat_types
~~~~~~~~~~~~~~~~~~~~~~~

Types for MATLAB.

:copyright: Copyright 2014-2024 by the sphinxcontrib-matlabdomain team, see AUTHORS.
:license: BSD, see LICENSE for details.
"""

import ntpath
import os
import stat
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from io import open  # for opening files with encoding in Python 2
from zipfile import ZipFile

from sphinx.environment import CONFIG_OK
from sphinx.errors import ConfigError
from sphinx.util.build_phase import BuildPhase
from sphinx.util.logging import getLogger
from sphinx.util.matching import Matcher

from . import mat_naming
from .mat_classify import (
    UNKNOWN,
    classify,
    decode_source,
    first_line_of_code,
    is_hidden,
)
from .mat_handlers import handler_by_extension, handler_for, handlers

def get_node(match_dict, key):
    res = match_dict.get(key)
    return res[0] if res else None

from tree_sitter import Node, Parser, Tree

from sphinxcontrib.mat_tree_sitter_parser import (
    ML_LANG,
    MatClassParser,
    MatFunctionParser,
    MatScriptParser,
    declaration,
    first_error,
    get_row,
    parsed_type,
)
from .mat_naming import (
    ROOT_REFERENCE,
    folder_of,
    is_private_reference,
    join_reference,
    parse_reference,
)

logger = getLogger("matlab-domain")

__all__ = [
    "MatApplication",
    "MatClass",
    "MatEnumeration",
    "MatException",
    "MatFunction",
    "MatMethod",
    "MatModule",
    "MatModuleAnalyzer",
    "MatObject",
    "MatProperty",
    "MatScript",
]


# MATLAB attribute type dictionaries.

# From:
#  - http://www.mathworks.com/help/matlab/matlab_oop/class-attributes.html
MATLAB_CLASS_ATTRIBUTE_TYPES = {
    "Abstract": bool,
    "AllowedSubclasses": list,
    "ConstructOnLoad": bool,
    "HandleCompatible": bool,
    "Hidden": bool,
    "InferiorClasses": list,
    "Sealed": bool,
    "SharedTestFixtures": list,
    "TestTags": list,
}

# From:
#  - https://mathworks.com/help/matlab/matlab_oop/property-attributes.html
#  - https://mathworks.com/help/matlab/matlab_prog/define-property-attributes-1.htm
#  - https://mathworks.com/help/matlab/ref/matlab.unittest.testcase-class.html
MATLAB_PROPERTY_ATTRIBUTE_TYPES = {
    "AbortSet": bool,
    "Abstract": bool,
    "Access": list,
    "ClassSetupParameter": bool,
    "Constant": bool,
    "Dependent": bool,
    "DiscreteState": bool,
    "GetAccess": list,
    "GetObservable": bool,
    "Hidden": bool,
    "MethodSetupParameter": bool,
    "NonCopyable": bool,
    "Nontunable": bool,
    "PartialMatchPriority": bool,
    "SetAccess": list,
    "SetObservable": bool,
    "TestParameter": bool,
    "Transient": bool,
}

# From
#  - https://mathworks.com/help/matlab/matlab_oop/method-attributes.html
#  - https://mathworks.com/help/matlab/ref/matlab.unittest.testcase-class.html
MATLAB_METHOD_ATTRIBUTE_TYPES = {
    "Abstract": bool,
    "Access": list,
    "Hidden": bool,
    "ParameterCombination": bool,
    "Sealed": list,
    "Static": bool,
    "Test": bool,
    "TestClassSetup": bool,
    "TestClassTeardown": bool,
    "TestMethodSetup": bool,
    "TestMethodTeardown": bool,
    "TestTags": list,
}

# The parsed-source model lives in ``env.domaindata['mat']`` and is pickled
# with the environment (plan.md Sections 2.1 and 2.2):
#
#   index: source path -> index-stage record (reference, type, mtime, size,
#     warnings, unreadable; matlab_name, private, scope from naming).
#   entities_table: path reference -> object. The index stage creates the
#     folders (the project root is ``./``); files are parsed on first use
#     (``resolve``).
#   parse_cache / parse_warnings: (path, mtime_ns, size) -> parsed object /
#     its parse-stage warnings, kept while the key is unchanged.
#   matlab_names: MATLAB name -> path references of the files and namespace
#     folders with that name (class-folder methods are reached through their
#     class).
#   doc_sources: docname -> {path: (mtime_ns, size)} of the files a document
#     used while it was read (incremental builds, parse-stage warnings).
#
# Tree-sitter objects are never pickled (see ``MatObject.__reduce_ex__``).


class _DetachedEntityStore:
    """Fallback for parsing outside a Sphinx build (unit tests that call
    ``MatObject.matlabify``): no index; files are parsed on every lookup.
    """

    def __init__(self):
        self.entities_table = {}
        self.data = {"matlab_names": {}, "index": {}}

    def source_paths(self):
        return {}

    def resolve(self, ref):
        parts = [] if ref == ROOT_REFERENCE else ref.rstrip("/").split("/")
        path = os.path.join(MatObject.basedir, *parts)
        if ref.endswith("/"):
            if not os.path.isdir(path) or (parts and parts[0][:1] in ("_", ".")):
                return None
            return MatModule(os.path.basename(path), path, ref)
        handler = handler_for(path)
        if handler is None:
            return None
        stem = os.path.splitext(parts[-1])[0]
        obj = _parse(handler, path, stem, folder_of(ref), handler.classify(path))
        obj.ref = ref
        return obj


_detached_store = _DetachedEntityStore()


def _domain(env=None):
    """Return the owner of the transient entity model: the live ``mat`` domain
    when a Sphinx build is active, else a detached fallback store.
    """
    env = env or MatObject.sphinx_env
    if env is None:
        return _detached_store
    return env.get_domain("mat")


def _is_class_folder(folder):
    return folder.rstrip("/").rpartition("/")[2].startswith("@")


def _reading_docname(dom):
    """Return the document being read, or None outside the read phase."""
    if isinstance(dom, _DetachedEntityStore):
        return None
    builder = getattr(MatObject.sphinx_app, "builder", None)
    if getattr(builder, "phase", None) != BuildPhase.READING:
        return None
    return dom.env.docname or None


def _note_lookup(dom):
    """Mark the document being read as depending on the index; return its
    used files (``{path: (mtime_ns, size)}``) or None.
    """
    docname = _reading_docname(dom)
    if docname is None:
        return None
    return dom.data["doc_sources"].setdefault(docname, {})


def _note_use(dom, ref):
    """Record a file (with its class-folder siblings) as used by the document
    being read. A file that could not be read makes it re-read every build.
    """
    used = _note_lookup(dom)
    if used is None:
        return
    folder = folder_of(ref)
    refs = [ref]
    if _is_class_folder(folder):
        refs = [r for _, r in dom.entities_table[folder].members if r[-1:] != "/"]
    for r in refs:
        path = dom.source_paths()[r]
        record = dom.data["index"][path]
        used[path] = (record["mtime"], record["size"])
        if getattr(dom.entities_table.get(r), "unreadable", False):
            dom.env.note_reread()


def resolve(dom, ref):
    """Object of a file or folder reference (without members), or None.

    Folders come from the index stage. A file is parsed on first use in this
    process, or taken from the parse cache, and recorded as used by the
    document being read.
    """
    if isinstance(dom, _DetachedEntityStore):
        return dom.resolve(ref)
    if ref[-1:] == "/":
        return dom.entities_table.get(ref)
    obj = dom.entities_table.get(ref)
    if obj is None:
        if ref not in dom.source_paths():
            return None
        folder = folder_of(ref)
        if _is_class_folder(folder):
            _load_class_folder(dom, folder)
        else:
            _load(dom, ref)
        obj = dom.entities_table[ref]
    _note_use(dom, ref)
    return obj


def _resolve_path(dom, rel):
    """Object for a path below the root, given without its extension or the
    trailing ``/`` of a folder (``.m`` before ``.mlapp``), or None.
    """
    for ref in (f"{rel}/", rel, *(rel + h.extension for h in handlers())):
        obj = resolve(dom, ref)
        if obj is not None:
            return obj
    return None


def _parse(handler, path, stem, folder, kind, text=None):
    """Parse one file; a failure gives an empty placeholder of the type ``kind``."""
    try:
        if text is None:
            obj = handler.parse(path, stem, folder, MatObject.encoding)
        else:
            obj = handler.parse(path, stem, folder, MatObject.encoding, text=text)
    except Exception as err:  # one bad file must not stop the build
        obj = placeholder(kind, stem, folder)
        obj.source_warnings = [("parse_error", failure_message(err), None)]
        obj.unreadable = isinstance(err, OSError)
    if obj is None:
        obj = placeholder(kind, stem, folder)
    if obj.source_type is None:
        obj.source_type = kind
    return obj


def _load(dom, ref):
    """Parse (or reuse) the file ``ref`` and add it to the entities table."""
    data = dom.data
    path = dom.source_paths()[ref]
    record = data["index"][path]
    key = (path, record["mtime"], record["size"])
    obj = data["parse_cache"].get(key)
    if obj is None:
        stem = os.path.splitext(os.path.basename(path))[0]
        if record["unreadable"]:
            # The index stage warned already.
            obj = placeholder(record["type"], stem, folder_of(ref))
            obj.source_type, obj.unreadable = record["type"], True
        else:
            handler = handler_by_extension(path)
            text = dom.text_cache.pop(key, None)
            obj = _parse(handler, path, stem, folder_of(ref), record["type"], text)
            if warnings := parse_warnings(path, ref, obj):
                data["parse_warnings"][key] = warnings
            # A read failure may go away without changing the key (permissions).
            if not obj.unreadable:
                data["parse_cache"][key] = obj
    else:
        logger.debug("[sphinxcontrib-matlabdomain] reusing parsed %s", path)
    obj.ref = ref
    obj.matlab_name = record["matlab_name"]
    obj.private, obj.scope = record["private"], record["scope"]
    dom.entities_table[ref] = obj
    return obj


def _load_class_folder(dom, folder):
    """Load the files of the class folder ``folder`` and attach its functions
    to its class as methods.
    """
    cf_entity = dom.entities_table[folder]
    entities = [
        (name, _load(dom, ref)) for name, ref in cf_entity.members if ref[-1:] != "/"
    ]
    class_entities = [e for e in entities if isinstance(e[1], MatClass)]
    func_entities = [e for e in entities if isinstance(e[1], MatFunction)]
    if len(class_entities) > 1:
        # Other classdef files in a class folder are not its class.
        class_entities = [e for e in class_entities if e[1].name == cf_entity.name[1:]]
    if len(class_entities) != 1:
        return
    cls = class_entities[0][1]
    # Drop folder methods attached to a class reused from the parse cache.
    cls.methods = {
        n: m
        for n, m in cls.methods.items()
        if not getattr(m, "from_class_folder", False)
    }
    for _, func in func_entities:
        func.__class__ = MatMethod
        func.cls = cls
        # TODO: Find the method attributes defined in classfolder class definition.
        func.attrs = {}
        func.from_class_folder = True
        cls.methods[func.name] = func


def _attrs(dom, ref):
    """``(matlab_name, private, scope)`` of a discovered file or folder
    reference, without parsing; None if it is not discovered.
    """
    if ref[-1:] == "/":
        entity = dom.entities_table.get(ref)
        if entity is None:
            return None
        return (
            getattr(entity, "matlab_name", None),
            getattr(entity, "private", False),
            getattr(entity, "scope", None),
        )
    record = dom.data["index"].get(dom.source_paths().get(ref))
    if record is None:
        return None
    return record.get("matlab_name"), record.get("private", False), record.get("scope")


def source_scope(dom, reference):
    """Folder from which the MATLAB name of ``reference`` is visible."""
    file, _ = parse_reference(reference)
    attrs = _attrs(dom, reference if file is None else file)
    return folder_of(reference) if attrs is None else attrs[2]


def _member(entity, members):
    obj = entity
    for name in members:
        if obj is None or isinstance(obj, MatModule):
            return None
        obj = obj.getter(name)
    return obj


def lookup_reference(dom, reference):
    """Discovered object with the exact path reference, or None."""
    file, members = parse_reference(reference)
    if file is None:
        return None
    return _member(resolve(dom, file), members)


def _named(dom, name):
    """Return ``(reference, file or folder reference)`` for a MATLAB name.

    The longest known prefix of ``name`` is the file or folder; the rest are
    members (``pkg.Cls.method``).
    """
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        refs = dom.data["matlab_names"].get(".".join(parts[:i]))
        if refs:
            return [(".".join([ref, *parts[i:]]), ref) for ref in refs]
    return []


def is_source_object(obj):
    """Return True for anything but a folder."""
    return not isinstance(obj, MatModule)


def is_folder_object(obj):
    """Return True for a folder."""
    return isinstance(obj, MatModule)


# ``find_source`` tests these on the reference, so it need not parse files.
is_source_object.folders = False
is_folder_object.folders = True


def _match(dom, ref, accept):
    """``(ref, object)`` if ``ref`` names an accepted object, else None.

    The object is None (resolved later) when ``accept`` only asks for a file
    or a folder.
    """
    file, members = parse_reference(ref)
    if file is None:
        return None
    folders = getattr(accept, "folders", None)
    if folders is not None and not members:
        if file[-1:] == "/":
            exists = file in dom.entities_table
        else:
            exists = file in dom.source_paths()
        return (ref, None) if exists and (file[-1:] == "/") == folders else None
    obj = _member(resolve(dom, file), members)
    return (ref, obj) if obj is not None and accept(obj) else None


def _resolved(dom, matches):
    return [(ref, resolve(dom, ref) if obj is None else obj) for ref, obj in matches]


def find_source(dom, target, folder=None, accept=is_source_object):
    """Resolve ``target`` to discovered objects (plan.md Section 2.3).

    1. exact path reference; 2. relative to ``folder`` (a path below it, or a
    MATLAB name visible from it, including its ``private/``); 3. a MATLAB name,
    excluding private files. Only objects for which ``accept(obj)`` is true
    count. A folder path without its trailing ``/`` is tried last, so it
    never shadows a MATLAB name. Returns ``[(reference, object)]``: empty if
    nothing matches, several if the MATLAB name is ambiguous. Only the files
    of the returned objects (and of members looked up) are parsed.
    """
    _note_lookup(dom)
    target = unicodedata.normalize("NFC", target.strip())
    if target == ".":
        target = ROOT_REFERENCE
    exact = [target]
    if folder:
        exact.append(join_reference(folder, target))
    for ref in exact:
        if match := _match(dom, ref, accept):
            return _resolved(dom, [match])

    named = [
        (match, _attrs(dom, base))
        for ref, base in _named(dom, target)
        if (match := _match(dom, ref, accept))
    ]
    if folder:
        local = [match for match, attrs in named if attrs[2] == folder]
        if local:
            return _resolved(dom, local)
    public = [match for match, attrs in named if not attrs[1]]
    if public:
        return _resolved(dom, public)

    for ref in (f"{r}/" for r in exact if not r.endswith("/")):
        if match := _match(dom, ref, accept):
            return _resolved(dom, [match])
    return []


def folder_reference(dom, target, location=None):
    """Folder reference for a folder argument (path or namespace name)."""
    target = target.strip()
    if target in (".", ROOT_REFERENCE):
        return ROOT_REFERENCE
    found = find_source(dom, target, accept=is_folder_object)
    if len(found) == 1:
        return found[0][0]
    if found:
        logger.warning(
            "[sphinxcontrib-matlabdomain] %r matches several folders: %s; "
            "use a path reference",
            target,
            ", ".join(sorted(ref for ref, _ in found)),
            type="mat",
            subtype="ambiguous",
            location=location,
        )
    return target if target.endswith("/") else f"{target}/"


def display_name(dom, reference):
    """MATLAB name of a reference, or the reference if it has none."""
    file, members = parse_reference(reference)
    attrs = _attrs(dom, file if file is not None else reference)
    if attrs is None or attrs[0] is None:
        return reference
    return ".".join([attrs[0], *members])


# Warnings hidden files (``is_hidden``) do not get.
_HIDDEN_QUIET = ("invalid_matlab", "no_code")
# Warnings the index stage reports; a parse does not repeat them.
_INDEX_SUBTYPES = ("encoding",)


def _warn(subtype, message, location=None):
    logger.warning(
        "[sphinxcontrib-matlabdomain] %s",
        message,
        type="mat",
        subtype=subtype,
        location=location,
    )


def _warn_file(ref, path, subtype, message, row):
    if not (is_hidden(ref) and subtype in _HIDDEN_QUIET):
        _warn(subtype, f"{ref} {message}", None if row is None else f"{path}:{row + 1}")


def parse_warnings(path, ref, obj):
    """Parse-stage warnings of a parsed file, ``[(subtype, message, location)]``:
    problems recorded while parsing it (``source_warnings``) and
    ``classify()`` disagreeing with tree-sitter.
    """
    hidden = is_hidden(ref)
    out = [
        (subtype, f"{ref} {message}", None if row is None else f"{path}:{row + 1}")
        for subtype, message, row in obj.source_warnings
        if subtype not in _INDEX_SUBTYPES and not (hidden and subtype in _HIDDEN_QUIET)
    ]
    if (
        not hidden
        and obj.parsed_type is not None
        and obj.source_type != obj.parsed_type
    ):
        out.append(
            (
                "invalid_matlab",
                f"{ref}: its first line of code makes it a {obj.source_type}, but "
                f"tree-sitter parsed a {obj.parsed_type}; the file may be invalid "
                "MATLAB.",
                None,
            )
        )
    return out


def project_root(config, srcdir):
    """Absolute project root, or ``None`` if none is configured.

    A relative ``matlab_src_code_project_root`` is relative to ``srcdir``
    (keeps sphinx-multiversion working).
    """
    root = config.matlab_src_code_project_root
    if root is None:
        return None
    return os.path.normpath(os.path.join(srcdir, root))


DEFAULT_INCLUDE_PATTERNS = ["**/*"]
_GLOB_CHARS = frozenset("*?[")


def _normalize_pattern(pattern, option):
    """``pattern`` relative to the project root, ``..`` resolved; a
    ConfigError for a pattern that is not relative or leaves the root.
    """
    if not isinstance(pattern, str) or not pattern:
        raise ConfigError(f"{option}: {pattern!r} is not a glob pattern.")
    if "\\" in pattern:
        raise ConfigError(f"{option}: {pattern!r} must use '/' as the separator.")
    if pattern.startswith("/") or ntpath.splitdrive(pattern)[0]:
        raise ConfigError(
            f"{option}: {pattern!r} must be relative to matlab_src_code_project_root."
        )
    parts, wildcard = [], False
    for part in pattern.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            # ``..`` after a wildcard cannot be resolved.
            if wildcard or not parts:
                raise ConfigError(
                    f"{option}: {pattern!r} leaves matlab_src_code_project_root."
                )
            parts.pop()
            continue
        wildcard = wildcard or not _GLOB_CHARS.isdisjoint(part)
        parts.append(part)
    if not parts:
        raise ConfigError(
            f"{option}: {pattern!r} names nothing below matlab_src_code_project_root."
        )
    return "/".join(parts) + ("/" if pattern.endswith("/") else "")


def _literal_folder(pattern):
    """``(folder, deep)``: the literal folders a pattern starts with (``''``
    for none), and whether it can match files in folders below them.
    """
    parts = pattern.rstrip("/").split("/")
    out = ""
    for part in parts[:-1]:
        if not _GLOB_CHARS.isdisjoint(part):
            return out, True
        out += f"{part}/"
    return out, "**" in parts[-1]


class SourceFilter:
    """Include and exclusion patterns below the project root (plan.md Section
    2.4), matched like Sphinx's ``exclude_patterns`` against path references
    (case-sensitive on every OS).
    """

    def __init__(self, include, exclude):
        self.include = Matcher(include)
        self.exclude = Matcher(exclude)
        #: ``(folder, deep)`` the include patterns can reach
        self.reach = sorted({_literal_folder(p) for p in include})
        self.prefixes = sorted({folder for folder, _ in self.reach})

    def includes(self, ref):
        """Return True for a file reference to index."""
        return self.include(ref) and not self.exclude(ref)

    def walks(self, folder):
        """Return True for a folder reference (``a/b/``) to list."""
        if self.exclude(folder) or self.exclude(folder[:-1]):
            return False
        return any(
            prefix.startswith(folder) or (deep and folder.startswith(prefix))
            for prefix, deep in self.reach
        )


def source_filter(config):
    """Return the validated ``SourceFilter`` of the config (or ConfigError)."""
    values = {}
    for option in ("matlab_src_include_patterns", "matlab_src_exclusion_patterns"):
        value = getattr(config, option)
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            raise ConfigError(f"{option} must be a list of glob patterns.")
        values[option] = [_normalize_pattern(p, option) for p in value]
    include = values["matlab_src_include_patterns"]
    exclude = values["matlab_src_exclusion_patterns"]
    customized = list(config.matlab_src_include_patterns) != DEFAULT_INCLUDE_PATTERNS
    if (customized or exclude) and config.matlab_src_code_project_root is None:
        raise ConfigError(
            "matlab_src_include_patterns and matlab_src_exclusion_patterns need "
            "matlab_src_code_project_root."
        )
    return SourceFilter(include, exclude)


def _within(path, root, pathmod=os.path):
    """Return True if the (resolved) ``path`` is ``root`` or below it."""
    try:
        common = pathmod.commonpath([path, root])
    except ValueError:  # another drive
        return False
    return pathmod.normcase(common) == pathmod.normcase(root)


def check_pattern_folders(sources, root):
    """ConfigError if a literal folder of an include pattern resolves (through
    a link or junction) outside the project root.
    """
    real_root = os.path.realpath(root)
    for prefix in sources.prefixes:
        path = os.path.join(root, *prefix.split("/"))
        if prefix and os.path.exists(path):
            real = os.path.realpath(path)
            if not _within(real, real_root):
                raise ConfigError(
                    f"matlab_src_include_patterns: {prefix} resolves to {real}, "
                    "outside matlab_src_code_project_root."
                )


def _is_link(path):
    """Return True for a symbolic link or a Windows junction."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    junction = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)
    return stat.S_ISLNK(st.st_mode) or (
        junction is not None and getattr(st, "st_reparse_tag", None) == junction
    )


class _Indexer:
    """The index stage (plan.md Section 2.1): walk the project root and read,
    decode and classify each source file once. Records of files whose
    ``(path, mtime, size)`` did not change are reused without reading.
    """

    def __init__(self, dom, basedir, config, old_index):
        self.dom = dom
        self.basedir = basedir
        self.old_index = old_index
        self.sources = source_filter(config)
        self.real_root = os.path.realpath(basedir)
        self.cache_text = config.matlab_src_cache_text
        self.pending = {}

    def run(self):
        root = MatModule(os.path.basename(self.basedir), self.basedir, ROOT_REFERENCE)
        self.dom.entities_table[ROOT_REFERENCE] = root
        self.list(root)
        self.descend(root)

    def descend(self, mod):
        for _, ref in mod.members:
            if ref[-1:] == "/":
                child = self.pending.pop(ref)
                self.dom.entities_table[ref] = child
                self.list(child)
                self.descend(child)

    def outside(self, path, ref):
        real = os.path.realpath(path)
        if _within(real, self.real_root):
            return False
        _warn(
            "outside_root",
            f"{ref} links to {real}, outside matlab_src_code_project_root; it is "
            "not documented.",
        )
        return True

    def list(self, mod):
        mod.members = []
        try:
            keys = os.listdir(mod.path)
        except OSError as err:  # one bad folder must not stop the build
            _warn(
                "parse_error",
                f"{mod.ref} cannot be read ({err.strerror or err}); its contents "
                "are not documented.",
            )
            return
        for key in keys:
            if _undecodable(key):
                _warn(
                    "parse_error",
                    f"{mod.ref}: the name {os.fsencode(key)!r} is not valid UTF-8; "
                    "it is not documented.",
                )
                continue
            path = os.path.join(mod.path, key)
            ref = join_reference(mod.ref, unicodedata.normalize("NFC", key))
            if os.path.isdir(path):
                # "." (VCS and editors) and "_" (Sphinx build/temp folders)
                if key.startswith((".", "_")):
                    continue
                ref += "/"
                if not self.sources.walks(ref):
                    continue
                if _is_link(path) and self.outside(path, ref):
                    continue
                self.pending[ref] = MatModule(key, path, ref)
                mod.members.append((key, ref))
                continue
            handler = handler_for(path)
            if handler is None or not self.sources.includes(ref):
                continue
            if _is_link(path) and self.outside(path, ref):
                continue
            if self.index_file(handler, path, ref):
                mod.members.append((os.path.splitext(key)[0], ref))

    def index_file(self, handler, path, ref):
        try:
            st = os.stat(path)
        except OSError:
            return False
        key = (path, st.st_mtime_ns, st.st_size)
        old = self.old_index.get(path)
        if (
            old is not None
            and (old["mtime"], old["size"]) == key[1:]
            and not old["unreadable"]
        ):
            kind, problems, unreadable = old["type"], old["warnings"], False
        else:
            text = None
            try:
                kind, text, problems = handler.read(path, MatObject.encoding)
                unreadable = False
            except OSError as err:
                kind, unreadable = UNKNOWN, True
                problems = [("parse_error", failure_message(err), None)]
            if self.cache_text and text is not None:
                self.dom.text_cache[key] = text
        self.dom.data["index"][path] = {
            "reference": ref,
            "type": kind,
            "mtime": key[1],
            "size": key[2],
            "warnings": problems,
            "unreadable": unreadable,
        }
        for subtype, message, row in problems:
            _warn_file(ref, path, subtype, message, row)
        return True


def _index_shape(data):
    """Return what names and lookups depend on: the folders, and each file's
    reference and type.
    """
    files = {path: (r["reference"], r["type"]) for path, r in data["index"].items()}
    return files, frozenset(r for r in data["entities_table"] if r[-1:] == "/")


def analyze(app):
    """Index stage (``builder-inited``): find, read and classify the sources.

    Files are parsed later, when a document needs them (``resolve``).
    """
    env = app.env
    dom = env.get_domain("mat")
    data = dom.data
    # Records and parses of the previous build are reused for unchanged files
    # unless the config changed.
    reuse = env.config_status == CONFIG_OK
    old_index = dict(data["index"]) if reuse else {}
    old_shape = _index_shape(data) if reuse else None
    if not reuse:
        data["parse_cache"].clear()
        data["parse_warnings"].clear()
    data["index"].clear()
    data["matlab_names"].clear()
    dom.entities_table.clear()
    dom.reset_sources()
    MatModuleAnalyzer.cache.clear()
    MatObject.encoding = env.config.matlab_src_encoding
    MatObject.sphinx_env = env
    MatObject.sphinx_app = app

    basedir = project_root(env.config, env.srcdir)
    if basedir is None:
        logger.debug("[sphinxcontrib-matlabdomain] no project root, skipping parsing.")
    else:
        MatObject.basedir = basedir
        if os.path.isdir(basedir):
            try:
                _Indexer(dom, basedir, env.config, old_index).run()
            except Exception as e:
                import traceback

                logger.error(f"[sphinxcontrib-matlabdomain] ERROR in analyze: {e}")
                logger.error(
                    f"[sphinxcontrib-matlabdomain] Traceback: {traceback.format_exc()}"
                )
                raise
        name_sources(dom, env.config)

    keys = {(path, r["mtime"], r["size"]) for path, r in data["index"].items()}
    for cache in (data["parse_cache"], data["parse_warnings"]):
        for key in [k for k in cache if k not in keys]:
            del cache[key]
    dom.index_changed = old_shape is not None and old_shape != _index_shape(data)


def outdated_docs(app, env, added, changed, removed):  # noqa: ARG001
    """``env-get-outdated``: documents that used a source file that changed,
    and, when files or folders were added, removed or changed type, every
    document that looked up sources.
    """
    dom = env.get_domain("mat")
    index = dom.data["index"]

    def stale(used):
        return any(
            (record := index.get(path)) is None
            or (record["mtime"], record["size"]) != key
            for path, key in used.items()
        )

    return [
        docname
        for docname, used in dom.data["doc_sources"].items()
        if dom.index_changed or stale(used)
    ]


def note_read_docs(app, env, docnames):  # noqa: ARG001
    """``env-before-read-docs``: remember the documents this build reads."""
    env.get_domain("mat").read_docnames = list(docnames)


def warn_parsed_sources(app, env):  # noqa: ARG001
    """``env-updated``: parse-stage warnings, once per file that a document
    read in this build used, in index order (serial and parallel builds).
    """
    dom = env.get_domain("mat")
    data = dom.data
    used = set()
    for docname in dom.read_docnames:
        used.update(data["doc_sources"].get(docname, ()))
    for path, record in data["index"].items():
        if path in used:
            key = (path, record["mtime"], record["size"])
            for warning in data["parse_warnings"].get(key, ()):
                _warn(*warning)


def _scope(folder):
    """Folder a MATLAB name in ``folder`` (a folder reference) is visible from.

    The folder above its ``@class`` and ``+namespace`` folders; for
    ``private/`` the folder holding it.
    """
    parts = [] if folder == ROOT_REFERENCE else folder.rstrip("/").split("/")
    if parts and parts[-1] == "private":
        parts.pop()
    else:
        parts = parts[: next((i for i, p in enumerate(parts) if p[:1] == "@"), None)]
        while parts and parts[-1][:1] == "+":
            parts.pop()
    return "/".join(parts) + "/" if parts else ROOT_REFERENCE


# Stands in for the project root (a plain folder), so folders above the root
# never change a MATLAB name.
_ROOT_PART = "root"


def _naming_folder(reference):
    return os.path.normpath(os.path.join(_ROOT_PART, folder_of(reference)))


def name_sources(dom, config):
    """Name every discovered file and folder the MATLAB way (``form_matlab_name``).

    Sets ``matlab_name`` (``None`` if MATLAB cannot call it), ``private`` and
    ``scope`` (see ``_scope``) on each entity and ``index`` record, and fills
    ``matlab_names``. Files MATLAB cannot call, except below ``private/``, get
    one ``mat.not_callable`` warning each.
    """
    version = config.matlab_version
    namelengthmax = mat_naming.resolve_namelengthmax(
        version, config.matlab_namelengthmax
    )
    matlab_names = dom.data["matlab_names"]
    for path, record in dom.data["index"].items():
        ref = record["reference"]
        name = mat_naming.form_matlab_name(
            _naming_folder(ref),
            os.path.basename(path),
            record["type"] == "class",
            namelengthmax,
            version,
        )
        matlab_name = mat_naming.canonical_name(name) if name else None
        private = is_private_reference(ref)
        scope = _scope(folder_of(ref)) if name is not None else None
        record.update(matlab_name=matlab_name, private=private, scope=scope)
        entity = dom.entities_table.get(ref)
        if entity is not None:
            entity.matlab_name, entity.private, entity.scope = matlab_name, private, scope
        if name is None:
            if not private and not is_hidden(ref):
                logger.warning(
                    "[sphinxcontrib-matlabdomain] %s is not callable in MATLAB %s; "
                    "it is documented by its path reference.",
                    ref,
                    version,
                    type="mat",
                    subtype="not_callable",
                )
            continue
        if matlab_name == name.pseudo_fullname:
            matlab_names.setdefault(matlab_name, []).append(ref)

    for ref, entity in dom.entities_table.items():
        if not isinstance(entity, MatModule):
            continue
        namespace = mat_naming.folder_namespace(
            _naming_folder(ref), namelengthmax, version
        )
        entity.matlab_name = namespace
        entity.private = is_private_reference(ref)
        entity.scope = _scope(ref) if namespace is not None else None
        if namespace is not None:
            matlab_names.setdefault(namespace, []).append(ref)


_TREE_SITTER_TYPES = (Node, Tree, Parser)


def _undecodable(name):
    """Return True for a file name ``os.listdir`` could not decode (POSIX bytes)."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def placeholder(kind, name, folder):
    """Empty object of the index type ``kind`` for a file that failed to parse."""
    if kind == "application":
        return MatApplication(name, folder, "")
    cls = {"class": MatClass, "function": MatFunction}.get(kind, MatScript)
    obj = cls.__new__(cls)
    MatObject.__init__(obj, name)
    obj.module = folder
    obj.docstring = None
    if cls is MatClass:
        obj.attrs, obj.bases, obj.properties = {}, [], {}
        obj.methods, obj.enumerations, obj.rem_tks = {}, {}, None
    elif cls is MatFunction:
        obj.retv, obj.args, obj.rem_tks = {}, {}, None
    else:
        obj.tokens = None
    return obj


def failure_message(err):
    """Warning text for a file that could not be read or parsed."""
    if isinstance(err, OSError):
        return (
            f"cannot be read ({err.strerror or err}); it is documented without "
            "content."
        )
    return (
        f"cannot be parsed ({type(err).__name__}: {err}); it is documented "
        "without content."
    )


def _restore_matobject(class_name):
    cls = globals()[class_name]
    return cls.__new__(cls)


class MatObject(object):
    """
    Base MATLAB object to which all others are subclassed.

    :param name: Name of MATLAB object.
    :type name: str

    MATLAB objects can be :class:`MatModule`, :class:`MatFunction`,
    :class:`MatApplication` or :class:`MatClass`.
    :class:`MatModule` are just folders that define a pseudo
    namespace for :class:`MatFunction`, :class:`MatApplication`
    and :class:`MatClass` in that folder.
    :class:`MatFunction` and :class:`MatClass` must begin with either
    ``function`` or ``classdef`` keywords.
    :class:`MatApplication` must be a ``.mlapp`` file.
    """

    basedir = None
    encoding = None
    sphinx_env = None
    sphinx_app = None
    #: index type (the handler's ``classify()``), tree-sitter's type (``.m``
    #: only) and whether the file has code
    source_type = None
    parsed_type = None
    has_code = True
    #: ``(subtype, message, row or None)`` found while parsing; the build
    #: warns them through ``parse_warnings``
    source_warnings = ()
    #: the file could not be read (an empty placeholder that is not cached)
    unreadable = False

    def __init__(self, name):
        #: name of MATLAB object
        self.name = name

    def ref_role(self):
        """Returns role to use for references to this object (e.g. when generating auto-links)"""
        return "ref"

    def __reduce_ex__(self, protocol):
        # The ``__module__`` property breaks pickle's default class lookup, so
        # restore by class name. Live tree-sitter objects are dropped.
        state = {
            k: None if isinstance(v, _TREE_SITTER_TYPES) else v
            for k, v in self.__dict__.items()
        }
        return (_restore_matobject, (type(self).__name__,), state)

    @property
    def __name__(self):
        return self.name

    def __repr__(self):
        # __str__() method not required, if not given, then __repr__() used
        return '<%s: "%s">' % (self.__class__.__name__, self.name)

    def getter(self, name, *defargs):
        if name == "__name__":
            return self.__name__
        elif len(defargs) == 0:
            logger.debug(
                '[sphinxcontrib-matlabdomain] Warning attribute "%s" was not found in %s.',
                name,
                self,
            )
            return None
        elif len(defargs) == 1:
            return defargs[0]
        else:
            return defargs

    @staticmethod
    def matlabify(objname):
        """
        Makes a MatObject.

        :param objname: Path of the object below the project root
            (:attr:`MatObject.basedir`) with ``/`` separators: a folder
            (``"target/+package"``), a file (``"target/+package/ClassBar.m"``)
            or a file without its extension (``.m`` is tried before
            ``.mlapp``). ``""`` is the project root.
        :type objname: str

        Every object gets its path reference as ``ref``. In a build, only
        discovered objects are found; files are parsed on first use.
        """
        if objname is None:
            return None
        rel = unicodedata.normalize("NFC", objname.strip("/"))
        if rel in ("", "."):
            return resolve(_domain(), ROOT_REFERENCE)
        return _resolve_path(_domain(), rel)

    @staticmethod
    def parse_mfile(mfile, name, path, encoding=None, text=None):
        """
        Parse an ``.m`` file with tree-sitter.

        :param mfile: Full path of mfile.
        :type mfile: str
        :param name: Name of :class:`MatObject`.
        :type name: str
        :param path: Folder reference of the folder containing :class:`MatObject`.
        :type path: str
        :param encoding: ``matlab_src_encoding``; ``None`` follows MATLAB
            (see ``decode_source``).
        :type encoding: str
        :param text: The decoded file content, if already read (the index
            stage reported its decoding problems); ``None`` reads the file.
        :type text: str
        :returns: :class:`MatObject` that represents the type of mfile.

        The type is ``classify()`` of the text (``source_type``). The object is
        built from what tree-sitter parsed (``parsed_type``); the two differ
        only for invalid MATLAB (see ``parse_warnings``). If the parser fails,
        the object is an empty ``placeholder`` of the ``classify()`` type.
        Problems are recorded in ``source_warnings``.
        """
        problem = None
        if text is None:
            with open(mfile, "rb") as code_f:
                text, problem = decode_source(code_f.read(), encoding)
        warnings = [] if problem is None else [("encoding", *problem)]
        source_type = classify(mfile, text)

        # tree-sitter reads UTF-8 and splits comment blocks at CRLF
        code = text.encode("utf-8")
        tree = Parser(ML_LANG).parse(code.replace(b"\r\n", b"\n"))

        modname = path  # folder reference

        parsed = parsed_type(tree.root_node)
        try:
            if parsed == "class":
                logger.debug(
                    "[sphinxcontrib-matlabdomain] parsing classdef %s from %s.",
                    name,
                    modname,
                )
                obj = MatClass(name, modname, tree.root_node, "utf-8")
            elif parsed == "function":
                logger.debug(
                    "[sphinxcontrib-matlabdomain] parsing function %s from %s.",
                    name,
                    modname,
                )
                obj = MatFunction(name, modname, tree.root_node, "utf-8")
            else:
                obj = MatScript(name, modname, tree.root_node, "utf-8")
        except Exception as err:  # one bad file must not stop the build
            obj = placeholder(source_type, name, modname)
            error = first_error(tree.root_node)
            message = failure_message(err)
            row = None
            if error is not None:
                row, column = error
                message += f" First syntax error: line {row + 1}, column {column + 1}."
            warnings.append(("parse_error", message, row))
            parsed = None
        else:
            decl_name = None
            if parsed == "function":
                decl_name = declaration(tree.root_node).child_by_field_name("name")
            if decl_name is not None and decl_name.type == "property_name":
                warnings.append(
                    (
                        "invalid_matlab",
                        f"declares the dotted function name "
                        f"{decl_name.text.decode('utf-8', errors='replace')}; MATLAB "
                        "allows dotted names only in a classdef file. It is "
                        "documented under its file name.",
                        get_row(decl_name.start_point),
                    )
                )
            parsed = parsed or "script"
        obj.source_type = source_type
        obj.parsed_type = parsed
        obj.has_code = source_type != "script" or bool(first_line_of_code(text))
        obj.source_warnings = warnings
        return obj

    @staticmethod
    def parse_mlappfile(mlappfile, name, path):
        """
        Uses ZipFile to read the metadata/appMetadata.xml file and
        the metadata/coreProperties.xml file description tags.
        Parses XML content using ElementTree.

        :param mlappfile: Full path of ``.mlapp`` file.
        :type mlappfile: str
        :param name: Name of :class:`MatApplication`.
        :type name: str
        :param path: Folder reference of the folder containing :class:`MatApplication`.
        :type path: str
        :returns: :class:`MatApplication` representing the application.
        """
        # Read contents of meta-data file
        # This might change in different Matlab versions
        # Note: `code` is a verbatim copy of the MATLAB source inside the App
        #       Variable `codeText` will contain the source.
        with ZipFile(mlappfile, "r") as mlapp:
            meta = ET.fromstring(mlapp.read("metadata/appMetadata.xml"))
            core = ET.fromstring(mlapp.read("metadata/coreProperties.xml"))
            # code = ET.fromstring(mlapp.read("matlab/document.xml"))

        metaNs = {"ns": "http://schemas.mathworks.com/appDesigner/app/2017/appMetadata"}
        coreNs = {
            "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
            "dc": "http://purl.org/dc/elements/1.1/",
            "dcmitype": "http://purl.org/dc/dcmitype/",
            "dcterms": "http://purl.org/dc/terms/",
            "xsi": "http://www.w3.org/2001/XMLSchema-instance",
        }
        # codeNs = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

        coreDesc = core.find("dc:description", coreNs)
        metaDesc = meta.find("ns:description", metaNs)
        # codeDesc = code.find(".//w:t", codeNs)
        # codeText = codeDesc.text

        doc = []
        if coreDesc is not None and coreDesc.text is not None:
            doc.append(coreDesc.text)
        if metaDesc is not None and metaDesc.text is not None:
            doc.append(metaDesc.text)
        docstring = "\n\n".join(doc)

        modname = path  # folder reference

        return MatApplication(name, modname, docstring)


# TODO: get docstring and __all__ from contents.m if exists
class MatModule(MatObject):
    """
    All MATLAB modules are packages. A package is a folder that serves as the
    namespace for any :class:`MatObjects` in the package folder. Sphinx will
    treats objects without a namespace as builtins, so all MATLAB projects
    should be packaged in a folder so that they will have a namespace. This
    can also be accomplished by using the MATLAB +folder package scheme.

    :param name: Name of :class:`MatObject`.
    :type name: str
    :param path: Path of folder containing :class:`MatObject`.
    :type path: str
    :param package: Folder reference (``target/+package/``; ``./`` for the root).
    :type package: str
    """

    def __init__(self, name, path, package):
        super(MatModule, self).__init__(name)
        #: Path to module on disk, path to package's __init__.py
        self.path = path
        #: folder reference
        self.package = package
        self.ref = package
        #: ``(name, reference)`` of its files (name without extension) and
        #: folders in listing order, set by the index stage
        self.members = None

    def ref_role(self):
        """Returns role to use for references to this object (e.g. when generating auto-links)"""
        return "folder"

    def member_refs(self):
        """``(name, reference)`` of the files and folders in the folder."""
        if self.members is None:  # detached use: no index stage
            self.members = []
            try:
                keys = os.listdir(self.path)
            except OSError:
                keys = []
            for key in keys:
                path = os.path.join(self.path, key)
                ref = join_reference(self.ref, unicodedata.normalize("NFC", key))
                if os.path.isdir(path):
                    if not key.startswith((".", "_")):
                        self.members.append((key, f"{ref}/"))
                elif handler_for(path) is not None:
                    self.members.append((os.path.splitext(key)[0], ref))
        return self.members

    def safe_getmembers(self):
        """``(name, object)`` of the files and folders in the folder; every
        file is parsed (or taken from the parse cache).
        """
        dom = _domain()
        return [(name, resolve(dom, ref)) for name, ref in self.member_refs()]

    @property
    def __doc__(self):
        return None

    @property
    def __all__(self):
        return self.safe_getmembers()

    @property
    def __path__(self):
        return [self.path]

    @property
    def __file__(self):
        return self.path

    @property
    def __package__(self):
        return self.package

    def getter(self, name, *defargs):
        """
        :class:`MatModule` ``getter`` method to get attributes.
        """
        if name == "__name__":
            return self.__name__
        elif name == "__doc__":
            return self.__doc__
        elif name == "__all__":
            return self.__all__
        elif name == "__file__":
            return self.__file__
        elif name == "__path__":
            return self.__path__
        elif name == "__package__":
            return self.__package__
        elif name == "__module__":
            logger.debug(
                "[sphinxcontrib-matlabdomain] mod %s is a package does not have __module__.",
                self,
            )
            return None
        else:
            # A file before a same-named folder.
            refs = [ref for n, ref in self.member_refs() if n == name]
            dom = _domain()
            if refs:
                return resolve(dom, next((r for r in refs if r[-1:] != "/"), refs[0]))
            # A path below the folder, e.g. with its extension (``f.m``).
            return _resolve_path(dom, join_reference(self.ref, name))


class MatFunction(MatObject):
    """
    A MATLAB function.

    :param name: Name of :class:`MatObject`.
    :type name: str
    :param modname: Name of folder containing :class:`MatObject`.
    :type modname: str
    :param tokens: List of tokens parsed from mfile by Pygments.
    :type tokens: list
    """

    def __init__(self, name, modname, tokens, encoding):
        super(MatFunction, self).__init__(name)
        parsed_function = MatFunctionParser(tokens, encoding)
        #: Path of folder containing :class:`MatObject`.
        self.module = modname
        #: docstring
        self.docstring = parsed_function.docstring
        #: output args
        self.retv = parsed_function.retv
        #: input args
        self.args = parsed_function.args
        #: remaining tokens after main function is parsed
        self.rem_tks = None

    def ref_role(self):
        """Returns role to use for references to this object (e.g. when generating auto-links)"""
        return "func"

    @property
    def __doc__(self):
        return self.docstring

    @property
    def __module__(self):
        return self.module

    def getter(self, name, *defargs):
        if name == "__name__":
            return self.__name__
        elif name == "__doc__":
            return self.__doc__
        elif name == "__module__":
            return self.__module__
        else:
            super(MatFunction, self).getter(name, *defargs)


class MatClass(MatObject):
    """
    A MATLAB class definition.

    :param name: Name of :class:`MatObject`.
    :type name: str
    :param path: Path of folder containing :class:`MatObject`.
    :type path: str
    :param tokens: List of tokens parsed from mfile by Pygments.
    :type tokens: list
    """

    def __init__(self, name, modname, tokens, encoding):
        super(MatClass, self).__init__(name)
        parsed_class = MatClassParser(tokens, encoding)
        #: Path of folder containing :class:`MatObject`.
        self.module = modname
        #: dictionary of class attributes
        self.attrs = parsed_class.attrs
        #: list of class superclasses
        self.bases = parsed_class.supers
        #: docstring
        self.docstring = parsed_class.docstring
        #: dictionary of class properties
        self.properties = parsed_class.properties
        #: dictionary of class methods
        self.methods = {
            name: MatMethod(name, parsed_fun, modname, self)
            for (name, parsed_fun) in parsed_class.methods.items()
        }
        #:
        self.enumerations = parsed_class.enumerations
        #: remaining tokens after main class definition is parsed
        self.rem_tks = None

    def ref_role(self):
        """Returns role to use for references to this object (e.g. when generating auto-links)"""
        return "class"

    def fullname(self, env):  # noqa: ARG002
        """Returns the path reference of the class, for use as link target"""
        return self.ref

    def link(self, env, name=None):  # noqa: ARG002
        """Returns link for class object"""
        title = name or getattr(self, "matlab_name", None) or self.ref
        return f":class:`{title} <{self.ref}>`"

    @property
    def __module__(self):
        return self.module

    @property
    def __doc__(self):
        return self.docstring

    @property
    def __bases__(self):
        bases_ = dict.fromkeys(list(self.bases))  # make copy of bases
        dom = _domain()
        folder = folder_of(self.ref) if getattr(self, "ref", None) else None
        for base in bases_:
            found = find_source(
                dom, base, folder, accept=lambda obj: isinstance(obj, MatClass)
            )
            if len(found) == 1:
                bases_[base] = found[0][1]

        return bases_

    def getter(self, name, *defargs):
        """
        :class:`MatClass` ``getter`` method to get attributes.
        """
        if name == "__name__":
            return self.__name__
        elif name == "__doc__":
            return self.__doc__
        elif name == "__module__":
            return self.__module__
        elif name == "__bases__":
            return self.__bases__
        elif name in self.properties:
            return MatProperty(name, self, self.properties[name])
        elif name in self.enumerations:
            return MatEnumeration(name, self, self.enumerations[name])
        elif name in self.methods:
            return self.methods[name]
        elif name in self.enumerations:
            return
        elif name == "__dict__":
            objdict = {pn: self.getter(pn) for pn in self.properties}
            objdict.update(self.methods)
            objdict.update({en: self.getter(en) for en in self.enumerations})
            return objdict
        else:
            super(MatClass, self).getter(name, *defargs)


class MatProperty(MatObject):
    def __init__(self, name, cls, attrs):
        super(MatProperty, self).__init__(name)
        self.cls = cls
        self.attrs = attrs["attrs"]
        self.default = attrs["default"]
        self.docstring = attrs["docstring"]
        self.size = attrs["size"]
        self.type = attrs["type"]
        self.validators = attrs["validators"]

    def ref_role(self):
        """Returns role to use for references to this object (e.g. when generating auto-links)"""
        return "attr"

    @property
    def __module__(self):
        return self.cls.module

    @property
    def __doc__(self):
        return self.docstring


class MatEnumeration(MatObject):
    def __init__(self, name, cls, attrs):
        super(MatEnumeration, self).__init__(name)
        self.cls = cls
        self.docstring = attrs["docstring"]

    def ref_role(self):
        """Returns role to use for references to this object (e.g. when generating auto-links)"""
        return "enum"

    @property
    def __module__(self):
        return self.cls.module

    @property
    def __doc__(self):
        return self.docstring


class MatMethod(MatFunction):
    def __init__(self, name, parsed_function, modname, cls):
        self.name = name
        #: Path of folder containing :class:`MatObject`.
        self.module = modname
        #: docstring
        self.docstring = parsed_function.docstring
        #: output args
        self.retv = parsed_function.retv
        #: input args
        self.args = parsed_function.args
        self.cls = cls
        self.attrs = parsed_function.attrs

    def ref_role(self):
        """Returns role to use for references to this object (e.g. when generating auto-links)"""
        return "meth"

    @property
    def __module__(self):
        return self.module

    @property
    def __doc__(self):
        return self.docstring


class MatScript(MatObject):
    def __init__(self, name, modname, tks, encoding):
        super(MatScript, self).__init__(name)
        parsed_script = MatScriptParser(tks, encoding)
        #: Path of folder containing :class:`MatScript`.
        self.module = modname
        #: List of tokens parsed from mfile by Pygments.
        self.tokens = tks
        #: docstring
        self.docstring = parsed_script.docstring

    @property
    def __doc__(self):
        return self.docstring

    @property
    def __module__(self):
        return self.module


class MatApplication(MatObject):
    """
    Representation of the documentation in a Matlab Application.

    :param name: Name of :class:`MatObject`.
    :type name: str
    :param modname: Name of folder containing :class:`MatObject`.
    :type modname: str
    :param desc: Summary and description string.
    :type desc: str
    """

    def __init__(self, name, modname, desc):
        super(MatApplication, self).__init__(name)
        #: Path of folder containing :class:`MatApplication`.
        self.module = modname
        #: docstring
        self.docstring = desc

    @property
    def __doc__(self):
        return self.docstring

    @property
    def __module__(self):
        return self.module


class MatException(MatObject):
    def __init__(self, name, path, tks):
        super(MatException, self).__init__(name)
        self.path = path
        self.tks = tks
        self.docstring = ""

    @property
    def __doc__(self):
        return self.docstring


class MatcodeError(Exception):
    def __str__(self):
        res = self.args[0]
        if len(self.args) > 1:
            res += " (exception was: %r)" % self.args[1]
        return res


class MatModuleAnalyzer(object):
    # cache for analyzer objects -- caches both by module and file name
    cache = {}

    @classmethod
    def for_folder(cls, dirname, modname):
        if ("folder", dirname) in cls.cache:
            return cls.cache["folder", dirname]
        obj = cls(None, modname, dirname, True)
        cls.cache["folder", dirname] = obj
        return obj

    @classmethod
    def for_module(cls, modname):
        if ("module", modname) in cls.cache:
            entry = cls.cache["module", modname]
            if isinstance(entry, MatcodeError):
                raise entry
            return entry
        mod = resolve(_domain(), modname)
        if isinstance(mod, MatModule):
            obj = cls.for_folder(mod.path, modname)
        elif isinstance(mod, MatClass):
            obj = cls.for_folder(mod.module, modname)
        else:
            err = MatcodeError("error importing %r" % modname)
            cls.cache["module", modname] = err
            raise err
        cls.cache["module", modname] = obj
        return obj

    def __init__(self, source, modname, srcname, decoded=False):
        # name of the module
        self.modname = modname
        # name of the source file
        self.srcname = srcname
        # file-like object yielding source lines
        self.source = source
        # cache the source code as well
        self.encoding = None
        self.code = None
        # will be filled by tokenize()
        self.tokens = None
        # will be filled by parse()
        self.parsetree = None
        # will be filled by find_attr_docs()
        self.attr_docs = None
        self.tagorder = None
        # will be filled by find_tags()
        self.tags = None

    def find_attr_docs(self, scope=""):
        """Find class and module-level attributes and their documentation."""
        if self.attr_docs is None:
            self.attr_docs = _AttrDocs(_domain().entities_table.get(self.modname))
            self.tagorder = _TagOrder(self.attr_docs)
        return self.attr_docs


class _AttrDocs(Mapping):
    """``(namespace, name) -> docstring`` of a folder's files and of its
    classes' members. Documenter namespaces are ``".".join(objpath)``, so
    only the project root's entries can match. A class is parsed only when
    its namespace is asked for.
    """

    def __init__(self, mod):
        # Documenter namespaces are ".".join(objpath); only root members match.
        members = mod.member_refs() if isinstance(mod, MatModule) else []
        self.package = "" if mod is None or mod.ref == ROOT_REFERENCE else mod.ref
        self.files = [(name, ref) for name, ref in members if ref[-1:] != "/"]
        #: file name -> position (the last file with that name)
        self.order = {name: i for i, (name, _) in enumerate(self.files)}
        self._classes = {}

    def class_members(self, stem):
        """Members (``__dict__``) of the classes in the files named ``stem``."""
        if stem not in self._classes:
            members = {}
            for name, ref in self.files:
                if name == stem:
                    obj = resolve(_domain(), ref)
                    if isinstance(obj, MatClass):
                        members.update(obj.getter("__dict__"))
            self._classes[stem] = members
        return self._classes[stem]

    def names(self, namespace):
        """Member names in ``namespace``, in source order."""
        if namespace == self.package:
            return list(dict.fromkeys(name for name, _ in self.files))
        if namespace.startswith(self.package):
            return list(self.class_members(namespace[len(self.package) :]))
        return []

    def __contains__(self, key):
        return key[1] in self.names(key[0])

    def __getitem__(self, key):
        namespace, name = key
        if namespace == self.package:
            refs = [ref for n, ref in self.files if n == name]
            if refs:
                return resolve(_domain(), refs[-1]).docstring
        elif namespace.startswith(self.package):
            members = self.class_members(namespace[len(self.package) :])
            if name in members:
                return members[name].docstring
        raise KeyError(key)

    def __iter__(self):
        for name in self.names(self.package):
            yield self.package, name
        for stem in self.names(self.package):
            for name in self.class_members(stem):
                yield f"{self.package}{stem}", name

    def __len__(self):
        return sum(1 for _ in self)


class _TagOrder:
    """Source order of a folder's files (``name``) and of a class's members
    (``Class.member``), for ``:member-order: bysource``.
    """

    def __init__(self, attr_docs):
        self.attr_docs = attr_docs

    def get(self, tag, default=None):
        order = self.attr_docs.order
        if tag in order:
            return order[tag]
        stem, dot, member = tag.partition(".")
        names = list(self.attr_docs.class_members(stem)) if dot else []
        return names.index(member) if member in names else default

    def __len__(self):
        # The default position for members without one: after all others.
        return sys.maxsize
