# P6 · Centro de monitoreo de visión

Proyecto individual de Santiago Ortiz para la materia Servicios de Aprendizaje
Automático. Este repositorio está en **fase progresiva**: ya recibe video,
ejecuta un detector real en CPU, persiste observaciones primarias versionadas,
publica una política de confianza exploratoria, persiste eventos/capturas y
genera alertas P6-06 con reglas explícitas y evidencia verificable, y calcula
métricas base G1–G7 recalculables, pero todavía no implementa el portal. No debe
presentarse aún como entrega completa.

## Estado y arquitectura actual

```text
Video reproducido en la laptop
    → grabador de pantalla local (fuera de este repositorio)
    → MJPEG en 127.0.0.1:8765
    → lector_opencv.py + fuente.py (este repositorio)
    → detectar_opencv.py + detector_yolox.py (YOLOX-Tiny COCO, CPU)
    → ejecutar_pipeline.py + observaciones.py (JSONL de P6-03)
    → evaluar_confianza.py + politica_confianza.py (política P6-04)
    → ejecutar_eventos.py + eventos.py (eventos y JPEG P6-05)
    → ejecutar_alertas.py + alertas.py (alertas persistentes P6-06)
    → calcular_metricas.py + metricas.py (reporte G1–G7 P6-07)
    → portal (pendiente)
```

El grabador está separado deliberadamente del Proyecto 6. Solo abre una señal
en la interfaz local de la laptop; no guarda imágenes ni video. El lector
recibe cuadros BGR y les asigna un número e instante de recepción UTC. También
acepta un archivo de video o una secuencia de imágenes. El generador de este
repositorio produce una **secuencia sintética propia** para verificar la
lectura desde una copia limpia; no demuestra inferencia ni sustituye las
escenas reales de C1/C2.

## Instalación y prueba reproducible de la fuente

Probado en Windows con Python 3.14.3. Desde la raíz de este repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Genera 12 imágenes sintéticas locales (ignoradas por Git) y léelas en orden:

```powershell
.\.venv\Scripts\python.exe generar_secuencia_demo.py
.\.venv\Scripts\python.exe lector_opencv.py --source .\datos_locales\secuencia_demo --source-id demo --sample-every 3 --interval-ms 200 --no-preview
```

La segunda orden debe terminar en `end_of_stream` con 12 cuadros recibidos,
4 seleccionados, 8 descartados por muestreo y 0 errores. `--interval-ms 200`
reproduce la secuencia a 5 cuadros/s; `--sample-every 3` entrega los cuadros
1, 4, 7 y 10 al detector. El muestreo por número de cuadro **no** garantiza una
frecuencia temporal fija si el stream real varía de velocidad. `--interval-ms`
solo acompasa archivos y secuencias, no los streams en vivo. Para una nueva
prueba, usa otra carpeta en `--output` o retira únicamente los archivos
generados que ya no necesites; el generador no sobrescribe archivos.

## Detector YOLOX-Tiny en CPU

`detector_yolox.py` ejecuta **YOLOX-Tiny 0.1.1rc0 COCO en ONNX** con OpenCV DNN
(backend `DNN_BACKEND_OPENCV`, target `DNN_TARGET_CPU`). No se agregó
`onnxruntime` ni otra dependencia. El CLI `detectar_opencv.py` conecta la fuente
de P6-01 con el detector y emite **una línea JSON por cuadro** más un resumen.

La primera ejecución descarga los pesos oficiales a `models/yolox_tiny.onnx`
(carpeta ignorada por Git), comprueba tamaño y SHA-256 antes de usarlos y los
instala con reemplazo atómico:

- URL: <https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx>
- SHA-256: `427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7`
- Tamaño: 20 219 662 bytes
- Entrada: `1×3×416×416` float32 BGR, valores 0–255, sin normalización
- Salida: `1×3549×85`; clases COCO en el orden del modelo

Para probarlo sobre la secuencia sintética, sin descargar si los pesos ya
existen:

```powershell
.\.venv\Scripts\python.exe detectar_opencv.py --source .\datos_locales\secuencia_demo --source-id demo --sample-every 3 --interval-ms 200 --no-preview
```

Para una fuente real o un archivo autorizado se usan las mismas opciones de
`lector_opencv.py`:

```powershell
.\.venv\Scripts\python.exe detectar_opencv.py --source http://127.0.0.1:8765/stream.mjpg --source-id monitor-video --sample-every 15 --seconds 60
```

