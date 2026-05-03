# TESTING.md — Checklist de pruebas E2E antes de abrir al 100% del tráfico

> Ejecuta estos casos **en orden** sobre el servidor de producción (Railway).
> Márcalos con ✅ PASS, ❌ FAIL, o ⚠️ PARCIAL según lo observado.
> No abrir al tráfico completo hasta que los casos 1–7 sean todos ✅.
> Los casos 8–10 son de carga/resiliencia — se documentan los resultados
> aunque alguno falle (especialmente el 10, que está aceptado).

---

## Herramientas necesarias

| Herramienta | Para qué |
|---|---|
| WhatsApp real (número de prueba) | Enviar mensajes al agente |
| Kommo CRM (acceso de admin) | Verificar etapas, tags y campos personalizados |
| Railway logs (`railway logs --tail`) | Ver logs del servidor en tiempo real |
| `curl` o Postman | Casos 8–9 (llamadas directas al webhook) |
| Cronómetro | Medir tiempos de respuesta |

---

## Pre-flight: verificaciones antes de empezar

Antes de ejecutar cualquier caso, confirma que:

- [ ] `GET https://tu-app.up.railway.app/` responde `{"status": "ok", "service": "agentkit-iad-mexico"}`
- [ ] Los logs arrancan sin errores de `ValidationError` ni `sys.exit(1)` (indica env vars faltantes)
- [ ] El log de arranque contiene `[QUEUE] Worker iniciado` y `Scheduler iniciado`
- [ ] El Google Sheet del inventario tiene al menos 3 filas con datos completos y `Disponible = Sí`
- [ ] Kommo tiene el pipeline "IA" con las etapas nombradas (verifica en Configuración → Pipelines)
- [ ] El número de WhatsApp de prueba **NO está** en ningún lead activo de Kommo (para que el caso 1 sea clean)

---

## CASO 1 — Lead nuevo recibe bienvenida automática

**Objetivo:** Verificar que cuando un lead entra al pipeline en etapa "Leads Entrantes",
el agente le envía el primer mensaje sin que el cliente haya escrito nada.

> **Nota sobre el flujo actual:** En el modo Kommo, el agente responde mensajes entrantes
> vía `/webhooks/kommo/chat`. El mensaje de bienvenida automático es responsabilidad del
> workflow de Kommo (ej: regla de automatización que manda el primer mensaje al entrar a
> la etapa "Leads Entrantes"). Si Kommo está configurado para enviar ese primer mensaje,
> el agente lo recibirá como `es_propio=False` y responderá normalmente.
>
> Si usas el modo Whapi (`AGENT_MODE=whapi`), el primer mensaje del cliente llega
> directamente al webhook `/webhook` y el agente responde sin automatización previa.

### Pasos

1. En Kommo, crea un lead manualmente en el pipeline "IA" en la etapa **Leads Entrantes**
   con el número de WhatsApp de prueba como contacto.
2. Inicia el cronómetro.
3. Espera a que llegue un mensaje al número de prueba (si hay automatización de bienvenida
   configurada en Kommo) **O** envía un primer mensaje: `"Hola, me interesa información"`.
4. Detén el cronómetro cuando llegue la respuesta del agente.

### Resultado esperado

- [ ] El cliente recibe un mensaje de bienvenida / respuesta en menos de 30 segundos.
- [ ] El mensaje menciona a Sofía y a IAD México (o el nombre configurado en `business.yaml`).
- [ ] El lead sigue en **Leads Entrantes** (no fue movido aún — no hay datos suficientes).
- [ ] En los logs aparece:
  ```
  [QUEUE] kommo_chat | lead=XXXX texto='Hola...' lag=XXms
  [BRAIN] Lead XXXX → XX chars
  [QUEUE] Respuesta enviada a lead XXXX
  ```

### Señales de falla

- ❌ No llega ningún mensaje después de 60 segundos → revisar que el webhook de Kommo
  apunta a `https://tu-app.up.railway.app/webhooks/kommo/chat?secret=TU_SECRET`.
