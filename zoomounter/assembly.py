"""Compose the generated mount into an assembly you can actually look at.

A plate on its own tells you very little. The questions a person actually has
-- does the motor foul the extrusion, which way does the shaft point, is the
bearing on the right side of the plate -- are all questions about the parts
*together*.

## What is generated and what is referenced

The **mount** is the designed part and comes from the Agent API, as always.

The **component** (motor, board) and the **bearing** are hand-authored KCL
here. That is a deliberate split, not a shortcut. You do not design a NEMA 17;
you look it up. Emitting reference geometry for catalogue parts from numbers
already in the catalogue is honest, instant, deterministic, and costs no
credits -- and it keeps the thing under verification clearly separated from
the thing that is only there for context. The mount is checked against its
spec; the reference bodies are explicitly not claimed to be accurate models of
anyone's particular motor.

Reference bodies are derived entirely from fields already in mounts.toml.
Nothing new was invented to draw them, because an unsourced dimension drawn
confidently is how this project's bugs start.

## How parts are positioned

Each part file places itself in assembly coordinates, so `main.kcl` is only
imports -- the pattern Zoo's own axial-fan sample uses. Positioning in the
assembly file would mean composing transforms in a language whose transform
semantics we would be guessing at; baking the position into each part means
every file is independently openable and independently correct.

The plate is extruded symmetrically about z=0, so it occupies -t/2..+t/2, and
everything else is placed relative to that.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# WHICH FACE OF THE MOTOR is bolted to the plate.
#
# This used to mean "which side of the plate the body sits on", which was not
# a physical choice at all: putting the motor below the plate instead of above
# it is the same assembly viewed from the other side. Switching it mirrored the
# picture and changed nothing about the build.
#
# What actually differs is which end of the motor the plate is fastened to, and
# it changes where the shaft goes:
#
#   FRONT -- the plate bolts to the shaft-end faceplate. The shaft passes
#            THROUGH the plate, so the motor and whatever the shaft drives end
#            up on opposite sides of it. This is the normal NEMA mounting, and
#            the reason a NEMA plate has a pilot bore at all.
#
#   BACK  -- the plate bolts to the motor's REAR face. The shaft points away
#            from the plate and never enters it; the motor body sits between
#            the plate and the load. The plate needs no shaft clearance, and
#            the load is cantilevered off it by the whole body length.
#
# The body is on the same side of the plate either way -- it is bolted to it.
FACE_FRONT = "front"  # plate on the shaft end; shaft passes through the plate
FACE_BACK = "back"  # plate on the rear face; shaft points away from the plate
MOUNTING_FACES = (FACE_FRONT, FACE_BACK)

# Gap opened between parts for the preview render. A 1mm plate under a 40mm
# motor is geometrically correct and visually useless -- the plate simply
# disappears. The exploded view is written to its own folder and only ever
# rendered, never verified, so an assembly that is deliberately not to scale
# can never be mistaken for the part.
DEFAULT_EXPLODE_MM = 25.0

# Reference-geometry constants. These affect only the context bodies, never
# the mount that gets verified.
SHAFT_STUB_MM = 20.0  # how much shaft to draw past the plate
PILOT_BOSS_HEIGHT_MM = 2.0  # NEMA register boss standing proud of the flange
BOARD_PCB_THICKNESS_MM = 1.6  # standard FR-4

# Depth of the motor's tapped mounting holes. Indicative: what comes from the
# catalogue is the hole POSITIONS and diameter, which is the whole point of
# drawing them. The depth is only so they read as blind holes rather than
# through-holes, and nothing is derived from it.
MOTOR_TAP_DEPTH_MM = 5.0

# Outside diameter of the flexible coupling drawn in the stub-shaft assembly.
# The standard D18-D19 x L25 part; length comes from bearings.COUPLING_LENGTH_MM
# so the two cannot drift apart.
COUPLING_OD_MM = 18.0
COLOUR_COUPLING = "#6b7280"
COLOUR_STANDOFF = "#9aa2ad"

HEADER = "@settings(defaultLengthUnit = mm, kclVersion = 2.0)\n"

# Shared assembly position. Zoo's own axial-fan sample uses exactly this
# pattern -- a parameters file exported into every part -- so an assembly moves
# by editing one place rather than by hunting transforms through several files.
PARAMS_FILE = "parameters.kcl"
PARAMS_IMPORT = f'import * from "{PARAMS_FILE}"\n'

# EVERY body carries its own translate, and that is not redundancy.
#
# `import "multi.kcl" as m` followed by `m |> translate(...)` moves only the
# LAST body in that module. The earlier ones silently stay at the origin -- no
# error, no warning, and a half-displaced assembly that looks deliberate.
# Measured rather than assumed: probes/assembly-translate has the two-body case
# that proves it, and it is finding #12 in NOTES-FOR-ZOO.md.
#
# So the transform goes on each body individually, driven by the shared
# parameters. It is the only arrangement that actually moves everything.
TRANSLATE_LINE = "  |> translate(x = asmX, y = asmY, z = asmZ)"
TRANSLATE = TRANSLATE_LINE + "\n"

COLOUR_COMPONENT = "#3d4756"
COLOUR_BEARING = "#8a8f98"
COLOUR_SHAFT = "#c8ccd2"


@dataclass
class AssemblyPart:
    """One file in the assembly."""

    name: str  # KCL identifier used in main.kcl
    filename: str
    kcl: str
    role: str  # "mount" | "component" | "bearing"


def _disc(var: str, plane_z: float, dia: float, length: float, colour: str) -> str:
    """A cylinder standing on a plane offset along Z.

    `circle` takes `start` and `center`, not a radius -- the start point is a
    point on the circumference and the radius falls out of the two. That is
    the form Zoo's own sample parts use."""
    r = dia / 2
    return (
        f"{var}Plane = offsetPlane(XY, offset = {plane_z:g})\n"
        f"{var}Sketch = sketch(on = {var}Plane) {{\n"
        f"  profile = circle(start = [{r:g}mm, 0mm], center = [0mm, 0mm])\n"
        f"  diameter(profile) == {dia:g}mm\n"
        f"}}\n"
        f"{var}Region = region(segments = [{var}Sketch.profile])\n"
        f"{var}Body = extrude({var}Region, length = {length:g})\n"
        f'  |> appearance(color = "{colour}")\n'
        f"{TRANSLATE}"
        f"hidden{var} = hide({var}Sketch)\n"
    )


