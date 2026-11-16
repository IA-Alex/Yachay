from __future__ import annotations

import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox

from ..client import ApiError, ApiUnreachable
from ..theme import PALETTE, SPACING, TYPE
from ..widgets import Card, SectionTitle, make_button, make_table, responsive_wrap


class VisualizationsViewMixin:
    """Local dashboard/report workflow, separate from execution history."""

    def _build_visualizations_view(self, parent: tk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        title = SectionTitle(
            parent,
            "Visualizaciones",
            "Genera informes locales a partir de una ejecución guardada.",
        )
        title.grid(row=0, column=0, sticky="ew", pady=(0, SPACING.lg))
        self.titles["visualizations"] = title
        controls = Card(parent)
        controls.grid(row=1, column=0, sticky="ew", pady=(0, SPACING.lg))
        row = tk.Frame(controls.body, background=PALETTE.surface)
        row.pack(fill="x")
        tk.Label(row, text="Modelo:", foreground=PALETTE.text,
                 background=PALETTE.surface, font=TYPE.font(TYPE.size_base)).pack(
                     side="left", padx=(0, SPACING.sm))
        self.visual_model = tk.StringVar(value="1")
        tk.Entry(row, textvariable=self.visual_model, width=7,
                 background=PALETTE.surface_alt, foreground=PALETTE.text,
                 insertbackground=PALETTE.text, relief="flat",
                 font=TYPE.font(TYPE.size_base)).pack(side="left", ipady=SPACING.xs)
        make_button(row, "Cargar ejecuciones", self._load_visual_runs,
                    kind="secondary").pack(side="left", padx=SPACING.sm)
        make_button(row, "Generar informe", self._generate_visual_report,
                    kind="primary").pack(side="left")
        make_button(row, "Abrir informe", self._open_visual_report,
                    kind="secondary").pack(side="left", padx=SPACING.sm)
        make_button(row, "Borrar informe", self._delete_visual_report,
                    kind="danger").pack(side="left")
        self.visual_status = responsive_wrap(tk.Label(
            controls.body, text="", foreground=PALETTE.disabled_text,
            background=PALETTE.surface, font=TYPE.font(TYPE.size_base),
            anchor="w", justify="left",
        ))
        self.visual_status.pack(anchor="w", fill="x", pady=(SPACING.sm, 0))
        card = Card(parent)
        card.grid(row=2, column=0, sticky="nsew")
        self.visual_runs, table = make_table(
            card.body,
            [("run", "Ejecución", 110, 90), ("when", "Cuándo", 180, 150),
             ("rows", "Filas", 90, 70), ("columns", "Columnas", 260, 150),
             ("stored", "Datos guardados", 120, 100)],
            stretch_column="columns",
        )
        table.grid(row=0, column=0, sticky="nsew")
        card.body.rowconfigure(0, weight=1)
        card.body.columnconfigure(0, weight=1)
        self._visual_run_ids: dict[str, int] = {}

    def _load_visual_runs(self) -> None:
        raw = self.visual_model.get().strip()
        if not raw.isdigit():
            self._set_visual_status("Indica un número de modelo.", PALETTE.error)
            return

        def worker() -> None:
            try:
                runs = self.client.runs(int(raw))
            except (ApiError, ApiUnreachable) as exc:
                message = getattr(exc, "message", str(exc))
                self._run_on_ui(lambda: self._set_visual_status(
                    message, PALETTE.error))
                return
            self._run_on_ui(lambda: self._render_visual_runs(runs))

        self._start_worker(worker)

    def _render_visual_runs(self, runs: list[dict]) -> None:
        self.visual_runs.delete(*self.visual_runs.get_children())
        self._visual_run_ids = {}
        for run in runs:
            item = self.visual_runs.insert(
                "", "end", values=(run["run_id"], self._format_when(run.get("created_at")),
                                   run["rows"], ", ".join(run.get("columns", [])),
                                   "Sí" if run.get("artifact_available") else "No"),
            )
            self._visual_run_ids[item] = run["run_id"]
        self._set_visual_status(
            f"{len(runs)} ejecución(es). Selecciona una para visualizar.",
            PALETTE.info if runs else PALETTE.disabled_text,
        )

    def _selected_visual_run(self) -> tuple[int, int] | None:
        selected = self.visual_runs.selection()
        if len(selected) != 1:
            self._set_visual_status("Selecciona una ejecución.", PALETTE.warning)
            return None
        model = self.visual_model.get().strip()
        if not model.isdigit():
            self._set_visual_status("Indica un número de modelo.", PALETTE.error)
            return None
        return int(model), self._visual_run_ids[selected[0]]

    def _generate_visual_report(self) -> None:
        selection = self._selected_visual_run()
        if selection is None:
            return
        model_id, run_id = selection
        report_path = self._report_path(run_id)
        if report_path.exists() and not messagebox.askyesno(
            "Regenerar informe", "El informe ya existe. ¿Deseas reemplazarlo?", parent=self
        ):
            return

        def worker() -> None:
            try:
                self.client.generate_report(model_id, run_id)
            except (ApiError, ApiUnreachable) as exc:
                message = getattr(exc, "message", str(exc))
                self._run_on_ui(lambda: self._set_visual_status(
                    message, PALETTE.error))
                return
            self._run_on_ui(lambda: self._report_generated(run_id))

        self._start_worker(worker)

    def _report_generated(self, run_id: int) -> None:
        self._set_visual_status("Informe generado correctamente.", PALETTE.success)
        messagebox.showinfo("Visualización", "Informe generado. Aceptar", parent=self)

    def _open_visual_report(self) -> None:
        selection = self._selected_visual_run()
        if selection is None:
            return
        path = self._report_path(selection[1])
        if not path.is_file():
            self._set_visual_status("Genera el informe antes de abrirlo.", PALETTE.warning)
            return
        webbrowser.open(path.as_uri())
        self._set_visual_status("Informe abierto en el navegador local.", PALETTE.success)

    def _delete_visual_report(self) -> None:
        selection = self._selected_visual_run()
        if selection is None:
            return
        model_id, run_id = selection
        if not messagebox.askyesno(
            "Borrar informe", "Se borrará el HTML; los datos de la ejecución se conservan.",
            parent=self,
        ):
            return

        def worker() -> None:
            try:
                self.client.delete_report(model_id, run_id)
            except (ApiError, ApiUnreachable) as exc:
                message = getattr(exc, "message", str(exc))
                self._run_on_ui(lambda: self._set_visual_status(
                    message, PALETTE.error))
                return
            self._run_on_ui(lambda: self._set_visual_status(
                "Informe borrado. Los datos de la ejecución siguen guardados.",
                PALETTE.success,
            ))
            self._run_on_ui(lambda: messagebox.showinfo(
                "Visualización", "Informe borrado. Aceptar", parent=self
            ))

        self._start_worker(worker)

    def _report_path(self, run_id: int) -> Path:
        return self.model_outputs_dir / f"report-{run_id}.html"

    def _set_visual_status(self, text: str, color: str) -> None:
        self.visual_status.configure(text=text, foreground=color)