- ❌ Log muestra `ERROR FATAL` o `ValidationError` → revisar variables de entorno en Railway.
- ❌ La respuesta menciona un negocio diferente → revisar `config/business.yaml` y
  `config/prompts.yaml`.

---

## CASO 2 — Cliente responde → agente responde en menos de 30 segundos

**Objetivo:** Verificar el tiempo de respuesta extremo a extremo bajo condiciones normales.

### Pasos

1. Usa el lead creado en el Caso 1 (ya tiene historial).
2. Envía el mensaje: `"¿Cuánto cuesta una acción del Acuario Vallarta?"`
3. Inicia el cronómetro en el momento exacto del envío.
4. Detén el cronómetro al recibir la respuesta.
5. Repite 3 veces con mensajes distintos y anota los tiempos:
   - `"¿Cuál es el ROI proyectado?"`
   - `"¿En qué zona está ubicado el proyecto?"`

### Resultado esperado

- [ ] Las 3 respuestas llegan en **menos de 30 segundos** cada una.
- [ ] Los tiempos registrados en los logs (`lag=XXms` y la latencia de Groq) son razonables.
- [ ] Las respuestas contienen información real del proyecto (precio, ROI, ubicación),
  **no inventada** — el agente debe citar `$550,000 MXN`, `30-35% anual`, `Plaza La Isla`.
- [ ] No aparecen mensajes de error en los logs.

### Tiempos de referencia

| Percentil | Tiempo aceptable |
|---|---|
| P50 (mediana) | < 8 s |
| P95 | < 20 s |
| Límite duro | < 30 s |

### Señales de falla

- ❌ Tiempo > 30 s → revisar latencia de Groq API y conexión Railway–Groq.
- ❌ El agente inventa datos (precio diferente, ROI diferente) → revisar system prompt en
  `config/prompts.yaml` — la sección "Sobre el negocio" debe tener los números correctos.

---

## CASO 3 — Conversación de calificación: 6 datos uno por uno

**Objetivo:** Verificar que el agente recopila los datos calificadores de forma natural
y los guarda en Kommo sin que el cliente los dé todos a la vez.

### Datos a recopilar

El agente debe obtener y guardar en Kommo vía `registrar_dato_calificador`:
presupuesto, zona, tipo, recámaras (si aplica), urgencia, forma de pago.

### Pasos

1. Con el lead del Caso 1 (limpia el historial si lo quieres fresh: borra el lead de
   Kommo y crea uno nuevo).
2. Inicia la conversación respondiendo de forma natural, revelando **un dato a la vez**:

   | Tu mensaje | Dato que revelas |
   |---|---|
   | `"Me interesa invertir en algo en Puerto Vallarta"` | zona |
   | `"Tengo como un millón de pesos disponibles"` | presupuesto |
   | `"Busco algo para rentar, tipo departamento"` | tipo |
   | `"Dos recámaras estaría bien"` | recámaras |
   | `"Quiero algo para este año si se puede"` | urgencia |
   | `"Pagaría de contado"` | forma de pago |

3. Después de cada mensaje, espera la respuesta del agente **y** verifica en Kommo
   que el dato fue guardado (campo personalizado o tag `dato_campo:valor`).

### Resultado esperado

- [ ] Después de `"Me interesa invertir en Puerto Vallarta"`:
  campo `zona` = "Puerto Vallarta" en Kommo (o tag `dato_zona:Puerto Vallarta`).
- [ ] Después de `"Tengo un millón de pesos"`:
  campo `presupuesto` = "1000000" en Kommo (o tag `dato_presupuesto:1000000`).
- [ ] Después de los 6 datos, en Kommo aparecen los 6 datos calificadores guardados.
- [ ] El agente **nunca pregunta el mismo dato dos veces** si ya fue respondido.
- [ ] En los logs aparece `Tool call #X: registrar_dato_calificador` por cada dato nuevo.
- [ ] El agente usa una pregunta distinta para cada dato — no lanza un formulario de golpe.

### Señales de falla

