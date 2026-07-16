"""
bob_predictions.py
──────────────────
Daily forecast (midnight CT).
  • Loads the latest actuals from the BOB view on Synapse.
  • Reads stored model params from Postgres.
  • Determines the last month with real data — anything after that is forecast territory.
  • Uses the LATER of last-actual-month and current-calendar-month as the
    effective cutoff, so the forecast window always moves forward even when
    the view hasn't been refreshed yet.
  • Uses stored params (ARIMA or SARIMAX) with log-transform + OEP regressor.
  • Deactivates any forecast rows at or before the effective cutoff.
  • Upserts the next 3 months of predictions to Postgres.

"If March rolls in, that's not a prediction anymore."
  → The forecast window always starts from the month AFTER the effective cutoff.
    Once March data appears in the view, March is no longer forecasted.
    And once the calendar hits April, April is treated as actual even if the
    view hasn't caught up yet — we don't forecast the current month.

Upsert logic
─────────────
  Key: (process_name, metric_name, forecast_month, is_active)
  Same window  → UPDATE values, entity/sub_entity, and bump created_on.
  New window   → INSERT fresh rows. Old months stay as history.

Tables
------
  SOURCE  : analytic_vault.bob_carrier_memberships_vw   (Synapse)
  PARAMS  : wpo.time_series_params                      (Postgres)
  TARGET  : wpo.time_series_forecasts                   (Postgres)
"""

import numpy as np
import pandas as pd
import pytz
from datetime import datetime
from statsmodels.tsa.statespace.sarimax import SARIMAX
from utils.db_utils import get_synapse_connection, get_postgres_connection


# ==========================================================
# CONFIG
# ==========================================================

PROCESS_NAME = "BOB"
VIEW_NAME = "analytic_vault.bob_carrier_memberships_vw"
FORECAST_HORIZON = 3  # months ahead

ENTITY_ID = "270681372"
SUB_ENTITY_ID = "270681372001"


# ==========================================================
# LOAD DATA  (Synapse)
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


def create_oep_series(date_index):
    """Binary OEP regressor — 1 for Nov, Dec, Jan (Open Enrollment Period)."""
    return pd.Series(
        [1 if d.month in (11, 12, 1) else 0 for d in date_index],
        index=date_index,
    )


# ==========================================================
# LOAD ACTIVE PARAMS  (Postgres)
# ==========================================================

def get_active_params(pg_conn):
    query = """
        SELECT *
        FROM wpo.time_series_params
        WHERE process_name = %s
          AND is_active = TRUE
    """
    return pd.read_sql(query, pg_conn, params=[PROCESS_NAME])


# ==========================================================
# FORECAST
# ==========================================================

def forecast_metric(series, params_row, effective_last):
    """
    Log-transform + OEP exog → fit stored order → forecast next 3 months.
    Supports both ARIMA (no seasonal) and SARIMAX (seasonal) params.

    effective_last : pd.Timestamp
        The effective cutoff month (max of last-actual, current-calendar-month).
        Forecast window starts at effective_last + 1 month.
    """

    series = series.dropna().asfreq("MS")

    # --- log transform ---
    series_log = np.log(series)

    # --- historical OEP regressor ---
    oep_hist = create_oep_series(series_log.index)

    # --- build order / seasonal_order from stored params ---
    order = (
        int(params_row["ar_p"]),
        int(params_row["diff_d"]),
        int(params_row["ma_q"]),
    )

    seasonal_order = None
    if params_row["model_type"] == "SARIMAX" and pd.notna(params_row.get("season_length")):
        seasonal_order = (
            int(params_row["seasonal_ar_p"]),
            int(params_row["seasonal_diff_d"]),
            int(params_row["seasonal_ma_q"]),
            int(params_row["season_length"]),
        )

    model = SARIMAX(
        series_log,
        order=order,
        seasonal_order=seasonal_order if seasonal_order else (0, 0, 0, 0),
        exog=oep_hist,
        enforce_stationarity=False,
        enforce_invertibility=False,
    )

    results = model.fit(disp=False)

    # --- how many steps from end-of-data to end-of-forecast-window ---
    # The model forecasts from series end, but we only KEEP months
    # after effective_last.
    data_end = series_log.index[-1]
    forecast_start = effective_last + pd.offsets.MonthBegin(1)

    # Total steps the model needs to project (from data end to last forecast month)
    total_steps = (
        (forecast_start.year - data_end.year) * 12
        + (forecast_start.month - data_end.month)
        + FORECAST_HORIZON - 1
    )

    # Full future index from data_end + 1 month through the last forecast month
    full_future_index = pd.date_range(
        start=data_end + pd.offsets.MonthBegin(1),
        periods=total_steps,
        freq="MS",
    )

    oep_full_future = create_oep_series(full_future_index)

    forecast_obj = results.get_forecast(steps=total_steps, exog=oep_full_future)
    forecast_log = forecast_obj.predicted_mean
    conf_log = forecast_obj.conf_int()

    # --- back-transform ---
    forecast_mean_full = np.exp(forecast_log)
    lower_ci_full = np.exp(conf_log.iloc[:, 0])
    upper_ci_full = np.exp(conf_log.iloc[:, 1])

    # --- trim to only the months we want (effective_last + 1 … + HORIZON) ---
    keep_index = pd.date_range(start=forecast_start, periods=FORECAST_HORIZON, freq="MS")
    forecast_mean = forecast_mean_full.reindex(keep_index)
    lower_ci = lower_ci_full.reindex(keep_index)
    upper_ci = upper_ci_full.reindex(keep_index)

    return forecast_mean, lower_ci, upper_ci


