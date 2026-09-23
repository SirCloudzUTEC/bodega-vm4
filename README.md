# Bodega Inteligente — VM4 (MV Ingesta)

Máquina de ingesta del componente de Data Science. Ejecuta **4 contenedores Python**, uno por cada microservicio con base de datos. Cada uno hace un pull del **100 %** de los registros de su base en VM3 y deja un CSV por tabla en S3. Después, un quinto contenedor registra **una tabla de AWS Glue por cada archivo**.

| Contenedor | Microservicio | Origen (VM3) | Archivos en S3 |
|---|---|---|---|
| `ingesta-inventario` | Inventario | MySQL `inventario_db` | `productos.csv`, `movimientos_inventario.csv` |
| `ingesta-proveedores` | Proveedores | MySQL `proveedores_db` | `proveedores.csv`, `tiempos_entrega.csv` |
| `ingesta-ventas` | Ventas | PostgreSQL `ventas_db` | `ventas_diarias.csv`, `pedidos_proveedor.csv` |
| `ingesta-prediccion` | Predicción | MongoDB `prediccion_db` | `predicciones.csv` |
| `catalogo-glue` | — | Manifiestos en S3 | 7 tablas en Glue `bodega_h2`, particionadas por `dt` |

Rutas en el bucket:

- `raw/<tabla>/dt=AAAA-MM-DD/<tabla>.csv`: datos con cabecera.
- `manifests/<tabla>/dt=AAAA-MM-DD.json`: registros exportados y columnas.

Cada job cuenta los registros en el origen (`COUNT(*)`) y **falla** si lo exportado no coincide. La lectura se hace dentro de una transacción de solo lectura, así el conteo y los datos corresponden al mismo momento. Nunca se generan datos de reemplazo.

## Requisitos previos

- VM3 desplegada y con datos (`./verificar.sh` en VM3 todo OK).
- La EC2 tiene el perfil `LabInstanceProfile`, que da las credenciales para S3 y Glue. No se usan claves AWS en archivos.
- Para la predicción nocturna, los backends deben estar desplegados y healthy detrás del ALB.

## Despliegue

Por **Session Manager**:

```bash
sudo -iu ubuntu
cd /opt/bodega
git clone <URL_DEL_REPO_VM4> vm4          # o desde S3: bodega-vm4.zip
cd vm4
cp .env.example .env && chmod 600 .env
nano .env        # IP de VM3, las 3 contraseñas de aplicación de VM3, bucket y DNS interno del ALB

docker compose up --build    # corre los 4 jobs en paralelo y luego el catálogo Glue; termina solo
./verificar.sh               # evidencia: 7/7 archivos en S3 con conteos y 7 tablas en Glue
```

### Qué debe verse

- En la salida de `up`, una línea por tabla, por ejemplo `movimientos_inventario: 22000 de 22000 registros (100 %)`, y al final `Catálogo listo: 7 tablas en Glue (bodega_h2)`.
- `docker compose ps -a`: los 5 contenedores en `exited (0)`. Es lo esperado, porque son jobs, no servicios.
- `./verificar.sh`: 7 manifiestos `OK` y 7 tablas `OK`.
- En la consola de Athena, workgroup `bodega-h2-analytics`, base `bodega_h2`: ejecutar **una por una** las sentencias de `athena_evidencia.sql` (4 JOINs y 2 vistas) y capturar cada resultado con estado `SUCCEEDED`.

## Correr un solo job

```bash
docker compose run --rm ingesta-ventas
docker compose run --rm --no-deps catalogo-glue     # re-registrar Glue sin repetir la ingesta
```

## Pipeline diario (opcional, recomendado para la demo)

`pipeline_nocturno.sh` hace tres pasos en orden:

1. Recalcula la predicción del día de cada producto con `POST /api/prediccion/calcular/{id}`, a través del ALB interno.
2. Corre los 4 jobs de ingesta.
3. Actualiza Glue.

Para programarlo todos los días a las 02:00 de Perú (07:00 UTC):

```bash
./instalar_timer.sh
sudo systemctl start bodega-nocturno.service       # probarlo ya, sin esperar a la noche
journalctl -u bodega-nocturno.service -n 100 --no-pager
```

La predicción usa *upsert* por producto y día, así que repetirla el mismo día no duplica documentos. Cada ingesta crea una partición `dt` nueva, y las consultas de Athena y de Analítica usan siempre la más reciente (`MAX(dt)`).

## Problemas frecuentes

| Síntoma | Qué revisar |
|---|---|
| `Access denied` / `authentication failed` | Contraseñas del `.env` distintas a las de VM3 |
| Timeout al conectar a VM3 | IP privada de VM3 y grupo de seguridad (VM4 → 3306/5432/27017) |
| `NoCredentialsError` o `AccessDenied` en S3/Glue | Perfil de instancia de la EC2; IMDSv2 con `HttpPutResponseHopLimit: 2` (lo fija la plantilla) |
| `catalogo-glue` dice `Faltan manifiestos` | Algún job de ingesta falló: revisar su salida y volver a correrlo |
| La predicción nocturna falla | `BODEGA_ALB_DNS` sin `http://`; targets healthy en el ALB; regla del SG VM4 → ALB :80 |

## Cambios respecto al repo original (MV4)

- **4 contenedores** (se agregó Predicción/MongoDB), cada uno con la data de un solo microservicio.
- Un solo `docker compose up` corre toda la ingesta y el catálogo. Antes había que editar `.env` entre job y job porque los tres compartían `DB_NAME`.
- Cada tabla va en su propia carpeta de S3 (`raw/<tabla>/dt=.../`), así Glue registra una tabla por archivo. Antes las dos tablas de cada job caían en la misma carpeta y Glue las mezclaba.
- Verificación del 100 %: conteo en origen, comparación con lo exportado y manifiesto en S3.
- Catálogo Glue automático, con columnas tomadas del manifiesto real, y pipeline diario con timer systemd.
- Sin claves AWS en `.env`: se usa el perfil IAM de la instancia.
