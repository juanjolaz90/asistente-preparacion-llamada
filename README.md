# Prototipo — Asistente de preparación de llamada (Amenitiz)

Porción funcional de la interacción principal: **perfil de hotel (+ fragmento de llamada) → qué proponer, cómo decirlo, qué no prometer, y la regla detrás de cada afirmación.**

No es producción. Es un prototipo acotado, correcto en lo que cubre y honesto en lo que no.

## Requisitos

- Python 3.10+
- Sin dependencias para el modo plantilla por línea de comandos.
- Para la demo web: `pip install streamlit` (o todo de una vez: `pip install -r requirements.txt`).
- Clave: copia `.env.example` como `.env` y rellena `ANTHROPIC_API_KEY`.
- Para el modo Claude: `pip install anthropic` y la clave en `ANTHROPIC_API_KEY`, ya sea en el entorno
  o en un archivo `.env` en la raíz (`ANTHROPIC_API_KEY=...`). Si está en los dos, gana el entorno.
  El `.env` está en `.gitignore` y nunca se sube.
  (opcional: `ANTHROPIC_MODEL=claude-sonnet-5-5`, también en el `.env`).

## Ejecutar

### Demo web (Streamlit)

```bash
streamlit run app.py
```

Se abre en `http://localhost:8501`.
- En la barra lateral eliges un caso de prueba (o "Perfil en blanco"). Todos los campos del perfil y la transcripción se pueden editar, para que los entrevistadores metan sus propios datos. El país es texto libre a propósito: "DE" tiene que dar un error controlado.
- Con "Preparar llamada" sale, en este orden:
  1. el escalado a persona, en rojo y arriba;
  2. el plan y el precio, con el segmento;
  3. los módulos recomendados, elegibles sin señal y no elegibles, cada uno con su motivo;
  4. el pitch, las objeciones y lo que no hay que decir;
  5. las señales con su evidencia;
  6. la tabla de fuentes (regla → afirmación) y el JSON completo.
- El interruptor "Usar Claude" solo se activa si hay `ANTHROPIC_API_KEY` en el entorno o en `.env`. Sin clave, la app funciona igual en modo plantilla. Si Claude falla o el guardrail bloquea, se avisa en pantalla y se usa la plantilla.
- La app llama a la misma función que el CLI (`recomendar.procesar`): no hay lógica duplicada.
- La tabla de fuentes se pinta en markdown y no con `st.dataframe`. En el Windows de desarrollo, el Control de aplicaciones bloquea una DLL de pandas y `st.dataframe` rompía la página. Así la demo no depende de pandas.

### Línea de comandos

```bash
python recomendar.py casos/caso_01_italia_bb.json            # sin IA (plantilla)
python recomendar.py casos/caso_01_italia_bb.json --claude   # señales + redacción con Claude
python recomendar.py casos/caso_01_italia_bb.json --json     # JSON completo (para n8n / card)
python run_eval.py            # todos los casos → salidas/*.json + salidas/resumen.md
python run_eval.py --claude
python test_guardrail.py      # el guardrail bloquea la redacción mala y deja pasar los falsos positivos conocidos
```

## Cómo está montado

```
casos/*.json ──► senales.py ──► reglas.py ──► redactor.py ──► guardrail.py ──► salida
 (perfil +        (qué dice      (SIN IA:       (plantilla      (bloquea módulos
  transcripción)   el cliente)    plan, precio,   o Claude)       o precios no
                                  elegibilidad,                   permitidos → cae
                                  escalado)                       a plantilla)
                      ▲
                 catalogo.json  ← única fuente de verdad (precios, reglas, segmentos)
```

| Archivo | Qué es | Determinista o generativo |
|---|---|---|
| `catalogo.json` | Planes, módulos, precios, elegibilidad, segmentos, descuentos, motivos de escalado. Lo mantiene Product GTM. | Datos |
| `senales.py` | Convierte perfil + transcripción en señales cerradas (`alto_volumen_mensajes`, `tiene_fnb`, `pide_descuento:20`…). Con `--claude`, Claude lee la transcripción y devuelve solo etiquetas de la lista cerrada con la cita que las justifica; sin API key (o si falla), palabras clave ES/FR/IT/PT. | Generativo acotado a lista cerrada + validación; regex determinista como fallback |
| `reglas.py` | Plan destino, precio, módulos recomendados / elegibles sin señal / no elegibles con motivo, escalado, fuentes. | Determinista |
| `redactor.py` | Pitch + objeciones a partir del resultado de reglas. Modo plantilla o modo Claude. | Generativo |
| `prompt_sistema.md` | Instrucciones a Claude: solo puede usar lo que le pasan las reglas. | — |
| `guardrail.py` | Revisa el texto final: módulos no permitidos, importes no permitidos, descuentos > 10%, escalado omitido. | Determinista |
| `recomendar.py` | CLI. Orquesta todo. | — |
| `run_eval.py` | Corre todos los casos y genera la tabla. | — |
| `app.py` | Demo en Streamlit: formulario editable + resultado. Usa `recomendar.procesar`. | — |
| `test_guardrail.py` | Comprueba que el guardrail bloquea la redacción mala y deja pasar los falsos positivos ya corregidos. | — |
| `casos/` | Los 2 fragmentos del caso + 4 casos propios (3 trampas + 1 de robustez). | — |