`--weights RUTA` cambia el archivo de pesos y `--no-download` prohíbe la
descarga: si falta o no coincide, el programa termina con error en vez de usar
un archivo dudoso. `--score-threshold` (0.10 por omisión) y `--nms-threshold`
(0.45) permiten ajustar los filtros; se documentan en cada resultado. La NMS es
**class-agnostic**, igual que en el demo de YOLOX. `--no-preview` evita la
ventana; con vista previa, `q` detiene el proceso.

Cada resultado incluye `model` (nombre, dataset, versión, URL, SHA-256, tamaño,
forma de entrada/salida, backend y clases), `filters`, `detections` con
`class_id`, `class_name`, `confidence` y `bbox_xyxy` en píxeles de la imagen
original, `class_counts`, `timings_ms` y `error`. Un fallo de lectura o
inferencia durante el procesamiento produce `status="error"`,
`detections=[]` y código de salida 1; si los pesos o el modelo no cargan, el
programa termina antes de emitir resultados por cuadro. Una escena **sin
objetos** produce `status="ok"` con `detections=[]`. Esa distinción evita
confundir una imagen vacía con un detector caído.

Pruebas del detector, pesos y CLI, sin red:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`tests/test_detector_pesos.py` comprueba la lógica de verificación con archivos
de prueba; no calcula el hash de los pesos reales. Las pruebas sustituyen la
sesión de inferencia y las descargas, por lo que no necesitan los 20 MB ni
acceso a Internet. La prueba real observada el 13 de
noviembre de 2026 con una imagen externa de YOLOX detectó `dog`, `bicycle`,
`car`, `tv` y `potted plant`; la inferencia tardó ~57 ms en CPU. Los pesos y
las imágenes externas no se versionan.

## Observaciones versionadas (P6-03)

El pipeline guarda una cabecera, una observación por cuadro y un resumen en un
JSONL con esquema `p6-03/1`. No persiste píxeles. Para generarlo sobre la
secuencia sintética, sin descargar los pesos:

```powershell
.\.venv\Scripts\python.exe ejecutar_pipeline.py --source .\datos_locales\secuencia_demo --source-id demo --sample-every 3 --interval-ms 0 --output .\datos_locales\observaciones\demo-p6-03.jsonl --run-id demo-p6-03 --no-download
```

La salida predeterminada es `datos_locales/observaciones/<run_id>.jsonl`; use
`--overwrite` para reemplazarla. Después se comprueba que los contadores
persistidos se puedan recalcular sin abrir la fuente ni cargar el modelo:

```powershell
.\.venv\Scripts\python.exe recalcular_observaciones.py --input .\datos_locales\observaciones\demo-p6-03.jsonl
```

La última verificación local obtuvo **64 pruebas** correctas y una corrida demo
con 12 cuadros recibidos, 4 procesados, 8 descartados por muestreo, 0 errores y
0 detecciones; el recalculo coincidió. La secuencia sintética no contiene
objetos COCO, así que no evalúa la calidad del detector. Contrato completo,
estados de término y límites: [docs/P6-03_OBSERVACIONES.md](docs/P6-03_OBSERVACIONES.md).

### Procedencia y reutilización

- Modelo y pesos: YOLOX (Megvii), versión `0.1.1rc0`, publicados en el release
  oficial. El código del proyecto YOLOX declara licencia Apache-2.0; no
  asumimos una licencia separada para los pesos ni los redistribuimos aquí.
- Dataset: COCO 2017, 80 clases; el orden de clases proviene del modelo.
- Diseño reutilizado de **Proyecto 5 (equipo 03)**, módulo `p5.detector`
  (detector y verificación de pesos de A1, Josué Godoy): letterbox abajo-derecha
  con relleno 114, decodificación de la rejilla YOLOX, NMS class-agnostic,
  orden COCO y constantes de URL/SHA-256. La implementación de P6 es
  independiente y usa OpenCV DNN en lugar de ONNX Runtime; no importa el paquete
  `p5` ni requiere sus archivos.
- Los cuadros sintéticos siguen siendo propios y no son evidencia de detección.
- Detalle y límites: [docs/P6-02_DETECTOR.md](docs/P6-02_DETECTOR.md).

## Política de confianza exploratoria (P6-04)

`evaluar_confianza.py` lee un JSONL de P6-03 ya persistido y escribe un
artefacto JSON versionado (`schema: p6-04/1`, `policy_id: p6-04-<huella>`) en
`datos_locales/politicas/<run_id>-p6-04.json`. No carga el modelo, no vuelve a
inferir, no aplica NMS y no crea eventos, alertas ni capturas.

```powershell
.\.venv\Scripts\python.exe evaluar_confianza.py --input .\datos_locales\observaciones\demo-p6-03.jsonl
```

