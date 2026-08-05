# Profit Plus → Odoo 19 migration

Complete runbook for migrating **Profit Plus Administrativo 2K** (SQL Server)
into **Odoo 19 Community**.

The same document in Chinese: [`MIGRATION_ZH.md`](MIGRATION_ZH.md).

---

## 1. Overview and source system

| | |
|---|---|
| Source | Profit Plus Administrativo 2K |
| Source engine | Microsoft SQL Server 2019 |
| Source database | `MULTI_A` (logical files `GLOBAL_A`, `GLOBAL_A_log`) |
| Input artefact | one native `.bak`, ~220 MB, dropped in `migration/db/` |
| Target | Odoo 19 Community, database `profit_migrado19` |
| Target URL | <http://localhost:10020> — user `admin`, password `admin` |
| Company currency | USD, with VES as a secondary currency |
| Transport | XML-RPC (`/xmlrpc/2/common`, `/xmlrpc/2/object`) |
| Runtime | ~2–3 hours end to end |

The migration is **idempotent**. Every record carries an external ID of the form
`__import__.profit_*`, so any step can be re-run without creating duplicates.

---

## 2. Data inventory

`etl/extract.py` pulls 24 tables out of `MULTI_A` into `export/raw/*.csv`. The
ones that carry the accounting weight:

| Profit table | Meaning | Becomes |
|---|---|---|
| `clientes` | customers | `res.partner` |
| `prov_ben` | suppliers | `res.partner` |
| `art`, `st_almac` | items, stock per warehouse | `product.template`, `stock.quant` |
| `factura`, `reng_fac` | sales invoices and their lines | `account.move` (`out_invoice`) |
| `docum_cc` | the receivables ledger — one row per open item | drives reconciliation and true-up |
| `cobros`, `reng_cob` | collections and what each one settles | `account.payment` + reconciliation |
| `tasas` | exchange rates | `res.currency.rate` |

`docum_cc` is the authority for balances. Its `tipo_doc` values:

| `tipo_doc` | Meaning | External ID prefix in Odoo |
|---|---|---|
| `FACT`, `GIRO`, `CHDV` | invoice | `profit_fac_` |
| `N/DB` | debit note | `profit_nd_` |
| `N/CR` | credit note | `profit_nc_` |
| `ADEL` | customer advance | none of its own — hangs off `profit_cob_<nro_orig>` |
| `AJPA` | payment adjustment | same as `ADEL` |

`ADEL` and `AJPA` have **no document of their own**. They point back at the
collection that created them, which is why `profit_cob_*` external IDs turn up
wherever advances are involved.

---

## 3. Architecture

Two Docker Compose stacks sharing one network.

```
                     network odoo-19-mns_default
   ┌───────────────────────────────┬──────────────────────────────┐
   │  docker-compose.yml (root)    │  migration/docker-compose.   │
   │                               │       migration.yml          │
   │  db-odoo-19-mns  postgres:18  │  migration_mssql   SQL 2022  │
   │  odoo-19-mns     odoo:19      │  migration_runner  python    │
   │      :10020                   │        │                     │
   └───────────────────────────────┴────────┼─────────────────────┘
                    ▲                       │
                    └──── XML-RPC ──────────┘
                       odoo-19-mns:8069
```

The root stack is untouched by this project. The migration stack is a **separate
file**, joined to the root network as `external`. That separation is deliberate:
`.github/workflows/deploy.yml` deploys on push to `main` with a plain
`docker compose up -d`, which only ever reads the root file — so SQL Server and
the migration runner never start in production.

The runner reaches Odoo at `odoo-19-mns:8069` and Postgres at
`db-odoo-19-mns:5432`, by container name.

---

## 4. Prerequisites and credentials

- Docker Desktop running, with images `odoo:19` and `postgres:18` available.
- The Profit `.bak` in `migration/db/`. Any filename ending in `.bak` works —
  the scripts detect it. Exactly one file; two or more is an error.
- ~4 GB free disk for SQL Server plus the restored database.

| Service | User | Password |
|---|---|---|
| Odoo 19 | `admin` | `admin` |
| Odoo master password | — | `admin` (`etc/odoo.conf`) |
| Postgres (Odoo) | `odoo` | `odoo19@2025` |
| SQL Server | `sa` | `Profit2Odoo!2026` |

> These are development credentials, committed in plain text. Rotate them before
> anything faces a network you do not control.

### The `.bak` is never committed

`migration/db/` is excluded from git. From the repository `.gitignore`:

```gitignore
migration/db/*
!migration/db/README.md
!migration/db/.gitkeep
```

