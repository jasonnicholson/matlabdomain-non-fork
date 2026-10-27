"""
sphinxcontrib.mat_types
~~~~~~~~~~~~~~~~~~~~~~~

Types for MATLAB.

:copyright: Copyright 2014-2024 by the sphinxcontrib-matlabdomain team, see AUTHORS.
:license: BSD, see LICENSE for details.
"""

import os
import unicodedata
import xml.etree.ElementTree as ET
from io import open  # for opening files with encoding in Python 2
from zipfile import ZipFile

from sphinx.environment import CONFIG_OK
from sphinx.util.logging import getLogger

from . import mat_naming
from .mat_classify import classify, decode_source, first_line_of_code, is_hidden
from .mat_handlers import handler_for, handlers

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
    path_reference,
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

# The parsed-source entity model is stored in ``env.domaindata['mat']`` and
# pickled with the environment. It is rebuilt each build by ``analyze()``;
# unchanged files reuse their parsed ``MatObject`` from ``parse_cache``.
# Tree-sitter objects are never pickled (see ``MatObject.__reduce_ex__``).
# Access the current build's model through ``_domain()``.
#
#   entities_table: path reference -> MatObject, for every discovered file and
#     folder (the project root is ``./``).
#   matlab_names: MATLAB name -> path references of the files and namespace
#     folders with that name (class-folder methods are reached through their
#     class).


class _DetachedEntityStore:
    """Fallback entity model for detached (non-Sphinx) parsing, e.g. unit tests
    that call ``MatObject.matlabify`` outside a build. Real builds always own the
    model on the live domain, so this is not used during a Sphinx build."""

    def __init__(self):
        self.entities_table = {}
        self.data = {"matlab_names": {}}


_detached_store = _DetachedEntityStore()


def _domain(env=None):
    """Return the owner of the transient entity model: the live ``mat`` domain
    when a Sphinx build is active, else a detached fallback store."""
    env = env or MatObject.sphinx_env
    if env is None:
        return _detached_store
    return env.get_domain("mat")


def recursive_find_all(obj):
    # Recursively finds all entities in all "modules" aka directories.
    # Bug fix: Check if obj.entities exists and is not None
    if not hasattr(obj, "entities") or obj.entities is None:
        return

    for _, o in obj.entities:
        if isinstance(o, MatModule):
            o.safe_getmembers()
            if o.entities:
                recursive_find_all(o)


def recursive_log_debug(obj, indent=""):
    # Traverse the object hierarchy and log to debug
    # Bug fix: Check if obj.entities exists and is not None
    if not hasattr(obj, "entities") or obj.entities is None:
        return

    for n, o in obj.entities:
        logger.debug(
            "[sphinxcontrib-matlabdomain] %s Name=%s, Entity=%s", indent, n, str(o)
        )
        if isinstance(o, MatModule):
            if o.entities:
                indent = indent + " "
                names = [n_ for n_, o_ in o.entities]
                logger.debug(
                    "[sphinxcontrib-matlabdomain] %s Names=%s", indent, str(names)
                )
                # print(indent + f"{names=}")
                recursive_log_debug(o, indent)
                indent = indent[:-1]
        if isinstance(o, MatClass):
            logger.debug(
                "[sphinxcontrib-matlabdomain] %s -> name=%s, methods=%s",
                indent,
                str(o.name),
                str(o.methods),
            )


def populate_entities_table(obj):
    # Recursively scan the hierarchy of entities and populate the domain's table.
    if not hasattr(obj, "entities") or obj.entities is None:
        return

    dom = _domain()
    for _, o in obj.entities:
        dom.entities_table[o.ref] = o
        if isinstance(o, MatModule):
            if o.entities:
                populate_entities_table(o)


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
    return _member(dom.entities_table.get(file), members)


def _named(dom, name):
    """``(reference, object, source entity)`` for a MATLAB name.

    The longest known prefix of ``name`` is the file or folder; the rest are
    members (``pkg.Cls.method``).
    """
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        refs = dom.data["matlab_names"].get(".".join(parts[:i]))
        if refs:
            rest = parts[i:]
            found = []
            for ref in refs:
                entity = dom.entities_table.get(ref)
                obj = _member(entity, rest)
                if obj is not None:
                    found.append((".".join([ref, *rest]), obj, entity))
            return found
    return []


