"""App Designer apps (``.mlapp``): a zip container with XML metadata.

The index stage claims every ``.mlapp`` file as an ``application`` without
opening it. The parse reads two metadata members through the guards of
``mat_container`` and gives a :class:`MatApplication` whose help is the
app's summary and description. Importing the module registers both; the
build and the commands document the app from the :class:`Kind` alone.
"""

from pathlib import Path

from .. import mat_handlers, mat_types
from ..mat_container import parse_xml, read_members
from ..mat_typing import override

APPLICATION = mat_handlers.Kind(
    name="application",
    label="application",
    role="app",
    section="Apps",
    noun="app",
    plural="apps",
    index_text="%s (application in %s)",
)


class MlappHandler(mat_handlers.Handler):
    """App Designer apps: every ``.mlapp`` file is one."""

    extension = ".mlapp"
    kinds = (APPLICATION,)

    @override
    def classify(self, path, text=None):
        return APPLICATION.name if self.discover(path) else "unknown"


class MatApplication(mat_types.MatObject):
    """The help of an App Designer app.

    :param name: Name of the app (the file's stem).
    :param modname: Folder reference of the folder that holds it.
    :param desc: Its summary and description.
    """

    def __init__(self, name, modname, desc):
        super().__init__(name)
        #: Folder reference of the folder that holds the app.
        self.module = modname
        #: docstring
        self.docstring = desc

    @property
    def __doc__(self):
        """The help text of the app (its summary and description): autodoc reads it as
        ``__doc__``, as it would the docstring of a Python object.
        """
        return self.docstring

    @property
    def __module__(self):
        """The folder reference of the folder that holds the app: autodoc reads it as
        ``__module__``, the module of a Python object.
        """
        return self.module


def parse(path, stem, folder, _encoding=None):
    """Return the :class:`MatApplication` of the app at ``path``.

    Its help is the ``description`` of ``metadata/coreProperties.xml`` (the
    Dublin Core ``dc:description``, the summary) and of
    ``metadata/appMetadata.xml``, in that order, a blank line apart. The
    names are matched without namespaces. ``code`` in the container is a
    copy of the app's MATLAB source and is not read.

    :raises ContainerFileError: for a file or member over a size limit, or
        XML with a DTD or nested too deep.
    """
    meta_part, core_part = "metadata/appMetadata.xml", "metadata/coreProperties.xml"
    meta_data, core_data = read_members(Path(path), (meta_part, core_part))
    meta = parse_xml(meta_data, meta_part)
    core = parse_xml(core_data, core_part)
    doc = [
        text
        for text in (core.child_text("description"), meta.child_text("description"))
        if text
    ]
    return MatApplication(stem, folder, "\n\n".join(doc))


mat_handlers.register(MlappHandler())
mat_types.register_model(
    MlappHandler.extension,
    parse,
    {APPLICATION.name: lambda name, folder: MatApplication(name, folder, "")},
)
