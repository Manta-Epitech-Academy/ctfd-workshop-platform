# DESIGN.md

Visual contract for the Epitech reskin. `plugins/workshop/assets/epitech-theme.css` is its one
implementation, injected via `register_plugin_stylesheet()` in `plugins/workshop/__init__.py` and
loaded on every page through CTFd core's `{{ Plugins.styles }}`. `CTFd/` is never touched for this
— same rule as everywhere else in this repo (see `CLAUDE.md`, "Never edit inside `CTFd/`").

No automated check enforces any of this (no `lint:design`, no contract test). This is prose, not
a gate — a deliberate scope call, not an oversight: see "What this deliberately isn't" below.

## Source of truth

Every value below is ported verbatim from `jump/frontend/src/routes/layout.css` (a sibling
Epitech product's own design contract) — its contrast ratios are already computed and commented
there; don't re-derive them here. If a token changes, port the diff from that file rather than
picking a new number by eye.

## Tokens

| Bootstrap var | Light | Dark |
|---|---|---|
| `--bs-primary` | `#013afb` (epi-blue) | `#809dfd` |
| `--bs-success` | `#007a46` (epi-tech-ink) | `#00ff97` (epi-tech) |
| `--bs-danger` | `#b5361a` (epi-together-ink) | `#ff9878` |
| `--bs-warning` | `#8a5a00` (epi-warning-ink) | `#ffd15c` |
| `--bs-dark` | `#0b0e1a` (epi-grey-900, jump's "chrome") | same |
| `--bs-body-bg` | `#f6f7fb` | `#0c0e13` |
| `--bs-body-color` | `#181818` | `#f1f2f6` |
| `--epi-card` / `--bs-card-bg` | `#ffffff` (jump's `--card`) | `#12151d` |
| `--bs-border-color` | `#d9dce8` | `#262b38` |
| `--bs-secondary-bg` / `--bs-tertiary-bg` | `#eceef5` | `#1b1f29` |
| `--bs-secondary-color` | `#555b71` | `#8b90a3` |
| `--bs-info` | CTFd default, untouched | CTFd default, untouched |

**Why light and dark get different hue values, when CTFd's own don't**: `--bs-primary` etc. are
the *same literal value* in both of CTFd's compiled theme blocks. We deliberately diverge,
because the "ink" shades (`epi-tech-ink`, `epi-together-ink`, `epi-warning-ink`) are calibrated
for a light surface and stop clearing contrast on a dark one — `jump` solved this once with a
light/dark split; reuse it, don't re-solve it.

**Four values are NOT in that table and never vary by theme**: `--epi-blue` (`#013afb`),
`--epi-tech` (`#00ff97`), `--epi-together` (`#ff5f3a`) and `--epi-grey-900` (`#0b0e1a`).
Everything in the table is a Bootstrap
*role* — `--bs-primary` means "the colour text and borders take on the current surface", and it is
correctly a different blue per theme. A brand *surface* is the opposite job. `jump` states the rule:
*"epiBlue never changes value. It is the logo. Dark surfaces use a separate darkPrimary for UI text
and borders."* Paint the band, the login panel or the neon CTA with `--bs-primary` and they turn
`#809dfd` in dark mode, next to a wordmark that stayed `#013afb`. `--epi-together` is the same
distinction one step further: `--bs-danger` already carries its *ink* variants (`#b5361a` /
`#ff9878`, both calibrated for body text), and the reward float wants the surface value, which is
neither of them.

**`--bs-info` is a deliberate gap.** `jump` defines no "info" role. Inventing one here would be
new design, not reuse — so it stays at CTFd's own default in both themes rather than guessing.

## Typography

Self-hosted (see `plugins/workshop/assets/fonts/LICENSE.md` — never a Google Fonts `<link>`; this
codebase's own rule is "vendor, don't CDN", `PLAN.md` §18, because a live session on school wifi
cannot be trusted to fetch anything external).

- **IBM Plex Sans Variable** — base font everywhere (`html, body, .container`), replacing Lato.
- **Anton** — `.jumbotron .container` **only**, replacing Raleway. Single weight (it only has
  one). Do not extend this to a bare `h1` or any heading selector sitewide: Anton is a poster
  face, and `jump`'s own `layout.css` documents the exact bug that follows from applying it
  broadly (a browser fakes the missing bold weight by smearing the glyphs). A display face is a
  choice a heading states, not a default it inherits.
- **Space Mono** — monospace contexts (code blocks, below).

## The app shell

The header is **not** CTFd's navbar restyled. `plugins/workshop/shell.py` replaces three core
templates outright — `components/navbar.html`, `page.html` and `login.html` — and the markup lives
in `plugins/workshop/templates/`.

**The header is the top half of one brand band.** It is `--epi-blue`, full width, `.on-dark`, and
the page's hero below it is the same blue with no seam between them: same colour, same width, no
margin, no z-index, no JS. That is a deliberate deviation from `jump` and it is argued in "The
brand band" below.

**Why a template override.** CTFd's shell is a dark `fixed-top` Bootstrap navbar above a dark
centred `.jumbotron` hero. `jump`'s talent space has neither, and no palette gets you from one to
the other — two CSS-only passes established that the expensive way. The markup had to change.

**How, without touching `CTFd/`.** `override_template()` is CTFd's own plugin API
(`CTFd/CTFd/plugins/__init__.py`); it writes into `app.overridden_templates`, which is the *first*
loader of the Jinja `ChoiceLoader` (`CTFd/CTFd/__init__.py`), ahead of the theme loader. Keys are
unprefixed logical names, so the override applies whichever theme an instance runs. Only the navbar
partial is overridden — never `base.html`, which would mean owning `window.init`,
`{{ Plugins.styles }}` and every upstream change to them.

An override is silent by construction: if upstream renames the partial, ours keeps being served and
nothing says so. `load_shell()` therefore checks the stock file still exists and logs a warning if
not. **If you upgrade CTFd, read that warning.**

**The shape** is ported from `jump/frontend/src/lib/components/talent/TalentPageHeader.svelte`:
transparent header in normal flow (never sticky or fixed), content centred at `max-w-5xl` (64rem,
`--epi-content-width`), wordmark left, ghost icon controls right, `h-8` boxes, `gap-1`.
`main`'s `padding-top: 3.5rem` — which existed only to clear the fixed bar — is reset, and `body`
becomes a flex column so the footer sits at the bottom instead of floating up a short page.

**Nav items are the one thing `jump` gives no precedent for**, because its talent header carries no
links at all: navigation there is card-based off one dashboard. Copying that literally was tried and
rejected — this audience is lycéens, CTFd has destinations `jump` doesn't, and "reachable by URL"
is not navigation. So the items are built from the two `jump` recipes that come closest, its
back-arrow hover and its badge:

- idle — `text-muted-foreground`, muted fill on hover
- **current page** — a flat 10% `--bs-primary` tint behind solid brand text. **Never a saturated
  fill**; that rule is the whole talent-space look and a filled pill would be the loudest block on
  the page.
- body font, never Anton. Anton stays display-only (see Typography).

**The other two overrides.** `page.html` (8 lines upstream) exists because an authored CTFd Page —
"Parcours", a subject index, the terms — had no `.jumbotron`, so it was the one surface with no band
and nothing naming it; it renders `{{ title }}` in a hero and is otherwise core's. `login.html` is
the handover from Jump, and puts `jump`'s brand panel beside a form that stays core's field for
field, so an upstream change to how CTFd authenticates is not something we track. Both are listed in
`plugins/workshop/README.md`'s portability notes, and both get the same boot-time staleness check.

**Two things in the markup are load-bearing.** Delete either and something breaks quietly:

1. `.theme-switch` wrapping an `<i class="fas ...">`. `color_mode_switcher.js` does
   `querySelector(".theme-switch i.fas")` and dereferences it **without a null check**, on
   `window.load`, before it binds the click handlers. Without that element every page throws a
   TypeError and the theme toggle stops working entirely.
2. The `Plugins.user_menu_pages` loop — the only route by which CTFd's Pages feature reaches the
   nav. "Parcours" is one of those Pages (created by `tools/sync_subject.py`), and so is anything an
   instructor adds later.

**Header icons are Font Awesome, not `jump`'s Lucide.** A deliberate deviation, forced by point 1:
the theme icon is contractually Font Awesome, and two icon sets inside one 32px cluster would look
worse than either alone. Font Awesome is already loaded by core, so this also vendors nothing.

**What the header carries**, and what left: nav is Workshop (added — it was previously reachable
only by clicking the logo), the Pages loop, Scoreboard, and Challenges **for admins only**. That
last one is the same steps as the workshop view in CTFd's own presentation, so for a participant it
was a second entry to one place, and the one that shows a subject as a grid of point values; the
instructor it was kept for is an admin here. `/challenges` stays served and reachable by URL —
this drops the link, not the route (PLAN.md §30). The right cluster is
notifications, theme, and a named account menu holding Profile, Team, Settings, Admin Panel and
Logout — six former navbar entries at `jump`'s three-control density. **Dropped**: the language
picker (all platform strings are English until a later i18n phase, per `CLAUDE.md`) and the
Users/Teams listings (a classmate roster is noise for this audience). The signed-out header shows
**no nav**, because nothing in it is reachable before signing in and `jump`'s login screen has none
either.

**The wordmark ships as two assets** (`assets/brand/EPITECH-LOGO-{BLEU,BLANC}-2025.svg`, vendored
from `jump/frontend/static/`) swapped by `[data-bs-theme]` in CSS, never one recoloured: the charte
forbids effects on the logo, and a background-image means only the version in use is fetched. An
instance that uploaded its own `ctf_logo` keeps it — the Epitech wordmark is the default, not an
override of the admin's choice.

**Page titles.** `.jumbotron` is restyled into a plain left-aligned Anton title row rather than
re-templated, so no core page template is owned. A CTFd Page renders its markdown straight into the
page container with no wrapper, so its leading `h1` gets the same treatment via
`main > .container > h1:first-child` — every core template's own `h1` is inside a jumbotron, so that
selector only ever matches an authored Page. Both carry `jump`'s accent-clip fix (`-my-1.5 py-1.5`):
`truncate` is `overflow-hidden` and condensed uppercase Anton puts `É` above the cap line, so a tight
line box slices the accent. French titles need it.

**Alignment is a contract.** `--epi-content-width` is one number for the header, every page's
top-level container, the page titles and the footer. It is checked by eye easily: the wordmark, the
page title, and the first card or table on every page all start at the same x.

**The admin panel is untouched by all of this** and that is not an accident: `themes/admin` loads
its stylesheets from a separate registry (`get_registered_admin_stylesheets()`) and never sees
`epitech-theme.css`, so its own fixed navbar and wider layout keep working. This is also why `main`
and `.container` can safely be restyled by element/class rather than behind a scoping class.

## The brand band — header + hero, and why it deviates

`jump` reserves full-bleed `epiBlue` for *hero and celebration* surfaces and says explicitly that
it is **not chrome**: *"a 250px column of pure brand blue in front of someone for eight hours is
fatiguing, which is why the sidebar is `chrome` and not blue."* Its talent header is transparent.

**We deviate: the header is blue, and the hero continues it.** The reasons, in the order they
matter:

- The fatigue argument is about a *permanent* surface. This band is ~200px at the top of a page
  that scrolls, the header is in normal flow (it always was) and leaves with it, and a workshop
  session is hours rather than a working day.
- It is not chrome in `jump`'s sense. The band carries the page's identity — title, accroche, cover
  image, progress — so it is a hero that happens to contain the nav. That is the Notion page banner
  the PO named when he said the interface was "très sobre", and he had already approved a blue
  navbar in an earlier mockup.
- Splitting them would put a white sliver between the wordmark and the page title that serves
  nothing.

**The primitive is `jump`'s `PageHero.svelte`**, ported: `--epi-blue`, `.on-dark`,
`blueprint-grid-inverse`, three half-opacity white pixel squares, two densities (comfortable and
compact). `LoginBrandPanel.svelte` is the same recipe as a column, and is what `/login` uses.

**Full-bleed is structural, never `margin-left: calc(50% - 50vw)`.** `{% block content %}` renders
straight into `<main>`, which has no padding, so a direct child of `main` is already full width. The
negative-margin trick needs a compensating `overflow-x: hidden`, and **that kills `position: sticky`
on the parts stepper** — the one thing on a long subject page that must survive scrolling. For the
same reason the band never becomes sticky: `workshop_page.js` measures the stepper against a header
that stays in flow.

**Three things the band breaks, and what they cost.** All three are blue-on-blue:

| What | Why it broke | What it takes instead |
|---|---|---|
| The current nav item | `--bs-primary` text on a `--bs-primary` tint, and the surface *is* `--bs-primary` — the label of the page you are on disappeared | white on `rgb(255 255 255 / .15)`. Same recipe, still never a saturated fill |
| The signed-out Login CTA | `.btn-primary`, whose fill Bootstrap bakes from `--bs-primary` | `jump`'s `buttonNeon` pair: `--epi-tech` fill, `--epi-blue` text. Which is what that button is anyway — the one hopeful action on the signed-out shell |
| Profile-page badges | `bg-primary` / `bg-secondary` / `text-bg-light` inside a `.jumbotron` | the same flat white tint. **Excluding those pages was the first attempt and was wrong**: whether a badge renders depends on whether that user filled in an affiliation, so one page would have had two layouts depending on its data |

Everything else in the header needed no colour rule at all — `.on-dark` carries it, which is what
promoting that to a real mechanism was for.

**`main > #ws-workshop > .container` is in the width rule** and has to be: the workshop page wraps
its band and its body in one `#ws-workshop`, so its container is a grandchild of `main`. That id is
**not** a layout hook — it is the scope root `workshop_page.js` binds to, and `updateCounters()` does
`ROOT.querySelector(".ws-overall")` behind an early return. Move the band outside it and the step
counter and progress bar silently stop updating after every solve, with no error, and the regression
suite does not catch it because it reads that counter from a fresh `GET`.

## The subject cover — the platform owns the layout, the subject owns the asset

A workshop's front page showed a title on an empty background, while every subject already carried
a one-line `project.summary` that nothing displayed and shipped a folder of screenshots. The band
now carries an accroche and a picture, and the contract is the point:

> **The subject supplies a sentence and a file. It never supplies a layout.**

No size, no crop, no alignment, no class. The crop, the frame, the type scale, the breakpoint where
the picture moves under the words and the motion rule are all here. That is what makes four subjects
arrive looking like one platform. The authoring side is `docs/CONTENT_CONVENTION.md` §3.2b.

Three levels, and only the first costs an author anything: derived (from `project.summary` and the
first image of the entrypoint document), declared per subject, declared per document. The derived
level is the floor and it is deliberate — **no subject can be blank**, so declaring a cover is an
improvement rather than a prerequisite.

**A part shows the subject's picture but not the subject's tagline.** That sentence is the front
door's promise about the whole subject; repeated over each part it says the same thing three times
and stops being read.

**GIF, not video.** The Pac-Man subject alone ships 20 GIFs for 2.4 MB, the largest 183 KB: the
pipeline handles them, they fit an offline room's budget, and a video format would add codec,
autoplay and `playsinline` for nothing. `prefers-reduced-motion` is honoured **without JS** — both
frames are in the DOM and the media query picks one, because a GIF cannot be paused.

## Motion

Tokens ported verbatim from `jump`: `--epi-ease-standard` `cubic-bezier(0.2,0,0,1)`,
`--epi-ease-emphatic` `cubic-bezier(0.2,0,0.2,1.2)`, `--epi-dur-fast/base/slow` 120/200/320ms.
**320ms is the ceiling for a state transition.** `--epi-ease-emphatic` overshoots and is for brand
reveals only.

`prefers-reduced-motion` is opted out of **once**, globally, rather than at each call site, so a new
animation cannot forget it. It sets a near-zero duration rather than `animation: none`: an animation
that never runs also never fires `animationend`, and anything that removes its own element on that
event would leak instead. The reward float does exactly that.

## Celebration — three intensities

33 solved steps in a row, each turning a chip from grey to green and doing nothing else. So:

| Moment | What happens |
|---|---|
| a step | a "+25 PTS" figure pops off the row and floats away |
| a part | confetti, over the completion block the page already renders |
| the workshop | confetti twice, and the block says it is over |

The float is `jump`'s `xp-float` keyframe ported frame for frame, count-up included; the burst is
`jump`'s `lib/actions/confetti.ts`, colours and all. `jump/DESIGN.md` caps a state transition at
320ms and then says a one-shot celebration states its own duration — which is what the 2.2s is.

**Anchored to the control that was pressed, in a fixed layer on `<body>`.** `jump` floats its XP
over the whole screen because it is celebrating a session; doing that 33 times would be a
punishment. The first version anchored it to the step's summary row instead, which is worse than
either: a step is a statement, a code block and usually a hint, so by the time somebody is typing a
validation code that row is most of a screen above the fold. Measured on a real solve, the float
landed at `top: -1479px` in a 1000px viewport. It was drawn on every correct answer and seen on
none.

Both halves of the fix are load-bearing. **To the control**, because that is where the eye already
is. **In a fixed layer**, because `fillBody()` replaces the step body's `innerHTML` — form and
button included — and `refresh()` then scrolls to whatever unlocked: a float parented to the button
is destroyed mid-animation, one parented to the page slides away with the scroll.

**The figure alone, in Anton, in `--epi-together`, lit by its own halo** — `jump`'s `XpFloat`, and
nothing else. A plate behind it and an icon beside it were both tried and both removed: either one
makes it read as a notification badge, which is the one thing a reward must not look like.

That does run the figure at 2.86:1 against the light page, under the 3:1 large-text floor. It is
`jump`'s own tradeoff and it is taken knowingly: a 2.2s celebration overlay carrying no information
the page does not already show — the counter, the bar and the step glyph all move with it — and the
halo is what separates it from the text underneath. The passing alternative is
`--epi-together-ink` (`#b5361a` in light), which reads as brick rather than as a reward. If this
ever has to carry information, that token is the one to switch to.

**The reward and the move to the next step are a sequence, not a moment.** They used to fire
together — the float popped while `refresh()` smooth-scrolled the page to whatever had just
unlocked — and they diluted each other: two things asking for attention at once, with the float,
which is fixed, hanging still while the whole page slid underneath it. It read as a system overlay
rather than as a consequence of the click. You act, you are paid, *then* the page moves.

Only the viewport move waits. Counters, chips and step bodies land immediately: they corroborate
the reward instead of competing with it, and they are not what steals the moment.

**How long it waits is the celebration's answer, not the page's.** `wsCelebrate.step()` returns the
number of milliseconds its moment needs — the count-up's own duration, so the figure has reached
its final value before anything else moves, or a shorter "has it been seen" when there is no number
to count or motion is reduced. Hard-coding that on the caller's side is how a pause and the
animation it is waiting for drift apart.

Two mechanics worth not rediscovering:

- **The hook is `submit()` and only `submit()`.** It is the one place where `correct` and
  `already_solved` are distinguishable, and the only place that knows *which* step — `refresh()`'s
  loop flips every step whose state moved, and one solve can move several.
- **The step's float fires before the refresh, not after.** `refresh()` opens the newly unlocked
  step and scrolls to it, so a float created afterwards pops on a row that has left the screen.
  That was the first version and it was invisible.

The confetti canvas is ours, promoted with `translateZ(0)` at a `z-index` **above the runtime
pane's 1030**: `canvas-confetti`'s default canvas composites behind an iframe, so a celebration
fired while the pane is open would be invisible — which is exactly when it matters.

`cover.mascot` puts the subject's own sprite in the float when it declares one. That is the one
thing here with no `jump` precedent.

**Reduced motion keeps the reward, and loses only the movement.** The global near-zero rule above
would shorten the float to nothing, i.e. delete the single confirmation a solve gives. It carries
its own `prefers-reduced-motion` block that keeps the full 2.2s and swaps the rise for a fade. The
confetti, which is decoration rather than confirmation, is skipped entirely.

The scroll to the next step has to read the preference **in JavaScript**. An explicit
`behavior: "smooth"` on `window.scrollTo` wins over the stylesheet's
`scroll-behavior: auto !important` — the CSS property is only consulted when the JS behaviour is
`"auto"` — so the global opt-out does not reach it, and somebody who asked for no motion was still
getting the one animation on the page that moves their whole viewport.

## Everything that can be pressed says so

`:active { transform: scale(0.97) }` on `.btn`, platform-wide, plus the same on chips, rating
thumbs and the folds. Bootstrap's `:active` only repaints the fill, which on a saturated brand
colour is close to invisible — and on a touch screen there was no hover to have said it first.

Two rules that go with it:

- **Only `transform` is transitioned, never `all`.** The fill and border already carry Bootstrap's
  own transition, and animating layout properties on hover costs a relayout per frame.
- **Hover effects sit behind `@media (hover: hover) and (pointer: fine)`.** A tap on a touch screen
  fires hover and leaves it stuck.

**Every fold on the participant path is drawn as a control, and one rule does it.** Not the three
we render — the part introduction, the author's short version, a hint — but every `<details>` on
the page, including the ones a *subject author* writes in markdown: the Pac-Man subject opens part
2 with a `<details><summary><b>Glossaire de la partie 2</b></summary>`, and left to the browser
that is a line of text with a small triangle, which reads as a rendering fault rather than as
something to press.

So the look is keyed off the element, never off a class an author would have to know about:

```css
:is(#ws-workshop, .challenge-desc, .challenge-hints) summary:not(.ws-step-summary)
```

`.ws-step-summary` is the one exclusion — a step is a full-width row with its own state glyph and
its own points, not a pill. The only per-fold addition anywhere is the hint's lightbulb, because a
hint is the fold you open to ask for help. **Adding a fourth kind of fold should need no CSS at
all**; if it does, that is the bug.

A `<details>` opened by hand reveals its content over `--epi-dur-base`; the class that does it is
set on a real `toggle`, never off `[open]`, so the folds already open at load do not all animate at
once.

A wrong answer shakes the field for 260ms and reselects it. A line of red text is easy to miss when
you are looking at the keyboard.

## The steps stepper sticks, the parts stepper does not

The part strip used to be the sticky one. It is the wrong bar to keep in front of somebody: a
subject has two or three parts, so it says the same thing for twenty minutes and only moves a
counter. What advances while they work is the step.

So each part's `.ws-stepper-steps` is `position: sticky` **inside its own section** — it leads
while you are in that part and is pushed out by the next one, which is what a stepper is for. It
lifts off the page once pinned, the current chip is scrolled onto the strip on arrival and whenever
a solve moves it, and a chip that gets checked off pops once.

`position: sticky` gives no state to style, and **the usual `IntersectionObserver` trick does not
apply here**: it calls an element "not fully intersecting" when the element is off-screen entirely,
so with one bar per part every bar below the fold claims to be stuck. Measured — at scroll 0 it
reported stuck on a bar 1204px down the page. One rect read per bar, coalesced into a frame, on a
passive scroll listener.

`stickyOffset()` measures the strip **of the part the anchor lands in**. Measuring a different
part's leaves the target under the real one.

## The reading surface

The page a participant actually works in is a document, and authored markdown had no treatment at
all — one four-line image cap. Ported from `jump`'s `.prose` block, whose own comment calls it
"Notion-like with Epitech branding": table borders with a quiet header, a 3px blockquote rule,
tinted inline code, links that fill in their underline on hover.

- **Headings inside authored content stay in the body face.** Anton is display-only, and an
  uppercase condensed `### Mise en application` would outrank the part title above it.
- **Prose gets a 68ch measure and nothing else does.** At the full 64rem column a line is ~110
  characters. Code blocks, tables and screenshots keep the full width: those are scanned, not read.
- **Screenshots get a frame and a 26rem height cap.** The mat is `--epi-grey-900` in both themes —
  subject media is pixel art from a game that only exists on black, and on a light ground every
  screenshot wears a halo, the same reason code blocks are already always-dark. The cap is because
  the Pac-Man intro shot renders 757px tall and pushed the first step below the fold.

**This deviates from `jump`**, which says photography is rectangles with no radius and no shadow.
That rule was written about photographs of people at campus events; this is a framed screen.

## Locked steps are quiet, not hidden

Arriving at a part at 0% meant eleven identical full-size cards, each with a padlock and a price in
points: the first impression of every subject was a wall saying "you may not". They are a quiet list
now — no card, no shadow, no points — so the one step that can be started is the only thing that
looks like a thing to do.

**Not folded away.** The syllabus is not a secret (`PLAN.md` §13) and seeing what is coming is the
point; a fold would put a click between a participant and the shape of the work.

## State glyphs come from CSS, once

The five-state icon table used to exist in four places: two templates, `workshop_page.js`, and the
`::before` rules. It is CSS only now; the templates emit an empty span and the JS only swaps a
class. They are Font Awesome, which core already loads, so this vendors nothing — and it is how the
four-colour 🔒 and 📄 emoji left a two-colour system.

**Only the solid face ships** (`fa-solid-900.woff2`; the theme bundles no regular file), so "not
started" is drawn as a ring rather than reached for as a hollow-circle glyph, which would silently
fall back to the wrong font.

State is still icon AND word AND colour, never colour alone: the `visually-hidden` state word and
the title attribute are untouched.

## Bootstrap limitations — read this before touching a color

A root-variable remap does **not** reach everything. Bootstrap bakes several component classes
with literal hex into their own scoped `--bs-btn-*` (etc.) vars at build time, so they never read
`--bs-primary` at runtime. If a future change to `--bs-primary` doesn't move a button, this is
why — the fix is a selector-level override, not a bigger variable.

Already handled in `epitech-theme.css`, and the reason each needed its own block:

- `.btn-primary`, `.btn-dark` (also every **unsolved challenge-board card** —
  `.challenge-button.btn-dark`), `.btn-success`, `.btn-danger`, `.btn-warning`,
  `.btn-outline-primary` — literal `--bs-btn-bg`/`-border-color`/`-hover-*`/`-active-*`.
- `.form-control:focus` / `.form-select:focus` — Bootstrap's alpha box-shadow ring, replaced with
  a solid outline (same reasoning `jump` gives: a box-shadow ring doesn't survive an
  `overflow-hidden` ancestor — the challenge modal, here — an outline does).
- `.form-check-input:checked` — literal, used by quiz radio/checkbox options.
- **`input, select { padding: .6rem !important }`** — CTFd's own rule, not Bootstrap's, and it
  makes every field on the platform 45.19px tall while `.btn` keeps Bootstrap's `.375rem` and comes
  out at 38. Harmless for as long as the two are on separate rows; the answer row puts a field and
  its Submit side by side, so the button takes the same `padding-block: 0.6rem` there. There is no
  way to land on the same height without matching an `!important` we do not own.
- `.dropdown-menu`, `.list-group`, `.pagination`, `.progress-bar` — literal active-state
  backgrounds.
- `.table-primary` — literal computed tint, not var-based.
- `.challenge-button` (the challenge board's cards) — core ships them as `btn btn-dark` and then
  pins `border: none`, a dated 15px glow, and a hardcoded `#29c830` solved fill inside per-theme
  blocks (`[data-bs-theme=X] .challenge-button`, `_challenge.scss`), all independent of
  `--bs-success`. Every property has to be overridden **at that same `0,2,0` specificity** or it
  loses regardless of source order. They are now talent-space cards: `--epi-card` surface, real
  border, `--epi-shadow-raised`, and a flat `--bs-success` tint for solved rather than a saturated
  fill.

  Known limitation, unchanged from stock: solved is signalled by **colour alone** on that board.
  Fixing it needs a glyph in the markup, which lives in a core template this plugin deliberately
  does not own. The workshop view — the page a participant actually works in — carries icon + text
  + colour instead.

**Not fixable in CSS at all**: the scoreboard/category charts are Chart.js-via-ECharts,
`<canvas>`-rendered, colored per-name by a JS `colorHash()` — genuinely CSS-immune. Left at
Bootstrap default. Fixing it means a `CTFd/` JS edit, out of scope for this pass and for the whole
"never edit `CTFd/`" rule as it stands today.

## Code highlighting uses `.ll-*`, not `.hljs-*`

CTFd core ships `lolight`, not highlight.js. Classes are `.ll-key` / `.ll-str` / `.ll-com` /
`.ll-num` / `.ll-nam` / `.ll-rex` / `.ll-pct`, themed via `[data-bs-theme="dark"] .ll-*`. **Any
future port of a `jump`-style `.hljs-*` override is a silent no-op here** — check
`CTFd/CTFd/themes/core/assets/scss/includes/utils/_lolight.scss` for the live class list before
porting a syntax theme from anywhere else.

Code blocks (`pre`/`pre code`) render on a forced-dark background in **both** themes — the same
move `jump` makes for the same reason: the neon `--epi-tech` green is only legible on a dark
surface (1.33:1 on white per `jump`'s own contrast note), so code gets one always-dark surface
instead of a palette that has to switch with the page.

### The plugin carries its own copy of lolight

The core theme highlights `pre code` **once**, on `DOMContentLoaded`
(`themes/core/assets/js/theme/highlight.js`), and keeps `lolight` module-local. The workshop page
fills a step's body in place after a solve, so every code block from the second step of a subject
onwards arrived as plain text — measured, 0 `.ll-*` spans in each body the page injected. That is
most of a workshop.

Re-exporting `lolight` from the core bundle would be a core edit, so `tools/build_vendor.sh`
vendors the same version the theme depends on (2.6 KB, UMD, global `lolight`), and
`workshop_page.js` re-tokenizes from `textContent`. Two things to know about that:

- **It always re-tokenizes, it never skips blocks that already have spans.** `lolight` rebuilds
  from `textContent` too, so the pass is idempotent, and that is what makes a block highlighted by
  the core at load and a block fetched after a solve come out identical.
- **It runs on `DOMContentLoaded` and again on `load`.** The core's own pass also runs on
  `DOMContentLoaded` and the order between the two handlers is not ours; if the core lands second
  it rebuilds the block and drops the extra tagging below. `load` is strictly after every
  `DOMContentLoaded` handler.
- Its own auto-run targets `.lolight`, a class nothing here uses, so loading it twice highlights
  nothing twice.

### Seven classes, so all seven have to differ

`lolight` is a language-agnostic tokenizer with exactly seven classes, and the palette does all the
separating. Two of them used to collide, and both are worth stating as rules:

- **`.ll-key` must not equal `.ll-nam`.** They were both the brand green, so a keyword and a
  variable name were one colour — no keyword highlighting at all.
- **`.ll-pct` must not equal the surface's ink.** It was `#f1f2f6` on a `#0b0e1a` block, i.e. the
  body colour of that very surface, so every operator was the same ink as the text around it.
  `pos.x > ghost.x` read as one grey run, on a subject whose subject *is* comparisons. Operators
  take the warm accent now, the third colour of the charte.

### The two gaps `lolight` leaves, and where they are patched

Its ruleset is one fixed regex shared by every language. `workshop_page.js` repairs two things
after tokenizing, which is far less brittle than shipping a second highlighter:

- **Missing keywords.** `local` is on nearly every line of beginner Lua; Python's `None`, `True`
  and `False` miss because the list carries `null`, `true`, `false` and is case-sensitive.
- **Line comments.** It only knows `//` and `#`, so `-- deplace le fantome` tokenized as two
  operators followed by live code — on a subject that has a step explaining that `--` is a note and
  not code. The fold is keyed off the `language-*` class the markdown renderer puts on `<code>`, so
  it never fires on a shell block where `--force` is a flag.

## `.on-dark` — a surface that is dark whatever the theme is

This started as "reused twice, not a system": code blocks and the Parcours graph, with a note
saying not to build a mechanism for two call sites. There are five now — the app header, the page
hero, the login brand panel, code blocks and the graph — which is exactly the condition that note
set for revisiting it. So `jump`'s own mechanism is ported (`layout.css`): a class that re-points
the ink tokens, because the ink shades are darkened until they clear AA on a *light* surface, which
is precisely what makes them illegible on a dark one.

**Every derived role is restated inside the class, and has to be.** A custom property holding
`var(--other)` is substituted on the element that *declares* it, not on the element that reads it,
so re-pointing an ink inside `.on-dark` never reaches a role declared on `:root`.
`[data-bs-theme="dark"]` gets away with it because it lands on the root itself; `.on-dark` is a
descendant and does not. `jump` documents the same trap and the same fix.

Two consequences that are decisions, not side effects:

- **`--bs-body-bg` is in the list.** The notification badge paints `color: var(--bs-body-bg)` over
  a `--bs-danger` fill. Invert the danger without inverting the ground and the count is light
  `#f6f7fb` on salmon `#ff9878`, which nobody can read.
- **The account dropdown goes dark in both themes.** Bootstrap derives `--bs-dropdown-bg` from
  `--bs-body-bg`, and the menu is a DOM descendant of the header, so inverting one inverts the
  other. A menu opened from a dark bar being a dark menu is coherent; the alternative is scoping
  `.on-dark` element by element inside the header, which costs more than it buys.

`.epi-hero` and a core `.jumbotron` earn the same tokens by selector rather than by class, because
no plugin can add a class to a core template.

## The "Parcours" graph

`plugins/workshop/assets/workshop.css` (`.ws-parcours-*`, `.ws-lg-*`) and
`plugins/workshop/assets/graph.js` (literal SVG fill/stroke colors) **must stay in sync** — the
same solved/open/locked/current-path encoding, recolored to `--epi-tech`(-ink) / `--epi-blue` /
`--epi-grey-200`/`--epi-grey-900` instead of the stock dark-arcade palette it shipped with. A
future color change to one needs the same change in the other; neither reads the other's values.

## Radius and elevation — the talent space's system

`--bs-border-radius-sm/-/-lg/-xl` are `0.5/0.75/1/1rem`, `-xxl` is `1.5rem`, and `.card` /
`.modal-content` / `.challenge-button` carry a real shadow (`--epi-shadow-raised`) in light mode.
This is `jump`'s **talent-space** system (`.camper-layout`/`.talent-surface` in `layout.css`) —
rounded-xl elevated cards, not `jump`'s flat `0.125rem` staff/charte default.

The first pass used the flat charte default, reasoning "one audience, no multi-space system to
justify a two-tier radius." That was wrong, corrected by direct feedback: the actual target isn't
`jump`'s staff tooling, it's specifically **`jump`'s talent space** — the participant-facing side
of `jump`, which is what this codebase's one audience (a workshop participant) actually maps to.
So it takes the soft/raised half of `jump`'s two-tier system, not the flat/square half, even though
there's only one tier here.

`--bs-border-radius-pill` is left alone (badges/pills stay round in either system). Shadow is
`none` in dark mode — a drop shadow reads as a smudge on a dark surface; `jump` does the same
(border carries the "raised" cue there instead). Not exhaustively audited for every Bootstrap
component that bakes radius as a literal the way color does — verify visually (buttons, cards,
inputs, badges) before trusting full variable coverage on a new component.

## Brand primitives — what a token remap alone doesn't buy

Recoloring Bootstrap's own components (buttons, cards, tables) makes the page *on-brand-colored*,
not *on-brand*. What actually reads as "`jump`'s talent space" is a handful of concrete visual
patterns, ported here as reusable classes rather than invented from scratch:

- **`.epi-blueprint-grid`** — a faint engineering-grid texture (`--epi-blue` at 6% light / white at
  4.5% dark, 48px cells), `jump`'s `BrandBackdrop`. Never a blur or a gradient glow —
  `BrandBackdrop`'s own comment in `jump` is explicit that the charte rules out both; a texture is a
  surface, a glow is a light source, and the charte wants the former.

  **Still unused as a page backdrop, and the rule stands**: in `jump` `BrandBackdrop` appears *only*
  on full-screen ceremony pages (onboarding, welcome, charte), never on the everyday shell. Keep it
  for a completion screen, which is what it is for.

  **The hero's grid is a different thing and is not this.** `.epi-hero` paints
  `blueprint-grid-inverse` — white at 4.5%, always, because the hero is dark in both themes —
  *inside* a blue block, which is exactly what `jump`'s own `PageHero` and `LoginBrandPanel` do.
  The distinction is the one that matters: a texture inside a brand surface is a component recipe;
  a texture behind a whole page is a ceremony.
- **`.epi-title-cursor`** — a trailing `_` glued to a heading with no whitespace, in the success
  "ink" color, `jump`'s `TitleCursor.svelte`. Used on the workshop title and each part title.
- **Tinted pill badges, not outlines** — `.ws-chip` state pills, the `.ws-overall-badge` step
  counter, the `.ws-next-doc-badge` completion icon: a `color-mix()` tint fill at ~12-16% opacity
  plus solid text, `jump`'s recipe for "a badge that reads as brand-colored without being a
  saturated block." A thin gray-or-colored outline (the first pass's approach) reads as unstyled
  Bootstrap regardless of which hex it borrows.
- **A bold number, not a fill-meter, for the hero counter** — `.ws-overall-num` is a large figure
  in a tinted badge (`jump`'s XP-badge treatment, reused for a step count instead of experience
  points), next to a thin rounded track, not a wide Bootstrap `.progress` bar. The old plain
  `.ws-overall-bar` still exists as a secondary, quieter variant for a card's or a subject's own
  progress — not the hero figure.
- **A completion moment, not a bare sentence** — finishing every step of a document renders
  `.ws-next-doc`: an icon in a tinted circle, an Anton headline for the terminal state, styled
  after `jump`'s removed "dynamic activity" step-through feature (`(talent)/[activityId]/`,
  removed ~470 commits back in `jump`'s history) — specifically its completion screen
  (`StepValidationBlock`, trophy + `font-heading text-4xl uppercase` "Mission Terminée !"), the
  concrete reference this correction is built from.

None of this is invented: every recipe above is copied from a real `jump` talent-space component
or from that removed step-through feature, not approximated from memory of "what feels like jump."

## What this deliberately isn't

- **Not enforced.** No `lint:design`, no contract test. `jump`'s version has both because it has
  many contributors and an agent-editable component library; this is one CSS file behind one
  registration call, and the cost of a test harness here would outweigh what it catches. If drift
  becomes a real problem, revisit — don't build the enforcement speculatively.
- **Not a copy of `jump/DESIGN.md`.** That file documents concepts this codebase doesn't have:
  per-space skins, a component library, staff-table density rules. Copying its structure here
  would describe things that don't exist and rot the moment `jump` changes something CTFd never
  had in the first place.
- **Not per-subject *styling*.** Every instance shares one stylesheet, and a subject cannot ship
  CSS, a class or a size. What a subject *can* now vary is content in fixed slots — an accroche, a
  cover image, a mascot (`docs/CONTENT_CONVENTION.md` §3.2b). That is the opposite of per-subject
  customization and the reason it is safe: four subjects fill the same layout rather than inventing
  four.

## The deviations from `jump`, in one place

Each one is a `jump` rule that failed a job this platform actually has. Nothing here is an
oversight, and nothing here should be "fixed" back without reading the reason.

| Deviation | `jump` says | This does | Why |
|---|---|---|---|
| Brand blue in the header | full-bleed `epiBlue` is for hero and celebration surfaces, never chrome | header and hero are one blue band | the fatigue argument is about a permanent surface; this is ~200px on a page that scrolls, on a header that was never sticky. And the band carries page identity, so it is a hero containing the nav |
| The blueprint grid in a page band | `BrandBackdrop` only on full-screen ceremony pages | `blueprint-grid-inverse` inside the hero | the rule is about a page *backdrop*; `jump`'s own `PageHero` and `LoginBrandPanel` put the same texture inside a brand block |
| Framed, matted media | photography is rectangles: no rounded corners, no shadow | subject screenshots get a dark mat, a border and a radius | that rule was written about photographs of people; subject media is pixel art that only exists on black and wears a halo on any lighter ground |
| A mascot in the reward float | no precedent at all | `cover.mascot`, the subject's own sprite | the art already ships in every subject repo, so it costs an author one line; the one genuinely new thing here |
| A glow on the reward float and the end-of-part block | no gradient / glow / blur on everyday surfaces | the float carries `jump`'s own `--epi-together` halo, the block a soft `--epi-tech` one | the rule is about *everyday* surfaces and it still holds for every one of them. These two are the celebration surfaces the same paragraph exempts, they exist for 2.2s and once per part respectively, and the float's halo is `jump`'s, not an invention |

**What is deliberately NOT deviated from**: the palette (no fifth hue), the ink rule, Anton as
display-only, no gradient / glow / blur on everyday surfaces, 320ms as the ceiling for a state
transition. Those are what make this read as Epitech next to Jump, which is the whole objective.
