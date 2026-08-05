# Profit Plus → Odoo 19 迁移

将 **Profit Plus Administrativo 2K**（SQL Server）迁移到
**Odoo 19 Community** 的完整操作手册。

英文版本：[`MIGRATION_EN.md`](MIGRATION_EN.md)。

---

## 1. 概述与源系统

| | |
|---|---|
| 源系统 | Profit Plus Administrativo 2K |
| 源数据库引擎 | Microsoft SQL Server 2019 |
| 源数据库 | `MULTI_A`（逻辑文件 `GLOBAL_A`、`GLOBAL_A_log`） |
| 输入文件 | 一个原生 `.bak`，约 220 MB，放在 `migration/db/` |
| 目标 | Odoo 19 Community，数据库 `profit_migrado19` |
| 访问地址 | <http://localhost:10020> — 用户 `admin`，密码 `admin` |
| 公司本位币 | USD，VES 作为第二货币 |
| 通信方式 | XML-RPC（`/xmlrpc/2/common`、`/xmlrpc/2/object`） |
| 耗时 | 全流程约 2–3 小时 |

整个迁移是**幂等的**。每条记录都带有 `__import__.profit_*` 形式的
external ID，因此任何步骤都可以重复执行而不会产生重复数据。

---

## 2. 数据清单

`etl/extract.py` 从 `MULTI_A` 中导出 24 张表到 `export/raw/*.csv`。
其中与会计核算直接相关的是：

| Profit 表 | 含义 | 对应到 |
|---|---|---|
| `clientes` | 客户 | `res.partner` |
| `prov_ben` | 供应商 | `res.partner` |
| `art`、`st_almac` | 商品、各仓库库存 | `product.template`、`stock.quant` |
| `factura`、`reng_fac` | 销售发票及其明细行 | `account.move`（`out_invoice`） |
| `docum_cc` | 应收账款台账 —— 每个未结项一行 | 驱动核销与尾差调整 |
| `cobros`、`reng_cob` | 收款单及其核销对象 | `account.payment` + 核销 |
| `tasas` | 汇率 | `res.currency.rate` |

余额以 `docum_cc` 为准。其 `tipo_doc` 取值：

| `tipo_doc` | 含义 | Odoo 中的 external ID 前缀 |
|---|---|---|
| `FACT`、`GIRO`、`CHDV` | 发票 | `profit_fac_` |
| `N/DB` | 借项通知单 | `profit_nd_` |
| `N/CR` | 贷项通知单 | `profit_nc_` |
| `ADEL` | 客户预收款 | 没有自己的单据 —— 挂在 `profit_cob_<nro_orig>` 上 |
| `AJPA` | 付款调整 | 同 `ADEL` |

`ADEL` 与 `AJPA` **没有独立单据**，它们指回产生自己的那张收款单。
这就是为什么凡是涉及预收款的地方都会出现 `profit_cob_*` 这种 external ID。

---

## 3. 架构

两套 Docker Compose 栈，共用同一个网络。

```
                     网络 odoo-19-mns_default
   ┌───────────────────────────────┬──────────────────────────────┐
   │  docker-compose.yml（根目录） │  migration/docker-compose.   │
   │                               │       migration.yml          │
   │  db-odoo-19-mns  postgres:18  │  migration_mssql   SQL 2022  │
   │  odoo-19-mns     odoo:19      │  migration_runner  python    │
   │      :10020                   │        │                     │
   └───────────────────────────────┴────────┼─────────────────────┘
                    ▲                       │
                    └──── XML-RPC ──────────┘
                       odoo-19-mns:8069
```

本项目**不修改**根目录的那套栈。迁移栈是一个**独立文件**，以 `external`
方式接入根网络。这样拆分是刻意的：`.github/workflows/deploy.yml` 会在推送到
`main` 时用一条普通的 `docker compose up -d` 部署，而它只读取根目录的文件，
所以 SQL Server 和迁移容器永远不会在生产环境里启动。

迁移容器通过容器名访问 Odoo（`odoo-19-mns:8069`）和 Postgres
（`db-odoo-19-mns:5432`）。

---

## 4. 前置条件与凭据

