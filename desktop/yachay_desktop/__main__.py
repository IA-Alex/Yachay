"""Yachay Desktop — interfaz de escritorio para el motor de datos local.

Una aplicación de escritorio mínima, ligera y elegante que se comunica con la
API v1 de Yachay a través de su socket Unix. No usa frameworks: tkinter/ttk de
la biblioteca estándar.

Uso:
    python -m yachay_desktop                 # socket por defecto <root>/data/motor.sock
    python -m yachay_desktop --socket /ruta/motor.sock
    yachay-desktop                            # si está instalado como script
"""

from yachay_desktop.app import main

if __name__ == "__main__":
    raise SystemExit(main())
