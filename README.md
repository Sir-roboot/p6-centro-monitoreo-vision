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
    → lector_opencv.py (este repositorio)
    → futuros detector, métricas, eventos y portal
```

El grabador está separado deliberadamente del Proyecto 6. Solo abre una señal
en la interfaz local de la laptop; no guarda imágenes ni video. El lector
recibe cuadros BGR y les asigna un número e instante de recepción UTC. **La
fuente actual no es aún reproducible desde este repositorio aislado**; antes
de la entrega añadiremos una fuente de video o secuencia autorizada y los
pasos necesarios para reproducir C1/C2.

## Instalación del prototipo

Probado en Windows con Python 3.14.3. Desde la raíz de este repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Con el grabador local activo en `127.0.0.1:8765`, ejecuta:

```powershell
.\.venv\Scripts\python.exe lector_opencv.py
```

La ventana muestra los cuadros recibidos. Pulsa `q` con la ventana activa o
Ctrl+C en la terminal para detener. También puedes limitar la prueba:

```powershell
.\.venv\Scripts\python.exe lector_opencv.py --seconds 20 --no-preview
```

No se guarda ningún archivo. Si la vista previa aparece sobre el área
capturada, muévela al otro monitor para evitar el efecto de espejo.

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
