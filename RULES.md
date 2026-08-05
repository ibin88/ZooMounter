# The rule registry

Every engineering claim ZooMounter makes lives in
[`zoomounter/data/rules.toml`](zoomounter/data/rules.toml), with where it came
from and how far it can be trusted. Python evaluates rules; it does not own
them.

This document is how you add one.

---

## Why this exists

This project shipped four bugs past a green test suite and a passing verifier.
Not one was a wrong calculation. Every one was a wrong or absent *number*:

- A NEMA square bolt spacing fed to a circular-pattern function — 6.4 mm out on
  every hole, every plate unusable.
- A single axial limit of 67 N applied to every motor, when it was a NEMA 23
  figure. A NEMA 17 passed silently at many times its own rating.
- Two vendors publishing "15" for the same quantity in different units, a 4.45×
  disagreement.
- A radial rating measured at 20 mm compared against a load applied at 15 mm,
  because the distance never travelled with the number.

The verifier caught none of them, and could not have. Verification compares the
generated part against the spec table — so when the spec table is wrong, a
wrong part passes every check.

**The central finding of this project is that an AI-generated number and a
verified one are indistinguishable once written down.** Both are plausible.
Both survive review. Both pass tests written from the same source.

You cannot fix that with more tests. You fix it by making provenance a
*required structural field*, so the difference is carried by the data rather
than by whoever happened to read the diff.

---

## The `status` field

Every rule declares one. This is the field that matters.

| Status | Meaning |
|---|---|
| `vendor-datasheet` | A manufacturer published it for a specific part. |
| `standard` | A published standard (ISO, NEMA) states it. |
| `derived` | We reasoned it out. Defensible, but **ours** — so it is labelled as ours rather than borrowing the authority of the two above. |
| `ai-proposed-unverified` | An AI suggested it and nobody has checked it. **Quarantined.** |
| `verified-against-physical` | Someone built it and measured it. |

### The quarantine is enforced, not advisory

`ai-proposed-unverified` rules never govern a generated part:

- `rules.governing_rules()` excludes them by construction.
- `rules.assert_no_unverified_rules_govern()` fails the test suite if one ever
  reaches a real part.
- `tests/test_rules.py` plants a fake quarantined rule to prove the guard
  actually bites, rather than passing because nothing is quarantined today.

This is what makes AI collaboration safe here rather than forbidden. A proposal
can sit in the registry, visible and labelled, until somebody checks it against
reality and promotes it. What it cannot do is quietly become part of the spec
because it was written convincingly.

**If you are an AI agent working in this repo: your rules go in as
`ai-proposed-unverified`. You do not get to promote your own status.** Promotion
requires a physical part or a citation you did not write.

---

## Adding a rule

Add a `[[rule]]` block to `zoomounter/data/rules.toml`:

```toml
[[rule]]
id         = "shaft_limit"
statement  = "A load applied at the shaft must not exceed the component's published shaft rating."
applies_to = { kind = "motor" }
source     = "Per-component figures in data/mounts.toml, each carrying its own datasheet citation."
severity   = "loud-warn"
status     = "vendor-datasheet"
remedy     = "Support the shaft in its own bearing and drive through a coupling."
```

### Required fields

- **`id`** — stable identifier. `Check.code` must match a rule id, so any
  message a user sees traces back to a claim with a source.
- **`statement`** — the claim, one sentence, in engineering terms.
- **`source`** — where it came from.
- **`severity`** — `pass` / `info` / `warn` / `loud-warn`.
- **`status`** — from the table above.

### Conditional and optional

- **`remedy`** — required for `warn` and `loud-warn`. A warning the user cannot
  act on is noise, and the loader rejects one.
- **`applies_to`** — which components it governs. `{}` means everything. Valid
  keys are `kind` and `process`; anything else is rejected, because an
  `applies_to` naming a field that does not exist would match nothing, which is
  the quietest possible way for a safety rule to stop firing.
- **`evaluated`** — set `false` for a declared limitation with no evaluator.

### What the loader rejects

A malformed registry raises `CatalogueError` at import — the tool refuses to
start rather than run on rules it could not check.

- Missing any required field.
- A duplicate `id`.
- An unknown `severity`, `status`, or `applies_to` key.
- A `warn`/`loud-warn` rule with no `remedy`.
- **A `source` that cites one of our own documents.** Provenance that points
  back at this project is not evidence. This rule caught its own author while
  this file was being written — a rule whose `source` *quoted* the offending
  path as an example of the defect was rejected for containing it.

---

## Declared limitations

Rules with `evaluated = false` are things ZooMounter explicitly does **not**
check. They carry the same provenance fields as any other rule and are printed
in every inspection report.

Recording them is not padding. An unmodelled load case that nobody mentions is
indistinguishable from one that passed. Currently declared:

- **Reaction torque** — present whenever the motor runs, at zero external load,
  reacted as shear in the bolt pattern. A NEMA 23 at 1.9 N·m across its 47.14 mm
  pattern puts roughly 28 N on each bolt, fully reversing on a bidirectional
  axis.
- **Fatigue** — endurance limits sit well below yield, and aluminium has no true
  endurance limit at all.
