"""
Charge-capture (VIEW CHARGE) scraper.

Some appointments show "Charge in billing" on the dashboard card but the
facesheet PDF says "No charges listed" — the charge lives at the encounter
level, reachable only via the drawer's Financial Information tab -> VIEW
CHARGE, which navigates (same tab) to a charge-capture page. These helpers
open that page and scrape the diagnosis + procedure detail.

Selectors rely only on stable hooks (data-testid, id, data-rbd-droppable-id,
semantic tags) — the charge-capture page's class names are dynamically hashed.
"""


def open_charge_capture(page):
    """
    Assumes the appointment drawer is open for the target patient. Clicks
    Financial Information, then VIEW CHARGE (navigates the same tab to the
    charge-capture page). Returns True if the charge page loaded, False if
    there was no charge to view or navigation failed. Caller must navigate
    back afterward.
    """
    try:
        fin = page.locator("[data-testid='financial-button']")
        if not fin.count():
            return False
        fin.first.click()
        page.wait_for_timeout(600)

        view = page.locator("#financial-view-charge-button")
        if not view.count():
            return False
        view.first.click()
        page.wait_for_url("**/charge-capture/view/**", timeout=15_000)
        page.wait_for_timeout(500)
        return True
    except Exception as e:
        print(f"[CHARGE] open_charge_capture failed: {e}")
        return False


def scrape_charge_capture(page):
    """
    On a charge-capture 'view' page, return:
      {"diagnoses":  [{"rank","code","description"}, ...],
       "procedures": [{"code","description","mod1".."mod4","units",
                       "charge","total","diagnosis_pointers":[...]}, ...]}
    or None if nothing could be read.
    """
    try:
        page.wait_for_selector("[data-testid='procedure-code-cell']", timeout=15_000)
    except Exception:
        pass

    try:
        data = page.evaluate(r"""
        () => {
            const out = { diagnoses: [], procedures: [] };

            const dxList = document.querySelector(
                "[data-rbd-droppable-id='first-list-diagnosis']");
            if (dxList) {
                dxList.querySelectorAll(":scope > div").forEach(row => {
                    const rankEl = row.querySelector("h4");
                    const descEl = row.querySelector(
                        "[class*='FullDiagnosisCodeDescription']");
                    const full = descEl ? descEl.textContent.trim() : "";
                    if (!full) return;
                    let code = full, description = "";
                    const i = full.indexOf(":");
                    if (i > -1) {
                        code = full.slice(0, i).trim();
                        description = full.slice(i + 1).trim();
                    }
                    out.diagnoses.push({
                        rank: rankEl ? rankEl.textContent.trim() : "",
                        code, description
                    });
                });
            }

            const cellText = (td) => {
                if (!td) return "";
                const p = td.querySelector("p");
                return (p ? p.textContent : td.textContent).trim();
            };
            document.querySelectorAll(
                "[data-testid='procedure-code-cell']").forEach(codeCell => {
                const tr = codeCell.closest("tr");
                if (!tr) return;
                const tds = Array.from(tr.querySelectorAll(":scope > td"));
                const raw = cellText(tds[0]);
                let code = raw, description = "";
                const d = raw.indexOf(" - ");
                if (d > -1) {
                    code = raw.slice(0, d).trim();
                    description = raw.slice(d + 3).trim();
                }
                const proc = {
                    code, description,
                    mod1: cellText(tds[1]), mod2: cellText(tds[2]),
                    mod3: cellText(tds[3]), mod4: cellText(tds[4]),
                    units: cellText(tds[5]), charge: cellText(tds[6]),
                    total: cellText(tds[7]),
                    diagnosis_pointers: []
                };
                if (tds[8]) {
                    tds[8].querySelectorAll("p").forEach(p => {
                        const t = p.textContent.trim();
                        if (t) proc.diagnosis_pointers.push(t);
                    });
                }
                out.procedures.push(proc);
            });

            return out;
        }
        """)
        if not data or (not data.get("diagnoses") and not data.get("procedures")):
            return None
        return data
    except Exception as e:
        print(f"[CHARGE] scrape_charge_capture failed: {e}")
        return None
