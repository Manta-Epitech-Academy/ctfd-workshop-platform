"""The app shell: the core theme templates this plugin replaces outright.

Why template overrides rather than CSS: CTFd's navbar is a dark `fixed-top`
Bootstrap bar over a dark centred `.jumbotron` hero, and jump's talent space has
neither. Recolouring it cannot get there, which is what two CSS-only passes
established the hard way. The markup itself has to change.

Each entry is the smallest surface that can do its job, and `base.html` is
deliberately not among them: owning it would mean owning `window.init`, the
`{{ Plugins.styles }}` hook and every future upstream change to them, to gain
nothing these do not already reach.

`CTFd/` itself stays pristine (see CLAUDE.md). `override_template` is CTFd's own
plugin API for exactly this: it writes into `app.overridden_templates`, which is
the FIRST loader of the Jinja `ChoiceLoader`, ahead of the theme loader. The key
is the unprefixed logical name, so an override applies whichever theme an
instance is running.

Anything added here goes in the "Portability notes" section of
plugins/workshop/README.md too — an override is the first thing a CTFd upgrade
has to be re-checked against.
"""
import os

from CTFd.plugins import override_template

# Logical template name -> the file in this plugin that replaces its body.
#
# Kept as includes so the markup lives in real template files that an editor, a
# linter and `git diff` can all see, rather than in Python strings. Reachable
# because CTFd points a FileSystemLoader at the plugin tree under the `plugins`
# prefix.
#
#   components/navbar.html  the app bar: Epitech wordmark, nav, account menu.
#   page.html               an authored CTFd Page ("Parcours", a subject index,
#                           the terms). Core wraps the body in a bare
#                           `.container` and renders no title, so it was the one
#                           surface with no brand band and nothing naming it.
OVERRIDES = {
    "components/navbar.html": "plugins/workshop/templates/navbar.html",
    "page.html": "plugins/workshop/templates/page.html",
}


def load_shell(app):
    for template, source in OVERRIDES.items():
        # An override is silent by nature: if upstream renames or restructures a
        # template, `overridden_templates` keeps happily serving ours and the
        # only symptom is a page that quietly stops matching the theme it
        # belongs to. Checking the stock file is still where we think it is
        # turns that into a line in the log at boot. A warning and not a hard
        # failure: a moved template must never be the reason an instance
        # refuses to start mid-session.
        stock = os.path.join(app.root_path, "themes", "core", "templates", template)
        if not os.path.isfile(stock):
            app.logger.warning(
                "workshop: %s is gone from the core theme (looked in %s). The "
                "Epitech shell override is still active and may now be based on "
                "a contract that no longer exists — re-check it against the "
                "upgraded theme.",
                template,
                stock,
            )

        override_template(template, '{%% include "%s" %%}\n' % source)
