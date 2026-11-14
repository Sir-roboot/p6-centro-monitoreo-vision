# P6-02 · Detector YOLOX-Tiny en CPU

## Modelo y artefacto

- Modelo: YOLOX-Tiny, versión `0.1.1rc0`, salida ONNX oficial de Megvii.
- Dataset: COCO 2017, 80 clases en el orden de la salida del modelo.
- URL: <https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx>
- SHA-256: `427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7`
- Tamaño: 20 219 662 bytes.
- Licencia del proyecto YOLOX: Apache-2.0. Los pesos no se distribuyen en este
  repositorio; se descargan desde el release oficial y `models/` está en
  `.gitignore`.

El detector usa solo `opencv-python` y `numpy`, ya presentes en
`requirements.txt`. No se agregó `onnxruntime`; la inferencia corre con OpenCV
DNN, backend OpenCV y target CPU.

## Contrato de entrada y salida

La entrada es una imagen BGR `uint8` de cualquier tamaño. El preprocesamiento:

1. Redimensiona sin deformar conservando el aspecto.
2. Rellena abajo y a la derecha con el valor 114 hasta `416×416`.
3. Convierte a `float32` BGR, sin normalización ni resta de media.
4. Forma el blob `1×3×416×416`.

La red entrega `1×3549×85`: 3549 celdas de las rejillas con strides 8, 16 y 32,
y 85 valores por celda (`cx, cy, w, h, objectness, 80 probabilidades`). El
puntaje es `objectness × probabilidad_de_clase`; se conservan los puntajes
mayores que `score_threshold` y se aplica NMS **class-agnostic** con
`nms_threshold`. Las cajas se transforman a píxeles de la imagen original, se
recortan a sus límites y se descartan las que caen por completo en el relleno.

`DetectorYolox.detectar()` devuelve `ResultadoCuadro`:

| Campo | Contenido |
|---|---|
| `status` | `ok` o `error` |
| `detections` | `class_id`, `class_name`, `confidence`, `bbox_xyxy` |
| `class_counts` | conteo ordenado por clase |
| `model` | nombre, dataset, versión, URL, SHA-256, tamaño, formas, backend, clases |
| `filters` | umbrales, tipo de NMS, preprocesamiento y relleno |
| `timings_ms` | `preprocess`, `inference`, `postprocess`, `total` |
| `error` | detalle técnico solo cuando `status="error"` |

Un cuadro sin objetos es `status="ok"` con `detections=[]`. Una imagen inválida,
un fallo de inferencia o una salida con forma incorrecta es `status="error"`
con `detections=[]` y un `error` concreto. No se confunde una escena vacía con
un detector caído.

## Pesos: descarga y verificación

`asegurar_pesos()` reutiliza un archivo solo si coincide en tamaño y SHA-256.
Si no existe, lo descarga a un archivo temporal en el mismo directorio, verifica
tamaño y SHA, y lo mueve con `os.replace`. Si la descarga o la verificación
fallan, elimina el temporal y no deja un modelo parcial. Un archivo local
inválido se conserva hasta que exista un reemplazo verificado; se registra una
advertencia. `--no-download` convierte cualquier faltante o discrepancia en un error.

## CLI

```powershell
.\.venv\Scripts\python.exe detectar_opencv.py --source .\datos_locales\secuencia_demo --source-id demo --sample-every 3 --interval-ms 200 --no-preview
```

Opciones principales: `--source`, `--source-id`, `--sample-every`,
`--interval-ms`, `--max-frames`, `--seconds`, `--weights`, `--no-download`,
`--score-threshold`, `--nms-threshold`, `--no-preview` y `--show-discards`.
Solo se infiere sobre cuadros `sampled`; los `discarded` se resumen y no se
detectan. Se emite un JSON por cuadro procesado y un JSON final
`{"summary": ...}` con `source`, `detector`, `detections`, `class_counts` y
`end_state`. El código de salida es 1 si hubo errores de lectura o de detector.

## Verificación observada

El 13 de noviembre de 2026, con Python 3.14.3, OpenCV 5.0.0 y los pesos
verificados:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Resultado: 38 pruebas, todas `ok`; no requieren red ni los pesos.

Sobre `datos_locales/secuencia_demo` con `--sample-every 3 --interval-ms 0
--no-preview`, se procesaron 4 cuadros y el resumen fue: 12 recibidos,
4 seleccionados, 8 descartados, 0 errores, `end_state="end_of_stream"`; la
secuencia sintética no contiene objetos COCO y produjo 0 detecciones, como se
esperaba. La inferencia tardó entre 39 y 44 ms por cuadro.

Como prueba con una imagen real externa se usó
[`assets/dog.jpg` del repositorio oficial de YOLOX](https://github.com/Megvii-BaseDetection/YOLOX/blob/main/assets/dog.jpg),
sin incorporarla al proyecto. Su SHA-256 observado fue
`5a9522051c3cec2bbd2f6323fccba32e8fbf3ddcc2b3e2fd46b04c720bc6f866`.
Para repetirla desde la raíz del repositorio, con los pesos ya descargados:

```powershell
$imagenPrueba = Join-Path $env:TEMP 'p6-02-dog.jpg'
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/Megvii-BaseDetection/YOLOX/main/assets/dog.jpg' -OutFile $imagenPrueba
Get-FileHash $imagenPrueba -Algorithm SHA256
.\.venv\Scripts\python.exe detectar_opencv.py --source $imagenPrueba --source-id prueba-real --max-frames 1 --no-download --no-preview
```

El hash debe coincidir con el anterior. Se obtuvieron 5 detecciones:
`dog` 0.54, `bicycle` 0.88, `car` 0.67, `potted plant` 0.11 y `tv` 0.16; la
inferencia tardó ~57 ms. Las dos últimas son detecciones débiles atribuibles al
umbral bajo de 0.10 y no se presentan como evidencia de calidad. Esta prueba
requiere red para obtener la imagen y no forma parte de las pruebas unitarias.

En esta instalación OpenCV 5 imprime
`Targets are not supported by the new graph engine for now` al fijar el target.
El backend sigue siendo el de OpenCV y la ejecución es en CPU; el aviso se
documenta como limitación del motor de grafos de OpenCV 5, no como uso de GPU.

## Procedencia y reutilización

El diseño se contrastó con el **Proyecto 5, equipo 03**, módulo `p5.detector`
(detector y verificación de pesos de A1, Josué Godoy): letterbox abajo-derecha
con relleno 114, decodificación de rejilla, NMS class-agnostic, orden COCO y
constantes de URL/SHA-256. La implementación de P6 es independiente, usa OpenCV
DNN en vez de ONNX Runtime y no importa `p5` ni copia su estructura de paquete.

## Límites conocidos

- Solo se validó en CPU; no hay ruta GPU ni aceleración específica.
- Los umbrales 0.10/0.45 son un punto de partida; todavía no se fijaron con
  métricas de C1/C2. Un umbral bajo aumenta falsos positivos.
- La NMS class-agnostic puede suprimir cajas solapadas de clases distintas.
- La secuencia sintética verifica el flujo, no la calidad del detector.
- Los pesos requieren una descarga inicial de ~20 MB. Sin red, hay que copiar
  el archivo verificado a `models/yolox_tiny.onnx` o usar `--weights`.
- No se implementan métricas, eventos, capturas ni alertas; pertenecen a las
  siguientes etapas.