Verified with `git check-ignore -v migration/db/<file>.bak`, which answers
`.gitignore:11`. The folder itself survives in the repository through
`.gitkeep`, so a fresh clone still has somewhere to put the backup.

The file holds real customer and invoicing data and is far past GitHub's
practical size limit. If you ever change these rules, check `git status`
**before** committing: a 220 MB file pushed by mistake stays in the history even
after you delete it, and removing it means rewriting the repository history.

---

## 5. Runbook

### One command

```powershell
.\migration\run-migration.ps1
```

```bash
./migration/run-migration.sh
```

Both run the same seven phases and return the same exit codes: `0` success,
`1` the migration stopped, `2` it finished but some tests are red.

Useful flags:

| PowerShell | Bash | Effect |
|---|---|---|
| `-Steps a,b,c` | `--steps a,b,c` | run only those loader steps |
| `-SkipExtract` | `--skip-extract` | reuse the CSVs already in `export/` |
| `-Force` | `--force` | restore the `.bak` again even if `MULTI_A` exists |
| `-SkipTests` | `--skip-tests` | skip the T1–T12 suite |

Retrying only reconciliation, for example:

```powershell
.\migration\run-migration.ps1 -SkipExtract -Steps reconcile,crossapply,trueup,verify
```

### The same thing by hand

```bash
# 1. infrastructure
docker compose up -d                                        # from the repo root
docker compose -f migration/docker-compose.migration.yml up -d

# 2. dependencies
docker exec migration_runner pip install -r /migration/requirements.txt

# 3. restore Profit
docker exec migration_runner python /migration/etl/restore_mssql.py

# 4. create the Odoo database
docker exec odoo-19-mns odoo -c /etc/odoo/odoo.conf -r odoo -w odoo19@2025 \
    -d profit_migrado19 -i base,contacts,product,sale_management,stock,account,l10n_ve \
    --load-language=es_419 --stop-after-init --no-http

# 5. ETL
docker exec migration_runner python -u /migration/etl/extract.py
docker exec migration_runner python -u /migration/etl/transform.py
docker exec migration_runner python -u /migration/etl/transform19.py
docker exec migration_runner python -u /migration/etl/load19.py

# 6. verification
docker exec migration_runner python -u /migration/etl/tests_migracion.py 19
```

`restore_mssql.py` reads the logical file names out of the backup with
`RESTORE FILELISTONLY` instead of assuming `GLOBAL_A` / `GLOBAL_A_log`, so a
backup from a different Profit company works without editing anything.

---

## 6. Pipeline

```
 MULTI_A ──extract.py───▶ export/raw/*.csv          24 tables, verbatim
         ──transform.py─▶ export/odoo_csv/*.csv     Odoo import format
                        ▶ export/plan/*.json        reconciliation plans
         ──transform19.py▶ export/odoo_csv19/*.csv  Odoo 19 adjustments
         ──load19.py────▶ Odoo, over XML-RPC
```

`load19.py` runs ten steps in this order:

| # | Step | What it does |
|---|---|---|
| 1 | `setup` | company, currencies, journals, the `MIGAJ` adjustment journal and account `899999` |
| 2 | `masters` | partners, products, taxes, payment terms |
| 3 | `invoices` | invoices, credit notes, debit notes; posts them |
| 4 | `payments` | one `account.payment` per real collection; posts them |
| 5 | `fix_payments` | gives a journal entry to payments left `in_process` |
| 6 | `reconcile` | applies `reconcile_plan.json`: each collection against what it settles |
| 7 | `crossapply` | the zero-cash document-against-document offsets |
| 8 | `trueup` | absorbs the leftover residual into `MIGAJ` |
| 9 | `stock` | warehouses and quantities on hand |
| 10 | `verify` | writes `export/verificacion19.md` |

**Order is load-bearing in two places.** `fix_payments` must follow `payments`,
because a payment with no journal entry has no lines to reconcile. And
`crossapply` must run **before** `trueup`, so the `MIGAJ` adjustment only
absorbs genuine exchange differential instead of writing off real business
offsets.

Run a subset by naming the steps:

```bash
docker exec migration_runner python -u /migration/etl/load19.py reconcile crossapply
```

---

## 7. Odoo 19 specifics

Full detail in [`ODOO19_API_NOTES.md`](ODOO19_API_NOTES.md). The short version:

