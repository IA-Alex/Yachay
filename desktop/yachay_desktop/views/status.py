from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..client import Job
from ..theme import PALETTE, SPACING, TYPE
from ..ui_constants import DEFAULT_LIMIT, FUNCTION_CATALOG
from ..widgets import Card, SectionTitle, make_button, make_table, responsive_wrap


class StatusViewMixin:
    """Vista de escritorio y acciones relacionadas."""

    def _build_status_view(self, parent: tk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        title = SectionTitle(
            parent,
            "Panel de estado",
            "Salud del servicio y resumen del sistema.",
        )
        title.grid(row=0, column=0, sticky="ew", pady=(0, SPACING.lg))
        self.titles["status"] = title

        body = tk.Canvas(
            parent, background=PALETTE.background, highlightthickness=0,
            yscrollincrement=24,
        )
        body.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            parent, orient="vertical", command=body.yview,
            style="Yachay.Vertical.TScrollbar",
        )
        scrollbar.grid(row=1, column=1, sticky="ns")
        body.configure(yscrollcommand=scrollbar.set)
        content = tk.Frame(body, background=PALETTE.background)
        content_window = body.create_window((0, 0), window=content, anchor="nw")

        def _fit_content(event=None) -> None:
            if event is not None:
                body.itemconfigure(content_window, width=event.width)
            body.configure(scrollregion=body.bbox("all"))

        content.bind("<Configure>", _fit_content, add="+")
        body.bind("<Configure>", _fit_content, add="+")

        self.toggle_button = make_button(
            title.actions,
            "Pausar auto-refresco",
            self._toggle_refresh,
            kind="ghost",
        )
        self.toggle_button.pack(side="right")
        make_button(title.actions, "Actualizar", self.refresh, kind="secondary").pack(
            side="right", padx=(0, SPACING.sm)
        )

        card = Card(content)
        card.grid(row=0, column=0, sticky="ew")
        self.status_body = card.body

        self._build_status_metrics(self.status_body)

        status_note = tk.Label(
            self.status_body,
            text=(
                "Solo consulta el estado. La reconciliación y la ejecución son "
                "tareas del worker."
            ),
            foreground=PALETTE.disabled_text,
            background=PALETTE.surface,
            font=TYPE.font(TYPE.size_sm),
            anchor="w",
            justify="left",
        )
        responsive_wrap(status_note).pack(anchor="w", pady=(SPACING.lg, 0), fill="x")

        self._build_function_catalog(content)

    def _build_status_metrics(self, parent: tk.Frame) -> None:
        """Construye las métricas y sus textos interpretativos."""
        grid = tk.Frame(parent, background=PALETTE.surface)
        grid.pack(fill="x")
        self.metric_labels: dict[str, tk.Label] = {}
        self.metric_hints: dict[str, tk.Label] = {}
        metrics = [
            ("service", "Servicio"),
            ("database", "Base de datos"),
            ("jobs_total", "Trabajos"),
            ("running", "En proceso"),
            ("failed", "Por revisar"),
            ("datasets", "Datasets"),
        ]
        for index, (key, label) in enumerate(metrics):
            cell = tk.Frame(grid, background=PALETTE.surface)
            cell.grid(
                row=index // 3, column=index % 3, sticky="w",
                padx=SPACING.xl, pady=SPACING.md,
            )
            tk.Label(
                cell,
                text=label.upper(),
                foreground=PALETTE.disabled_text,
                background=PALETTE.surface,
                font=TYPE.font(TYPE.size_micro),
                anchor="w",
            ).pack(anchor="w")
            value = tk.Label(
                cell,
                text="Sin datos",
                foreground=PALETTE.text,
                background=PALETTE.surface,
                font=TYPE.font(TYPE.size_lg, weight=TYPE.weight_semibold),
                anchor="w",
            )
            value.pack(anchor="w")
            self.metric_labels[key] = value
            # Segunda línea interpretativa: el número nunca va solo (P-01).
            hint = tk.Label(
                cell,
                text="",
                foreground=PALETTE.disabled_text,
                background=PALETTE.surface,
                font=TYPE.font(TYPE.size_sm),
                anchor="w",
            )
            hint.pack(anchor="w")
            self.metric_hints[key] = hint

    def _build_function_catalog(self, parent: tk.Frame) -> None:
        """Construye el catálogo documentado sin mezclarlo con el estado."""
        functions_card = Card(parent)
        functions_card.grid(row=2, column=0, sticky="nsew", pady=(SPACING.lg, 0))
        tk.Label(
            functions_card.body,
            text="Funciones del motor",
            foreground=PALETTE.text,
            background=PALETTE.surface,
            font=TYPE.font(TYPE.size_lg, weight=TYPE.weight_semibold),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            functions_card.body,
            text="Alcance documentado y punto de acceso actual.",
            foreground=PALETTE.disabled_text,
            background=PALETTE.surface,
            font=TYPE.font(TYPE.size_sm),
            anchor="w",
        ).pack(anchor="w", pady=(SPACING.xs, SPACING.md))
        self.functions_tree, functions_table = make_table(
            functions_card.body,
            [
                ("phase", "Fase", 55, 45),
                ("function", "Función", 170, 130),
                ("description", "Qué hace", 360, 220),
                ("access", "Acceso", 170, 130),
            ],
            stretch_column="description",
        )
        functions_table.pack(fill="both", expand=True)
        for phase, function, description, access in FUNCTION_CATALOG:
            self.functions_tree.insert(
                "", "end", values=(phase, function, description, access)
            )

    def _render_status(self, health, jobs: list[Job], datasets) -> None:
        if health is None:
            self.metric_labels["service"].configure(text="No disponible", foreground=PALETTE.error)
            self.metric_labels["database"].configure(
                text="Sin conexión", foreground=PALETTE.disabled_text
            )
            self.metric_hints["service"].configure(text="sin conexión con el motor")
            self.metric_hints["database"].configure(text="esperando al motor")
            for key in ("jobs_total", "running", "failed", "datasets"):
                self.metric_labels[key].configure(
                    text="No disponible", foreground=PALETTE.disabled_text
                )
                self.metric_hints[key].configure(text="sin conexión con el motor")
            return
        else:
            self.metric_labels["service"].configure(text="En línea", foreground=PALETTE.success)
            self.metric_hints["service"].configure(text="la API responde")
            database = health.get("database", "Sin datos")
            self.metric_labels["database"].configure(
                text="Operativa" if database == "ok" else str(database),
                foreground=PALETTE.success if database == "ok" else PALETTE.warning,
            )
            self.metric_hints["database"].configure(
                text="lee SQLite correctamente" if database == "ok" else "revisar SQLite"
            )

        total = len(jobs)
        running = sum(1 for job in jobs if job.state == "running")
        failed = sum(1 for job in jobs if job.state == "failed")
        deferred = sum(1 for job in jobs if job.state == "deferred")
        self.metric_labels["jobs_total"].configure(text=str(total))
        self.metric_hints["jobs_total"].configure(
            text=f"últimos {DEFAULT_LIMIT} trabajos"
        )
        self.metric_labels["running"].configure(text=str(running), foreground=PALETTE.primary)
        self.metric_hints["running"].configure(text="procesándose ahora")
        if failed:
            self.metric_labels["failed"].configure(text=str(failed), foreground=PALETTE.error)
            self.metric_hints["failed"].configure(text="requieren atención")
        else:
            self.metric_labels["failed"].configure(text="0", foreground=PALETTE.success)
            self.metric_hints["failed"].configure(text="nada pendiente")
        self.metric_labels["datasets"].configure(text=str(len(datasets)))
        self.metric_hints["datasets"].configure(text="versiones publicadas")

        # Señal de conjunto en el panel: cuántos trabajos piden revisión.
        if health is not None:
            if failed or deferred:
                self.metric_hints["failed"].configure(
                    text=f"requieren atención (+{deferred} diferidos)"
                    if deferred
                    else "requieren atención"
                )
                self.metric_labels["failed"].configure(foreground=PALETTE.error)
