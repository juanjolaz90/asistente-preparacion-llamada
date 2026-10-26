#!/usr/bin/env python3
"""
Punto de entrada del prototipo.

Uso:
  python recomendar.py casos/caso_01_italia_bb.json             # modo plantilla (sin IA)
  python recomendar.py casos/caso_01_italia_bb.json --claude    # señales y redacción con Claude (requiere ANTHROPIC_API_KEY)
  python recomendar.py casos/caso_01_italia_bb.json --json      # salida JSON completa (para n8n / HubSpot card)

Flujo: perfil + transcripción → señales → reglas (determinista) → redactor → guardrail → salida.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from senales import extraer_senales          # noqa: E402
from reglas import cargar_catalogo, evaluar  # noqa: E402
from redactor import construir_contexto, redactar_plantilla, redactar_claude  # noqa: E402
from guardrail import comprobar              # noqa: E402


def procesar(caso: dict, usar_claude: bool = False) -> dict:
    cat = cargar_catalogo()
    perfil = caso["perfil"]
    transcripcion = caso.get("transcripcion")

    info_senales = {}
    senales, evidencias = extraer_senales(perfil, transcripcion, usar_claude=usar_claude, info=info_senales)
    resultado = evaluar(perfil, senales, cat)

    salida = {
        "caso": caso.get("nombre", "sin nombre"),
        "senales": {s: evidencias.get(s, "") for s in sorted(senales)},
        "senales_extraccion": info_senales,
        "reglas": resultado,
        "redaccion": None,
        "guardrail": {"violaciones": [], "fallback_plantilla": False},
    }

    if resultado["estado"] != "ok":
        # error de input o sin recomendación: no se redacta nada
        return salida

    if usar_claude:
        try:
            contexto = construir_contexto(perfil, resultado, senales, evidencias, transcripcion)
            red = redactar_claude(contexto)
        except RuntimeError as e:
            salida["guardrail"]["error_llm"] = str(e)
            red = redactar_plantilla(perfil, resultado)
            salida["guardrail"]["fallback_plantilla"] = True
    else:
        red = redactar_plantilla(perfil, resultado)

    viol = comprobar(red, resultado, cat, perfil, senales)
    if viol and red.get("_modo", "").startswith("claude"):
        # Bloqueamos la redacción del modelo y caemos a plantilla
        salida["guardrail"]["violaciones"] = viol
        salida["guardrail"]["fallback_plantilla"] = True
        salida["guardrail"]["redaccion_bloqueada"] = red
        red = redactar_plantilla(perfil, resultado)
        viol = comprobar(red, resultado, cat, perfil, senales)
    salida["guardrail"]["violaciones_finales"] = viol
    salida["redaccion"] = red
    return salida


def imprimir(salida: dict):
    r = salida["reglas"]
    print("=" * 78)
    print(f"CASE: {salida['caso']}")
    print("=" * 78)
    ext = salida.get("senales_extraccion", {})
    print(f"Signals ({ext.get('modo', '?')}):", ", ".join(salida["senales"]) or "none")
    if ext.get("aviso"):
        print("  !", ext["aviso"])
    for d in ext.get("descartes", []):
        print("  discarded:", d)
    if r["estado"] != "ok":
        print(f"\nSTATUS: {r['estado'].upper()}")
        for e in r.get("errores", []) + r.get("avisos", []):
            print("  -", e)
        return
    if r["escalar"]:
        print("\n>>> ESCALATE TO A PERSON")
        for e in r["escalar"]:
            print(f"  - [{e['motivo']}] {e['detalle']}")
    print(f"\nPlan: {r['plan_destino']} — {r['precio_plan']['detalle']}")
    if r.get("segmento"):
        s = r["segmento"]
        print(f"Segment {s['codigo']} ({s['nombre']}), delta {s['delta_precio_pct']}%. Tone: {s['tono']}")
    print("\nRecommended modules:")
    for m in r["modulos_recomendados"] or []:
        print(f"  ✓ {m['nombre']} ({m['precio']}) — {m['motivo']} [{m['regla']}]")
    if not r["modulos_recomendados"]:
        print("  (none)")
    print("NOT eligible modules:")
    for m in r["modulos_no_elegibles"]:
        print(f"  ✗ {m['nombre']} — {m['motivo']} [{m['regla']}]")
    if r["avisos"]:
        print("Warnings:")
        for a in r["avisos"]:
            print("  !", a)

    red = salida["redaccion"]
    print(f"\n--- Writing (mode: {red.get('_modo')}) ---")
    if red.get("escalar_a_persona"):
        print("ESCALATE:", "; ".join(red["escalar_a_persona"]))
    print("Summary:", red.get("resumen_cliente"))
    print("Pitch:", red.get("pitch"))
    for o in red.get("objeciones", []):
        print(f"  Objection: {o.get('objecion')}\n  Answer: {o.get('respuesta')}")
    if red.get("no_decir"):
        print("Do not say:", "; ".join(red["no_decir"]))
    g = salida["guardrail"]
    if g.get("violaciones") or g.get("error_llm"):
        print("\nGUARDRAIL:", g.get("violaciones") or g.get("error_llm"), "→ template used")
    print("\nSources:")
    for f in r["fuentes"]:
        print(f"  [{f['regla']}] {f['afirmacion']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("caso", help="path to the case JSON")
    ap.add_argument("--claude", action="store_true", help="extract signals and write with Claude")
    ap.add_argument("--json", action="store_true", help="print the full JSON")
    args = ap.parse_args()

    try:
        caso = json.load(open(args.caso, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Could not read the case: {e}", file=sys.stderr)
        sys.exit(1)

    salida = procesar(caso, usar_claude=args.claude)
    if args.json:
        print(json.dumps(salida, ensure_ascii=False, indent=2))
    else:
        imprimir(salida)


if __name__ == "__main__":
    main()
