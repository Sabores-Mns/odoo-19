# Odoo 19 API notes for this migration

What changed between the Odoo 17/18 loaders and `etl/load19.py`, and where each
difference is absorbed. Everything marked **confirmed** was observed in a real
migration run; everything under *To verify* still needs checking against the
live container.

---

## Confirmed differences

Observed during the validated Odoo 19 run of 2026-07-11 (project
`Migrate Profit Odoo`, see `odoo19/GUIA_ODOO19.md`).

### `uom.category` no longer exists

The model was removed; units of measure are now flat.

- `01_uom_category.csv` is **not loaded** — it is absent from `MASTER_FILES`
  in `etl/load19.py`.
- `02_uom_uom.csv` is reduced to `id,name` by `etl/transform19.py`.

Loading the category file against v19 fails with
`Object uom.category doesn't exist`.

### `res.users.groups_id` → `group_ids`

The many2many to `res.groups` was renamed. `transform19.py` rewrites the column
header. A CSV still carrying `groups_id/id` is rejected at import time.

### `product.template.uom_po_id` removed

Purchase UoM no longer exists as a separate field; `transform19.py` drops the
column.

### `account.payment.term` duplicates its lines on re-import

Re-running the master load without a stable external ID **per line** appends a
second set of lines, and the record then fails validation with *"the sum of
percentages must be 100%"*.

Handled in two places:

- `transform19.py` emits `line_ids/id` for every payment-term line.
- `load19.py` lists `05_account_payment_term.csv` in `SKIP_IF_EXISTS`, so a
  re-run skips terms that already exist instead of updating them.

### `cannot marshal None` faults are expected, and the commit succeeds

`action_post` and `account.move.line.reconcile` return values that
`xmlrpc.client` cannot serialise. The client raises
`Fault: cannot marshal None unless allow_none is enabled`, but **the server has
already committed the transaction**.

This is why `load19.py` has:

```python
def is_marshal_fault(exc):
    return "cannot marshal" in str(exc)
```

and why `post_safe()` and `reconcile_lines()` swallow only that specific fault.
The v17 loader used a bare `except Exception: pass` in `step_trueup`, which also
hid genuine failures; that is corrected here — anything that is not a marshal
fault is counted and logged.

### Products are storable via `is_storable`, not `type='product'`

`transform.py` already emits `type=consu` plus `is_storable=1`, which is the
v18+ model and is unchanged in v19. The `_product_compat()` /
`_adapt_product_columns()` probing the v17 loader did is therefore dead code and
was removed.

### Database creation

`--without-demo` may no longer be accepted; the current flag is `--with-demo`,
and a database created from the CLI has no demo data by default. The creation
command in `run-migration.ps1` / `run-migration.sh` passes neither flag.

`etc/odoo.conf` does **not** contain `db_user` / `db_password` — the container
entrypoint injects them from the environment. A `docker exec odoo …` invocation
bypasses that entrypoint, so `-r odoo -w odoo19@2025` must be passed explicitly
or the command dies with a Postgres authentication error.

---

## Reconciliation: `reconcile()` applies the maximum, not the amount you meant

`account.move.line.reconcile()` is *automatic* matching. Given a set of debit
and credit lines it offsets as much as it can, in its own order. There is no
argument for "apply exactly this much".

That is wrong for a migration. Profit records, per receipt line
(`reng_cob.neto`), exactly how much was applied to each document. Handing Odoo
the whole group and letting it decide meant a partial payment cancelled the
entire invoice — silently erasing debt the customer still owed:

```
FAC 11564 = 13,392.00     receipt 5448 applies 8,572.65     Profit balance 4,819.35
                          automatic reconcile settled all 13,392.00
```

The loader therefore creates `account.partial.reconcile` records directly:

```python
call("account.partial.reconcile", "create", [{
    "debit_move_id": invoice_line_id,
    "credit_move_id": payment_line_id,
    "amount": 8572.65,
    "debit_amount_currency": 8572.65,
    "credit_amount_currency": 8572.65,
}])
```

