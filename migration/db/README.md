# Respaldo de Profit (origen de la migración)

**Deja aquí el archivo `.bak` de Profit.** Es lo único que hay que aportar a
mano; todo lo demás lo genera el ETL.

```
migration/db/
├── README.md          <- este archivo (sí se versiona)
├── .gitkeep           <- mantiene la carpeta en git (sí se versiona)
└── mnsglup.bak        <- TU respaldo (ignorado por git)
```

## Qué archivo va aquí

Un backup nativo de **SQL Server** de la base de Profit Plus Administrativo.
El de referencia de este proyecto:

| | |
|---|---|
| Nombre | `mnsglup.bak` (cualquier nombre `*.bak` sirve) |
| Tamaño | ~220 MB |
| Origen | SQL Server 2019 |
| Base lógica | `MULTI_A` |
| Archivos lógicos | `GLOBAL_A` (datos) y `GLOBAL_A_log` (log) |

`run-migration.ps1` **detecta automáticamente** el `.bak` que haya en esta
carpeta, así que no importa cómo se llame. Si encuentra más de uno se detiene y
pide que dejes solo el que quieras migrar.

Los nombres lógicos también se detectan en tiempo de ejecución con
`RESTORE FILELISTONLY`; los valores de arriba solo se usan como respaldo si esa
consulta falla. Es decir: un `.bak` de otra empresa Profit debería funcionar sin
tocar nada.

## Cómo se usa

La carpeta se monta **en solo lectura** dentro del contenedor de SQL Server
(`./db:/backups:ro` en `docker-compose.migration.yml`), de modo que el proceso
de restauración no puede alterar ni borrar tu archivo original. Dentro del
contenedor se ve como `/backups/mnsglup.bak`.

Restaurar es el paso 4 de `run-migration.ps1`, o a mano:

```powershell
docker compose -f migration/docker-compose.migration.yml up -d
docker exec migration_runner python /migration/etl/restore_mssql.py
```

## Por qué no se sube a GitHub

El `.bak` contiene **datos reales de clientes y facturación**, y pesa unos
220 MB (muy por encima del límite práctico de GitHub). Las reglas están en el
`.gitignore` de la raíz del repositorio:

```gitignore
migration/db/*
!migration/db/README.md
!migration/db/.gitkeep
```

Comprobado: `git check-ignore -v migration/db/<archivo>.bak` responde
`.gitignore:11`, y `git status` nunca lo lista.

> ⚠️ Si alguna vez cambias estas reglas, verifica **antes** de hacer commit con
> `git status` que el `.bak` no aparece. Un archivo de este tamaño subido por
> error queda en el historial de git aunque lo borres después, y sacarlo obliga
> a reescribir la historia del repositorio.
