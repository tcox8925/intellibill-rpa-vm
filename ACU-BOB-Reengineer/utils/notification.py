# ==========================================================
#  utils/notification.py
# ==========================================================
"""
notification.py
---------------
Purpose:
    Build plain-text and HTML notification content for the
    ACU pipeline run report.

    Plain text is used for console logging.
    HTML is sent via Teams channel email (dark-mode safe).
"""

from html import escape as esc
from typing import List, Dict, Any, Optional


# ==========================================================
#  CARRIER CLASSIFICATION
# ==========================================================
def _classify_carriers(all_metrics):
    halted = [m for m in all_metrics if m["status"] == "value_change"]
    errored = [m for m in all_metrics if m["status"] == "error"]
    no_data = [m for m in all_metrics if m["status"] in ("no_data", "no_contracts")]
    processed = [m for m in all_metrics if m["status"] in ("success", "threshold_exceeded")]
    zero_result = [m for m in processed if m["results_count"] == 0]
    high_exc = [m for m in processed if m["exception_rate"] >= 10 and m["results_count"] > 0]
    elevated = [m for m in processed if 5 <= m["exception_rate"] < 10 and m["results_count"] > 0]
    clean = [m for m in processed if m["exception_rate"] < 5 and m["results_count"] > 0]
    return {
        "halted": halted, "errored": errored, "no_data": no_data,
        "zero_result": zero_result, "high_exc": high_exc,
        "elevated": elevated, "clean": clean,
    }


def _carrier_detail_line(m):
    contracts = m.get("contracts_loaded", "?")
    cov = ""
    if isinstance(contracts, int) and contracts > 0 and m["total_rows"] > 0:
        cov = f", contracts={contracts:,} ({round(contracts / m['total_rows'] * 100)}% coverage)"
    cats = m.get("exception_categories", {})
    top_cat = max(cats, key=cats.get) if cats else ""
    return (
        f"{m['carrier_name']}: {m['results_count']:,} agents, "
        f"{m['exceptions_count']:,} exc ({m['exception_rate']}%)"
        f"{cov}"
        f"{f', primary: {top_cat}' if top_cat else ''}"
    )


def _run_totals(all_metrics):
    total_rows = sum(m["total_rows"] for m in all_metrics)
    total_agents = sum(m["results_count"] for m in all_metrics)
    total_exc = sum(m["exceptions_count"] for m in all_metrics)
    total_missing = sum(m["missing_count"] for m in all_metrics)
    rate = round(total_exc / total_rows * 100, 1) if total_rows > 0 else 0
    return total_rows, total_agents, total_exc, total_missing, rate


