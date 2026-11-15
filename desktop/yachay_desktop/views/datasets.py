from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ..client import ApiError, ApiUnreachable
from ..formatting import format_epoch, short_id
from ..theme import PALETTE, SPACING, TYPE, resolve_symbol
from ..widgets import Card, SectionTitle, make_button, make_table


class DatasetsViewMixin:
    """Vista de escritorio y acciones relacionadas."""

    def _build_datasets_view(self, parent: tk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        title = SectionTitle(
            parent,
            "Catálogo de datasets",
            "Resultados publicados. El manifest es la marca física de cada versión.",
        )
        title.grid(row=0, column=0, sticky="ew", pady=(0, SPACING.lg))
        self.titles["datasets"] = title

        card = Card(parent)
        card.grid(row=1, column=0, sticky="nsew")

        self.datasets_tree, table = make_table(
            card.body,
            [
                ("dataset", "Dataset", 220, 160),
                ("run", "Run", 180, 140),
                ("committed", "Publicado", 170, 150),
                ("integrity", "Integridad", 170, 150),
            ],
            stretch_column="integrity",
        )
        table.grid(row=0, column=0, sticky="nsew")
        card.body.rowconfigure(0, weight=1)
        card.body.columnconfigure(0, weight=1)

        self.datasets_tree.bind(
            "<<TreeviewSelect>>", lambda _e: self._update_dataset_actions()
        )

        actions = tk.Frame(parent, background=PALETTE.background)
        actions.grid(row=2, column=0, sticky="ew", pady=(SPACING.lg, 0))
        self.btn_manifest = make_button(
            actions, "Ver manifest", self._show_manifest, kind="secondary", state="disabled"
        )
        self.btn_manifest.pack(side="left")
        self.datasets_hint = tk.Label(
            actions,
            text="Selecciona un dataset para inspeccionar su manifest.",
            foreground=PALETTE.disabled_text,
            background=PALETTE.background,
            font=TYPE.font(TYPE.size_sm),
        )
        self.datasets_hint.pack(side="left", padx=SPACING.md)

    def _render_datasets(self, datasets, *, unavailable: bool = False) -> None:
        self.datasets_tree.delete(*self.datasets_tree.get_children())
        self._datasets_by_item = {}
        if not datasets:
            self.datasets_tree.insert(
                "",
                "end",
                values=(
                    "Sin conexión con el motor"
                    if unavailable
                    else "Sin datasets publicados",
                    "",
                    "",
                    "",
                ),
            )
            self._update_dataset_actions()
            return
        for dataset in datasets:
            integrity = (
                f"{resolve_symbol('✓')} Íntegro"
                if dataset.manifest_present
                else f"{resolve_symbol('⚠')} Manifest ausente"
            )
            item = self.datasets_tree.insert(
                "",
                "end",
                values=(
                    dataset.dataset,
                    short_id(dataset.run_id, 16),
                    format_epoch(dataset.committed_at),
                    integrity,
                ),
            )
            self._datasets_by_item[item] = dataset
        self._update_dataset_actions()

    def _selected_dataset(self):
        selection = self.datasets_tree.selection()
        if not selection:
            return None
        return getattr(self, "_datasets_by_item", {}).get(selection[0])

    def _update_dataset_actions(self) -> None:
        dataset = self._selected_dataset()
        if dataset is None:
            self.btn_manifest.configure(state="disabled")
            self.datasets_hint.configure(
                text="Selecciona un dataset para inspeccionar su manifest."
            )
            return
        self.btn_manifest.configure(state="normal")
        if dataset.manifest_present:
            self.datasets_hint.configure(text="Íntegro: el manifest está disponible.")
        else:
            self.datasets_hint.configure(
                text="Manifest ausente: el motor responderá «no disponible» al abrirlo."
            )

    def _show_manifest(self) -> None:
        dataset = self._selected_dataset()
        if dataset is None:
            return

        def worker():
            try:
                manifest = self.client.manifest(dataset.run_id)
                self._run_on_ui(lambda: self._open_manifest_window(dataset, manifest))
            except ApiError as exc:
                text = f"{exc.label}\n\n{exc.message}"
                self._run_on_ui(lambda: messagebox.showwarning(
                    "Manifest no disponible", text, parent=self))
            except ApiUnreachable as exc:
                text = str(exc)
                self._run_on_ui(lambda: messagebox.showerror(
                    "Motor no disponible", text, parent=self))

        self._start_worker(worker)

    def _open_manifest_window(self, dataset, manifest: dict) -> None:
        window = tk.Toplevel(self)
        window.title(f"Manifest · {dataset.dataset}")
        window.configure(background=PALETTE.background)
        window.geometry("560x520")

        tk.Label(
            window,
            text=dataset.dataset,
            foreground=PALETTE.text,
            background=PALETTE.background,
            font=TYPE.font(TYPE.size_title, weight=TYPE.weight_semibold),
            anchor="w",
        ).pack(anchor="w", padx=SPACING.xl, pady=(SPACING.xl, SPACING.sm))

        text = tk.Text(
            window,
            background=PALETTE.surface,
            foreground=PALETTE.text,
            insertbackground=PALETTE.text,
            relief="flat",
            font=TYPE.font(TYPE.size_base, mono=True),
            highlightthickness=1,
            highlightbackground=PALETTE.border,
            padx=SPACING.md,
            pady=SPACING.md,
            wrap="word",
        )
        text.pack(fill="both", expand=True, padx=SPACING.xl, pady=(0, SPACING.lg))
        import json as _json

        text.insert("1.0", _json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
        text.configure(state="disabled")

        make_button(window, "Cerrar", window.destroy, kind="secondary").pack(
            anchor="e", padx=SPACING.xl, pady=(0, SPACING.xl)
        )
