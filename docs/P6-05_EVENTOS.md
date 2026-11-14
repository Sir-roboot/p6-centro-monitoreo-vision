# P6-05 · Eventos y capturas persistentes

`ejecutar_eventos.py` une la fuente visual, el detector real, las observaciones
P6-03 y la política P6-04. Cada cuadro inferido produce un evento P6-05; los
cuadros descartados por muestreo no lo producen. La imagen BGR que recibió el
detector se codifica como JPEG y se conserva junto al evento. No se inventan
predicciones, puntuaciones ni zonas. Las zonas de cada detección se copian del
artefacto P6-04 y se comprueban contra la observación primaria.

## Uso con la señal local

Con el grabador auxiliar activo y el video reproduciéndose en el monitor 2:

```powershell
$runId = "p6-05-" + (Get-Date -Format "yyyyMMdd-HHmmss")
.\.venv\Scripts\python.exe .\ejecutar_eventos.py `
  --source http://127.0.0.1:8765/stream.mjpg `
  --source-id monitor-video --sample-every 15 --max-frames 136 `
  --run-id $runId `
  --reference .\datos_locales\observaciones\monitor-p6-04-20261113-214440.jsonl `
  --lower 0.17 --upper 0.61 `
  --threshold-justification "Cuartiles redondeados de referencia previa; exploratorio" `
  --no-download
```

`--max-frames 136` recibe 136 cuadros y selecciona los números 1, 16, ..., 136:
10 inferencias, equivalentes a dos ventanas completas de cinco cuadros. La
referencia del ejemplo es **local**; en otro equipo se debe generar o proporcionar
una referencia externa compatible. Sin `--reference`, P6-04 usa una referencia
interna y puede devolver `insufficient_evidence`; el programa no la convierte
en normalidad. Los umbrales siguen siendo exploratorios hasta preparar C1/C2.

El comando publica, sin sobrescribir corridas anteriores:

- `datos_locales/observaciones/<run_id>.jsonl`: datos primarios P6-03;
- `datos_locales/politicas/<run_id>-p6-04.json`: zonas y cambio sostenido P6-04;
- `datos_locales/eventos/<run_id>.jsonl`: eventos P6-05;
- `datos_locales/capturas/<hash-run>/`: JPEG vinculados.

Para consultar después de detener o reiniciar el proceso, sin cargar el modelo:

```powershell
.\.venv\Scripts\python.exe .\consultar_eventos.py --run-id $runId --verify-captures
```

`consultar_eventos(root, run_id)` devuelve los eventos a un futuro portal.
`abrir_captura(root, evento)` resuelve el vínculo **solo dentro de la carpeta de
capturas** y comprueba SHA-256 antes de entregar bytes; una captura alterada o
ausente no pasa como válida. El portal podrá usar estas funciones sin volver a
inferir. El `event_id` es determinista a partir de `observation_id`; no se
reemplaza una corrida ya publicada.

## Contrato del evento

Cada línea JSON contiene `event_id`, `run_id`, `observation_id`, `frame_id`,
`occurred_at`, `source`, `model`, `filters`, `source_config`, `status`,
`technical_result`, `reason`, `prediction`, `detections`, `zones`, `zone`,
`policy_id`, `policy_status`, `policy_denominator`, `evidence_sufficient`,
`normality_established`, `policy_config`, `observation_ref`, `policy_ref` y
`capture`. `prediction` es la detección de mayor confianza para lectura rápida;
`detections` conserva todas. `zone` es la zona más cautelosa presente en el
cuadro (`low` antes de `doubt` antes de `accepted`), por lo que puede diferir de
la zona de `prediction`. Los estados `no_detections`, `no_score` y
`technical_error` quedan separados y no reciben una confianza inventada.

`capture` es `null` cuando la fuente no entregó píxeles, como en un error de
lectura. De otro modo contiene `path` relativo al directorio local y `sha256`.
La política de conservación es **guardar cada cuadro muestreado que llegó al
detector**, incluyendo cuadros sin detecciones y errores del detector con imagen;
no guardar los descartados por muestreo. En la configuración de 15 cuadros por
segundo y `--sample-every 15`, equivale aproximadamente a un JPEG por segundo.
No hay TTL ni borrado automático. El espacio en disco debe vigilarse en corridas
largas; una política más selectiva tendría que declararse y probarse antes de
usarla.

## Verificación

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Las pruebas incluyen: cuadro aceptado, baja confianza, sin detecciones, error
técnico, rechazo de política alterada, no sobrescritura de corrida, captura
corrupta y consulta desde un proceso nuevo. Las imágenes de prueba son
sintéticas: verifican el contrato y **no sustituyen** una demostración C1/C2.
La suite completa ejecutó 95 pruebas correctas el 13 nov 2026.

Corrida local real del 13 nov 2026, `p6-05-video-20261113-221955`: 136 cuadros
recibidos, 10 inferidos, 234 detecciones puntuadas (55 `low`, 95 `doubt`, 84
`accepted`), dos ventanas completas, 10 eventos y 10 JPEG. La consulta posterior
verificó los 10 hashes y se abrió visualmente una captura de la calle, sin
interfaz de navegador. Resultado técnico `ok`; la política informó
`no_sustained_change`, que **no** certifica seguridad ni precisión del detector.
SHA-256 del manifiesto local de eventos:
`e96c4263f3d5b5ee6fc93b5fa03591e4223cb1626b18a0d60d0616de78ed1966`.

Tras las últimas correcciones, se repitió la ejecución con la señal real en
`p6-05-verificacion-20261113-222520`: resultado técnico `ok`, 10 eventos y
10 capturas verificadas por una consulta en un proceso nuevo. SHA-256 del
segundo manifiesto local:
`d5091d4119a0c34ac231f9080f0978ddf9655a48042631b3ea00b0afd3ea343e`.

El video, JSONL y JPEG de esta corrida están en `datos_locales/` (ignorado por
Git). El resumen documenta una comprobación local, pero un clon limpio no puede
reabrir esos archivos. La evidencia final de C1/C2 necesitará entradas o
capturas accesibles y una demostración reproducible, además de métricas,
alertas y consulta en el portal.

P6-05 **no genera alertas ni portal**. P6-06 podrá referenciar eventos por
`event_id`, condiciones por `policy_id` y evidencia visual por `capture.path`;
deberá registrar sus propias reglas y no convertir `insufficient_evidence` en
normalidad.
