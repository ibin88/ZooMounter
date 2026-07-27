"""Drop a generated part into an existing Zoo Design Studio project.

The friction this removes: you're part-way through a design, you realise you
need a motor mount, and the tool that makes one asks you where your project
is, what to call the file, and then leaves you to hand-write the import. By
the time you've done that you may as well have modelled the plate yourself.

So instead: find the project by walking up from wherever you are (the same
trick git uses for `.git`), write the part alongside the ones already there,
and add the import line to `main.kcl`. Run `zoomounter add` inside your
project and the part shows up in the assembly.

Conventions are read from the project rather than imposed on it. Zoo projects
put every part in a flat file at the project root, each ending in
`export partName = body`, and `main.kcl` pulls them in with
`import partName from "partName.kcl"`. Generated parts follow suit, so they're
indistinguishable from hand-written ones and you can edit them freely.
"""

import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_MARKER = "project.toml"
DEFAULT_ENTRY = "main.kcl"

# A top-level KCL assignment: `name = ...` at the start of a line.
_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_]\w*)\s*=", re.MULTILINE)
# An existing import line, so we can find where the import block ends.
_IMPORT_RE = re.compile(r"^import\s+.*$", re.MULTILINE)


class ProjectError(RuntimeError):
    pass


def _read_preserving_newlines(path: Path) -> tuple[str, str]:
    """Read a file without translating line endings, and report which it uses.

    Matters because we're editing a file someone else wrote. `read_text()` /
    `write_text()` round-trips LF into CRLF on Windows, which rewrites every
    line of the file to add one import -- turning a 3-line change into a
    236-line diff and burying the actual edit.
    """
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read()
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, newline


def _write_preserving_newlines(path: Path, text: str, newline: str) -> None:
    """Write back using the file's own line ending, normalising ours to match."""
    normalised = text.replace("\r\n", "\n").replace("\n", newline)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(normalised)


@dataclass
class ZooProject:
    root: Path
    entry: Path  # main.kcl, or whatever default_file says

    @property
    def name(self) -> str:
        return self.root.name


def find_project(start: Path | None = None) -> ZooProject | None:
    """Walk up from `start` looking for a Zoo project, like git finds .git.

    Returns None rather than raising -- not being in a project is a normal
    situation the caller should handle with a helpful message, not a stack
    trace.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        marker = candidate / PROJECT_MARKER
        if not marker.exists():
            continue
        entry_name = _read_default_file(marker) or DEFAULT_ENTRY
        entry = candidate / entry_name
        if entry.exists():
            return ZooProject(root=candidate, entry=entry)
        # A project.toml with no entry file is still a project -- the entry
        # just hasn't been created yet.
        return ZooProject(root=candidate, entry=candidate / DEFAULT_ENTRY)
    return None


def _read_default_file(project_toml: Path) -> str | None:
    """Pull `default_file` out of project.toml without needing a TOML parser
    for one key. Zoo writes it as a plain top-level string."""
    try:
        text = project_toml.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r'^\s*default_file\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


def last_assignment(kcl_code: str) -> str | None:
    """Name of the final top-level assignment -- the finished solid.

    The Agent API returns a script that builds up a part through several
    intermediate variables and usually ends with an `appearance(...)` call
    assigned to a final name. That last one is what we want to export.
    """
    names = _ASSIGNMENT_RE.findall(kcl_code)
    return names[-1] if names else None


def wrap_as_module(kcl_code: str, export_name: str) -> str:
    """Add the `export` line that makes generated KCL importable.

    Agent API output is a standalone script -- it renders on its own but
    exports nothing, so `import x from "x.kcl"` against it fails. One line
    fixes that, and doing it here means the generated file follows the same
    convention as every hand-written part in the project.
    """
    body = kcl_code.rstrip()
    if re.search(rf"^export\s+{re.escape(export_name)}\s*=", body, re.MULTILINE):
        return body + "\n"

    final = last_assignment(body)
    if final is None:
        raise ProjectError(
            "The generated KCL has no top-level assignment to export, so it "
            "can't be imported as a module. Write it out with --output-dir "
            "and inspect it."
        )
    if final == export_name:
        # Already the right name; just mark it exported.
        return re.sub(
            rf"^{re.escape(final)}\s*=", f"export {final} =", body, count=1, flags=re.MULTILINE
        ) + "\n"
    return f"{body}\n\nexport {export_name} = {final}\n"


def add_part(
    project: ZooProject,
    part_name: str,
    kcl_code: str,
    comment: str = "",
) -> tuple[Path, bool]:
    """Write `part_name.kcl` into the project and import it from the entry file.

    Returns (path_to_part, entry_was_modified). Safe to run twice: the part
    file is overwritten (so regenerating with new numbers works), but the
    import is only added if it isn't already there.
    """
    # Match whatever line ending the project's entry file already uses, so a
    # generated part looks native alongside the hand-written ones.
    newline = "\n"
    if project.entry.exists():
        _, newline = _read_preserving_newlines(project.entry)

    part_path = project.root / f"{part_name}.kcl"
    _write_preserving_newlines(part_path, wrap_as_module(kcl_code, part_name), newline)
    entry_modified = _ensure_import(project.entry, part_name, comment)
    return part_path, entry_modified


def _ensure_import(entry: Path, part_name: str, comment: str) -> bool:
    """Add `import part from "part.kcl"` plus an instance line, once."""
    if not entry.exists():
        raise ProjectError(
            f"{entry.name} not found in the project, so there's nothing to wire "
            f"the part into. The part file was still written."
        )

    text, newline = _read_preserving_newlines(entry)
    text = text.replace("\r\n", "\n")  # work in LF, restore on write
    import_line = f'import {part_name} from "{part_name}.kcl"'
    if import_line in text:
        return False

    imports = list(_IMPORT_RE.finditer(text))
    if imports:
        # Slot in after the existing import block, keeping it tidy.
        insert_at = imports[-1].end()
        text = text[:insert_at] + "\n" + import_line + text[insert_at:]
    else:
        # No imports yet -- go after the @settings line if there is one.
        settings = re.search(r"^@settings\([^)]*\)\s*$", text, re.MULTILINE)
        insert_at = settings.end() if settings else 0
        text = text[:insert_at] + "\n\n" + import_line + text[insert_at:]

    instance_block = f"\n\n// {comment}\n" if comment else "\n\n"
    instance_block += (
        f"// Generated by ZooMounter. Position it with |> translate(...) as needed.\n"
        f"{part_name}\n"
    )
    text = text.rstrip() + instance_block

    _write_preserving_newlines(entry, text, newline)
    return True