- Docker Desktop 已运行，本地已有 `odoo:19` 和 `postgres:18` 镜像。
- Profit 的 `.bak` 已放入 `migration/db/`。文件名任意，只要以 `.bak` 结尾，
  脚本会自动识别。**只能放一个**，两个或以上会直接报错。
- 至少 4 GB 可用磁盘空间，供 SQL Server 及还原后的数据库使用。

| 服务 | 用户 | 密码 |
|---|---|---|
| Odoo 19 | `admin` | `admin` |
| Odoo 主密码 | — | `admin`（`etc/odoo.conf`） |
| Postgres（Odoo） | `odoo` | `odoo19@2025` |
| SQL Server | `sa` | `Profit2Odoo!2026` |

> 这些是开发用凭据，以明文提交在仓库中。在接入任何不受你控制的网络之前，
> 请先轮换它们。

### `.bak` 绝不会被提交

`migration/db/` 已从 git 中排除。仓库 `.gitignore` 中的规则：

```gitignore
migration/db/*
!migration/db/README.md
!migration/db/.gitkeep
```

已用 `git check-ignore -v migration/db/<文件>.bak` 验证，返回
`.gitignore:11`。目录本身通过 `.gitkeep` 保留在仓库里，因此新克隆的仓库
仍然有地方放备份文件。

该文件包含真实的客户与开票数据，且远超 GitHub 的实际体积限制。
如果你要修改这些规则，**提交前**务必先用 `git status` 确认：一个 220 MB
的文件一旦误推，即使后来删除也会永远留在 git 历史中，清除它意味着要重写
整个仓库的历史。

---

## 5. 操作手册

### 一条命令

```powershell
.\migration\run-migration.ps1
```

```bash
./migration/run-migration.sh
```

两者执行同样的七个阶段，返回同样的退出码：`0` 成功，`1` 迁移中断，
`2` 迁移完成但有测试未通过。

常用参数：

| PowerShell | Bash | 作用 |
|---|---|---|
| `-Steps a,b,c` | `--steps a,b,c` | 只执行指定的加载步骤 |
| `-SkipExtract` | `--skip-extract` | 复用 `export/` 中已有的 CSV |
| `-Force` | `--force` | 即使 `MULTI_A` 已存在也重新还原 `.bak` |
| `-SkipTests` | `--skip-tests` | 跳过 T1–T12 测试套件 |

例如，只重跑核销部分：

```powershell
.\migration\run-migration.ps1 -SkipExtract -Steps reconcile,crossapply,trueup,verify
```

### 手工执行同样的流程

```bash
# 1. 基础设施
docker compose up -d                                        # 在仓库根目录
docker compose -f migration/docker-compose.migration.yml up -d

# 2. 依赖
docker exec migration_runner pip install -r /migration/requirements.txt

# 3. 还原 Profit 备份
docker exec migration_runner python /migration/etl/restore_mssql.py

# 4. 创建 Odoo 数据库
docker exec odoo-19-mns odoo -c /etc/odoo/odoo.conf -r odoo -w odoo19@2025 \
    -d profit_migrado19 -i base,contacts,product,sale_management,stock,account,l10n_ve \
    --load-language=es_419 --stop-after-init --no-http

# 5. ETL
docker exec migration_runner python -u /migration/etl/extract.py
docker exec migration_runner python -u /migration/etl/transform.py
docker exec migration_runner python -u /migration/etl/transform19.py
docker exec migration_runner python -u /migration/etl/load19.py

# 6. 校验
docker exec migration_runner python -u /migration/etl/tests_migracion.py 19
```

`restore_mssql.py` 通过 `RESTORE FILELISTONLY` 从备份文件中读取真实的逻辑
文件名，而不是写死 `GLOBAL_A` / `GLOBAL_A_log`，因此换成另一家 Profit
公司的备份也无需改任何配置。

---

## 6. 处理流程

```
 MULTI_A ──extract.py───▶ export/raw/*.csv          24 张表，原样导出
         ──transform.py─▶ export/odoo_csv/*.csv     Odoo 导入格式
                        ▶ export/plan/*.json        核销计划
         ──transform19.py▶ export/odoo_csv19/*.csv  Odoo 19 适配
         ──load19.py────▶ Odoo，通过 XML-RPC
```

`load19.py` 按以下顺序执行十个步骤：

