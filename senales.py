"""
Extracción de señales a partir del perfil y de un fragmento de transcripción.

Dos modos para la transcripción (las señales del perfil de HubSpot son siempre deterministas):
  - "claude": Claude lee la transcripción y devuelve SOLO etiquetas de la lista cerrada
    (`PALABRAS.keys()` + `pide_descuento:N`), cada una con la frase literal que la justifica.
    Salida forzada por JSON schema con `enum`; además se valida aquí: etiqueta fuera de la
    lista, cita que no aparece en la transcripción o % fuera de rango → se descarta.
  - "regex": diccionario de palabras clave por idioma (ES/FR/IT/PT/EN). Fallback sin API key
    o si la llamada falla. Determinista y auditable, pero frágil (p. ej. "Booking" → alta_cuota_ota).

Las reglas no cambian: reciben el mismo conjunto de etiquetas venga de donde venga.
"""

import json
import os
import re

# señal -> lista de expresiones (regex, sin distinguir mayúsculas)
PALABRAS = {
    "alto_volumen_mensajes": [
        r"whatsapp", r"mensajes", r"messaggi", r"messages", r"mensagens",
        r"respond(iendo|er|o)", r"rispond", r"répond", r"horas al d[ií]a",
    ],
    "huespedes_multilingues": [
        r"ingl[eé]s", r"alem[aá]n", r"neerland[eé]s", r"franc[eé]s", r"italiano",
        r"inglese", r"tedesco", r"anglais", r"allemand", r"idiomas", r"lingue", r"langues",
    ],
    "tiene_web_propia": [
        r"ya tengo (una )?web", r"ho gi[aà] un sito", r"j'ai d[ée]j[aà] un site",
        r"j[aá] tenho (um )?site", r"me la hizo", r"no uso",
    ],
    "sin_web": [
        r"no tenemos web", r"sin web", r"non abbiamo (un )?sito", r"pas de site",
        r"solo (es )?una p[aá]gina", r"p[aá]gina con un (n[uú]mero de )?tel[eé]fono",
    ],
    "alta_cuota_ota": [
        r"booking", r"expedia", r"comisiones", r"commissioni", r"commissions", r"ota",
    ],
    "tiene_fnb": [
        r"\bbar\b", r"restaurante", r"ristorante", r"desayuno", r"colazione",
        r"petit[- ]d[ée]jeuner", r"caf[eé]\b", r"cocina",
    ],
    "caja_aparte": [
        r"caja aparte", r"cuadr\w+ (todo )?a mano", r"(caja|cobr\w+|tpv|ticket\w*) .{0,25}a mano", r"cassa separata",
        r"caisse s[ée]par[ée]e",
    ],
    "precios_manuales": [
        r"cambiamos los precios", r"precios demasiado bajos", r"cuando me acuerdo",
        r"prezzi", r"tarifs", r"llenos en junio", r"precio(s)? fijo",
    ],
    "demanda_estacional": [
        r"verano", r"agosto", r"estate", r"[ée]t[ée]\b", r"temporada", r"stagione", r"saison",
    ],
    "pide_precio_antiguo": [
        r"precio antiguo", r"precio de antes", r"prezzo (vecchio|di prima)",
        r"ancien prix", r"pre[cç]o antigo", r"dejar(me)? con el precio",
    ],
    "menciona_competencia": [
        r"herramientas de booking", r"cambiar(me)? de", r"irme", r"cancelar", r"dar(me)? de baja",
        r"competencia", r"otro (pms|proveedor|sistema)", r"strumenti di booking", r"disdire",
    ],
    "self_checkin": [r"self[- ]?check[- ]?in", r"sin recepci[oó]n", r"sin personal", r"llaves"],
    "tiene_extras": [r"traslados", r"early check", r"late check", r"extras", r"parking", r"spa"],
}

DESCUENTO_RE = re.compile(r"(\d{1,2})\s?%\s*(de )?(descuento|desc\.|sconto|remise|desconto)|descuento\s*(del|de)?\s*(\d{1,2})\s?%", re.I)

