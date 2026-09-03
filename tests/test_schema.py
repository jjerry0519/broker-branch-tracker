import pyarrow as pa
from ingest.schema import BranchFlow, PARQUET_SCHEMA, flows_to_table, table_to_flows


def _sample() -> list[BranchFlow]:
    return [
        BranchFlow("2026-09-03", "twse", "2330", "9200", "凱基台北",
                   120000, 20000, 100000, 1085.0),
        BranchFlow("2026-09-03", "twse", "2330", "1440", "美林",
                   0, 55000, -55000, 1085.0),
    ]


def test_flows_to_table_roundtrip():
    flows = _sample()
    table = flows_to_table(flows)
    assert table.schema.equals(PARQUET_SCHEMA)
    assert table.num_rows == 2
    assert table_to_flows(table) == flows


def test_schema_column_types():
    assert PARQUET_SCHEMA.field("buy_shares").type == pa.int64()
    assert PARQUET_SCHEMA.field("close_price").type == pa.float64()
    assert PARQUET_SCHEMA.field("stock_id").type == pa.string()
