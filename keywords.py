# -*- coding: utf-8 -*-
"""
Palabras clave para detectar licitaciones relacionadas con
recubrimientos y pinturas (anticorrosivos / industriales,
impermeabilizantes y pintura decorativa / arquitectonica).

Puedes agregar o quitar terminos libremente. La busqueda NO distingue
mayusculas ni acentos (se normaliza el texto antes de comparar), asi que
no hace falta poner las versiones con y sin acento.
"""

PALABRAS_CLAVE = [
    # --- Genericas ---
    "pintura",
    "pinturas",
    "recubrimiento",
    "recubrimientos",
    "revestimiento",

    # --- Anticorrosivos / industriales ---
    "anticorrosivo",
    "anticorrosiva",
    "anticorrosion",
    "epoxico",
    "epoxica",
    "epoxi",
    "epoxy",
    "poliuretano",
    "alquidalico",
    "primario anticorrosivo",
    "imprimante",
    "imprimador",
    "rica en zinc",
    "galvanizado en frio",
    "intumescente",
    "retardante de fuego",
    "esmalte",
    "esmaltes",
    "preparacion de superficie",
    "sandblast",
    "chorro de arena",

    # --- Impermeabilizantes ---
    "impermeabilizante",
    "impermeabilizacion",
    "impermeabilizar",
    "membrana asfaltica",
    "acrilico impermeabilizante",

    # --- Decorativa / arquitectonica ---
    "vinilica",
    "vinil acrilica",
    "acrilica",
    "latex",
    "barniz",
    "sellador",
    "selladora",
    "esmalte alquidalico",
]
