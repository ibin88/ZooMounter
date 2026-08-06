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

- **…but multi-body STEP export drops every body name.** Exporting an assembly
  of ten solids gives ten `PRODUCT` entries, correctly separated and
  individually selectable — and all ten are called `UNIDENTIFIED_PRODUCT`. The
  KCL knew them as `mount`, `motorBody`, `coupling`, `stubShaft`,
  `bearingOuter` and so on; none of that survives the export.

  The geometry is right and the structure is right, so this is close to free to
  fix: carry the KCL variable (or the module name) into the `PRODUCT` name. As
  it stands, opening the file in CATIA gives a specification tree of ten
  identical rows, and the only way to tell the bearing from the coupling is to
  click each one and watch what highlights. For anyone whose next step is
  "switch off the reference geometry and keep the part I designed" — which is
  most people importing an assembly — that is the difference between one click
  and ten guesses.

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

## 13. What I think Zoo should own, and what I should not have had to build

Everything below I built because it was missing, not because it belonged in a
mount generator. Each one is a thing every serious user of the Agent API will
hit, will solve privately, and will solve worse than Zoo could.

### 13.1 Verification of generated geometry against a spec

This is the big one, and it is the reason this project exists.

I ask the Agent API for a plate with four holes at exact coordinates. I get
KCL back. Nothing in the platform tells me whether the holes landed where I
asked. So I wrote a STEP parser that pulls `CIRCLE → AXIS2_PLACEMENT_3D →
CARTESIAN_POINT` out of the export and compares every hole centre and diameter
to what was requested.

Everyone building on text-to-CAD needs this. Most will approximate it with a
volume or bounding-box check, which is the trap I fell into first: a correct
plate and one with a bolt hole displaced 2mm differ by **0.004% in volume**,
and both pass. One of them is scrap.

Zoo is uniquely placed to do this properly, because the File Format API already
measures geometry and the Agent API already knows what was asked. Something like:

```
POST /file/conforms-to
{
  "file": <STEP or KCL>,
  "expect": {
    "holes": [{"x": 15.5, "y": 15.5, "dia": 3.4}, ...],
    "bbox_mm": [42.3, 42.3, 4.0]
  }
}
```

returning per-feature found/not-found and positional error. That single endpoint
turns "AI-generated CAD" from a demo into something you can put in a pipeline,
and it is not something a user can build as well as you can — I am parsing your
export format from the outside and guessing at tolerances.

### 13.2 Provenance travelling with generated geometry

A generated part currently arrives as bare KCL. Nothing in it records what was
asked for, which model produced it, or when. So the moment it lands in someone's
project it is indistinguishable from a part a person drew.

That matters more than it sounds. My whole project turned on the finding that an
AI-generated number and a verified one look identical once written down. The same
is true of geometry: six months later nobody can tell which parts in an assembly
were generated, from what prompt, or whether anyone checked them.

A `// @generated` header block, or a sidecar manifest, carrying the prompt hash,
the model, the timestamp and any verification result, would cost you nothing and
would let a team answer "where did this part come from" without archaeology.

### 13.3 A standard-parts catalogue where every figure carries its conditions

I hand-curated two catalogues: NEMA frames and bearings. The interesting part is
not the numbers, it is that the loader **rejects** a figure that arrives without
the conditions it was measured under.

That rule exists because I got it wrong four times. Two vendors publish "15" for
the same quantity in different units. A radial rating is meaningless without the
distance it was quoted at, and I compared a 20mm rating to a 15mm load for weeks
without noticing.

If Zoo hosted a standard-parts library — NEMA frames, bearings, extrusion
profiles, fasteners — with that constraint enforced at the schema level, every
generator built on your platform would inherit it. Without it, each of us curates
our own copy and each of us gets a different subset wrong.

The schema matters more than the data. `28` is not a radial rating. `28 N at
20 mm from the flange, per JK42HSxx datasheet` is.

### 13.4 A stated convention for where a part's origin goes

I put mine at the centre of the plate, on the mounting face. That is a guess.
Another generator will guess differently, and parts from two tools will not
compose without someone reading both sources to find the numbers.

One paragraph in your docs — "a part's origin should be at its primary mounting
datum" or whatever you think right — would make generated parts interoperable by
default. This is the cheapest item on this list and possibly the highest
leverage.

### 13.5 Already filed above