| # | 步骤 | 作用 |
|---|---|---|
| 1 | `setup` | 公司、货币、日记账，以及 `MIGAJ` 调整日记账和 `899999` 科目 |
| 2 | `masters` | 业务伙伴、商品、税、付款条款 |
| 3 | `invoices` | 发票、贷项通知单、借项通知单，并过账 |
| 4 | `payments` | 每张真实收款单生成一条 `account.payment`，并过账 |
| 5 | `fix_payments` | 为停留在 `in_process` 的付款补上会计凭证 |
| 6 | `reconcile` | 按 `reconcile_plan.json` 把每张收款单与其核销的单据对冲 |
| 7 | `crossapply` | 处理无现金流的单据对单据抵销 |
| 8 | `trueup` | 把剩余尾差计入 `MIGAJ` |
| 9 | `stock` | 仓库与库存数量 |
| 10 | `verify` | 生成 `export/verificacion19.md` |

**有两处顺序不能颠倒。** `fix_payments` 必须排在 `payments` 之后，
因为没有会计凭证的付款根本没有分录行可供核销；`crossapply` 必须排在
`trueup` **之前**，这样 `MIGAJ` 调整才只吸收真正的汇兑差额，而不会把真实
业务抵销当成尾差一笔勾销。

只跑其中几步，直接写出步骤名：

```bash
docker exec migration_runner python -u /migration/etl/load19.py reconcile crossapply
```

---

## 7. Odoo 19 的特殊之处

完整细节见 [`ODOO19_API_NOTES.md`](ODOO19_API_NOTES.md)。简要如下：

| 变化 | 处理方式 |
|---|---|
| `uom.category` 已移除 | 不再加载 `01_uom_category.csv`；`02_uom_uom.csv` 精简为 `id,name` |
| `res.users.groups_id` → `group_ids` | 由 `transform19.py` 重命名 |
| `product.template.uom_po_id` 已移除 | 由 `transform19.py` 删除该列 |
| `account.payment.term` 重复导入会重复行 | 每行一个 external ID，加上加载器中的 `SKIP_IF_EXISTS` |
| `account.payment` 过账为 `in_process` 且无凭证 | `step_fix_payments` 设置 `payment_account_id` 并重新过账 |
| 过账/核销时报 `Fault: cannot marshal None` | 显式容忍 —— 服务端其实已经提交 |

最后一条值得强调。XML-RPC 无法序列化 `action_post` 和 `reconcile` 的返回值，
因此客户端会在事务**已经写入之后**抛出异常。`load19.py` 只吞掉这一种异常：

```python
def is_marshal_fault(exc):
    return "cannot marshal" in str(exc)
```

Odoo 17 的加载器在这里用的是裸 `except Exception: pass`，连真正的失败也一并
掩盖了。本版本会把其他异常记录下来并计数。

---

## 8. 货币模型

公司账套本位币是 **USD**，**VES** 为第二货币，按「每美元多少玻利瓦尔」报价。

Profit 按当日汇率保存每笔收款的玻利瓦尔等值，而 Odoo 的汇率方向正好相反 ——
它存的是相对本位币的乘数。因此
`load19.py::_rates_to_secondary_currency()` 会把每一条 `base.USD` 汇率记录
改写成 `base.VES`，并取 `rate = 1/rate`。

这不是外观问题。不做这个反转，一笔 1290 万美元的应收会显示成
**47.24 亿**，所有核销比对都失去意义。

由此带来的后果是：以玻利瓦尔支付的单据，换算回美元时很少能刚好等于原始金额。
这些尾数就是真实的汇兑差额，由第 8 步（`trueup`）吸收。完整分析见
[`EXCHANGE_DIFFERENCES.md`](EXCHANGE_DIFFERENCES.md)。

> ⚠️ `ops/sync_bcv.py` 写入 `res.currency.rate` 时**没有**做反转。
> 两种约定必有一种是错的。在测试 T11 确认之前，不要把 `sync_bcv.py`
> 设为定时任务。

---

## 9. 预收款与核销

这才是整个迁移真正的重点，涉及三套机制。

### 预收款（`ADEL`）作为可用信用