- ❌ El dato no aparece en Kommo después del mensaje → `KOMMO_FIELD_*` no está configurado
  y los tags `dato_campo:valor` tampoco aparecen → revisar `agent/tools.py:CUSTOM_FIELD_IDS`.
- ❌ El agente hace todas las preguntas en un solo mensaje → revisar system prompt,
  sección de instrucciones de conversación.
- ❌ En los logs no aparece `Tool call` → la versión de Groq o el modelo no está
  devolviendo tool calls; verificar que `GROQ_MODEL=llama-3.3-70b-versatile`.

---

## CASO 4 — Lead pide cita → tag agregado y asesor notificado

**Objetivo:** Verificar que cuando el lead expresa intención de ver el proyecto,
se registra la propuesta de cita en Kommo sin mover la etapa (guardia de cita).

### Pasos

1. Con el lead del Caso 3 (que ya tiene datos calificadores guardados), envía:
   `"Me gustaría agendar una llamada para el próximo martes a las 10am"`
2. Espera la respuesta del agente.
3. Revisa en Kommo el lead, sección **Tags**.
4. Si hay asesores configurados en Kommo, verifica que uno recibió notificación.

### Resultado esperado

- [ ] El agente confirma que registró la solicitud de cita con una respuesta como
  *"Con gusto — el equipo confirmará la disponibilidad para ese horario"*.
- [ ] En Kommo, el lead tiene el tag **`cita_propuesta`**.
- [ ] En Kommo, el lead tiene el tag **`cita_YYYY-MM-DD_HH:MM`** con la fecha/hora propuesta.
- [ ] La etapa del lead **NO cambió** — sigue donde estaba (el asesor debe confirmar manualmente).
- [ ] En los logs aparece `Tool call #X: agendar_cita`.
- [ ] Si `asesor_id` está configurado en el tool, el lead fue reasignado al asesor en Kommo.

> **Aclaración de diseño:** El bot agrega el tag `cita_propuesta` como señal para el equipo
> humano. La cita real se confirma manualmente desde Kommo. No se mueve a "Cita (pre)"
> automáticamente para evitar que el bot congele leads sin confirmación del asesor.

### Señales de falla

- ❌ El tag `cita_propuesta` no aparece en Kommo → el tool `agendar_cita` falló; revisar logs.
- ❌ El agente movió el lead a "Cita (pre)" automáticamente → esto es un bug; el bot
  debe usar `ia_sugiere_cita`, no mover la etapa directamente.
- ❌ El agente dijo que agendó pero en Kommo no hay cambios → revisar `KOMMO_ACCESS_TOKEN`
  con `scripts/probar_kommo.py`.

---

## CASO 5 — Lead dice "no me molesten" → mueve a Baja

**Objetivo:** Verificar el flujo completo de opt-out: el lead expresa que no quiere
ser contactado y el sistema lo mueve a "Ventas Perdidos" automáticamente.

### Pasos

1. Crea un lead nuevo en Kommo (número de prueba diferente al de los casos anteriores).
2. Envíalo directamente a la etapa **Leads Entrantes**.
3. Envía el mensaje: `"No me molesten, por favor"`.
4. Espera la respuesta del agente.
5. Revisa en Kommo la etapa y los tags del lead.

### Resultado esperado

- [ ] El agente responde con un mensaje de despedida respetuoso
  (ej: *"Entendido, no te escribiremos más. ¡Mucho éxito!"*).
- [ ] La etapa del lead en Kommo cambió a **Ventas Perdidos** (ID 143 = BAJA).
- [ ] En los logs aparece `Tool call #X: clasificar_lead` con `urgencia=opt-out`.
- [ ] El lead ya **no recibe seguimientos automáticos** del scheduler (verificar que no
  llega ningún mensaje al día siguiente).

> **Nota de implementación:** El código mueve el lead a BAJA (etapa 143) y lo deja
> como "Ventas Perdidos" en Kommo. No existe tag `DND_PERMANENTE` en la implementación
> actual — si quieres ese tag adicional para filtros en Kommo, agrégalo en
> `_tool_clasificar_lead` junto al `moveLeadToStage(BAJA)`.