# ==========================================================
# SAVE FORECAST → Postgres  (upsert by forecast window)
# ==========================================================

def save_forecast(pg_conn, forecasts, effective_last):
    """
    1. Deactivate any active forecasts at or before effective_last
       (these months are now considered "actual" territory).
    2. Upsert on (process_name, metric_name, forecast_month, is_active).

    Same window  → UPDATE values, entity/sub_entity, and bump created_on.
    New window   → INSERT fresh rows. Old months stay as history.
    """

    cursor = pg_conn.cursor()
    today = datetime.utcnow().date()

    # --- FIX 1: deactivate stale forecast rows ---
    cursor.execute("""
        UPDATE wpo.time_series_forecasts
        SET is_active = FALSE
        WHERE process_name = %s
          AND is_active = TRUE
          AND forecast_month <= %s
    """, (PROCESS_NAME, effective_last))

    deactivated = cursor.rowcount
    if deactivated:
        print(f"🗑️  Deactivated {deactivated} stale forecast row(s) "
              f"(forecast_month ≤ {effective_last:%Y-%m})")

    inserted = 0
    updated = 0

    for row in forecasts:

        # Try UPDATE first — match on core key, also set entity/sub_entity
        cursor.execute("""
            UPDATE wpo.time_series_forecasts
            SET prediction    = %s,
                lower_ci      = %s,
                upper_ci      = %s,
                horizon_month = %s,
                model_type    = %s,
                created_on    = %s,
                entity_id     = %s,
                sub_entity_id = %s
            WHERE process_name   = %s
              AND metric_name    = %s
              AND forecast_month = %s
              AND is_active = TRUE
        """,
        (
            row["prediction"],
            row["lower_ci"],
            row["upper_ci"],
            row["horizon_month"],
            row["model_type"],
            today,
            ENTITY_ID,
            SUB_ENTITY_ID,
            PROCESS_NAME,
            row["metric_name"],
            row["forecast_month"],
        ))

        if cursor.rowcount == 0:
            # New forecast month — INSERT
            cursor.execute("""
                INSERT INTO wpo.time_series_forecasts
                (id, process_name, metric_name,
                 forecast_month, horizon_month,
                 prediction, lower_ci, upper_ci,
                 model_type, created_on, is_active,
                 entity_id, sub_entity_id)
                VALUES (gen_random_uuid(), %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, TRUE,
                        %s, %s)
            """,
            (
                PROCESS_NAME,
                row["metric_name"],
                row["forecast_month"],
                row["horizon_month"],
                row["prediction"],
                row["lower_ci"],
                row["upper_ci"],
                row["model_type"],
                today,
                ENTITY_ID,
                SUB_ENTITY_ID,
            ))
            inserted += 1
        else:
            updated += 1

    pg_conn.commit()
    cursor.close()

    print(f"✅ Forecasts saved — {updated} updated, {inserted} inserted (Postgres)")


# ==========================================================
# MAIN
# ==========================================================

def run_forecast():

    syn_conn = get_synapse_connection()
    pg_conn = get_postgres_connection()

    # --- load actuals from Synapse ---
    df_raw = load_raw_data(syn_conn)
    monthly = aggregate_monthly(df_raw)
    last_actual = monthly.index.max()

    # --- FIX 2: date-aware forecast window ---
    # Use the LATER of last-actual-month and current-calendar-month.
    # This prevents re-forecasting the current month when the view
    # hasn't been refreshed yet (e.g. April 1st, view still has March).
    ct = pytz.timezone("US/Central")
    current_month = pd.Timestamp(datetime.now(ct).replace(day=1, hour=0,
                                                       minute=0, second=0,
                                                       microsecond=0,
                                                       tzinfo=None))
    effective_last = max(last_actual, current_month)

    print(f"Last month with actual data : {last_actual:%Y-%m}")
    print(f"Current calendar month      : {current_month:%Y-%m}")
    print(f"Effective cutoff            : {effective_last:%Y-%m}")
    print(f"Forecasting {FORECAST_HORIZON} months ahead "
          f"→ {effective_last + pd.offsets.MonthBegin(1):%Y-%m} … "
          f"{effective_last + pd.offsets.MonthBegin(FORECAST_HORIZON):%Y-%m}\n")

    # --- load params from Postgres ---
    params_df = get_active_params(pg_conn)

    if params_df.empty:
        print("⚠️  No active params found in wpo.time_series_params. Run training first.")
        syn_conn.close()
        pg_conn.close()
        return

    forecast_rows = []

    for _, row in params_df.iterrows():

        metric = row["metric_name"]
        series = monthly[metric]

        forecast_mean, lower_ci, upper_ci = forecast_metric(series, row, effective_last)

        for i in range(FORECAST_HORIZON):
            forecast_rows.append({
                "metric_name": metric,
                "forecast_month": forecast_mean.index[i],
                "horizon_month": i + 1,
                "prediction": float(forecast_mean.iloc[i]),
                "lower_ci": float(lower_ci.iloc[i]),
                "upper_ci": float(upper_ci.iloc[i]),
                "model_type": row["model_type"],
            })

        print(f"  {metric}  →  "
              f"{forecast_mean.index[0]:%Y-%m} … {forecast_mean.index[-1]:%Y-%m}  "
              f"(model: {row['model_type']})")

    # --- write to Postgres ---
    save_forecast(pg_conn, forecast_rows, effective_last)

    syn_conn.close()
    pg_conn.close()
    print("✅ Daily forecast complete.")


if __name__ == "__main__":
    run_forecast()