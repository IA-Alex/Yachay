"""Tokens visuales oficiales de Yachay (sección 18 del documento canónico).

Única fuente de verdad para color, espaciado y tipografía en la interfaz de
escritorio. Ningún widget define un color literal: todos consumen estos tokens.

La paleta es la identidad cromática oficial; no se le atribuyen significados
culturales. Los nombres son semánticos para que escritorio y CLI compartan
intención.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Resolución de fuentes y glifos (FE-001)
#
# Tk resuelve familias SOLO contra las fuentes que él mismo enumera. En un
# sistema con X11 core fonts, un nombre instalado por fontconfig pero ausente de
# esa lista (p. ej. «DejaVu Sans» en este build de Tk 9) se colapsa a `fixed`,
# un bitmap que NO contiene `✓ ✕ ⚠ ⏸`. El resultado era tofu en las tablas: los
# estados dejaban de distinguirse, que es justo lo que la sección 18 prohíbe.
#
# Aquí se resuelve el primer candidato que Tk reconozca de verdad y se comprueba
# que sepa dibujar los símbolos de estado. Si ninguno los dibuja, los símbolos se
# degradan a ASCII inequívoco: el contrato «nunca solo color» sobrevive.
# ---------------------------------------------------------------------------

# Familias verificadas contra Tk 9.0.3 sobre X11 en Debian/GNOME.
# No se incluyen nombres que fontconfig conozca pero que Tk pueda sustituir por
# `fixed`: la UI debe tener una resolución determinista y libre de bitmap.
_UI_FONT_CANDIDATES = ("Nimbus Sans L",)
_MONO_FONT_CANDIDATES = ("LucidaTypewriter",)

# Símbolos decorativos por defecto (sección 18) y su equivalente ASCII.
_SYMBOLS_UI = "→…▶✓▤✕⚠⊘⏸"
_SYMBOLS_ASCII = {
    "→": "->", "…": "...", "▶": ">", "✓": "[ok]", "▤": "[#]",
    "✕": "[x]", "⚠": "[!]", "⊘": "[/]", "⏸": "[||]",
}


def _resolved_family(candidates: tuple[str, ...], fallback: str) -> str:
    """Primera familia de `candidates` que Tk resuelve a sí misma, no a `fixed`.

    Requiere una raíz Tk ya creada; se importa `tkinter` de forma perezosa y se
    captura el fallo si aún no hay raíz o no hay display. En ese caso devuelve
    el primer candidato nominal: el valor se recalcula al construir la ventana.
    """
    try:
        from tkinter import font as tkfont

        available = {name.lower() for name in tkfont.families()}
    except Exception:  # pragma: no cover - sin Tk, sin raíz o sin display
        return candidates[0] if candidates else fallback

    for name in candidates:
        if name.lower() in available:
            try:
                candidate = tkfont.Font(family=name, size=10)
                # Tk hace coerción silenciosa a `fixed`; la descartamos.
                if candidate.actual("family").lower() != "fixed":
                    return name
            except Exception:  # pragma: no cover - familia no instanciable
                continue
    return fallback


def _family_supports_symbols(family: str) -> bool:
    """Indica si `family` dibuja todos los símbolos de estado con ancho propio.

    Un glifo ausente cae al bitmap `fixed`, que lo mide como caja (7 px). Se
    compara contra el ancho del bloque de reemplazo `\ufffd` para detectarlo.
    """
    try:
        from tkinter import font as tkfont

        probe = tkfont.Font(family=family, size=10, weight="bold")
        tofu = probe.measure("\ufffd")
        return all(probe.measure(symbol) != tofu for symbol in _SYMBOLS_UI)
    except Exception:  # pragma: no cover - sin Tk disponible
        return False


class _ResolvedFonts:
    """Fuentes y mapa de símbolos resueltos para el entorno actual.

    La resolución es perezosa y se memoiza: `theme.py` se puede importar sin una
    raíz Tk (el gate corre sin sesión gráfica) y el valor real se fija la primera
    vez que una ventana pide una fuente.
    """

    def __init__(self) -> None:
        self._ui: str | None = None
        self._mono: str | None = None
        self._symbols: bool | None = None

    def _ensure(self) -> None:
        if self._ui is not None:
            return
        self._ui = _resolved_family(_UI_FONT_CANDIDATES, "Nimbus Sans L")
        self._mono = _resolved_family(_MONO_FONT_CANDIDATES, "LucidaTypewriter")
        self._symbols = _family_supports_symbols(self._ui)

    @property
    def ui(self) -> str:
        self._ensure()
        return self._ui or "Nimbus Sans L"

    @property
    def mono(self) -> str:
        self._ensure()
        return self._mono or "LucidaTypewriter"

    @property
    def symbols_available(self) -> bool:
        self._ensure()
        return bool(self._symbols)

    def symbol(self, char: str) -> str:
        """Devuelve el símbolo si la fuente lo dibuja, o su forma ASCII."""
        if self.symbols_available:
            return char
        return _SYMBOLS_ASCII.get(char, char)


_RESOLVED = _ResolvedFonts()


@dataclass(frozen=True)
class Palette:
    """Los cinco tokens canónicos + seis auxiliares de estado (sección 18)."""

    # Paleta base (los cinco tokens oficiales).
    background: str = "#101B23"  # superficie principal
    surface: str = "#20313A"     # paneles, tablas, tarjetas
    primary: str = "#36B6A0"     # acciones, selección, interactivos
    accent: str = "#D5A85D"      # información destacada / jerarquía secundaria
    text: str = "#F0F5F3"        # contenido principal y encabezados

    # Tokens auxiliares de estado (independientes de la identidad de marca).
    success: str = "#49D17D"     # completado / saludable
    warning: str = "#F0C674"     # atención / degradado
    error: str = "#FF6B6B"       # fallo / bloqueo
    info: str = "#6CB6FF"        # información contextual
    focus: str = "#7DD3FC"       # foco visible
    disabled: str = "#82909A"    # no disponible (uso no textual o sobre `background`)
    # El token `disabled` sobre `surface` da 4.10:1 (< 4.5:1 AA). Este token
    # derivado, más claro, se usa como TEXTO deshabilitado sobre `surface`
    # (5.2:1) para cumplir AA sin alterar la identidad de marca.
    disabled_text: str = "#95A3AC"

    # Superficies derivadas, para profundidad sutil sin salir de la paleta.
    surface_alt: str = "#1A2830"  # filas alternas / encabezados de tabla
    sidebar: str = "#16232B"      # plano de navegación, un escalón bajo `surface`
    border: str = "#2C3E48"       # líneas de 1 px (derivada de surface)


@dataclass(frozen=True)
class Spacing:
    """Escala única de 4 px. Nada intermedio."""

    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24
    xxl: int = 32


@dataclass(frozen=True)
class Typography:
    """Familias resueltas para el entorno + escala de tamaños y pesos.

    `family`/`mono` son propiedades: se resuelven la primera vez que una ventana
    las consulta (no al importar), y devuelven la familia real que Tk sabe
    instanciar aquí. Los nombres de la identidad viven en las cadenas de
    preferencia `_UI_FONT_CANDIDATES`/`_MONO_FONT_CANDIDATES`.
    """

    # Escala lógica conservada del diseño existente. `font()` la convierte a
    # píxeles físicos al crear cada especificación Tk.
    size_micro: int = 10
    size_sm: int = 11
    size_base: int = 12
    size_lg: int = 14
    size_title: int = 22  # título de vista: jerarquía clara sobre `size_lg`
    # Tk 9 sobre X11 solo garantiza los pesos semánticos `normal` y `bold`.
    # La jerarquía de diseño se expresa con esos dos valores compatibles.
    weight_regular: str = "normal"       # 400
    weight_medium: str = "normal"        # 500
    weight_semibold: str = "bold"        # 600
    weight_bold: str = "bold"            # 700
    # Compatibilidad con las referencias históricas de la UI.
    weight_normal: str = "normal"

    def pixel_size(self, logical_size: int) -> int:
        """Convierte la escala lógica a píxeles físicos para Tk.

        En Tk, un tamaño positivo se interpreta como puntos y se vuelve a
        escalar con `tk scaling`. Con el escalado fraccional 1.666 de GNOME/X11
        eso puede introducir redondeos visibles en contornos pequeños. Un
        tamaño negativo expresa píxeles físicos; aquí se conserva la escala
        lógica de diseño y se evita una segunda interpolación del backend.
        """
        try:
            import tkinter as tk

            root = tk._default_root
            scaling = float(root.tk.call("tk", "scaling")) if root else 1.0
        except Exception:  # pragma: no cover - sin sesión gráfica
            scaling = 1.0
        return -max(1, round(abs(logical_size) * scaling))

    def font(
        self,
        logical_size: int,
        *,
        mono: bool = False,
        weight: str | None = None,
    ) -> tuple[str, int] | tuple[str, int, str]:
        """Devuelve una especificación Tk con tamaño físico y familia central."""
        family = self.mono if mono else self.family
        size = self.pixel_size(logical_size)
        return (family, size, weight) if weight else (family, size)

    @property
    def family(self) -> str:
        """Familia de UI realmente instanciable por Tk en este entorno."""
        return _RESOLVED.ui

    @property
    def mono(self) -> str:
        """Familia monoespaciada realmente instanciable por Tk."""
        return _RESOLVED.mono

    @property
    def symbols_available(self) -> bool:
        """True si la familia dibuja los símbolos de estado (no tofu)."""
        return _RESOLVED.symbols_available

    def symbol(self, char: str) -> str:
        """Símbolo de estado, degradado a ASCII si la fuente no lo dibuja."""
        return _RESOLVED.symbol(char)


# Etiquetas en español y token de estado para cada estado real del motor
# (motor/storage/schema.py: JOB_STATES). El color nunca es la única señal:
# cada estado lleva una etiqueta textual y un símbolo.
JOB_STATE_LABELS: dict[str, tuple[str, str, str]] = {
    # estado        : (etiqueta es,    token de color, símbolo único)
    "claimed": ("Reclamado", "info", "→"),
    "queued": ("En cola", "info", "…"),
    "running": ("En proceso", "primary", "▶"),
    "committed": ("Completado", "success", "✓"),
    "archived": ("Archivado", "disabled", "▤"),
    "failed": ("Fallido", "error", "✕"),
    "quarantined": ("En cuarentena", "warning", "⚠"),
    "cancelled": ("Cancelado", "disabled", "⊘"),
    "deferred": ("Diferido", "warning", "⏸"),
}

# Símbolos de integridad del catálogo: inequívocos y opuestos (no comparten glifo
# con ningún estado de la cola, para que no se confundan entre vistas).
INTEGRITY_OK = ("✓", "Íntegro", "success")
INTEGRITY_MISSING = ("⚠", "Manifest ausente", "warning")

# Estados sobre los que la API permite cada acción (motor/api/routes.py).
CANCELLABLE_STATES = ("claimed", "queued", "running", "deferred")
RETRYABLE_STATES = ("failed", "quarantined", "cancelled")

PALETTE = Palette()
SPACING = Spacing()
TYPE = Typography()


def resolve_symbol(symbol: str) -> str:
    """Símbolo de estado listo para pintar (ASCII si la fuente no lo dibuja).

    Fuente única para widgets y vistas: así ni la cola ni el catálogo dependen
    de que la familia resuelta contenga el glifo (FE-001).
    """
    return _RESOLVED.symbol(symbol)


def state_label(state: str) -> tuple[str, str, str]:
    """(etiqueta, token de color, símbolo) de un estado, símbolo ya resuelto."""
    label, token, symbol = JOB_STATE_LABELS.get(
        state, (state or "Desconocido", "disabled", "?")
    )
    return label, token, resolve_symbol(symbol)


def filter_options() -> dict[str, str | None]:
    """Mapa etiqueta en español → estado crudo (o None para «Todos»).

    El filtro de la cola lee el estado crudo del motor; aquí se traduce a la
    etiqueta española para que el control no mezcle idiomas (FE-007).
    """
    options: dict[str, str | None] = {"Todos": None}
    for state in JOB_STATE_LABELS:
        label, _token, symbol = state_label(state)
        options[f"{symbol}  {label}"] = state
    return options


# Validación cromática documentada (sección 18): estas combinaciones cumplen
# los ratios anotados. Sirven como referencia; la UI no las recalcula.
CONTRAST_NOTES = {
    "text/background": "15.84:1 (cumple 4.5:1)",
    "text/surface": "12.20:1 (cumple 4.5:1)",
    "primary/background": "6.95:1 (cumple 4.5:1)",
    "accent/background": "7.97:1 (cumple 4.5:1)",
}
