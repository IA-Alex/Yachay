"""Widgets reutilizables de la interfaz Yachay.

Cada widget consume los tokens de `theme.py`; ninguno define un color literal.
El estado nunca se comunica solo por color: siempre lleva etiqueta + símbolo.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from yachay_desktop.theme import (
    PALETTE,
    SPACING,
    TYPE,
    state_label,
)


class StateBadge(tk.Frame):
    """Etiqueta de estado con símbolo + texto + color + borde (nunca solo color).

    El borde del color del estado añade una tercera señal de forma, para que el
    badge se distinga incluso en escala de grises.
    """

    def __init__(self, master: tk.Misc, state: str):
        label, token, symbol = state_label(state)
        color = getattr(PALETTE, token, PALETTE.disabled)
        super().__init__(
            master,
            background=PALETTE.surface_alt,
            highlightbackground=color,   # borde = señal de forma
            highlightthickness=1,
        )
        tk.Label(
            self,
            text=f"{symbol}  {label}",
            foreground=color,
            background=PALETTE.surface_alt,
            font=TYPE.font(TYPE.size_sm, weight=TYPE.weight_bold),
            padx=SPACING.sm,
            pady=2,
        ).pack()


class SectionTitle(tk.Frame):
    """Encabezado de sección: título, subtítulo y acciones opcionales.

    Incluye un slot de "frescura" reutilizable: cada vista muestra ahí si sus
    datos están vivos, congelados o con incidencias (P-04).
    """

    def __init__(self, master: tk.Misc, title: str, subtitle: str = ""):
        super().__init__(master, background=PALETTE.background)

        # Rejilla de dos columnas: el texto cede ancho (columna 0, expansible) y
        # las acciones conservan el suyo (columna 1). Con `pack`, una ventana
        # estrecha pintaba la frescura por encima del título; con grid el texto
        # se recorta a su celda y nunca se solapa (FE-002).
        self.columnconfigure(0, weight=1, minsize=0)
        self.columnconfigure(1, weight=0)

        left = tk.Frame(self, background=PALETTE.background)
        left.grid(row=0, column=0, sticky="ew")
        left.columnconfigure(0, weight=1)

        tk.Label(
            left,
            text=title,
            foreground=PALETTE.text,
            background=PALETTE.background,
            font=TYPE.font(TYPE.size_title, weight=TYPE.weight_semibold),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        if subtitle:
            subtitle_label = tk.Label(
                left,
                text=subtitle,
                foreground=PALETTE.disabled_text,
                background=PALETTE.background,
                font=TYPE.font(TYPE.size_base),
                anchor="w",
                justify="left",
            )
            responsive_wrap(subtitle_label).grid(
                row=1, column=0, sticky="ew", pady=(SPACING.xs, 0)
            )

        self.actions = tk.Frame(self, background=PALETTE.background)
        self.actions.grid(row=0, column=1, sticky="ne")

        # Indicador de frescura, siempre a la izquierda de las acciones; se
        # trunca antes de invadir el espacio del título (FE-002).
        self.freshness = tk.Label(
            self.actions,
            text="Actualizando…",
            foreground=PALETTE.info,
            background=PALETTE.background,
            font=TYPE.font(TYPE.size_sm),
            anchor="e",
            justify="right",
        )
        self.freshness.pack(side="left", padx=(0, SPACING.md))

        def _adapt_header(_event=None) -> None:
            # En ventanas medias, mantener las acciones a la derecha deja al
            # título con una columna microscópica y convierte el subtítulo en
            # una palabra por línea. El encabezado pasa a dos filas antes de
            # que el contenido pierda legibilidad.
            stacked = self.winfo_width() < 900
            if stacked:
                left.grid(row=0, column=0, columnspan=2, sticky="ew")
                self.actions.grid(
                    row=1, column=0, columnspan=2, sticky="w",
                    pady=(SPACING.sm, 0),
                )
            else:
                left.grid(row=0, column=0, columnspan=1, sticky="ew")
                self.actions.grid(row=0, column=1, columnspan=1, sticky="ne")

        self.bind("<Configure>", _adapt_header, add="+")
        _adapt_header()

    def set_freshness(self, text: str, token: str = "info") -> None:
        """Actualiza el indicador con un color de la paleta por token.

        El texto se abrevia solo si desborda un umbral holgado. La contención
        real del ancho la da la rejilla de `SectionTitle`, que recorta la celda
        del título: aquí solo se evita un texto absurdo (FE-002).
        """
        limit = 72
        shown = text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
        self.freshness.configure(
            text=shown, foreground=getattr(PALETTE, token, PALETTE.info)
        )


class Card(tk.Frame):
    """Superficie elevada con borde de 1 px y padding generoso."""

    def __init__(self, master: tk.Misc, **kwargs):
        super().__init__(
            master,
            background=PALETTE.surface,
            highlightbackground=PALETTE.border,
            highlightthickness=1,
            **kwargs,
        )
        self.body = tk.Frame(self, background=PALETTE.surface)
        self.body.pack(fill="both", expand=True, padx=SPACING.lg, pady=SPACING.lg)


def make_button(
    master: tk.Misc,
    text: str,
    command,
    *,
    kind: str = "secondary",
    state: str = "normal",
    tooltip: str = "",
) -> tk.Button:
    """Botón plano, minimalista, con foco visible y estado deshabilitado claro.

    El estado deshabilitado se comunica por DOS señales además del color:
    texto de bajo contraste (pero ≥4.5:1 sobre `surface`, ver `disabled_text`)
    y borde atenuado. Nunca depende solo del color.
    """
    palette = {
        "primary": (PALETTE.primary, PALETTE.background),
        "secondary": (PALETTE.surface_alt, PALETTE.text),
        "danger": (PALETTE.surface_alt, PALETTE.error),
        "ghost": (PALETTE.background, PALETTE.text),
    }
    bg, fg = palette.get(kind, palette["secondary"])
    enabled = state == "normal"
    button = tk.Button(
        master,
        text=text,
        command=command,
        state=state,
        background=bg,
        foreground=fg,
        activebackground=bg,
        activeforeground=fg,
        disabledforeground=PALETTE.disabled_text,  # ≥4.5:1 sobre surface (AA)
        font=TYPE.font(TYPE.size_base, weight=TYPE.weight_semibold),
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=(
            (PALETTE.border if kind != "primary" else PALETTE.primary)
            if enabled
            else PALETTE.surface_alt  # borde atenuado = segunda señal, no solo color
        ),
        highlightcolor=PALETTE.focus,     # foco visible por teclado
        padx=SPACING.md,
        pady=SPACING.sm // 2,
        cursor="hand2" if enabled else "arrow",
    )
    if tooltip:
        _attach_tooltip(button, tooltip)
    return button


def configure_ttk_styles(root: tk.Misc) -> None:
    """Aplica la paleta al tema ttk (tablas, scrollbars). Una sola vez."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:  # pragma: no cover - tema no disponible
        pass

    # Fijar también los estilos ttk base evita que un ttk.Button futuro (o un
    # tema que herede este estilo) caiga silenciosamente en TkDefaultFont /
    # Helvetica. Los widgets ttk de la aplicación usan estilos propios, pero
    # el contrato tipográfico debe ser seguro para la familia estándar también.
    style.configure("TButton", font=TYPE.font(TYPE.size_base, weight=TYPE.weight_semibold))

    style.configure(
        "Yachay.Treeview",
        background=PALETTE.surface,
        fieldbackground=PALETTE.surface,
        foreground=PALETTE.text,
        bordercolor=PALETTE.border,
        rowheight=36,
        font=TYPE.font(TYPE.size_base),
    )
    style.configure(
        "Yachay.Treeview.Heading",
        background=PALETTE.surface_alt,
        foreground=PALETTE.primary,
        relief="flat",
        font=TYPE.font(TYPE.size_base, weight=TYPE.weight_semibold),
        padding=(SPACING.md, SPACING.sm),
        # El encabezado se alinea a la izquierda como los datos (FE-005): un
        # encabezado centrado sobre una columna alineada a la izquierda hace
        # ilegible la correspondencia columna↔valor.
        anchor="w",
    )
    style.configure(
        "Yachay.TCombobox",
        font=TYPE.font(TYPE.size_base),
        foreground=PALETTE.text,
        fieldbackground=PALETTE.surface_alt,
        background=PALETTE.surface_alt,
        bordercolor=PALETTE.border,
        arrowsize=SPACING.md,
    )
    style.map(
        "Yachay.Treeview",
        background=[("selected", PALETTE.primary)],
        foreground=[("selected", PALETTE.background)],
    )
    style.map(
        "Yachay.Treeview.Heading",
        background=[("active", PALETTE.surface_alt)],
    )
    style.configure(
        "Yachay.Vertical.TScrollbar",
        background=PALETTE.surface_alt,
        troughcolor=PALETTE.background,
        bordercolor=PALETTE.background,
        arrowcolor=PALETTE.disabled_text,
    )
    style.configure(
        "Yachay.Horizontal.TScrollbar",
        background=PALETTE.surface_alt,
        troughcolor=PALETTE.background,
        bordercolor=PALETTE.background,
        arrowcolor=PALETTE.disabled_text,
    )


