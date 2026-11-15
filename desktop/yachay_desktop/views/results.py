from __future__ import annotations

import tkinter as tk

from ..client import ApiError, ApiUnreachable
from ..theme import PALETTE, SPACING, TYPE
from ..widgets import Card, SectionTitle, make_button, make_table, responsive_wrap


class ResultsViewMixin:
    """Vista de escritorio y acciones relacionadas."""

    def _build_results_view(self, parent: tk.Frame) -> None:
        """Vista 4: ejecuciones del modelo y comparación de dos."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        title = SectionTitle(
            parent,
            "Resultados",
            "Historial de ejecuciones y qué cambió entre dos de ellas.",
        )
        title.grid(row=0, column=0, sticky="ew", pady=(0, SPACING.lg))
        self.titles["results"] = title

        controls = Card(parent)
        controls.grid(row=1, column=0, sticky="ew", pady=(0, SPACING.lg))
        row = tk.Frame(controls.body, background=PALETTE.surface)
        row.pack(fill="x")
        tk.Label(row, text="Modelo:", foreground=PALETTE.text,
                 background=PALETTE.surface, font=TYPE.font(TYPE.size_base),
                 ).pack(side="left", padx=(0, SPACING.sm))
        self.results_model = tk.StringVar(value="1")
        tk.Entry(row, textvariable=self.results_model, width=6,
                 background=PALETTE.surface_alt, foreground=PALETTE.text,
                 insertbackground=PALETTE.text, relief="flat",
                 font=TYPE.font(TYPE.size_base), highlightthickness=1,
                 highlightbackground=PALETTE.border, highlightcolor=PALETTE.focus,
                 ).pack(side="left", ipady=SPACING.xs)
        make_button(row, "Ver ejecuciones", self._load_runs, kind="secondary").pack(
            side="left", padx=SPACING.sm
        )
        make_button(row, "Comparar seleccionadas", self._do_diff, kind="primary").pack(
            side="left"
        )
        self.results_status = responsive_wrap(tk.Label(
            controls.body, text="", foreground=PALETTE.disabled_text,
            background=PALETTE.surface, font=TYPE.font(TYPE.size_base),
            anchor="w", justify="left",
        ))
        self.results_status.pack(anchor="w", fill="x", pady=(SPACING.sm, 0))

        card = Card(parent)
        card.grid(row=2, column=0, sticky="nsew")
        self.runs_tree, table = make_table(
            card.body,
            [
                ("run", "Ejecución", 100, 90),
                ("when", "Cuándo", 180, 150),
                ("rows", "Filas", 80, 60),
                ("hash", "Huella", 300, 140),
            ],
            selectmode="extended",
            stretch_column="hash",
        )
        table.grid(row=0, column=0, sticky="nsew")
        card.body.rowconfigure(0, weight=1)
        card.body.columnconfigure(0, weight=1)
        self._runs_by_item: dict[str, int] = {}

    def _load_runs(self) -> None:
        model_id = self.results_model.get().strip()
        if not model_id.isdigit():
            self._set_results_status("Indica un número de modelo.", PALETTE.error)
            return

        def worker():
            try:
                runs = self.client.runs(int(model_id))
            except (ApiError, ApiUnreachable) as exc:
                text = getattr(exc, "message", str(exc))
                self._run_on_ui(lambda: self._set_results_status(text, PALETTE.error))
                return
            self._run_on_ui(lambda: self._render_runs(runs))

        self._start_worker(worker)

    def _render_runs(self, runs: list[dict]) -> None:
        self.runs_tree.delete(*self.runs_tree.get_children())
        self._runs_by_item = {}
        for run in runs:
            when = self._format_when(run.get("created_at"))
            item = self.runs_tree.insert(
                "", "end",
                values=(run["run_id"], when, run["rows"], run["hash"][:32] + "…"),
            )
            self._runs_by_item[item] = run["run_id"]
        self._set_results_status(
            f"{len(runs)} ejecución(es). Selecciona dos y pulsa «Comparar».",
            PALETTE.info if runs else PALETTE.disabled_text,
        )

    def _format_when(self, value) -> str:
        from yachay_desktop.formatting import format_epoch

        return format_epoch(value) if value else "—"

    def _do_diff(self) -> None:
        selected = [self._runs_by_item.get(i) for i in self.runs_tree.selection()]
        selected = [s for s in selected if s is not None]
        if len(selected) != 2:
            self._set_results_status("Selecciona exactamente dos ejecuciones.", PALETTE.warning)
            return
        before, after = sorted(selected)

        def worker():
            try:
                result = self.client.diff(before, after)
            except ApiError as exc:
                text = f"✕ {exc.label}: {exc.message}"
                self._run_on_ui(lambda: self._set_results_status(text, PALETTE.error))
                return
            except ApiUnreachable as exc:
                text = str(exc)
                self._run_on_ui(lambda: self._set_results_status(text, PALETTE.error))
                return
            self._run_on_ui(lambda: self._show_diff(before, after, result))

        self._start_worker(worker)

    def _show_diff(self, before: int, after: int, result: dict) -> None:
        if result["changed"]:
            text = f"⚠ Cambió entre {before} y {after}: {result['reason']}"
            color = PALETTE.warning
        else:
            text = f"✓ Sin cambios entre {before} y {after}."
            color = PALETTE.success
        self._set_results_status(text, color)

    def _set_results_status(self, text: str, color: str) -> None:
        self.results_status.configure(text=text, foreground=color)