收款单被加载为原生的 `account.payment` 记录，而不是裸的会计凭证。
正因如此，未核销的预收款才会在客户发票界面上显示为可用信用，
而不是一条毫无作用的会计分录。

预收款在 Profit 中没有自己的单据：`docum_cc` 里 `ADEL` 类型的行通过
`nro_orig` 指向收款单，所以在 Odoo 中它的余额挂在
`profit_cob_<nro_orig>` 上。

验收标准：

- `partner_type = customer` 的 `account.payment` 数量与真实收款单数量一致，
  且**不为零**。
- 过账后没有会计凭证的付款数量：**0**。
- Odoo 中未核销的预收款信用，与 `docum_cc` 中仍有余额的 `ADEL` 行相符。

### 交叉核销（`crossapply`）

Profit 把单据对单据的抵销记为**金额为零**的收款单 —— 例如第 2909 号收款单，
它用贷项通知单 447 冲销借项通知单 132，金额 $343,098.91，实际没有一分钱流动。

`reconcile_plan.json` 把这类收款排除在外（约 824 张收款单、3,485 行、
合计约 $30.1M），所以在这个步骤存在之前，那些单据在 Odoo 里一直挂着全额未结。
`step_crossapply` 直接从原始 CSV 中找出它们 —— 未作废且
`abs(monto) < 0.0001` 的收款单 —— 按收款单分组，并对涉及两个或以上不同单据的
分组执行核销。

### 尾差调整（`trueup`）与 `MIGAJ` 日记账

第 6、7 步之后仍然剩下的就是汇兑差额。对于每一条 Profit 认为已结清、
但 Odoo 仍显示未结的应收分录行，`trueup` 会生成一张两行的凭证：

- 一行冲原应收科目，金额为剩余尾差；
- 另一行记入 **`899999`** 科目（`income_other`），属于 **`MIGAJ`** 日记账；

然后把两者核销。凭证日期取 `config.ADJ_DATE`（默认等于 `CUTOFF_DATE`），
参考号为 `MIGAJ_PAY_<external id>`。

之后还有一轮扫描，用来处理执行过程中对手方状态发生变化、因而没能核销掉的
调整凭证。这轮扫描按 `ref like 'MIGAJ_PAY_%'` 查找 —— Odoo 17 的加载器是按
凭证名称查找、而且把月份写死成 `MIGAJ/2026/07/`，只要换个月份执行，
这一步就悄无声息地失效了。

---

## 10. 校验

### `export/verificacion19.md`（由 `step_verify` 生成）

包含数量、付款、核销完整性和应收账款四部分。决定迁移是否合格的几行：

| 检查项 | 期望值 |
|---|---|
| 没有会计凭证的付款 | **0** |
| 已核销但剩余金额 ≠ 0 的分录行 | **0** |
| 未核销的 `MIGAJ` 调整凭证 | **0** |
| Odoo 应收余额 vs Profit 目标值 | 在容差范围内 |
| 与 Profit 余额不一致的单据数 | 越少越好 |

本数据集的 Profit 目标值为 **USD 14,544,680.74**。超出 $0.02 容差的单据会
写入 `export/mismatch19.csv`，按差额大小排序 —— 排查时从这里开始。

### `tests_migracion.py 19`（T1–T12）

它拿 Odoo 与 **Profit 的原始 CSV** 对比，而不是与 transform 的输出对比，
因此能发现流程中任何一环引入的错误。

T1 客户 · T2 供应商 · T3 商品 · T4a 发票 · T4b 借项通知单 ·
T5 贷项通知单 · T6a 开票总额 USD · T6b 贷项通知单总额 USD ·
T7a 收款凭证 · T7b 收款总额 USD · T8a 已比对单据数 ·
**T8b 余额不一致的单据数** · T8c 应收净额 USD ·
T9 余额不一致的客户数 · T10 库存 · T11 汇率 · T12 有税号的客户数。

结果写入 `export/test_odoo19.md`。只要有测试未通过，运行脚本就以 `2` 退出 ——
迁移本身仍然跑完了，但在读完报告之前不要签字确认。

作为参考，2026-07-11 那次已验证的 Odoo 19 迁移结果是：6,628 张单据中有 46 张
存在差异，合计约 $39,101。

### 在界面上人工检查

用 `admin`/`admin` 打开 <http://localhost:10020>，然后：