### Formato de entrada (`casos/*.json`)

```json
{
  "nombre": "texto libre",
  "esperado": "lo que debería salir (para la evaluación)",
  "perfil": {
    "tipo_alojamiento": "hotel | B&B | casa de huéspedes | apartamentos | hostel",
    "habitaciones": 24,
    "pais": "ES | FR | IT | PT",
    "idioma_comercial": "es",
    "caso": "migracion | nuevo_negocio",
    "plan_actual": "Presence | Sales Pro | Ultimate | null",
    "segmento_migracion": "A | B | C",
    "delta_precio_pct": 18,
    "health_score": "alto | medio | bajo",
    "antiguedad_meses": 30,
    "cuota_ota_pct": 70,
    "cerradura": "Nuki | Salto KS | TTLock | null",
    "senales": ["opcional: señales ya conocidas"]
  },
  "transcripcion": "texto opcional, anonimizado"
}
```

Son los campos de HubSpot que lista la ficha. En producción los rellena un workflow de n8n leyendo el Contact/Company y la última transcripción.

### Formato de salida (`--json`)

`senales` (con la evidencia de cada una), `reglas` (plan, precio, módulos por categoría, escalado, avisos, fuentes), `redaccion` (escalar_a_persona, resumen, pitch, objeciones, no_decir, _modo) y `guardrail` (violaciones, si cayó a plantilla).

## Supuestos que he tomado

1. **Plazas de early access de Ami**: el catálogo tiene `plazas_restantes: 23`. En producción es un contador que lee de HubSpot/Looker.
2. **Cerraduras compatibles**: lista inventada (Nuki, Salto KS, TTLock). La ficha solo dice "lista de partners".
3. **Nuevo negocio → Advanced solo con motivo**: Core por defecto; Advanced cuando Revenue Manager encaja (≥ 8 hab. + señal de precios/estacionalidad). Es mi lectura de "Advanced no es destino por defecto".
4. **Website Builder por mapeo (Ultimate → Core + WB)**: si el cliente dice que ya tiene web, el módulo sigue en la migración pero el pitch no se apoya en él.
5. **Segmento C siempre escala**: la herramienta prepara al AM senior pero marca la llamada como "decisión humana".
6. **`gestion_propietario`** se infiere de B&B / casa de huéspedes ≤ 12 hab. Es una heurística.
7. **Idioma**: el comercial recibe el pitch en su idioma (`idioma_comercial`); la transcripción puede venir en ES/FR/IT/PT.
8. **Precios** tal cual la ficha (ilustrativos).

## Qué no hace bien (y qué corregiría)

- **Extracción de señales (hecho: Claude con lista cerrada, regex como fallback).** Con `--claude`, `senales.py` manda la transcripción a Claude, que solo puede devolver etiquetas de `PALABRAS.keys()` (un `enum` en el JSON schema) más `descuento_pedido_pct`, cada una con una cita literal. Además se valida en código: etiqueta fuera de la lista, cita que no está tal cual en la transcripción o % que no aparece → se descarta y se apunta en `senales_extraccion.descartes`. Si no hay API key o la llamada falla, se usa el regex y se avisa en `senales_extraccion.aviso`. Las señales del perfil de HubSpot (`cuota_ota_pct`, tipo de alojamiento…) siguen siendo deterministas, y las reglas no han cambiado.
  - *Resultado*: desaparece el falso positivo `alta_cuota_ota` del caso 01 (estable en 3 ejecuciones seguidas): "las herramientas de Booking" se lee como `menciona_competencia`, no como dependencia de las OTA. En el caso 03, Claude detecta además `caja_aparte` en francés ("La caisse est séparée…"), que el regex no cogía. No cambia la recomendación porque el POS no es elegible en Francia. En los 6 casos el resultado de las reglas es el mismo que con regex.
  - *Coste y límites*: añade una llamada a la API por caso (esfuerzo `low`), es decir, latencia y coste. Es no determinista: la misma transcripción podría dar etiquetas distintas en casos límite. La validación de cita literal descarta señales si el modelo parafrasea en vez de copiar; prefiero perder una señal a inventarla. La tabla de `salidas/resumen.md` muestra qué modo se usó en cada caso.
