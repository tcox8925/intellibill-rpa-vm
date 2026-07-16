"""
bob_training.py
───────────────
Monthly retrain (1st of month).
  • Grid-searches ARIMA across (p, d, q).
  • Once 12+ months of raw data, also grid-searches SARIMAX (P, D, Q, s=12).
  • Picks the model with the lowest AIC per metric.

Tables
------
  SOURCE  : analytic_vault.bob_carrier_memberships_vw   (Synapse)
  TARGET  : wpo.time_series_params                      (Postgres)
"""

import itertools
import numpy as np
import pandas as pd
from datetime import datetime
from statsmodels.tsa.statespace.sarimax import SARIMAX
from utils.db_utils import get_synapse_connection, get_postgres_connection


# ==========================================================
# CONFIG
# ==========================================================

PROCESS_NAME = "BOB"
VIEW_NAME = "analytic_vault.bob_carrier_memberships_vw"

METRICS = [
    "total_policies",
    "total_members",
    "new_policies",
    "new_members",
]

# --- ARIMA search space ---
ARIMA_P = [0, 1]
ARIMA_D = [1]
ARIMA_Q = [0, 1]

# --- SARIMAX search space (enabled at 12+ months) ---
SARIMAX_THRESHOLD = 12          # months of data before trying seasonal
SEASONAL_P = [0, 1]
SEASONAL_D = [0, 1]
SEASONAL_Q = [0, 1]
SEASON_LENGTH = 12              # monthly seasonality


# ==========================================================
# LOAD + AGGREGATE  (Synapse)
# ==========================================================

def load_raw_data(syn_conn):
    query = f"""
        SELECT report_date, mem_effective_date,
               contract_count, mem_count
        FROM {VIEW_NAME}
    """
    return pd.read_sql(query, syn_conn)


def aggregate_monthly(df):

    df["report_date"] = pd.to_datetime(df["report_date"])
    df["mem_effective_date"] = pd.to_datetime(df["mem_effective_date"])

    df["report_month"] = df["report_date"].dt.to_period("M").dt.to_timestamp()
    df["effective_month"] = df["mem_effective_date"].dt.to_period("M").dt.to_timestamp()

    monthly = df.groupby("report_month").agg(
        total_policies=("contract_count", "sum"),
        total_members=("mem_count", "sum"),
        new_policies=(
            "contract_count",
            lambda x: x[
                df.loc[x.index, "effective_month"]
                == df.loc[x.index, "report_month"]
            ].sum(),
        ),
        new_members=(
            "mem_count",
            lambda x: x[
                df.loc[x.index, "effective_month"]
                == df.loc[x.index, "report_month"]
            ].sum(),
        ),
    ).sort_index()

    return monthly


# ==========================================================
# GRID SEARCH — ARIMA
# ==========================================================

