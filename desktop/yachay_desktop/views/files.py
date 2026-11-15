from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from ..client import ApiError, ApiUnreachable, check_name
from ..formatting import human_bytes
from ..theme import PALETTE, SPACING, TYPE
from ..ui_constants import MAX_REQUEST_BYTES
from ..widgets import Card, SectionTitle, make_button, make_table, responsive_wrap


class FilesViewMixin:
    """Vista de escritorio y acciones relacionadas."""

    def _build_files_view(self, parent: tk.Frame) -> None:
        """Vista 1: cargar un archivo y ver su perfil (M1)."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        title = SectionTitle(
            parent,
            "Cargar archivo",
            "Deposítalo y perfílalo; quedará listo para crear el modelo.",
        )
        title.grid(row=0, column=0, sticky="ew", pady=(0, SPACING.lg))
        self.titles["files"] = title

        picker = Card(parent)
        picker.grid(row=1, column=0, sticky="ew", pady=(0, SPACING.lg))
        row = tk.Frame(picker.body, background=PALETTE.surface)
        row.pack(fill="x")
        self.files_path = tk.StringVar(value="")
        tk.Label(
            row, text="Archivo:", foreground=PALETTE.text,
            background=PALETTE.surface, font=TYPE.font(TYPE.size_base),
        ).pack(side="left", padx=(0, SPACING.sm))
        entry = tk.Entry(
            row, textvariable=self.files_path,
            background=PALETTE.surface_alt, foreground=PALETTE.text,
            insertbackground=PALETTE.text, relief="flat",
            font=TYPE.font(TYPE.size_base),
            highlightthickness=1, highlightbackground=PALETTE.border,
            highlightcolor=PALETTE.focus,
        )
        entry.pack(side="left", fill="x", expand=True, ipady=SPACING.xs)
        make_button(row, "Elegir…", self._choose_profile_file, kind="secondary").pack(
            side="left", padx=(SPACING.sm, 0)
        )
        self.profile_button = make_button(
            picker.body, "Cargar y perfilar", self._do_profile, kind="primary"
        )
        self.profile_button.pack(anchor="w", pady=(SPACING.md, 0))
        self.files_status = responsive_wrap(tk.Label(
            picker.body, text="", foreground=PALETTE.disabled_text,
            background=PALETTE.surface, font=TYPE.font(TYPE.size_base),
            anchor="w", justify="left",
        ))
        self.files_status.pack(anchor="w", fill="x", pady=(SPACING.sm, 0))

        card = Card(parent)
        card.grid(row=2, column=0, sticky="nsew")
        self.profile_tree, table = make_table(
            card.body,
            [
                ("column", "Columna", 190, 140),
                ("nulls", "Nulos", 65, 55),
                ("unique", "Únicos", 70, 60),
                ("dups", "Duplicados", 100, 80),
                ("type", "Tipo", 85, 70),
                ("range", "Rango", 170, 120),
            ],
            stretch_column="range",
        )
        table.grid(row=0, column=0, sticky="nsew")
        card.body.rowconfigure(0, weight=1)
        card.body.columnconfigure(0, weight=1)

    def _choose_profile_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Elegir archivo",
            filetypes=[
                ("Hojas y documentos", "*.csv *.xlsx *.ods *.txt *.docx *.pdf"),
                ("Todos (nombres admitidos)", "*.csv *.xlsx *.ods *.txt *.docx *.pdf"),
            ],
        )
        if path:
            self.files_path.set(path)

    def _do_profile(self) -> None:
        path = Path(self.files_path.get().strip())
        if not path.is_file():
            self._set_files_status("Selecciona un archivo válido.", PALETTE.error)
            return
        try:
            size = path.stat().st_size
        except OSError as exc:
            self._set_files_status(f"No se pudo leer el archivo: {exc}", PALETTE.error)
            return
        try:
            check_name(path.name)
        except ApiError as exc:
            self._set_files_status(f"✕ {exc.message}", PALETTE.error)
            return
        if size > MAX_REQUEST_BYTES:
            self._set_files_status(
                f"✕ El archivo supera el límite de {human_bytes(MAX_REQUEST_BYTES)}.",
                PALETTE.error,
            )
            return
        self.profile_button.configure(state="disabled")
        self._set_files_status(
            f"Subiendo {path.name} ({human_bytes(size)})…", PALETTE.info
        )

        def worker():
            try:
                data = path.read_bytes()
                if len(data) > MAX_REQUEST_BYTES:
                    raise ApiError(
                        "payload_too_large",
                        f"El archivo supera el límite de {human_bytes(MAX_REQUEST_BYTES)}.",
                    )
                result = self.client.upload(path.name, data)
            except ApiError as exc:
                text = f"✕ {exc.label}: {exc.message}"
                self._run_on_ui(lambda: self._set_files_status(text, PALETTE.error))
                self._run_on_ui(lambda: self.profile_button.configure(state="normal"))
                return
            except ApiUnreachable as exc:
                text = str(exc)
                self._run_on_ui(lambda: self._set_files_status(text, PALETTE.error))
                self._run_on_ui(lambda: self.profile_button.configure(state="normal"))
                return
            except OSError as exc:
                text = f"No se pudo leer el archivo: {exc}"
                self._run_on_ui(lambda: self._set_files_status(text, PALETTE.error))
                self._run_on_ui(lambda: self.profile_button.configure(state="normal"))
                return
            name = result.get("name", path.name)
            try:
                profile = self.client.profile(name)
            except (ApiError, ApiUnreachable) as exc:
                message = getattr(exc, "message", str(exc))
                self._run_on_ui(lambda: self._set_files_status(message, PALETTE.error))
                self._run_on_ui(lambda: self.profile_button.configure(state="normal"))
                return
            self._run_on_ui(lambda: self._render_profile(name, profile))
            self._run_on_ui(lambda: self.profile_button.configure(state="normal"))

        self._start_worker(worker)

    def _render_profile(self, name: str, profile: dict) -> None:
        # Cargar archivo también deposita el archivo; sincronizarlo con Modelo
        # evita que la vista vuelva a pedir el nombre de la fuente.
        self.model_source.set(name)
        self.profile_tree.delete(*self.profile_tree.get_children())
        columns = profile.get("profile", [])
        if not columns:
            self.profile_tree.insert(
                "", "end", values=("Sin columnas perfiladas", "", "", "", "", "")
            )
            return
        for column in columns:
            rango = "—"
            if column["numeric_min"] is not None:
                rango = f"{column['numeric_min']:g} … {column['numeric_max']:g}"
            self.profile_tree.insert(
                "", "end",
                values=(column["name"], column["nulls"], column["unique"],
                        column["duplicates"], column["inferred_type"], rango),
            )
        nota = profile.get("note") or ""
        self._set_files_status(
            f"✓ {name}: {profile.get('rows', 0)} filas, "
            f"{len(profile.get('profile', []))} columnas. {nota}".strip(),
            PALETTE.success,
        )
        self._files_last = name

    def _set_files_status(self, text: str, color: str) -> None:
        self.files_status.configure(text=text, foreground=color)