def is_source_object(obj):
    """True for anything but a folder."""
    return not isinstance(obj, MatModule)


def find_source(dom, target, folder=None, accept=is_source_object):
    """Resolve ``target`` to discovered objects (plan.md Section 2.3).

    1. exact path reference; 2. relative to ``folder`` (a path below it, or a
    MATLAB name visible from it, including its ``private/``); 3. a MATLAB name,
    excluding private files. Only objects for which ``accept(obj)`` is true
    count. A folder path without its trailing ``/`` is tried last, so it
    never shadows a MATLAB name. Returns ``[(reference, object)]``: empty if
    nothing matches, several if the MATLAB name is ambiguous.
    """
    target = unicodedata.normalize("NFC", target.strip())
    if target == ".":
        target = ROOT_REFERENCE
    exact = [target]
    if folder:
        exact.append(join_reference(folder, target))
    for ref in exact:
        obj = lookup_reference(dom, ref)
        if obj is not None and accept(obj):
            return [(ref, obj)]

    named = [(r, o, e) for r, o, e in _named(dom, target) if accept(o)]
    if folder:
        local = [(r, o) for r, o, e in named if getattr(e, "scope", None) == folder]
        if local:
            return local
    public = [(r, o) for r, o, e in named if not getattr(e, "private", False)]
    if public:
        return public

    for ref in (f"{r}/" for r in exact if not r.endswith("/")):
        obj = dom.entities_table.get(ref)
        if obj is not None and accept(obj):
            return [(ref, obj)]
    return []


def folder_reference(dom, target, location=None):
    """Folder reference for a folder argument (path or namespace name)."""
    target = target.strip()
    if target in (".", ROOT_REFERENCE):
        return ROOT_REFERENCE
    found = find_source(dom, target, accept=lambda obj: isinstance(obj, MatModule))
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
    entity = dom.entities_table.get(file if file is not None else reference)
    base = getattr(entity, "matlab_name", None)
    if base is None:
        return reference
    return ".".join([base, *members])


def source_key(path):
    """Cache key for a source file: ``(path, mtime_ns, size)``."""
    st = os.stat(path)
    return (path, st.st_mtime_ns, st.st_size)


# Warnings hidden files (``is_hidden``) do not get.
_HIDDEN_QUIET = ("invalid_matlab", "no_code")


def check_source(path, ref, obj):
    """Warn about a parsed file: problems recorded while parsing it
    (``source_warnings``), ``classify()`` and tree-sitter disagreeing, or no
    code. Runs on every build, also when the parse is reused.
    """
    hidden = is_hidden(ref)
    for subtype, message, row in obj.source_warnings:
        if hidden and subtype in _HIDDEN_QUIET:
            continue
        logger.warning(
            "[sphinxcontrib-matlabdomain] %s %s",
            ref,
            message,
            type="mat",
            subtype=subtype,
            location=None if row is None else f"{path}:{row + 1}",
        )
    if hidden:
        return
    if obj.parsed_type is not None and obj.source_type != obj.parsed_type:
        logger.warning(
            "[sphinxcontrib-matlabdomain] %s: its first line of code makes it a "
            "%s, but tree-sitter parsed a %s; the file may be invalid MATLAB.",
            ref,
            obj.source_type,
            obj.parsed_type,
            type="mat",
            subtype="invalid_matlab",
        )
    if not obj.has_code:
        logger.warning(
            "[sphinxcontrib-matlabdomain] %s has no code; it is documented as a "
            "script.",
            ref,
            type="mat",
            subtype="no_code",
        )