# ==========================================================
#  PLAIN TEXT (for console logging)
# ==========================================================
def build_notification(all_metrics, run_date_str, uploaded, new_carriers, deactivated,
                       ai_text="", test_mode=False, pending_mappings=None,
                       skipped_inactive=None, has_attachments=False):
    mode = " [TEST]" if test_mode else ""
    total_rows, total_agents, total_exc, total_missing, rate = _run_totals(all_metrics)
    classified = _classify_carriers(all_metrics)
    halted, errored, no_data = classified["halted"], classified["errored"], classified["no_data"]
    zero_result, high_exc, elevated, clean = (
        classified["zero_result"], classified["high_exc"],
        classified["elevated"], classified["clean"],
    )

    lines = []
    lines.append(f"ACU Processing Report{mode}")
    lines.append(f"Run Date: {run_date_str}")
    lines.append("=" * 60)
    lines.append(
        f"\nCarriers: {len(all_metrics)}  |  Rows: {total_rows:,}  |  "
        f"Agents: {total_agents:,}  |  Exceptions: {total_exc:,} ({rate}%)  |  "
        f"Missing: {total_missing:,}"
    )

    if new_carriers:
        lines.append(f"\nNEW CARRIERS ({len(new_carriers)}):")
        for nc in new_carriers:
            lines.append(f"  - {nc['file_info']['file_name']} (pending review)")

    if deactivated:
        lines.append(f"\nDEACTIVATED ({len(deactivated)}):")
        for d in deactivated:
            lines.append(f"  - {d['carrier_name']}: {d['reason']}")

    if pending_mappings:
        lines.append(f"\nAWAITING MAPPING REVIEW ({len(pending_mappings)}):")
        for pm in pending_mappings:
            date_str = f", detected {pm['detected_date']}" if pm.get("detected_date") else ""
            lines.append(f"  - {pm['file_name']}: {pm['column_count']} columns ({pm['status']}{date_str})")

    if skipped_inactive:
        lines.append(f"\nSKIPPED - INACTIVE ({len(skipped_inactive)}):")
        for s in sorted(skipped_inactive, key=lambda x: x["carrier_name"]):
            lines.append(f"  - {s['carrier_name']} (file: {s['file_name']})")

    if halted or errored:
        lines.append(f"\nREQUIRES IMMEDIATE ATTENTION:")
        for m in halted:
            lines.append(f"  - {m['carrier_name']}: Halted (value map mismatch). {'; '.join(m.get('errors', []))}")
        for m in errored:
            lines.append(f"  - {m['carrier_name']}: Error. {'; '.join(m.get('errors', []))}")

    if zero_result or no_data:
        lines.append(f"\nZERO MATCHED AGENTS:")
        for m in sorted(zero_result + no_data, key=lambda x: x["carrier_name"]):
            lines.append(f"  - {m['carrier_name']}: {m['total_rows']:,} rows, 0 matched ({m['status']})")

    if high_exc:
        lines.append(f"\nHIGH EXCEPTION CARRIERS:")
        for m in sorted(high_exc, key=lambda x: -x["exception_rate"]):
            lines.append(f"  - {_carrier_detail_line(m)}")

    if elevated:
        lines.append(f"\nELEVATED (5-10%):")
        for m in sorted(elevated, key=lambda x: -x["exception_rate"]):
            lines.append(f"  - {m['carrier_name']}: {m['results_count']:,} agents, {m['exceptions_count']:,} exc ({m['exception_rate']}%)")

    if clean:
        names = sorted(m["carrier_name"] for m in clean)
        lines.append(f"\nCOMPLETED NORMALLY ({len(clean)} carriers):")
        for n in names:
            lines.append(f"  - {n}")

    if uploaded:
        lines.append(f"\nOUTPUT FILES:")
        for k, v in uploaded.items():
            lines.append(f"  {k}: {v}")

    if has_attachments:
        lines.append(f"\nExceptions and missing agents files are attached to this email.")

    if ai_text:
        lines.append(f"\nANALYSIS:")
        lines.append(ai_text)

    return "\n".join(lines)


