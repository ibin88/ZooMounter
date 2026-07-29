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

It also has a concrete architectural consequence I'd have designed around if
I'd known: ZooMounter computes dimensions in Python and bakes numbers into
the prompt. Knowing the output is parametric, the better design emits the
*relationships* and lets the KCL carry them, so a downstream user can adjust
`slotLength` in Design Studio and have the model stay correct. I'd have built
it that way from the start.

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

*Findings from building [ZooMounter](https://github.com/ibin88/ZooMounter) for
the Zoo API Makeathon, July 2026. Happy to expand on any of these.*
