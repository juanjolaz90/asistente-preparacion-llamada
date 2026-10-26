"""
Capa generativa: redacta pitch y objeciones a partir del resultado de las reglas.

Dos modos:
  - "plantilla": sin IA. Construye el texto con plantillas a partir del resultado.
    Sirve para probar el motor sin API key y como fallback si el guardrail bloquea.
  - "claude": llama a la API de Anthropic. Requiere ANTHROPIC_API_KEY en el entorno
    o en un archivo .env junto a este script (el entorno tiene prioridad).

En ambos casos, la salida pasa por el guardrail (guardrail.py) antes de devolverse.
"""

import json
import os
from pathlib import Path

PROMPT_PATH = Path(__file__).parent / "prompt_sistema.md"
ENV_PATH = Path(__file__).parent / ".env"


def _cargar_env(ruta: Path = ENV_PATH) -> None:
    """Carga variables CLAVE=valor del .env sin pisar las que ya estén en el entorno.
    Sin dependencias (no usa python-dotenv). Si el archivo no existe o no se puede leer, no hace nada."""
    try:
        lineas = ruta.read_text(encoding="utf-8-sig").splitlines()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return
    for linea in lineas:
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        if linea.startswith("export "):
            linea = linea[len("export "):]
        clave, valor = linea.split("=", 1)
        clave, valor = clave.strip(), valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        if clave and not os.environ.get(clave):
            os.environ[clave] = valor


_cargar_env()
MODELO = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")


def _fmt_modulos(lista):
    return [f"- {m['nombre']} ({m['precio']}): {m['motivo']} [{m['regla']}]" for m in lista]


def construir_contexto(perfil: dict, resultado: dict, senales: set, evidencias: dict, transcripcion: str | None) -> str:
    bloques = []
    bloques.append("PERFIL_HUBSPOT:\n" + json.dumps({k: v for k, v in perfil.items() if k != "senales"}, ensure_ascii=False, indent=2))
    bloques.append("SEÑALES_DETECTADAS:\n" + "\n".join(f"- {s}: {evidencias.get(s, '')}" for s in sorted(senales)))
    r = resultado
    bloques.append(
        "RESULTADO_REGLAS:\n"
        f"plan_recomendado: {r['plan_destino']} — {r['precio_plan']['detalle']} [{'PLAN-ADV' if r['plan_destino']=='Advanced' else 'PLAN-CORE'}]\n"
        + ("segmento: " + json.dumps(r.get("segmento"), ensure_ascii=False) + "\n" if r.get("segmento") else "")
        + "modulos_recomendados:\n" + ("\n".join(_fmt_modulos(r["modulos_recomendados"])) or "- ninguno") + "\n"
        + "modulos_elegibles_sin_senal (no proponer de forma genérica):\n" + ("\n".join(_fmt_modulos(r["modulos_elegibles_sin_senal"])) or "- ninguno") + "\n"
        + "modulos_NO_elegibles:\n" + ("\n".join(_fmt_modulos(r["modulos_no_elegibles"])) or "- ninguno") + "\n"
        + "escalar:\n" + ("\n".join(f"- {e['motivo']}: {e['detalle']}" for e in r["escalar"]) or "- no") + "\n"
        + "avisos:\n" + ("\n".join(f"- {a}" for a in r["avisos"]) or "- ninguno")
    )
    if transcripcion:
        bloques.append("TRANSCRIPCION (anonimizada):\n" + transcripcion)
    bloques.append(f"idioma_comercial: {perfil.get('idioma_comercial', 'es')}")
    return "\n\n".join(bloques)


