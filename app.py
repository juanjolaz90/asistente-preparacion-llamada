#!/usr/bin/env python3
"""
Demo en Streamlit del asistente de preparación de llamada.

Uso:
  streamlit run app.py

Funciona sin API key (señales por palabras clave + redacción por plantilla) y con ella
(señales y redacción con Claude). La clave se lee del entorno o del archivo .env.
La lógica es la misma que la del CLI: llama a recomendar.procesar().
"""

import copy
import json
import os
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

import redactor  # noqa: E402,F401 — importarlo carga el .env
from recomendar import procesar  # noqa: E402
from reglas import cargar_catalogo  # noqa: E402

BASE = Path(__file__).parent
CASOS_DIR = BASE / "casos"
CAT = cargar_catalogo()

TIPOS = ["hotel", "B&B", "casa de huéspedes", "apartamentos", "hostel"]
IDIOMAS = ["es", "fr", "it", "pt", "en"]
PLANES_LEGACY = ["(ninguno)", "Presence", "Sales Pro", "Ultimate"]
SEGMENTOS = ["(sin segmento)", "A", "B", "C"]
HEALTH = ["(sin dato)", "alto", "medio", "bajo"]
CERRADURAS = ["(ninguna / no se sabe)"] + list(CAT["modulos"]["smart_locks"].get("cerraduras_compatibles", [])) + ["Otra"]
VACIO = "— Perfil en blanco —"

st.set_page_config(page_title="Preparación de llamada · Amenitiz", page_icon="📞", layout="wide")


# ------------------------------------------------------------------ datos
@st.cache_data
def cargar_casos() -> dict:
    casos = {}
    for f in sorted(CASOS_DIR.glob("*.json")):
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
            casos[c.get("nombre", f.stem)] = c
        except (OSError, json.JSONDecodeError) as e:
            st.warning(f"No se pudo leer {f.name}: {e}")
    return casos


def _idx(opciones: list, valor, defecto: int = 0) -> int:
    return opciones.index(valor) if valor in opciones else defecto


def hay_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ------------------------------------------------------------------ barra lateral
casos = cargar_casos()
with st.sidebar:
    st.header("📞 Preparación de llamada")
    nombre_caso = st.selectbox("Caso de partida", [VACIO] + list(casos), index=1 if casos else 0)
    st.caption("Carga un caso de prueba y edítalo, o empieza en blanco.")

    st.divider()
    con_key = hay_api_key()
    usar_claude = st.toggle("Usar Claude (señales + redacción)", value=con_key, disabled=not con_key)
    if con_key:
        st.caption(f"Modelo: `{redactor.MODELO}`. Si Claude falla o el guardrail bloquea, se usa la plantilla y se avisa.")
    else:
        st.caption("Sin `ANTHROPIC_API_KEY` (ni en el entorno ni en `.env`): modo sin IA, con palabras clave y plantilla.")

    st.divider()
    st.caption("**Principio**: lo que es un dato (precio, elegibilidad, descuento, escalado) sale de las reglas y el catálogo. "
               "Claude solo redacta, y un guardrail revisa el texto antes de enseñarlo.")

# Al cambiar de caso, el resultado anterior deja de valer: se borra para no enseñarlo con otro perfil
if st.session_state.get("caso_mostrado") != nombre_caso:
    st.session_state.pop("salida", None)
    st.session_state["caso_mostrado"] = nombre_caso

caso_base = copy.deepcopy(casos.get(nombre_caso, {"perfil": {}, "transcripcion": ""}))
pb = caso_base.get("perfil", {})
k = f"{nombre_caso}::"  # prefijo de claves: al cambiar de caso, el formulario se recarga con sus valores

# ------------------------------------------------------------------ formulario
st.title("Asistente de preparación de llamada")
if caso_base.get("esperado"):
    with st.expander("Qué debería salir en este caso (para la evaluación)"):
        st.write(caso_base["esperado"])

