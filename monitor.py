# -*- coding: utf-8 -*-
"""
Monitor de concursos de CFE (Micrositio de Concursos / MSC) - v2.1

Enfoque robusto: en vez de imitar la peticion AJAX (que el servidor rechaza
con error 500), el robot hace CLIC en el boton "Buscar" real de la pagina y
luego lee la tabla de resultados directo de la memoria del navegador
(el dataSource de Kendo UI). Asi la pagina usa su propio token y todo cuadra.

  1. Abre la pagina y hace clic en "Buscar" (sin filtros = todos los vigentes).
  2. Lee la lista completa de concursos del grid.
  3. Detecta los NUEVOS (compara contra estado/vistos.json por numero).
  4. Filtra por palabras clave: primero en la descripcion; si no, abre el
     detalle (ventana real) y revisa los anexos PDF.
  5. Escribe reporte y, si hay hallazgos, genera salida/nuevo_issue.md.
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

RAIZ = Path(__file__).parent
ESTADO = RAIZ / "estado" / "vistos.json"
DIR_REPORTES = RAIZ / "reportes"
DIR_SALIDA = RAIZ / "salida"
DIR_DEBUG = RAIZ / "depuracion"

MAX_ANEXOS = 12
MAX_PDF_MB = 25
MAX_DETALLES = 30   # cuantos detalles abrir por corrida (para no eternizarse)

HOY = datetime.now().strftime("%Y-%m-%d")


# ----------------------- Coincidencias -----------------------
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


# ----------------------- Estado -----------------------
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


# ----------------------- Lista de concursos -----------------------
JS_LEER_GRID = r"""
() => {
  try {
    if (!window.$jq1) return null;
    var g = $jq1('#gridProcesos').data('kendoGrid');
    if (!g || !g.dataSource) return null;
    var d = g.dataSource.data();
    var out = [];
    for (var i = 0; i < d.length; i++) {
      var x = d[i];
      out.push({
        Numero: x.Numero, Descripcion: x.Descripcion,
        EntidadFederativa: x.EntidadFederativa,
        FechaPublicacion: x.FechaPublicacion,
        EstadoProcedimiento: x.EstadoProcedimiento,
        Id: x.Id
      });
    }
    return out;
  } catch (e) { return null; }
}
"""


def obtener_lista(page):
    """Hace clic en Buscar y lee el grid. Devuelve lista de dicts."""
    DIR_DEBUG.mkdir(parents=True, exist_ok=True)
    # Clic en el boton Buscar
    try:
        page.click("#buscar", timeout=15000)
    except Exception as e:
        print("[lista] no se pudo clic en #buscar: %s" % e)

    # Esperar a que el grid de Kendo tenga datos (hasta ~40s)
    datos = None
    for intento in range(40):
        page.wait_for_timeout(1000)
        try:
            datos = page.evaluate(JS_LEER_GRID)
        except Exception:
            datos = None
        if datos:
            break

    # Guardar evidencia siempre
    try:
        page.screenshot(path=str(DIR_DEBUG / ("pantalla_%s.png" % HOY)),
                        full_page=True)
        (DIR_DEBUG / ("pagina_%s.html" % HOY)).write_text(
            page.content(), encoding="utf-8")
    except Exception:
        pass

    if not datos:
        print("[lista] el grid no devolvio datos")
        return []
    print("[lista] concursos leidos del grid: %d" % len(datos))
    (DIR_DEBUG / ("lista_%s.json" % HOY)).write_text(
        json.dumps(datos, ensure_ascii=False)[:200000], encoding="utf-8")
    return datos


def _campo(d, *nombres):
    for n in nombres:
        if n in d and d[n] not in (None, ""):
            return d[n]
    return ""


def normaliza_concurso(d):
    return {
        "numero": str(_campo(d, "Numero", "numero")),
        "descripcion": str(_campo(d, "Descripcion", "descripcion")),
        "entidad": str(_campo(d, "EntidadFederativa", "entidadFederativa")),
        "fecha": str(_campo(d, "FechaPublicacion", "fechaPublicacion")),
        "estado": str(_campo(d, "EstadoProcedimiento", "estadoProcedimiento")),
        "id": str(_campo(d, "Id", "id")),
    }


# ----------------------- Detalle / anexos -----------------------
def obtener_anexos(ctx, page, id_proc, guardar_muestra=False):
    """Abre el detalle (ventana real) via MostrarDetalle y saca URLs de PDF."""
    if not id_proc:
        return []
    pop = None
    try:
        with ctx.expect_page(timeout=20000) as pinfo:
            page.evaluate("(id) => { MostrarDetalle(id); }", id_proc)
        pop = pinfo.value
        pop.wait_for_load_state("domcontentloaded", timeout=20000)
        pop.wait_for_timeout(1500)
        html = pop.content()
    except Exception as e:
        print("[detalle] id %s: %s" % (id_proc, e))
        if pop:
            try:
                pop.close()
            except Exception:
                pass
        return []

    if guardar_muestra:
        try:
            (DIR_DEBUG / ("detalle_muestra_%s.html" % HOY)).write_text(
                html[:300000], encoding="utf-8")
        except Exception:
            pass

    urls = set()
    for m in re.finditer(r'(GetAnexo/\d+)', html, re.IGNORECASE):
        urls.add(m.group(1))
    for m in re.finditer(r'href=["\']([^"\']*\.pdf[^"\']*)["\']',
                         html, re.IGNORECASE):
        urls.add(m.group(1))

    absolutas = []
    for u in urls:
        if u.startswith("http"):
            absolutas.append(u)
        elif u.startswith("/"):
            absolutas.append("https://msc.cfe.mx" + u)
        else:
            absolutas.append(BASE + u.lstrip("/"))
    absolutas = list(dict.fromkeys(absolutas))[:MAX_ANEXOS]

    # Descargar y extraer texto de cada PDF (usando la ventana de detalle)
    textos = []
    for url in absolutas:
        try:
            resp = pop.request.get(url, timeout=60000)
            if not resp.ok:
                continue
            b = resp.body()
            if len(b) > MAX_PDF_MB * 1024 * 1024 or b[:4] != b"%PDF":
                continue
            textos.append(extract_text(io.BytesIO(b)) or "")
        except Exception:
            continue
    try:
        pop.close()
    except Exception:
        pass
    return textos


# ----------------------- Reporte -----------------------
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


# ----------------------- Main -----------------------
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
        page.wait_for_timeout(4000)

        crudos = obtener_lista(page)
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
                textos = obtener_anexos(ctx, page, c["id"],
                                        guardar_muestra=muestra)
                muestra = False
                for texto in textos:
                    cc = buscar_coincidencias(texto)
                    if cc:
                        co, fuente = cc, "anexo PDF"
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