def make_table(
    master: tk.Misc,
    columns: list[tuple[str, str, int, int]],
    *,
    selectmode: str = "browse",
    stretch_column: str | None = None,
) -> tuple[ttk.Treeview, tk.Frame]:
    """Tabla con scroll y columnas coherentes, dentro de su contenedor.

    `columns` es una lista de ``(clave, encabezado, ancho, minimo)``. La tabla y
    sus dos scrollbars se colocan en una rejilla propia; el llamador solo tiene
    que hacer `grid` del contenedor devuelto.

    Reglas de anchura (FE-003/FE-006):
    - exactamente UNA columna se estira (`stretch_column`) y absorbe el espacio
      sobrante, de modo que el ancho total no desborda la tarjeta;
    - el resto queda fijo con su `minwidth`, y la scrollbar horizontal solo
      aparece si la ventana es más estrecha que la suma de mínimos.
    """
    holder = tk.Frame(master, background=PALETTE.surface)
    holder.rowconfigure(0, weight=1)
    holder.columnconfigure(0, weight=1)

    keys = [column[0] for column in columns]
    heading_font = tkfont.Font(
        root=master,
        font=ttk.Style(master).lookup("Yachay.Treeview.Heading", "font"),
    )
    heading_padding = SPACING.md * 2
    minimum_widths = {
        key: max(minwidth, heading_font.measure(heading) + heading_padding)
        for key, heading, _width, minwidth in columns
    }
    tree = ttk.Treeview(
        holder,
        columns=keys,
        show="headings",
        style="Yachay.Treeview",
        selectmode=selectmode,
    )
    if stretch_column is None:
        stretch_column = keys[-1]
    for key, heading, width, _minwidth in columns:
        tree.heading(key, text=heading, anchor="w")
        tree.column(
            key,
            width=width,
            minwidth=minimum_widths[key],
            anchor="w",
            stretch=key == stretch_column,
        )

    # Ajusta las columnas al ancho real del contenedor. Los anchos nominales
    # sirven como preferencia, pero no deben forzar recorte ni scrollbar en una
    # ventana donde la suma de mínimos sí cabe.
    def _fit_columns(_event=None) -> None:
        available = holder.winfo_width() - 22  # scrollbar vertical + borde
        if available <= 0:
            return
        preferred = {key: width for key, _heading, width, _min in columns}
        minimum = minimum_widths
        minimum_total = sum(minimum.values())
        if available < minimum_total:
            widths = minimum
        else:
            extra = available - minimum_total
            preferred_extra = sum(
                max(0, preferred[key] - minimum[key]) for key in preferred
            )
            widths = {}
            for key in preferred:
                share = (
                    max(0, preferred[key] - minimum[key]) / preferred_extra
                    if preferred_extra
                    else 0
                )
                widths[key] = minimum[key] + round(extra * share)
            widths[stretch_column] += available - sum(widths.values())
        for key, width in widths.items():
            tree.column(key, width=max(minimum[key], width))

    yscroll = ttk.Scrollbar(
        holder, orient="vertical", command=tree.yview,
        style="Yachay.Vertical.TScrollbar",
    )
    xscroll = ttk.Scrollbar(
        holder, orient="horizontal", command=tree.xview,
        style="Yachay.Horizontal.TScrollbar",
    )
    tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    yscroll.grid(row=0, column=1, sticky="ns")

    # La scrollbar horizontal solo se muestra si el contenido realmente no cabe
    # (FE-003): una barra en reposo bajo una tabla que cabe parece un desborde.
    def _sync_xscroll(first: str, last: str) -> None:
        xscroll.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            xscroll.grid_remove()
        else:
            xscroll.grid(row=1, column=0, sticky="ew")

    tree.configure(xscrollcommand=_sync_xscroll)
    xscroll.grid(row=1, column=0, sticky="ew")
    holder.bind("<Configure>", _fit_columns, add="+")
    _fit_columns()
    _sync_xscroll(*tree.xview())

    return tree, holder


def responsive_wrap(widget: tk.Label, *, inset: int = 0) -> tk.Label:
    """Mantiene el texto envuelto dentro del ancho real de su contenedor."""
    parent = widget.master

    def _resize(_event=None) -> None:
        width = parent.winfo_width() - inset
        if width > 1:
            widget.configure(wraplength=width)

    parent.bind("<Configure>", _resize, add="+")
    _resize()
    return widget


def _attach_tooltip(widget: tk.Widget, text: str) -> None:
    """Tooltip mínimo: aparece al pasar el ratón, sin dependencias."""
    state = {"window": None}

    def show(_event=None):
        if state["window"] is not None:
            return
        x = widget.winfo_rootx() + 12
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        window = tk.Toplevel(widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        tk.Label(
            window,
            text=text,
            background=PALETTE.surface_alt,
            foreground=PALETTE.text,
            font=TYPE.font(TYPE.size_sm),
            padx=SPACING.sm,
            pady=SPACING.xs,
            highlightbackground=PALETTE.border,
            highlightthickness=1,
        ).pack()
        state["window"] = window

    def hide(_event=None):
        if state["window"] is not None:
            state["window"].destroy()
            state["window"] = None

    widget.bind("<Enter>", show, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<ButtonPress>", hide, add="+")