La política clasifica cada detección puntuada con fronteras estrictas:
`low` si `s < 0.30`, `doubt` si `0.30 <= s < 0.70` y `accepted` si
`s >= 0.70`. **0.30/0.70 son exploratorios**: se basan en cinco puntuaciones
reales (0.8840, 0.6717, 0.5386, 0.1594, 0.1128) de **una sola imagen
`dog.jpg`** documentada en P6-02. No son una línea base válida para C1/C2 ni
una calibración; el artefacto lo declara con `robust_reference: false`.

El denominador son las **detecciones puntuadas post-filtro** ya persistidas, no
los frames ni los eventos. El contrato heredado exige un piso de score de 0.10
y NMS **class-agnostic** (0.45); si el piso persistido es estrictamente mayor
que 0.10, las puntuaciones bajas pudieron descartarse antes y no se recuperan.
La referencia externa se pasa con `--reference` y se evalúa aparte; sin ella se
usa el primer bloque como referencia interna, que no es evidencia independiente.

Las ventanas son consecutivas y no solapadas (5 frames muestreados por omisión,
al menos 3 detecciones puntuadas). Un frame muestreado sin detección o con
error interrumpe la ventana; un cuadro `discarded` por muestreo no ocupa lugar.
No se unen tramos separados. Hay cambio sostenido si 2 ventanas completas
consecutivas están degradadas: fracción `low >= 0.5` y caída de la media
`>= 0.10` frente a la referencia. Los estados insuficientes
(`insufficient_evidence`, `reference_incompatible`) **nunca** acreditan
normalidad; `normality_established` solo puede ser `true` con
`no_sustained_change` y no hay ventanas incompletas. P6-05 consume las
puntuaciones de P6-04 sin reclasificarlas.

La corrida demo sobre la secuencia sintética no contiene objetos COCO: produce
0 detecciones puntuadas, referencia `insufficient` y cambio
`insufficient_evidence`; no demuestra zonas ni cambio sostenido. La última
verificación local obtuvo **129 pruebas** correctas. Contrato completo, CLI,
artefacto, estados y límites:
[docs/P6-04_CONFIANZA.md](docs/P6-04_CONFIANZA.md).

## Eventos y capturas persistentes (P6-05)

`ejecutar_eventos.py` registra cada cuadro inferido como evento y conserva su
JPEG con vínculo y SHA-256. `consultar_eventos.py` permite releerlos y verificar
las imágenes después de reiniciar, sin cargar el modelo. Al terminar, el CLI
invoca P6-06 para publicar las alertas de la misma corrida; P6-05 en sí no
calcula reglas de alerta. Contrato, comandos y límites:
[docs/P6-05_EVENTOS.md](docs/P6-05_EVENTOS.md).

## Alertas persistentes (P6-06)

`alertas.py` convierte únicamente eventos P6-05 ya persistidos y la política
P6-04 que esos eventos referencian en alertas explícitas. No vuelve a inferir,
no recalcula NMS ni zonas y no convierte `insufficient_evidence` en normalidad.
Publica `datos_locales/alertas/<run_id>.jsonl` sin sobrescribir corridas y sin
expiración automática. Cada alerta declara `occurrence_state` inmutable
(`triggered` para warning/critical, `informational` para insuficiencia), que no
es un estado activo/resuelto ni un ciclo de vida. Una corrida sin cuadros
muestreados produce 0 eventos y solo la alerta informativa enlazada a la
política, sin fabricar detecciones ni capturas. Las reglas son
`p6-06-low-confidence` (baja confianza independiente de `sustained_change`),
`p6-06-sustained-change` (solo con evidencia suficiente de P6-04) y
`p6-06-insufficient-evidence` (informativa: la insuficiencia no acredita
normalidad). El comando
`.\\.venv\\Scripts\\python.exe .\\consultar_alertas.py --run-id $runId --verify`
relee las alertas, exige igualdad exacta de cada `event_ref` con su evento
(`policy_id` y captura incluidos) y comprueba los hashes de las capturas después
de reiniciar. La vista de alertas del portal (R5.3) sigue pendiente; P6-06 deja
un contrato de consulta. En la corrida local
`p6-05-verificacion-20261113-222520` se generaron 10 alertas `warning` de baja
confianza con `no_sustained_change`, sin alertas críticas ni de insuficiencia;
las 10 capturas y eventos se verificaron correctamente. Eso acredita C2 por
alerta de baja confianza, **no** demuestra drift ni calidad del detector.
Contrato, comandos, reglas y límites:
[docs/P6-06_ALERTAS.md](docs/P6-06_ALERTAS.md).