Assembly mates (#11) and module-level `translate` moving only the last body
(#12). Both are positioning problems, and positioning is where the handoff from
generator to human currently breaks.

---

## 14. Where an LLM would genuinely help, and where it would not

I have strong opinions here because I built the non-LLM version and can see
exactly which parts were hard for the wrong reasons.

### 14.1 The hard part for a novice is not CAD. It is knowing which questions matter.

My tool asks for a shaft load in newtons. Consider what a hobbyist actually
knows: they have a NEMA 17, a belt, and a thing they want to move. They do not
know the belt tension. They do not know that a radial rating is quoted at a
distance from the flange, so the number is meaningless without saying where the
load acts. They do not know that the shaft is the weak point rather than the
bracket — that was the single biggest correction in this whole project, and it
came from a mechatronics engineer, not from the tool.

A text box marked "Shaft load (N)" hands all of that difficulty to the person
least equipped to carry it. And the answer they type becomes the input to a
chain of otherwise-rigorous checks. **The weakest link in my tool is the number
the user invents**, and no amount of provenance downstream fixes it.

### 14.2 What an LLM is genuinely good at here

**Turning a situation into a structured spec.** "It's a NEMA 17 driving a small
belt on a gantry, and the operator reaches into that area" is something a
beginner can say and an LLM can turn into `service=moving`,
`workspace=shared`, plus the follow-up questions that actually change the
answer. That is a classification task with an inspectable output — exactly the
shape where a wrong answer is visible immediately.

**Asking the one question that matters.** Most inputs do not change the verdict.
An expert knows which one does; a form cannot. An LLM that has read the rule
registry can look at a partial spec and ask "how far from the motor face does
the pulley sit?" — because it can see that the radial rating is a moment limit
and the offset is the term nobody supplies correctly.

**Explaining the chain in plain language.** My output says `BEARING REQUIRED,
200N at 15mm (3000N·mm) exceeds 75N at 20mm (1500N·mm), 2.0x over`. That is
correct and it is opaque to the person who most needs it. The same facts as
"your belt is pulling about twice as hard as this motor's bearings are rated
for; a bearing carrying the shaft would fix it" is the same information and a
different tool.

**Noticing that no rule applies.** My registry declines to model board mounts and
says so. An LLM sitting on top of a rule registry could say "nothing here covers
your case, and here is what actually governs it" — which is far more useful than
a confident number and far harder to get from a form.

### 14.3 What an LLM should not do, and why I am confident about it

It should not be the authority for a number, and it should not make the
engineering decision.

This project shipped four bugs past a green test suite **and** a passing
verifier. Not one was a coding error; all four were wrong or absent numbers. A
language model is a machine for producing plausible numbers. Putting one in the
decision seat does not fix that failure — it industrialises it, generating
confident specs faster than anyone can check them, while the verifier keeps
passing because the spec is what is wrong.

The architecture that works is already in your platform, one layer down: **the
Agent API is a language model, and it works because it is constrained to
coordinates I computed and verified against.** It does the drawing, not the
deciding. Apply the same relationship one layer up and you get the interaction
layer without the epistemics problem.

My test for which layer something belongs in: *can it be wrong silently?* If
yes, it is a rule with a source. If it is reading intent from a human, it is a
model's job.

### 14.4 The concrete suggestion

Zookeeper already does natural language → geometry. What is missing is natural
language → **spec** → verified geometry, with the spec visible in the middle
where a human can correct it.

That middle artifact is the whole thing. It is what makes the output auditable,
regression-testable, and correctable once for everyone rather than re-derived
per conversation. It is also what lets a non-expert participate: they can read
"NEMA 17, belt load 40N acting 25mm from the face, on a moving gantry" and say
"no, it's more like 60" — which they cannot do with a prompt that went straight
to a solid.

If you build that, the rule registry in this repo is the shape of the thing that
sits under it, and it is MIT licensed. Take any of it.

---

## 15. `EngineHangup` is reported as a KCL error in the user's file

Found on the last day, so it is last — but it is the most straightforwardly
actionable thing in this document.

A `zoo kcl snapshot` failed like this:

```
KCL EngineHangup error

  × engine hangup: modeling connection interrupted; please reconnect and retry
  │ (API call ID: 5b71a635-6f2c-4142-89ee-7d244f938364)
   ╭─[8:1]
 7 │
 8 │ import "mount.kcl" as mount
   · ─────────────┬─────────────
   ·              ╰── ...\assembly-exploded\main.kcl
 9 │ import "component.kcl" as component
   ╰────
```

**The file was fine.** The identical bytes, unedited, rendered on the very next
attempt seconds later. I retried three times while diagnosing; it succeeded on
the first retry, every time.

The problem is the presentation, not the failure. Connections drop — that is
normal and the message even says "please reconnect and retry". But it is
rendered in the same diagnostic format KCL uses for source errors, complete
with a span underlining line 8 of *my* file and an arrow pointing at it. Every
visual cue says "the error is here, in this import." Nothing is wrong at line
8. Nothing is wrong anywhere in the file.

Line 8 is not even meaningfully the location — it is just wherever the engine
happened to be when the socket died. A different drop would underline a
different line, and neither is a place anyone should go looking.

I lost time editing generated KCL that was already correct before I thought to
simply run it again.

### What I'd suggest

1. **Don't format transport failures as source diagnostics.** No span, no
   caret, no line number. `EngineHangup` is not a property of the file. A plain
   `error: connection to the modeling engine was interrupted (call ID ...);
   retry` carries everything useful and misleads nobody.
2. **Retry inside the CLI.** It is transient by nature and the CLI already
   knows it is transient — it says so. One or two automatic retries with a
   short backoff would make this invisible for the overwhelmingly common case.
   Every caller is otherwise obliged to implement the same retry, keyed off
   matching your error text, which is a fragile contract to hand out.
3. **Keep the call ID.** That part is genuinely good — it made this reportable.

### What I did about it

`_run_zoo_cli` in [`zoomounter/generate.py`](zoomounter/generate.py) retries
these, and only these — a real KCL error still fails on the first attempt,
because retrying broken geometry three times only makes the user wait longer
for the same answer. Covered by
[`tests/test_transient_retry.py`](tests/test_transient_retry.py).

This also surfaced a bug on my side worth naming, since it is the reason the
above took any time at all: the GUI reported *every* background failure as
`NoneType: None`. The error path formatted its message inside a lambda handed
to Tk's `after()`, and Python clears the `except ... as e` binding when the
block exits — so the message was built from a name that no longer held the
exception. A perfectly good error message existed and never reached the screen.
Fixed in `2847c32`. If you take one thing from this section: an error that
cannot be seen and an error that does not exist cost the same.

---

*Findings from building [ZooMounter](https://github.com/ibin88/ZooMounter) for
the Zoo API Makeathon, July-August 2026. Happy to expand on any of these.*
