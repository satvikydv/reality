"""Published prices used for cost estimates (USD)."""
SAM_USD_PER_IMAGE = 2.50 / 1000
SPARK_USD_PER_M_IN = 1.25
SPARK_USD_PER_M_OUT = 4.25


def spark_usd(input_tokens: int, output_tokens: int) -> float:
    return input_tokens / 1e6 * SPARK_USD_PER_M_IN + output_tokens / 1e6 * SPARK_USD_PER_M_OUT