- **Modo plantilla suena a robot.** Es deliberado: sirve para probar las reglas y como fallback. El pitch real lo escribe Claude con `--claude`.
- **Guardrail por regex.** Puede dar falsos positivos (p. ej. "no te recomiendo POS" se permite por la negación, pero "POS no" en otra frase podría bloquearse). Prefiero eso a un precio inventado. Falsos positivos que salieron en la primera ejecución real con Claude, y cómo los he corregido:
  - *Caso 04*: Claude citaba el precio actual del cliente ("Sales Pro, 65 €/mes"), la subida ("subida del 18 %") y lo que pide la clienta ("solo acepta con un 20 % de descuento"). El guardrail lo leía como un precio inventado y descuentos ofrecidos, y caía a plantilla. Ahora se permiten esas tres cifras **solo en ese contexto**: el precio legacy junto al plan actual o a "actual / antes", el % de subida junto a "subida / aumento", y el % pedido junto a "pide / acepta / exige" y sin verbo de oferta. Además, el campo `objecion` (lo que dice el cliente) ya no se revisa como oferta. "Core a 65 €", "te doy un 20 %" o "te aplico un 18 % de descuento" siguen bloqueando.
  - *Caso 03*: "POS" casaba dentro de "**pos**ible" y en la etiqueta `[MOD-POS]`. Ahora se busca la palabra completa y fuera de etiquetas.
  - *Pendiente*: en una ejecución del caso 03, Claude escribió "Sobre el POS, me comprometo a consultarlo…". No lleva negación cerca y el guardrail lo bloquea. Es una frase aceptable, pero no la abro: aflojar más la regla abriría la puerta a proponer el POS en Francia. Lo dejo como falso positivo conocido; cae a plantilla y se avisa.
  - `test_guardrail.py` fija las dos cosas: la redacción mala (POS en FR, 39 €, 15 %, Core a 89 €, Core a 65 €, 20 % ofrecido, sin escalado) se bloquea, y los falsos positivos anteriores pasan.
- **Objeciones**: en la primera ejecución Claude devolvía 4 objeciones por caso (la ficha pide 1–2) y mezclaba frases para el cliente con instrucciones para el comercial. El prompt ahora fija un máximo de 2 y exige que la respuesta sea una frase dirigida al cliente; las instrucciones internas van en `no_decir`. Comprobado: 2 objeciones en los casos 01, 02 y 04.
- **Dato inconsistente en el caso 04**: `delta_precio_pct` es 18, pero de 65 € a 79 € la subida es del 21,5 %. Lo detectó Claude en `no_decir`. No cambia el segmento (B: 10–25 %), pero el caso de prueba debería corregirse.
- **API**: el modelo actual rechaza `temperature`, así que `redactor.py` no lo envía. `max_tokens` está en 16000 porque el pensamiento adaptativo, activo por defecto, cuenta dentro del límite.
- **No lee HubSpot.** El perfil llega en JSON. La integración (n8n → HubSpot API → `recomendar.py` → custom card / nota en la timeline) está especificada en el documento, no construida.
- **No hay memoria de llamadas anteriores** más allá del campo de resumen de IA que ya existe en HubSpot (se pasa en el perfil como `resumen_ia` si se quiere).

## Pasar a n8n (lo que haría después)

1. **Trigger**: HubSpot → cambio de `deal stage` a "Llamada de migración programada" (o botón en la card).
2. **HubSpot node**: leer Company + último Call engagement (transcripción) → montar el JSON de perfil.
3. **Code node (Python)**: `senales` + `reglas` tal cual (son puros, sin dependencias).
4. **Anthropic node**: `prompt_sistema.md` como system, contexto como user, salida JSON.
5. **Code node**: `guardrail`. Si bloquea → plantilla + aviso a RevOps en Slack.
6. **HubSpot node**: escribir la recomendación en una propiedad custom (`prep_llamada_json`) y mostrarla en una **CRM card** (UI extension) con botones 👍 / 👎.