def _ring(var: str, plane_z: float, od: float, bore: float, length: float, colour: str) -> str:
    """A tube or ring created by subtracting an inner cylinder from an outer one."""
    ro = od / 2
    ri = bore / 2
    return (
        f"{var}Plane = offsetPlane(XY, offset = {plane_z:g})\n"
        f"{var}OuterSketch = sketch(on = {var}Plane) {{\n"
        f"  profile = circle(start = [{ro:g}mm, 0mm], center = [0mm, 0mm])\n"
        f"  diameter(profile) == {od:g}mm\n"
        f"}}\n"
        f"{var}InnerSketch = sketch(on = {var}Plane) {{\n"
        f"  profile = circle(start = [{ri:g}mm, 0mm], center = [0mm, 0mm])\n"
        f"  diameter(profile) == {bore:g}mm\n"
        f"}}\n"
        f"{var}OuterRegion = region(segments = [{var}OuterSketch.profile])\n"
        f"{var}InnerRegion = region(segments = [{var}InnerSketch.profile])\n"
        f"{var}OuterExtrude = extrude({var}OuterRegion, length = {length:g})\n"
        f"{var}InnerExtrude = extrude({var}InnerRegion, length = {length:g})\n"
        f"{var}Body = subtract({var}OuterExtrude, tools = [{var}InnerExtrude])\n"
        f'  |> appearance(color = "{colour}")\n'
        f"{TRANSLATE}"
        f"hidden{var}Outer = hide({var}OuterSketch)\n"
        f"hidden{var}Inner = hide({var}InnerSketch)\n"
    )


