# Parquet support

## Install

```bash
pip install icm-engine[parquet]
# or
uv pip install icm-engine[parquet]
```

## Usage

Pass `.parquet` files directly — the CLI auto-detects by extension:

```bash
icm --plan plan.yaml --transactions deals.parquet --payees reps.parquet --output ./output
```

## Creating Parquet files from your warehouse

### Snowflake

```sql
COPY INTO @my_stage/deals.parquet
FROM (
  SELECT id, payee_id, deal_id, period, amount, product, close_date
  FROM commissions.transactions_v
)
FILE_FORMAT = (TYPE = PARQUET);
```

### BigQuery

```bash
bq extract --destination_format=PARQUET \
  'project:dataset.transactions_view' \
  gs://bucket/deals.parquet
```

### DuckDB / Pandas

```python
import pandas as pd
df = pd.read_sql("SELECT * FROM transactions_view", conn)
df.to_parquet("deals.parquet")
```

## Column requirements

Same as CSV. See the main README for required fields.

Parquet files are read as-is — no fuzzy column mapping is applied (unlike
XLSX). Column names must match the canonical field names exactly.
