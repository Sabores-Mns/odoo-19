# Profit Plus → Odoo 19 migration

Everything needed to migrate **Profit Plus Administrativo 2K** (SQL Server) into
**Odoo 19 Community**, database `profit_migrado19`.

---

## Quick start

1. Drop the Profit `.bak` backup into [`db/`](db/README.md). Exactly one file.
2. From the repository root:

   ```powershell
   .\migration\run-migration.ps1
   ```

   ```bash
   ./migration/run-migration.sh
   ```

3. Open <http://localhost:10020> with `admin` / `admin`.

The script brings up the infrastructure, restores the backup, creates the
database, runs the whole ETL and verifies the result. It takes about 2–3 hours
and is re-runnable: every phase detects whether it already ran and skips it.

Exit codes: `0` all good · `1` the migration stopped · `2` finished but some
tests are red.

### Re-running only part of it

```powershell
.\migration\run-migration.ps1 -SkipExtract -Steps reconcile,crossapply,trueup,verify
```

| Flag | Effect |
|---|---|
| `-Steps a,b,c` | only those loader steps |
| `-SkipExtract` | reuse the CSVs already in `export/` (saves ~10 min) |
| `-SkipTests` | skip the final test suite |
| `-Force` | re-run the `RESTORE` even if `MULTI_A` already exists |

> **Long runs and containers.** The loader runs inside `migration_runner`.
> Killing the host process does **not** stop it — the container keeps going. To
> run a step detached and follow it from a log:
>
> ```bash
> docker exec -d migration_runner sh -c \
>   'python -u /migration/etl/load19.py reconcile > /migration/export/reconcile.log 2>&1'
> ```

---

## Documentation

Written material lives in `docs/`. Read `MIGRATION_EN.md` first; the rest answer
specific questions.

| Document | What it covers | Read it when |
|---|---|---|
| [`docs/MIGRATION_EN.md`](docs/MIGRATION_EN.md) | The full manual, 13 sections: architecture, every ETL stage, troubleshooting table, operational tooling | You want the complete picture, or a command failed and you need the error table |
| [`docs/MIGRATION_ZH.md`](docs/MIGRATION_ZH.md) | The same manual in Simplified Chinese (简体中文) | 需要中文文档 |
| [`docs/ODOO19_API_NOTES.md`](docs/ODOO19_API_NOTES.md) | What changed between the Odoo 17/18 loaders and v19, each difference verified against a live container — removed models, renamed fields, the payment behaviour that needs `fix_payments`, and the marshalling faults that are safe to ignore | You are touching `load19.py`, or Odoo rejects a field |
| [`docs/EXCHANGE_DIFFERENCES.md`](docs/EXCHANGE_DIFFERENCES.md) | Why the bi-monetary ledger is set up the way it is: Profit stores bolívar equivalents at the day's rate, and re-expressing in USD does not close to the cent | A balance is off by small amounts, or you are about to schedule `ops/sync_bcv.py` |
| [`db/README.md`](db/README.md) | Where to put the `.bak` and why it must never reach GitHub | Setting up on a new machine |

---

## Layout

| Path | Contents |
|---|---|
| `db/` | **the Profit `.bak`** — git-ignored, you supply it |
| `etl/` | the pipeline: `extract` → `transform` → `transform19` → `load19` |
| `ops/` | diagnostics, cleanup and backup tools |
| `export/` | ETL output: CSVs, JSON plans and reports |
| `docs/` | manuals and analysis — see the table above |
| `run-migration.ps1` / `.sh` | the entry point |
| `docker-compose.migration.yml` | SQL Server + migrator container |

The root `docker-compose.yml` is **never touched**. The migration compose is a
separate file that attaches to the root stack's network as external, so the
auto-deploy workflow never starts SQL Server or the migrator in production.

SQL Server is published on host port **1434**, not 1433, because the older v17
migration project (`profit_mssql`) claims 1433 when Docker restarts. Inside the
network the runner still reaches it at `mssql:1433`.

---

## The ten steps of `load19.py`

```
setup → masters → invoices → payments → fix_payments →
reconcile → crossapply → trueup → stock → verify
```

### How settlement works

`reconcile` applies **the exact amount Profit recorded** for every receipt line,
building `account.partial.reconcile` records directly instead of calling Odoo's
automatic `reconcile()`.