# Qué significa cada etiqueta. Es lo que lee Claude; tiene que ser igual de estricto que las reglas.
DESCRIPCIONES = {
    "alto_volumen_mensajes": "El cliente dedica mucho tiempo a responder mensajes de huéspedes (WhatsApp, email, chat).",
    "huespedes_multilingues": "Sus huéspedes hablan varios idiomas o tiene que atenderles en varios idiomas.",
    "tiene_web_propia": "Ya tiene una web propia que le sirve o no quiere pagar por otra.",
    "sin_web": "No tiene web, o su web es mínima (p. ej. solo una página con un teléfono) y no recibe reservas.",
    "alta_cuota_ota": "Depende de las OTA (Booking, Expedia…) para SUS RESERVAS: la mayoría le llegan por ahí o se queja de las comisiones. "
                      "NO aplica si solo menciona Booking como alternativa a la que irse (eso es menciona_competencia).",
    "tiene_fnb": "Tiene restaurante, bar, cafetería o sirve desayunos/comidas.",
    "caja_aparte": "Cobra el bar/restaurante/tienda con una caja o TPV separado del software del hotel, o lo cuadra a mano.",
    "precios_manuales": "Cambia los precios a mano, tarde o sin método, o dice que vende demasiado barato.",
    "demanda_estacional": "Tiene temporada alta/baja marcada (verano, agosto, temporada…).",
    "pide_precio_antiguo": "Pide mantener el precio o el plan que tenía antes.",
    "menciona_competencia": "Habla de cancelar, darse de baja, irse a otro proveedor o a las herramientas de Booking/otra plataforma.",
    "self_checkin": "Los huéspedes entran solos: sin recepción, llaves en caja, check-in sin personal.",
    "tiene_extras": "Vende o podría vender extras: traslados, early/late check-in, parking, spa…",
}
assert set(DESCRIPCIONES) == set(PALABRAS), "DESCRIPCIONES y PALABRAS deben tener las mismas etiquetas"

_PROMPT_SENALES = """Eres un extractor de señales para un sistema de reglas de Amenitiz (software para hoteles pequeños).
Lees un fragmento de transcripción de llamada (puede estar en español, francés, italiano, portugués o inglés)
y devuelves SOLO etiquetas de esta lista cerrada, cuando lo que dice el CLIENTE las justifica claramente:

{lista}

Además, si el cliente pide un descuento concreto en %, devuelve ese número en "descuento_pedido_pct"; si no, 0.

Reglas:
- Solo cuenta lo que dice el cliente sobre su propia situación, no lo que dice el comercial.
- Ante la duda, no la incluyas. Es mejor perder una señal que inventarla.
- "frase" es una cita LITERAL y corta de la transcripción (copiada tal cual, en el idioma original) que justifica la etiqueta.
- No añadas etiquetas que no estén en la lista."""


def _normalizar(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().strip("….\"'«»“”").lower()


def _senales_claude(transcripcion: str) -> tuple[dict, list[str]]:
    """Devuelve ({etiqueta: frase}, [descartes]). Lanza RuntimeError si no se puede llamar a la API."""
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("Falta el paquete 'anthropic'.") from e
    from redactor import MODELO  # importa redactor → carga el .env si existe
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("Falta ANTHROPIC_API_KEY en el entorno o en el archivo .env.")

    etiquetas = sorted(PALABRAS)
    esquema = {
        "type": "object",
        "properties": {
            "senales": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "senal": {"type": "string", "enum": etiquetas},
                        "frase": {"type": "string"},
                    },
                    "required": ["senal", "frase"],
                    "additionalProperties": False,
                },
            },
            "descuento_pedido_pct": {"type": "integer"},
            "frase_descuento": {"type": "string"},
        },
        "required": ["senales", "descuento_pedido_pct", "frase_descuento"],
        "additionalProperties": False,
    }
    sistema = _PROMPT_SENALES.format(lista="\n".join(f"- {k}: {DESCRIPCIONES[k]}" for k in etiquetas))
    try:
        msg = anthropic.Anthropic().messages.create(
            model=MODELO,
            max_tokens=4000,
            system=sistema,
            messages=[{"role": "user", "content": "TRANSCRIPCION:\n" + transcripcion}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": esquema}},
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"Error de la API de Anthropic: {e}") from e
    if msg.stop_reason == "refusal":
        raise RuntimeError("El modelo rechazó la petición de extracción.")
    texto = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    try:
        data = json.loads(texto)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"La extracción no devolvió JSON válido: {e}") from e

    # Validación propia: no nos fiamos solo del schema
    texto_norm = _normalizar(transcripcion)
    encontradas, descartes = {}, []
    for item in data.get("senales", []):
        s, frase = item.get("senal"), str(item.get("frase", ""))
        if s not in PALABRAS:
            descartes.append(f"{s}: etiqueta fuera de la lista cerrada")
        elif not frase or _normalizar(frase) not in texto_norm:
            descartes.append(f"{s}: la cita «{frase}» no aparece literal en la transcripción")
        elif s not in encontradas:
            encontradas[s] = frase
    pct, frase_d = data.get("descuento_pedido_pct") or 0, str(data.get("frase_descuento", ""))
    if pct:
        if not (1 <= int(pct) <= 99):
            descartes.append(f"pide_descuento:{pct}: porcentaje fuera de rango")
        elif not re.search(rf"\b{int(pct)}\s?%", transcripcion):
            descartes.append(f"pide_descuento:{pct}: el {pct}% no aparece en la transcripción")
        else:
            encontradas[f"pide_descuento:{int(pct)}"] = frase_d or f"{pct}%"
    return encontradas, descartes


