# P6 · Centro de monitoreo de visión

Proyecto individual de Santiago Ortiz para la materia Servicios de Aprendizaje
Automático. Este repositorio está en **fase progresiva**: ya recibe video,
ejecuta un detector real en CPU y persiste observaciones primarias versionadas,
pero todavía no implementa métricas, eventos, alertas ni portal. No debe
presentarse aún como entrega completa.

## Estado y arquitectura actual

```text
Video reproducido en la laptop
    → grabador de pantalla local (fuera de este repositorio)
    → MJPEG en 127.0.0.1:8765
    → lector_opencv.py + fuente.py (este repositorio)
    → detectar_opencv.py + detector_yolox.py (YOLOX-Tiny COCO, CPU)
    → ejecutar_pipeline.py + observaciones.py (JSONL de P6-03)
    → futuras métricas, eventos, alertas y portal
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
Ni el lector ni el grabador guardan cuadros en esta etapa.

El contrato de salida para el detector está en [docs/P6-01_FUENTE.md](docs/P6-01_FUENTE.md).

## Pendiente para la entrega

- Definir C1/C2 con escenas reales reproducibles y permiso de uso confirmado;
  el detector ya está integrado, pero falta la evidencia de esas escenas.
- Implementar confianza por zonas, referencia y cambio sostenido; distinguir
  datos insuficientes de estado normal.
- Registrar métricas G1–G7 aplicables, eventos, capturas y alertas persistentes.
- Construir consulta de eventos y alertas en el portal.
- Documentar y ejecutar C1/C2 con evidencia vinculada al commit evaluado.

El material de origen, claves y capturas privadas no deben agregarse al
repositorio sin revisar sus permisos y contenido.