| Change | How it is handled |
|---|---|
| `uom.category` removed | `01_uom_category.csv` is not loaded; `02_uom_uom.csv` reduced to `id,name` |
| `res.users.groups_id` → `group_ids` | renamed by `transform19.py` |
| `product.template.uom_po_id` removed | column dropped by `transform19.py` |
| `account.payment.term` duplicates lines on re-import | external ID per line, plus `SKIP_IF_EXISTS` in the loader |
| `account.payment` posts to `in_process` with no entry | `step_fix_payments` sets `payment_account_id` and re-posts |
| `Fault: cannot marshal None` on post/reconcile | tolerated explicitly — the server has already committed |

That last one deserves emphasis. XML-RPC cannot serialise what `action_post` and
`reconcile` return, so the client raises a fault **after** the transaction has
been written. `load19.py` swallows only that specific fault:

```python
def is_marshal_fault(exc):
    return "cannot marshal" in str(exc)
```

The Odoo 17 loader used a bare `except Exception: pass` here, which also hid real
failures. This version counts and logs anything else.

---

## 8. Currency model

The company ledger is in **USD**; **VES** is the secondary currency, quoted in
bolívars per dollar.

Profit stores each collection's bolívar equivalent at the rate of the day. Odoo
stores rates the other way round — as a multiplier against the company currency.
`load19.py::_rates_to_secondary_currency()` therefore rewrites every `base.USD`
rate row as a `base.VES` row with `rate = 1/rate`.

This is not cosmetic. Without the inversion a $12.9M receivable renders as
**4,724 million**, and every reconciliation comparison becomes meaningless.

The consequence is that a document paid in bolívars rarely re-expresses to
exactly its original dollar amount. Those residual cents are genuine exchange
differential, and step 8 (`trueup`) is what absorbs them. See
[`EXCHANGE_DIFFERENCES.md`](EXCHANGE_DIFFERENCES.md) for the full analysis.

> ⚠️ `ops/sync_bcv.py` writes `res.currency.rate` **without** inverting. One of
> the two conventions is wrong. Do not schedule `sync_bcv.py` until test T11 has
> confirmed which.

---

## 9. Advances and reconciliation

This is what the migration is really about, and it has three mechanisms.

### Advances (`ADEL`) as applicable credit

Collections are loaded as native `account.payment` records rather than raw
journal entries. That is what makes an unapplied advance show up as available
credit on the customer's invoice form, instead of being an inert accounting
entry.

An advance has no document of its own in Profit: the `docum_cc` row of type
`ADEL` points at the collection in `nro_orig`, so in Odoo its balance lives on
`profit_cob_<nro_orig>`.

Success criteria:

- `account.payment` count for `partner_type = customer` matches the number of
  real collections, and is **not zero**.
- Payments posted without a journal entry: **0**.
- Open advance credit in Odoo matches the `ADEL` rows in `docum_cc` that still
  carry a balance.

### Cross-application (`crossapply`)

Profit records document-against-document offsets as collections with **amount
zero** — for example collection 2909, which cancels credit note 447 against
debit note 132 for $343,098.91 without a cent changing hands.

`reconcile_plan.json` excludes these (roughly 824 collections, 3,485 lines,
about $30.1M), so before this step existed those documents stayed open in Odoo
at their full value. `step_crossapply` finds them directly in the raw CSVs —
non-voided collections with `abs(monto) < 0.0001` — groups the lines by
collection, and reconciles any group touching two or more distinct documents.

### True-up (`trueup`) and the `MIGAJ` journal

What survives steps 6 and 7 is exchange differential. For every receivable line
that Profit reports as settled but Odoo still shows as open, `trueup` posts a
two-line entry:

- one line against the original receivable account, for the residual;
- the balancing line against account **`899999`** (`income_other`), in journal
  **`MIGAJ`**;

then reconciles the two. Entries are dated `config.ADJ_DATE` (defaults to
`CUTOFF_DATE`) and referenced `MIGAJ_PAY_<external id>`.

A second sweep picks up adjustments whose counterpart changed state mid-run. It
searches by `ref like 'MIGAJ_PAY_%'` — the Odoo 17 loader searched by entry name
with the month hardcoded (`MIGAJ/2026/07/`), which silently stopped working the
moment the migration ran in a different month.

---

## 10. Verification

### `export/verificacion19.md` (written by `step_verify`)

Volumes, payments, reconciliation integrity, portfolio. The lines that decide
whether the migration is good:

| Check | Expected |
|---|---|
| Payments with no journal entry | **0** |
| Reconciled lines with residual ≠ 0 | **0** |
| Unreconciled `MIGAJ` adjustments | **0** |
| Odoo residual portfolio vs Profit target | within tolerance |
| Documents whose balance differs from Profit | as low as possible |