## Métricas base G1–G7 (P6-07)

`metricas.py` lee los JSONL ya persistidos de observaciones, eventos y alertas,
y calcula **una métrica base por grupo G1–G7** sin volver a inferir ni
reclasificar zonas. `calcular_metricas.py` expone el cálculo por CLI y escribe un
reporte JSON `p6-07/1` con numerador, denominador explícito, valor, estado,
evidencia, ventana, exclusiones, ausencias e identificadores de los insumos.
Cada razón es cero segura: denominador `0` produce `valor: null` y
`estado: "sin_datos"`, nunca una división ni un cero saludable.

Sobre la corrida `p6-05-verificacion-20261113-222520`, con un instante fijo:

```powershell
$runId = "p6-05-verificacion-20261113-222520"
.\.venv\Scripts\python.exe .\calcular_metricas.py `
  --observaciones ".\datos_locales\observaciones\$runId.jsonl" `
  --eventos ".\datos_locales\eventos\$runId.jsonl" `
  --alertas ".\datos_locales\alertas\$runId.jsonl" `
  --root . --generated-at "2026-11-14T04:26:00+00:00" `
  --output ".\datos_locales\reportes\p6-07-$runId.json"
```

La selección base es: G1 errores técnicos entre cuadros muestreados, G2
descartes entre cuadros recibidos, G3 disco disponible, G4 detecciones `low`
entre detecciones puntuadas, G5 eventos trazables entre eventos persistidos, G6
antigüedad del último reporte y G7 alertas `triggered` entre alertas
persistidas. G3 es una lectura instantánea del disco y G6 depende de
`--generated-at`; G7 usa `occurrence_state` como proxy porque P6-06 no modela un
ciclo de vida. Estas métricas describen operación y evidencia, **no** exactitud
del modelo.
Definiciones, ejemplo de recálculo manual y límites:
[docs/P6-07_METRICAS.md](docs/P6-07_METRICAS.md).

## Entrada del monitor y video propio

Coloca el video en pantalla completa en el monitor dedicado. En la primera
terminal inicia el grabador auxiliar **fuera de este repositorio** usando el
número de monitor que muestre `--list-monitors`. Con la señal local activa en
`127.0.0.1:8765`, en la segunda terminal ejecuta:

```powershell
.\.venv\Scripts\python.exe lector_opencv.py --source http://127.0.0.1:8765/stream.mjpg --source-id monitor-video --sample-every 15 --seconds 60
```

El grabador produce nominalmente 15 cuadros/s; seleccionar uno de cada 15
aproxima 1 inferencia/s. El lector imprime metadatos JSON por cuadro
seleccionado y un resumen que separa `received`, `sampled`, `discarded` y
`read_errors`. La ventana de vista previa debe permanecer en el otro monitor.
Pulsa `q` con esa ventana activa o Ctrl+C para detener. Para no abrirla:

```powershell
.\.venv\Scripts\python.exe lector_opencv.py --source-id monitor-video --sample-every 15 --seconds 60 --no-preview
```

También se puede indicar `--source ruta\a\video.mp4` para un archivo local
autorizado. La fuente MJPEG no queda reproducible desde un clon aislado por sí
sola: requiere el grabador y el video. La secuencia sintética sí verifica la
entrada sin esos recursos, pero C1/C2 requerirán escenas reales accesibles.
Ni el lector ni el grabador guardan cuadros por sí mismos; el comando P6-05
conserva solo los cuadros muestreados que pasan por inferencia.

El contrato de salida para el detector está en [docs/P6-01_FUENTE.md](docs/P6-01_FUENTE.md).

## Pendiente para la entrega

- Definir C1/C2 con escenas reales reproducibles y permiso de uso confirmado;
  el detector ya está integrado, pero falta la evidencia de esas escenas.
- Revisar y sustituir los umbrales exploratorios 0.30/0.70 de P6-04 con
  evidencia de C1/C2; la política ya distingue insuficiencia de normalidad, pero
  no acredita esas escenas.
- Validar las reglas P6-06 con escenas reales de C1/C2; la implementación ya
  existe, pero sus umbrales y resultados todavía son exploratorios.
- Registrar métricas adicionales del catálogo G1–G7 y sus umbrales; la base
  P6-07 ya es recalculable, pero no cubre todo el catálogo.
- Construir la consulta de eventos y alertas en el portal (R5.3); P6-06 solo
  expone funciones y CLI locales.
- Documentar y ejecutar C1/C2 con evidencia vinculada al commit evaluado.

El material de origen, claves y capturas privadas no deben agregarse al
repositorio sin revisar sus permisos y contenido.