with st.form("perfil"):
    st.subheader("Perfil del hotel (campos HubSpot)")
    c1, c2, c3, c4 = st.columns(4)
    tipo = c1.selectbox("Tipo de alojamiento", TIPOS, index=_idx(TIPOS, pb.get("tipo_alojamiento")), key=k + "tipo")
    hab = c2.number_input("Habitaciones (0 = sin dato)", min_value=0, max_value=500, step=1,
                          value=int(pb["habitaciones"]) if isinstance(pb.get("habitaciones"), int) else 0, key=k + "hab")
    pais = c3.text_input("País (ES / FR / IT / PT)", value=pb.get("pais", "ES"), key=k + "pais",
                         help="Texto libre a propósito: un país fuera de mercado debe dar error controlado.")
    idioma = c4.selectbox("Idioma del comercial", IDIOMAS, index=_idx(IDIOMAS, pb.get("idioma_comercial", "es")), key=k + "idioma")

    c1, c2, c3, c4 = st.columns(4)
    tipo_caso = c1.radio("Caso", ["migracion", "nuevo_negocio"], index=_idx(["migracion", "nuevo_negocio"], pb.get("caso")),
                         horizontal=True, key=k + "caso", format_func=lambda x: "Migración" if x == "migracion" else "Nuevo negocio")
    plan_actual = c2.selectbox("Plan actual (legacy)", PLANES_LEGACY, index=_idx(PLANES_LEGACY, pb.get("plan_actual")), key=k + "plan")
    segmento = c3.selectbox("Segmento de migración", SEGMENTOS, index=_idx(SEGMENTOS, pb.get("segmento_migracion")), key=k + "seg")
    health = c4.selectbox("Health score", HEALTH, index=_idx(HEALTH, pb.get("health_score")), key=k + "health")

    c1, c2, c3, c4 = st.columns(4)
    delta = c1.number_input("Subida de precio (%)", min_value=0, max_value=500, step=1, value=int(pb.get("delta_precio_pct") or 0), key=k + "delta")
    mrr = c2.number_input("MRR actual (€/mes, 0 = sin dato)", min_value=0, max_value=10000, step=1, value=int(pb.get("mrr_actual") or 0), key=k + "mrr")
    antig = c3.number_input("Antigüedad (meses)", min_value=0, max_value=600, step=1, value=int(pb.get("antiguedad_meses") or 0), key=k + "antig")
    ota = c4.number_input("Cuota de OTA (%)", min_value=0, max_value=100, step=1, value=int(pb.get("cuota_ota_pct") or 0), key=k + "ota")

    c1, c2 = st.columns([1, 3])
    cerr_def = pb.get("cerradura")
    cerr = c1.selectbox("Cerradura", CERRADURAS, index=_idx(CERRADURAS, cerr_def, defecto=(len(CERRADURAS) - 1 if cerr_def else 0)), key=k + "cerr")
    cerr_otra = c2.text_input("Si es otra, ¿cuál?", value=cerr_def if cerr_def and cerr_def not in CERRADURAS else "", key=k + "cerr_otra")

    st.subheader("Fragmento de la llamada (opcional)")
    transcripcion = st.text_area("Transcripción anonimizada (ES / FR / IT / PT / EN)", value=caso_base.get("transcripcion") or "",
                                 height=160, key=k + "trans")

    enviado = st.form_submit_button("Preparar llamada", type="primary", width="stretch")


def construir_perfil() -> dict:
    """Parte del perfil del caso (conserva campos extra como owner o competidor) y aplica lo editado."""
    p = copy.deepcopy(pb)
    p.update({
        "tipo_alojamiento": tipo,
        "pais": pais.strip().upper(),
        "idioma_comercial": idioma,
        "caso": tipo_caso,
        "plan_actual": None if plan_actual == PLANES_LEGACY[0] else plan_actual,
        "segmento_migracion": None if segmento == SEGMENTOS[0] else segmento,
        "health_score": None if health == HEALTH[0] else health,
        "delta_precio_pct": int(delta),
        "antiguedad_meses": int(antig),
        "cuota_ota_pct": int(ota),
        "cerradura": (cerr_otra.strip() or None) if cerr == "Otra" else (None if cerr == CERRADURAS[0] else cerr),
    })
    if hab > 0:
        p["habitaciones"] = int(hab)
    else:
        p.pop("habitaciones", None)
    if mrr > 0:
        p["mrr_actual"] = int(mrr)
    else:
        p.pop("mrr_actual", None)
    return p


if enviado:
    caso = {"nombre": nombre_caso if nombre_caso != VACIO else "Perfil manual",
            "perfil": construir_perfil(), "transcripcion": transcripcion.strip() or None}
    with st.spinner("Preparando la llamada…" + (" (Claude: señales y redacción)" if usar_claude else "")):
        try:
            st.session_state["salida"] = procesar(caso, usar_claude=usar_claude)
            st.session_state["entrada"] = caso
        except Exception as e:  # noqa: BLE001 — en la demo, nunca una traza en pantalla
            st.session_state.pop("salida", None)
            st.error(f"No se pudo procesar el caso: {e!r}")

# ------------------------------------------------------------------ resultado
salida = st.session_state.get("salida")
if not salida:
    st.info("Rellena o carga un perfil y pulsa **Preparar llamada**.")
    st.stop()

st.divider()
r = salida["reglas"]
red = salida.get("redaccion") or {}
g = salida.get("guardrail", {})
ext = salida.get("senales_extraccion", {})

# 1. Estados que paran la herramienta
if r["estado"] == "error_input":
    st.error("**Datos de entrada no válidos. No se genera recomendación.**\n\n" + "\n".join(f"- {e}" for e in r.get("errores", [])))
    st.stop()
