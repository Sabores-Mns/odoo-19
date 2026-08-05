-- Inventario del esquema Profit Plus (BD MULTI_A)
-- Uso: sqlcmd -S localhost -U sa -d MULTI_A -i 00_inventario.sql
SET NOCOUNT ON;

PRINT '=== TABLAS Y CONTEOS ===';
SELECT t.name AS tabla, SUM(p.rows) AS filas
FROM sys.tables t
JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
GROUP BY t.name
HAVING SUM(p.rows) > 0
ORDER BY filas DESC;

PRINT '=== BASES DE DATOS ===';
SELECT name FROM sys.databases;