1. 打开 `export/mismatch19.csv` 中的某张单据，与 Profit 核对余额。
2. 打开一个有预收款的客户，确认该付款在发票界面上被列为可用信用。
   **这是最重要的一项人工检查** —— 选择 `account.payment` 方案的全部意义
   就在于此。

---

## 11. 已知的数据质量问题

### 负库存忠实于源系统 —— 不要「修正」它

库存总量约为 **−22,453,940**。这是正确的：`export/raw/st_almac.csv` 中确实
存在 `01,PRODT-0007,-4652017` 这样的行。Profit 之所以有负库存，是因为该企业
在没有完整登记采购的情况下就发生了销售。

测试 T10 会把源数据与 Odoo 对比，结果是通过。调整这些数量反而会让 Odoo 与
Profit 不一致，并破坏审计线索。应把它作为数据质量问题记录下来，交由业务方
今后改进。

### 仍会有少量单据对不上

尾差调整之后，仍有部分单据与 Profit 存在差异。已观察到的原因包括：
玻利瓦尔来回换算产生的舍入误差、收款单引用了 `docum_cc` 中不存在的单据、
以及已作废但作废动作从未被记录的单据。
`export/mismatch19.csv` 会逐条列出。

---

## 12. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `network odoo-19-mns_default not found` | 根目录的栈没起来 | 先在仓库根目录执行 `docker compose up -d` |
| `No .bak file in .../db` | 缺少备份文件 | 复制到 `migration/db/`，参见该目录的 `README.md` |
| `:10020` 没有响应 | 仍在启动，或已崩溃 | `docker logs odoo-19-mns --tail 50` |
| 创建数据库时 Postgres 认证失败 | `odoo.conf` 里没有 `db_user` | 显式传入 `-r odoo -w odoo19@2025` |
| `Object uom.category doesn't exist` | v17/v18 的 CSV 被送进了 v19 | 重新执行 `transform19.py` |
| `the sum of percentages must be 100%` | 付款条款被导入了两次 | 删掉重复的行后重跑 —— 加载器现在会跳过已存在的条款 |
| 日志里出现 `Fault: cannot marshal None` | 过账/核销时的预期现象 | 无需处理，事务已提交 |
| 付款记录存在，但钱没进账 | 停留在 `in_process` 且无凭证 | 执行 `fix_payments` 步骤 |
| 应收金额出现「亿」级数字 | 汇率没有反转 | 重跑 `setup`，检查 `_rates_to_secondary_currency()` |
| `SQL Server did not answer` | 容器仍在启动 | `restore_mssql.py` 最多等待 200 秒；查看 `docker logs migration_mssql` |

---

## 13. 回滚与重跑

每个步骤都是幂等的，所以常规的恢复方式就是重跑失败的那一步。

```bash
# 只重跑核销，复用已加载的数据
./migration/run-migration.sh --skip-extract --steps reconcile,crossapply,trueup,verify
```

`ops/` 下的专用工具：

| 脚本 | 用途 |
|---|---|
| `diag.py` | 查看当前状态：数量、余额、未核销分录 |
| `reset_reconcile.py` | 撤销核销，但不动凭证本身 |
| `cleanup_migadj.py` | 删除 `MIGAJ` 调整凭证 |
| `purge_moves.py` | 删除迁移生成的会计凭证 —— 破坏性操作 |
| `backup_db.py` | 生成可还原的 zip；加 `--verify` 会还原为 `prueba19` 再删除 |

彻底重来最省事的做法是直接从数据库层面处理：

```bash
docker exec -e PGPASSWORD=odoo19@2025 db-odoo-19-mns \
    dropdb -U odoo profit_migrado19
./migration/run-migration.sh --skip-extract
```

在任何破坏性操作之前，先做一次备份：

```bash
docker exec migration_runner python /migration/ops/backup_db.py --verify
```

它会生成 Odoo 原生格式的 `export/profit_migrado19.zip` —— 包含 `dump.sql`、
`manifest.json` 和 filestore —— 可以直接从
<http://localhost:10020/web/database/manager> 还原。单纯的 `pg_dump` 不等价：
它会丢失附件，而且还原时 Odoo 会抱怨模块版本不匹配。