def _box(
    var: str,
    plane_z: float,
    width: float,
    depth: float,
    length: float,
    colour: str,
    holes: tuple[tuple[float, float], ...] = (),
    hole_dia: float = 0.0,
    hole_depth: float = 0.0,
) -> str:
    """A rectangular prism, optionally with blind holes in the face it stands on.

    The holes matter more than they look. Without them the motor is a blank
    slab, and the one question an assembly can answer at a glance -- do the
    plate's bolt holes actually line up with the motor's? -- is invisible. That
    is precisely the mismatch this project shipped once and did not catch: a
    6.4mm positional error on every hole, passing every check. Drawing both
    patterns puts it on screen.
    """
    hw, hd = width / 2, depth / 2
    lines = [
        f"{var}Plane = offsetPlane(XY, offset = {plane_z:g})",
        f"{var}Sketch = sketch(on = {var}Plane) {{",
        f"  e1 = line(start = [{-hw:g}mm, {-hd:g}mm], end = [{hw:g}mm, {-hd:g}mm])",
        f"  e2 = line(start = [{hw:g}mm, {-hd:g}mm], end = [{hw:g}mm, {hd:g}mm])",
        f"  e3 = line(start = [{hw:g}mm, {hd:g}mm], end = [{-hw:g}mm, {hd:g}mm])",
        f"  e4 = line(start = [{-hw:g}mm, {hd:g}mm], end = [{-hw:g}mm, {-hd:g}mm])",
        "}",
        f"{var}Region = region(",
        f"  segments = [{var}Sketch.e1, {var}Sketch.e2, {var}Sketch.e3, {var}Sketch.e4],",
        ")",
    ]

    if not holes or hole_dia <= 0:
        lines.append(f"{var}Body = extrude({var}Region, length = {length:g})")
        lines.append(f'  |> appearance(color = "{colour}")')
        lines.append(TRANSLATE_LINE)
        lines.append(f"hidden{var} = hide({var}Sketch)")
        return "\n".join(lines) + "\n"

    lines.append(f"{var}Solid = extrude({var}Region, length = {length:g})")
    # Blind holes run into the mounting face, i.e. the same direction the body
    # was extruded, but only as deep as the tapping.
    depth_signed = hole_depth if length >= 0 else -hole_depth
    tools = []
    for i, (hx, hy) in enumerate(holes):
        h = f"{var}Hole{i}"
        tools.append(f"{h}Solid")
        lines += [
            f"{h}Sketch = sketch(on = {var}Plane) {{",
            f"  profile = circle(start = [{hx + hole_dia / 2:g}mm, {hy:g}mm], "
            f"center = [{hx:g}mm, {hy:g}mm])",
            f"  diameter(profile) == {hole_dia:g}mm",
            "}",
            f"{h}Region = region(segments = [{h}Sketch.profile])",
            f"{h}Solid = extrude({h}Region, length = {depth_signed:g})",
        ]
    lines.append(f"{var}Body = subtract({var}Solid, tools = [{', '.join(tools)}])")
    lines.append(f'  |> appearance(color = "{colour}")')
    lines.append(TRANSLATE_LINE)
    lines.append(f"hidden{var} = hide({var}Sketch)")
    lines += [f"hidden{var}Hole{i} = hide({var}Hole{i}Sketch)" for i in range(len(holes))]
    return "\n".join(lines) + "\n"


def _sign(face: str) -> float:
    """Kept only for the bearing and board branches, where "which side" is
    still the meaningful question -- neither has a shaft end to bolt by.

    For motors it is NOT the right abstraction and is deliberately not used:
    putting the body below the plate rather than above it is the same build
    seen from underneath. See the FACE_FRONT / FACE_BACK notes at the top.
    """
    return 1.0 if face == FACE_FRONT else -1.0


def _standoffs(mount, s: float, body_base: float) -> list[str]:
    """The spacers holding the motor off the plate.

    Without these the stub-shaft assembly shows a motor hovering in space and
    nothing explaining why it stays there. The standoff is real, load-bearing
    hardware -- it is what carries the motor's weight and its reaction torque
    into the plate -- so leaving it out made the picture describe a build that
    could not stand up.

    Drawn at true length from the motor's face, so the exploded view separates
    them from the plate along with the motor rather than stretching them.
    """
    length = mount.motor_standoff_mm
    if length <= 0 or not mount.hole_positions:
        return []

    # A hex standoff for a given screw is a little larger across corners than
    # the screw head. 1.8x the clearance hole matches the head-diameter rule
    # already used for fasteners elsewhere, and is only ever drawn, never
    # dimensioned -- what is real here is the POSITION, which is the motor's
    # own bolt pattern.
    dia = mount.bolt_hole_dia_mm * 1.8
    out = []
    for i, (hx, hy) in enumerate(mount.hole_positions):
        r = dia / 2
        var = f"standoff{i}"
        out.append(
            f"{var}Plane = offsetPlane(XY, offset = {body_base:g})\n"
            f"{var}Sketch = sketch(on = {var}Plane) {{\n"
            f"  profile = circle(start = [{hx + r:g}mm, {hy:g}mm], "
            f"center = [{hx:g}mm, {hy:g}mm])\n"
            f"  diameter(profile) == {dia:g}mm\n"
            f"}}\n"
            f"{var}Region = region(segments = [{var}Sketch.profile])\n"
            f"{var}Body = extrude({var}Region, length = {-s * length:g})\n"
            f'  |> appearance(color = "{COLOUR_STANDOFF}")\n'
            f"{TRANSLATE}"
            f"hidden{var} = hide({var}Sketch)\n"
        )
    return out