This matters. Automatic reconciliation applies the *maximum* it can, so a
receipt that Profit posted as a partial payment would cancel the whole invoice
and silently erase debt the customer still owes. A real case: receipt 5448 pays
`8,572.65` against invoice FAC 11564 of `13,392.00`, leaving `4,819.35`
outstanding — automatic reconciliation settled all `13,392.00`.

The per-line amounts come from `reng_cob.neto` and are carried in
`export/plan/reconcile_plan.json`:

```json
"5448": {"docs":    [{"id": "profit_fac_11564", "monto": 8572.65}],
         "fuentes": [{"id": "profit_nc_1214",   "monto": 795.74}]}
```

Sources are consumed **before** the receipt's own cash, each capped at the
amount Profit assigned to it. Otherwise a receipt with enough cash covers the
invoices on its own and the credit notes and advances are never consumed —
leaving them floating as customer credit even though Profit considers them
applied.

### Ordering constraints

- `fix_payments` runs after `payments`: a payment with no journal entry has no
  lines to reconcile.
- **`crossapply` runs before `trueup`**, so the MIGAJ adjustment only absorbs
  genuine FX differences instead of penalising real business documents.

### What `trueup` is for

It writes off whatever is still outstanding on documents and payments that
Profit reports as settled (`esperado == 0`), so nothing is left floating —
including cent-level FX remainders. Balances Profit still reports as open
(unapplied advances) are left untouched and remain as customer credit.

Adjustments above `TRUEUP_AVISO` (default **20 USD**, override via environment)
are still written off, but reported: at that size it is no longer rounding, and
it should be looked at rather than quietly absorbed by MIGAJ.

---

## Verification

| File | Contents |
|---|---|
| `export/verificacion19.md` | counts, balances, reconciliation integrity |
| `export/mismatch19.csv` | documents whose balance differs from Profit |
| `export/test_odoo19.md` | T1–T12 test results |

These must come out at zero: payments without a journal entry, reconciled lines
carrying a residual, and unreconciled MIGAJ adjustments.

### The receivables target

The net target is **USD 14,497,946.12** (`FACT + N/DB − N/CR`).

An older figure of `14,544,680.74` circulated in earlier notes. It is wrong:
`saldo_objetivo.json` stores open credit notes as positive, so that total *adds*
them instead of subtracting, inflating it by twice the open N/C balance
(2 × 23,367.31 = 46,734.62). `verify` now applies the sign, which is also why it
used to report ~48 mismatched documents where test T8b, which handles the sign
correctly, found far fewer.

> Negative stock (≈ −22,453,940) is **faithful to the source** and is not
> corrected: Profit carries negative stock because the company sells without
> recording every purchase. See `docs/MIGRATION_EN.md` §11.

---

## The backup never reaches GitHub

`migration/db/` is excluded in the root `.gitignore`. Verified with
`git check-ignore -v migration/db/<file>.bak`, which answers `.gitignore:11`.
Before any commit, confirm with `git status` that the `.bak` is absent.

---

## Running it on another machine

The `.bak` is **not** in git — copy it across separately. Everything else is
overridable through environment variables (`ODOO_URL`, `ODOO_DB`, `MSSQL_HOST`,
`RAW_DIR`, `TRUEUP_AVISO`, …), so the same code runs against a differently named
container or database.

Requirements on the target host: the `odoo-19` stack up (it creates the
`odoo-19-mns_default` network the migrator attaches to), a container named
`odoo-19-mns`, and the images `postgres:18`, `odoo:19`,
`mcr.microsoft.com/mssql/server:2022-latest` and `python:3.12-slim`.

If a database named `profit_migrado19` already exists there, phase 5 detects it
and does **not** recreate it — but the ETL will still load on top. Decide
whether you want a fresh database or a backup of the existing one first.

---

## Credentials

| Service | User | Password |
|---|---|---|
| Odoo 19 | `admin` | `admin` |
| Odoo master password | — | `admin` |
| Postgres | `odoo` | `odoo19@2025` |
| SQL Server | `sa` | `Profit2Odoo!2026` |

These are development credentials, committed in plain text. Rotate them before
exposing any of this to a network you do not control.

Note that Odoo rewrites `etc/odoo.conf` on startup, replacing the plaintext
`admin_passwd` with a pbkdf2 hash. The file will show as modified in
`git status`; that is Odoo, not you. If `ops/backup_db.py` ever returns *Access
Denied*, compare that file against git before looking anywhere else.
