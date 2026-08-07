# Profit Plus 2K8 Sample Data

## Purpose

This directory contains small CSV samples extracted from the `MULTI_A` SQL Server database used by Profit Plus Administrativo 2K8. The files are intended for data discovery, mapping, ETL development, and validation before loading information into Odoo 19.

Each export includes the original database column names and supports the mapping between Profit Plus and Odoo 19.

## Profit Plus 2K8 context

Profit Plus stores commercial and administrative information in a normalized SQL Server database. Its table and column names are abbreviated and mostly written in Spanish. Common prefixes include:

- `co_`: code or business identifier, such as customer, product, warehouse, or salesperson code.
- `des_` / `_des`: description.
- `fec_` / `fecha`: transaction or audit date.
- `monto`, `saldo`, `tot_`: monetary amount, outstanding balance, or total.
- `reng_`: detail line associated with a document header.
- `co_us_*` and `fe_us_*`: user and timestamp audit fields.

## Data relationships

The main relationships represented by these samples are:

```text
clientes ──< factura ──< reng_fac >── art
    │            │                      │
    │            └── vendedor           ├── lin_art
    │                                   ├── cat_art
    ├──< docum_cc                       ├── sub_lin
    │                                   ├── unidades
    └──< cobros ──< reng_cob            ├── prov
                                        └── st_almac >── almacen

moneda ──< tasas
bancos ──< mov_ban
almacen / cash configuration ──< mov_caj
```

`factura` and `cobros` are document headers. `reng_fac` and `reng_cob` contain their detail or application lines. `docum_cc` represents accounts-receivable documents such as invoices, debit notes, credit notes, and advances.

## Query catalog

### 1. `clientes.csv` — Customers

Extracts customer master data used to create Odoo contacts and receivable accounts. It includes the customer code, legal and commercial information, address, contact details, tax identifiers, credit terms, balances, salesperson, zone, segment, and tax classification.

```sql
SELECT TOP 10
       co_cli, cli_des, tipo, direc1, direc2, ciudad, zip, telefonos, fax,
       email, rif, nit, inactivo, fecha_reg, saldo, mont_cre, plaz_pag,
       co_zon, co_seg, co_ven, tipo_iva, contribu, juridico, tipo_per, co_pais
FROM clientes;
```

Primary business key: `co_cli`.

### 2. `prov.csv` — Vendors

Extracts supplier master data for Odoo vendor contacts and payable-related mappings. It includes address, contact information, tax identifiers, status, registration date, balance, payment term, and nationality indicator.

```sql
SELECT TOP 10
       co_prov, prov_des, direc1, direc2, ciudad, zip, telefonos, email,
       rif, nit, inactivo, fecha_reg, saldo, plaz_pag, nacional
FROM prov;
```

Primary business key: `co_prov`.

### 3. `vendedor.csv` — Salespeople

Extracts the salesperson catalog. These records can be mapped to Odoo salespeople or used as historical ownership references on migrated customers and invoices.

```sql
SELECT TOP 10
       co_ven, ven_des, tipo, cedula, telefonos, email, comision, comisionv
FROM vendedor;
```

Primary business key: `co_ven`.

### 4. `art.csv` — Products

Extracts the product master. It includes classification codes, reference and model, main supplier, sales and purchase units, current stock, tax settings, five sales prices, costs, weight, main warehouse, and registration date.

```sql
SELECT TOP 10
       co_art, art_des, co_lin, co_cat, co_subl, co_color, ref, modelo,
       co_prov, uni_venta, uni_compra, stock_act, anulado, tipo, tipo_imp,
       co_imp, prec_vta1, prec_vta2, prec_vta3, prec_vta4, prec_vta5,
       ult_cos_un, cos_pro_un, peso, alm_prin, fecha_reg
FROM art;
```

Primary business key: `co_art`.

### 5. `lin_art.csv` — Product lines

Extracts the high-level product line catalog used to group products.

```sql
SELECT TOP 10 co_lin, lin_des
FROM lin_art;
```

Referenced by `art.co_lin`.

### 6. `cat_art.csv` — Product categories

Extracts Profit product categories for mapping products into Odoo product categories or reporting groups.

```sql
SELECT TOP 10 co_cat, cat_des
FROM cat_art;
```

Referenced by `art.co_cat`.

### 7. `sub_lin.csv` — Product sublines

Extracts the product subline catalog, providing a more detailed classification below the main line.

```sql
SELECT TOP 10 co_subl, subl_des
FROM sub_lin;
```

Referenced by `art.co_subl`.

### 8. `unidades.csv` — Units of measure

Extracts the unit catalog used by products and invoice lines, such as units, boxes, packages, or bulk units.

```sql
SELECT TOP 10 co_uni, des_uni
FROM unidades;
```

Referenced by product and transaction unit fields such as `uni_venta` and `uni_compra`.

### 9. `condicio.csv` — Payment conditions

Extracts payment conditions and their credit-day definitions. These records are used to map Profit credit arrangements to Odoo payment terms.

```sql
SELECT TOP 10 co_cond, cond_des, dias_cred
FROM condicio;
```

Primary business key: `co_cond`.

### 10. `almacen.csv` — Warehouses

Extracts the warehouse catalog. Warehouse codes are referenced by stock balances and invoice detail lines.

```sql
SELECT TOP 10 co_alma, alma_des
FROM almacen;
```

Primary business key: `co_alma`.

### 11. `bancos.csv` — Banks

