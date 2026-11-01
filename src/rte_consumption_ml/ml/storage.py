import os

import pandas as pd

from rte_consumption_ml.ml.settings import ON_DATABRICKS


def append_rows(df, table, csv_path):
    # Databricks: append to a Delta table (dashboards read tables)
    # local: append to a CSV file (no Spark here)
    if ON_DATABRICKS:
        from pyspark.sql import SparkSession

        spark = SparkSession.builder.getOrCreate()
        spark.createDataFrame(df).write.mode("append").saveAsTable(table)
    else:
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        df.to_csv(csv_path, mode="a", header=not os.path.exists(csv_path), index=False)


def read_rows(table, csv_path):
    if ON_DATABRICKS:
        from pyspark.sql import SparkSession

        spark = SparkSession.builder.getOrCreate()
        return spark.table(table).toPandas()
    return pd.read_csv(csv_path)