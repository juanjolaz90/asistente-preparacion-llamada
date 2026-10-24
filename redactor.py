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

    resumen = f"{perfil.get('tipo_alojamiento','Alojamiento')} de {perfil.get('habitaciones')} hab. en {perfil.get('pais')}."
    if seg:
        resumen += f" Migración, segmento {seg['codigo']} ({seg['nombre']}), subida de {seg.get('delta_precio_pct')}%."

    frases = []
    if r["caso"] == "migracion":
        frases.append(f"Tu plan pasa a {plan} [{'PLAN-ADV' if plan=='Advanced' else 'PLAN-CORE'}]; el precio queda en {precio}.")
    else:
        frases.append(f"Para un alojamiento como el tuyo el plan que encaja es {plan}: {precio} [{'PLAN-ADV' if plan=='Advanced' else 'PLAN-CORE'}].")
    for m in recs:
        frases.append(f"{m['nombre']} ({m['precio']}): {m['motivo']} [{m['regla']}].")
    motivos_esc = {e["motivo"] for e in r["escalar"]}
    if motivos_esc & {"descuento_superior", "excepcion_precio", "segmento_c", "riesgo_churn"}:
        frases.append("Sobre precio y condiciones, hoy no puedo cerrar nada: lo revisa el responsable de CS y te llamamos con una respuesta [ESC].")
    if "modulo_no_disponible" in motivos_esc:
        no_disp = [m["nombre"] for m in r["modulos_no_elegibles"] if "No disponible en" in m["motivo"]]
        if no_disp:
            frases.append(f"Si pregunta por {', '.join(no_disp)}: hoy no está disponible en {perfil.get('pais')} y no tenemos fecha que podamos prometer [{[m['regla'] for m in r['modulos_no_elegibles'] if m['nombre'] in no_disp][0]}].")

    objeciones = []
    if "pide_precio_antiguo" in str(perfil.get("senales", "")) or any(e["motivo"] == "excepcion_precio" for e in r["escalar"]):
        objeciones.append({"objecion": "¿No me puedes dejar con el precio antiguo?", "respuesta": "No está en mi mano decidirlo. Lo paso al responsable de CS con tu caso y te contesto en [consultar con CS] días. Mientras, te cuento lo que ganas con el cambio [ESC]."})
    if any(e["motivo"] == "descuento_superior" for e in r["escalar"]):
        objeciones.append({"objecion": "Quiero un descuento mayor.", "respuesta": "Puedo aplicar hasta un 10% si pasas a pago anual [DESC-10]. Más que eso tiene que aprobarlo CS; te lo escalo hoy."})
    if not objeciones:
        objeciones.append({"objecion": "No necesito más funciones, solo pago más.", "respuesta": "Entiendo. De lo nuevo, lo que te afecta de verdad es " + (recs[0]["nombre"] if recs else "el plan base") + "; el resto no hace falta que lo actives [" + (recs[0]["regla"] if recs else "PLAN-CORE") + "]."})

    no_decir = ["No prometer módulos de la lista de 'no elegibles'."]
    if esc:
        no_decir.append("No confirmar ningún precio distinto del calculado ni fechas de disponibilidad.")

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