Extracts the bank catalog used by Profit cash and banking operations.

```sql
SELECT TOP 10 co_ban, des_ban
FROM bancos;
```

Primary business key: `co_ban`.

### 12. `zona.csv` — Customer zones

Extracts geographic or commercial sales zones assigned to customers.

```sql
SELECT TOP 10 co_zon, zon_des
FROM zona;
```

Referenced by `clientes.co_zon`.

### 13. `segmento.csv` — Customer segments

Extracts commercial customer segments used for classification, pricing, reporting, or sales analysis.

```sql
SELECT TOP 10 co_seg, seg_des
FROM segmento;
```

Referenced by `clientes.co_seg`.

### 14. `moneda.csv` — Currencies

Extracts the Profit currency catalog and its current/default exchange value.

```sql
SELECT TOP 10 co_mone, mone_des, cambio
FROM moneda;
```

Primary business key: `co_mone`.

### 15. `tasas.csv` — Historical exchange rates

Extracts dated purchase and sales exchange rates for each currency. These rates support the reconstruction of foreign-currency transactions.

```sql
SELECT TOP 10 co_mone, fecha, tasa_c, tasa_v
FROM tasas;
```

Related to `moneda` through `co_mone`.

### 16. `st_almac.csv` — Stock by warehouse

Extracts the current product quantity stored for each product and warehouse combination.

```sql
SELECT TOP 10 co_alma, co_art, stock_act
FROM st_almac;
```

Composite business key: `co_alma` + `co_art`.

### 17. `factura.csv` — Sales invoice headers

Extracts sales invoice header information, including issue and due dates, customer, salesperson, currency, exchange rate, totals, outstanding balance, document status, control number, tax identity, and comments.

```sql
SELECT TOP 10
       fact_num, fec_emis, fec_venc, fec_reg, co_cli, co_ven, moneda, tasa,
       tot_bruto, glob_desc, tot_reca, tot_flete, iva, tot_neto, saldo,
       anulada, status, num_control, nombre, rif, comentario, descrip
FROM factura;
```

Primary business key: `fact_num`. Related details are stored in `reng_fac`.

### 18. `reng_fac.csv` — Sales invoice lines

Extracts product and amount details for each sales invoice: product, warehouse, quantity, sales unit, price, discount, tax treatment, net line amount, description, and cancellation flag.

```sql
SELECT TOP 10
       fact_num, reng_num, co_art, co_alma, total_art, uni_venta, prec_vta,
       porc_desc, tipo_imp, isv, reng_neto, des_art, anulado
FROM reng_fac;
```

Composite business key: `fact_num` + `reng_num`.

### 19. `docum_cc.csv` — Accounts-receivable documents

Extracts customer receivable documents. Depending on `tipo_doc`, a row may represent an invoice-related balance, debit note, credit note, advance, or another receivable adjustment. Origin fields link derived documents back to their source.

```sql
SELECT TOP 10
       tipo_doc, nro_doc, co_cli, co_ven, fec_emis, fec_venc, fec_reg,
       moneda, tasa, monto_bru, monto_imp, monto_net, saldo, anulado,
       doc_orig, nro_orig, observa, num_control, origen
FROM docum_cc;
```

Common business key: `tipo_doc` + `nro_doc` + `co_cli`.

### 20. `cobros.csv` — Customer receipt headers

Extracts receipt headers for customer collections. It identifies the customer, salesperson, receipt date, amount, discounts, credit-note amount, currency, exchange rate, advance reference, status, and origin.

```sql
SELECT TOP 10
       cob_num, recibo, co_cli, co_ven, fec_cob, anulado, monto, dppago,
       mont_ncr, ncr, tasa, moneda, descrip, adel_num, origen
FROM cobros;
```

Primary business key: `cob_num`. Applications are stored in `reng_cob`.

### 21. `reng_cob.csv` — Receipt applications

Extracts the lines that apply a customer receipt to invoices, notes, or other receivable documents. It contains the target document type and number, applied amount, discounts, credit-note references, currency, exchange rate, and withholding amounts.

```sql
SELECT TOP 10
       cob_num, reng_num, tp_doc_cob, doc_num, neto, dppago, reng_ncr,
       moneda, tasa, monto_reten, ret_iva
FROM reng_cob;
```

Composite business key: `cob_num` + `reng_num`.

### 22. `mov_ban.csv` — Bank movements

Extracts raw bank transactions such as deposits, checks, transfers, receipt-related movements, reconciliation data, currency information, accounting references, audit information, and optional custom fields.

```sql
SELECT TOP 10 *
FROM mov_ban;
```

The query intentionally uses `SELECT *` because the Profit schema may contain version-specific audit and extension fields. The CSV headers preserve the column order returned by the source table.

### 23. `mov_caj.csv` — Cash movements

Extracts raw cash-register movements, including transaction type, payment method, document reference, debit and credit values, associated bank or beneficiary, currency, accounting reference, audit information, transfer metadata, and custom fields.

```sql
SELECT TOP 10 *
FROM mov_caj;
```

As with `mov_ban`, `SELECT *` retains version-specific fields and their physical SQL Server column order.

## Reference

The general table purpose and field terminology follow the Profit Plus Administrativo data dictionary and the structures observed in the `MULTI_A` database:

- Profit Plus data dictionary: <https://es.scribd.com/document/515492685/Diccionario-de-Datos-Profit>
- Project extractor: `migration/etl/extract.py`
