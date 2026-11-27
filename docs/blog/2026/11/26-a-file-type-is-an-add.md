# A file type is an add

*Decided 2026-11-26. Revisit when a file type other than `.m` needs
something the registry can't give it, such as its own configuration
values or build hooks.*

Until this week, adding a file type to sphinxcontrib-matlabdomain meant
editing the core. App Designer apps (`.mlapp`) are a small feature: one
class that reads two XML files out of a zip archive. Yet the word
"application" appeared in seven modules: the file handler, the object
model, the documenters, the domain, the API check, `scmd apidoc` and `scmd
quickstart`. Simulink support was worse. When we cut it from the 1.0.0
work, about 1,600 lines came out of 16 core modules, on top of its own
package.

That doesn't scale to the file types still on the roadmap: live scripts,
Simscape components, and Simulink coming back in a new design. Each one
would make the core bigger, and anyone reading the core, a person or an AI
agent, would have to read past code for file types they aren't working
on.

So the rule now is that `.m` is the core file type and every other type is
an add. A type lives in one module under `sphinxcontrib/mat_filetypes/`,
and nothing outside that module mentions it. To remove a type, you delete
the module and one import line.

## What a file type hands over

A type gives the core three things.

The **handler** claims the type's files and reads them for the index: the
extension, `classify` and `read`. It goes into the same registry as the
`.m` handler, with `mat_handlers.register`.

The handler carries **`Kind` records**, one per index type. A `Kind` holds
the words the rest of the package shows for that type: the object type's
label in the domain ("application"), the cross-reference role (`app`), the
section title on apidoc pages ("Apps"), the noun in the quickstart report,
the plural in the API-check summary, and the general index entry. The
domain, the documenters and the commands read those words from the `Kind`,
so none of them has "application" typed into it.

The `.m` kinds work the same way. Function, class and script are `Kind`
records too, and the core no longer spells out their words. What the core
keeps for `.m` is what only `.m` does: classes have members, functions
have argument lists, and apidoc pages put their sections in a fixed order.

The **model** turns a file into an object: a `parse` function, the empty
placeholder for a file that fails to parse, and the object classes. It is
registered with `mat_types.register_model`.

This is everything the `.mlapp` module registers:

```python
mat_handlers.register(MlappHandler())
mat_types.register_model(
    MlappHandler.extension,
    parse,
    {APPLICATION.name: lambda name, folder: MatApplication(name, folder, "")},
)
```

## The cycle it removed

`parse` used to be a method of the handler, and the handlers imported
`mat_types` to run it. `mat_types` imports the registry, so each module
depended on the other; our import-direction test carried that as its first
exception. Now `parse` is registered from the type's module, which sits
above both, so the registry no longer imports anything above it and the
exception is gone.

## Other ways we could have done it

We could have kept `parse` on the handler and moved the `.m` handler into
`mat_types`. That reads more simply, but the registry would then be empty
until something imported `mat_types`, and path references look in the
registry before that happens.

We could have given each type hooks into the build and into each command:
a `setup(app)`, a callback for apidoc, another for the API check. `.mlapp`
needs none of them, and our import rules would put each build hook in a
module of its own. The first type that needs a hook will add it.

Python packages often find plugins through entry points in
`pyproject.toml`. That would turn the registry into an interface other
packages could depend on, and we don't promise any Python interface before
1.0.0.

## How we keep it that way

`tests/test_mat_handlers.py` defines a fake Simscape type, `.ssc`, that
registers a handler and a model and nothing else. The test builds a page
with it, links to it, looks for it in the general index and in the API-check
summary, and runs `scmd apidoc` on it. The same file checks that every
registered type has complete `Kind` records, and that no module outside a
type's own names its extension, its kinds or its classes.

Checked with Sphinx 9.2.0 and Python 3.14.3. On the 15 projects of the
real-project gauntlet, `.mlapp` files give the same pages, warnings and
API-check output as before the change.
