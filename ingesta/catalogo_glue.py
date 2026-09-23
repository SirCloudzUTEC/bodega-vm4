"""Registra en AWS Glue una tabla por cada archivo CSV cargado (7 tablas) y la
partición dt de la ingesta. Las columnas salen del manifiesto que escribió
cada job, así el catálogo siempre coincide con el CSV real.

Todas las columnas se registran como string (OpenCSVSerde); en Athena se
convierten con TRY_CAST (ver athena_evidencia.sql).
"""
import json
import os
import sys
from datetime import datetime, timezone

import boto3

REGION = os.getenv("AWS_REGION", "us-east-1")
BUCKET = os.environ["S3_BUCKET"]
DATABASE = os.getenv("ATHENA_DATABASE", "bodega_h2")
DT = os.getenv("DATA_DATE") or datetime.now(timezone.utc).date().isoformat()
TABLAS = ["productos", "movimientos_inventario", "proveedores", "tiempos_entrega",
          "ventas_diarias", "pedidos_proveedor", "predicciones"]

glue = boto3.client("glue", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)


def manifiesto(tabla):
    obj = s3.get_object(Bucket=BUCKET, Key=f"manifests/{tabla}/dt={DT}.json")
    return json.loads(obj["Body"].read())


def descriptor(ubicacion, columnas):
    return {
        "Columns": [{"Name": c, "Type": "string"} for c in columnas],
        "Location": ubicacion,
        "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
        "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
        "SerdeInfo": {
            "SerializationLibrary": "org.apache.hadoop.hive.serde2.OpenCSVSerde",
            "Parameters": {"separatorChar": ",", "quoteChar": '"', "escapeChar": "\\"},
        },
    }


def main():
    faltan = []
    manifiestos = {}
    for t in TABLAS:
        try:
            manifiestos[t] = manifiesto(t)
        except s3.exceptions.NoSuchKey:
            faltan.append(t)
    if faltan:
        sys.exit(f"Faltan manifiestos de dt={DT} para: {', '.join(faltan)}. Correr antes la ingesta.")

    try:
        glue.get_database(Name=DATABASE)
    except glue.exceptions.EntityNotFoundException:
        glue.create_database(DatabaseInput={"Name": DATABASE})
        print(f"Base Glue {DATABASE} creada")

    for tabla, m in manifiestos.items():
        base = f"s3://{BUCKET}/raw/{tabla}/"
        entrada = {
            "Name": tabla,
            "TableType": "EXTERNAL_TABLE",
            "PartitionKeys": [{"Name": "dt", "Type": "string"}],
            "StorageDescriptor": descriptor(base, m["columnas"]),
            "Parameters": {"classification": "csv", "skip.header.line.count": "1"},
        }
        try:
            glue.get_table(DatabaseName=DATABASE, Name=tabla)
            glue.update_table(DatabaseName=DATABASE, TableInput=entrada)
            accion = "actualizada"
        except glue.exceptions.EntityNotFoundException:
            glue.create_table(DatabaseName=DATABASE, TableInput=entrada)
            accion = "creada"
        particion = {"Values": [DT], "StorageDescriptor": descriptor(f"{base}dt={DT}/", m["columnas"])}
        try:
            glue.create_partition(DatabaseName=DATABASE, TableName=tabla, PartitionInput=particion)
        except glue.exceptions.AlreadyExistsException:
            glue.update_partition(DatabaseName=DATABASE, TableName=tabla,
                                  PartitionValueList=[DT], PartitionInput=particion)
        print(f"{DATABASE}.{tabla:24s} {accion}; partición dt={DT}; {m['registros']} registros "
              f"({len(m['columnas'])} columnas)", flush=True)
    print(f"Catálogo listo: {len(manifiestos)} tablas en Glue ({DATABASE})")


if __name__ == "__main__":
    main()
