#!/usr/bin/env bash
# Pipeline diario: predicciones del día -> ingesta 100 % (4 jobs) -> catálogo Glue.
# Lo ejecuta el timer systemd a las 02:00 de Perú; también se puede correr a mano.
set -uo pipefail
cd "$(dirname "$0")"
DC="docker compose"
echo "== $(date -u +%FT%TZ) 1/3 predicción nocturna"
$DC --profile nocturno run --rm prediccion-nocturna || echo "AVISO: hubo predicciones fallidas; se continúa con la ingesta"
echo "== 2/3 ingesta (4 contenedores)"
fallo=0
for job in ingesta-inventario ingesta-proveedores ingesta-ventas ingesta-prediccion; do
  $DC run --rm "$job" || { echo "ERROR en $job"; fallo=1; }
done
[ "$fallo" -eq 0 ] || { echo "Ingesta incompleta: no se actualiza Glue"; exit 1; }
echo "== 3/3 catálogo Glue"
$DC run --rm --no-deps catalogo-glue