def _coupling_and_stub(mount, thickness_mm: float, s: float, explode_mm: float) -> list[str]:
    """The flexible coupling and the stub shaft it drives.

    This is what makes the stub-shaft topology legible: you can see that the
    motor's shaft stops in the coupling and never enters the plate, so nothing
    the bearing carries can reach the motor's own bearings. A picture of that
    is worth more than the paragraph explaining it.
    """
    from .bearings import COUPLING_LENGTH_MM

    half_t = thickness_mm / 2
    gap_bottom = s * (half_t + explode_mm)
    standoff = mount.motor_standoff_mm
    # Centre the coupling in the standoff gap, leaving air at both ends.
    slack = max(standoff - COUPLING_LENGTH_MM, 0.0) / 2
    coupling_base = gap_bottom + s * slack

    return [
        _disc("coupling", coupling_base, COUPLING_OD_MM, s * COUPLING_LENGTH_MM,
              COLOUR_COUPLING),
        # Stub shaft: up into the coupling, down through the plate and out.
        _disc(
            "stubShaft",
            coupling_base + s * (COUPLING_LENGTH_MM * 0.6),
            mount.bearing_bore_mm,
            -s * (COUPLING_LENGTH_MM * 0.6 + slack + explode_mm + thickness_mm + SHAFT_STUB_MM),
            COLOUR_SHAFT,
        ),
    ]


