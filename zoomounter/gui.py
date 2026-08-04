"""ZooMounter desktop GUI.

A thin presentation layer over the same backend the CLI uses -- no
duplicated logic. Every field maps directly to a `zoomounter.cli.build_parser`
flag; the domain-rules calc (mechanics.required_thickness) runs locally and
instantly as you fill in the form (no API cost), so you can see the
calculated thickness before committing to a generation call. "Generate &
Verify" runs the real Agent API -> Zoo CLI export -> File Format API
pipeline in a background thread so the window stays responsive, then shows
the same pass/fail results the CLI report contains.

Run with: python -m zoomounter.gui
"""

import io
import os
import tempfile
import threading
import traceback
import uuid
from tkinter import filedialog
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from . import generate, mechanics, verify
from .config import load_environment
from .cli import default_output_dir, write_report
from .materials import MATERIALS, get_material
from .bearings import BY_DESIGNATION, auto_bearing_mount
from .mount_specs import MOUNTS, get_mount

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

MOUNT_KEYS = [*MOUNTS.keys(), "bearing", "custom"]
MATERIAL_KEYS = [*MATERIALS.keys(), "custom"]


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ZooMounter")
        self.geometry("880x680")
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.output_dir = Path("output")
        self.generation_running = False

        self._build_form()
        self._build_results()
        self._on_mount_change(self.mount_var.get())
        self._on_material_change(self.material_var.get())

    # ---- form -----------------------------------------------------------

    def _build_form(self) -> None:
        form = ctk.CTkFrame(self)
        form.grid(row=0, column=0, padx=16, pady=16, sticky="nsew")
        form.grid_columnconfigure(1, weight=1)
        row = 0

        ctk.CTkLabel(form, text="ZooMounter", font=ctk.CTkFont(size=20, weight="bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )
        row += 1

        ctk.CTkLabel(form, text="Mount type").grid(row=row, column=0, sticky="w")
        self.mount_var = ctk.StringVar(value="nema17")
        ctk.CTkOptionMenu(form, values=MOUNT_KEYS, variable=self.mount_var, command=self._on_mount_change).grid(
            row=row, column=1, sticky="ew", pady=4
        )
        row += 1

        self.bearing_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.bearing_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        self.bearing_frame.grid_columnconfigure(1, weight=1)
        self.shaft_dia_var = self._labeled_entry(
            self.bearing_frame, 0, "Shaft diameter (mm)", "8")
        ctk.CTkLabel(self.bearing_frame, text="Bearing").grid(row=1, column=0, sticky="w")
        self.bearing_var = ctk.StringVar(value="auto")
        ctk.CTkOptionMenu(
            self.bearing_frame,
            values=["auto", *sorted(BY_DESIGNATION)],
            variable=self.bearing_var,
        ).grid(row=1, column=1, sticky="ew", pady=4)
        ctk.CTkLabel(
            self.bearing_frame,
            text="auto picks the smallest bearing that fits the shaft and carries the load. Radial loads get a deep groove bearing, axial loads a thrust bearing.",
            font=ctk.CTkFont(size=11), text_color="gray60", anchor="w", wraplength=380,
        ).grid(row=2, column=0, columnspan=2, sticky="w")
        row += 1

        self.custom_mount_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.custom_mount_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        self.custom_mount_frame.grid_columnconfigure(1, weight=1)
        self.plate_width_var = self._labeled_entry(self.custom_mount_frame, 0, "Plate width (mm)", "50")
        self.bolt_count_var = self._labeled_entry(self.custom_mount_frame, 1, "Bolt count", "4")
        self.bolt_circle_var = self._labeled_entry(self.custom_mount_frame, 2, "Bolt circle dia (mm)", "40")
        self.bolt_hole_var = self._labeled_entry(self.custom_mount_frame, 3, "Bolt hole dia (mm)", "4")
        self.center_hole_var = self._labeled_entry(self.custom_mount_frame, 4, "Center hole dia (mm, 0=none)", "0")
        row += 1
        
        ctk.CTkLabel(form, text="Host mounting").grid(row=row, column=0, sticky="w")
        self.host_mount_var = ctk.StringVar(value="none")
        ctk.CTkOptionMenu(
            form, values=["none", "2020-slots", "4040-slots", "corner-holes"], variable=self.host_mount_var, command=self._on_host_mount_change
        ).grid(row=row, column=1, sticky="ew", pady=4)
        row += 1

        self.host_mount_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.host_mount_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        self.host_mount_frame.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(self.host_mount_frame, text="Slot direction").grid(row=0, column=0, sticky="w")
        self.host_slot_dir_var = ctk.StringVar(value="parallel")
        ctk.CTkOptionMenu(self.host_mount_frame, values=["parallel", "perpendicular"], variable=self.host_slot_dir_var, command=lambda _: self._draw_schematic()).grid(row=0, column=1, sticky="ew", pady=4)
        
        self.plate_width_override_var = self._labeled_entry(self.host_mount_frame, 1, "Plate width override (mm, blank=auto)", "")
        self.plate_width_override_var.trace_add("write", lambda *_: self._draw_schematic())
        
        ctk.CTkLabel(self.host_mount_frame, text="Component face").grid(row=3, column=0, sticky="w")
        self.mounting_face_var = ctk.StringVar(value="front")
        ctk.CTkOptionMenu(
            self.host_mount_frame, values=["front", "back"],
            variable=self.mounting_face_var,
            command=lambda _: self._draw_schematic(),
        ).grid(row=3, column=1, sticky="ew", pady=4)

        self.schematic_canvas = ctk.CTkCanvas(self.host_mount_frame, height=180, bg="#2b2b2b", highlightthickness=0)
        self.schematic_canvas.grid(row=2, column=0, columnspan=2, sticky="ew", pady=8)
        row += 1

        ctk.CTkLabel(form, text="Material").grid(row=row, column=0, sticky="w")
        self.material_var = ctk.StringVar(value="aluminum_6061")
        ctk.CTkOptionMenu(
            form, values=MATERIAL_KEYS, variable=self.material_var, command=self._on_material_change
        ).grid(row=row, column=1, sticky="ew", pady=4)
        row += 1

        self.custom_material_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.custom_material_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        self.custom_material_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.custom_material_frame, text="Process").grid(row=0, column=0, sticky="w")
        self.process_var = ctk.StringVar(value="machined")
        ctk.CTkOptionMenu(self.custom_material_frame, values=["3d_print", "machined"], variable=self.process_var).grid(
            row=0, column=1, sticky="ew", pady=4
        )
        self.density_var = self._labeled_entry(self.custom_material_frame, 1, "Density (kg/m3)", "2700")
        self.youngs_var = self._labeled_entry(self.custom_material_frame, 2, "Young's modulus (GPa)", "69")
        self.yield_var = self._labeled_entry(self.custom_material_frame, 3, "Yield strength (MPa)", "240")
        row += 1

        ctk.CTkLabel(form, text="Load type").grid(row=row, column=0, sticky="w")
        self.load_type_var = ctk.StringVar(value="radial")
        ctk.CTkOptionMenu(form, values=list(mechanics.LOAD_TYPES), variable=self.load_type_var).grid(
            row=row, column=1, sticky="ew", pady=4
        )
        row += 1
        ctk.CTkLabel(
            form,
            text="radial = side load (belt/pulley), bending-governed. axial = thrust along bolt axis, bearing-stress-governed.",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
            anchor="w",
            wraplength=380,
        ).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        self.load_var = self._labeled_entry(form, row, "Load (N)", "5")
        row += 1
        self.safety_var = self._labeled_entry(form, row, "Safety factor", "2.0")
        row += 1
        self.overhang_var = self._labeled_entry(form, row, "Overhang override (mm, blank=auto, radial only)", "")
        row += 1
        ctk.CTkLabel(form, text="Output base folder").grid(row=row, column=0, sticky="w")
        output_row = ctk.CTkFrame(form, fg_color="transparent")
        output_row.grid(row=row, column=1, sticky="ew", pady=4)
        output_row.grid_columnconfigure(0, weight=1)
        self.output_base_var = ctk.StringVar(value="output")
        ctk.CTkEntry(output_row, textvariable=self.output_base_var).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(output_row, text="Browse…", width=70, command=self._browse_output_folder).grid(row=0, column=1)
        ctk.CTkLabel(
            form,
            text="Each run gets its own subfolder here (mount_material_timestamp) so nothing overwrites.",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
            anchor="w",
        ).grid(row=row + 1, column=0, columnspan=2, sticky="w")
        row += 1
        row += 1

        self.export_step_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            form,
            text="Export STEP + verify (uncheck for a fast preview-only run)",
            variable=self.export_step_var,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        row += 1

        ctk.CTkButton(form, text="Preview calculation", command=self._preview).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(12, 4)
        )
        row += 1


        self.generate_button = ctk.CTkButton(
            form, text="Generate && Verify", fg_color="#2fa572", hover_color="#227a54", command=self._start_generate
        )
        self.generate_button.grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        row += 1

        self.status_label = ctk.CTkLabel(form, text="", justify="left", anchor="w", wraplength=380)
        self.status_label.grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)

    def _labeled_entry(self, parent, row: int, label: str, default: str) -> ctk.StringVar:
        ctk.CTkLabel(parent, text=label).grid(row=row, column=0, sticky="w")
        var = ctk.StringVar(value=default)
        ctk.CTkEntry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", pady=4)
        return var

    def _browse_output_folder(self) -> None:
        folder = filedialog.askdirectory(
            title="Select output base folder",
            initialdir=self.output_base_var.get() or ".",
        )
        if folder:
            self.output_base_var.set(folder)

    def _on_mount_change(self, value: str) -> None:
        if value == "custom":
            self.custom_mount_frame.grid()
        else:
            self.custom_mount_frame.grid_remove()
        self._on_bearing_visibility(value)

    def _on_host_mount_change(self, value: str) -> None:
        if value == "none":
            self.host_mount_frame.grid_remove()
        else:
            self.host_mount_frame.grid()
            self._draw_schematic()

    # ---- 3D schematic -----------------------------------------------------
    #
    # Isometric rather than top-down, because plate thickness is the number
    # this whole tool exists to compute and a plan view cannot show it.
    #
    # Geometry comes from _resolve_spec(), i.e. from apply_host_mount itself.
    # The previous version re-derived the plate width inline with its own copy
    # of the auto-sizing rule, which silently went stale when the extrusion
    # series gained a real 40mm pitch -- it drew every 4040 mount as a 2020.

    _ISO_COS30 = 0.8660254037844387
    _ISO_SIN30 = 0.5

    @staticmethod
    def _iso(x: float, y: float, z: float) -> tuple[float, float]:
        """Project model mm onto the canvas plane. +z is up the screen."""
        return (
            (x - y) * App._ISO_COS30,
            (x + y) * App._ISO_SIN30 - z,
        )

    @staticmethod
    def _circle_3d(cx, cy, r, z, segments=20):
        import math
        return [
            (cx + r * math.cos(2 * math.pi * i / segments),
             cy + r * math.sin(2 * math.pi * i / segments),
             z)
            for i in range(segments)
        ]

    @staticmethod
    def _slot_3d(cx, cy, length, width, direction, z, segments=10):
        """Obround outline: two semicircular caps offset +/-(L-W)/2 from
        centre. Same construction verify.py expects in the STEP, so what is
        drawn here is what gets checked later."""
        import math
        r = width / 2
        d = max((length - width) / 2, 0.0)
        pts = []
        for i in range(segments + 1):  # cap at +d, sweeping -90 -> +90
            a = -math.pi / 2 + math.pi * i / segments
            pts.append((d + r * math.cos(a), r * math.sin(a)))
        for i in range(segments + 1):  # cap at -d, sweeping +90 -> +270
            a = math.pi / 2 + math.pi * i / segments
            pts.append((-d + r * math.cos(a), r * math.sin(a)))
        if direction == "y":
            pts = [(-py, px) for px, py in pts]
        return [(cx + px, cy + py, z) for px, py in pts]

    def _draw_schematic(self):
        c = self.schematic_canvas
        hm = self.host_mount_var.get()
        if hm == "none":
            c.delete("all")
            return

        try:
            mount, _material, _load, _sf, thickness = self._resolve_spec()
            base = get_mount(
                self.mount_var.get(),
                plate_width_mm=float(self.plate_width_var.get()) if self.plate_width_var.get() else None,
                bolt_count=int(self.bolt_count_var.get()) if self.bolt_count_var.get() else None,
                bolt_circle_dia_mm=float(self.bolt_circle_var.get()) if self.bolt_circle_var.get() else None,
                bolt_hole_dia_mm=float(self.bolt_hole_var.get()) if self.bolt_hole_var.get() else None,
                center_hole_dia_mm=float(self.center_hole_var.get() or 0),
            )
        except Exception:
            # A half-typed load or width is not an error worth rendering. Keep
            # the last good frame instead of flashing a blank canvas on every
            # keystroke.
            return

        c.delete("all")
        c.update_idletasks()
        cw = c.winfo_width() or 400
        ch = c.winfo_height() or 180

        W = mount.plate_width_mm
        H = mount.plate_height_mm
        T_real = thickness.required_thickness_mm

        # A 1.05mm plate against an 80mm footprint is a hairline. Thicken it
        # to stay visible, but say so -- an unlabelled exaggeration is how a
        # drawing starts lying about the part.
        T_min_visible = max(W, H) * 0.05
        T = max(T_real, T_min_visible)
        exaggerated = T > T_real + 1e-9

        shapes: list[dict] = []
        # (anchor_point_3d, text, colour, font, (dx_px, dy_px), anchor)
        # The pixel offset is applied AFTER projection on purpose: nudging a
        # label in model space gets sheared by the isometric transform, so
        # "10mm below the edge" lands somewhere diagonal and on top of the
        # geometry.
        labels: list[tuple] = []

        hw, hh = W / 2, H / 2

        # Two side faces are visible in this projection: x=+W/2 and y=+H/2.
        shapes.append({
            "pts": [(hw, -hh, 0), (hw, hh, 0), (hw, hh, T), (hw, -hh, T)],
            "fill": "#1d2320", "outline": "#2fa572", "width": 1,
        })
        shapes.append({
            "pts": [(-hw, hh, 0), (hw, hh, 0), (hw, hh, T), (-hw, hh, T)],
            "fill": "#232a26", "outline": "#2fa572", "width": 1,
        })
        # Top face last so its edges sit above the sides.
        shapes.append({
            "pts": [(-hw, -hh, T), (hw, -hh, T), (hw, hh, T), (-hw, hh, T)],
            "fill": "#2d3733", "outline": "#2fa572", "width": 2,
        })

        # Auto-width ghost, when an override is in play.
        try:
            from .mount_specs import apply_host_mount
            auto = apply_host_mount(
                base,
                host_mount=hm,
                host_slot_direction=self.host_slot_dir_var.get(),
                plate_width_override=None,
            )
            if abs(auto.plate_width_mm - W) > 1e-6:
                ahw = auto.plate_width_mm / 2
                shapes.append({
                    "pts": [(-ahw, -hh, T), (ahw, -hh, T), (ahw, hh, T), (-ahw, hh, T)],
                    "fill": "", "outline": "#666666", "width": 1, "dash": (3, 5),
                })
                # (-W/2, +H/2) is the leftmost corner under isometric, so the
                # label clears the motor body instead of sitting behind it.
                labels.append((
                    (-ahw, hh, T), f"auto: {auto.plate_width_mm:.0f}mm",
                    "#888888", ("Arial", 8), (-6, -6), "e",
                ))
        except Exception:
            pass

        # Motor body, standing on the plate. NEMA frames are square, and
        # apply_host_mount only widens X, so the body footprint is the
        # unwidened mount.
        body_len = base.body_cg_offset_mm * 2
        if body_len > 0:
            bw = base.plate_width_mm / 2
            bh = base.plate_height_mm / 2
            top = T + body_len
            shapes.append({
                "pts": [(bw, -bh, T), (bw, bh, T), (bw, bh, top), (bw, -bh, top)],
                "fill": "#333333", "outline": "#777777", "width": 1,
            })
            shapes.append({
                "pts": [(-bw, bh, T), (bw, bh, T), (bw, bh, top), (-bw, bh, top)],
                "fill": "#3b3b3b", "outline": "#777777", "width": 1,
            })
            shapes.append({
                "pts": [(-bw, -bh, top), (bw, -bh, top), (bw, bh, top), (-bw, bh, top)],
                "fill": "#454545", "outline": "#888888", "width": 1,
            })
            labels.append((
                (0, 0, top), f"Motor {base.plate_width_mm:.0f}mm",
                "white", ("Arial", 9), (0, 0), "center",
            ))
        else:
            for x, y in mount.hole_positions:
                shapes.append({
                    "pts": self._circle_3d(x, y, mount.bolt_hole_dia_mm / 2, T),
                    "fill": "#121614", "outline": "#2fa572", "width": 1,
                })
            if mount.center_hole_dia_mm > 0:
                shapes.append({
                    "pts": self._circle_3d(0, 0, mount.center_hole_dia_mm / 2, T),
                    "fill": "#121614", "outline": "#2fa572", "width": 1,
                })

        # Host-side features -- the point of this panel.
        for x, y, length, width, direction in mount.host_slots:
            shapes.append({
                "pts": self._slot_3d(x, y, length, width, direction, T),
                "fill": "#0f1a1c", "outline": "#4fd6e0", "width": 2,
            })
        for x, y, dia in mount.host_holes:
            shapes.append({
                "pts": self._circle_3d(x, y, dia / 2, T),
                "fill": "#0f1a1c", "outline": "#4fd6e0", "width": 2,
            })
        if mount.host_slots:
            # Label the slot nearest the viewer. The far one sits behind the
            # motor body in this projection.
            near = max(mount.host_slots, key=lambda s: s[0] + s[1])
            labels.append((
                (near[0], near[1], T), "Slot",
                "#4fd6e0", ("Arial", 8), (0, -16), "center",
            ))

        # Dimension lines: width along the front-bottom edge, thickness up the
        # near corner.
        dim_drop = max(W, H) * 0.10
        shapes.append({
            "pts": [(-hw, hh + dim_drop, 0), (hw, hh + dim_drop, 0)],
            "fill": "", "outline": "#2fa572", "width": 1, "open": True,
        })
        width_label = f"{W:.0f}mm"
        width_label += " (override)" if self.plate_width_override_var.get().strip() else " (auto)"
        labels.append((
            (0, hh + dim_drop, 0), width_label,
            "#2fa572", ("Arial", 9), (0, 16), "center",
        ))

        shapes.append({
            "pts": [(hw + dim_drop * 0.6, hh, 0), (hw + dim_drop * 0.6, hh, T)],
            "fill": "", "outline": "#e0a54f", "width": 1, "open": True,
        })
        t_text = f"t = {T_real:.2f}mm"
        if exaggerated:
            t_text += " (not to scale)"
        labels.append((
            (hw + dim_drop * 0.6, hh, T / 2), t_text,
            "#e0a54f", ("Arial", 8), (10, 4), "w",
        ))

        # --- project, fit, draw ---
        projected = [[self._iso(*p) for p in s["pts"]] for s in shapes]
        label_pts = [self._iso(*entry[0]) for entry in labels]

        every = [p for poly in projected for p in poly] + label_pts
        xs = [p[0] for p in every]
        ys = [p[1] for p in every]
        span_x = max(max(xs) - min(xs), 1e-6)
        span_y = max(max(ys) - min(ys), 1e-6)
        pad = 26  # room for the dimension text, which sits outside the solid
        scale = min((cw - pad) / span_x, (ch - pad) / span_y)
        off_x = cw / 2 - (min(xs) + max(xs)) / 2 * scale
        off_y = ch / 2 - (min(ys) + max(ys)) / 2 * scale

        def to_canvas(pt):
            return (pt[0] * scale + off_x, pt[1] * scale + off_y)

        for shape, poly in zip(shapes, projected):
            flat = [v for pt in poly for v in to_canvas(pt)]
            if shape.get("open"):
                c.create_line(*flat, fill=shape["outline"], width=shape["width"])
            else:
                c.create_polygon(
                    *flat,
                    fill=shape["fill"],
                    outline=shape["outline"],
                    width=shape["width"],
                    dash=shape.get("dash", ()),
                )

        for entry, pt in zip(labels, label_pts):
            _pos, text, colour, font, (dx, dy), anchor = entry
            x, y = to_canvas(pt)
            c.create_text(x + dx, y + dy, text=text, fill=colour,
                          font=font, anchor=anchor)

    def _on_bearing_visibility(self, mount_key: str) -> None:
        if mount_key == "bearing":
            self.bearing_frame.grid()
        else:
            self.bearing_frame.grid_remove()

    def _on_material_change(self, value: str) -> None:
        if value == "custom":
            self.custom_material_frame.grid()
        else:
            self.custom_material_frame.grid_remove()

    # ---- results panel ----------------------------------------------------

    def _build_results(self) -> None:
        panel = ctk.CTkFrame(self)
        panel.grid(row=0, column=1, padx=(0, 16), pady=16, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(panel, text="Preview", font=ctk.CTkFont(size=20, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 4)
        )
        self.preview_image_label = ctk.CTkLabel(panel, text="(no preview yet)", height=220)
        self.preview_image_label.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        panel.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(panel, text="Verification", font=ctk.CTkFont(size=20, weight="bold")).grid(
            row=2, column=0, sticky="w", padx=12, pady=(4, 8)
        )
        self.results_text = ctk.CTkTextbox(panel, wrap="word", height=100)
        self.results_text.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 8))
        panel.grid_rowconfigure(3, weight=1)
        self.results_text.configure(state="disabled")

        self.overall_label = ctk.CTkLabel(panel, text="", font=ctk.CTkFont(size=22, weight="bold"))
        self.overall_label.grid(row=4, column=0, pady=4)

        self.open_folder_button = ctk.CTkButton(
            panel, text="Open output folder", state="disabled", command=self._open_output_folder
        )
        self.open_folder_button.grid(row=5, column=0, sticky="ew", padx=12, pady=(4, 12))

    def _show_preview_image(self, image_path: Path) -> None:
        # Read fully into memory and decode from bytes rather than keeping a
        # handle on the file -- Image.open() alone keeps the file locked on
        # Windows. Delete right after reading, on this (main) thread, so
        # there's no race with the background thread that would otherwise
        # try to delete it while/before it's being read (that race is what
        # crashed the background thread and left the GUI stuck on
        # "Working..." -- the delete failed with a PermissionError that
        # nothing caught).
        data = image_path.read_bytes()
        try:
            image_path.unlink()
        except OSError:
            pass  # never left behind long (temp dir), and not fatal either way

        img = Image.open(io.BytesIO(data))
        img.load()
        img.thumbnail((420, 320))
        ctk_image = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
        self.preview_image_label.configure(image=ctk_image, text="")
        self.preview_image_label.image = ctk_image  # keep a reference, tkinter won't hold one for us

    def _set_results_text(self, text: str) -> None:
        self.results_text.configure(state="normal")
        self.results_text.delete("1.0", "end")
        self.results_text.insert("1.0", text)
        self.results_text.configure(state="disabled")

    def _open_output_folder(self) -> None:
        try:
            os.startfile(self.output_dir)  # noqa: S606 -- Windows-only, intentional
        except AttributeError:
            import subprocess

            subprocess.run(["xdg-open", str(self.output_dir)])

    # ---- spec resolution --------------------------------------------------

    def _resolve_spec(self):
        if self.mount_var.get() == "bearing":
            return self._resolve_bearing_spec()
        mount = get_mount(
            self.mount_var.get(),
            plate_width_mm=float(self.plate_width_var.get()) if self.plate_width_var.get() else None,
            bolt_count=int(self.bolt_count_var.get()) if self.bolt_count_var.get() else None,
            bolt_circle_dia_mm=float(self.bolt_circle_var.get()) if self.bolt_circle_var.get() else None,
            bolt_hole_dia_mm=float(self.bolt_hole_var.get()) if self.bolt_hole_var.get() else None,
            center_hole_dia_mm=float(self.center_hole_var.get() or 0),
        )
        
        from .mount_specs import apply_host_mount
        override = self.plate_width_override_var.get()
        mount = apply_host_mount(
            mount,
            host_mount=self.host_mount_var.get(),
            host_slot_direction=self.host_slot_dir_var.get(),
            plate_width_override=float(override) if override else None
        )
        material = get_material(
            self.material_var.get(),
            density_kg_m3=float(self.density_var.get()) if self.density_var.get() else None,
            youngs_modulus_gpa=float(self.youngs_var.get()) if self.youngs_var.get() else None,
            yield_mpa=float(self.yield_var.get()) if self.yield_var.get() else None,
            process=self.process_var.get(),
        )
        load_n = float(self.load_var.get())
        safety_factor = float(self.safety_var.get())
        overhang = float(self.overhang_var.get()) if self.overhang_var.get() else None
        thickness = mechanics.required_thickness(
            load_n=load_n,
            mount=mount,
            material=material,
            safety_factor=safety_factor,
            lever_arm_mm=overhang,
            load_type=self.load_type_var.get(),
        )
        return mount, material, load_n, safety_factor, thickness

    def _resolve_bearing_spec(self):
        """Same shape as _resolve_spec, for a block built around a bearing
        chosen from the load case."""
        load_n = float(self.load_var.get())
        safety_factor = float(self.safety_var.get())
        designation = self.bearing_var.get()
        mount, selection = auto_bearing_mount(
            load_type=self.load_type_var.get(),
            load_n=load_n,
            shaft_dia_mm=float(self.shaft_dia_var.get()),
            safety_factor=safety_factor,
            designation=None if designation == "auto" else designation,
        )
        if mount is None:
            raise ValueError(
                "; ".join(n.message for n in selection.notes if n.level == "LOUD WARN")
                or "No bearing fits this case."
            )
        material = get_material(
            self.material_var.get(),
            density_kg_m3=float(self.density_var.get()) if self.density_var.get() else None,
            youngs_modulus_gpa=float(self.youngs_var.get()) if self.youngs_var.get() else None,
            yield_mpa=float(self.yield_var.get()) if self.yield_var.get() else None,
            process=self.process_var.get(),
        )
        thickness = mechanics.required_thickness(
            load_n=load_n, mount=mount, material=material,
            safety_factor=safety_factor,
            lever_arm_mm=float(self.overhang_var.get()) if self.overhang_var.get() else None,
            load_type=self.load_type_var.get(),
        )
        # Selection notes ride along with the thickness result so they reach
        # the results panel and the report without extra plumbing.
        from . import bearings as _b
        thickness.notes.extend(selection.notes)
        thickness.notes.append(
            _b.check_seat_depth(selection.bearing, thickness.required_thickness_mm,
                                self.load_type_var.get())
        )
        self._selected_bearing = selection.bearing
        return mount, material, load_n, safety_factor, thickness

    def _preview(self) -> None:
        try:
            mount, material, load_n, safety_factor, thickness = self._resolve_spec()
        except (ValueError, TypeError) as e:
            self._show_preview_popup(f"Input error: {e}", is_error=True)
            return
        lines = [
            f"Required thickness: {thickness.required_thickness_mm:.2f} mm "
            f"(governed by {thickness.governing_limit})",
            f"[{thickness.load_type}] stress: {thickness.thickness_from_stress_mm:.2f} mm, "
            f"deflection: {thickness.thickness_from_deflection_mm:.2f} mm, "
            f"process min: {thickness.min_wall_mm:.2f} mm",
            f"Plate: {mount.plate_width_mm:.1f} x {mount.plate_height_mm:.1f} mm",
        ]
        lines.extend(f"- {n}" for n in thickness.notes)
        has_warning = any(n.level in ("WARN", "LOUD WARN") for n in thickness.notes)
        self._show_preview_popup("\n".join(lines), is_error=False, has_warning=has_warning, thickness=thickness, mount=mount)

    def _show_preview_popup(self, text: str, is_error: bool = False, has_warning: bool = False, thickness=None, mount=None) -> None:
        popup = ctk.CTkToplevel(self)
        popup.title("Preview Calculation")
        popup.geometry("550x550")
        
        # Make it modal
        popup.transient(self)
        popup.grab_set()

        color = "white"
        if is_error:
            color = "#e05252"
        elif has_warning:
            color = "#d98c00"

        if thickness and mount and not is_error:
            canvas = ctk.CTkCanvas(popup, height=220, bg="#2b2b2b", highlightthickness=0)
            canvas.pack(fill="x", padx=20, pady=(20, 10))
            popup.update_idletasks()
            
            w = canvas.winfo_width() or 510
            h = canvas.winfo_height() or 220
            cx, cy = w / 2, h / 2

            if thickness.load_type == "radial":
                # Draw cantilever beam
                beam_w = 200
                beam_h = max(10, thickness.required_thickness_mm * 2)
                wall_x = cx - beam_w / 2 - 20
                wall_y = cy - 20
                
                # Wall
                canvas.create_rectangle(wall_x - 10, wall_y - 60, wall_x, wall_y + 80, fill="#555", outline="gray")
                for i in range(5):
                    y = wall_y - 40 + i*25
                    canvas.create_line(wall_x - 10, y, wall_x - 20, y + 10, fill="gray")
                
                # Beam (undeflected outline)
                canvas.create_rectangle(wall_x, wall_y - beam_h/2, wall_x + beam_w, wall_y + beam_h/2, fill="", outline="#444", dash=(2, 4))
                
                # Deflected Beam
                deflect_px = 30  # exaggerated for visual effect
                pts = [wall_x, wall_y - beam_h/2]
                for i in range(1, 11):
                    x = wall_x + (beam_w * i / 10)
                    y = (wall_y - beam_h/2) + deflect_px * (i/10)**2
                    pts.extend([x, y])
                for i in range(10, -1, -1):
                    x = wall_x + (beam_w * i / 10)
                    y = (wall_y + beam_h/2) + deflect_px * (i/10)**2
                    pts.extend([x, y])
                canvas.create_polygon(pts, fill="#2fa572", outline="white", width=2)
                
                # Load arrow
                arrow_x = wall_x + beam_w - 10
                arrow_y1 = wall_y + beam_h/2 + deflect_px - 40
                arrow_y2 = wall_y + beam_h/2 + deflect_px + 10
                canvas.create_line(arrow_x, arrow_y1, arrow_x, arrow_y2, arrow=(ctk.LAST if 'tk' not in globals() else 'last'), fill="#e05252", width=3)
                canvas.create_text(arrow_x + 10, arrow_y1 - 10, text=f"{thickness.effective_load_n:.0f} N", fill="#e05252", anchor="w")
                
                # Lever arm dimension
                dim_y = wall_y - 40
                canvas.create_line(wall_x, dim_y, wall_x + beam_w, dim_y, fill="#777", width=1)
                canvas.create_line(wall_x, dim_y - 5, wall_x, dim_y + 5, fill="#777", width=1)
                canvas.create_line(wall_x + beam_w, dim_y - 5, wall_x + beam_w, dim_y + 5, fill="#777", width=1)
                canvas.create_text(wall_x + beam_w/2, dim_y - 10, text=f"Lever arm: {thickness.lever_arm_mm:.1f} mm", fill="white")
                
                # Deflection label
                canvas.create_line(wall_x + beam_w + 10, wall_y, wall_x + beam_w + 10, wall_y + deflect_px, fill="cyan", dash=(2, 2))
                canvas.create_text(wall_x + beam_w + 15, wall_y + deflect_px/2, text=f"Defl: {thickness.thickness_from_deflection_mm:.1f} mm", fill="cyan", anchor="w")

            else:
                # Axial: show punch-through shear
                plate_h = 140
                plate_w = max(20, thickness.required_thickness_mm * 3)
                px = cx - plate_w/2
                py = cy
                
                # Plate
                canvas.create_rectangle(px, py - plate_h/2, px + plate_w, py + plate_h/2, fill="#2fa572", outline="white", width=2)
                
                # Motor body
                canvas.create_rectangle(px + plate_w, py - 40, px + plate_w + 80, py + 40, fill="#333", outline="gray")
                canvas.create_text(px + plate_w + 40, py, text="Motor", fill="gray")
                
                # Screw head pulling through
                hole_y = py - 30
                screw_w = 40
                head_w = 12
                head_h = 24
                # screw shaft
                canvas.create_rectangle(px - head_w, hole_y - 4, px + screw_w, hole_y + 4, fill="#888", outline="black")
                # screw head
                canvas.create_rectangle(px - head_w, hole_y - head_h/2, px, hole_y + head_h/2, fill="#aaa", outline="black")
                
                # Shear lines
                canvas.create_line(px, hole_y - head_h/2, px + plate_w, hole_y - head_h/2, fill="red", dash=(4, 4), width=2)
                canvas.create_line(px, hole_y + head_h/2, px + plate_w, hole_y + head_h/2, fill="red", dash=(4, 4), width=2)
                
                # Force arrows pulling screw out
                canvas.create_line(px - head_w - 20, hole_y, px - head_w, hole_y, arrow=(ctk.LAST if 'tk' not in globals() else 'last'), fill="#e05252", width=3)
                canvas.create_text(px - head_w - 30, hole_y, text=f"{thickness.effective_load_n:.0f} N\n(thrust)", fill="#e05252", anchor="e")
                
                # Annotation
                canvas.create_text(cx, py + plate_h/2 + 20, text=f"Governing limit: {thickness.governing_limit}", fill="white")

        label = ctk.CTkLabel(
            popup, 
            text=text, 
            justify="left", 
            anchor="nw", 
            text_color=color,
            wraplength=460
        )
        label.pack(fill="both", expand=True, padx=20, pady=10)
        
        btn = ctk.CTkButton(popup, text="Close", command=popup.destroy)
        btn.pack(pady=(0, 20))

    # ---- generation ---------------------------------------------------

    def _start_generate(self) -> None:
        if self.generation_running:
            return
        try:
            mount, material, load_n, safety_factor, thickness = self._resolve_spec()
        except (ValueError, TypeError) as e:
            self.status_label.configure(text=f"Input error: {e}", text_color="#e05252")
            return

        self.output_dir = default_output_dir(
            self.mount_var.get(), self.material_var.get(), base=self.output_base_var.get() or "output"
        )
        self.generation_running = True
        self.generate_button.configure(state="disabled", text="Working...")
        self.open_folder_button.configure(state="disabled")
        self._set_results_text("")
        self.overall_label.configure(text="")
        self.preview_image_label.configure(image=None, text="(generating...)")
        self.status_label.configure(
            text=f"Output folder: {self.output_dir}\nSubmitting to Zoo Agent API...", text_color=("black", "white")
        )

        thread = threading.Thread(
            target=self._run_pipeline,
            args=(mount, material, load_n, safety_factor, thickness, self.export_step_var.get()),
            daemon=True,
        )
        thread.start()

    def _run_pipeline(self, mount, material, load_n, safety_factor, thickness, do_export_step: bool) -> None:
        # Catches *any* exception, not just our own error types -- a
        # background thread that dies on an unexpected error otherwise
        # leaves the GUI stuck on "Working..." forever with no explanation,
        # which is exactly what happened here (see comment in
        # _show_preview_image for the specific bug that triggered it).
        try:
            self._run_pipeline_inner(mount, material, load_n, safety_factor, thickness, do_export_step)
        except Exception as e:
            traceback.print_exc()
            self.after(0, lambda: self._on_generate_error(f"{type(e).__name__}: {e}"))

    def _build_assembly(self, mount, thickness, kcl_code):
        """Write the to-scale assembly and an exploded copy for the render.

        Returns the exploded main.kcl, or None if anything went wrong -- a
        failed assembly costs the context view, never the part."""
        from . import assembly as asm

        try:
            base = get_mount(self.mount_var.get()) if self.mount_var.get() in MOUNTS else mount
        except Exception:
            base = mount
        bearing = getattr(self, "_selected_bearing", None)
        if self.mount_var.get() != "bearing":
            bearing = None
        face = self.mounting_face_var.get()
        t_mm = thickness.required_thickness_mm

        try:
            main, _ = asm.write_assembly(
                self.output_dir / "assembly", mount, t_mm, kcl_code,
                bearing=bearing, face=face, base_mount=base,
            )
            generate._write_project_toml(self.output_dir / "assembly")
            exploded, _ = asm.write_assembly(
                self.output_dir / "assembly-exploded", mount, t_mm, kcl_code,
                bearing=bearing, face=face, base_mount=base,
                explode_mm=asm.DEFAULT_EXPLODE_MM,
            )
            generate._write_project_toml(self.output_dir / "assembly-exploded")
            return exploded
        except Exception:
            traceback.print_exc()
            return None

    def _run_pipeline_inner(self, mount, material, load_n, safety_factor, thickness, do_export_step: bool) -> None:
        def on_status(elapsed: float, status: str) -> None:
            self.after(0, lambda: self.status_label.configure(text=f"Generating... {status} ({elapsed:.0f}s elapsed)"))

        prompt = generate.build_prompt(mount, material, thickness.required_thickness_mm)
        kcl_code = generate.generate_kcl(prompt, on_status=on_status)
        kcl_path = generate.write_kcl_project(kcl_code, self.output_dir)

        # Render the assembly rather than the bare plate. The mount alone does
        # not answer the questions people actually have -- which way the shaft
        # points, whether the motor clears the extrusion, where the bearing
        # sits. Exploded, because a 1mm plate under a 40mm motor is correct
        # and invisible.
        self.after(0, lambda: self.status_label.configure(text="Building assembly..."))
        render_path = self._build_assembly(mount, thickness, kcl_code) or kcl_path

        self.after(0, lambda: self.status_label.configure(text="Rendering preview..."))
        preview_path = Path(tempfile.gettempdir()) / f"zoomounter_preview_{uuid.uuid4().hex}.png"
        generate.snapshot_preview(render_path, preview_path)
        # Scheduled onto the main thread via .after(), not called directly --
        # this background thread must not touch the file after this point
        # (see _show_preview_image for why, and where the cleanup happens).
        self.after(0, lambda: self._show_preview_image(preview_path))

        if not do_export_step:
            self.after(0, lambda: self._on_preview_only_done())
            return

        self.after(0, lambda: self.status_label.configure(text="Executing KCL into a STEP file via Zoo CLI..."))
        step_path = generate.export_step(kcl_path, self.output_dir)
        self.after(0, lambda: self.status_label.configure(text="Verifying against Zoo File Format API..."))
        result = verify.verify(step_path, mount, material, thickness.required_thickness_mm)

        report_path = self.output_dir / "inspection_report.md"
        # The on-screen preview lives in a temp file; the report needs one
        # that survives alongside it.
        report_preview = self.output_dir / "preview.png"
        try:
            generate.snapshot_preview(kcl_path, report_preview)
        except Exception:
            report_preview = None
        write_report(
            report_path, mount.name, material.name, load_n, safety_factor,
            thickness, result, preview_path=report_preview,
        )
        self.after(0, lambda: self._on_generate_done(result, report_path))

    def _on_generate_error(self, message: str) -> None:
        self.generation_running = False
        self.generate_button.configure(state="normal", text="Generate && Verify")
        self.status_label.configure(text=f"Error: {message}", text_color="#e05252")

    def _on_preview_only_done(self) -> None:
        self.generation_running = False
        self.generate_button.configure(state="normal", text="Generate && Verify")
        self.open_folder_button.configure(state="normal")
        self.status_label.configure(
            text=f"Preview only -- no STEP file or verification generated. Project: {self.output_dir}",
            text_color=("black", "white"),
        )
        self._set_results_text("(STEP export was unchecked -- nothing to verify)")

    def _on_generate_done(self, result: verify.VerificationResult, report_path: Path) -> None:
        self.generation_running = False
        self.generate_button.configure(state="normal", text="Generate && Verify")
        self.open_folder_button.configure(state="normal")
        self.status_label.configure(text=f"Done. Report: {report_path}", text_color=("black", "white"))

        lines = []
        for check in result.checks:
            mark = "PASS" if check.passed else "FAIL"
            lines.append(f"[{mark}] {check.name}\n    {check.detail}\n")
        if result.mass_g is not None:
            lines.append(
                f"Mass: {result.mass_g:.2f} g (reported property, not an independent "
                f"check -- it is volume x your supplied density)\n"
            )
        self._set_results_text("\n".join(lines))
        if result.passed:
            self.overall_label.configure(text="PASS", text_color="#2fa572")
        else:
            self.overall_label.configure(text="FAIL", text_color="#e05252")


def main() -> int:
    load_environment()
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