Confirmed on Odoo 19: the invoice line's `amount_residual` lands on exactly
`4,819.35`. The model also carries `debit_currency_id` / `credit_currency_id`,
and `full_reconcile_id` is assigned automatically once a line reaches zero.

**Source order matters as much as the amounts.** Consume the declared sources
(advances, credit notes) *before* the receipt's own cash, each capped at the
amount Profit assigned it. With the payment first, a receipt holding enough cash
covers the invoices by itself and the credit notes are never consumed — they
stay floating as customer credit even though Profit considers them applied. Real
case: credit note 331 contributes 70,233.23 to a receipt that also carries
78,395.61 in cash.

### Unreconciling

`account.move.line.remove_move_reconcile()` exists and works. It raises the
usual marshalling `Fault` while still committing.

### What is left after all this

Cent-level differences remain on documents Profit still reports as open — three
documents, `-1.19` in total. `trueup` deliberately leaves them: it only zeroes
what Profit considers settled, and adjusting a live balance would be inventing
an entry over real debt.

---

## The database manager speaks JSON-RPC

`/web/database/list` rejects form-encoded bodies with **415 Unsupported Media
Type**; it needs `Content-Type: application/json` and a JSON-RPC envelope.

More importantly, a **restore keeps running on the server after Odoo drops the
HTTP connection**. The client sees `http.client.RemoteDisconnected` while the
database is still being built — it can take another two minutes to appear.
Treating that disconnect as a failure, or checking for the database immediately,
both report a restore that actually succeeded as broken. `ops/backup_db.py`
polls until the database shows up.

---

## Payments: the fragile part

Since Odoo 18, `account.payment` posts to state `in_process` and generates **no
journal entry** when the journal's payment-method line has no outstanding
account. The payment exists; the money does not.

`step_fix_payments` is the fix: it writes
`account.payment.method.line.payment_account_id = journal.default_account_id`
for every inbound method line that lacks one, then re-drafts and re-posts the
affected payments.

Two checks confirm it worked:

- `step_payments` warns if any payment posted without a `move_id`.
- `step_verify` reports **Pagos sin asiento (debe ser 0)**.

If v19 leaves payments without entries even after this fix, the fallback is the
`payment_moves` route — plain `account.move` entries instead of
`account.payment`. That was the route actually validated on 2026-07-11. It costs
the feature the user explicitly asked for (advances usable as credit from the
invoice form), so it is a fallback, not a default.

---

## Verified against the live container — 2026-08-04

Probed against `profit_migrado19` on `odoo-19-mns` (Odoo 19, Docker 29.6.1).
**One assumption was inverted and one was incomplete**; both are recorded here
and handled in the code.

| # | Check | Assumed | **Observed** | Outcome |
|---|---|---|---|---|
| 1 | `account.payment`: `move_id`, `ref`, `state` | `ref` replaces `memo` | **`memo` exists (char, stored); `ref` does NOT exist.** `move_id` many2one; `state` ∈ `draft, in_process, paid, canceled, rejected` | ❌ **assumption inverted** — see below |
| 2 | `account.payment.method.line.payment_account_id` | exists | many2one, present | ✅ `step_fix_payments` valid |
| 3 | `account.account.code` plain char, no `code_store` | plain char | **`code_store` exists** (stored, company-dependent) and `account.code.mapping` is a real model; `code` itself is a non-stored char | ⚠️ incomplete, but harmless — see below |
| 4 | `asset_receivable` / `income_other` in `account_type` | yes | both present | ✅ |
| 5 | `account.move.line.reconcile()` flat id list | yes | model and `reconciled` field present; flat-list call unchanged | ✅ |
| 6 | `res.currency.rate`: `rate` | `rate` | `rate`, `company_rate` and `inverse_company_rate` all present; `rate` still writable | ✅ — and the conflict below is **resolved** |
| 7 | `stock.quant.action_apply_inventory` | exists | method resolves; `inventory_quantity` present | ✅ `step_stock` valid |
| 8 | `/xmlrpc/2/common` and `/xmlrpc/2/object` served | yes | authenticated, `uid=2` | ✅ |

