"""Yachay Desktop — interfaz gráfica de escritorio para el motor de datos local.

Paquete de UI autónomo (tkinter/ttk). No importa el paquete `motor`: habla con
la API v1 exclusivamente por su socket Unix, igual que cualquier otro cliente.
Esta separación es deliberada y refleja la arquitectura del motor (ADR-016): la
UI es un cliente de solo lectura más tres acciones acotadas, nunca una autoridad
de estado.
"""

__version__ = "0.1.0"
