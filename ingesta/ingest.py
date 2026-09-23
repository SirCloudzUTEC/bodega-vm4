"""Ingesta PULL del 100 % de los registros de UN microservicio hacia S3.

Cada contenedor corre este script con un JOB distinto (1 contenedor = 1 microservicio):
  inventario  -> MySQL inventario_db:   productos, movimientos_inventario
  proveedores -> MySQL proveedores_db:  proveedores, tiempos_entrega
  ventas      -> PostgreSQL ventas_db:  ventas_diarias, pedidos_proveedor
  prediccion  -> MongoDB prediccion_db: predicciones

Por tabla: COUNT(*) en origen, exporta por lotes de 1000 a CSV con cabecera,
comprueba que lo exportado == lo contado (si no, falla) y sube a
  s3://<bucket>/raw/<tabla>/dt=<AAAA-MM-DD>/<tabla>.csv
más un manifiesto con conteo y columnas en
  s3://<bucket>/manifests/<tabla>/dt=<AAAA-MM-DD>.json
Nunca genera datos de reemplazo: ante cualquier error termina con código 1.
"""
import csv
import json
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from urllib.parse import quote_plus

import boto3

JOB = os.environ.get("JOB", "")
HOST = os.environ["DB_HOST"]
BUCKET = os.environ["S3_BUCKET"]
REGION = os.getenv("AWS_REGION", "us-east-1")
DT = os.getenv("DATA_DATE") or datetime.now(timezone.utc).date().isoformat()
LOTE = 1000

JOBS = {
    "inventario": ("mysql", "inventario_db", ["productos", "movimientos_inventario"]),
    "proveedores": ("mysql", "proveedores_db", ["proveedores", "tiempos_entrega"]),
    "ventas": ("postgres", "ventas_db", ["ventas_diarias", "pedidos_proveedor"]),
    "prediccion": ("mongo", "prediccion_db", ["predicciones"]),
}
PRED_FIELDS = ["producto_id", "fecha", "velocidad_venta_diaria", "stock_actual",
               "dias_hasta_agotamiento", "tiempo_entrega_promedio", "prob_quiebre",
               "nivel_riesgo", "created_at"]

s3 = boto3.client("s3", region_name=REGION)


def texto(valor):
    if valor is None:
        return ""
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    return str(valor)


def exportar(tabla, columnas, filas, esperado):
    """Escribe el CSV, verifica el conteo y lo sube con su manifiesto."""
    ruta = None
    exportado = 0
    try:
        with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8", suffix=".csv",
                                         delete=False) as f:
            ruta = f.name
            w = csv.writer(f)
            w.writerow(columnas)
            for fila in filas:
                w.writerow([texto(fila.get(c)) for c in columnas])
                exportado += 1
        if exportado != esperado:
            raise RuntimeError(f"{tabla}: origen tiene {esperado} registros, se exportaron {exportado}")
        clave = f"raw/{tabla}/dt={DT}/{tabla}.csv"
        s3.upload_file(ruta, BUCKET, clave)
        manifiesto = {"tabla": tabla, "job": JOB, "dt": DT, "registros": exportado,
                      "columnas": list(columnas), "s3_key": clave,
                      "generado_utc": datetime.now(timezone.utc).isoformat()}
        s3.put_object(Bucket=BUCKET, Key=f"manifests/{tabla}/dt={DT}.json",
                      Body=json.dumps(manifiesto, ensure_ascii=False).encode(),
                      ContentType="application/json")
        print(f"[{JOB}] {tabla}: {exportado} de {esperado} registros (100 %) -> s3://{BUCKET}/{clave}",
              flush=True)
    finally:
        if ruta and os.path.exists(ruta):
            os.remove(ruta)


def job_mysql(base, tablas):
    import mysql.connector
    conn = mysql.connector.connect(host=HOST, port=3306, user="bodega",
                                   password=os.environ["MYSQL_PASSWORD"], database=base,
                                   connection_timeout=15)
    try:
        # Una sola transacción de lectura: COUNT y SELECT ven la misma foto de los datos.
        inicio = conn.cursor()
        inicio.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        inicio.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        inicio.close()
        for tabla in tablas:
            c = conn.cursor()
            c.execute(f"SELECT COUNT(*) FROM {tabla}")
            esperado = c.fetchone()[0]
            c.close()
            cur = conn.cursor(dictionary=True)
            cur.execute(f"SELECT * FROM {tabla} ORDER BY id")
            columnas = list(cur.column_names)

            def filas():
                while True:
                    lote = cur.fetchmany(LOTE)
                    if not lote:
                        return
                    yield from lote

            exportar(tabla, columnas, filas(), esperado)
            cur.close()
        conn.commit()
    finally:
        conn.close()


def job_postgres(base, tablas):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(host=HOST, port=5432, user="bodega",
                            password=os.environ["POSTGRES_PASSWORD"], dbname=base,
                            connect_timeout=15)
    try:
        cur = conn.cursor()
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for tabla in tablas:
            cur.execute(f"SELECT COUNT(*) FROM {tabla}")
            esperado = cur.fetchone()[0]
            flujo = conn.cursor(name=f"export_{tabla}", cursor_factory=RealDictCursor)
            flujo.itersize = LOTE
            flujo.execute(f"SELECT * FROM {tabla} ORDER BY id")
            primera = flujo.fetchone()
            columnas = [d.name for d in flujo.description]

            def filas():
                if primera is not None:
                    yield primera
                    yield from flujo

            exportar(tabla, columnas, filas(), esperado)
            flujo.close()
        conn.commit()
    finally:
        conn.close()


def job_mongo(base, tablas):
    from pymongo import MongoClient
    uri = (f"mongodb://bodega:{quote_plus(os.environ['MONGO_PASSWORD'])}@{HOST}:27017/"
           f"{base}?authSource={base}")
    client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    try:
        coleccion = client[base][tablas[0]]
        esperado = coleccion.count_documents({})
        docs = coleccion.find({}, {"_id": 0, **{c: 1 for c in PRED_FIELDS}}).sort(
            [("producto_id", 1), ("fecha", 1)]).batch_size(LOTE)
        exportar(tablas[0], PRED_FIELDS, docs, esperado)
    finally:
        client.close()


def main():
    if JOB not in JOBS:
        sys.exit(f"JOB inválido '{JOB}'. Opciones: {', '.join(JOBS)}")
    motor, base, tablas = JOBS[JOB]
    print(f"[{JOB}] pull 100 % desde {motor}:{base} ({HOST}), dt={DT}", flush=True)
    {"mysql": job_mysql, "postgres": job_postgres, "mongo": job_mongo}[motor](base, tablas)
    print(f"[{JOB}] OK", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - cualquier fallo debe terminar en código 1
        print(f"[{JOB}] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