def _senales_regex(transcripcion: str) -> dict:
    """Extracción por palabras clave. Devuelve {etiqueta: fragmento}."""
    encontradas = {}
    texto = transcripcion.lower()
    for senal, patrones in PALABRAS.items():
        for p in patrones:
            m = re.search(p, texto, re.I)
            if m:
                ini, fin = max(0, m.start() - 40), min(len(texto), m.end() + 40)
                encontradas[senal] = "…" + transcripcion[ini:fin].strip() + "…"
                break

    m = DESCUENTO_RE.search(texto)
    if m:
        pct = next((g for g in m.groups() if g and g.isdigit()), None)
        if pct:
            encontradas[f"pide_descuento:{pct}"] = "…" + transcripcion[max(0, m.start()-30):m.end()+30].strip() + "…"
    return encontradas


def extraer_senales(perfil: dict, transcripcion: str | None, usar_claude: bool = False,
                    info: dict | None = None) -> tuple[set, dict]:
    """
    Devuelve (conjunto de señales, dict de evidencias señal -> fragmento que la justifica).
    Mezcla señales declaradas en el perfil (campo `senales`) con las detectadas en el texto.

    Con `usar_claude=True` la transcripción la lee Claude; si falla (sin API key, error de red…)
    se usa el modo regex. Si se pasa `info`, se rellena con `modo` ("claude:<modelo>" | "regex" |
    "sin_transcripcion"), `aviso` (por qué se cayó a regex) y `descartes` (etiquetas rechazadas).
    """
    info = info if info is not None else {}
    senales = set(perfil.get("senales", []) or [])
    evidencias = {s: "declarada en el perfil" for s in senales}

    # Señales derivadas de campos estructurados de HubSpot
    if (perfil.get("cuota_ota_pct") or 0) >= 50:
        senales.add("alta_cuota_ota")
        evidencias["alta_cuota_ota"] = f"cuota_ota_pct={perfil.get('cuota_ota_pct')} (HubSpot)"
    if perfil.get("tipo_alojamiento") in ("apartamentos",):
        senales.add("apartamentos")
        evidencias["apartamentos"] = "tipo_alojamiento (HubSpot)"
    if perfil.get("tipo_alojamiento") in ("B&B", "casa de huéspedes") and (perfil.get("habitaciones") or 0) <= 12:
        senales.add("gestion_propietario")
        evidencias["gestion_propietario"] = "tipo y tamaño del alojamiento (supuesto: ≤12 hab. en B&B/casa de huéspedes suele gestionarlo el propietario)"

    if not transcripcion:
        info["modo"] = "sin_transcripcion"
        return senales, evidencias

    encontradas = None
    if usar_claude:
        try:
            encontradas, info["descartes"] = _senales_claude(transcripcion)
            from redactor import MODELO
            info["modo"] = f"claude:{MODELO}"
        except Exception as e:  # noqa: BLE001 — cualquier fallo (API, SDK, red) debe caer a regex, no romper
            info["aviso"] = f"Extracción con Claude no disponible, se usa regex: {e}"
    if encontradas is None:
        encontradas = _senales_regex(transcripcion)
        info["modo"] = "regex"

    for senal, evid in encontradas.items():
        if senal not in senales:  # lo declarado en el perfil prevalece
            senales.add(senal)
            evidencias[senal] = evid

    # "tiene_web_propia" anula "sin_web" si aparecen las dos (prevalece lo que dice el cliente)
    if "tiene_web_propia" in senales and "sin_web" in senales:
        senales.discard("sin_web")

    return senales, evidencias