- **Impact** — a hard stop can exceed the nominal running load by an order of
  magnitude.
- **Belt pretension** — loads the shaft at zero torque, before any useful work.

---

## `applies_to`: domain awareness as data

A rule declares the component class it governs. A component that matches no load
rules gets told what actually governs it, instead of receiving a number the tool
has no model for.

This is the honest handling of the board and panel mounts the catalogue used to
carry. A Raspberry Pi plate was being sized against a fabricated 15 mm shaft
offset because the loader's default filled in a field the row never set. The fix
is not a better default — it is that no shaft rule applies to a board, and the
tool should say so rather than produce a thickness.

---

## Keeping the registry and the code honest

`tests/test_rules.py` enforces the join:

- Every `Check.code` emitted anywhere in the domain layer names a declared rule.
- A check's level never exceeds its rule's declared severity — a `LOUD WARN`
  reaching the user from a rule declared `info` means the registry no longer
  describes the code.

Without those, `rules.toml` is just a second place to write things down, and the
two drift. That drift is the failure mode the registry exists to end.

---

## Where this goes next

### 1. The registry has never met reality

As of 2026-08-05, all 29 rules break down like this:

| Status | Count |
|---|---|
| `derived` — our own reasoning | 18 |
| `standard` — ISO, NEMA | 7 |
| `vendor-datasheet` | 4 |
| **`verified-against-physical`** | **0** |

Nothing here has ever been checked against a part that exists. Most of it is
reasoning we did, correctly labelled as ours — which is the honest state, not a
good one.

**This is the highest-value work remaining, and it is not more rules.** It is
building one bracket, loading it, and promoting the handful of rules that
survive. A registry that tracks its own epistemic state and reports it
unflatteringly is doing the thing this project claims to do; a registry that
never moves a rule to `verified-against-physical` is a well-organised opinion.

### 2. The tool gets smarter by being told more about the situation

Not by being given more geometry options. That distinction is the whole lesson
of `application.py`.

ZooMounter shipped a flag for the bearing topology and nearly shipped a question
about whether to build one plate or two. Both were the same error: handing the
user a choice between *shapes* when the shape is a consequence of the
*application*. Replacing them with `ApplicationContext` did not add a feature —
it moved a decision to where the information actually lives.

The pattern generalises. Every new context input unlocks derivations the tool
currently has to decline:

| Input it does not have | What it could then decide |
|---|---|
| duty cycle and shaft speed | L10 bearing life instead of a static C0 check |
| ambient and motor case temperature | whether a printed part is viable at all |
| frame stiffness, how the motor is otherwise supported | whether it needs its own base plate |
| peak acceleration | whether a moving mount's inertial load governs |

The base-plate question is the live example. ZooMounter declines it today
because it is not given what the answer depends on. **Declining is the correct
output.** Do not resolve it by adding a flag — that is how it was wrong before.

### 3. LLM at the edges, registry at the core

The obvious next thought is to let a language model make these decisions
directly. Don't.

This project's central finding is that **a plausible number and a correct number
are indistinguishable once written down**. Two bugs survived a green test suite
*and* a passing verifier for exactly that reason. A language model is a machine
for producing plausible numbers. Putting one in the decision seat does not solve
that failure mode — it industrialises it, generating confident specs faster than
anyone can check them, while the verifier keeps passing them because the spec is
what is wrong.

The correct architecture is already in this repo, one layer down. **The Agent API
is a language model, and it works here because it is constrained to coordinates
we computed and verified against them.** It does the drawing, not the deciding —
and given exact numbers it lands them sub-0.001 mm. Apply the same relationship
one layer up.

Three jobs a model genuinely earns:

1. **Input interpretation.** *"It's on a moving gantry near the operator"* →
   `ApplicationContext(service="moving", workspace="shared")`. Structured,
   inspectable, and a wrong reading is visible immediately.
2. **Rule drafting.** Read a datasheet, propose a catalogue row or a rule —
   arriving as `ai-proposed-unverified` by construction, which is what the
   quarantine is for.
3. **Explanation.** Turn a rule chain into prose for someone who did not write it.

None of those is the decision. All three are checkable.

**The test for which layer something belongs in:** *can it be wrong silently?*
If yes, it is a rule. Numbers, limits, thresholds, decisions — anything where
being wrong looks identical to being right until a part is scrap.

### 4. What must never move into the model

Stated as a prohibition, because it is the change a future contributor is most
likely to make and least likely to notice the cost of:

- **The decision.** A conclusion whose derivation is invisible cannot be
  audited, regression-tested, or corrected once for everyone. A rule chain can
  be pinned by a test; a judgement cannot.
- **The numbers.** Every figure needs a source and its measurement conditions.
  A model will supply both, convincingly, without either being true.
- **The provenance.** `status` is the membrane between the two halves of this
  architecture. A model may propose a rule; it does not get to promote its own
  status. That requires a citation it did not write, or a part someone built.

Reproducibility is the practical version of the same point. Generative output is
not repeatable, which is why the determinism check is still open in `HANDOFF.md`
— and why nothing that has to be identical next Tuesday should depend on a
sampled token.