def _parse_cached(path, ref, parse):
    """Run ``parse()`` unless the previous build parsed the same key.

    Records the result in the domain's ``index`` and ``parse_cache``; the
    index ``type`` is the handler's ``classify()`` (``obj.source_type``).
    Detached parses (no Sphinx build) record nothing and do not warn.
    """
    dom = _domain()
    if isinstance(dom, _DetachedEntityStore):
        obj = parse()
        if obj is not None:
            obj.ref = ref
        return obj
    key = source_key(path)
    obj = dom.previous_parse_cache.pop(key, None)
    if obj is None:
        obj = parse()
    else:
        logger.debug("[sphinxcontrib-matlabdomain] reusing parsed %s", path)
    if obj is not None:
        obj.ref = ref
        check_source(path, ref, obj)
        # A read failure may go away without changing the key (permissions).
        if not obj.unreadable:
            dom.data["parse_cache"][key] = obj
        dom.data["index"][path] = {
            "reference": ref,
            "type": obj.source_type,
            "mtime": key[1],
            "size": key[2],
        }
    return obj


def project_root(config, srcdir):
    """Absolute project root, or ``None`` if none is configured.

    ``matlab_src_code_project_root`` wins over the deprecated
    ``matlab_src_dir``; relative values are relative to ``srcdir`` (keeps
    sphinx-multiversion working).
    """
    root = config.matlab_src_code_project_root
    if root is None:
        root = config.matlab_src_dir
    if root is None:
        return None
    return os.path.normpath(os.path.join(srcdir, root))