The Profit target for this dataset is **USD 14,544,680.74**. Any document
outside a $0.02 tolerance is listed in `export/mismatch19.csv`, sorted by the
size of the difference — start there when investigating.

### `tests_migracion.py 19` (T1–T12)

Compares Odoo against the **raw Profit CSVs**, not against the transform output,
so it catches errors introduced anywhere in the pipeline.

T1 customers · T2 suppliers · T3 products · T4a invoices · T4b debit notes ·
T5 credit notes · T6a total invoiced USD · T6b total credit notes USD ·
T7a collection entries · T7b total collected USD · T8a documents compared ·
**T8b documents whose balance differs** · T8c net portfolio USD ·
T9 customers whose balance differs · T10 stock · T11 rates ·
T12 customers with a tax ID.

Results land in `export/test_odoo19.md`. The runner scripts exit `2` if anything
is red — the migration still completed, but do not sign it off until you have
read the report.

For reference, the validated Odoo 19 run of 2026-07-11 finished with 46 of 6,628
documents differing, about $39,101 in aggregate.

### By hand in the UI

Open <http://localhost:10020> as `admin`/`admin` and:

1. Open a document from `export/mismatch19.csv` and compare its balance against
   Profit.
2. Open a customer holding an advance and confirm the payment is offered as
   credit from the invoice form. This is the single most important manual check
   — it is the behaviour the whole `account.payment` decision exists for.

---

## 11. Known data-quality findings

### Negative stock is faithful to the source — do not "fix" it

Total quantity on hand comes out around **−22,453,940 units**. This is correct:
`export/raw/st_almac.csv` literally contains rows such as
`01,PRODT-0007,-4652017`. Profit carries negative stock because the business
sells without recording every purchase.

Test T10 compares source against Odoo and passes. Adjusting the quantities would
make Odoo disagree with Profit and destroy the audit trail. Record it as a data
quality issue for the business to resolve going forward.

### A residual set of documents will not match

Some documents keep a difference against Profit after the true-up. Causes
observed: rounding across the bolívar round trip, collections referencing
documents absent from `docum_cc`, and voided documents whose voiding was never
recorded. `export/mismatch19.csv` lists them individually.

---

## 12. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `network odoo-19-mns_default not found` | root stack is down | `docker compose up -d` from the repository root first |
| `No .bak file in .../db` | backup missing | copy it into `migration/db/`, see its `README.md` |
| Odoo does not answer on `:10020` | still booting, or crashed | `docker logs odoo-19-mns --tail 50` |
| Database creation fails on Postgres authentication | `odoo.conf` has no `db_user` | pass `-r odoo -w odoo19@2025` explicitly |
| `Object uom.category doesn't exist` | a v17/v18 CSV reached v19 | re-run `transform19.py` |
| `the sum of percentages must be 100%` | payment terms imported twice | delete the duplicated lines and re-run — the loader now skips existing terms |
| `Fault: cannot marshal None` in the log | expected on post/reconcile | nothing; the transaction committed |
| Payments exist but the money does not | `in_process` with no entry | run the `fix_payments` step |
| Receivables in the billions | rates not inverted | re-run `setup`, check `_rates_to_secondary_currency()` |
| `SQL Server did not answer` | container still starting | `restore_mssql.py` waits up to 200 s; check `docker logs migration_mssql` |

---

## 13. Rollback and re-running

Every step is idempotent, so the normal recovery is simply to re-run the step
that failed.

```bash
# just reconciliation, on data already loaded
./migration/run-migration.sh --skip-extract --steps reconcile,crossapply,trueup,verify
```

Targeted tools in `ops/`:

| Script | Use |
|---|---|
| `diag.py` | current state: counts, balances, unreconciled lines |
| `reset_reconcile.py` | undo reconciliation without touching the entries |
| `cleanup_migadj.py` | remove the `MIGAJ` adjustment entries |
| `purge_moves.py` | delete migrated journal entries — destructive |
| `backup_db.py` | restorable zip; `--verify` restores it as `prueba19` and drops it |

Starting completely over is cheapest at the database level:

```bash
docker exec -e PGPASSWORD=odoo19@2025 db-odoo-19-mns \
    dropdb -U odoo profit_migrado19
./migration/run-migration.sh --skip-extract
```

Before anything destructive, take a backup:

```bash
docker exec migration_runner python /migration/ops/backup_db.py --verify
```

It writes `export/profit_migrado19.zip` in Odoo's native format — `dump.sql`,
`manifest.json` and the filestore — restorable straight from
<http://localhost:10020/web/database/manager>. A plain `pg_dump` is not
equivalent: it loses the attachments, and Odoo complains about module versions
on restore.
