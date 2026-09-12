# Seeds

Drop small, mostly-static CSV files here (news/current-events data, industry
lists, whatever reference data you want to join against campaign
performance). Run:

```bash
export DBT_PROFILES_DIR=warehouse
cd warehouse
dbt seed
```

Each `seeds/whatever.csv` becomes a table you can reference in any model as
`{{ ref('whatever') }}`. For anything large or that updates often, prefer a
proper `source` (see `models/staging/`) over a seed — seeds are meant for
small, hand-maintained reference data, not a general data-loading mechanism.