def component_kcl(mount, thickness_mm: float, face: str, explode_mm: float = 0.0) -> str | None:
    """Reference body for the component this mount carries.

    `mount` must be the BASE spec, not one that has been through
    apply_host_mount. That widens plate_width_mm to carry the host slots, and
    passing the widened spec here drew a NEMA 17 body 80mm across instead of
    42.3mm -- the motor silently inherited the bracket's size. Same mistake as
    the GUI schematic re-deriving plate width, made again a week later, which
    is why the caller now has to hand over the base explicitly rather than
    this function guessing which spec it was given.

    Returns None when the catalogue has nothing to draw it from -- a flange or
    a custom mount has no component, and inventing one would put geometry on
    screen that answers to no data.
    """
    half_t = thickness_mm / 2
    front = face == FACE_FRONT
    body_len = mount.body_cg_offset_mm * 2

    if mount.kind == "motor" and body_len > 0:
        parts = [
            f"// Reference geometry for a {mount.name}, "
            f"{'front' if front else 'rear'}-face mounted.",
            "// Drawn from catalogue dimensions for context only -- this is not",
            "// the designed part and is not verified against anything.",
            HEADER,
        ]
        # The motor sits on the plate whichever face is bolted -- it is
        # fastened to it. In the stub-shaft topology it stands off on spacers,
        # with the coupling in the gap. That is real geometry rather than the
        # explode offset, so it survives into the to-scale assembly.
        standoff = mount.motor_standoff_mm
        body_base = half_t + standoff + explode_mm
        body_far = body_base + body_len

        parts.append(
            _box("motorBody", body_base, mount.plate_width_mm,
                 mount.plate_height_mm, body_len, COLOUR_COMPONENT,
                 holes=mount.hole_positions,
                 hole_dia=mount.bolt_hole_dia_mm,
                 hole_depth=MOTOR_TAP_DEPTH_MM)
        )

        # The pilot boss and the shaft both live on the motor's FRONT face.
        # Which end of the assembly that is depends on which face got bolted.
        shaft_face = body_base if front else body_far
        boss_dir = -1.0 if front else 1.0

        if mount.center_hole_dia_mm > 0 and standoff == 0:
            # The boss only registers in something it is touching, so a
            # rear-mounted motor's boss stands proud into open air.
            parts.append(
                _disc("pilotBoss", shaft_face, mount.center_hole_dia_mm,
                      boss_dir * PILOT_BOSS_HEIGHT_MM, COLOUR_COMPONENT)
            )

        if mount.shaft_dia_mm > 0:
            if not front:
                # Rear-mounted: the shaft leaves the far end of the motor and
                # runs AWAY from the plate. It never enters it, which is the
                # whole difference between the two mountings -- and why the
                # plate needs no shaft clearance in this configuration.
                parts.append(
                    _disc("shaft", shaft_face, mount.shaft_dia_mm,
                          SHAFT_STUB_MM, COLOUR_SHAFT)
                )
            elif standoff > 0:
                # Motor shaft reaches down into the coupling, and stops there.
                parts.append(
                    _disc("shaft", shaft_face, mount.shaft_dia_mm,
                          -(explode_mm + standoff * 0.8), COLOUR_SHAFT)
                )
            else:
                # Straight through the plate and out the far side. The explode
                # offset is added to the LENGTH as well as the start, or the
                # shaft stops in mid-air short of the hole it passes through --
                # which is what the exploded preview used to show.
                parts.append(
                    _disc("shaft", shaft_face, mount.shaft_dia_mm,
                          -(thickness_mm + SHAFT_STUB_MM + explode_mm),
                          COLOUR_SHAFT)
                )

        if standoff > 0:
            parts += _standoffs(mount, 1.0, body_base)
            # A coupling only exists if the motor's shaft points at the plate.
            # Rear-mounted it points the other way, and drawing one would show
            # a drive train that cannot be built.
            if front and mount.bearing_bore_mm > 0:
                parts += _coupling_and_stub(mount, thickness_mm, 1.0, explode_mm)
        return "\n".join(parts)

    if mount.kind == "bearing":
        parts = [
            f"// Reference geometry for the shaft supported by this {mount.name}.",
            "// Drawn for context only -- this is not the designed part.",
            HEADER,
        ]
        # Shaft passes through the block completely.
        s = _sign(face)
        shaft_start = s * half_t + s * 20.0
        parts.append(
            _disc("shaft", shaft_start, mount.shaft_dia_mm,
                  -s * (thickness_mm + 40.0), COLOUR_SHAFT)
        )
        return "\n".join(parts)

    if mount.kind == "board":
        return "\n".join([
            f"// Reference geometry for a {mount.name}.",
            "// Board outline approximated by the mounting-hole envelope plus a",
            "// margin; the real PCB outline is not in the catalogue, so this is",
            "// indicative only.",
            HEADER,
            _box("board", _sign(face) * half_t, mount.plate_width_mm + 20,
                 mount.plate_height_mm + 6, _sign(face) * BOARD_PCB_THICKNESS_MM,
                 COLOUR_COMPONENT),
        ])

    return None


def bearing_kcl(bearing, mount, thickness_mm: float, explode_mm: float = 0.0) -> str:
    """Reference body for the bearing, sitting in its seat.

    A ring: outer diameter to the seat bore, inner diameter to the shaft. For
    a thrust block the seat is a blind counterbore, so the bearing sits down
    inside it; for a radial block it is a through-bore and the bearing sits
    flush in the plate.
    """
    half_t = thickness_mm / 2
    if mount.bearing_seat_depth_mm > 0:
        # Blind counterbore opening from the top face.
        base_z = half_t - mount.bearing_seat_depth_mm + explode_mm
    else:
        base_z = -half_t + (thickness_mm - bearing.width_mm) / 2 - explode_mm

    return "\n".join([
        f"// Reference geometry for a {bearing.designation} bearing "
        f"({bearing.bore_mm:g}x{bearing.od_mm:g}x{bearing.width_mm:g}mm).",
        "// Catalogue part shown seated in the mount; not a designed component.",
        HEADER,
        _ring("bearingOuter", base_z, bearing.od_mm, bearing.bore_mm, bearing.width_mm, COLOUR_BEARING),
    ])


