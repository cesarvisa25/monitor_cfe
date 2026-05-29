# -*- coding: utf-8 -*-
"""
Monitor de concursos de CFE (Micrositio de Concursos / MSC) - v2

Ajustado a la estructura real del portal:
  - La lista de concursos se obtiene del endpoint Procedure/getProcBusqueda
    (es lo que hace el boton "Buscar" de la pagina).
  - El detalle de cada concurso (con sus anexos PDF) se obtiene de
    Procedure/Details enviando el Id del procedimiento.
"""

import io
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
from pdfminer.high_level import extract_text

from keywords import PALABRAS_CLAVE

BASE = "https://msc.cfe.mx/Aplicaciones/NCFE/Concursos/"
URL_INICIO = BASE
URL_BUSQUEDA = BASE + "Procedure/getProcBusqueda"
URL_DETALLE = BASE + "Procedure/Details"

RAIZ = Path(__file__).parent
ESTADO = RAIZ / "estado" / "vistos.json"
DIR_REPORTES = RAIZ / "reportes"
DIR_SALIDA = RAIZ / "salida"
DIR_DEBUG = RAIZ / "depuracion"

MAX_ANEXOS = 15
MAX_PDF_MB = 25
MAX_DETALLES = 120

HOY = datetime.now().strftime("%Y-%m-%d")


def normaliza(texto):
    texto = (texto or "").lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


_PATRONES = [(kw, re.compile(r"\b" + re.escape(normaliza(kw)) + r"\b"))
             for kw in PALABRAS_CLAVE]


def buscar_coincidencias(texto):
    norm = normaliza(texto)
    hallazgos, vistos = [], set()
    for kw, patron in _PATRONES:
        m = patron.search(norm)
        if m and kw not in vistos:
            vistos.add(kw)
            ini = max(0, m.start() - 60)
            fin = min(len(texto), m.end() + 60)
            frag = re.sub(r"\s+", " ", texto[ini:fin]).strip()
            hallazgos.append((kw, frag))
    return hallazgos