if r["estado"] == "sin_recomendacion":
    st.warning("**Sin recomendación.**\n\n" + "\n".join(f"- {a}" for a in r.get("avisos", [])))
    for f in r.get("fuentes", []):
        st.caption(f"[{f['regla']}] {f['afirmacion']}")
    st.stop()

# 2. Escalado en rojo, arriba
if r["escalar"]:
    st.error("### ⛔ ESCALAR A PERSONA\n" + "\n".join(f"- **{e['motivo']}**: {e['detalle']}" for e in r["escalar"]))
else:
    st.success("Sin motivos de escalado: el comercial puede llevar la llamada.")

# Avisos de cómo se ha generado
if g.get("error_llm"):
    st.warning(f"Claude no estaba disponible y se usó la plantilla: {g['error_llm']}")
elif g.get("fallback_plantilla"):
    st.warning("El guardrail bloqueó la redacción de Claude y se usó la plantilla:\n" + "\n".join(f"- {v}" for v in g.get("violaciones", [])))
if ext.get("aviso"):
    st.warning(ext["aviso"])

# 3. Plan y precio
c1, c2 = st.columns([1, 2])
with c1:
    st.metric(f"Plan {r['plan_destino']}", f"{r['precio_plan']['total_mes']} €/mes")
    st.caption(r["precio_plan"]["detalle"] + f"  [{'PLAN-ADV' if r['plan_destino'] == 'Advanced' else 'PLAN-CORE'}]")
with c2:
    if r.get("segmento"):
        s = r["segmento"]
        st.markdown(f"**Segmento {s['codigo']} · {s['nombre']}**: subida de {s.get('delta_precio_pct')} %  \n"
                    f"Enfoque: {s['enfoque']}  \nTono: {s['tono']}")
    for a in r.get("avisos", []):
        st.info(a)

# 4. Módulos
st.subheader("Módulos")
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("**✅ Recomendados**")
    for m in r["modulos_recomendados"] or []:
        st.markdown(f"**{m['nombre']}** · {m['precio']}  \n{m['motivo']} `[{m['regla']}]`")
    if not r["modulos_recomendados"]:
        st.caption("Ninguno")
with c2:
    st.markdown("**➖ Elegibles sin señal** (no proponer de forma genérica)")
    for m in r["modulos_elegibles_sin_senal"] or []:
        st.markdown(f"{m['nombre']} · {m['precio']}  \n{m['motivo']} `[{m['regla']}]`")
    if not r["modulos_elegibles_sin_senal"]:
        st.caption("Ninguno")
with c3:
    st.markdown("**❌ No elegibles**")
    for m in r["modulos_no_elegibles"] or []:
        st.markdown(f"{m['nombre']}  \n{m['motivo']} `[{m['regla']}]`")
    if not r["modulos_no_elegibles"]:
        st.caption("Ninguno")

# 5. Redacción
modo = red.get("_modo", "?")
st.subheader("Pitch")
st.caption(f"Redacción: `{modo}` · Señales: `{ext.get('modo', '?')}`")
if red.get("resumen_cliente"):
    st.markdown(f"_{red['resumen_cliente']}_")
st.markdown(red.get("pitch", ""))

c1, c2 = st.columns(2)
with c1:
    st.subheader("Objeciones")
    for o in red.get("objeciones", []):
        st.markdown(f"**«{o.get('objecion', '')}»**  \n{o.get('respuesta', '')}")
with c2:
    st.subheader("🚫 No decir")
    for n in red.get("no_decir", []):
        st.markdown(f"- {n}")

# 6. Señales y fuentes
c1, c2 = st.columns(2)
with c1:
    with st.expander(f"Señales detectadas ({len(salida['senales'])})", expanded=False):
        for s, evid in salida["senales"].items():
            st.markdown(f"- `{s}` ← {evid}")
        for d in ext.get("descartes", []):
            st.caption(f"Descartada: {d}")
with c2:
    with st.expander(f"Fuentes: regla del catálogo detrás de cada afirmación ({len(r['fuentes'])})", expanded=True):
        # Tabla en markdown y no st.dataframe: así no depende de pandas (en este Windows, el
        # Control de aplicaciones bloquea una DLL de pandas y st.dataframe rompe la página)
        filas = ["| Regla | Afirmación |", "|---|---|"]
        filas += [f"| `{f['regla']}` | {str(f['afirmacion']).replace('|', '/')} |" for f in r["fuentes"]]
        st.markdown("\n".join(filas))

with st.expander("JSON completo (lo que recibiría n8n / la card de HubSpot)"):
    st.json({"entrada": st.session_state.get("entrada"), "salida": salida}, expanded=False)
