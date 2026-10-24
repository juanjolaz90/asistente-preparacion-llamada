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
    detalle = f"{p['precio_base']} € hasta {p['habitaciones_incluidas']} hab."
    if extra:
        detalle += f" + {extra} × {p['precio_hab_extra']} € = {total} €/mes"
    else:
        detalle += f" = {total} €/mes"
    return total, detalle


def _validar_perfil(perfil: dict, cat: dict) -> list[str]:
    errores = []
    if perfil.get("pais") not in cat["mercados"]:
        errores.append(f"País '{perfil.get('pais')}' no es un mercado de Amenitiz ({', '.join(cat['mercados'])}).")
    if not isinstance(perfil.get("habitaciones"), int) or perfil["habitaciones"] <= 0:
        errores.append("Falta 'habitaciones' o no es un entero positivo.")
    if perfil.get("caso") not in ("migracion", "nuevo_negocio"):
        errores.append("'caso' debe ser 'migracion' o 'nuevo_negocio'.")
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
            out["avisos"].append(f"Plan actual '{plan_actual}' no es legacy: no hay migración que preparar.")
            return out
        destino = mapa[plan_actual]["destino"]
        if destino is None:
            out["estado"] = "sin_recomendacion"
            out["avisos"].append(mapa[plan_actual]["nota"])
            fuente(mapa["id_regla"], "Presence no migra")
            return out
        out["plan_destino"] = destino
        modulos_forzados = mapa[plan_actual]["modulos"]
        fuente(mapa["id_regla"], f"{plan_actual} → {destino}" + (f" + {', '.join(modulos_forzados)}" if modulos_forzados else ""))
        fuente(mapa["id_regla"], "Advanced es upsell posterior, no destino por defecto")
    else:
        # Nuevo negocio: Core por defecto. Advanced solo si hay una razón concreta
        # (Revenue Manager exige Advanced; o sin web + ≥8 hab. donde Advanced sale a cuenta).
        necesita_rm = hab >= cat["modulos"]["revenue_manager"]["min_habitaciones"] and (
            senales & set(cat["modulos"]["revenue_manager"]["senales_encaje"])
        )
        if necesita_rm:
            out["plan_destino"] = "Advanced"
            fuente("MOD-RM", "Revenue Manager solo en Advanced y ≥ 8 hab. → el plan recomendado es Advanced")
        else:
            out["plan_destino"] = "Core"
            fuente("PLAN-CORE", "Nuevo negocio sin necesidad de Revenue Manager → Core")

    plan = out["plan_destino"]
    total, detalle = precio_plan(cat, plan, hab)
    out["precio_plan"] = {"plan": plan, "total_mes": total, "detalle": detalle}
    fuente(cat["planes"][plan]["id_regla"], f"Precio {plan}: {detalle}")

    incluidos = set(cat["planes"][plan].get("modulos_incluidos", []))

    # ---------- 2. Módulos ----------
    for mid, m in cat["modulos"].items():
        item = {"id": mid, "nombre": m["nombre"], "precio": m["precio"], "regla": m["id_regla"], "motivo": ""}

        if mid in incluidos:
            item["motivo"] = f"Incluido en {plan}"
            item["precio"] = "incluido"
            out["modulos_recomendados"].append(item)
            fuente(m["id_regla"], f"{m['nombre']} incluido en {plan}")
            continue

        # Elegibilidad (dura)
        if pais not in m["paises"]:
            item["motivo"] = f"No disponible en {pais} (solo {', '.join(m['paises'])})"
            out["modulos_no_elegibles"].append(item)
            if senales & set(m["senales_encaje"]):
                out["escalar"].append({"motivo": "modulo_no_disponible", "detalle": f"{m['nombre']} encaja por las señales pero no está disponible en {pais}. No prometer fecha."})
                fuente(m["id_regla"], f"{m['nombre']}: encaja pero no disponible en {pais}")
            continue
        if plan not in m["planes_permitidos"]:
            item["motivo"] = f"Solo en {', '.join(m['planes_permitidos'])}; el plan destino es {plan}"
            out["modulos_no_elegibles"].append(item)
            if senales & set(m["senales_encaje"]):
                out["avisos"].append(f"{m['nombre']} encaja por las señales pero exige {', '.join(m['planes_permitidos'])}. Posible upsell posterior; no mezclarlo en la migración.")
                fuente(m["id_regla"], f"{m['nombre']} exige {', '.join(m['planes_permitidos'])}")
            continue
        if hab < m["min_habitaciones"]:
            item["motivo"] = f"Mínimo {m['min_habitaciones']} habitaciones (tiene {hab})"
            out["modulos_no_elegibles"].append(item)
            continue
        if m.get("requiere_cerradura_compatible"):
            cerr = perfil.get("cerradura")
            if cerr not in m["cerraduras_compatibles"]:
                item["motivo"] = f"Requiere cerradura compatible ({', '.join(m['cerraduras_compatibles'])}); cerradura actual: {cerr or 'desconocida'}"
                out["modulos_no_elegibles"].append(item)
                if senales & set(m["senales_encaje"]):
                    out["avisos"].append("Smart Locks encaja; preguntar qué cerradura tienen antes de proponerlo.")
                continue
        if m.get("early_access") and m["early_access"]["plazas_restantes"] <= 0:
            item["motivo"] = "Early access sin plazas"
            out["modulos_no_elegibles"].append(item)
            if senales & set(m["senales_encaje"]):
                out["escalar"].append({"motivo": "sin_plazas", "detalle": f"{m['nombre']} encaja pero no quedan plazas de early access."})
            continue

        # Encaje (señales)
        encaja = bool(senales & set(m["senales_encaje"])) or mid in modulos_forzados
        contra = bool(senales & set(m.get("senales_contra", [])))
        if mid in modulos_forzados and contra:
            # Caso típico: Ultimate → Core + WB, pero el cliente ya tiene web y no la usa.
            item["motivo"] = "Va incluido en la migración por mapeo, pero el cliente dice que ya tiene web. No venderlo como valor; mencionarlo solo como opción."
            out["modulos_elegibles_sin_senal"].append(item)
            out["avisos"].append("Website Builder: viene por mapeo legacy pero el cliente no lo valora. No apoyar el pitch en él.")
            fuente(m["id_regla"], "Website Builder por mapeo Ultimate → Core + WB; cliente ya tiene web")
            continue
        if encaja and not contra:
            evid = sorted(senales & set(m["senales_encaje"]))
            item["motivo"] = "Encaja por: " + ", ".join(evid) if evid else "Incluido por mapeo de migración"
            if m.get("early_access"):
                item["motivo"] += f" · early access: {m['early_access']['plazas_restantes']} plazas restantes"
            out["modulos_recomendados"].append(item)
            fuente(m["id_regla"], f"{m['nombre']} ({m['precio']}): {item['motivo']}")
        else:
            item["motivo"] = "Elegible, pero sin señal que lo justifique. No proponer de forma genérica."
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
                out["escalar"].append({"motivo": "descuento_superior", "detalle": f"Pide {pct}%. El comercial puede ofrecer hasta {maximo}% por {cat['descuentos']['condicion']}. Por encima: {cat['descuentos']['aprobador_excepciones']}."})
                fuente("DESC-10", f"Descuento máximo sin aprobación: {maximo}% por {cat['descuentos']['condicion']}")
            else:
                out["avisos"].append(f"Pide {pct}%: dentro del margen del comercial, solo si pago anual anticipado.")
                fuente("DESC-10", f"Descuento {pct}% ≤ {maximo}% permitido con pago anual")

    # Contexto del segmento para el redactor
    if caso == "migracion" and seg in cat["segmentos_migracion"]:
        s = cat["segmentos_migracion"][seg]
        out["segmento"] = {"codigo": seg, "nombre": s["nombre"], "enfoque": s["enfoque"], "tono": s["tono"], "delta_precio_pct": perfil.get("delta_precio_pct")}
        fuente(s["id_regla"], f"Segmento {seg} ({s['nombre']}): {s['enfoque']}")

    # Si no hay nada que recomendar más allá del plan, decirlo
    if not out["modulos_recomendados"] and caso == "nuevo_negocio":
        out["avisos"].append("Ningún módulo encaja con las señales disponibles. Recomendar solo el plan y hacer más discovery.")

    return out


if __name__ == "__main__":
    import sys
    from senales import extraer_senales
    caso = json.load(open(sys.argv[1], encoding="utf-8"))
    sen, _ = extraer_senales(caso["perfil"], caso.get("transcripcion"))
    print(json.dumps(evaluar(caso["perfil"], sen), ensure_ascii=False, indent=2))
