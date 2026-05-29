# -*- coding: utf-8 -*-
"""
Monitor de concursos de CFE (Micrositio de Concursos / MSC).

Que hace al correr:
  1. Abre la busqueda publica del MSC con un navegador real (Playwright).
  2. Lee la lista de concursos y detecta los NUEVOS (compara contra estado/vistos.json).
  3. De cada concurso nuevo descarga los anexos PDF y les extrae el texto.
  4. Marca los concursos cuya descripcion o anexos contengan palabras clave
     de recubrimientos / pinturas (ver keywords.py).
  5. Escribe un reporte y, si hay hallazgos, genera salida/nuevo_issue.md
     (el workflow lo convierte en un Issue de GitHub que te llega por correo).

IMPORTANTE: la funcion extraer_lista_concursos() es la parte que casi
seguro hay que AFINAR contra la pagina real de CFE. Por eso cada corrida
guarda en depuracion/ una captura y el HTML de la pagina, para poder
ajustar los selectores sin tener que entrar al sitio (que bloquea bots).
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

# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------
BASE = "https://msc.cfe.mx"
# Pagina de entrada de concursos. Desde aqui se intenta llegar a la lista.
URL_INICIO = "https://msc.cfe.mx/Aplicaciones/NCFE/Concursos/"

RAIZ = Path(__file__).parent
ESTADO = RAIZ / "estado" / "vistos.json"
DIR_REPORTES = RAIZ / "reportes"
DIR_SALIDA = RAIZ / "salida"
DIR_DEBUG = RAIZ / "depuracion"

# Limite de anexos por concurso para no descargar gigas (ajustable)
MAX_ANEXOS = 15
# Tamano maximo de PDF a procesar (MB)
MAX_PDF_MB = 25

HOY = datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def normaliza(texto: str) -> str:
    """Minusculas + sin acentos, para comparar sin importar tildes."""
    texto = texto.lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto


# Regex de cada palabra clave, ya normalizada y con limites de palabra.
_PATRONES = [
    (kw, re.compile(r"\b" + re.escape(normaliza(kw)) + r"\b"))
    for kw in PALABRAS_CLAVE
]


def buscar_coincidencias(texto: str):
    """Devuelve lista de (palabra_clave, fragmento_contexto)."""
    norm = normaliza(texto)
    hallazgos = []
    vistos = set()
    for kw, patron in _PATRONES:
        m = patron.search(norm)
        if m and kw not in vistos:
            vistos.add(kw)
            ini = max(0, m.start() - 60)
            fin = min(len(texto), m.end() + 60)
            # Tomamos el fragmento del texto ORIGINAL (con acentos) por posicion
            fragmento = texto[ini:fin].replace("\n", " ").strip()
            fragmento = re.sub(r"\s+", " ", fragmento)
            hallazgos.append((kw, fragmento))
    return hallazgos


def cargar_estado():
    if ESTADO.exists():
        try:
            return set(json.loads(ESTADO.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def guardar_estado(vistos: set):
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(
        json.dumps(sorted(vistos), ensure_ascii=False, indent=0),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------
def extraer_lista_concursos(page):
    """
    Devuelve una lista de dicts: {id, numero, descripcion, url_detalle}.

    >>> ZONA A AFINAR <<<
    Estrategia generica: busca cualquier tabla y filas que contengan un enlace
    hacia 'Detalle'. Cuando veamos el HTML real (carpeta depuracion/) ajustamos
    los selectores exactos aqui.
    """
    concursos = []

    # Guardamos evidencia para depurar / afinar selectores luego.
    DIR_DEBUG.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(DIR_DEBUG / f"pantalla_{HOY}.png"), full_page=True)
        (DIR_DEBUG / f"pagina_{HOY}.html").write_text(page.content(), encoding="utf-8")
    except Exception as e:
        print(f"[debug] no se pudo guardar evidencia: {e}")

    # Intento 1: enlaces que apuntan a la vista de detalle del concurso.
    enlaces = page.locator("a[href*='Detalle'], a[href*='detalle']")
    n = enlaces.count()
    print(f"[lista] enlaces a detalle encontrados: {n}")

    for i in range(n):
        try:
            a = enlaces.nth(i)
            href = a.get_attribute("href") or ""
            texto_fila = ""
            # Subimos a la fila de la tabla para tomar numero/descripcion
            fila = a.locator("xpath=ancestor::tr[1]")
            if fila.count() > 0:
                texto_fila = fila.inner_text(timeout=2000)
            else:
                texto_fila = a.inner_text(timeout=2000)

            url = href if href.startswith("http") else BASE + href
            # ID = parametro Id de la URL, o el href completo si no hay
            m = re.search(r"[?&]Id=([^&]+)", href)
            ident = m.group(1) if m else href

            concursos.append({
                "id": ident,
                "numero": _primer_codigo(texto_fila),
                "descripcion": " ".join(texto_fila.split())[:300],
                "url_detalle": url,
            })
        except Exception as e:
            print(f"[lista] fila {i} omitida: {e}")

    # Deduplicar por id
    unicos = {}
    for c in concursos:
        unicos[c["id"]] = c
    return list(unicos.values())


def _primer_codigo(texto: str) -> str:
    """Intenta extraer un numero de procedimiento tipo 'CFE-0013-...'."""
    m = re.search(r"CFE[-\w]+", texto)
    return m.group(0) if m else "(sin numero)"


def obtener_links_anexos(page, url_detalle):
    """Abre el detalle del concurso y regresa las URLs de los anexos (PDF)."""
    links = []
    try:
        page.goto(url_detalle, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
    except Exception as e:
        print(f"[detalle] no abrio {url_detalle}: {e}")
        return links

    # GetAnexo es el endpoint tipico de descarga de anexos en el MSC.
    anclas = page.locator("a[href*='GetAnexo'], a[href*='Anexo'], a[href$='.pdf']")
    for i in range(min(anclas.count(), MAX_ANEXOS)):
        href = anclas.nth(i).get_attribute("href") or ""
        if not href:
            continue
        url = href if href.startswith("http") else BASE + href
        links.append(url)
    return list(dict.fromkeys(links))  # dedup conservando orden


def descargar_y_extraer(contexto, url):
    """Descarga un anexo usando la sesion del navegador y extrae su texto."""
    try:
        resp = contexto.request.get(url, timeout=60000)
        if not resp.ok:
            print(f"[anexo] {resp.status} en {url}")
            return ""
        datos = resp.body()
        if len(datos) > MAX_PDF_MB * 1024 * 1024:
            print(f"[anexo] demasiado grande, omitido: {url}")
            return ""
        # Solo intentamos extraer texto si parece PDF
        if datos[:4] != b"%PDF":
            return ""
        return extract_text(io.BytesIO(datos)) or ""
    except Exception as e:
        print(f"[anexo] error con {url}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------
def escribir_reportes(hallazgos):
    DIR_REPORTES.mkdir(parents=True, exist_ok=True)
    DIR_SALIDA.mkdir(parents=True, exist_ok=True)

    ruta_md = DIR_REPORTES / f"{HOY}.md"
    lineas = [f"# Concursos CFE con recubrimientos / pinturas — {HOY}", ""]

    if not hallazgos:
        lineas.append("Sin coincidencias nuevas hoy.")
    else:
        lineas.append(f"**{len(hallazgos)} concurso(s) con coincidencias:**")
        lineas.append("")
        for h in hallazgos:
            lineas.append(f"## {h['numero']}")
            lineas.append(f"- **Descripcion:** {h['descripcion']}")
            lineas.append(f"- **Detalle:** {h['url_detalle']}")
            kws = ", ".join(sorted({k for k, _ in h["coincidencias"]}))
            lineas.append(f"- **Palabras encontradas:** {kws}")
            lineas.append(f"- **Donde:** {h['fuente']}")
            # Un fragmento de ejemplo
            if h["coincidencias"]:
                kw, frag = h["coincidencias"][0]
                lineas.append(f"- **Contexto:** ...{frag}...")
            lineas.append("")

    contenido = "\n".join(lineas)
    ruta_md.write_text(contenido, encoding="utf-8")
    print(f"[reporte] escrito en {ruta_md}")

    # Solo creamos el archivo para el Issue si HAY hallazgos
    if hallazgos:
        (DIR_SALIDA / "nuevo_issue.md").write_text(contenido, encoding="utf-8")
        print("[reporte] salida/nuevo_issue.md generado (habra Issue)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    vistos = cargar_estado()
    print(f"[estado] concursos ya vistos: {len(vistos)}")

    hallazgos = []

    with sync_playwright() as p:
        navegador = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        contexto = navegador.new_context(
            locale="es-MX",
            timezone_id="America/Mexico_City",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )
        # Pequeno truco para ocultar el flag de automatizacion
        contexto.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = contexto.new_page()

        print(f"[nav] abriendo {URL_INICIO}")
        page.goto(URL_INICIO, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)

        concursos = extraer_lista_concursos(page)
        print(f"[lista] concursos detectados: {len(concursos)}")

        nuevos = [c for c in concursos if c["id"] not in vistos]
        print(f"[lista] concursos NUEVOS: {len(nuevos)}")

        for c in nuevos:
            vistos.add(c["id"])
            fuente = None
            coincidencias = buscar_coincidencias(c["descripcion"])
            if coincidencias:
                fuente = "descripcion"

            # Si la descripcion no basta, revisamos los anexos PDF
            if not coincidencias:
                anexos = obtener_links_anexos(page, c["url_detalle"])
                print(f"[detalle] {c['numero']}: {len(anexos)} anexo(s)")
                for url in anexos:
                    texto = descargar_y_extraer(contexto, url)
                    if not texto:
                        continue
                    co = buscar_coincidencias(texto)
                    if co:
                        coincidencias = co
                        fuente = url
                        break

            if coincidencias:
                c["coincidencias"] = coincidencias
                c["fuente"] = fuente
                hallazgos.append(c)
                print(f"[HALLAZGO] {c['numero']} -> {[k for k,_ in coincidencias]}")

        navegador.close()

    escribir_reportes(hallazgos)
    guardar_estado(vistos)
    print(f"[fin] hallazgos hoy: {len(hallazgos)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # No queremos que el workflow truene; guardamos lo que se pueda.
        print(f"[ERROR] {e}", file=sys.stderr)
        DIR_DEBUG.mkdir(parents=True, exist_ok=True)
        (DIR_DEBUG / f"error_{HOY}.txt").write_text(str(e), encoding="utf-8")
        sys.exit(0)
