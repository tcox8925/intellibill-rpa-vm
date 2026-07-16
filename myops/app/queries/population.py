"""Raw SQL constants for Population Dashboard endpoints."""

# ---------------------------------------------------------------------------
# Cards aggregation — latest record per member from pch_member_roster
# ---------------------------------------------------------------------------
CARDS_SQL = """
WITH latest AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY amisys_number ORDER BY report_date DESC
        ) AS rn
    FROM wpo.pch_member_roster
    WHERE (:entity_id IS NULL OR company_id = :entity_id)
      AND (:carrier_id IS NULL OR carrier_id = :carrier_id)
      AND (:report_date IS NULL OR report_date = :report_date)
      AND (:line_of_business IS NULL OR line_of_business = :line_of_business)
      AND (:gender IS NULL OR UPPER(gender) = :gender)
)
SELECT
    COUNT(DISTINCT amisys_number) AS total_members,
    COALESCE(SUM(CAST(NULLIF(member_months, '') AS NUMERIC)), 0) AS member_months,
    AVG(CAST(NULLIF(risk_score, '') AS NUMERIC)) AS avg_risk_score,
    COUNT(DISTINCT CASE
        WHEN LOWER(population_health_category) = 'healthy' THEN amisys_number
    END) AS healthy_count,
    COUNT(DISTINCT CASE
        WHEN LOWER(population_health_category) LIKE 'chronic%%'
         AND LOWER(population_health_category) LIKE '%%1 condition%%'
        THEN amisys_number
    END) AS chronic_1_count,
    COUNT(DISTINCT CASE
        WHEN LOWER(population_health_category) LIKE 'chronic%%'
         AND LOWER(population_health_category) LIKE '%%2%%condition%%'
        THEN amisys_number
    END) AS chronic_2plus_count
FROM latest
WHERE rn = 1
"""

# ---------------------------------------------------------------------------
# Card details — member-level rows (paginated)
# ---------------------------------------------------------------------------
CARD_DETAILS_SQL = """
WITH latest AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY amisys_number ORDER BY report_date DESC
        ) AS rn
    FROM wpo.pch_member_roster
    WHERE (:entity_id IS NULL OR company_id = :entity_id)
      AND (:carrier_id IS NULL OR carrier_id = :carrier_id)
      AND (:report_date IS NULL OR report_date = :report_date)
      AND (:line_of_business IS NULL OR line_of_business = :line_of_business)
      AND (:gender IS NULL OR UPPER(gender) = :gender)
      AND (:product IS NULL OR product = :product)
      AND (:population_health_category IS NULL OR population_health_category = :population_health_category)
      AND (:primary_risk_category IS NULL OR primary_risk_category = :primary_risk_category)
)
SELECT
    amisys_number, first_name, last_name, member_dob, gender,
    line_of_business, product, population_health_category,
    primary_risk_category, risk_score
FROM latest
WHERE rn = 1
  {card_filter}
ORDER BY {sort_by} {sort_order}
LIMIT :page_size OFFSET :offset
"""

CARD_DETAILS_COUNT_SQL = """
WITH latest AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY amisys_number ORDER BY report_date DESC
        ) AS rn
    FROM wpo.pch_member_roster
    WHERE (:entity_id IS NULL OR company_id = :entity_id)
      AND (:carrier_id IS NULL OR carrier_id = :carrier_id)
      AND (:report_date IS NULL OR report_date = :report_date)
      AND (:line_of_business IS NULL OR line_of_business = :line_of_business)
      AND (:gender IS NULL OR UPPER(gender) = :gender)
      AND (:product IS NULL OR product = :product)
      AND (:population_health_category IS NULL OR population_health_category = :population_health_category)
      AND (:primary_risk_category IS NULL OR primary_risk_category = :primary_risk_category)
)
SELECT COUNT(*) AS total
FROM latest
WHERE rn = 1
  {card_filter}
"""

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
FILTERS_REPORT_DATES_SQL = """
SELECT DISTINCT report_date
FROM wpo.pch_member_roster
WHERE company_id = :entity_id
ORDER BY report_date DESC
"""

FILTERS_GENDERS_SQL = """
SELECT DISTINCT gender
FROM wpo.pch_member_roster
WHERE company_id = :entity_id
  AND gender IS NOT NULL AND gender != ''
"""

# ---------------------------------------------------------------------------
# Membership trend (by month)
# ---------------------------------------------------------------------------
MEMBERSHIP_BY_MONTH_SQL = """
SELECT report_date AS month,
       COUNT(DISTINCT amisys_number) AS count
FROM wpo.pch_member_roster
WHERE (:entity_id IS NULL OR company_id = :entity_id)
  AND (:carrier_id IS NULL OR carrier_id = :carrier_id)
  AND (:line_of_business IS NULL OR line_of_business = :line_of_business)
GROUP BY report_date
ORDER BY report_date
"""