### Señales de falla

- ❌ La etapa no cambió a "Ventas Perdidos" → el modelo no llamó `clasificar_lead`;
  revisar system prompt, sección de opt-out.
- ❌ El lead sigue recibiendo mensajes de seguimiento → `es_etapa_congelada(BAJA)` debería
  retornar `True`; verificar con `pytest tests/test_etapas.py -v`.
- ❌ El agente responde de forma agresiva o no reconoce el opt-out → ajustar system prompt.

### Variantes a probar (marca las que ejecutes)

- [ ] `"Baja mis datos"` → misma conducta
- [ ] `"No quiero que me contacten"` → misma conducta
- [ ] `"Quitar de la lista"` → misma conducta

---

## CASO 6 — Lead en etapa congelada envía mensaje → agente NO responde

**Objetivo:** Verificar la guardia más crítica del sistema: el bot guarda silencio
cuando el lead está siendo atendido por un humano.

### Pasos

1. Toma el lead del Caso 4 o crea uno nuevo.
2. **Muévelo manualmente** en Kommo a la etapa **IA - Cita (pre)** (ID 105360867).
3. Desde el número de prueba, envía: `"Hola, ¿a qué hora es mi cita?"`
4. Espera 60 segundos.
5. Revisa los logs del servidor.

### Resultado esperado

- [ ] El cliente **no recibe ningún mensaje** del bot.
- [ ] En los logs aparece exactamente:
  ```
  [BRAIN] Lead XXXX en etapa congelada (IA - Cita (pre)) — silenciado
  ```
- [ ] El mensaje fue procesado por la cola (`[QUEUE] kommo_chat | lead=XXXX`) pero
  la respuesta fue descartada.

### Variantes: repite con cada etapa congelada

| Etapa | ID | ¿Silenciado? |
|---|---|---|
| IA - Cita (pre) | 105360867 | [ ] ✅ / ❌ |
| Cita (durante y post) | 105360871 | [ ] ✅ / ❌ |
| Apartado | 105360875 | [ ] ✅ / ❌ |
| IA - Buscando algo diferente | 105360887 | [ ] ✅ / ❌ |
| Logrado con éxito (Ganado) | 142 | [ ] ✅ / ❌ |
| Ventas Perdidos (Baja) | 143 | [ ] ✅ / ❌ |

### Señales de falla

- ❌ El bot responde a un lead congelado → bug crítico; ejecutar inmediatamente:
  ```bash
  pytest tests/test_brain.py::test_etapa_congelada_retorna_none -v
  ```
  Si ese test pasa pero el bug ocurre en prod, el problema está en el parsing del
  webhook (el `status_id` del lead no se está leyendo correctamente desde Kommo).

---

## CASO 7 — Inventario: agente consulta el Sheet y no inventa

**Objetivo:** Verificar que cuando el cliente pregunta por propiedades, el agente
consulta el Google Sheet real y no genera datos ficticios.

### Preparación

1. Asegúrate de que el Sheet tiene al menos:
   - 1 propiedad disponible en Puerto Vallarta, precio entre $500k–$2M.
   - 1 propiedad **no disponible** (columna Disponible = "No" o "Vendido").
   - 1 propiedad en otra ciudad (Guadalajara, CDMX, etc.).

### Pasos

**Subprueba A — Consulta genérica:**
1. Envía: `"¿Qué propiedades tienen disponibles?"`
2. El agente debe describir propiedades reales del Sheet.
3. Toma nota de los nombres que devuelve.
4. **Verifica manualmente** en el Sheet que esas propiedades existen y están disponibles.

- [ ] Las propiedades mencionadas existen en el Sheet.
- [ ] No aparecen propiedades con `Disponible = "No"`.
- [ ] En los logs aparece `Tool call #X: consultar_inventario`.

**Subprueba B — Filtro de zona:**
1. Envía: `"¿Tienen algo en Guadalajara?"`
2. Si hay propiedades en Guadalajara: el agente las menciona.
3. Si no hay: el agente lo dice honestamente (puede ofrecer casi-matches).

