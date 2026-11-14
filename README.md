# P6 · Centro de monitoreo de visión

Proyecto individual de Santiago Ortiz para la materia Servicios de Aprendizaje
Automático. Este repositorio está en **fase inicial**: recibe video en OpenCV,
pero todavía no ejecuta el detector ni implementa métricas, eventos, alertas o
portal. No debe presentarse aún como entrega completa.

## Estado y arquitectura actual

```text
Video reproducido en la laptop
    → grabador de pantalla local (fuera de este repositorio)
    → MJPEG en 127.0.0.1:8765
    → lector_opencv.py + fuente.py (este repositorio)
    → futuros detector, métricas, eventos y portal
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
1, 4, 7 y 10 al futuro detector. El muestreo por número de cuadro **no**
garantiza una frecuencia temporal fija si el stream real varía de velocidad.
`--interval-ms` solo acompasa archivos y secuencias, no los streams en vivo.
Para una nueva prueba, usa otra carpeta en `--output` o retira únicamente los
archivos generados que ya no necesites; el generador no sobrescribe archivos.

Para comprobar errores y estados de lectura:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

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

- Integrar un detector real e identificar pesos, clases, versión,
  preprocesamiento y componentes reutilizados.
- Definir fuente reproducible y política de muestreo progresivo.
- Implementar confianza por zonas, referencia y cambio sostenido; distinguir
  datos insuficientes de estado normal.
- Registrar métricas G1–G7 aplicables, eventos, capturas y alertas persistentes.
- Construir consulta de eventos y alertas en el portal.
- Documentar y ejecutar C1/C2 con evidencia vinculada al commit evaluado.

El material de origen, claves y capturas privadas no deben agregarse al
repositorio sin revisar sus permisos y contenido.
