# Bank Statements — Field Documentation

This repository contains bank transaction files exported from four Venezuelan financial institutions.
The following documents the column structure for each bank to facilitate data processing and integration.

---

## Table of Contents

- [Banco Mercantil](#banco-mercantil)
- [Bancaribe](#bancaribe)
- [Banesco](#banesco)
- [Banco de Venezuela](#banco-de-venezuela)
- [Movement Type Glossary](#movement-type-glossary)

---

## Banco Mercantil

**File:** `Mercantil.jpeg`

The statement presents columns without text headers; they are identified by numeric position.

| Column No.  | Field Name                  | Type    | Description |
|:-----------:|-----------------------------|---------|-------------|
| Column1     | Internal identifier         | Numeric | Internal bank/branch code (e.g. `105`) |
| Column2     | Currency/bank code          | Text    | Currency or bank identifier (e.g. `VEB`) |
| Column3     | Account number              | Numeric | Bank account number linked to the transaction |
| **Column4** | **Date**                    | Numeric | Transaction date in `DDMMYYYY` format |
| **Column5** | **Reference**               | Numeric | Unique reference number identifying the transaction |
| **Column6** | **Movement Type**           | Text    | Nature of the movement: `SI`, `NC`, `ND` |
| **Column7** | **Description**             | Text    | Operation detail (e.g. `PAYMENT TO THIRD PARTIES VIA INTERNET`) |
| **Column8** | **Amount**                  | Decimal | Transaction amount |
| **Column9** | **Total Account Balance**   | Decimal | Cumulative account balance after the movement |
| Column10    | Additional field            | Numeric | Category code or operation channel |

### Possible values — Movement Type (Column6)

| Value | Meaning         |
|-------|-----------------|
| `SI`  | Initial Balance |
| `NC`  | Credit Note     |
| `ND`  | Debit Note      |

---

## Bancaribe

**File:** `Bancaribe.jpeg`

The statement presents clear text column headers.

| Column              | Type    | Description |
|---------------------|---------|-------------|
| **Fecha**           | Date    | Transaction date in `DD/MM/YYYY` format |
| **Referencia**      | Numeric | Unique reference number of the movement |
| **Descripcion**     | Text    | Operation detail (e.g. `RECEIVED TRANSFER LBTR – TREASURY`) |
| **Tipo de Movimiento** | Text | Movement classification: `C` (Credit) or `D` (Debit) |
| **Monto**           | Decimal | Monetary value of the transaction |
| **Numero de factura** | Text  | Invoice or document number associated with the operation (may be empty) |
| **Modificacion**    | Text    | Records if there was a subsequent adjustment or modification |

### Possible values — Movement Type

| Value | Meaning                   |
|-------|---------------------------|
| `C`   | Credit — deposit into account |
| `D`   | Debit — charge from account   |

---

## Banesco

**File:** `Banesco.jpeg`

The statement presents clear text column headers.

| Column              | Type    | Description |
|---------------------|---------|-------------|
| **Fecha**           | Text    | Transaction date in `DD de [month] de YYYY` format |
| **Referencia**      | Numeric | Unique reference code assigned by the bank |
| **Descripcion**     | Text    | Movement detail (e.g. `TRF.MB 0134 V017287328 WOO LIN`) |
| **Monto**           | Decimal | Operation amount |
| **Saldo**           | Decimal | Account balance after the movement |
| **Tipo de Movimiento** | Text | Accounting type: `Nota de Credito` or `Nota de Debito` |

### Possible values — Movement Type

| Value             | Meaning                            |
|-------------------|------------------------------------|
| `Nota de Credito` | Credit Note — money entering the account |
| `Nota de Debito`  | Debit Note — money leaving the account   |

---

## Banco de Venezuela

**File:** `Venezuela.jpeg`

The statement presents lowercase headers (programmatic style).

| Column             | Type    | Description |
|--------------------|---------|-------------|
| **fecha**          | Date    | Transaction date in `DD/MM/YYYY` format |
| **referencia**     | Numeric | Movement reference number |
| **concepto**       | Text    | Short operation description (e.g. `PAGO A PRO`, `TRANSF RECI`) |
| **saldo**          | Decimal | Account balance after the movement |
| **monto**          | Decimal | Amount involved in the transaction |
| **tipoMovimiento** | Text    | Operation type: `Nota de Cred` or `Nota de Deb` |
| **rif**            | Text    | Taxpayer ID (Registro de Informacion Fiscal) of the account holder |
| **numeroCuenta**   | Numeric | Full bank account number of the holder |

### Possible values — tipoMovimiento

| Value          | Meaning     |
|----------------|-------------|
| `Nota de Cred` | Credit Note |
| `Nota de Deb`  | Debit Note  |

---

## Movement Type Glossary

Unified reference of values used across all banks:

| Code / Label                                             | Meaning |
|----------------------------------------------------------|---------|
| `SI`                                                     | Initial Balance — opening balance for the statement period |
| `NC` / `C` / `Nota de Credito` / `Nota de Cred`         | **Credit** — incoming funds / deposit into account |
| `ND` / `D` / `Nota de Debito` / `Nota de Deb`           | **Debit** — outgoing funds / charge from account |

---

## Repository Files

| File              | Bank               |
|-------------------|--------------------|
| `Mercantil.jpeg`  | Banco Mercantil    |
| `Bancaribe.jpeg`  | Bancaribe          |
| `Banesco.jpeg`    | Banesco            |
| `Venezuela.jpeg`  | Banco de Venezuela |
| `README.md`       | This document      |

---

> **Note:** Date formats, amounts, and descriptions may vary depending on the report version generated by each bank.
> It is recommended to validate the file header before processing data automatically.