- [ ] El agente no inventa propiedades en Guadalajara si no las hay.
- [ ] El agente no confunde zonas (no mezcla Guadalajara con Puerto Vallarta).

**Subprueba C — Pregunta fuera del inventario:**
1. Envía: `"¿Tienen casas en Cancún?"`
2. Si no hay propiedades en Cancún, el agente debe decirlo y ofrecer alternativas.

- [ ] El agente no inventa propiedades en Cancún.
- [ ] El agente ofrece lo disponible como alternativa o pregunta el presupuesto.

**Subprueba D — Caché:**
1. Edita el Sheet: cambia el precio de una propiedad.
2. Espera **5 minutos** (TTL del caché = 300 s).
3. Envía: `"¿Cuánto cuesta [nombre de la propiedad editada]?"`

- [ ] Después de 5 minutos el agente reporta el precio actualizado.
- [ ] En los logs aparece `[INVENTARIO] Descargando CSV desde Google Sheets` al expirar el caché.

### Señales de falla

- ❌ El agente inventa nombres de propiedades → revisar `INVENTORY_SHEET_CSV_URL` en Railway.
- ❌ El tool `consultar_inventario` nunca aparece en logs → el modelo no está usando tools;
  verificar `GROQ_MODEL` y que `tools_instrucciones` esté en `config/prompts.yaml`.
- ❌ Propiedades "No disponible" aparecen en resultados → ejecutar:
  ```bash
  pytest tests/test_inventario.py::test_no_disponibles_quedan_excluidos -v
  ```

---

## CASO 8 — Carga: 10 mensajes simultáneos, ninguno se pierde

**Objetivo:** Verificar que la cola FIFO in-memory procesa todos los mensajes bajo
carga concurrente y ninguno se descarta.

### Preparación

Necesitas 10 lead IDs reales en Kommo en etapa activa (no congelada).
Puedes crear 10 leads de prueba manualmente o usar el mismo lead ID 10 veces
(el worker procesa en serie, por lo que se encolan y responden todos).

### Pasos

1. Abre 2 terminales: una para logs, una para ejecutar el test.

2. **Terminal 1 — logs:**
   ```bash
   railway logs --tail 200
   ```

3. **Terminal 2 — 10 requests concurrentes al webhook:**
   ```bash
   # Crea este script y ejecútalo: scripts/test_carga.sh
   WEBHOOK="https://tu-app.up.railway.app/webhooks/kommo/chat"
   SECRET="TU_KOMMO_WEBHOOK_SECRET"
   LEAD_ID="TU_LEAD_ID"

   for i in $(seq 1 10); do
     curl -s -X POST "$WEBHOOK?secret=$SECRET" \
       --data-urlencode "messages[0][id]=msg-test-$i" \
       --data-urlencode "messages[0][dialog_id]=dialog-$LEAD_ID" \
       --data-urlencode "messages[0][chat_id]=52322$LEAD_ID@c.us" \
       --data-urlencode "messages[0][text]=Mensaje de carga número $i" \
       --data-urlencode "messages[0][from_me]=false" \
       --data-urlencode "leads[0][id]=$LEAD_ID" \
       --data-urlencode "account_id=TEST" &
   done
   wait
   echo "10 requests enviados"
   ```

4. Espera 60–120 segundos y revisa logs.
5. Cuenta en los logs cuántos mensajes fueron procesados y respondidos.

### Resultado esperado

- [ ] Los 10 webhooks devolvieron `{"status": "ok"}` inmediatamente (< 200 ms cada uno).
- [ ] En los logs aparecen 10 líneas `[QUEUE] kommo_chat | lead=XXXX`.
- [ ] En los logs aparecen 10 líneas `[QUEUE] Respuesta enviada a lead XXXX`.
- [ ] El orden de procesamiento es FIFO (los mensajes se responden en el orden en que llegaron).
- [ ] No hay errores `[QUEUE] Error procesando item`.
- [ ] El tamaño máximo de la cola (`qsize=N`) se registra en los logs DEBUG.

