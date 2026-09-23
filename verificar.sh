#!/usr/bin/env bash
# Evidencia de la ingesta: estado de los jobs, archivos en S3, manifiestos y tablas Glue.
# Usa las credenciales del perfil IAM de la EC2 (aws cli dentro del contenedor de ingesta).
cd "$(dirname "$0")"
set -a; . ./.env; set +a
DT="${DATA_DATE:-$(date -u +%F)}"
echo "== Jobs (último estado)"; docker compose ps -a --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
docker compose run --rm --no-deps -T -e DT="$DT" -e ATHENA_DATABASE="${ATHENA_DATABASE:-bodega_h2}" \
  --entrypoint python catalogo-glue - <<'PY'
import json, os, boto3
b, dt, db = os.environ["S3_BUCKET"], os.environ["DT"], os.environ["ATHENA_DATABASE"]
s3, glue = boto3.client("s3"), boto3.client("glue")
tablas = ["productos","movimientos_inventario","proveedores","tiempos_entrega","ventas_diarias","pedidos_proveedor","predicciones"]
print(f"\n== Manifiestos dt={dt} (conteo exportado = conteo en origen)")
ok = 0
for t in tablas:
    try:
        m = json.loads(s3.get_object(Bucket=b, Key=f"manifests/{t}/dt={dt}.json")["Body"].read())
        size = s3.head_object(Bucket=b, Key=m["s3_key"])["ContentLength"]
        print(f"OK    {t:24s} {m['registros']:>7} registros  {size/1024:>8.0f} KiB  s3://{b}/{m['s3_key']}"); ok += 1
    except Exception as e:
        print(f"FALTA {t:24s} ({type(e).__name__})")
print(f"\n== Tablas Glue en {db}")
try:
    nombres = sorted(t["Name"] for t in glue.get_tables(DatabaseName=db)["TableList"])
    for t in tablas: print(("OK    " if t in nombres else "FALTA ") + t)
except Exception as e:
    print(f"No se pudo leer Glue: {e}")
print(f"\n{ok}/7 archivos de hoy en S3")
PY
