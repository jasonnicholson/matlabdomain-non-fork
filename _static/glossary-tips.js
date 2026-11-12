/* Hover tips for links to glossary terms (docs/_ext/glossary_tips.py).
 *
 * The build appends a call with the tips, keyed by link target relative to
 * the site root. A link in the page's main content whose target has a tip
 * shows it on hover and on keyboard focus; Escape closes it.
 */
function glossaryTips(tips) {
  "use strict";
  const root = new URL(
    document.documentElement.dataset.content_root || "./",
    location.href,
  );
  const targets = new Map();
  for (const [target, html] of Object.entries(tips)) {
    targets.set(new URL(target, root).href, html);
  }
  const targetOf = (link) => {
    const url = new URL(link.href);
    url.search = "";
    return url.href;
  };

  const tip = document.createElement("div");
  tip.className = "glossary-tip";
  tip.id = "glossary-tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.appendChild(tip);
  let current = null;
  let timer = 0;

  function place(link) {
    const gap = 6;
    const margin = 8;
    const box = link.getBoundingClientRect();
    const width = tip.offsetWidth;
    const height = tip.offsetHeight;
    const right = document.documentElement.clientWidth - width - margin;
    const left = Math.max(margin, Math.min(box.left, right));
    let top = box.bottom + gap;
    if (top + height > window.innerHeight - margin && box.top - gap - height > margin) {
      top = box.top - gap - height;
    }
    tip.style.left = `${left + window.scrollX}px`;
    tip.style.top = `${top + window.scrollY}px`;
  }
  function show(link) {
    clearTimeout(timer);
    if (current === link) return;
    hide();
    current = link;
    tip.innerHTML = targets.get(targetOf(link));
    tip.style.left = "0px";
    tip.style.top = "0px";
    tip.hidden = false;
    link.setAttribute("aria-describedby", tip.id);
    place(link);
  }
  function hide() {
    clearTimeout(timer);
    if (current) current.removeAttribute("aria-describedby");
    current = null;
    tip.hidden = true;
  }
  function later(action) {
    clearTimeout(timer);
    timer = setTimeout(action, 150);
  }

  const main = document.querySelector('[role="main"]') || document.body;
  for (const link of main.querySelectorAll("a[href]")) {
    if (!targets.has(targetOf(link))) continue;
    link.addEventListener("mouseenter", () => later(() => show(link)));
    link.addEventListener("mouseleave", () => later(hide));
    link.addEventListener("focus", () => show(link));
    link.addEventListener("blur", hide);
  }
  // The pointer may move onto the tip, for example to select its text.
  tip.addEventListener("mouseenter", () => clearTimeout(timer));
  tip.addEventListener("mouseleave", () => later(hide));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hide();
  });
}