def analyze(app):
    # Using the "MatObject.matlabify" and "MatModule.safe_getmembers" the
    # project root is recursively scanned for MATLAB objects only once.
    # All entities found are stored in the domain's `entities_table`.

    try:
        # Shared source state lives in env.domaindata and is rebuilt here each
        # build. Parsed results from the previous build are reused for
        # unchanged files unless the config changed.
        dom = app.env.get_domain("mat")
        entities_table = dom.entities_table
        parse_cache = dom.data["parse_cache"]
        if app.env.config_status == CONFIG_OK:
            dom.previous_parse_cache = dict(parse_cache)
        else:
            dom.previous_parse_cache = {}
        parse_cache.clear()
        dom.data["index"].clear()
        dom.data["matlab_names"].clear()
        entities_table.clear()
        MatModuleAnalyzer.cache.clear()

        basedir = project_root(app.env.config, app.env.srcdir)
        if basedir is None:
            logger.debug(
                "[sphinxcontrib-matlabdomain] no project root, skipping parsing."
            )
            return

        MatObject.basedir = basedir  # set MatObject base directory
        MatObject.encoding = app.env.config.matlab_src_encoding
        MatObject.sphinx_env = app.env  # pass env to MatObject cls
        MatObject.sphinx_app = app  # pass app to MatObject cls

        # Set the root object and get root members.
        logger.debug("[sphinxcontrib-matlabdomain] Starting matlabify")
        root = MatObject.matlabify("")
        if not root:
            logger.debug("[sphinxcontrib-matlabdomain] root is None, returning")
            return

        logger.debug(
            f"[sphinxcontrib-matlabdomain] root={root}, root.entities={getattr(root, 'entities', 'NO ENTITIES ATTR')}"
        )
        root.safe_getmembers()
        logger.debug(
            f"[sphinxcontrib-matlabdomain] After safe_getmembers, root.entities={getattr(root, 'entities', 'NO ENTITIES ATTR')}"
        )

        logger.debug("[sphinxcontrib-matlabdomain] Starting recursive_find_all")
        recursive_find_all(root)
        logger.debug("[sphinxcontrib-matlabdomain] Finished recursive_find_all")

        # Print the hierarchy of entities to the log.
        logger.debug("[sphinxcontrib-matlabdomain] Found the following entities:")
        recursive_log_debug(root)

        logger.debug("[sphinxcontrib-matlabdomain] Starting populate_entities_table")
        entities_table[ROOT_REFERENCE] = root
        populate_entities_table(root)
        logger.debug("[sphinxcontrib-matlabdomain] Finished populate_entities_table")
        dom.previous_parse_cache = {}
    except Exception as e:
        import traceback

        logger.error(f"[sphinxcontrib-matlabdomain] ERROR in analyze: {e}")
        logger.error(
            f"[sphinxcontrib-matlabdomain] Traceback: {traceback.format_exc()}"
        )
        raise

    # Transform Class Folders classes from
    #
    # @ClassFolder (Module)
    #     ClassFolder (Class)
    #     method1 (Function)
    #     method2 (Function)
    #
    # To
    #
    # ClassFolder (Class) with the method1 and method2 add to the ClassFolder Class.

    class_folder_modules = [
        v
        for v in entities_table.values()
        if isinstance(v, MatModule) and v.name.startswith("@")
    ]
    # For each Class Folder module
    for cf_entity in class_folder_modules:
        # Bug fix: Check if cf_entity has entities and they're not None
        if not hasattr(cf_entity, "entities") or cf_entity.entities is None:
            continue

        # Find the class entity class.
        class_entities = [e for e in cf_entity.entities if isinstance(e[1], MatClass)]
        func_entities = [e for e in cf_entity.entities if isinstance(e[1], MatFunction)]

        if len(class_entities) > 1:
            # Other classdef files in a class folder are not its class.
            class_entities = [
                e for e in class_entities if e[1].name == cf_entity.name[1:]
            ]
        if len(class_entities) != 1:
            continue
        cls = class_entities[0][1]
        # Drop folder methods attached to a class reused from the parse cache.
        cls.methods = {
            n: m
            for n, m in cls.methods.items()
            if not getattr(m, "from_class_folder", False)
        }

        # Add functions to class
        for func_name, func in func_entities:
            func.__class__ = MatMethod
            func.cls = cls
            # TODO: Find the method attributes defined in classfolder class definition.
            func.attrs = {}
            func.from_class_folder = True
            cls.methods[func.name] = func

    name_sources(dom, app.env.config)


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
    #: ``(subtype, message, row or None)`` found while parsing; warned by
    #: ``check_source`` on every build
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

        Every object gets its path reference as ``ref``.
        """
        # no object name given
        logger.debug(f"[sphinxcontrib-matlabdomain] enter matlabify {objname=}.")

        if objname is None:
            return None
        rel = objname.strip("/")
        if rel in ("", "."):
            rel = ""
            fullpath = MatObject.basedir
            name = os.path.basename(fullpath)
        else:
            parts = rel.split("/")
            name = parts[-1]
            fullpath = os.path.join(MatObject.basedir, *parts)

        logger.debug(f"[sphinxcontrib-matlabdomain] matlabify {rel=}, {fullpath=}")
        if os.path.isdir(fullpath):
            if rel.startswith(("_", ".")):
                return None
            ref = path_reference(MatObject.basedir, fullpath, is_folder=True)
            mod = _domain().entities_table.get(ref)
            if isinstance(mod, MatModule):
                logger.debug("[sphinxcontrib-matlabdomain] Module %s already loaded.", ref)
                return mod
            logger.debug(
                f"[sphinxcontrib-matlabdomain] matlabify MatModule {ref=}, {fullpath=}"
            )
            return MatModule(name, fullpath, ref)

        handler = handler_for(fullpath)
        if handler is not None:
            stem = os.path.splitext(name)[0]
        else:
            stem = name
            handler = next(
                (h for h in handlers() if h.discover(fullpath + h.extension)), None
            )
            if handler is None:
                return None
            fullpath += handler.extension
        ref = path_reference(MatObject.basedir, fullpath, is_folder=False)
        folder = folder_of(ref)
        logger.debug(f"[sphinxcontrib-matlabdomain] matlabify parse {ref=}, {fullpath=}")

        def parse():
            try:
                obj = handler.parse(fullpath, stem, folder, MatObject.encoding)
            except Exception as err:  # one bad file must not stop the build
                obj = placeholder(handler.classify(fullpath), stem, folder)
                obj.source_warnings = [("parse_error", failure_message(err), None)]
                obj.unreadable = isinstance(err, OSError)
            if obj is not None and obj.source_type is None:
                obj.source_type = handler.classify(fullpath)
            return obj

        return _parse_cached(fullpath, ref, parse)

    @staticmethod
    def parse_mfile(mfile, name, path, encoding=None):
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
        :returns: :class:`MatObject` that represents the type of mfile.

        The type is ``classify()`` of the text (``source_type``). The object is
        built from what tree-sitter parsed (``parsed_type``); the two differ
        only for invalid MATLAB (see ``check_source``). If the parser fails,
        the object is an empty ``placeholder`` of the ``classify()`` type.
        Problems are recorded in ``source_warnings``.
        """
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

    #: the folder was listed (it may be empty or unreadable)
    listed = False

    def __init__(self, name, path, package):
        super(MatModule, self).__init__(name)
        #: Path to module on disk, path to package's __init__.py
        self.path = path
        #: folder reference
        self.package = package
        self.ref = package
        #: entities found in the module: class, function, module (subpath and +package)
        self.entities = []

    def ref_role(self):
        """Returns role to use for references to this object (e.g. when generating auto-links)"""
        return "folder"

    def safe_getmembers(self):
        logger.debug(
            f"[sphinxcontrib-matlabdomain] MatModule.safe_getmembers {self.name=}, {self.path=}, {self.package=}"
        )
        if self.entities or self.listed:
            return self.entities
        self.listed = True

        results = []
        try:
            keys = os.listdir(self.path)
        except OSError as err:  # one bad folder must not stop the build
            logger.warning(
                "[sphinxcontrib-matlabdomain] %s cannot be read (%s); its "
                "contents are not documented.",
                self.ref,
                err.strerror or err,
                type="mat",
                subtype="parse_error",
            )
            keys = []
        for key in keys:
            if _undecodable(key):
                logger.warning(
                    "[sphinxcontrib-matlabdomain] %s: the name %r is not valid "
                    "UTF-8; it is not documented.",
                    self.ref,
                    os.fsencode(key),
                    type="mat",
                    subtype="parse_error",
                )
                continue
            # make full path
            path = os.path.join(self.path, key)
            # Do not visit directories starting with:
            # - "." (VCS and Editors)
            # - "_" (build/temp folders in Sphinx)
            if os.path.isdir(path):
                if key.startswith(".") or key.startswith("_"):
                    continue
                name = key
            # Only visit source files with a handler
            elif handler_for(path) is not None:
                name = os.path.splitext(key)[0]
            else:
                continue
            # A file and a folder with the same name are separate objects.
            value = MatObject.matlabify(join_reference(self.ref, key))
            if value:
                results.append((name, value))
        self.entities = results
        # results.sort()
        return results

    @property
    def __doc__(self):
        return None

    @property
    def __all__(self):
        return self.entities
        # results = self.safe_getmembers()
        # if results:
        #     results = list(zip(*self.safe_getmembers()))[0]
        # return results

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
            # Search if we already has this entity (a file before a same-named folder)
            matches = [e for n, e in self.entities if n == name]
            if matches:
                logger.debug(
                    "[sphinxcontrib-matlabdomain] mod %s already has entity %s.",
                    self,
                    name,
                )
                return next(
                    (e for e in matches if not isinstance(e, MatModule)), matches[0]
                )
            # If not - try to MATLABIFY it.
            entity = MatObject.matlabify(join_reference(self.ref, name))
            if entity:
                self.entities.append((name, entity))
                logger.debug(
                    f"[sphinxcontrib-matlabdomain] entity {name=} imported from {self=}"
                )
                return entity


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
        mod = _domain().entities_table.get(modname)
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
        if self.attr_docs is not None:
            return self.attr_docs
        attr_visitor_collected = {}
        attr_visitor_tagorder = {}
        tagnumber = 0
        mod = _domain().entities_table.get(self.modname)
        # Documenter namespaces are ".".join(objpath); only root members match.
        package = "" if mod.package == ROOT_REFERENCE else mod.package

        # walk package tree
        for k, v in mod.safe_getmembers():
            if hasattr(v, "docstring"):
                attr_visitor_collected[package, k] = v.docstring
                attr_visitor_tagorder[k] = tagnumber
                tagnumber += 1
            if isinstance(v, MatClass):
                for mk, mv in v.getter("__dict__").items():
                    namespace = f"{package}{k}"
                    tagname = "%s.%s" % (k, mk)
                    tagname = tagname.lstrip(".")
                    attr_visitor_collected[namespace, mk] = mv.docstring
                    attr_visitor_tagorder[tagname] = tagnumber
                    tagnumber += 1
        self.attr_docs = attr_visitor_collected
        self.tagorder = attr_visitor_tagorder
        return attr_visitor_collected
