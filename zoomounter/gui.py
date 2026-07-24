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
from pathlib import Path

import customtkinter as ctk
from dotenv import load_dotenv
from PIL import Image

from . import generate, mechanics, verify
from .cli import default_output_dir, write_report
from .materials import MATERIALS, get_material
from .mount_specs import MOUNTS, get_mount

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

MOUNT_KEYS = [*MOUNTS.keys(), "custom"]
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

        self.custom_mount_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.custom_mount_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        self.custom_mount_frame.grid_columnconfigure(1, weight=1)
        self.plate_width_var = self._labeled_entry(self.custom_mount_frame, 0, "Plate width (mm)", "50")
        self.bolt_count_var = self._labeled_entry(self.custom_mount_frame, 1, "Bolt count", "4")
        self.bolt_circle_var = self._labeled_entry(self.custom_mount_frame, 2, "Bolt circle dia (mm)", "40")
        self.bolt_hole_var = self._labeled_entry(self.custom_mount_frame, 3, "Bolt hole dia (mm)", "4")
        self.center_hole_var = self._labeled_entry(self.custom_mount_frame, 4, "Center hole dia (mm, 0=none)", "0")
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
        self.output_base_var = self._labeled_entry(form, row, "Output base folder", "output")
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
        self.preview_label = ctk.CTkLabel(form, text="", justify="left", anchor="w")
        self.preview_label.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 12))
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

    def _on_mount_change(self, value: str) -> None:
        if value == "custom":
            self.custom_mount_frame.grid()
        else:
            self.custom_mount_frame.grid_remove()

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
        mount = get_mount(
            self.mount_var.get(),
            plate_width_mm=float(self.plate_width_var.get()) if self.plate_width_var.get() else None,
            bolt_count=int(self.bolt_count_var.get()) if self.bolt_count_var.get() else None,
            bolt_circle_dia_mm=float(self.bolt_circle_var.get()) if self.bolt_circle_var.get() else None,
            bolt_hole_dia_mm=float(self.bolt_hole_var.get()) if self.bolt_hole_var.get() else None,
            center_hole_dia_mm=float(self.center_hole_var.get() or 0),
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

    def _preview(self) -> None:
        try:
            mount, material, load_n, safety_factor, thickness = self._resolve_spec()
        except (ValueError, TypeError) as e:
            self.preview_label.configure(text=f"Input error: {e}", text_color="#e05252")
            return
        lines = [
            f"Required thickness: {thickness.required_thickness_mm:.2f} mm "
            f"(governed by {thickness.governing_limit})",
            f"[{thickness.load_type}] stress: {thickness.thickness_from_stress_mm:.2f} mm, "
            f"deflection: {thickness.thickness_from_deflection_mm:.2f} mm, "
            f"process min: {thickness.min_wall_mm:.2f} mm",
        ]
        lines.extend(f"- {n}" for n in thickness.notes)
        has_warning = any(n.startswith("WARNING") for n in thickness.notes)
        self.preview_label.configure(
            text="\n".join(lines),
            text_color="#d98c00" if has_warning else ("black", "white"),
        )

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

    def _run_pipeline_inner(self, mount, material, load_n, safety_factor, thickness, do_export_step: bool) -> None:
        def on_status(elapsed: float, status: str) -> None:
            self.after(0, lambda: self.status_label.configure(text=f"Generating... {status} ({elapsed:.0f}s elapsed)"))

        prompt = generate.build_prompt(mount, material, thickness.required_thickness_mm)
        kcl_code = generate.generate_kcl(prompt, on_status=on_status)
        kcl_path = generate.write_kcl_project(kcl_code, self.output_dir)

        self.after(0, lambda: self.status_label.configure(text="Rendering preview..."))
        preview_path = Path(tempfile.gettempdir()) / f"zoomounter_preview_{uuid.uuid4().hex}.png"
        generate.snapshot_preview(kcl_path, preview_path)
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
        write_report(report_path, mount.name, material.name, load_n, safety_factor, thickness, result)
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
    load_dotenv()
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
