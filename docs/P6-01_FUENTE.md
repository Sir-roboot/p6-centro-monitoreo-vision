# P6-01 · Fuente visual y muestreo

## Origen y permiso

- La secuencia `datos_locales/secuencia_demo` es generada por código propio en
  `generar_secuencia_demo.py`; es una prueba sintética de lectura, no evidencia
  de detección ni material de terceros.
- La fuente real de trabajo es la reproducción de un video de YouTube en un
  monitor dedicado, capturado en la misma laptop por un grabador auxiliar
  local. El video pertenece a su respectivo titular. No se copia ni publica
  su contenido en este repositorio. Antes de usarlo como evidencia entregable
  hay que confirmar permiso de uso y una forma de acceso estable para el
  evaluador, o sustituirlo por una grabación propia/autorizada.
- Candidato C1: escena urbana con vehículos y peatones del video de trabajo.
  Candidato C2: un tramo con oclusión o iluminación difícil, o una secuencia
  propia preparada. Ninguno está confirmado: se necesitan puntuaciones reales
  del detector y los umbrales fijados previamente.

## Política y contrato

`fuente.leer_fuente` recibe MJPEG/RTSP, un archivo de video o un directorio
de imágenes ordenadas por nombre. Para directorios y archivos, `interval_ms`
marca el ritmo progresivo. Para el stream, se usa su propio ritmo de llegada.
`sample_every=N` selecciona los cuadros 1, 1+N, 1+2N, etc. La configuración
inicial del monitor es 15 cuadros/s nominales y `N=15`, aproximadamente una
selección por segundo. Es una política por cuadros, no por reloj.

Cada cuadro leído produce `ObservacionFuente` con `source_id`, `frame_id`,
`frame_number`, `received_at` UTC, `status`, `discard_reason`, dimensiones y
el arreglo BGR **solo si fue seleccionado**. Los estados son `sampled`,
`discarded` (motivo `sampling_interval`) y `read_error` (motivo concreto).
`end_of_stream` es un registro terminal distinto, sin ID ni imagen. El resumen
del CLI separa cuadros recibidos, seleccionados, descartados y errores.
`to_dict()` excluye los píxeles y se puede usar directamente para el contrato
de P6-02. No hay todavía resultados de modelo ni puntuaciones.

Un `read()` fallido de un stream es `stream_read_failed`; el fin de una
secuencia o archivo se registra como `end_of_stream`. En un archivo de video,
OpenCV puede no distinguir EOF de un archivo truncado: esa limitación se
documenta y no se interpreta como inferencia exitosa. Un archivo de imagen
dañado se identifica como `invalid_image`.

## Verificación local inicial

Desde la raíz del repositorio, con Python 3.14.3 y `requirements.txt`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe generar_secuencia_demo.py
.\.venv\Scripts\python.exe lector_opencv.py --source .\datos_locales\secuencia_demo --source-id demo --sample-every 3 --interval-ms 0 --no-preview
```

Observado el 13 de noviembre de 2026: 7 pruebas pasan. El comando de lectura
entregó `demo-000001`, `000004`, `000007`, `000010`, luego `end_of_stream`;
resumen: 12 recibidos, 4 seleccionados, 8 descartados, 0 errores. Se usó
`--interval-ms 0` solo para acelerar la verificación automatizada; la
configuración documentada de demostración es 200 ms. La prueba del monitor
dedicado se hizo después con el monitor 2 y la señal local
`http://127.0.0.1:8765/stream.mjpg`:

```powershell
.\.venv\Scripts\python.exe lector_opencv.py --source-id monitor-video --sample-every 15 --seconds 5 --no-preview
```

Observado: 55 cuadros recibidos, 4 seleccionados, 51 descartados, 0 errores,
`end_state=max_duration`; resolución 2560×1080. Una inspección temporal de un
cuadro mostró la escena vial en pantalla completa sin interfaz del navegador
ni controles de YouTube. Hay franjas negras laterales por la relación de
aspecto del monitor. Otro cuadro tuvo una diferencia media de 74.67 niveles
por canal respecto al primero, por lo que la imagen no estaba congelada.
La imagen temporal usada para esa inspección se eliminó; no se incorpora
contenido de terceros al repositorio. La evidencia se asociará al commit
cuando Santiago apruebe y publique este módulo.
