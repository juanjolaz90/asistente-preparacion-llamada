"""
Guardrail de salida. Se ejecuta sobre el texto redactado (por plantilla o por Claude)
antes de enseñárselo al comercial.

Bloquea si:
  - menciona un módulo que no está en la lista de recomendados/incluidos
    (los no elegibles solo pueden aparecer en 'no_decir' o para explicar por qué no),
  - menciona un importe en euros que no coincide con los precios permitidos,
  - hay motivos de escalado y el JSON no los refleja en 'escalar_a_persona'.

Es intencionadamente estricto: preferimos un falso positivo (que caiga al modo
plantilla) a que un comercial lea un precio inventado.

Excepciones acotadas (para no bloquear al citar datos del propio cliente):
  - el precio legacy (`mrr_actual`) se puede citar si va junto al plan actual o a
    palabras como "actual", "ahora pagas", "antes"; "Core a 65 €" sigue bloqueándose,
  - el % de subida (`delta_precio_pct`) se puede citar si va junto a "subida", "aumento"…,
  - el % que pide el cliente (`pide_descuento:N`) se puede citar si va junto a "pide",
    "acepta", "exige"… y no hay un verbo de oferta ("te doy", "te aplico", "puedo hacer"…),
  - el campo `objecion` (lo que dice el cliente) no se revisa como oferta de descuento.
"""

import re

_RE_LEGACY = r"actual|ahora pag|hoy pag|pagas hoy|pagabas|antes|antiguo|legacy|anterior|pasa\w* de|sub\w+ de|cambia\w* de"
# El precio legacy se bloquea solo si se ofrece como precio nuevo: verbo de oferta o nombre del plan nuevo pegado a la cifra
_RE_OFERTA_PRECIO = r"te (lo |la )?(dejo|hago|aplico|mantengo|dejamos|hacemos)|puedo dejarte|se queda en|queda en|quedas en|quedar(te|se) en|por s[oó]lo|\b(core|advanced) (a|por|en)\b"
_RE_SUBIDA = r"sub(e|ida)|aument|increment|hausse|rincar|encarec"
_RE_PETICION = r"\bpid(e|es|en|i[oó])\b|\bpedido\b|solicit|\bexig|\bacept|\bquiere|reclam|condici[oó]n"
_RE_OFERTA = r"te (lo |la )?(doy|hago|aplico|dejo|damos|hacemos|aplicamos|dejamos)|te puedo (dar|hacer|aplicar|dejar)|puedo (dar|hacer|aplicar|dejar)te|ofrezco|ofrecemos|aprobad[oa]"


def _pct_pedido(senales) -> set[int]:
    pedidos = set()
    for s in senales or ():
        if s.startswith("pide_descuento:"):
            try:
                pedidos.add(int(s.split(":", 1)[1]))
            except ValueError:
                pass
    return pedidos


def _precios_permitidos(resultado: dict, cat: dict) -> set[int]:
    permitidos = set()
    pp = resultado.get("precio_plan") or {}
    if pp:
        plan = cat["planes"][pp["plan"]]
        permitidos |= {plan["precio_base"], plan["precio_hab_extra"], pp["total_mes"]}
    modulos_num = []
    for m in resultado.get("modulos_recomendados", []):
        pn = cat["modulos"][m["id"]].get("precio_num")
        if pn:
            permitidos.add(pn)
            modulos_num.append(pn)
    # Sumas legítimas: plan + un módulo recomendado, y plan + todos ("Core con Ami son 148 €")
    if pp and modulos_num:
        for pn in modulos_num:
            permitidos.add(pp["total_mes"] + pn)
        permitidos.add(pp["total_mes"] + sum(modulos_num))
    permitidos.add(cat["descuentos"]["max_sin_aprobacion_pct"])  # el 10% puede mencionarse
    return permitidos