### Cómo interpretar los resultados

| Mensajes respondidos | Estado |
|---|---|
| 10/10 | ✅ PASS — cola funciona correctamente |
| 8–9/10 | ⚠️ PARCIAL — probablemente timeout de Kommo, revisar sendChatMessage |
| < 8/10 | ❌ FAIL — bug en la cola o en el procesador |

### Señales de falla

- ❌ Algunos mensajes nunca se procesan → revisar si el worker crasheó; buscar
  `[QUEUE] Worker detenido` en los logs sin haber reiniciado el servidor.
- ❌ El servidor tarda > 1 s en responder 200 → el webhook handler está bloqueándose;
  la llamada a `await enqueue()` debería ser instantánea.

---

## CASO 9 — Resiliencia: Kommo responde lento (> 2 s), no se pierden mensajes

**Objetivo:** Verificar que si la API de Kommo está lenta, el sistema no pierde mensajes
y el webhook sigue respondiendo 200 a tiempo.

> El diseño actual desacopla la recepción del webhook (respuesta 200 inmediata) del
> procesamiento (asíncrono en la cola). Si Kommo tarda en responder, el worker espera,
> pero el webhook ya respondió 200 y el mensaje está seguro en la cola.

### Pasos — Simulación de Kommo lento

1. **Opción A (recomendada) — Throttle en Railway:**
   Cambia temporalmente en Railway la variable `KOMMO_ACCESS_TOKEN` a un valor inválido.
   Las llamadas a Kommo fallarán con 403 (KommoForbiddenError), lo cual es análogo a
   una respuesta lenta/errónea.

2. Envía 3 mensajes al webhook con 5 segundos de separación:
   ```bash
   for i in 1 2 3; do
     curl -X POST "https://tu-app.up.railway.app/webhooks/kommo/chat?secret=SECRET" \
       --data-urlencode "messages[0][text]=Mensaje resiliencia $i" \
       --data-urlencode "messages[0][from_me]=false" \
       --data-urlencode "leads[0][id]=TU_LEAD_ID"
     echo "Enviado $i — esperando 5s"
     sleep 5
   done
   ```

3. Revisa los logs durante y después de los envíos.

4. Restaura `KOMMO_ACCESS_TOKEN` al valor correcto.

5. Verifica que los mensajes que fallaron son re-procesados o que los errores están
   debidamente registrados.

### Resultado esperado

- [ ] Los 3 webhooks respondieron `{"status": "ok"}` en < 500 ms.
- [ ] En los logs aparece `[QUEUE] kommo_chat | lead=XXXX` para los 3 mensajes.
- [ ] Cuando Kommo falla, aparece el error en logs pero el worker sigue corriendo:
  ```
  [QUEUE] Error enviando respuesta a lead XXXX: 403 Forbidden...
  ```
- [ ] El worker **no crashea** por el error — sigue procesando mensajes nuevos.
- [ ] Al restaurar el token, los mensajes **nuevos** se procesan correctamente
  (los 3 anteriores no se re-intentan — la cola ya los procesó/descartó).

> **Comportamiento esperado ante errores de Kommo:**
> El error queda registrado en logs, el worker continúa, y los mensajes
> **siguientes** son procesados normalmente. Los mensajes que fallaron
> **no se reintentan** (son in-memory, sin persistencia). Este es el comportamiento
> aceptado hasta que se implemente la cola Redis.

### Señales de falla

- ❌ El worker crasheó (`[QUEUE] Worker detenido` en logs sin restart) → bug crítico;
  el `try/except` en `_worker()` debería capturar cualquier excepción.
- ❌ El servidor tardó > 2 s en responder 200 → el webhook está esperando a Kommo;
  revisar si algún `await` de Kommo está fuera de la cola.

---

## CASO 10 — Restart del servidor con mensajes en cola

