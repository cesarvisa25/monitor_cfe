# Monitor de licitaciones CFE — recubrimientos y pinturas

Revisa todos los días hábiles el Micrositio de Concursos (MSC) de CFE,
detecta los concursos nuevos, descarga sus anexos PDF y te avisa cuando
alguno pide **recubrimientos o pinturas** (anticorrosivos / industriales,
impermeabilizantes o pintura decorativa).

Corre solo y gratis en **GitHub Actions**. No necesitas dejar tu compu prendida.

---

## 1. Qué necesitas

- Una cuenta de GitHub (gratis).
- Nada más. No se requiere tarjeta, servidor ni instalar programas.

## 2. Subir el proyecto a GitHub

1. Entra a https://github.com y crea un repositorio nuevo
   (botón **New** → ponle un nombre, ej. `monitor-cfe` → **Create**).
2. Sube **todos** los archivos de esta carpeta al repositorio
   (puedes arrastrarlos en *Add file → Upload files*, conservando la
   carpeta `.github/workflows/`).

## 3. Dar permisos al robot

GitHub debe poder guardar el estado y abrirte avisos:

1. En tu repo ve a **Settings → Actions → General**.
2. Hasta abajo, en **Workflow permissions**, elige
   **Read and write permissions** y guarda.

## 4. Primera corrida (a mano)

1. Ve a la pestaña **Actions** de tu repo.
2. Si pide habilitar workflows, acepta.
3. Elige **Monitor CFE Concursos** → botón **Run workflow**.
4. Espera a que termine (unos minutos).

> La primera vez es normal que marque MUCHOS concursos o ninguno: aún no
> hay "memoria" de lo ya visto. A partir de la segunda corrida solo te
> reporta lo **nuevo**.

## 5. Cómo te llegan los avisos

Cuando encuentra concursos con recubrimientos/pinturas, **abre un Issue**
en tu repo con el listado. GitHub te manda ese Issue **por correo**
automáticamente (es la forma más fácil, sin configurar contraseñas).

También guarda un reporte en la carpeta `reportes/AAAA-MM-DD.md` dentro
del repo, por si quieres el histórico.

## 6. Horario

Está programado de **lunes a viernes a las 8:00 am (hora de Monclova)**.
Para cambiarlo, edita la línea `cron` en
`.github/workflows/monitor.yml`. El valor está en hora UTC
(Monclova = UTC−6, todo el año).

## 7. Cambiar lo que busca

Abre `keywords.py` y agrega o quita términos de la lista. No importan
acentos ni mayúsculas. Por ejemplo, para que también detecte "thinner"
o "solvente", solo agrégalos a la lista.

---

## Ajuste fino (importante)

CFE tiene **detección de bots** y su página no se pudo inspeccionar de
antemano. El script usa un navegador real, pero la parte que lee la
**lista** de concursos puede necesitar un ajuste según cómo esté armada
la página el día que corra.

Por eso, cada corrida guarda evidencia en la carpeta `depuracion/`
(una captura de pantalla y el HTML real de la página). Esos archivos
quedan disponibles así:

- En la pestaña **Actions**, abre la corrida → sección **Artifacts** →
  descarga **reporte-…** (trae `reportes/` y `depuracion/`).

Si la lista sale vacía o incompleta, **mándame el archivo
`depuracion/pagina_AAAA-MM-DD.html`** y yo ajusto los selectores exactos
en la función `extraer_lista_concursos()` de `monitor.py`. Con eso queda
fino.

## Limitaciones honestas

- Si un anexo es un **PDF escaneado** (foto, sin texto), no se puede leer
  sin OCR. Se puede agregar OCR después si te topas con muchos así.
- Si CFE refuerza el bloqueo anti-bots, puede requerir otro ajuste.
- Los minutos de GitHub Actions son gratis de sobra para esto
  (este proceso usa unos pocos minutos al día).
