#!/usr/bin/env python3
"""
Ejecuta todos los casos de /casos y vuelca:
  - salidas/<caso>.json  → salida completa de cada caso
  - salidas/resumen.md   → tabla: caso · esperado · señales (y cómo se extrajeron) · lo que devolvió (reglas) · guardrail

La columna "qué corregiría" se rellena a mano en evaluacion.md: es un juicio, no un cálculo.

Uso:
  python run_eval.py            # modo plantilla
  python run_eval.py --claude   # con Claude (ANTHROPIC_API_KEY)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from recomendar import procesar  # noqa: E402

BASE = Path(__file__).parent
CASOS = sorted((BASE / "casos").glob("*.json"))
OUT = BASE / "salidas"
OUT.mkdir(exist_ok=True)


def resumen_reglas(s: dict) -> str:
    r = s["reglas"]
    if r["estado"] != "ok":
        return f"**{r['estado']}** — " + "; ".join(r.get("errores", []) + r.get("avisos", []))
    partes = [f"Plan **{r['plan_destino']}** (€{r['precio_plan']['total_mes']}/month)"]
    if r["modulos_recomendados"]:
        partes.append("Recommends: " + ", ".join(m["nombre"] for m in r["modulos_recomendados"]))
    if r["escalar"]:
        partes.append("ESCALATES: " + ", ".join(e["motivo"] for e in r["escalar"]))
    if r["avisos"]:
        partes.append("Warnings: " + " / ".join(r["avisos"]))
    return ". ".join(partes)


def resumen_senales(s: dict) -> str:
    ext = s.get("senales_extraccion", {})
    txt = f"*{ext.get('modo', '?')}*: " + (", ".join(s.get("senales", {})) or "none")
    if ext.get("aviso"):
        txt += f" ⚠ {ext['aviso']}"
    if ext.get("descartes"):
        txt += " · discarded: " + "; ".join(ext["descartes"])
    return txt


def main():
    usar_claude = "--claude" in sys.argv
    filas = []
    for f in CASOS:
        caso = json.load(open(f, encoding="utf-8"))
        try:
            s = procesar(caso, usar_claude=usar_claude)
        except Exception as e:  # noqa: BLE001 — queremos que la eval siga aunque un caso explote
            s = {"caso": caso.get("nombre"), "error_fatal": repr(e)}
        (OUT / (f.stem + ".json")).write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        g = s.get("guardrail", {})
        gtxt = "template fallback: " + ", ".join(g.get("violaciones", [])) if g.get("fallback_plantilla") else "OK"
        if "error_fatal" in s:
            gtxt = "ERROR: " + s["error_fatal"]
        filas.append((caso.get("nombre", f.stem), caso.get("esperado", ""), resumen_senales(s),
                      resumen_reglas(s) if "reglas" in s else "—", gtxt))

    md = ["| Case | Expected | Signals | Returned (rules) | Guardrail |", "|---|---|---|---|---|"]
    for c, e, sn, d, g in filas:
        md.append(f"| {c} | {e} | {sn} | {d} | {g} |")
    (OUT / "resumen.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    print(f"\nOutputs in {OUT}/")


if __name__ == "__main__":
    main()