### 1 — `account.payment` keeps `memo`; there is no `ref`

The reverse of what the loader assumed. `read_csv()` renamed the `memo` column
of `16_account_payment_cobros.csv` to `ref`, which would have failed the whole
payments step with *Invalid field 'ref' on model 'account.payment'*. The rename
was removed; `memo` now passes through untouched.

The `ref` values this loader really does use (`MIGAJ_PAY_*` in `step_trueup`
and `step_verify`) live on **`account.move`**, which does have `ref`. Those are
unaffected.

### 3 — `code_store` exists, but `step_setup` still works

`account.account.code` is now a non-stored char backed by the company-dependent
`code_store`, with `account.code.mapping` present. That is the company-dependent
layout the note warned about — but it changes nothing here, because both of the
operations `step_setup` performs were checked directly against the container:

- `search([('code','=','899999')])` executes without error;
- `create({'code': '899999', …})` succeeds and populates `code_store`
  automatically (read-back returns `code='899999'`, `code_store='899999'`).

So account `899999` can still be found and created by code. No change needed.

The probe used to answer 1–7:

```bash
docker exec migration_runner python - <<'PY'
import xmlrpc.client
from config import ODOO
c = xmlrpc.client.ServerProxy(f"{ODOO['url']}/xmlrpc/2/common")
uid = c.authenticate(ODOO['db'], ODOO['user'], ODOO['password'], {})
m = xmlrpc.client.ServerProxy(f"{ODOO['url']}/xmlrpc/2/object", allow_none=True)

def fields(model, names):
    got = m.execute_kw(ODOO['db'], uid, ODOO['password'], model, 'fields_get',
                       [names], {'attributes': ['type']})
    print(model, '->', {k: v['type'] for k, v in got.items()} or 'NONE OF THEM')

fields('account.payment', ['move_id', 'ref', 'memo', 'state'])
fields('account.payment.method.line', ['payment_account_id'])
fields('account.account', ['code', 'code_store', 'account_type'])
fields('res.currency.rate', ['rate', 'company_rate', 'inverse_company_rate'])
fields('stock.quant', ['inventory_quantity'])
PY
```

### The exchange-rate conflict — resolved: there is no conflict

The two writers were compared assuming `load19` stores `1/tasa`. It does not.
Following the value end to end:

| Stage | Currency | Value stored |
|---|---|---|
| `transform.py:289` → `12_res_currency_rate.csv` | `base.USD` | `1 / tasa_v` |
| `load19._rates_to_secondary_currency()` | reassigned to `base.VES` | `1 / (1/tasa_v)` = **`tasa_v`** |
| `ops/sync_bcv.py:45-60` | `VES` | BCV rate = **Bs per USD** |

Both end at **bolívares per dollar on the VES currency record**, so the two use
the *same* convention. The apparent contradiction came from reading `load19`'s
`1/rate` in isolation, without accounting for `transform.py` having already
inverted the value on the way into the CSV.

Test **T11b** now asserts this directly: it takes the most recent USD rate from
`export/raw/tasas.csv` and requires Odoo's VES rate for that date to equal
`tasa_v` (0.1% tolerance). If someone later flips either convention, T11b turns
red instead of the drift going unnoticed.

Note that the original **T11 could never have caught this**: it only compared
*row counts*, never magnitudes — and it counted rates on `USD`, which
`_rates_to_secondary_currency()` leaves permanently empty, so it failed by
construction. It now counts on `VES`.

See `EXCHANGE_DIFFERENCES.md` for why the bi-monetary ledger is set up this way.
