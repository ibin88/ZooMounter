# Notes for Zoo — findings from building ZooMounter

Written for the Zoo team, from two days of building against the Engine, Agent
and File Format APIs with no prior exposure to the platform. Everything below
is something that cost real debugging time or that I couldn't answer from the
docs and had to determine empirically. Ordered roughly by how much time it
cost.

Every claim here is reproducible from this repo. Where I say "confirmed by",
that's an actual response I got, not an inference.

---

## 1. `POST /ai/text-to-cad/{output_format}` returns KCL source, not a file

This was the single biggest surprise, and it shaped the whole architecture.

The endpoint takes an `output_format` path parameter (`step`, `stl`, `obj`,
`gltf`...), and the `TextToCad` schema documents an `outputs` field described
as *"The output of the model in the given file format the user requested,
base64 encoded."* That reads unambiguously as "you get a file back in the
format you named."

In practice, polling `GET /user/text-to-cad/{id}` to `status: "completed"`
returned a response with **no `outputs` key at all**. The geometry came back
in the `code` field as KCL source. Confirmed on every generation I ran —
the returned keys were consistently:

```
type, id, created_at, started_at, completed_at, user_id, status,
updated_at, conversation_id, prompt, output_format, model_version,
model, feedback, code
```

So `output_format: "step"` was accepted, echoed back, and then no STEP was
produced. Getting an actual file required a second, separate step (see #2).

**Suggestions:** either populate `outputs`, or document that this endpoint is
KCL-first and that `output_format` describes the *intended* target rather than
the response payload. If `outputs` is populated only under conditions I didn't
hit, the conditions are worth stating. As it stands, the field's description
promises something the response didn't deliver, which is the kind of thing
that sends a new developer looking for their own bug.

---

## 2. Executing KCL into a file needs the websocket Engine API — a REST path would help a lot

Given KCL source and wanting a STEP file, there's no REST endpoint that does
it. `/file/conversion` converts *between existing formats*, and takes file
bytes, not KCL. Executing KCL means `/ws/modeling/commands` — a real-time
websocket protocol.

For a batch tool that just wants "KCL in, STEP out, once," implementing a
websocket client against a real-time modelling protocol is a large amount of
work for a fundamentally synchronous operation. I ended up shelling out to
your own CLI (`zoo kcl export`), which wraps it.

That works well, and I'd recommend it to others — but it means my Python tool
has a hard dependency on a separately-installed ~96MB binary, which is an
awkward thing to ask users of a `pip install`-able package to do.

**Suggestion:** a synchronous `POST /kcl/export?output_format=step` taking KCL
in the body would remove the websocket requirement for the large class of
batch/CI/scripting use cases that don't need interactive modelling. This is
the single change that would most simplify tools like this one.

**Related, minor:** `zoo kcl export` requires the output directory to already
exist and errors with `output directory ... does not exist or is not a
directory` rather than creating it. Easy to work around once you know; a
one-line fix or a clearer message would save the discovery.

---

## 3. Two separate credit pools, only one of which is documented

The pricing page describes "$10 of free API calls per month" and
pay-as-you-go at $0.0083/second. What `GET /user/payment/balance` actually
returns is two independent balances:

```json
{
  "stable_api_credits_remaining_monetary_value": 4975.74,
  "stable_api_credits_remaining": 599487,
  "monthly_api_credits_remaining_monetary_value": 0.0,
  "monthly_api_credits_remaining": 0
}
```

I had exhausted the $10 monthly allowance without noticing, because calls kept
succeeding — funded by the "stable" pool, which I have no documentation for
and didn't know existed. I only found it by querying the endpoint directly
while trying to work out how many generations I had left.

I'd been budgeting my work on the assumption of a hard ~$10 ceiling. That
assumption was wrong in a way that materially changed my plans, and nothing in
the docs or the app told me so.

**Suggestions:** document what "stable" credits are, how they're granted
(makeathon participation? new accounts?), whether they expire, and the order
in which the two pools are consumed. Surfacing both balances in the app would
be even better — the numbers exist, they're just not visible where you'd look.

---

## 4. `allow_pay_as_you_go` defaults to `false` and isn't mentioned anywhere

`GET /user` returns `"allow_pay_as_you_go": false` on a new account. I couldn't
find this documented. It's genuinely good default behaviour — it means you
can't accidentally run up a bill — but a developer planning around the
pay-as-you-go pricing has no way to know from the docs that there's a flag
gating it, or where to flip it.

**Suggestion:** mention it on the pricing page, and have the API return a
clear, specific error when a call is refused for this reason.

---

## 5. Docs pages are JS-rendered, so they're invisible to non-browser tooling

`zoo.dev/api-pricing`, `zoo.dev/docs/developer-tools/api/*` and the pricing
tables all render client-side. Fetching them returns shells with `Loading...`
where the content should be.

This matters more than it used to: it's not just curl and scrapers, it's every
AI coding assistant a developer might use to help them build against your API.
Anything that fetches a URL rather than driving a browser sees nothing. In
practice I got my answers from the OpenAPI spec at `https://api.zoo.dev`
directly, which was excellent — complete, well-typed, genuinely pleasant to
work from.

**Suggestion:** server-render the docs, or offer a plain-text/markdown mirror.
Pointing people at the OpenAPI spec more prominently would also help; it was
more useful than the prose docs and I found it late.

---

## 6. Small things

- **`GET /user/text-to-cad/{id}` doesn't include a progress indication.** The
  status goes `queued` → `in_progress` → `completed`, with generations taking
  1–3 minutes. Any hint of expected duration or percentage would let tools
  show a real progress bar instead of a spinner. (I poll every 10s and show
  elapsed time, which is the best I can do.)

- **The docs URL structure has near-duplicates** — `zoo.dev/docs/api/...` and
  `zoo.dev/docs/developer-tools/api/...` both exist with overlapping content,
  and some paths 404 under one prefix but resolve under the other. I hit
  several 404s guessing between them.

- **STEP export quality is genuinely good.** Worth saying, since the rest of
  this is problems. Exported files are clean AP242, with sensible entity
  structure and correct `LENGTH_UNIT` declarations. I parse hole positions
  straight out of them for verification and it was straightforward — the
  `CIRCLE` → `AXIS2_PLACEMENT_3D` → `CARTESIAN_POINT` chain is exactly what
  you'd hope for.

- **KCL output from text-to-cad is readable and parametric**, with named
  variables (`plateWidth = 42.3mm`) rather than hard-coded literals. That made
  it much easier to trust than opaque generated geometry, and it's a real
  strength of the KCL-first approach.

---

## 7. One observation about accuracy, which is a compliment

Across every generation, when the prompt specified exact numeric coordinates,
the Agent API hit them **exactly** — sub-0.001mm on hole centres, verified by
parsing the resulting STEP files. Zero drift.

That's remarkable, and it's the thing that made this project viable: I built a
verification layer expecting to catch drift, and it hasn't caught any on
well-specified prompts. My verifier's own tests use a deliberately corrupted
fixture, because I couldn't get the API to produce a genuinely wrong part when
given precise input.

Worth knowing that precision-constrained prompting works this reliably — it's
a much stronger selling point than "text to CAD" implies, and it's what makes
Zoo usable as a *deterministic* geometry backend rather than a creative tool.

---

## 8. The Agent API emits *parametric, constrained* KCL — and nothing says so

This is the finding I'd most want fed back into the marketing copy, because I
only discovered it by accident and it changes what the API is for.

I had assumed text-to-CAD returned frozen geometry: literal coordinates baked
into a sketch, fine for a one-off part and useless as a starting point. So I
built ZooMounter to compute every dimension itself and treat the API as a
dumb renderer.

That's not what comes back. Asked for two obround slots, the API returned:

```kcl
slotLength = 20mm
slotWidth = 6mm
slotRadius = slotWidth / 2
slotCenterSpacing = slotLength - slotWidth
...
leftArc = arc(start = [-32mm, 3mm], end = [-32mm, -3mm], center = [-32mm, 0mm])
leftTopGuide = line(start = [-32mm, 0mm], end = [-32mm, 3mm], construction = true)
...
horizontalDistance([leftArc.center, rightArc.center]) == slotCenterSpacing
radius(leftArc) == slotRadius
```

Three things there that I did not ask for and did not expect:

1. **Named parameters**, not literals. Every dimension I stated became a
   variable.
2. **A derived relationship.** `slotCenterSpacing = slotLength - slotWidth` is
   the actual geometry of an obround slot — the arc centres sit `(L-W)/2`
   either side of centre. Nothing in my prompt said that. It was inferred.
3. **Real sketch constraints plus construction guides**, so the profile stays
   solvable when a parameter changes.

Measured across all six probe files in `probes/results/`:

| Probe | Lines | Named params | Constraints | Construction lines |
|---|---|---|---|---|
| holes | 74 | 7 | 9 | 0 |
| slot | 179 | 8 | 41 | 8 |
| counterbore | 76 | 8 | 10 | 0 |
| pocket | 78 | 8 | 12 | 0 |
| boss | 59 | 8 | 10 | 0 |
| chamfer | 65 | 5 | 24 | 0 |

Every generation, without exception. The output is an editable parametric
model, not a mesh with extra steps.

**Why it matters commercially:** "text to CAD" sets the expectation of a
throwaway blob you inspect and discard. What you actually ship is a
*constrained parametric model a human can keep working in* — which is the
difference between a novelty and a starting point for real work. That is a
much stronger claim and I saw it stated nowhere in the docs, the FAQ, or the
v1 announcement.

### The part you can control, which I'd put in the docs

Named parameters come back regardless. **Relationships only come back if you
ask for them**, and that distinction decides whether the model is editable.

ZooMounter originally computed every dimension in Python and stated the
answers as fixed coordinates. The result looked parametric and wasn't:

```
plateWidth       = 80.0mm
slotCenterOffset = 30.0mm
```

Ten well-named parameters, one derived value — and that one was
`cutLength = plateThickness + 2.0mm`, an internal extrude detail. None of
the design intent survived. Widen the slot spacing in Design Studio and the
plate does not grow with it, because nothing recorded that the plate is
sized *from* the slots.

Rewriting the prompt to declare parameters and state derivations
(`plateWidth = slotSpacing + 2 * edgeMargin`) rather than pre-computed
numbers, then generating the identical part again:

| | literal prompt | relationship prompt |
|---|---|---|
| Parameters | 10 | 12 |
| Derived | 1 (10%) | **3 (25%)** |
| Hole positions | 0.000mm | 0.000mm |
| Bounding box | 0.0% | 0.0% |
| Volume | 0.5% | 0.5% |

**Identical geometry.** Editability cost nothing in accuracy — which was the
thing genuinely in doubt, since this project's whole premise is that exact
numbers produce exact parts, and relationships meant giving some of those
numbers up. They didn't need to be given up: state the value *and* the
derivation, and you get both.

Both files are in `probes/` — `item5/2020-slots/main.kcl` is the before,
`parametric/2020-slots/main.kcl` the after.

Two things I'd suggest from this:

1. **Say this in the docs.** "Ask in relationships, get a model that edits"
   is a concrete, teachable prompting rule with a measurable outcome, and I
   found it by accident after building the wrong architecture first.
2. **One rough edge:** the submitted prompt is echoed into a `/* */` header
   in every returned file. Once the prompt contains parameter declarations,
   any tool parsing that KCL sees each declaration twice — once in the
   comment, once in the code. Harmless if you strip comments, and a silent
   double-count if you don't. Worth a note, or worth not echoing the prompt
   verbatim.

---

## 9. What the Agent API can actually build (six features, all verified)

There was no list I could find of which manufacturing features text-to-CAD
handles, so I measured it. One feature per prompt, exact numbers stated, and
every result checked by **parsing the returned STEP** — never by looking at a
render and deciding it seemed right.

| Feature | Result | How it was proven |
|---|---|---|
| Through holes (control) | supported | 4 circles at stated centres, Ø5.00 |
| **Obround slot** | **supported** | 4 arc-ends at ±(L−W)/2, Ø6.00 |
| Counterbore | supported | Concentric Ø5.00 and Ø10.00 at origin |
| Blind pocket | supported | Volume 32,400mm³ vs 36,000 solid — **0.0% off** |
| Raised boss | supported | Bounding box 11.00mm tall, Ø20.00 circle |
| Chamfer on named edges | supported | Volume 18,900mm³ vs 21,600 unchamfered — **0.0% off** |

Reproduce with `python probes/probe_features.py`.

Two notes on method, both of which cost me something to learn:

**Each check was proven able to fail before it was run.** Every checker was
first fed a fabricated correct part *and* a part with the feature missing,
and had to pass one and reject the other. The slot check, for instance,
rejects a plain round hole sitting where the slot should be — otherwise
"the API returned circles" would have read as success.

**My first chamfer probe was worthless and I nearly shipped it.** I used a
realistic 3mm chamfer, which removes 0.5% of the part volume — inside my 5%
measurement tolerance. That check would have passed an unchamfered block.
It's now 15mm, where present-versus-absent is 14% apart. Flagging it because
if you build a conformance suite for this, feature-detection thresholds need
to be derived from measurement resolution, not from what a real part looks
like.

The headline for your purposes: **slots, pockets, counterbores, bosses and
chamfers all work**, and the volume-verified ones came back exact.

---

## 10. The Agent API is repeatable, and nobody says so

Section 7 says the Agent API hits specified coordinates exactly. That was
measured one shape at a time. It leaves the more important question open:
**does the same prompt return the same geometry twice?**

For a generative endpoint that is not obvious, and the failure mode is nasty.
A model that is accurate on average but not repeatable passes every
verification run individually and still breaks things — the part you showed a
colleague yesterday is not the part they get today, and a regression test on
generated geometry can never be written.

So I measured it. `probes/determinism.py` sends one fixed prompt N times,
exports each result to STEP, and diffs the parsed geometry rather than the KCL
text — two files can differ in variable order or whitespace and describe the
same solid, so source comparison answers a less interesting question.

**Result: identical across 3 runs.** Same nine holes, same positions, same
diameters, same bounding box. The fingerprints are in
`probes/determinism/summary.json`.

```
[1/3] 9 holes, bbox [100.0, 56.4, 7.0]
[2/3] 9 holes, bbox [100.0, 56.4, 7.0]
[3/3] 9 holes, bbox [100.0, 56.4, 7.0]
IDENTICAL across 3 runs: same holes, same bounding box.
```

Three runs is not proof of determinism — it is enough to rule out obvious
run-to-run drift on a well-specified prompt, and not enough to say anything
about loosely-specified ones, which I would expect to behave differently.

**Why this is worth publishing:** repeatability is the property that makes the
Agent API usable in a build pipeline rather than only in a design session. If
Zoo is willing to state it — even scoped, as "geometry is stable for prompts
that fully constrain their dimensions" — that is a much stronger claim than
"text to CAD", and it is the one an engineer integrating the API actually
needs to hear. Right now the docs are silent on it, so every serious user has
to run this experiment themselves.

---

## 11. No assembly mates, and the gap shows up at the handoff

KCL has sketch constraints and they are good — I lean on them heavily, and
finding #8 is about how well the Agent API emits them. Assemblies have nothing
equivalent. Positioning an imported part is manual `translate`/`rotate`
arithmetic against numbers you work out yourself.

I know this is a roadmap item with a public spec open, so this is not a bug
report. It is a note about **where the gap actually bites**, which turned out
not to be where I expected.

I assumed it would hurt while building assemblies. It didn't much — ZooMounter
places its own parts, so it just does the arithmetic. The gap bit at the
**handoff**: the moment a generated part is delivered into someone else's
project. My tool knows the plate's bolt pattern, its thickness, and which face
the motor mounts to. It knows nothing about where that plate sits in your
machine, and there is no way to express "this face mates to that extrusion"
even as an unsolved constraint for the user to resolve later.

So every delivered part lands at the origin, and the honest thing to do is say
so:

```kcl
import xMotorMount from "xMotorMount.kcl"

// Generated by ZooMounter. Position it with |> translate(...) as needed.
xMotorMount
```

That comment is doing real work, and it is a workaround for a missing feature.

**What would help, in rough order of value to me:**

1. **An unsolved mate.** Even a declarative `mate(partA.face1, partB.face2)`
   that Design Studio surfaces as "unresolved" would let a generator hand over
   *intent* rather than a transform it had to invent. Intent survives the user
   moving things; a hardcoded translate does not.
2. **A named datum on an imported part.** If `xMotorMount` could expose
   `mountFace` or `boltCircle` as a referenceable entity, a human could position
   against it without reading my source to find the numbers.
3. Failing both: a documented convention for where a part's origin *should* be.
   I put mine at the centre of the plate on the mounting face, which is a guess.
   If Zoo stated a convention, every generator would agree and parts from
   different tools would compose.

The wider point: text-to-CAD makes it easy to produce parts and no easier to
**place** them. As more parts arrive from generators rather than from a person
who knows where they go, the missing half becomes the bottleneck.

---

## 12. `translate` on an imported module moves only its last body

This one is a bug report, and it is the most expensive thing I found.

Positioning a multi-part assembly the obvious way silently half-works:

```kcl
import "multi.kcl" as multi     // defines bodyA, then bodyB

multi
  |> translate(x = 30, y = 0, z = 0)
```

`bodyB` — the last solid defined in the module — lands at x=30. `bodyA` stays
at x=0. No error, no warning, and the export succeeds. The result looks like a
deliberate design.

| Body | Defined | X after `translate(x = 30)` |
|---|---|---|
| `bodyA` | first | **0.0** |
| `bodyB` | last | 30.0 |

Reproduction, exported STEP and parsed coordinates are in
`probes/assembly-translate/`.

**Why this is worse than a straightforward failure.** A four-part mount
assembly positioned this way puts three bodies at the origin and one where you
asked. If the part you happened to look at first is the one that moved, you
conclude it worked. The failure is invisible until something does not fit, and
by then the transform is several edits back.

It also interacts badly with generated assemblies specifically. A tool emitting
KCL cannot inspect what it produced to check the transform took — it would have
to export to STEP and parse the coordinates back, which is exactly what I ended
up doing to find this.

**The workaround, which I would rather not need.** Every body carries its own
transform, driven by a shared parameters file:

```kcl
// parameters.kcl
export asmX = 0
export asmY = 0
export asmZ = 0

// each part file
import * from "parameters.kcl"
someBody = extrude(...)
  |> translate(x = asmX, y = asmY, z = asmZ)
```

Verified: a real 15-body mount assembly then shifts by exactly 100mm as a unit.

**What would help:** either make module-level `translate` apply to every body in
the module, or reject it with an error saying it cannot. Silently transforming
one of four is the only outcome with no good use case. If the current behaviour
is intentional — perhaps the module evaluates to its last expression — then the
docs should say so, because the whole-file import form reads as "bring in this
assembly" and nothing suggests it collapses to one solid.

---

*Findings from building [ZooMounter](https://github.com/ibin88/ZooMounter) for
the Zoo API Makeathon, July-August 2026. Happy to expand on any of these.*
