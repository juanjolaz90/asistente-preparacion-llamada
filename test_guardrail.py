#!/usr/bin/env python3
"""
Prueba del guardrail sin IA ni dependencias: python test_guardrail.py

- La redacción "mala" (POS en FR, 39 €, 15 % de descuento, Core a 89 €…) tiene que BLOQUEARSE.
- La redacción del caso 04 que antes daba falso positivo (cita el precio legacy de 65 €,
  la subida del 18 % y el 20 % que pide la clienta) tiene que PASAR.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from senales import extraer_senales          # noqa: E402
from reglas import cargar_catalogo, evaluar  # noqa: E402
from guardrail import comprobar              # noqa: E402

CASOS = Path(__file__).parent / "casos"
CAT = cargar_catalogo()


def _preparar(nombre_caso: str):
    caso = json.loads((CASOS / nombre_caso).read_text(encoding="utf-8"))
    perfil = caso["perfil"]
    senales, _ = extraer_senales(perfil, caso.get("transcripcion"))
    return perfil, senales, evaluar(perfil, senales, CAT)


def _salida(pitch: str, respuesta: str = "", objecion: str = "", resumen: str = "") -> dict:
    return {
        "escalar_a_persona": ["motivo"],
        "resumen_cliente": resumen,
        "pitch": pitch,
        "objeciones": [{"objecion": objecion, "respuesta": respuesta}],
        "no_decir": [],
    }


# (caso, descripción, salida, debe_bloquear)
PRUEBAS = [
    # --- redacción mala: debe bloquear
    ("caso_03_francia_restaurante.json", "POS propuesto en Francia",
     _salida("Te recomiendo el POS para tu restaurante, encaja perfecto."), True),
    ("caso_03_francia_restaurante.json", "POS a 39 € en Francia",
     _salida("El terminal te sale a 39 €/mes."), True),
    ("caso_04_descuento_20.json", "Ofrece 15 % de descuento",
     _salida("Te hago un 15% de descuento si firmas hoy."), True),
    ("caso_04_descuento_20.json", "Core a 89 €",
     _salida("Tu plan pasa a Core a 89 €/mes."), True),
    ("caso_04_descuento_20.json", "Core al precio legacy (65 €)",
     _salida("Te dejo Core a 65 €/mes."), True),
    ("caso_04_descuento_20.json", "Ofrece el 20 % que pide",
     _salida("Como me pides, te doy un 20% de descuento."), True),
    ("caso_04_descuento_20.json", "Ofrece el 18 % (= subida) como descuento",
     _salida("Te aplico un 18% de descuento para compensar."), True),
    ("caso_01_italia_bb.json", "Sin escalado reflejado",
     {**_salida("Tu plan pasa a Core a 79 €/mes."), "escalar_a_persona": []}, True),

    # --- falsos positivos ya corregidos: deben pasar
    ("caso_03_francia_restaurante.json", "Caso 03: 'posible' no es 'POS'",
     _salida("Paso tu caso al equipo para que te diga qué es posible de verdad [MOD-POS], y mientras vemos el plan Core [PLAN-CORE]."), False),
    ("caso_04_descuento_20.json", "Caso 04: legacy, subida y % pedido",
     _salida(
         pitch="Tu plan pasaría a Core, a 79 €/mes hasta 10 habitaciones [PLAN-CORE]. Ami tiene un precio desde 69 €/mes [MOD-AMI].",
         resumen="Cliente desde hace 30 meses (Sales Pro, 65 €/mes) y en migración a Core con una subida del 18% [segmento]. Acepta el cambio solo con un 20% de descuento.",
         objecion="Solo acepto si me dais un 20% de descuento.",
         respuesta="Reconoce la subida del 18% sin discutirla [segmento]. Tu margen es de hasta un 10% por pago anual anticipado [DESC-10].",
     ), False),
]


def main() -> int:
    fallos = 0
    for nombre_caso, desc, salida, debe_bloquear in PRUEBAS:
        perfil, senales, resultado = _preparar(nombre_caso)
        viol = comprobar(salida, resultado, CAT, perfil, senales)
        ok = bool(viol) == debe_bloquear
        fallos += not ok
        estado = "OK  " if ok else "FALLO"
        esperado = "bloquea" if debe_bloquear else "pasa"
        print(f"[{estado}] {desc} (esperado: {esperado}){' → ' + '; '.join(viol) if viol else ''}")
    print(f"\n{len(PRUEBAS) - fallos}/{len(PRUEBAS)} pruebas OK")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