**Objetivo:** Documentar el comportamiento actual ante un restart con mensajes pendientes.
Este test **fallará** intencionalmente — está documentado para tener una línea base
antes de implementar Redis.

> **Comportamiento esperado y aceptado:** La cola actual es `asyncio.Queue` en memoria.
> Al reiniciar el servidor, la cola se pierde. Los mensajes en tránsito durante el restart
> no se procesan. Esto es un riesgo conocido y aceptado hasta la siguiente etapa.

### Pasos

1. Con el servidor en Railway corriendo, envía 5 mensajes en rápida sucesión:
   ```bash
   for i in $(seq 1 5); do
     curl -s -X POST "https://tu-app.up.railway.app/webhooks/kommo/chat?secret=SECRET" \
       --data-urlencode "messages[0][text]=Mensaje pre-restart $i" \
       --data-urlencode "messages[0][from_me]=false" \
       --data-urlencode "leads[0][id]=TU_LEAD_ID" &
   done
   ```

2. **Inmediatamente** (dentro de los siguientes 3 segundos), fuerza un restart:
   - En Railway: Settings → Redeploy (o haz un commit vacío para triggear deploy).

3. Espera a que el servidor vuelva a estar online (generalmente < 30 s en Railway).

4. Revisa cuántos de los 5 mensajes fueron respondidos en Kommo.

### Resultado esperado (aceptado)

- [ ] Los mensajes que ya estaban **siendo procesados** al momento del restart
  pueden haberse enviado o no — resultado indeterminado, aceptado.
- [ ] Los mensajes que estaban **en cola esperando** (no procesados aún) **se pierden** — aceptado.
- [ ] El servidor arranca limpiamente después del restart sin errores:
  ```
  Base de datos inicializada
  [QUEUE] Worker iniciado
  Scheduler iniciado
  ```
- [ ] Los mensajes **nuevos** que llegan después del restart se procesan normalmente.

### Documentar el resultado real

| Mensajes enviados | Mensajes respondidos | Mensajes perdidos |
|---|---|---|
| 5 | ___ | ___ |

> **Siguiente paso (post-release):** Reemplazar `asyncio.Queue` en `services/queue.py`
> por una cola Redis (rq, arq, o dramatiq) para garantizar persistencia ante restarts.
> La interfaz pública (`enqueue()`, `iniciar_worker()`) no cambia.

---

## Resumen de resultados

Completa esta tabla al terminar todos los casos:

| # | Caso | Estado | Notas |
|---|---|---|---|
| 1 | Lead nuevo recibe bienvenida | ⬜ PENDIENTE | |
| 2 | Respuesta en < 30 segundos | ⬜ PENDIENTE | |
| 3 | Calificación: 6 datos uno por uno | ⬜ PENDIENTE | |
| 4 | Pide cita → tag + notificación asesor | ⬜ PENDIENTE | |
| 5 | "No me molesten" → etapa Baja | ⬜ PENDIENTE | |
| 6 | Etapa congelada → bot silenciado | ⬜ PENDIENTE | |
| 7 | Inventario: Sheet real, no inventa | ⬜ PENDIENTE | |
| 8 | 10 mensajes simultáneos sin pérdidas | ⬜ PENDIENTE | |
| 9 | Kommo lento → sistema resiliente | ⬜ PENDIENTE | |
| 10 | Restart con cola activa (baseline) | ⬜ PENDIENTE | |

### Criterio de apertura al tráfico

```
Casos 1–7 todos ✅  →  OK para abrir al 100% del tráfico
Casos 8–9          →  Documentar resultados, no bloquean apertura
Caso 10            →  Se acepta fallo, documentar mensajes perdidos como baseline
```

---

## Rollback

Si cualquier caso 1–6 falla en producción con tráfico real:

1. En Railway, ve a **Deployments** y haz rollback al deploy anterior (un clic).
2. El deploy anterior estará disponible mientras el nuevo no reemplaza el slug.
3. Tiempo estimado de rollback: < 60 segundos.
4. Después del rollback, abre un issue con: logs completos, mensaje que lo trigger, comportamiento observado vs esperado.