# ==========================================================
#  HTML (for Teams email — dark-mode safe)
# ==========================================================
def build_notification_html(all_metrics, run_date_str, uploaded, new_carriers, deactivated,
                            ai_text="", test_mode=False, pending_mappings=None,
                            skipped_inactive=None, has_attachments=False):
    mode = " [TEST]" if test_mode else ""
    total_rows, total_agents, total_exc, total_missing, rate = _run_totals(all_metrics)
    classified = _classify_carriers(all_metrics)
    halted, errored, no_data = classified["halted"], classified["errored"], classified["no_data"]
    zero_result, high_exc, elevated, clean = (
        classified["zero_result"], classified["high_exc"],
        classified["elevated"], classified["clean"],
    )

    html = []

    # Outer wrapper — explicit white background for dark mode safety
    html.append("""<html><body style="margin: 0; padding: 0;">
    <div style="font-family: Segoe UI, Calibri, Arial, sans-serif; font-size: 14px; color: #1a1a1a; line-height: 1.5; max-width: 800px; background: #ffffff; padding: 24px; margin: 0 auto;">""")

    # Header
    html.append(f"""
    <div style="border-bottom: 3px solid #2b579a; padding-bottom: 12px; margin-bottom: 16px;">
        <h2 style="margin: 0; color: #2b579a; font-size: 20px;">ACU Processing Report{esc(mode)}</h2>
        <span style="color: #555; font-size: 13px;">Run Date: {esc(run_date_str)}</span>
    </div>""")

    # Summary bar
    exc_color = '#c4314b' if rate >= 10 else '#d48806' if rate >= 5 else '#2b579a'
    html.append(f"""
    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px; background: #f0f2f5; border-radius: 4px;">
        <tr>
            <td style="padding: 12px 16px; text-align: center; border-right: 1px solid #d0d4da;">
                <div style="font-size: 22px; font-weight: 600; color: #2b579a;">{len(all_metrics)}</div>
                <div style="font-size: 11px; color: #555; text-transform: uppercase;">Carriers</div>
            </td>
            <td style="padding: 12px 16px; text-align: center; border-right: 1px solid #d0d4da;">
                <div style="font-size: 22px; font-weight: 600; color: #2b579a;">{total_rows:,}</div>
                <div style="font-size: 11px; color: #555; text-transform: uppercase;">Rows</div>
            </td>
            <td style="padding: 12px 16px; text-align: center; border-right: 1px solid #d0d4da;">
                <div style="font-size: 22px; font-weight: 600; color: #2b579a;">{total_agents:,}</div>
                <div style="font-size: 11px; color: #555; text-transform: uppercase;">Agents</div>
            </td>
            <td style="padding: 12px 16px; text-align: center; border-right: 1px solid #d0d4da;">
                <div style="font-size: 22px; font-weight: 600; color: {exc_color};">{total_exc:,}</div>
                <div style="font-size: 11px; color: #555; text-transform: uppercase;">Exceptions ({rate}%)</div>
            </td>
            <td style="padding: 12px 16px; text-align: center;">
                <div style="font-size: 22px; font-weight: 600; color: #555;">{total_missing:,}</div>
                <div style="font-size: 11px; color: #555; text-transform: uppercase;">Missing</div>
            </td>
        </tr>
    </table>""")

    # ── Section helpers ──
    def _section(title, color, items_html):
        return f"""
    <div style="margin-bottom: 16px;">
        <div style="font-weight: 600; font-size: 13px; color: {color}; text-transform: uppercase; letter-spacing: 0.5px; padding-bottom: 4px; border-bottom: 1px solid #dde1e6; margin-bottom: 8px;">{esc(title)}</div>
        {items_html}
    </div>"""

    def _bullet_list(items, color="#1a1a1a"):
        return "<ul style='margin: 4px 0; padding-left: 20px;'>" + \
            "".join(f"<li style='margin: 3px 0; color: {color};'>{i}</li>" for i in items) + "</ul>"

    # ── Sections ──

    if new_carriers:
        items = [f"{esc(nc['file_info']['file_name'])} <span style='color:#888;'>(pending review)</span>" for nc in new_carriers]
        html.append(_section("New Carriers", "#2b579a", _bullet_list(items)))

    if deactivated:
        items = [f"{esc(d['carrier_name'])}: {esc(d['reason'])}" for d in deactivated]
        html.append(_section("Deactivated", "#555", _bullet_list(items)))

    if pending_mappings:
        items = []
        for pm in pending_mappings:
            date_str = f", detected {esc(pm['detected_date'])}" if pm.get("detected_date") else ""
            items.append(f"{esc(pm['file_name'])}: {pm['column_count']} columns <span style='color:#888;'>({esc(pm['status'])}{date_str})</span>")
        html.append(_section(f"Awaiting Mapping Review ({len(pending_mappings)})", "#d48806", _bullet_list(items)))

    if skipped_inactive:
        items = [
            f"<strong>{esc(s['carrier_name'])}</strong> <span style='color:#888;'>(file: {esc(s['file_name'])})</span>"
            for s in sorted(skipped_inactive, key=lambda x: x["carrier_name"])
        ]
        html.append(_section(f"Skipped - Inactive ({len(skipped_inactive)})", "#888", _bullet_list(items)))

    if halted or errored:
        items = []
        for m in halted:
            items.append(f"<strong>{esc(m['carrier_name'])}</strong>: Halted (value map mismatch). {esc('; '.join(m.get('errors', [])))}")
        for m in errored:
            items.append(f"<strong>{esc(m['carrier_name'])}</strong>: Error. {esc('; '.join(m.get('errors', [])))}")
        html.append(_section("Requires Immediate Attention", "#c4314b", _bullet_list(items, "#c4314b")))

    if zero_result or no_data:
        items = []
        for m in sorted(zero_result + no_data, key=lambda x: x["carrier_name"]):
            items.append(f"<strong>{esc(m['carrier_name'])}</strong>: {m['total_rows']:,} rows in file, 0 agents matched <span style='color:#888;'>({m['status']})</span>")
        html.append(_section("Zero Matched Agents", "#c4314b", _bullet_list(items)))

    # High exception table
    if high_exc:
        rows_html = ""
        for m in sorted(high_exc, key=lambda x: -x["exception_rate"]):
            contracts = m.get("contracts_loaded", "?")
            cov_str = ""
            if isinstance(contracts, int) and contracts > 0 and m["total_rows"] > 0:
                cov_str = f"{round(contracts / m['total_rows'] * 100)}%"
            cats = m.get("exception_categories", {})
            top_cat = max(cats, key=cats.get) if cats else ""
            exc_c = "#c4314b" if m["exception_rate"] >= 20 else "#d48806"
            rows_html += f"""<tr style="background: #ffffff;">
                <td style="padding: 6px 10px; border-bottom: 1px solid #eee; color: #1a1a1a;">{esc(m['carrier_name'])}</td>
                <td style="padding: 6px 10px; border-bottom: 1px solid #eee; text-align: right; color: #1a1a1a;">{m['results_count']:,}</td>
                <td style="padding: 6px 10px; border-bottom: 1px solid #eee; text-align: right; color: {exc_c}; font-weight: 600;">{m['exceptions_count']:,} ({m['exception_rate']}%)</td>
                <td style="padding: 6px 10px; border-bottom: 1px solid #eee; text-align: right; color: #1a1a1a;">{contracts:,} {f'({cov_str})' if cov_str else ''}</td>
                <td style="padding: 6px 10px; border-bottom: 1px solid #eee; color: #1a1a1a;">{esc(top_cat)}</td>
            </tr>"""

        table = f"""
        <table style="width: 100%; border-collapse: collapse; font-size: 13px; background: #ffffff;">
            <tr style="background: #f0f2f5;">
                <th style="padding: 8px 10px; text-align: left; font-weight: 600; color: #1a1a1a; border-bottom: 2px solid #dde1e6;">Carrier</th>
                <th style="padding: 8px 10px; text-align: right; font-weight: 600; color: #1a1a1a; border-bottom: 2px solid #dde1e6;">Agents</th>
                <th style="padding: 8px 10px; text-align: right; font-weight: 600; color: #1a1a1a; border-bottom: 2px solid #dde1e6;">Exceptions</th>
                <th style="padding: 8px 10px; text-align: right; font-weight: 600; color: #1a1a1a; border-bottom: 2px solid #dde1e6;">Contracts (Coverage)</th>
                <th style="padding: 8px 10px; text-align: left; font-weight: 600; color: #1a1a1a; border-bottom: 2px solid #dde1e6;">Primary Type</th>
            </tr>
            {rows_html}
        </table>"""
        html.append(_section("High Exception Carriers", "#c4314b", table))

    if elevated:
        items = [
            f"<strong>{esc(m['carrier_name'])}</strong>: {m['results_count']:,} agents, {m['exceptions_count']:,} exc ({m['exception_rate']}%)"
            for m in sorted(elevated, key=lambda x: -x["exception_rate"])
        ]
        html.append(_section("Elevated Exception Carriers (5-10%)", "#d48806", _bullet_list(items)))

    if clean:
        names = sorted(m["carrier_name"] for m in clean)
        items = [esc(n) for n in names]
        html.append(_section(f"Completed Normally ({len(clean)} carriers, under 5% exceptions)", "#107c10", _bullet_list(items, "#107c10")))

    if uploaded:
        items = [f"<strong>{esc(k)}</strong>: <span style='font-size: 12px; color: #666;'>{esc(v)}</span>" for k, v in uploaded.items()]
        html.append(_section("Output Files", "#555", _bullet_list(items)))

    # Attachment note
    if has_attachments:
        html.append("""
    <div style="margin: 16px 0; padding: 10px 14px; background: #f0f2f5; border-left: 3px solid #2b579a; font-size: 13px; color: #1a1a1a;">
        The exceptions and missing agents detail files for this run are attached to this email.
    </div>""")

    # AI Analysis
    if ai_text:
        paragraphs = ai_text.strip().split("\n\n")
        analysis_html = "".join(f"<p style='margin: 8px 0; color: #1a1a1a;'>{esc(p)}</p>" for p in paragraphs if p.strip())
        html.append(_section("Analysis", "#2b579a", analysis_html))

    # Footer
    html.append("""
    <div style="border-top: 1px solid #dde1e6; padding-top: 8px; margin-top: 20px; font-size: 11px; color: #999;">
        834 Labs - Data Operations | ACU Pipeline
    </div>
    </div></body></html>""")

    return "\n".join(html)
