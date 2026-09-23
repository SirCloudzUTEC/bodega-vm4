-- Ejecutar cada sentencia por separado en Athena, workgroup bodega-h2-analytics,
-- base bodega_h2. Guardar screenshot del resultado y estado SUCCEEDED.
-- Los JOIN entre MySQL, PostgreSQL y Mongo usan producto_id lógico.

-- 1: MySQL, productos y movimientos.
SELECT p.categoria, COUNT(*) AS movimientos,
       SUM(TRY_CAST(m.cantidad AS BIGINT)) AS unidades_movidas
FROM bodega_h2.productos p JOIN bodega_h2.movimientos_inventario m
  ON TRY_CAST(p.id AS BIGINT)=TRY_CAST(m.producto_id AS BIGINT)
WHERE p.dt=(SELECT MAX(dt) FROM bodega_h2.productos)
  AND m.dt=(SELECT MAX(dt) FROM bodega_h2.movimientos_inventario)
GROUP BY p.categoria ORDER BY movimientos DESC;

-- 2: ventas PostgreSQL y productos MySQL.
SELECT p.categoria, COUNT(*) AS ventas,
       SUM(TRY_CAST(v.cantidad_vendida AS BIGINT)) AS unidades
FROM bodega_h2.ventas_diarias v JOIN bodega_h2.productos p
  ON TRY_CAST(v.producto_id AS BIGINT)=TRY_CAST(p.id AS BIGINT)
WHERE v.dt=(SELECT MAX(dt) FROM bodega_h2.ventas_diarias)
  AND p.dt=(SELECT MAX(dt) FROM bodega_h2.productos)
GROUP BY p.categoria ORDER BY unidades DESC;

-- 3: MySQL proveedores y sus tiempos de entrega, catálogo de inventario.
SELECT pr.nombre AS proveedor, p.categoria,
       AVG(TRY_CAST(t.dias_entrega_promedio AS DOUBLE)) AS dias_promedio
FROM bodega_h2.proveedores pr JOIN bodega_h2.tiempos_entrega t
  ON TRY_CAST(pr.id AS BIGINT)=TRY_CAST(t.proveedor_id AS BIGINT)
JOIN bodega_h2.productos p
  ON TRY_CAST(t.producto_id AS BIGINT)=TRY_CAST(p.id AS BIGINT)
WHERE pr.dt=(SELECT MAX(dt) FROM bodega_h2.proveedores)
  AND t.dt=(SELECT MAX(dt) FROM bodega_h2.tiempos_entrega)
  AND p.dt=(SELECT MAX(dt) FROM bodega_h2.productos)
GROUP BY pr.nombre,p.categoria ORDER BY dias_promedio DESC LIMIT 30;

-- 4: Mongo (riesgo), MySQL (productos) y PostgreSQL (ventas agregadas).
WITH venta AS (
 SELECT producto_id, SUM(TRY_CAST(cantidad_vendida AS BIGINT)) AS unidades
 FROM bodega_h2.ventas_diarias
 WHERE dt=(SELECT MAX(dt) FROM bodega_h2.ventas_diarias)
 GROUP BY producto_id
)
SELECT p.nombre, p.categoria, COUNT(*) AS dias_en_riesgo,
       COALESCE(MAX(venta.unidades),0) AS unidades_vendidas
FROM bodega_h2.predicciones pr JOIN bodega_h2.productos p
  ON TRY_CAST(pr.producto_id AS BIGINT)=TRY_CAST(p.id AS BIGINT)
LEFT JOIN venta ON TRY_CAST(pr.producto_id AS BIGINT)=TRY_CAST(venta.producto_id AS BIGINT)
WHERE pr.dt=(SELECT MAX(dt) FROM bodega_h2.predicciones)
  AND p.dt=(SELECT MAX(dt) FROM bodega_h2.productos)
  AND TRY_CAST(pr.prob_quiebre AS DOUBLE)>0.66
GROUP BY p.nombre,p.categoria ORDER BY dias_en_riesgo DESC LIMIT 30;

-- Vista 1: rotación por categoría (snapshot más reciente).
CREATE OR REPLACE VIEW bodega_h2.v_rotacion_categoria AS
SELECT p.categoria,
       SUM(TRY_CAST(v.cantidad_vendida AS BIGINT)) AS unidades_vendidas
FROM bodega_h2.productos p JOIN bodega_h2.ventas_diarias v
  ON TRY_CAST(p.id AS BIGINT)=TRY_CAST(v.producto_id AS BIGINT)
WHERE p.dt=(SELECT MAX(dt) FROM bodega_h2.productos)
  AND v.dt=(SELECT MAX(dt) FROM bodega_h2.ventas_diarias)
GROUP BY p.categoria;

-- Vista 2: riesgo alto por categoría (snapshot más reciente).
CREATE OR REPLACE VIEW bodega_h2.v_riesgo_categoria AS
SELECT p.categoria, COUNT(*) AS dias_en_riesgo
FROM bodega_h2.productos p JOIN bodega_h2.predicciones pr
  ON TRY_CAST(p.id AS BIGINT)=TRY_CAST(pr.producto_id AS BIGINT)
WHERE p.dt=(SELECT MAX(dt) FROM bodega_h2.productos)
  AND pr.dt=(SELECT MAX(dt) FROM bodega_h2.predicciones)
  AND TRY_CAST(pr.prob_quiebre AS DOUBLE)>0.66
GROUP BY p.categoria;

-- Consultar vistas y capturar resultados:
SELECT * FROM bodega_h2.v_rotacion_categoria ORDER BY unidades_vendidas DESC;
SELECT * FROM bodega_h2.v_riesgo_categoria ORDER BY dias_en_riesgo DESC;
