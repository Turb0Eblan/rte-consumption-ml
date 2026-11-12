import os

import pandas as pd

from rte_consumption_ml.ml.settings import ON_DATABRICKS


def replace_day(df, table, csv_path, day_col, day):
    if ON_DATABRICKS:
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
        spark.conf.set("spark.sql.session.timeZone", "Europe/Paris")

        if spark.catalog.tableExists(table):
            cols = ", ".join(spark.table(table).columns)
            spark.createDataFrame(df).createOrReplaceTempView("new_rows")
            spark.sql(f"""
            INSERT INTO {table}
            REPLACE WHERE {day_col} = '{day.isoformat()}'
            SELECT {cols} FROM new_rows
            """)
        else:
            spark.createDataFrame(df).write.mode("append").saveAsTable(table)

        print("Replaced", day, "in", table)

    else:
        old = pd.read_csv(csv_path) if os.path.exists(csv_path) else pd.DataFrame()

        if not old.empty:
            old = old[old[day_col] != day.isoformat()]   # drop this day's rows, then write them again
        pd.concat([old, df]).to_csv(csv_path, index=False)

        print("Replaced", day, "in", table)

#================================================================================================

def append_rows(df, table, csv_path):
    # Databricks: append to a Delta table (dashboards read tables)
    # local: append to a CSV file (no Spark here)
    if ON_DATABRICKS:
        from pyspark.sql import SparkSession

        spark = SparkSession.builder.getOrCreate()
        spark.conf.set("spark.sql.session.timeZone", "Europe/Paris")
        spark.createDataFrame(df).write.mode("append").saveAsTable(table)

        print("Appended", len(df), "rows to", table)

    else:
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        df.to_csv(csv_path, mode="a", header=not os.path.exists(csv_path), index=False)

        print("Appended", len(df), "rows to", csv_path)

#================================================================================================

def read_rows(table, csv_path):
    if ON_DATABRICKS:
        from pyspark.sql import SparkSession

        spark = SparkSession.builder.getOrCreate()
        spark.conf.set("spark.sql.session.timeZone", "Europe/Paris")
        return spark.table(table).toPandas()
    return pd.read_csv(csv_path)