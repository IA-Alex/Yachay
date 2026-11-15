from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ..client import ApiError, ApiUnreachable, Job
from ..formatting import format_epoch, short_id
from ..theme import CANCELLABLE_STATES, PALETTE, RETRYABLE_STATES, SPACING, TYPE, state_label
from ..ui_constants import DEFAULT_LIMIT
from ..widgets import Card, SectionTitle, make_button, make_table, responsive_wrap


class JobsViewMixin:
    """Vista de escritorio y acciones relacionadas."""

    def _build_jobs_view(self, parent: tk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        title = SectionTitle(
            parent,
            "Cola de trabajos",
            "Estado real de cada trabajo. Las acciones se habilitan según el estado.",
        )
        title.grid(row=0, column=0, sticky="ew", pady=(0, SPACING.lg))
        self.titles["jobs"] = title

        filter_box = tk.Frame(title.actions, background=PALETTE.background)
        filter_box.pack(side="right")
        tk.Label(
            filter_box,
            text="Filtrar:",
            foreground=PALETTE.disabled_text,
            background=PALETTE.background,
            font=TYPE.font(TYPE.size_sm),
        ).pack(side="left", padx=(0, SPACING.sm))
        self.jobs_filter = ttk.Combobox(
            filter_box,
            state="readonly",
            width=20,
            values=list(self._filter_options),
            style="Yachay.TCombobox",
            # ttk puede conservar `TkTextFont` en la instancia aunque el
            # estilo tenga fuente propia; fijarla aquí evita esa sustitución.
            font=TYPE.font(TYPE.size_base),
        )
        self.jobs_filter.set("Todos")
        self.jobs_filter.pack(side="left")
        self.jobs_filter.bind("<<ComboboxSelected>>", lambda _e: self._load_jobs())

        card = Card(parent)
        card.grid(row=1, column=0, sticky="nsew")

        self.jobs_tree, table = make_table(
            card.body,
            [
                ("job", "Trabajo", 100, 85),
                ("state", "Estado", 140, 120),
                ("attempt", "Intento", 75, 70),
                ("created", "Creado", 145, 135),
                ("started", "Iniciado", 145, 135),
                ("reason", "Motivo diferido", 145, 130),
            ],
            stretch_column="reason",
        )
        table.grid(row=0, column=0, sticky="nsew")
        card.body.rowconfigure(0, weight=1)
        card.body.columnconfigure(0, weight=1)

        self.jobs_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_job_actions())

        actions = tk.Frame(parent, background=PALETTE.background)
        actions.grid(row=2, column=0, sticky="ew", pady=(SPACING.lg, 0))
        self.btn_retry = make_button(
            actions, "Reintentar", self._retry_selected, kind="secondary", state="disabled"
        )
        self.btn_retry.grid(row=0, column=0, sticky="w")
        self.btn_cancel = make_button(
            actions, "Cancelar", self._cancel_selected, kind="danger", state="disabled"
        )
        self.btn_cancel.grid(row=0, column=1, sticky="w", padx=(SPACING.sm, 0))
        actions.columnconfigure(2, weight=1)
        self.jobs_hint = responsive_wrap(tk.Label(
            actions,
            text="Selecciona un trabajo para ver las acciones disponibles.",
            foreground=PALETTE.disabled_text,
            background=PALETTE.background,
            font=TYPE.font(TYPE.size_sm),
            justify="left",
            anchor="w",
        ))

        def _adapt_job_actions(_event=None) -> None:
            stacked = actions.winfo_width() < 700
            if stacked:
                self.jobs_hint.grid(
                    row=1, column=0, columnspan=3, sticky="ew",
                    padx=0, pady=(SPACING.sm, 0),
                )
            else:
                self.jobs_hint.grid(
                    row=0, column=2, columnspan=1, sticky="ew",
                    padx=SPACING.md, pady=0,
                )

        actions.bind("<Configure>", _adapt_job_actions, add="+")
        _adapt_job_actions()

    def _load_jobs(self) -> None:
        def worker():
            state = self._filter_options.get(self.jobs_filter.get())
            try:
                jobs = self.client.jobs(state=state, limit=DEFAULT_LIMIT)
                self._run_on_ui(lambda: self._render_jobs(jobs))
            except (ApiError, ApiUnreachable) as exc:
                error = exc

                def report_error() -> None:
                    self._last_error = error
                    message = (
                        f"{error.label}: {error.message}"
                        if isinstance(error, ApiError)
                        else str(error)
                    )
                    self._show_banner(message)
                    self._mark_fresh(timestamp=False)

                self._run_on_ui(report_error)

        self._start_worker(worker)

    def _render_jobs(self, jobs: list[Job], *, unavailable: bool = False) -> None:
        self.jobs_tree.delete(*self.jobs_tree.get_children())
        self._jobs_by_item = {}
        if not jobs:
            self.jobs_tree.insert(
                "",
                "end",
                values=(
                    "—",
                    "Sin conexión con el motor"
                    if unavailable
                    else "Sin trabajos en el sistema",
                    "",
                    "",
                    "",
                    "",
                ),
            )
            self._update_job_actions()
            return
        for job in jobs:
            label = self._state_label(job.state)
            item = self.jobs_tree.insert(
                "",
                "end",
                values=(
                    short_id(job.job_id, 10),
                    label,
                    job.attempt,
                    format_epoch(job.created_at),
                    format_epoch(job.started_at),
                    job.deferred_reason or "—",
                ),
            )
            self._jobs_by_item[item] = job
        self._update_job_actions()

    def _state_label(self, state: str) -> str:
        """Etiqueta legible del estado, con símbolo resuelto para la fuente.

        Usa `theme.state_label` para que el símbolo degradado a ASCII cuando la
        familia no dibuja el glifo (FE-001): la señal nunca es solo el color.
        """
        label, _token, symbol = state_label(state)
        return f"{symbol}  {label}"

    def _selected_job(self) -> Job | None:
        selection = self.jobs_tree.selection()
        if not selection:
            return None
        return getattr(self, "_jobs_by_item", {}).get(selection[0])

    def _update_job_actions(self) -> None:
        job = self._selected_job()
        if job is None:
            self.btn_retry.configure(state="disabled")
            self.btn_cancel.configure(state="disabled")
            self.jobs_hint.configure(
                text="Selecciona un trabajo para ver las acciones disponibles."
            )
            return

        can_retry = job.state in RETRYABLE_STATES
        can_cancel = job.state in CANCELLABLE_STATES and not job.cancel_requested
        self.btn_retry.configure(state="normal" if can_retry else "disabled")
        self.btn_cancel.configure(state="normal" if can_cancel else "disabled")

        if can_retry:
            hint = "Trabajo terminal: puedes reencolarlo."
        elif can_cancel:
            hint = "En curso o en cola: puedes solicitar su cancelación."
        elif job.cancel_requested:
            hint = "Cancelación ya solicitada; el worker la aplicará entre etapas."
        else:
            hint = f"El estado «{job.state}» no admite acciones."
        self.jobs_hint.configure(text=hint)

    def _retry_selected(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        self._run_action("reintentar", self.client.retry, job.job_id)

    def _cancel_selected(self) -> None:
        job = self._selected_job()
        if job is None:
            return
        self._run_action("cancelar", self.client.cancel, job.job_id)

    def _run_action(self, name: str, action, job_id: str) -> None:
        def worker():
            try:
                action(job_id)
                self._run_on_ui(self.refresh)
            except ApiError as exc:
                text = f"{exc.label}\n\n{exc.message}"
                self._run_on_ui(lambda: messagebox.showerror(
                    "No se pudo " + name, text, parent=self))
            except ApiUnreachable as exc:
                text = str(exc)
                self._run_on_ui(lambda: messagebox.showerror(
                    "Motor no disponible", text, parent=self))

        self._start_worker(worker)
