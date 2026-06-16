from src.tools.source_api import (
    detect_source_api,
    source_api_surface,
    source_specific_names,
)


def test_surface_is_derived_from_the_library():
    surface = source_api_surface("pandas")
    assert "sort_values" in surface
    assert "pivot_table" in surface
    assert "read_csv" in surface
    assert source_api_surface("does_not_exist_xyz") == frozenset()


def test_source_specific_excludes_shared_and_generic_names():
    spec = source_specific_names("pandas", "polars")
    # source-only constructs
    assert {"sort_values", "pivot_table", "reset_index", "iterrows"} <= spec
    # shared with polars -> ambiguous -> excluded
    assert "filter" not in spec
    assert "head" not in spec
    # generic container methods -> excluded
    assert "get" not in spec
    assert "items" not in spec


def test_detect_finds_alias_specific_method_and_structural():
    code = (
        "import pandas as pd\n"
        "def f(path):\n"
        "    df = pd.read_csv(path)\n"
        "    df = df[df['x'] > 0]\n"
        "    df['y'] = df['x'] * 2\n"
        "    return df.sort_values('y').reset_index(drop=True)\n"
    )
    names = {h.name for h in detect_source_api(code, "pandas", "polars")}
    assert "read_csv" in names  # alias-qualified
    assert "sort_values" in names  # source-specific method
    assert "reset_index" in names
    assert "boolean_indexing" in names  # structural
    assert "column_assign" in names  # structural


def test_generalist_catches_methods_absent_from_curated_catalog():
    # nlargest / iterrows are pandas-specific but not in the hand-curated
    # pattern_scanner catalog — the introspection detector still catches them.
    code = (
        "def f(df):\n"
        "    top = df.nlargest(5, 'score')\n"
        "    for _, row in df.iterrows():\n"
        "        pass\n"
        "    return top\n"
    )
    names = {h.name for h in detect_source_api(code, "pandas", "polars")}
    assert "nlargest" in names
    assert "iterrows" in names


def test_clean_polars_method_call_is_not_flagged():
    code = (
        "import polars as pl\n"
        "def f(path):\n"
        "    return pl.read_csv(path).with_columns(pl.col('x').alias('y'))\n"
    )
    names = {h.name for h in detect_source_api(code, "pandas", "polars")}
    # no pandas alias, and with_columns/col are not source-specific
    assert "with_columns" not in names
    assert "read_csv" not in names  # it's pl.read_csv, not pd.read_csv