def parameters_kcl() -> str:
    """The one place an assembly's position lives.

    ZooMounter puts everything at the origin because it does not know where the
    assembly belongs in your machine -- KCL has no mate or constraint system to
    express that with. Three numbers in one file is the next best thing: change
    them and every part moves together, instead of hunting transforms through
    four files and getting one of them wrong.
    """
    return (
        "// Where this assembly sits in your project.\n"
        "//\n"
        "// ZooMounter places everything at the origin: it sized and verified\n"
        "// the mount, but it has no idea where the assembly belongs in your\n"
        "// machine. Change these three numbers and every part moves together.\n"
        "//\n"
        "// Every body applies these individually and that is deliberate --\n"
        "// translating a whole imported module moves only its LAST body. See\n"
        "// finding #12 in NOTES-FOR-ZOO.md.\n"
        + HEADER
        + "\nexport asmX = 0\nexport asmY = 0\nexport asmZ = 0\n"
    )


def _with_params(kcl: str) -> str:
    """Give a part file access to the shared position.

    Inserted after the @settings header, because KCL wants settings first.
    """
    if PARAMS_IMPORT.strip() in kcl:
        return kcl
    marker = HEADER.rstrip("\n")
    if marker in kcl:
        return kcl.replace(marker, marker + "\n" + PARAMS_IMPORT.rstrip("\n"), 1)
    return PARAMS_IMPORT + kcl


def _positioned_mount(mount_kcl: str) -> str:
    """The Agent API's part, plus a translate driven by the shared parameters.

    Deliberately ADDITIVE: not one line of the generated geometry is edited,
    and at the default asmX/Y/Z of 0 the result is geometrically identical to
    what was verified. What is added is a handle to move it by, which the
    generated script has no way to provide for itself.
    """
    from . import zoo_project

    body = zoo_project.last_assignment(mount_kcl)
    if body is None:
        # Nothing to hang a transform on. Say so in the file rather than
        # silently shipping an assembly whose mount ignores the parameters.
        return (
            mount_kcl.rstrip()
            + "\n\n// NOTE: ZooMounter could not find a final assignment to\n"
            "// position, so this part ignores parameters.kcl and stays at the\n"
            "// origin. Move it by hand.\n"
        )
    return (
        mount_kcl.rstrip()
        + f"\n\n// Positioned from {PARAMS_FILE}. The geometry above is exactly\n"
        "// what was generated and verified; only the placement is added.\n"
        f"{body}\n{TRANSLATE}"
    )


def main_kcl(parts: list[AssemblyPart]) -> str:
    """The assembly file: imports only.

    Matches Zoo's own axial-fan sample. Each part has already placed itself,
    so there are no transforms here to get wrong."""
    lines = [
        "// Assembly generated by ZooMounter.",
        "//",
        "// The mount is the designed part, generated by Zoo's Agent API and",
        "// verified against its spec. The other bodies are catalogue reference",
        "// geometry, drawn for context so the fit can be seen.",
        HEADER,
    ]
    lines += [f'import "{p.filename}" as {p.name}' for p in parts]
    lines.append("")
    lines += [p.name for p in parts]
    return "\n".join(lines) + "\n"


def write_assembly(
    output_dir: Path,
    mount,
    thickness_mm: float,
    mount_kcl: str,
    bearing=None,
    face: str = FACE_FRONT,
    base_mount=None,
    explode_mm: float = 0.0,
) -> tuple[Path, list[AssemblyPart]]:
    """Write every part plus main.kcl. Returns (main.kcl path, parts).

    `mount` is the final spec the plate was generated from. `base_mount` is
    the same mount before host-side features widened it, and is what the
    component body is drawn from -- see component_kcl for why that has to be
    passed rather than inferred."""
    if face not in MOUNTING_FACES:
        raise ValueError(f"mounting face must be one of {MOUNTING_FACES}, got {face!r}")

    output_dir.mkdir(parents=True, exist_ok=True)
    parts = [AssemblyPart("mount", "mount.kcl", _positioned_mount(mount_kcl), "mount")]

    body = component_kcl(base_mount or mount, thickness_mm, face, explode_mm)
    if body:
        parts.append(AssemblyPart("component", "component.kcl", body, "component"))

    if bearing is not None:
        parts.append(
            AssemblyPart("bearing", "bearing.kcl",
                         bearing_kcl(bearing, mount, thickness_mm, explode_mm),
                         "bearing")
        )

    (output_dir / PARAMS_FILE).write_text(parameters_kcl(), encoding="utf-8")
    for p in parts:
        (output_dir / p.filename).write_text(_with_params(p.kcl), encoding="utf-8")

    main_path = output_dir / "main.kcl"
    main_path.write_text(main_kcl(parts), encoding="utf-8")
    return main_path, parts