def grid_search_arima(series, metric_name):
    """Non-seasonal ARIMA grid search. Returns best result dict."""

    best_aic = float("inf")
    best_order = None
    best_res = None

    for order in itertools.product(ARIMA_P, ARIMA_D, ARIMA_Q):
        try:
            model = SARIMAX(
                series,
                order=order,
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            res = model.fit(disp=False)
            print(f"  ARIMA{order}  AIC={res.aic:.2f}")

            if not np.isnan(res.aic) and res.aic < best_aic:
                best_aic = res.aic
                best_order = order
                best_res = res
        except Exception:
            continue

    if best_res is None:
        return None

    return _build_param_row(
        metric_name=metric_name,
        model_type="ARIMA",
        order=best_order,
        seasonal_order=None,
        result=best_res,
        months_used=len(series),
    )


# ==========================================================
# GRID SEARCH — SARIMAX
# ==========================================================

def grid_search_sarimax(series, metric_name):
    """Seasonal ARIMA grid search (s=12). Returns best result dict or None."""

    best_aic = float("inf")
    best_order = None
    best_seasonal = None
    best_res = None

    non_seasonal = list(itertools.product(ARIMA_P, ARIMA_D, ARIMA_Q))
    seasonal = list(itertools.product(SEASONAL_P, SEASONAL_D, SEASONAL_Q))

    for order in non_seasonal:
        for s_order in seasonal:
            # skip the purely non-seasonal case (0,0,0,s) — that's just ARIMA
            if s_order == (0, 0, 0):
                continue
            try:
                model = SARIMAX(
                    series,
                    order=order,
                    seasonal_order=(*s_order, SEASON_LENGTH),
                    enforce_stationarity=False,
                    enforce_invertibility=False,
                )
                res = model.fit(disp=False, maxiter=200)
                label = f"  SARIMAX{order}x{s_order},{SEASON_LENGTH}"
                print(f"{label}  AIC={res.aic:.2f}")

                if not np.isnan(res.aic) and res.aic < best_aic:
                    best_aic = res.aic
                    best_order = order
                    best_seasonal = s_order
                    best_res = res
            except Exception:
                continue

    if best_res is None:
        return None

    return _build_param_row(
        metric_name=metric_name,
        model_type="SARIMAX",
        order=best_order,
        seasonal_order=best_seasonal,
        result=best_res,
        months_used=len(series),
    )


# ==========================================================
# HELPER — build param row dict
# ==========================================================

def _build_param_row(metric_name, model_type, order, seasonal_order, result, months_used):

    import math

    aic = float(result.aic)
    bic = float(result.bic)

    return {
        "metric_name": metric_name,
        "model_type": model_type,
        "ar_p": order[0],
        "diff_d": order[1],
        "ma_q": order[2],
        "seasonal_ar_p": seasonal_order[0] if seasonal_order else None,
        "seasonal_diff_d": seasonal_order[1] if seasonal_order else None,
        "seasonal_ma_q": seasonal_order[2] if seasonal_order else None,
        "season_length": SEASON_LENGTH if seasonal_order else None,
        "aic": aic if not math.isnan(aic) else None,
        "bic": bic if not math.isnan(bic) else None,
        "months_used": months_used,
    }


# ==========================================================
# TUNE — pick best model for a metric
# ==========================================================

def tune_metric(series, metric_name):
    """
    Always tries ARIMA.
    If 12+ months of data, also tries SARIMAX.
    Returns whichever has the lower AIC.
    """

    series = series.dropna().asfreq("MS")
    months = len(series)

    print(f"\n{'='*50}")
    print(f"Training  {metric_name}  ({months} months)")
    print(f"{'='*50}")

    best = grid_search_arima(series, metric_name)

    if months >= SARIMAX_THRESHOLD:
        print(f"\n  ↳ {months} months ≥ {SARIMAX_THRESHOLD} — also trying SARIMAX …")
        sarimax = grid_search_sarimax(series, metric_name)

        if sarimax and sarimax["aic"] < best["aic"]:
            print(f"  🏆 SARIMAX wins  (AIC {sarimax['aic']:.2f} < {best['aic']:.2f})")
            best = sarimax
        else:
            print(f"  🏆 ARIMA holds   (AIC {best['aic']:.2f})")
    else:
        print(f"  ↳ {months} months < {SARIMAX_THRESHOLD} — skipping SARIMAX")

    return best


# ==========================================================
# SAVE PARAMETERS → Postgres
# ==========================================================

def save_parameters(pg_conn, param_rows):

    cursor = pg_conn.cursor()

    # Deactivate previous active params for this process
    cursor.execute("""
        UPDATE wpo.time_series_params
        SET is_active = FALSE
        WHERE process_name = %s
          AND is_active = TRUE
    """, (PROCESS_NAME,))

    for row in param_rows:
        cursor.execute("""
            INSERT INTO wpo.time_series_params
            (id,
             process_name, metric_name, model_type,
             ar_p, diff_d, ma_q,
             seasonal_ar_p, seasonal_diff_d, seasonal_ma_q, season_length,
             aic, bic, months_used, trained_on, is_active)
            VALUES (gen_random_uuid(),
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, TRUE)
        """,
        (
            PROCESS_NAME,
            row["metric_name"],
            row["model_type"],
            row["ar_p"],
            row["diff_d"],
            row["ma_q"],
            row["seasonal_ar_p"],
            row["seasonal_diff_d"],
            row["seasonal_ma_q"],
            row["season_length"],
            row["aic"],
            row["bic"],
            row["months_used"],
            datetime.utcnow(),
        ))

    pg_conn.commit()
    cursor.close()

    print("\n✅ Parameters saved to wpo.time_series_params (Postgres)")


# ==========================================================
# MAIN
# ==========================================================

def run_training():

    syn_conn = get_synapse_connection()
    pg_conn = get_postgres_connection()

    # --- read from Synapse ---
    df_raw = load_raw_data(syn_conn)
    monthly = aggregate_monthly(df_raw)

    print(f"\nTotal months of data: {len(monthly)}")

    param_rows = []
    for metric in METRICS:
        param_rows.append(tune_metric(monthly[metric], metric))

    # --- write to Postgres ---
    save_parameters(pg_conn, param_rows)

    syn_conn.close()
    pg_conn.close()
    print("✅ Training complete.")


if __name__ == "__main__":
    run_training()