# ---------------------------------------------------------------------------
# Financial summary — PMPM KPI (aggregated)
# ---------------------------------------------------------------------------
FIN_SUMMARY_KPI_SQL = """
SELECT
    SUM(CAST(REGEXP_REPLACE(NULLIF(inpatient, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_inpatient,
    SUM(CAST(REGEXP_REPLACE(NULLIF(outpatient, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_outpatient,
    SUM(CAST(REGEXP_REPLACE(NULLIF(primary_care, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_primary_care,
    SUM(CAST(REGEXP_REPLACE(NULLIF(specialty, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_specialty,
    SUM(CAST(REGEXP_REPLACE(NULLIF(net_rx, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_net_rx,
    SUM(CAST(REGEXP_REPLACE(NULLIF(net_other_medical, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_net_other_medical,
    SUM(CAST(REGEXP_REPLACE(NULLIF(member_months, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_member_months
FROM wpo.pch_fin_summary
WHERE (:entity_id IS NULL OR company_id = :entity_id)
  AND (:carrier_id IS NULL OR carrier_id = :carrier_id)
  AND (:report_date IS NULL OR report_date = :report_date)
"""

# ---------------------------------------------------------------------------
# Financial summary — PMPM trend (by month)
# ---------------------------------------------------------------------------
FIN_SUMMARY_TREND_SQL = """
SELECT
    month,
    SUM(CAST(REGEXP_REPLACE(NULLIF(inpatient, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_inpatient,
    SUM(CAST(REGEXP_REPLACE(NULLIF(outpatient, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_outpatient,
    SUM(CAST(REGEXP_REPLACE(NULLIF(primary_care, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_primary_care,
    SUM(CAST(REGEXP_REPLACE(NULLIF(specialty, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_specialty,
    SUM(CAST(REGEXP_REPLACE(NULLIF(net_rx, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_net_rx,
    SUM(CAST(REGEXP_REPLACE(NULLIF(net_other_medical, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_net_other_medical,
    SUM(CAST(REGEXP_REPLACE(NULLIF(member_months, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS sum_member_months
FROM wpo.pch_fin_summary
WHERE (:entity_id IS NULL OR company_id = :entity_id)
  AND (:carrier_id IS NULL OR carrier_id = :carrier_id)
GROUP BY month
ORDER BY month
"""

# ---------------------------------------------------------------------------
# Top Rx expenses
# ---------------------------------------------------------------------------
TOP_RX_SQL = """
SELECT
    drug_desc,
    SUM(CAST(REGEXP_REPLACE(NULLIF(paid_amt, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS total_paid,
    COUNT(*) AS claim_count
FROM wpo.pch_rx_claim
WHERE (:entity_id IS NULL OR entity_id = :entity_id)
  AND (:carrier_id IS NULL OR carrier_id = :carrier_id)
  AND (:report_date IS NULL OR report_date = :report_date)
  AND drug_desc IS NOT NULL AND drug_desc != ''
GROUP BY drug_desc
ORDER BY total_paid DESC
LIMIT 10
"""

# ---------------------------------------------------------------------------
# Top diagnoses (with HCC mapping)
# ---------------------------------------------------------------------------
TOP_DIAGNOSES_SQL = """
SELECT
    mc.primary_diag_code AS diag_code,
    hcc.description,
    COUNT(*) AS claim_count,
    SUM(CAST(REGEXP_REPLACE(NULLIF(mc.paid_amt, ''), '[\\$,()]', '', 'g') AS NUMERIC)) AS total_paid,
    hcc.cms_hcc_v22,
    hcc.cms_hcc_v24,
    hcc.cms_hcc_v28
FROM wpo.pch_med_claims mc
LEFT JOIN wpo.lup_hcc_icd_mapping hcc
    ON mc.primary_diag_code = hcc.diagnosis_code
WHERE (:entity_id IS NULL OR mc.entity_id = :entity_id)
  AND (:carrier_id IS NULL OR mc.carrier_id = :carrier_id)
  AND (:report_date IS NULL OR mc.report_date = :report_date)
  AND mc.primary_diag_code IS NOT NULL AND mc.primary_diag_code != ''
GROUP BY mc.primary_diag_code, hcc.description,
         hcc.cms_hcc_v22, hcc.cms_hcc_v24, hcc.cms_hcc_v28
ORDER BY claim_count DESC
LIMIT 15
"""
