from __future__ import annotations

import tkinter as tk
from pathlib import Path

from ..client import ApiError, ApiUnreachable
from ..theme import PALETTE, SPACING, TYPE
from ..widgets import Card, SectionTitle, make_button, responsive_wrap


class ExecutionViewMixin:
    """Vista de escritorio y acciones relacionadas."""

    def _build_execute_view(self, parent: tk.Frame) -> None:
        """Vista 3: ejecutar el modelo y ver la huella del resultado."""
        parent.columnconfigure(0, weight=1)

        title = SectionTitle(
            parent,
            "Ejecutar modelo",
            "Apila una o varias fuentes compatibles, aplica las reglas y guarda la huella.",
        )
        title.grid(row=0, column=0, sticky="ew", pady=(0, SPACING.lg))
        self.titles["execute"] = title

        card = Card(parent)
        card.grid(row=1, column=0, sticky="ew", pady=(0, SPACING.lg))
        row = tk.Frame(card.body, background=PALETTE.surface)
        row.pack(fill="x")

        tk.Label(row, text="Modelo:", foreground=PALETTE.text,
                 background=PALETTE.surface, font=TYPE.font(TYPE.size_base),
                 ).pack(side="left", padx=(0, SPACING.sm))
        self.exec_model = tk.StringVar(value="1")
        tk.Entry(row, textvariable=self.exec_model, width=6,
                 background=PALETTE.surface_alt, foreground=PALETTE.text,
                 insertbackground=PALETTE.text, relief="flat",
                 font=TYPE.font(TYPE.size_base), highlightthickness=1,
                 highlightbackground=PALETTE.border, highlightcolor=PALETTE.focus,
                 ).pack(side="left", ipady=SPACING.xs)

        tk.Label(row, text="Fuentes (separa con ;):", foreground=PALETTE.text,
                 background=PALETTE.surface, font=TYPE.font(TYPE.size_base),
                 ).pack(side="left", padx=(SPACING.md, SPACING.sm))
        self.exec_source = tk.StringVar(value="")
        tk.Entry(row, textvariable=self.exec_source,
                 background=PALETTE.surface_alt, foreground=PALETTE.text,
                 insertbackground=PALETTE.text, relief="flat",
                 font=TYPE.font(TYPE.size_base), highlightthickness=1,
                 highlightbackground=PALETTE.border, highlightcolor=PALETTE.focus,
                 ).pack(side="left", fill="x", expand=True, ipady=SPACING.xs)

        self.exec_button = make_button(
            card.body, "Ejecutar", self._do_execute, kind="primary"
        )
        self.exec_button.pack(anchor="w", pady=(SPACING.md, 0))
        self.exec_status = responsive_wrap(tk.Label(
            card.body, text="", foreground=PALETTE.disabled_text,
            background=PALETTE.surface, font=TYPE.font(TYPE.size_base),
            anchor="w", justify="left",
        ))
        self.exec_status.pack(anchor="w", fill="x", pady=(SPACING.sm, 0))

        detail_card = Card(parent)
        detail_card.grid(row=2, column=0, sticky="nsew")
        parent.rowconfigure(2, weight=1)
        tk.Label(
            detail_card.body, text="Detalle de la ejecución",
            foreground=PALETTE.text, background=PALETTE.surface,
            font=TYPE.font(TYPE.size_base, weight=TYPE.weight_semibold), anchor="w",
        ).pack(anchor="w", pady=(0, SPACING.sm))

        detail_frame = tk.Frame(detail_card.body, background=PALETTE.surface)
        detail_frame.pack(fill="both", expand=True)
        self.exec_detail = tk.Text(
            detail_frame, height=8, background=PALETTE.surface_alt,
            foreground=PALETTE.text, insertbackground=PALETTE.text, relief="flat",
            font=TYPE.font(TYPE.size_sm, mono=True), highlightthickness=1,
            highlightbackground=PALETTE.border, padx=SPACING.md, pady=SPACING.md,
            wrap="word",
        )
        self.exec_detail.pack(fill="both", expand=True)
        # Estado vacío explícito (FE-004): una caja en blanco parece rota.
        self._set_exec_detail(
            "Aún no has ejecutado el modelo.\n\n"
            "Indica el modelo y fuentes cargadas separadas por punto y coma; "
            "pulsa «Ejecutar» "
            "para ver aquí el run_id, las columnas, el número de filas y la huella."
        )

    def _set_exec_detail(self, text: str) -> None:
        """Reemplaza el detalle monoespaciado por `text`, en solo lectura."""
        self.exec_detail.configure(state="normal")
        self.exec_detail.delete("1.0", "end")
        self.exec_detail.insert("1.0", text)
        self.exec_detail.configure(state="disabled")

    def _do_execute(self) -> None:
        model_id = self.exec_model.get().strip()
        sources = [
            Path(item.strip()).name
            for item in self.exec_source.get().split(";")
            if item.strip()
        ]
        self.exec_source.set("; ".join(sources))
        if not model_id.isdigit() or not sources:
            self._set_exec_status(
                "Indica un modelo (número) y al menos una fuente.", PALETTE.error
            )
            return
        self.exec_button.configure(state="disabled")
        self._set_exec_status("Ejecutando…", PALETTE.info)

        def worker():
            try:
                request_sources = sources[0] if len(sources) == 1 else sources
                result = self.client.execute(int(model_id), request_sources)
            except ApiError as exc:
                text = f"✕ {exc.label}: {exc.message}"
                self._run_on_ui(lambda: self._set_exec_status(text, PALETTE.error))
                self._run_on_ui(lambda: self.exec_button.configure(state="normal"))
                return
            except ApiUnreachable as exc:
                text = str(exc)
                self._run_on_ui(lambda: self._set_exec_status(text, PALETTE.error))
                self._run_on_ui(lambda: self.exec_button.configure(state="normal"))
                return
            self._run_on_ui(lambda: self._show_exec_result(result))
            self._run_on_ui(lambda: self.exec_button.configure(state="normal"))

        self._start_worker(worker)

    def _show_exec_result(self, result: dict) -> None:
        self._set_exec_status(
            f"✓ Ejecución {result['run_id']}: {result['rows_out']} filas. "
            f"Huella {result['hash'][:16]}…",
            PALETTE.success,
        )
        detail = {
            "run_id": result["run_id"],
            "model_id": result["model_id"],
            "fuentes": result.get("sources", []),
            "operación": result.get("operation", "single_source"),
            "columnas": result["columns"],
            "filas": result["rows_out"],
            "hash": result["hash"],
        }
        import json as _json

        self._set_exec_detail(_json.dumps(detail, ensure_ascii=False, indent=2))

    def _set_exec_status(self, text: str, color: str) -> None:
        self.exec_status.configure(text=text, foreground=color)