# ---------------------------------------------------------------- modo plantilla
def redactar_plantilla(perfil: dict, resultado: dict) -> dict:
    r = resultado
    seg = r.get("segmento")
    esc = [f"{e['motivo']}: {e['detalle']}" for e in r["escalar"]]
    recs = [m for m in r["modulos_recomendados"]]
    plan = r["plan_destino"]
    precio = r["precio_plan"]["detalle"]

    resumen = f"{perfil.get('tipo_alojamiento','Property')} with {perfil.get('habitaciones')} rooms in {perfil.get('pais')}."
    if seg:
        resumen += f" Migration, segment {seg['codigo']} ({seg['nombre']}), price increase of {seg.get('delta_precio_pct')}%."

    frases = []
    if r["caso"] == "migracion":
        frases.append(f"Your plan moves to {plan} [{'PLAN-ADV' if plan=='Advanced' else 'PLAN-CORE'}]; the price is {precio}.")
    else:
        frases.append(f"For a property like yours, the plan that fits is {plan}: {precio} [{'PLAN-ADV' if plan=='Advanced' else 'PLAN-CORE'}].")
    for m in recs:
        frases.append(f"{m['nombre']} ({m['precio']}): {m['motivo']} [{m['regla']}].")
    motivos_esc = {e["motivo"] for e in r["escalar"]}
    if motivos_esc & {"descuento_superior", "excepcion_precio", "segmento_c", "riesgo_churn"}:
        frases.append("On price and conditions I can't close anything today: the Head of CS reviews it and we call you back with an answer [ESC].")
    if "modulo_no_disponible" in motivos_esc:
        no_disp = [m["nombre"] for m in r["modulos_no_elegibles"] if "Not available in" in m["motivo"]]
        if no_disp:
            frases.append(f"If they ask about {', '.join(no_disp)}: it is not available in {perfil.get('pais')} today and we have no date we can promise [{[m['regla'] for m in r['modulos_no_elegibles'] if m['nombre'] in no_disp][0]}].")

    objeciones = []
    if "pide_precio_antiguo" in str(perfil.get("senales", "")) or any(e["motivo"] == "excepcion_precio" for e in r["escalar"]):
        objeciones.append({"objecion": "Can't you keep me on the old price?", "respuesta": "That is not my decision to make. I'll pass your case to the Head of CS and get back to you in [check with CS] days. Meanwhile, let me tell you what you gain with the change [ESC]."})
    if any(e["motivo"] == "descuento_superior" for e in r["escalar"]):
        objeciones.append({"objecion": "I want a bigger discount.", "respuesta": "I can apply up to 10% if you move to annual payment [DESC-10]. Anything above that has to be approved by CS; I'll escalate it today."})
    if not objeciones:
        objeciones.append({"objecion": "I don't need more features, I just pay more.", "respuesta": "I understand. Of what's new, what really matters to you is " + (recs[0]["nombre"] if recs else "the base plan") + "; you don't need to switch on the rest [" + (recs[0]["regla"] if recs else "PLAN-CORE") + "]."})

    no_decir = ["Do not promise modules from the 'not eligible' list."]
    if esc:
        no_decir.append("Do not confirm any price other than the calculated one, or any availability dates.")

    return {
        "escalar_a_persona": esc,
        "resumen_cliente": resumen,
        "pitch": " ".join(frases),
        "objeciones": objeciones[:2],
        "no_decir": no_decir,
        "_modo": "plantilla",
    }


# ---------------------------------------------------------------- modo Claude
def redactar_claude(contexto: str) -> dict:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("Falta el paquete 'anthropic'. Instala con: pip install anthropic") from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("Falta ANTHROPIC_API_KEY en el entorno o en el archivo .env.")

    client = anthropic.Anthropic()
    sistema = PROMPT_PATH.read_text(encoding="utf-8")
    try:
        msg = client.messages.create(
            model=MODELO,
            # Sin temperature: la API actual rechaza los parámetros de muestreo.
            # max_tokens holgado porque el pensamiento adaptativo (activo por defecto) cuenta dentro.
            max_tokens=16000,
            system=sistema,
            messages=[{"role": "user", "content": contexto}],
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"Error de la API de Anthropic: {e}") from e

    texto = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    # Tolerar ```json ... ```
    if texto.startswith("```"):
        texto = texto.strip("`")
        texto = texto[texto.find("{"):texto.rfind("}") + 1]
    try:
        data = json.loads(texto)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"La respuesta del modelo no es JSON válido: {e}\n---\n{texto[:800]}") from e
    data["_modo"] = f"claude:{MODELO}"
    return data
