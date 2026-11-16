from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox

from ..client import ApiError, ApiUnreachable
from ..theme import PALETTE, SPACING, TYPE
from ..widgets import Card, SectionTitle, make_button, responsive_wrap


class AssistantViewMixin:
    """A task-scoped interface for previewing existing model operations."""

    def _build_assistant_view(self, parent: tk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        title = SectionTitle(
            parent,
            "Propuestas de modelado",
            "Describe el cambio; revisa las reglas antes de crear una versión.",
        )
        title.grid(row=0, column=0, sticky="ew", pady=(0, SPACING.lg))
        self.titles["assistant"] = title

        controls = Card(parent)
        controls.grid(row=1, column=0, sticky="ew", pady=(0, SPACING.lg))
        row = tk.Frame(controls.body, background=PALETTE.surface)
        row.pack(fill="x")
        self.assistant_model = tk.StringVar(value="1")
        self.assistant_source = tk.StringVar(value="")
        for label, variable, width in (
            ("Modelo", self.assistant_model, 8), ("Fuente", self.assistant_source, 24),
        ):
            tk.Label(row, text=f"{label}:", foreground=PALETTE.text,
                     background=PALETTE.surface, font=TYPE.font(TYPE.size_base)).pack(
                         side="left", padx=(SPACING.md, SPACING.xs))
            tk.Entry(row, textvariable=variable, width=width,
                     background=PALETTE.surface_alt, foreground=PALETTE.text,
                     insertbackground=PALETTE.text, relief="flat",
                     font=TYPE.font(TYPE.size_base)).pack(side="left", ipady=SPACING.xs)
        self.assistant_instruction = tk.Text(
            controls.body, height=4, wrap="word", undo=True,
            background=PALETTE.surface_alt, foreground=PALETTE.text,
            insertbackground=PALETTE.text, relief="flat", padx=SPACING.sm,
            pady=SPACING.sm, font=TYPE.font(TYPE.size_base),
        )
        self.assistant_instruction.pack(fill="x", pady=(SPACING.md, SPACING.sm))
        actions = tk.Frame(controls.body, background=PALETTE.surface)
        actions.pack(fill="x")
        make_button(actions, "Proponer reglas", self._request_assistant_proposal,
                    kind="primary").pack(side="left")
        make_button(actions, "Aceptar y crear versión", self._accept_assistant_proposal,
                    kind="secondary").pack(side="left", padx=SPACING.sm)
        self.assistant_status = responsive_wrap(tk.Label(
            controls.body, text="La IA recibe nombres de columnas y estadísticas del perfil; "
            "no recibe filas del archivo.", foreground=PALETTE.disabled_text,
            background=PALETTE.surface, font=TYPE.font(TYPE.size_base),
            anchor="w", justify="left",
        ))
        self.assistant_status.pack(fill="x", pady=(SPACING.sm, 0))

        preview = Card(parent)
        preview.grid(row=2, column=0, sticky="nsew")
        tk.Label(preview.body, text="Vista previa", foreground=PALETTE.text,
                 background=PALETTE.surface, font=TYPE.font(TYPE.size_base,
                                                            weight=TYPE.weight_semibold)).pack(
                                                                anchor="w")
        self.assistant_preview = tk.Text(
            preview.body, height=14, wrap="word", state="disabled",
            background=PALETTE.surface_alt, foreground=PALETTE.text,
            relief="flat", padx=SPACING.md, pady=SPACING.md,
            font=TYPE.font(TYPE.size_base),
        )
        self.assistant_preview.pack(fill="both", expand=True, pady=(SPACING.sm, 0))
        self._assistant_proposal: dict | None = None
        self._assistant_context: tuple[int, str, str] | None = None

    def _request_assistant_proposal(self) -> None:
        model = self.assistant_model.get().strip()
        source = self.assistant_source.get().strip()
        instruction = self.assistant_instruction.get("1.0", "end-1c").strip()
        if not model.isdigit() or not source or not instruction:
            self._set_assistant_status("Indica modelo, fuente e instrucción.", PALETTE.warning)
            return
        context = (int(model), source, instruction)
        self._assistant_proposal = None
        self._assistant_context = None
        self._show_assistant_preview("Consultando el proveedor…")
        self._set_assistant_status("Preparando propuesta…", PALETTE.info)

        def worker() -> None:
            try:
                result = self.client.propose_model_rules(*context)
                proposal = result["proposal"]
            except (ApiError, ApiUnreachable, KeyError) as exc:
                message = getattr(exc, "message", str(exc))
                self._run_on_ui(lambda: self._set_assistant_status(message, PALETTE.error))
                return
            self._run_on_ui(lambda: self._receive_assistant_proposal(proposal, context))

        self._start_worker(worker)

    def _receive_assistant_proposal(self, proposal: dict, context: tuple[int, str, str]) -> None:
        self._assistant_proposal = proposal
        self._assistant_context = context
        lines = [proposal["summary"], ""]
        for item in proposal["rules"]:
            lines.append(
                f"{item['name']} · {item['type']} · {item['column']} · "
                f"{json.dumps(item['params'], ensure_ascii=False)}"
            )
        if not proposal["rules"]:
            lines.append("Sin reglas propuestas.")
        self._show_assistant_preview("\n".join(lines))
        self._set_assistant_status(
            f"{len(proposal['rules'])} regla(s). Aceptarlas crea una nueva versión; "
            "la ejecución seguirá siendo manual.", PALETTE.success,
        )

    def _accept_assistant_proposal(self) -> None:
        context = self._assistant_context
        proposal = self._assistant_proposal
        current = (
            int(self.assistant_model.get()) if self.assistant_model.get().strip().isdigit()
            else -1,
            self.assistant_source.get().strip(),
            self.assistant_instruction.get("1.0", "end-1c").strip(),
        )
        if proposal is None or context is None or context != current:
            self._set_assistant_status(
                "La vista previa ya no corresponde a estos datos. Genera otra propuesta.",
                PALETTE.warning,
            )
            return
        if not proposal["rules"]:
            self._set_assistant_status("No hay reglas que aceptar.", PALETTE.warning)
            return

        def worker() -> None:
            try:
                result = self.client.apply_model_proposal(context[0], proposal)
            except (ApiError, ApiUnreachable) as exc:
                message = getattr(exc, "message", str(exc))
                self._run_on_ui(lambda: self._set_assistant_status(message, PALETTE.error))
                return
            self._run_on_ui(lambda: self._assistant_applied(result))

        self._start_worker(worker)

    def _assistant_applied(self, result: dict) -> None:
        self._assistant_proposal = None
        self._assistant_context = None
        version = result["version"]
        self._set_assistant_status(
            f"Creada la versión {version}; no se ejecutó el modelo.", PALETTE.success
        )
        messagebox.showinfo(
            "Modelo actualizado", f"Versión {version} creada. Aceptar", parent=self
        )
        self._show_assistant_preview("La propuesta fue aplicada.")
        self._load_models()

    def _show_assistant_preview(self, text: str) -> None:
        self.assistant_preview.configure(state="normal")
        self.assistant_preview.delete("1.0", "end")
        self.assistant_preview.insert("1.0", text)
        self.assistant_preview.configure(state="disabled")

    def _set_assistant_status(self, text: str, color: str) -> None:
        self.assistant_status.configure(text=text, foreground=color)