glossaryTips({"reference/glossary.html#term-API": "<p class=\"glossary-tip-term\">API</p>\n<p>The functions, classes and other public interfaces a project provides.\nAPI reference pages describe how to call them and what they return.</p>\n", "reference/glossary.html#term-Docutils": "<p class=\"glossary-tip-term\">Docutils</p>\n<p>The Python tools that parse reStructuredText. Sphinx uses Docutils and\nadds its own directives, roles and build features.</p>\n", "reference/glossary.html#term-MyST": "<p class=\"glossary-tip-term\">MyST</p>\n<p>A Markdown format and parser that can express Sphinx directives and\nroles. It is an alternative to reStructuredText when enabled in Sphinx.</p>\n", "reference/glossary.html#term-PATH": "<p class=\"glossary-tip-term\">PATH</p>\n<p>The operating system’s list of folders to search for executable\ncommands. This is separate from MATLAB’s search path.</p>\n", "reference/glossary.html#term-RemoveInSphinxXXXWarning": "<p class=\"glossary-tip-term\">RemoveInSphinxXXXWarning</p>\n<p>A warning about a Sphinx feature scheduled for removal in a future\nversion, such as <code class=\"docutils literal notranslate\"><span class=\"pre\">RemovedInSphinx10Warning</span></code>. Check the warning\nmessage and the named Sphinx version when updating Sphinx or an\nextension. The Sphinx glossary spells it <code class=\"docutils literal notranslate\"><span class=\"pre\">RemoveInSphinxXXXWarning</span></code>.</p>\n", "reference/glossary.html#term-RemovedInSphinxXXWarning": "<p class=\"glossary-tip-term\">RemovedInSphinxXXWarning</p>\n<p>A warning about a Sphinx feature scheduled for removal in a future\nversion, such as <code class=\"docutils literal notranslate\"><span class=\"pre\">RemovedInSphinx10Warning</span></code>. Check the warning\nmessage and the named Sphinx version when updating Sphinx or an\nextension. The Sphinx glossary spells it <code class=\"docutils literal notranslate\"><span class=\"pre\">RemoveInSphinxXXXWarning</span></code>.</p>\n", "reference/glossary.html#term-builder": "<p class=\"glossary-tip-term\">builder</p>\n<p>The part of Sphinx that produces an output or performs a check. The\nHTML builder writes web pages; <code class=\"docutils literal notranslate\"><span class=\"pre\">matlab-api-check</span></code> checks MATLAB help.</p>\n", "reference/glossary.html#term-configuration-directory": "<p class=\"glossary-tip-term\">configuration directory</p>\n<p>The folder containing <code class=\"docutils literal notranslate\"><span class=\"pre\">conf.py</span></code>. It is normally the source directory,\nbut the <code class=\"docutils literal notranslate\"><span class=\"pre\">-c</span></code> build option can select a different folder.</p>\n", "reference/glossary.html#term-cross-reference": "<p class=\"glossary-tip-term\">cross-reference</p>\n<p>A link to a documented item or location, such as a function, page,\nsection or glossary entry. A role can create the link by name.</p>\n", "reference/glossary.html#term-directive": "<p class=\"glossary-tip-term\">directive</p>\n<p>A reStructuredText instruction for a block of content. For example,\n<code class=\"docutils literal notranslate\"><span class=\"pre\">..</span> <span class=\"pre\">mat:autofunction::</span> <span class=\"pre\">times_two</span></code> requests documentation for a MATLAB\nfunction. A directive can take options and content below its first line.</p>\n", "reference/glossary.html#term-doctree": "<p class=\"glossary-tip-term\">doctree</p>\n<p>Sphinx’s in-memory tree of nodes for a parsed document. Extensions can\ninspect or change the tree before a builder writes the output.</p>\n", "reference/glossary.html#term-document-name": "<p class=\"glossary-tip-term\">document name</p>\n<p>A source document’s path relative to the source directory, without its\nfilename extension, using forward slashes on every operating system.\nFor example, <code class=\"docutils literal notranslate\"><span class=\"pre\">getting-started/quickstart</span></code> names a page in this site.</p>\n", "reference/glossary.html#term-domain": "<p class=\"glossary-tip-term\">domain</p>\n<p>A group of directives and roles for related objects. The <code class=\"docutils literal notranslate\"><span class=\"pre\">mat</span></code> domain\ndescribes and links MATLAB objects; <code class=\"docutils literal notranslate\"><span class=\"pre\">py</span></code> does the same for Python.</p>\n", "reference/glossary.html#term-environment": "<p class=\"glossary-tip-term\">environment</p>\n<p>Sphinx’s stored information about the documents in a project. It helps\nSphinx resolve links and avoid rereading unchanged source files.</p>\n", "reference/glossary.html#term-extension": "<p class=\"glossary-tip-term\">extension</p>\n<p>A Python package or module that changes or adds Sphinx behavior. This\npackage adds the <code class=\"docutils literal notranslate\"><span class=\"pre\">mat</span></code> domain; <code class=\"docutils literal notranslate\"><span class=\"pre\">sphinxcontrib-mermaid</span></code> adds diagrams.</p>\n", "reference/glossary.html#term-inventory": "<p class=\"glossary-tip-term\">inventory</p>\n<p>The <code class=\"docutils literal notranslate\"><span class=\"pre\">objects.inv</span></code> file that lists a site’s documented targets and\ntheir addresses. Other Sphinx projects use it to create links to them.</p>\n", "reference/glossary.html#term-markup": "<p class=\"glossary-tip-term\">markup</p>\n<p>Text notation for structure and formatting, such as heading underlines\nand link syntax. A parser turns the notation into document elements.</p>\n", "reference/glossary.html#term-master-document": "<p class=\"glossary-tip-term\">master document</p>\n<p>The top-level source document, usually <code class=\"docutils literal notranslate\"><span class=\"pre\">index.rst</span></code>. It contains the\nroot table of contents that connects the documentation pages.</p>\n", "reference/glossary.html#term-object": "<p class=\"glossary-tip-term\">object</p>\n<p>A documented item, such as a MATLAB function or class. A description\ndirective creates an entry that a role can link to.</p>\n", "reference/glossary.html#term-reST": "<p class=\"glossary-tip-term\">reST</p>\n<p>A plain-text markup format used by Sphinx. Files commonly end in\n<code class=\"docutils literal notranslate\"><span class=\"pre\">.rst</span></code> and contain headings, links, directives and roles.</p>\n", "reference/glossary.html#term-reStructuredText": "<p class=\"glossary-tip-term\">reStructuredText</p>\n<p>A plain-text markup format used by Sphinx. Files commonly end in\n<code class=\"docutils literal notranslate\"><span class=\"pre\">.rst</span></code> and contain headings, links, directives and roles.</p>\n", "reference/glossary.html#term-role": "<p class=\"glossary-tip-term\">role</p>\n<p>Inline markup within text. For example, <code class=\"docutils literal notranslate\"><span class=\"pre\">:mat:func:`times_two`</span></code> links\nto the documentation of the MATLAB function <code class=\"docutils literal notranslate\"><span class=\"pre\">times_two</span></code>.</p>\n", "reference/glossary.html#term-root-document": "<p class=\"glossary-tip-term\">root document</p>\n<p>The top-level source document, usually <code class=\"docutils literal notranslate\"><span class=\"pre\">index.rst</span></code>. It contains the\nroot table of contents that connects the documentation pages.</p>\n", "reference/glossary.html#term-signature": "<p class=\"glossary-tip-term\">signature</p>\n<p>An object’s call or declaration syntax, such as a function name with\nits arguments. Sphinx displays it above the object’s description.</p>\n", "reference/glossary.html#term-source-directory": "<p class=\"glossary-tip-term\">source directory</p>\n<p>The folder containing a Sphinx project’s source documents and their\nsubfolders. Its location is an argument to <code class=\"docutils literal notranslate\"><span class=\"pre\">sphinx-build</span></code>.</p>\n", "reference/glossary.html#term-theme": "<p class=\"glossary-tip-term\">theme</p>\n<p>The appearance and navigation layout of the generated HTML site.\nThis documentation uses the Furo theme.</p>\n", "reference/glossary.html#term-toctree": "<p class=\"glossary-tip-term\">toctree</p>\n<p>A directive that connects pages into a table of contents. It controls\nthe document hierarchy and, in many themes, the navigation sidebar.</p>\n", "reference/glossary.html#term-virtual-environment": "<p class=\"glossary-tip-term\">virtual environment</p>\n<p>A separate Python interpreter and set of installed packages for a\nproject. uv normally keeps it in the project’s <code class=\"docutils literal notranslate\"><span class=\"pre\">.venv</span></code> folder.</p>\n"});
