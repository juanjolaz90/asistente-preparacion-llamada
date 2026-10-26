"""
Motor de reglas determinista. Aquí NO hay IA.

Entrada: perfil (campos tipo HubSpot) + señales extraídas.
Salida: dict con plan destino, precio calculado, módulos (recomendados / elegibles
sin señal / no elegibles con motivo), motivos de escalado y la regla del catálogo
que justifica cada afirmación.

Todo lo que sea un DATO (precio, elegibilidad, descuento, segmento) sale de aquí.
El LLM, después, solo redacta alrededor de este resultado.
"""

import json
from pathlib import Path

CATALOGO_PATH = Path(__file__).parent / "catalogo.json"


def cargar_catalogo(path=CATALOGO_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def precio_plan(cat: dict, plan: str, habitaciones: int) -> tuple[int, str]:
    p = cat["planes"][plan]
    extra = max(0, (habitaciones or 0) - p["habitaciones_incluidas"])
    total = p["precio_base"] + extra * p["precio_hab_extra"]
    detalle = f"€{p['precio_base']} up to {p['habitaciones_incluidas']} rooms"
    if extra:
        detalle += f" + {extra} × €{p['precio_hab_extra']} = €{total}/month"
    else:
        detalle += f" = €{total}/month"
    return total, detalle


def _validar_perfil(perfil: dict, cat: dict) -> list[str]:
    errores = []
    if perfil.get("pais") not in cat["mercados"]:
        errores.append(f"Country '{perfil.get('pais')}' is not an Amenitiz market ({', '.join(cat['mercados'])}).")
    if not isinstance(perfil.get("habitaciones"), int) or perfil["habitaciones"] <= 0:
        errores.append("'habitaciones' (rooms) is missing or is not a positive integer.")
    if perfil.get("caso") not in ("migracion", "nuevo_negocio"):
        errores.append("'caso' must be 'migracion' or 'nuevo_negocio'.")
    return errores


def evaluar(perfil: dict, senales: set, cat: dict | None = None) -> dict:
    cat = cat or cargar_catalogo()
    errores = _validar_perfil(perfil, cat)
    if errores:
        return {"estado": "error_input", "errores": errores}

    pais = perfil["pais"]
    hab = perfil["habitaciones"]
    caso = perfil["caso"]
    out = {
        "estado": "ok",
        "caso": caso,
        "plan_destino": None,
        "precio_plan": None,
        "modulos_recomendados": [],
        "modulos_elegibles_sin_senal": [],
        "modulos_no_elegibles": [],
        "escalar": [],
        "fuentes": [],
        "avisos": [],
    }

    def fuente(id_regla, texto):
        out["fuentes"].append({"regla": id_regla, "afirmacion": texto})

    # ---------- 1. Plan destino ----------
    plan_actual = perfil.get("plan_actual")
    modulos_forzados = []
    if caso == "migracion":
        mapa = cat["migracion_legacy"]
        if plan_actual not in ("Presence", "Sales Pro", "Ultimate"):
            out["estado"] = "sin_recomendacion"
            if plan_actual:
                out["avisos"].append(f"Current plan '{plan_actual}' is not a legacy plan: there is no migration to prepare.")
            else:
                out["avisos"].append("The current plan is missing. For a migration it has to be Presence, Sales Pro or Ultimate; if this is new business, change the case.")
            return out
        destino = mapa[plan_actual]["destino"]
        if destino is None:
            out["estado"] = "sin_recomendacion"
            out["avisos"].append(mapa[plan_actual]["nota"])
            fuente(mapa["id_regla"], "Presence does not migrate")
            return out
        out["plan_destino"] = destino
        modulos_forzados = mapa[plan_actual]["modulos"]
        fuente(mapa["id_regla"], f"{plan_actual} → {destino}" + (f" + {', '.join(modulos_forzados)}" if modulos_forzados else ""))
        fuente(mapa["id_regla"], "Advanced is a later upsell, not the default destination")
    else:
        # Nuevo negocio: Core por defecto. Advanced solo si hay una razón concreta
        # (Revenue Manager exige Advanced; o sin web + ≥8 hab. donde Advanced sale a cuenta).
        necesita_rm = hab >= cat["modulos"]["revenue_manager"]["min_habitaciones"] and (
            senales & set(cat["modulos"]["revenue_manager"]["senales_encaje"])
        )
        if necesita_rm:
            out["plan_destino"] = "Advanced"
            fuente("MOD-RM", "Revenue Manager only on Advanced and ≥ 8 rooms → the recommended plan is Advanced")
        else:
            out["plan_destino"] = "Core"
            fuente("PLAN-CORE", "New business with no need for Revenue Manager → Core")

    plan = out["plan_destino"]
    total, detalle = precio_plan(cat, plan, hab)
    out["precio_plan"] = {"plan": plan, "total_mes": total, "detalle": detalle}
    fuente(cat["planes"][plan]["id_regla"], f"{plan} price: {detalle}")

    incluidos = set(cat["planes"][plan].get("modulos_incluidos", []))

    # ---------- 2. Módulos ----------
    for mid, m in cat["modulos"].items():
        item = {"id": mid, "nombre": m["nombre"], "precio": m["precio"], "regla": m["id_regla"], "motivo": ""}

        if mid in incluidos:
            item["motivo"] = f"Included in {plan}"
            item["precio"] = "included"
            out["modulos_recomendados"].append(item)
            fuente(m["id_regla"], f"{m['nombre']} included in {plan}")
            if senales & set(m.get("senales_contra", [])):
                # Va incluido en el plan, pero el cliente dice que no lo necesita (p. ej. ya tiene web)
                out["avisos"].append(f"{m['nombre']}: included in {plan}, but the customer says they do not need it. Do not lean the pitch on it.")
            continue

        # Elegibilidad (dura)
        if pais not in m["paises"]:
            item["motivo"] = f"Not available in {pais} ({', '.join(m['paises'])} only)"
            out["modulos_no_elegibles"].append(item)
            if senales & set(m["senales_encaje"]):
                out["escalar"].append({"motivo": "modulo_no_disponible", "detalle": f"{m['nombre']} fits the signals but is not available in {pais}. Do not promise a date."})
                fuente(m["id_regla"], f"{m['nombre']}: fits but not available in {pais}")
            continue
        if plan not in m["planes_permitidos"]:
            item["motivo"] = f"{', '.join(m['planes_permitidos'])} only; the target plan is {plan}"
            out["modulos_no_elegibles"].append(item)
            if senales & set(m["senales_encaje"]):
                momento = "do not mix it into the migration" if caso == "migracion" else "do not mix it into this proposal"
                out["avisos"].append(f"{m['nombre']} fits the signals but requires {', '.join(m['planes_permitidos'])}. Possible later upsell; {momento}.")
                fuente(m["id_regla"], f"{m['nombre']} requires {', '.join(m['planes_permitidos'])}")
            continue
        if hab < m["min_habitaciones"]:
            item["motivo"] = f"Minimum {m['min_habitaciones']} rooms (has {hab})"
            out["modulos_no_elegibles"].append(item)
            continue
        if m.get("requiere_cerradura_compatible"):
            cerr = perfil.get("cerradura")
            if cerr not in m["cerraduras_compatibles"]:
                item["motivo"] = f"Requires a compatible lock ({', '.join(m['cerraduras_compatibles'])}); current lock: {cerr or 'unknown'}"
                out["modulos_no_elegibles"].append(item)
                if senales & set(m["senales_encaje"]):
                    out["avisos"].append("Smart Locks fits; ask which lock they have before proposing it.")
                continue
        if m.get("early_access") and m["early_access"]["plazas_restantes"] <= 0:
            item["motivo"] = "Early access with no places left"
            out["modulos_no_elegibles"].append(item)
            if senales & set(m["senales_encaje"]):
                out["escalar"].append({"motivo": "sin_plazas", "detalle": f"{m['nombre']} fits but there are no early-access places left."})
            continue

        # Encaje (señales)
        encaja = bool(senales & set(m["senales_encaje"])) or mid in modulos_forzados
        contra = bool(senales & set(m.get("senales_contra", [])))
        if mid in modulos_forzados and contra:
            # Caso típico: Ultimate → Core + WB, pero el cliente ya tiene web y no la usa.
            item["motivo"] = "Included in the migration by mapping, but the customer says they already have a website. Do not sell it as value; mention it only as an option."
            out["modulos_elegibles_sin_senal"].append(item)
            out["avisos"].append("Website Builder: comes from the legacy mapping but the customer does not value it. Do not lean the pitch on it.")
            fuente(m["id_regla"], "Website Builder by mapping Ultimate → Core + WB; customer already has a website")
            continue
        if encaja and not contra:
            evid = sorted(senales & set(m["senales_encaje"]))
            item["motivo"] = "Fits by: " + ", ".join(evid) if evid else "Included by migration mapping"
            if m.get("early_access"):
                item["motivo"] += f" · early access: {m['early_access']['plazas_restantes']} places left"
            out["modulos_recomendados"].append(item)
            fuente(m["id_regla"], f"{m['nombre']} ({m['precio']}): {item['motivo']}")
        else:
            item["motivo"] = "Eligible, but no signal justifies it. Do not propose it generically."
            out["modulos_elegibles_sin_senal"].append(item)

    # ---------- 3. Escalado ----------
    esc = cat["escalado"]["motivos"]
    seg = perfil.get("segmento_migracion")
    if caso == "migracion" and seg == "C":
        out["escalar"].append({"motivo": "segmento_c", "detalle": esc["segmento_c"]})
        fuente("SEG-C", esc["segmento_c"])
    if "pide_precio_antiguo" in senales:
        out["escalar"].append({"motivo": "excepcion_precio", "detalle": esc["excepcion_precio"]})
        fuente("ESC", esc["excepcion_precio"])
    if "menciona_competencia" in senales:
        out["escalar"].append({"motivo": "riesgo_churn", "detalle": esc["riesgo_churn"]})
        fuente("ESC", esc["riesgo_churn"])
    for s in senales:
        if s.startswith("pide_descuento:"):
            pct = int(s.split(":")[1])
            maximo = cat["descuentos"]["max_sin_aprobacion_pct"]
            if pct > maximo:
                out["escalar"].append({"motivo": "descuento_superior", "detalle": f"Asks for {pct}%. The rep can offer up to {maximo}% for {cat['descuentos']['condicion']}. Above that: {cat['descuentos']['aprobador_excepciones']}."})
                fuente("DESC-10", f"Maximum discount without approval: {maximo}% for {cat['descuentos']['condicion']}")
            else:
                out["avisos"].append(f"Asks for {pct}%: within the rep's limit, only with upfront annual payment.")
                fuente("DESC-10", f"Discount {pct}% ≤ {maximo}% allowed with annual payment")

    # Contexto del segmento para el redactor
    if caso == "migracion" and seg in cat["segmentos_migracion"]:
        s = cat["segmentos_migracion"][seg]
        out["segmento"] = {"codigo": seg, "nombre": s["nombre"], "enfoque": s["enfoque"], "tono": s["tono"], "delta_precio_pct": perfil.get("delta_precio_pct")}
        fuente(s["id_regla"], f"Segment {seg} ({s['nombre']}): {s['enfoque']}")

    # Si no hay nada que recomendar más allá del plan, decirlo
    if not out["modulos_recomendados"] and caso == "nuevo_negocio":
        out["avisos"].append("No module fits the available signals. Recommend the plan only and do more discovery.")

    return out


if __name__ == "__main__":
    import sys
    from senales import extraer_senales
    caso = json.load(open(sys.argv[1], encoding="utf-8"))
    sen, _ = extraer_senales(caso["perfil"], caso.get("transcripcion"))
    print(json.dumps(evaluar(caso["perfil"], sen), ensure_ascii=False, indent=2))
