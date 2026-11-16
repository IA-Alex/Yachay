"""Yachay Desktop — ventana principal y vistas.

Siete vistas, todas alimentadas por la API v1 real a través del socket Unix:

1. Panel de estado   → GET /v1/health + conteos derivados de jobs/datasets
2. Cargar archivo    → POST /v1/inputs + GET /v1/profile
3. Modelo            → GET/POST /v1/models
4. Ejecutar          → POST /v1/models/{id}/runs
5. Resultados        → GET /v1/models/{id}/runs + GET /v1/diff
6. Cola de trabajos  → GET /v1/jobs (+ cancelar / reintentar)
7. Catálogo          → GET /v1/datasets (+ detalle del manifest)

Ninguna vista inventa datos: los estados vacíos son estados vacíos reales.
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path

from yachay_desktop.client import ApiClient, ApiError, ApiUnreachable
from yachay_desktop.theme import (
    PALETTE,
    SPACING,
    TYPE,
    filter_options,
)
from yachay_desktop.ui_constants import (
    DEFAULT_LIMIT,
    FUNCTION_CATALOG,
    MAX_REQUEST_BYTES,
    REFRESH_MS,
)
from yachay_desktop.views.assistant import AssistantViewMixin
from yachay_desktop.views.datasets import DatasetsViewMixin
from yachay_desktop.views.execution import ExecutionViewMixin
from yachay_desktop.views.files import FilesViewMixin
from yachay_desktop.views.jobs import JobsViewMixin
from yachay_desktop.views.models import ModelsViewMixin
from yachay_desktop.views.results import ResultsViewMixin
from yachay_desktop.views.status import StatusViewMixin
from yachay_desktop.views.visualizations import VisualizationsViewMixin
from yachay_desktop.widgets import (
    SectionTitle,
    configure_ttk_styles,
)

__all__ = ["FUNCTION_CATALOG", "MAX_REQUEST_BYTES", "YachayApp", "main"]



class YachayApp(
    StatusViewMixin, JobsViewMixin, DatasetsViewMixin, FilesViewMixin,
    ModelsViewMixin, ExecutionViewMixin, ResultsViewMixin, VisualizationsViewMixin,
    AssistantViewMixin, tk.Tk,
):
    """Ventana principal de la interfaz de escritorio."""

    def __init__(
        self, socket_path: Path, *, auto_start: bool = True,
        model_outputs_dir: Path | None = None,
    ):
        super().__init__()
        self.client = ApiClient(socket_path)
        self.socket_path = socket_path
        self.model_outputs_dir = (
            Path(model_outputs_dir) if model_outputs_dir is not None
            else Path(socket_path).parent / "model-outputs"
        )
        self._auto_refresh = auto_start
        self._after_id: str | None = None
        self._last_error: Exception | None = None
        self._refresh_generation = 0
        self._closed = threading.Event()
        self._workers: set[threading.Thread] = set()
        self._workers_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._models_load_lock = threading.Lock()
        # Etiqueta en español del filtro → estado crudo del motor (FE-007).
        self._filter_options = filter_options()

        self.title("Yachay · Motor de datos local")
        self.geometry("1080x680")
        self.minsize(880, 560)
        self.configure(background=PALETTE.background)
        configure_ttk_styles(self)

        # Puente hilos→Tk. `after()` invocado desde un hilo de trabajo NO
        # ejecuta el callback en este entorno (se pierde en silencio), así que
        # los workers encolan el resultado y el hilo principal lo drena con un
        # `after` propio. Sin esto ninguna vista salía nunca de su estado inicial.
        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._drain_id: str | None = self.after(50, self._drain_ui)

        # Cierre controlado: sin este handler el `after` de refresco seguía
        # programado y un worker en vuelo podía llamar `after(0, …)` sobre una
        # raíz ya destruida (`TclError` en la salida) (DF-001).
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_layout()
        self._bind_keys()
        self.show_view("status")
        if auto_start:
            self._schedule_refresh(immediate=True)

    # -- estructura ---------------------------------------------------------

    def _build_layout(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self._build_sidebar()

        container = tk.Frame(self, background=PALETTE.background)
        container.grid(row=0, column=1, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        self.banner = self._build_banner(container)

        self.content = tk.Frame(container, background=PALETTE.background)
        self.content.grid(row=1, column=0, sticky="nsew", padx=SPACING.xl, pady=SPACING.lg)
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        self.views: dict[str, tk.Frame] = {}
        self.titles: dict[str, SectionTitle] = {}
        for name, builder in (
            ("status", self._build_status_view),
            ("files", self._build_files_view),
            ("model", self._build_model_view),
            ("execute", self._build_execute_view),
            ("results", self._build_results_view),
            ("visualizations", self._build_visualizations_view),
            ("assistant", self._build_assistant_view),
            ("jobs", self._build_jobs_view),
            ("datasets", self._build_datasets_view),
        ):
            frame = tk.Frame(self.content, background=PALETTE.background)
            frame.grid(row=0, column=0, sticky="nsew")
            builder(frame)
            self.views[name] = frame

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self, background=PALETTE.sidebar, width=232)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        header = tk.Frame(sidebar, background=PALETTE.sidebar)
        header.pack(fill="x", padx=SPACING.lg, pady=(SPACING.xl, SPACING.lg))
        tk.Label(
            header,
            text="Yachay",
            foreground=PALETTE.text,
            background=PALETTE.sidebar,
            font=TYPE.font(TYPE.size_title, weight=TYPE.weight_semibold),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Motor de datos local",
            foreground=PALETTE.accent,
            background=PALETTE.sidebar,
            font=TYPE.font(TYPE.size_sm),
            anchor="w",
        ).pack(anchor="w")

        self.nav_buttons: dict[str, tk.Button] = {}
        for name, label in (
            ("status", "Panel de estado"),
            ("files", "Cargar archivo"),
            ("model", "Modelo"),
            ("execute", "Ejecutar"),
            ("results", "Resultados"),
            ("visualizations", "Visualizaciones"),
            ("assistant", "Propuestas"),
            ("jobs", "Cola de trabajos"),
            ("datasets", "Catálogo de datasets"),
        ):
            button = tk.Button(
                sidebar,
                text="  " + label,
                anchor="w",
                command=lambda n=name: self.show_view(n),
                background=PALETTE.sidebar,
                foreground=PALETTE.text,
                activebackground=PALETTE.surface_alt,
                activeforeground=PALETTE.text,
                font=TYPE.font(TYPE.size_base),
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
                padx=SPACING.lg,
                pady=SPACING.sm,
                cursor="hand2",
            )
            button.pack(fill="x")
            self.nav_buttons[name] = button

        footer = tk.Frame(sidebar, background=PALETTE.sidebar)
        footer.pack(side="bottom", fill="x", padx=SPACING.lg, pady=SPACING.lg)
        tk.Label(
            footer,
            text="Socket",
            foreground=PALETTE.disabled_text,
            background=PALETTE.sidebar,
            font=TYPE.font(TYPE.size_sm),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            footer,
            text=self._wrapped_socket_path(),
            foreground=PALETTE.disabled_text,
            background=PALETTE.sidebar,
            font=TYPE.font(TYPE.size_sm - 1, mono=True),
            anchor="w",
            justify="left",
        ).pack(anchor="w", fill="x")

    def _wrapped_socket_path(self) -> str:
        """Ruta del socket con cortes en «/»: no parte «motor.sock» (FE-008)."""
        path = str(self.socket_path)
        width = 26  # caracteres que caben en el sidebar a la fuente mono pequeña
        if len(path) <= width:
            return path
        parts = path.split("/")
        lines: list[str] = []
        current = parts[0] or "/"
        for part in parts[1:]:
            candidate = f"{current}/{part}" if current != "/" else f"/{part}"
            if len(candidate) > width and current:
                lines.append(current)
                current = part
            else:
                current = candidate
        if current:
            lines.append(current)
        return "\n".join(lines)

    def _build_banner(self, parent: tk.Misc) -> tk.Frame:
        banner = tk.Frame(parent, background=PALETTE.error)
        inner = tk.Frame(banner, background=PALETTE.error)
        inner.pack(fill="x", padx=SPACING.lg, pady=SPACING.sm)
        inner.columnconfigure(0, weight=1)
        label = tk.Label(
            inner,
            text="",
            background=PALETTE.error,
            foreground=PALETTE.background,
            font=TYPE.font(TYPE.size_base, weight=TYPE.weight_medium),
            anchor="w",
            justify="left",
        )
        label.grid(row=0, column=0, sticky="ew")
        # Acción de recuperación: el usuario nunca queda sin salida (P-03).
        retry = tk.Button(
            inner,
            text="Reintentar conexión",
            command=self.refresh,
            background=PALETTE.background,
            foreground=PALETTE.error,
            activebackground=PALETTE.surface,
            activeforeground=PALETTE.error,
            font=TYPE.font(TYPE.size_sm, weight=TYPE.weight_medium),
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=PALETTE.background,
            padx=SPACING.md,
            pady=2,
            cursor="hand2",
        )
        retry.grid(row=0, column=1, sticky="e", padx=(SPACING.md, 0))
        banner.label = label  # type: ignore[attr-defined]
        return banner

    def _bind_keys(self) -> None:
        self.bind("<Control-r>", lambda _e: self.refresh())
        self.bind("<F5>", lambda _e: self.refresh())
        for index, name in enumerate(
            ("status", "files", "model", "execute", "results", "visualizations", "assistant",
             "jobs", "datasets"), start=1
        ):
            self.bind(f"<Control-Key-{index}>", lambda _e, n=name: self.show_view(n))

    # -- navegación ---------------------------------------------------------

    def show_view(self, name: str) -> None:
        self.active_view = name
        for view_name, frame in self.views.items():
            frame.tkraise() if view_name == name else None
        for button_name, button in self.nav_buttons.items():
            selected = button_name == name
            button.configure(
                background=PALETTE.surface_alt if selected else PALETTE.surface,
                foreground=PALETTE.primary if selected else PALETTE.text,
                font=TYPE.font(
                    TYPE.size_base,
                    weight=TYPE.weight_medium if selected else TYPE.weight_normal,
                ),
            )
        # El ciclo periódico o el botón explícito de actualización mantienen
        # los datos al día; navegar no dispara una consulta duplicada.

    def _on_close(self) -> None:
        """Cancela el refresco y el drenaje pendientes y cierra la ventana.

        Un `after` que dispara después de `destroy()` revienta con `TclError`
        porque la interpretación ya no existe. Se cancela antes de destruir y
        se espera al worker de refresco para que no conserve la raíz Tk viva
        desde otro hilo (Tcl_AsyncDelete/DF-002).
        """
        self._closed.set()
        for attr in ("_after_id", "_drain_id"):
            pending = getattr(self, attr)
            if pending is not None:
                self.after_cancel(pending)
                setattr(self, attr, None)
        with self._workers_lock:
            workers = tuple(self._workers)
        for worker in workers:
            if worker is not threading.current_thread() and hasattr(worker, "join"):
                worker.join(timeout=2)
        self._ui_queue = queue.SimpleQueue()
        self.destroy()

    def _start_worker(self, target, *args) -> bool:
        """Ejecuta una tarea de API y la registra para el cierre de Tk."""
        def runner() -> None:
            try:
                target(*args)
            finally:
                with self._workers_lock:
                    self._workers.discard(threading.current_thread())

        worker = threading.Thread(target=runner, daemon=True)
        with self._workers_lock:
            if self._closed.is_set():
                return False
            self._workers.add(worker)
        worker.start()
        return True

    def _run_on_ui(self, callback, *args) -> None:
        """Programa `callback(*args)` para que corra en el hilo de Tk.

        Seguro desde un worker: encola. En el hilo principal ejecuta al
        instante. Es la única forma de tocar widgets desde otro hilo.
        """
        if self._closed.is_set():
            return
        self._ui_queue.put((callback, args))
        if threading.current_thread() is threading.main_thread():
            self._drain_ui()

    def _drain_ui(self) -> None:
        """Ejecuta en el hilo de Tk lo que los workers dejaron en la cola."""
        while True:
            try:
                callback, args = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            callback(*args)
        if self._drain_id is not None:
            try:
                self.after_cancel(self._drain_id)
            except tk.TclError:
                return
        try:
            self._drain_id = self.after(50, self._drain_ui)
        except tk.TclError:
            # La ventana se destruyó entre el sondeo y la reprogramación.
            self._drain_id = None

    # -- refresco -----------------------------------------------------------

    def _schedule_refresh(self, *, immediate: bool = False) -> None:
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        if immediate:
            self.refresh()
        if self._auto_refresh:
            self._after_id = self.after(REFRESH_MS, self._schedule_refresh)

    def refresh(self) -> None:
        """Refresca en un hilo, sin iniciar otro sondeo si el anterior sigue activo."""
        if not self._auto_refresh or self._closed.is_set():
            return
        if not self._refresh_lock.acquire(blocking=False):
            return
        self._refresh_generation += 1
        generation = self._refresh_generation
        if not self._start_worker(self._refresh_worker, generation):
            self._refresh_lock.release()

    def _refresh_worker(self, generation: int | None = None) -> None:
        try:
            try:
                health = self.client.health()
                jobs = self.client.jobs(limit=DEFAULT_LIMIT)
                datasets = self.client.datasets(limit=DEFAULT_LIMIT)
                error = None
            except ApiUnreachable as exc:
                health, jobs, datasets, error = None, [], [], exc
            except ApiError as exc:
                health, jobs, datasets, error = None, [], [], exc

            if self._closed.is_set():
                return
            self._run_on_ui(
                lambda: self._apply_refresh(health, jobs, datasets, error, generation)
            )
        finally:
            self._refresh_lock.release()

    def _apply_refresh(
        self, health, jobs, datasets, error, generation: int | None = None
    ) -> None:
        if generation is not None and generation != self._refresh_generation:
            return
        self._last_error = error
        if error is None:
            self._hide_banner()
        else:
            message = (
                f"{error.label}: {error.message}"
                if isinstance(error, ApiError)
                else str(error)
            )
            self._show_banner(message)

        self._render_status(health, jobs, datasets)
        unavailable = health is None
        self._render_jobs(jobs, unavailable=unavailable)
        self._render_datasets(datasets, unavailable=unavailable)
        self._load_models(generation=generation)
        self._mark_fresh(timestamp=True)

    def _mark_fresh(self, *, timestamp: bool) -> None:
        """Actualiza el indicador de frescura de todas las vistas (P-04).

        Un indicador por vista: siempre visible en el encabezado, aunque el
        usuario esté en otra pestaña. Refleja el estado global de conexión y,
        cuando corresponde, la hora del último refresco.
        """
        from datetime import datetime

        hour = datetime.now().strftime("%H:%M:%S")
        if self._last_error is None:
            text = f"Actualizado {hour}" if timestamp else "Datos al día"
            token = "success"
        elif isinstance(self._last_error, ApiError):
            text = f"{self._last_error.label}"
            token = "error"
        else:
            text = "Sin conexión con el motor"
            token = "error"
        if not self._auto_refresh:
            text = "Auto-refresco en pausa · " + text
            token = "warning"
        for title in self.titles.values():
            title.set_freshness(text, token)

    def _show_banner(self, message: str) -> None:
        self.banner.label.configure(text="⚠  " + message)  # type: ignore[attr-defined]
        self.banner.grid(row=0, column=0, sticky="ew")

    def _hide_banner(self) -> None:
        self.banner.grid_remove()

    # -- auto-refresco ------------------------------------------------------

    def _toggle_refresh(self) -> None:
        self._auto_refresh = not self._auto_refresh
        if self._auto_refresh:
            self.toggle_button.configure(text="Pausar auto-refresco")
            self._schedule_refresh(immediate=True)
        else:
            self.toggle_button.configure(text="Reanudar auto-refresco")
            if self._after_id is not None:
                self.after_cancel(self._after_id)
                self._after_id = None
            # La pausa se refleja en el indicador de las cuatro vistas (P-04).
            self._mark_fresh(timestamp=False)


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada: parsea el socket y abre la ventana."""
    parser = argparse.ArgumentParser(
        prog="yachay-desktop",
        description="Interfaz de escritorio para el motor de datos Yachay",
    )
    parser.add_argument(
        "--socket",
        default=None,
        help="Ruta del socket Unix de la API (por defecto: <root>/data/motor.sock)",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Raíz del proyecto del motor (por defecto: .)",
    )
    args = parser.parse_args(argv)

    socket_path = (
        Path(args.socket) if args.socket else Path(args.root) / "data" / "motor.sock"
    )
    try:
        app = YachayApp(
            socket_path,
            model_outputs_dir=Path(args.root) / "data" / "model-outputs",
        )
    except tk.TclError as exc:  # pragma: no cover - sin display
        print(f"No se pudo abrir la interfaz gráfica: {exc}", file=sys.stderr)
        return 1
    app.mainloop()
    return 0