def comprobar(salida: dict, resultado: dict, cat: dict, perfil: dict | None = None, senales=None) -> list[str]:
    """Devuelve lista de violaciones. Vacía = OK.
    `perfil` y `senales` son opcionales: sin ellos no se aplican las excepciones acotadas."""
    violaciones = []
    perfil = perfil or {}
    texto_pitch = " ".join([
        salida.get("resumen_cliente", ""),
        salida.get("pitch", ""),
        " ".join(o.get("respuesta", "") + " " + o.get("objecion", "") for o in salida.get("objeciones", [])),
    ])
    # Para descuentos no se mira lo que dice el cliente (campo 'objecion'), solo lo que dice el comercial
    texto_comercial = " ".join([
        salida.get("resumen_cliente", ""),
        salida.get("pitch", ""),
        " ".join(o.get("respuesta", "") for o in salida.get("objeciones", [])),
    ])
    mrr_legacy = perfil.get("mrr_actual") if perfil.get("caso") == "migracion" else None
    plan_actual = str(perfil.get("plan_actual") or "").lower()
    delta_subida = perfil.get("delta_precio_pct")
    pct_pedidos = _pct_pedido(senales)

    # 1. Módulos no permitidos en pitch/objeciones
    permitidos = {m["nombre"].lower() for m in resultado.get("modulos_recomendados", [])}
    for m in resultado.get("modulos_no_elegibles", []) + resultado.get("modulos_elegibles_sin_senal", []):
        nombre = m["nombre"].lower()
        if nombre in permitidos:
            continue
        # Permitimos mencionarlo solo si va acompañado de una negación cercana ("no está disponible", "no incluye", "no puedo")
        # Palabra completa ("POS" no casa con "posible") y fuera de etiquetas como [MOD-POS].
        # Se revisa lo que dice el comercial, no la objeción: el cliente puede preguntar por cualquier módulo.
        for mt in re.finditer(r"(?<![\w-])" + re.escape(nombre) + r"(?!\w)", texto_comercial.lower()):
            ventana = texto_comercial.lower()[max(0, mt.start() - 80): mt.end() + 80]
            if not re.search(r"\bno\b|non\b|pas\b|não\b|todav[ií]a|a[uú]n no|m[aá]s adelante|no est[aá]|\bnot\b|n't\b|unavailable|only (available )?in spain", ventana):
                violaciones.append(f"Menciona '{m['nombre']}' sin negación y no está entre los recomendados.")
                break

    # 2. Importes en euros
    permitidos_num = _precios_permitidos(resultado, cat)
    for mt in re.finditer(r"(\d{1,4}(?:[.,]\d{1,2})?)\s?(?:€|eur|euros)", texto_pitch, re.I):
        n = mt.group(1).replace(",", ".")
        try:
            val = int(float(n))
        except ValueError:
            continue
        if val not in permitidos_num:
            if mrr_legacy is not None and val == int(mrr_legacy):
                # Precio legacy del cliente: se puede citar (es un dato suyo) salvo que se ofrezca como precio nuevo
                cerca = texto_pitch[max(0, mt.start() - 40): mt.end() + 15].lower()
                if not re.search(_RE_OFERTA_PRECIO, cerca):
                    continue
            violaciones.append(f"Importe {mt.group(0).strip()} no está entre los precios permitidos {sorted(permitidos_num)}.")

    # 3. Porcentajes de descuento por encima del máximo
    for mt in re.finditer(r"(\d{1,2})\s?%", texto_comercial):
        pct = int(mt.group(1))
        ventana = texto_comercial[max(0, mt.start() - 60): mt.end() + 60].lower()
        if any(w in ventana for w in ("descuento", "sconto", "remise", "desconto", "discount")) and pct > cat["descuentos"]["max_sin_aprobacion_pct"]:
            if re.search(r"\bno\b|aprob|escal|responsable|\bnot\b|n't\b|approv|head of cs", ventana):
                continue
            antes = texto_comercial[max(0, mt.start() - 30): mt.start()].lower()
            if delta_subida is not None and pct == int(delta_subida) and re.search(_RE_SUBIDA, antes):
                continue  # es el % de subida de precio, no un descuento
            cerca = texto_comercial[max(0, mt.start() - 60): mt.end() + 20].lower()
            if pct in pct_pedidos and re.search(_RE_PETICION, cerca) and not re.search(_RE_OFERTA, cerca):
                continue  # cita lo que pide el cliente, no lo ofrece
            violaciones.append(f"Ofrece un {pct}% de descuento sin aprobación.")

    # 4. Escalado reflejado
    if resultado.get("escalar") and not salida.get("escalar_a_persona"):
        violaciones.append("Hay motivos de escalado en las reglas pero la salida no los refleja.")

    return violaciones
