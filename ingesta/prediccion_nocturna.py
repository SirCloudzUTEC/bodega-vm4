"""Recalcula la predicción del día de cada producto llamando a Predicción por el ALB interno.

No toca bases de datos: lista los productos vía Inventario (paginado) y hace
POST /api/prediccion/calcular/{id} con concurrencia acotada y reintentos.
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

ALB = os.environ.get("BODEGA_ALB_DNS", "").strip().removeprefix("http://").rstrip("/")
if not ALB:
    sys.exit("Falta BODEGA_ALB_DNS en .env (output InternalAlbDnsName, sin http://)")
BASE = f"http://{ALB}"


def productos():
    ids, skip = [], 0
    while True:
        r = requests.get(f"{BASE}/api/inventario/productos", params={"skip": skip, "limit": 200}, timeout=30)
        r.raise_for_status()
        pagina = r.json()
        ids += [p["id"] for p in pagina]
        if len(pagina) < 200:
            return ids
        skip += 200


def calcular(pid):
    motivo = ""
    for intento in range(3):
        try:
            r = requests.post(f"{BASE}/api/prediccion/calcular/{pid}", timeout=40)
            if r.ok:
                return pid, ""
            motivo = f"HTTP {r.status_code}: {r.text[:120]}"
        except requests.RequestException as exc:
            motivo = str(exc)
        time.sleep(1 + 2 * intento)
    return pid, motivo


def main():
    ids = productos()
    print(f"Productos a recalcular: {len(ids)}", flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        resultados = list(pool.map(calcular, ids))
    fallidos = [(p, m) for p, m in resultados if m]
    print(f"Predicciones correctas: {len(ids) - len(fallidos)}  fallidas: {len(fallidos)}", flush=True)
    for p, m in fallidos[:15]:
        print(f"  FALLA producto {p}: {m}", flush=True)
    sys.exit(1 if fallidos else 0)


if __name__ == "__main__":
    main()
