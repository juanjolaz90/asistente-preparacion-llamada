#!/usr/bin/env python3
"""
Prueba del guardrail sin IA ni dependencias: python test_guardrail.py

- La redacción "mala" (POS en FR, 39 €, 15 % de descuento, Core a 89 €…) tiene que BLOQUEARSE.
- La redacción del caso 04 que antes daba falso positivo (cita el precio legacy de 65 €,
  la subida del 22 % y el 20 % que pide la clienta) tiene que PASAR.
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
    ("caso_04_descuento_20.json", "Mantiene el precio legacy ('te lo mantengo en 65 €')",
     _salida("Si firmas hoy te lo mantengo en 65 €/mes."), True),
    ("caso_04_descuento_20.json", "Ofrece el 20 % que pide",
     _salida("Como me pides, te doy un 20% de descuento."), True),
    ("caso_04_descuento_20.json", "Ofrece el 22 % (= subida) como descuento",
     _salida("Te aplico un 22% de descuento para compensar."), True),
    ("caso_01_italia_bb.json", "Sin escalado reflejado",
     {**_salida("Tu plan pasa a Core a 79 €/mes."), "escalar_a_persona": []}, True),

    ("caso_04_descuento_20.json", "Suma inventada (Core + Ami = 149 €)",
     _salida("Core con Ami te sale por 149 €/mes."), True),

    # --- pitch en inglés (idioma_comercial = en): mismas reglas
    ("caso_03_francia_restaurante.json", "EN: ofrece 15% discount",
     _salida("I can give you a 15% discount if you sign today."), True),
    ("caso_03_francia_restaurante.json", "EN: POS recomendado en Francia",
     _salida("The POS would be perfect for your restaurant."), True),
    ("caso_03_francia_restaurante.json", "EN: 'POS is not available in France' pasa",
     _salida("The POS is not available in France today and I can't promise a date [MOD-POS]. Guest Upsell fits your restaurant [MOD-GU]."), False),
    ("caso_04_descuento_20.json", "EN: subida (+22%) y 20% como condición del cliente",
     _salida(resumen="Customer on Sales Pro (€65/month), who accepts that the price goes up (+22%) but sets a 20% discount as a condition.",
             pitch="Your plan moves to Core at €79/month [PLAN-CORE]. On your 20% request, I can't confirm any figure on this call [descuento_superior]."), False),
    ("caso_04_descuento_20.json", "EN: 'I can give you 20% off' bloquea",
     _salida("You asked for 20% and I can give you a 20% discount today."), True),
    ("caso_04_descuento_20.json", "EN: cita el 20% que pide, sin ofrecerlo",
     _salida("You asked for a 20% discount; that is not something I can approve, so I will pass it to the Head of CS [descuento_superior]."), False),

    # --- falsos positivos ya corregidos: deben pasar
    ("caso_04_descuento_20.json", "Caso 04: 'pasar de 65 € a 79 € se nota'",
     _salida("Lo entiendo: pasar de 65 € a 79 € se nota [PLAN-CORE]."), False),
    ("caso_04_descuento_20.json", "Suma legítima (Core 79 € + Ami 69 € = 148 €)",
     _salida("Core con Ami te sale por 148 €/mes en total [PLAN-CORE][MOD-AMI]."), False),
    ("caso_03_francia_restaurante.json", "Caso 03: el cliente pregunta por el POS (objeción)",
     _salida("Para tu restaurante lo que encaja es Guest Upsell [MOD-GU].", objecion="¿Cuándo vais a lanzar el POS en Francia?",
             respuesta="Hoy el POS no está disponible en Francia y no tengo fecha [MOD-POS]."), False),
    ("caso_03_francia_restaurante.json", "Caso 03: 'posible' no es 'POS'",
     _salida("Paso tu caso al equipo para que te diga qué es posible de verdad [MOD-POS], y mientras vemos el plan Core [PLAN-CORE]."), False),
    ("caso_04_descuento_20.json", "Caso 04: 'acepta… solo si le dan un 20%'",
     _salida("Cliente de Sales Pro que acepta la subida de precio solo si le dan un 20% de descuento [descuento_superior]."), False),
    ("caso_04_descuento_20.json", "Caso 04: legacy, subida y % pedido",
     _salida(
         pitch="Tu plan pasaría a Core, a 79 €/mes hasta 10 habitaciones [PLAN-CORE]. Ami tiene un precio desde 69 €/mes [MOD-AMI].",
         resumen="Cliente desde hace 30 meses (Sales Pro, 65 €/mes) y en migración a Core con una subida del 22% [segmento]. Acepta el cambio solo con un 20% de descuento.",
         objecion="Solo acepto si me dais un 20% de descuento.",
         respuesta="Reconoce la subida del 22% sin discutirla [segmento]. Tu margen es de hasta un 10% por pago anual anticipado [DESC-10].",
     ), False),
]


def main() -> int:
    fallos = 0
    for nombre_caso, desc, salida, debe_bloquear in PRUEBAS:
        perfil, senales, resultado = _preparar(nombre_caso)
        viol = comprobar(salida, resultado, CAT, perfil, senales)
        ok = bool(viol) == debe_bloquear
        fallos += not ok
        estado = "OK  " if ok else "FAIL"
        esperado = "blocks" if debe_bloquear else "passes"
        print(f"[{estado}] {desc} (expected: {esperado}){' → ' + '; '.join(viol) if viol else ''}")
    print(f"\n{len(PRUEBAS) - fallos}/{len(PRUEBAS)} tests OK")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
