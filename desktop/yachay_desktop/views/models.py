from __future__ import annotations

import tkinter as tk
from pathlib import Path

from ..client import ApiError, ApiUnreachable
from ..theme import PALETTE, SPACING, TYPE
from ..widgets import Card, SectionTitle, make_button, make_table, responsive_wrap


class ModelsViewMixin:
    """Vista de escritorio y acciones relacionadas."""

    def _build_model_view(self, parent: tk.Frame) -> None:
        """Vista 2: crear y ver el modelo (M2)."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        title = SectionTitle(
            parent,
            "Modelo",
            "Define entidades, atributos y claves a partir de un archivo cargado.",
        )
        title.grid(row=0, column=0, sticky="ew", pady=(0, SPACING.lg))
        self.titles["model"] = title
        make_button(title.actions, "Actualizar", self._load_models, kind="secondary").pack(
            side="right"
        )

        creator = Card(parent)
        creator.grid(row=1, column=0, sticky="ew", pady=(0, SPACING.lg))
        row = tk.Frame(creator.body, background=PALETTE.surface)
        row.pack(fill="x")
        tk.Label(
            row, text="Fuente:", foreground=PALETTE.text,
            background=PALETTE.surface, font=TYPE.font(TYPE.size_base),
        ).pack(side="left", padx=(0, SPACING.sm))
        self.model_source = tk.StringVar(value="")
        tk.Entry(
            row, textvariable=self.model_source,
            background=PALETTE.surface_alt, foreground=PALETTE.text,
            insertbackground=PALETTE.text, relief="flat",
            font=TYPE.font(TYPE.size_base), highlightthickness=1,
            highlightbackground=PALETTE.border, highlightcolor=PALETTE.focus,
        ).pack(side="left", fill="x", expand=True, ipady=SPACING.xs)
        tk.Label(
            row, text="Nombre:", foreground=PALETTE.text,
            background=PALETTE.surface, font=TYPE.font(TYPE.size_base),
        ).pack(side="left", padx=(SPACING.md, SPACING.sm))
        self.model_name = tk.StringVar(value="")
        tk.Entry(
            row, textvariable=self.model_name,
            background=PALETTE.surface_alt, foreground=PALETTE.text,
            insertbackground=PALETTE.text, relief="flat",
            font=TYPE.font(TYPE.size_base), highlightthickness=1,
            highlightbackground=PALETTE.border, highlightcolor=PALETTE.focus,
            width=18,
        ).pack(side="left", ipady=SPACING.xs)
        self.model_button = make_button(
            creator.body, "Crear modelo desde la fuente", self._do_create_model,
            kind="primary",
        )
        self.model_button.pack(anchor="w", pady=(SPACING.md, 0))
        self.model_status = responsive_wrap(tk.Label(
            creator.body, text="", foreground=PALETTE.disabled_text,
            background=PALETTE.surface, font=TYPE.font(TYPE.size_base),
            anchor="w", justify="left",
        ))
        self.model_status.pack(anchor="w", fill="x", pady=(SPACING.sm, 0))

        card = Card(parent)
        card.grid(row=2, column=0, sticky="nsew")
        self.models_tree, table = make_table(
            card.body,
            [
                ("model", "Modelo", 150, 110),
                ("version", "Versión", 80, 70),
                ("entity", "Entidad", 180, 130),
                ("attributes", "Atributos", 90, 80),
                ("key", "Clave", 180, 120),
            ],
            stretch_column="key",
        )
        table.grid(row=0, column=0, sticky="nsew")
        card.body.rowconfigure(0, weight=1)
        card.body.columnconfigure(0, weight=1)

    def _load_models(self, *, generation: int | None = None) -> None:
        if not self._models_load_lock.acquire(blocking=False):
            return

        def worker():
            try:
                try:
                    models = self.client.models()
                    details = [self.client.model(m["model_id"]) for m in models]
                except (ApiError, ApiUnreachable) as exc:
                    if generation is not None and generation != self._refresh_generation:
                        return
                    message = (
                        f"{exc.label}: {exc.message}"
                        if isinstance(exc, ApiError)
                        else str(exc)
                    )
                    self._run_on_ui(
                        lambda: self._set_model_status(
                            f"No se pudieron cargar los modelos: {message}", PALETTE.error
                        )
                    )
                    return
                if generation is not None and generation != self._refresh_generation:
                    return
                self._run_on_ui(lambda: self._render_models(details))
            finally:
                self._models_load_lock.release()

        if not self._start_worker(worker):
            self._models_load_lock.release()

    def _render_models(self, models: list[dict]) -> None:
        self.models_tree.delete(*self.models_tree.get_children())
        if not models:
            self.models_tree.insert("", "end", values=("Sin modelos", "", "", "", ""))
            return
        for model in models:
            entities = model.get("entities", [])
            if not entities:
                self.models_tree.insert(
                    "", "end",
                    values=(model["name"], model["version"], "(sin entidades)", "", ""),
                )
                continue
            for entity in entities:
                self.models_tree.insert(
                    "", "end",
                    values=(
                        model["name"], model["version"], entity["name"],
                        len(entity["attributes"]), ", ".join(entity["key"]) or "—",
                    ),
                )

    def _set_model_context(self, model_id: int | str, source: str) -> None:
        """Propaga el modelo y archivo recién creados a las vistas siguientes."""
        value = str(model_id)
        self.exec_model.set(value)
        self.exec_source.set(source)
        self.results_model.set(value)

    def _do_create_model(self) -> None:
        # La API recibe un nombre de entrada, no una ruta local. Esto permite
        # pegar una ruta completa sin provocar "el nombre debe ser un nombre
        # simple" y mantiene el mismo valor que devuelve la carga.
        raw_source = self.model_source.get().strip()
        source = Path(raw_source).name
        name = self.model_name.get().strip() or Path(source).stem
        if not source:
            self._set_model_status("Escribe el nombre de una fuente cargada.", PALETTE.error)
            return
        self.model_button.configure(state="disabled")
        self._set_model_status(f"Creando modelo desde {source}…", PALETTE.info)

        def worker():
            try:
                result = self.client.create_model(name, source)
            except ApiError as exc:
                text = f"✕ {exc.label}: {exc.message}"
                self._run_on_ui(lambda: self._set_model_status(text, PALETTE.error))
                self._run_on_ui(lambda: self.model_button.configure(state="normal"))
                return
            except ApiUnreachable as exc:
                text = str(exc)
                self._run_on_ui(lambda: self._set_model_status(text, PALETTE.error))
                self._run_on_ui(lambda: self.model_button.configure(state="normal"))
                return
            self._run_on_ui(lambda: self._set_model_status(
                f"✓ Modelo «{name}» creado.", PALETTE.success))
            self._run_on_ui(lambda: self.model_button.configure(state="normal"))
            if result.get("model_id") is not None:
                self._run_on_ui(
                    lambda: self._set_model_context(result["model_id"], source)
                )
            self._run_on_ui(self._load_models)

        self._start_worker(worker)

    def _set_model_status(self, text: str, color: str) -> None:
        self.model_status.configure(text=text, foreground=color)