def cargar_estado():
    if ESTADO.exists():
        try:
            return set(json.loads(ESTADO.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def guardar_estado(vistos):
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(json.dumps(sorted(vistos), ensure_ascii=False),
                      encoding="utf-8")


def leer_token(page):
    try:
        return page.get_attribute(
            "input[name='__RequestVerificationToken']", "value") or ""
    except Exception:
        return ""


def obtener_lista(page, token):
    datos = {
        "__RequestVerificationToken": token,
        "TipoProcedimientoClave": "", "TipoContratacionClave": "",
        "IdEntidadFederativa": "", "Numero": "", "Descripcion": "",
        "EstadoProcedimientoContratacionClave": "", "FechaPublicacion": "",
        "FechaPublicacionIni": "", "FechaPublicacionFin": "",
        "TestigoSocial": "", "idCaracterProcedimiento": "", "Modalidad": "",
    }
    headers = {"X-Requested-With": "XMLHttpRequest", "Referer": URL_INICIO}
    resp = page.request.post(URL_BUSQUEDA, form=datos, headers=headers,
                             timeout=60000)
    DIR_DEBUG.mkdir(parents=True, exist_ok=True)
    cuerpo = resp.text()
    (DIR_DEBUG / ("busqueda_%s.json" % HOY)).write_text(
        cuerpo[:200000], encoding="utf-8")
    if not resp.ok:
        print("[busqueda] HTTP %s" % resp.status)
        return []
    try:
        data = json.loads(cuerpo)
    except Exception as e:
        print("[busqueda] respuesta no es JSON: %s" % e)
        return []
    if not isinstance(data, list):
        print("[busqueda] respuesta inesperada")
        return []
    print("[busqueda] concursos recibidos: %d" % len(data))
    return data


def _campo(d, *nombres):
    for n in nombres:
        if n in d and d[n] not in (None, ""):
            return d[n]
    return ""


def normaliza_concurso(d):
    return {
        "numero": str(_campo(d, "Numero", "numero", "NumeroProcedimiento")),
        "descripcion": str(_campo(d, "Descripcion", "descripcion")),
        "entidad": str(_campo(d, "EntidadFederativa", "entidadFederativa")),
        "fecha": str(_campo(d, "FechaPublicacion", "fechaPublicacion")),
        "estado": str(_campo(d, "EstadoProcedimiento", "estadoProcedimiento")),
        "id": str(_campo(d, "Id", "id", "IdProcedimiento", "idProcedimiento")),
    }


def obtener_anexos(page, token, id_proc, guardar_muestra=False):
    if not id_proc:
        return []
    headers = {"X-Requested-With": "XMLHttpRequest", "Referer": URL_INICIO}
    try:
        resp = page.request.post(
            URL_DETALLE,
            form={"id": id_proc, "__RequestVerificationToken": token},
            headers=headers, timeout=60000)
        if not resp.ok:
            return []
        html = resp.text()
    except Exception as e:
        print("[detalle] error id %s: %s" % (id_proc, e))
        return []

    if guardar_muestra:
        DIR_DEBUG.mkdir(parents=True, exist_ok=True)
        (DIR_DEBUG / ("detalle_muestra_%s.html" % HOY)).write_text(
            html[:300000], encoding="utf-8")

    urls = set()
    for m in re.finditer(r'href=["\']([^"\']*(?:GetAnexo|Anexo)[^"\']*)["\']',
                         html, re.IGNORECASE):
        urls.add(m.group(1))
    for m in re.finditer(r'href=["\']([^"\']*\.pdf[^"\']*)["\']',
                         html, re.IGNORECASE):
        urls.add(m.group(1))
    for m in re.finditer(r'(Procedure/GetAnexo/\d+)', html, re.IGNORECASE):
        urls.add(m.group(1))

    absolutas = []
    for u in urls:
        if u.startswith("http"):
            absolutas.append(u)
        elif u.startswith("/"):
            absolutas.append("https://msc.cfe.mx" + u)
        else:
            absolutas.append(BASE + u.lstrip("/"))
    return list(dict.fromkeys(absolutas))[:MAX_ANEXOS]


def descargar_y_extraer(page, url):
    try:
        resp = page.request.get(url, timeout=60000)
        if not resp.ok:
            return ""
        datos = resp.body()
        if len(datos) > MAX_PDF_MB * 1024 * 1024:
            return ""
        if datos[:4] != b"%PDF":
            return ""
        return extract_text(io.BytesIO(datos)) or ""
    except Exception as e:
        print("[anexo] error %s: %s" % (url, e))
        return ""


def escribir_reportes(hallazgos):
    DIR_REPORTES.mkdir(parents=True, exist_ok=True)
    DIR_SALIDA.mkdir(parents=True, exist_ok=True)
    lineas = ["# Concursos CFE con recubrimientos / pinturas - %s" % HOY, ""]
    if not hallazgos:
        lineas.append("Sin coincidencias nuevas hoy.")
    else:
        lineas.append("**%d concurso(s) con coincidencias:**\n" % len(hallazgos))
        for h in hallazgos:
            kws = ", ".join(sorted({k for k, _ in h["coincidencias"]}))
            lineas += [
                "## %s  (%s)" % (h["numero"], h["entidad"]),
                "- **Descripcion:** %s" % h["descripcion"],
                "- **Fecha publicacion:** %s" % h["fecha"],
                "- **Palabras encontradas:** %s" % kws,
                "- **Detectado en:** %s" % h["fuente"],
            ]
            if h["coincidencias"]:
                _, frag = h["coincidencias"][0]
                lineas.append("- **Contexto:** ...%s..." % frag)
            lineas.append("")
    contenido = "\n".join(lineas)
    (DIR_REPORTES / ("%s.md" % HOY)).write_text(contenido, encoding="utf-8")
    print("[reporte] %s.md" % HOY)
    if hallazgos:
        (DIR_SALIDA / "nuevo_issue.md").write_text(contenido, encoding="utf-8")
        print("[reporte] salida/nuevo_issue.md generado")


def main():
    vistos = cargar_estado()
    print("[estado] ya vistos: %d" % len(vistos))
    hallazgos = []

    with sync_playwright() as p:
        navegador = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = navegador.new_context(
            locale="es-MX", timezone_id="America/Mexico_City",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1366, "height": 900})
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()

        print("[nav] abriendo %s" % URL_INICIO)
        page.goto(URL_INICIO, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3500)

        token = leer_token(page)
        print("[token] %s" % ("ok" if token else "NO ENCONTRADO"))

        crudos = obtener_lista(page, token)
        concursos = [normaliza_concurso(c) for c in crudos]
        concursos = [c for c in concursos if c["numero"]]
        nuevos = [c for c in concursos if c["numero"] not in vistos]
        print("[lista] total=%d nuevos=%d" % (len(concursos), len(nuevos)))

        detalles = 0
        muestra = True
        for c in nuevos:
            vistos.add(c["numero"])
            co = buscar_coincidencias(c["descripcion"])
            fuente = "descripcion" if co else None
            if not co and detalles < MAX_DETALLES:
                detalles += 1
                anexos = obtener_anexos(page, token, c["id"],
                                        guardar_muestra=muestra)
                muestra = False
                for url in anexos:
                    texto = descargar_y_extraer(page, url)
                    if texto:
                        cc = buscar_coincidencias(texto)
                        if cc:
                            co, fuente = cc, url
                            break
            if co:
                c["coincidencias"] = co
                c["fuente"] = fuente
                hallazgos.append(c)
                print("[HALLAZGO] %s -> %s" % (c["numero"], [k for k, _ in co]))

        navegador.close()

    escribir_reportes(hallazgos)
    guardar_estado(vistos)
    print("[fin] hallazgos: %d" % len(hallazgos))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[ERROR] %s" % e, file=sys.stderr)
        DIR_DEBUG.mkdir(parents=True, exist_ok=True)
        (DIR_DEBUG / ("error_%s.txt" % HOY)).write_text(str(e), encoding="utf-8")
        sys.exit(0)
