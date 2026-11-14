# P6-08 · Portal mínimo local

`portal.py` expone por HTTP los artefactos ya persistidos por P6-04, P6-05 y
P6-06: eventos, alertas y capturas JPEG. Es un visor **de solo lectura**:

- no ejecuta inferencia, NMS ni la política de confianza;
- no recalcula zonas, confianzas, eventos ni alertas;
- no crea, modifica ni borra artefactos;
- no convierte una corrida vacía o una alerta informativa en normalidad.

Usa solo la biblioteca estándar (`http.server`, `html`, `json`, `urllib`), sin
dependencias nuevas. Escucha exclusivamente en `127.0.0.1`.

## Uso

Con artefactos en `datos_locales/`:

```powershell
.\.venv\Scripts\python.exe .\portal.py --root .\datos_locales --port 8080
```

Abrir `http://127.0.0.1:8080/`. Para habilitar las métricas derivadas
opcionales:

```powershell
.\.venv\Scripts\python.exe .\portal.py `
  --root .\datos_locales --port 8080 --metrics
```

El proceso imprime la URL real (incluido el puerto elegido) y se detiene con
`Ctrl+C`. No hay autenticación: el portal está pensado para un equipo local y
no debe exponerse en otra interfaz ni publicarse por un túnel.

## Rutas

| Ruta | Respuesta |
| --- | --- |
| `/` o `/portal` | Índice de corridas con conteos de eventos, alertas y capturas. |
| `/portal/runs/<run_id>` | Alertas (campos del contrato P6-06) y tabla de eventos. |
| `/portal/runs/<run_id>/events/<event_id>` | Evento completo, captura y JSON persistido escapado. |
| `/portal/runs/<run_id>/alerts/<alert_id>` | Alerta completa y JSON persistido escapado. |
| `/portal/runs/<run_id>/captures/<event_id>` | JPEG verificado del evento. |
| `/portal/runs/<run_id>/metrics` | Conteos derivados, solo con `--metrics`. |
| `/healthz` | `{"ok": true, "read_only": true}`. |

Las alertas muestran `occurred_at`, `rule_id`, `severity`, `occurrence_state`,
`reason`, `suggested_action`, `event_id`, `event_ids` y `window`; los enlaces de
captura salen de `window.event_refs[].event_id`, no de rutas arbitrarias. Una
corrida sin eventos se presenta con un aviso explícito y no como normalidad.

## Seguridad y rutas

- **Solo loopback**: `crear_servidor` fija `127.0.0.1`; no acepta un host
  alternativo.
- **Solo GET/HEAD**: POST, PUT, DELETE y PATCH responden `405`.
- **Identificadores estrictos**: `run_id`, `event_id` y `alert_id` se validan
  con los patrones de P6-05/P6-06 antes de tocar artefactos. Cualquier `%`,
  barra invertida o byte nulo en la ruta responde `400`; no se decodifican
  segmentos, de modo que variantes codificadas de *traversal* no llegan al
  sistema de archivos.
- **Capturas confinadas**: la ruta del JPEG se resuelve por `event_id` mediante
  `abrir_captura`, que exige que el archivo esté dentro de `capturas/` y que su
  SHA-256 coincida con el evento. Una captura alterada, sustituida o ausente no
  se entrega.
- **HTML escapado**: todo valor persistido se interpola con
  `html.escape(..., quote=True)`, también dentro de bloques `<pre>`.
- **Cabeceras defensivas**: `Content-Security-Policy` sin scripts ni recursos
  externos, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` y
  `Cache-Control: no-store`.

## Métricas opcionales

Sin `--metrics`, `/metrics` responde `404`. Con la bandera habilitada devuelve
`application/json` con `schema: "p6-08/metrics/1"` y conteos calculados en el
momento a partir de los artefactos persistidos: eventos, alertas, capturas,
estados de evento, zonas, severidades, reglas y `occurrence_state`.

Son **conteos operativos**, no las métricas G1–G7: no miden precisión, recall ni
calidad predictiva y no acreditan C1/C2. El propio JSON incluye
`limitations` con esa aclaración. La página de corrida solo muestra el enlace
cuando las métricas están habilitadas.

## API reutilizable

```python
from portal import crear_servidor, listar_runs, metricas_run

runs = listar_runs("datos_locales")
metricas = metricas_run("datos_locales", runs[0])
servidor = crear_servidor("datos_locales", 0, metricas=True)
```

`crear_servidor(..., 0)` permite elegir un puerto libre; el servidor no arranca
hasta llamar a `serve_forever()`. Las pruebas lo levantan en un hilo y lo
detienen con `shutdown()`/`server_close()`.

## Verificación

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`tests/test_portal.py` genera una corrida real de 10 eventos y 11 alertas con el
detector falso de P6-05 y comprueba: índice y detalle, entrega de JPEG
verificado solo por `event_id`, solo loopback y métodos permitidos, rechazo de
traversal e identificadores inválidos, escapado de `<script>` persistido,
métricas habilitadas/deshabilitadas y validaciones locales. Son 8 pruebas; la
suite completa ejecutó 119 pruebas correctas el 13 nov 2026.

## Límites

- El portal no sustituye la consulta por CLI ni crea evidencia nueva; un clon
  limpio sin `datos_locales/` no puede mostrar corridas.
- La tabla de eventos se limita a 500 filas por página; el artefacto completo
  sigue disponible mediante las funciones de consulta.
- No implementa búsqueda, filtros, paginación interactiva, autenticación ni
  sesiones.
- No modela ciclo de vida de alertas: `occurrence_state` se muestra tal como lo
  persiste P6-06 (`triggered`/`informational`), sin `active`/`resolved`.
- Las métricas opcionales son conteos, no una evaluación de calidad del modelo.
