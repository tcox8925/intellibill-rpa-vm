"""Print-preview clone/isolation internals and PDF generation."""

import base64
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from playwright.sync_api import Locator, Page, TimeoutError as PWTimeout

from pf_sync_pkg.chart_ui import dismiss_stray_print_preview_modal, format_pdf_name, note_date_tokens
from pf_sync_pkg.constants import DEFAULT_TIMEOUT
from pf_sync_pkg.dom_utils import first_visible_locator
from pf_sync_pkg.models import QueueRecord, SyncConfig
from pf_sync_pkg.utils import clean

# Anchor debug-artifact output to pf_sync_v5_6/ (this package's parent dir) rather
# than a bare relative path -- this app can be mounted as a sub-app inside
# myops/server.py's process (see pf_sync_v5_6/server.py's module docstring),
# whose cwd is myops/, which would otherwise silently create pf_sync_debug/
# inside myops/ instead of pf_sync_v5_6/.
_PF_SYNC_DEBUG_DIR = Path(__file__).resolve().parent.parent / "pf_sync_debug"


def _mark_current_visible_print_links(page: Page, selector: str, marker: str) -> int:
    """Mark print links that already exist before PF creates the chart preview.

    The patient Summary page itself contains several generic ``a.print-link`` icons.
    Waiting on ``a.print-link[title='Print']`` therefore matched the Summary page before
    the printable chart overlay had rendered, causing CDP to save the wrong screen.
    Only links visible before clicking the modal Print button are marked, so a preview
    link that is inserted later -- or was pre-rendered but hidden -- remains eligible.
    """
    try:
        return int(
            page.evaluate(
                """
                ({selector, marker}) => {
                    let count = 0;
                    for (const el of document.querySelectorAll(selector)) {
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        const visible = style.display !== 'none' &&
                            style.visibility !== 'hidden' &&
                            rect.width > 0 && rect.height > 0;
                        if (visible) {
                            el.setAttribute('data-pf-sync-preexisting-print-link', marker);
                            count += 1;
                        }
                    }
                    return count;
                }
                """,
                {"selector": selector, "marker": marker},
            )
        )
    except Exception:
        return 0


def _new_visible_print_preview_link(
    page: Page, selector: str, marker: str, timeout_ms: int
) -> Locator:
    """Wait for the new print control belonging to PF's printable chart overlay."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        links = page.locator(selector)
        try:
            count = links.count()
        except Exception:
            count = 0
        for index in range(count):
            link = links.nth(index)
            try:
                if not link.is_visible():
                    continue
                if link.get_attribute('data-pf-sync-preexisting-print-link') == marker:
                    continue
                return link
            except Exception:
                continue
        time.sleep(0.2)
    raise PWTimeout(
        "Printable chart preview did not appear. Existing Summary-page print icons were "
        "ignored, but no new preview Print control became visible."
    )


def _find_and_mark_print_document(
    page: Page,
    record: QueueRecord,
    config: SyncConfig,
    timeout_ms: int = DEFAULT_TIMEOUT,
):
    """Locate the actual PF printable SOAP document, not the Summary page.

    PF keeps the browser URL on ``/summary`` and renders the chart preview as a
    body-level overlay (and, in some builds, inside a same-origin iframe).  The blue
    printer link is only a toolbar control and is not necessarily a descendant of the
    printable document.  Therefore, finding an ancestor of that link is unreliable.

    Instead, search every same-origin frame for the smallest visible element containing
    the distinctive printable-chart text markers and the encounter date.  The Summary
    page cannot satisfy this guard because it does not contain the combined
    PATIENT/FACILITY/ENCOUNTER/NOTE TYPE headings plus SOAP body headings.
    """
    date_tokens = note_date_tokens(record.appointment_date, config.note_date_formats)
    date_tokens = [clean(token).upper() for token in date_tokens if clean(token)]
    deadline = time.time() + timeout_ms / 1000.0
    last_diagnostics: list = []

    finder_js = r"""
        ({dateTokens}) => {
            const normalize = value => String(value || '')
                .replace(/ /g, ' ')
                .replace(/\s+/g, ' ')
                .trim()
                .toUpperCase();

            document.querySelectorAll('[data-pf-sync-print-document-root]')
                .forEach(el => el.removeAttribute('data-pf-sync-print-document-root'));

            const selectors = [
                'main', 'article', 'section', 'form', 'table',
                'div', 'body'
            ].join(',');
            const candidates = [];
            const viewportArea = Math.max(1, innerWidth * innerHeight);

            for (const el of document.querySelectorAll(selectors)) {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                if (style.display === 'none' || style.visibility === 'hidden' ||
                    rect.width < 250 || rect.height < 250) {
                    continue;
                }

                const text = normalize(el.innerText || el.textContent || '');
                if (text.length < 350) continue;

                const hasPatient = /(^|\s)PATIENT(\s|$)/.test(text);
                const hasFacility = /(^|\s)FACILITY(\s|$)/.test(text);
                const hasEncounter = /(^|\s)ENCOUNTER(\s|$)/.test(text);
                const hasNoteType = text.includes('NOTE TYPE') || text.includes('SOAP NOTE');
                const hasClinicalBody = [
                    'SUBJECTIVE', 'OBJECTIVE', 'ASSESSMENT', 'PLAN',
                    'CHIEF COMPLAINT', 'VITALS FOR THIS ENCOUNTER'
                ].some(token => text.includes(token));
                const hasDate = !dateTokens.length || dateTokens.some(token => text.includes(token));

                // This conjunction deliberately excludes the underlying patient Summary.
                if (!(hasPatient && hasFacility && hasEncounter && hasNoteType &&
                      hasClinicalBody && hasDate)) {
                    continue;
                }

                const position = style.position || '';
                const z = Number.parseInt(style.zIndex || '0', 10) || 0;
                const area = Math.max(1, rect.width * rect.height);
                let score = 1000;
                score += hasClinicalBody ? 200 : 0;
                score += hasDate ? 150 : 0;
                score += (position === 'fixed' || position === 'absolute') ? 60 : 0;
                score += z > 100 ? 40 : 0;
                score += style.backgroundColor && style.backgroundColor !== 'rgba(0, 0, 0, 0)' ? 15 : 0;
                // Prefer the tightest element that still contains the whole print document.
                score -= Math.min(200, Math.round((area / viewportArea) * 10));
                if (el === document.body) score -= 300;

                candidates.push({el, score, textLength: text.length, rect, z, position, text});
            }

            candidates.sort((a, b) =>
                (b.score - a.score) ||
                (a.textLength - b.textLength) ||
                ((a.rect.width * a.rect.height) - (b.rect.width * b.rect.height))
            );

            if (!candidates.length) {
                const bodyText = normalize(document.body?.innerText || '');
                return {
                    found: false,
                    url: location.href,
                    bodyTextLength: bodyText.length,
                    bodyHasFacility: bodyText.includes('FACILITY'),
                    bodyHasNoteType: bodyText.includes('NOTE TYPE'),
                    bodyHasSubjective: bodyText.includes('SUBJECTIVE'),
                    bodyHasObjective: bodyText.includes('OBJECTIVE'),
                    dateTokens
                };
            }

            const chosen = candidates[0];

            // The tightest matching element is commonly one DIV.print-section.
            // For an "all SOAP notes" print, PF renders many sibling print sections
            // inside one scrollable modal. Promote the match to the smallest ancestor
            // containing every available print section so PDF generation captures the
            // whole chart rather than only the first encounter.
            let promoted = chosen.el;
            let cursor = chosen.el.parentElement;
            while (cursor && cursor !== document.body && cursor !== document.documentElement) {
                const sectionCount = cursor.querySelectorAll('.print-section').length;
                if (sectionCount >= 2) {
                    promoted = cursor;
                    break;
                }
                cursor = cursor.parentElement;
            }

            // If PF has only one section in this print job, still promote to the nearest
            // scroll-clamping ancestor/modal rather than cloning the leaf section.
            if (promoted === chosen.el) {
                cursor = chosen.el.parentElement;
                while (cursor && cursor !== document.body && cursor !== document.documentElement) {
                    const style = getComputedStyle(cursor);
                    const hasScrollClamp = cursor.scrollHeight > cursor.clientHeight + 8 ||
                        ['auto', 'scroll', 'hidden'].includes(style.overflowY);
                    const hasPreviewToolbar = !!cursor.querySelector(
                        'a.print-link[title="Print"], .glyphicon-print, .glyphicon-remove, .icon-go-away'
                    );
                    if (hasScrollClamp || hasPreviewToolbar) {
                        promoted = cursor;
                        break;
                    }
                    cursor = cursor.parentElement;
                }
            }

            const promotedRect = promoted.getBoundingClientRect();
            const promotedStyle = getComputedStyle(promoted);
            const promotedText = normalize(promoted.innerText || promoted.textContent || '');
            const sectionCount = promoted.querySelectorAll('.print-section').length ||
                (promoted.matches('.print-section') ? 1 : 0);

            promoted.setAttribute('data-pf-sync-print-document-root', 'true');
            return {
                found: true,
                url: location.href,
                tag: promoted.tagName,
                className: String(promoted.className || ''),
                width: Math.round(promotedRect.width),
                height: Math.round(promotedRect.height),
                scrollHeight: Math.round(promoted.scrollHeight || promotedRect.height),
                clientHeight: Math.round(promoted.clientHeight || promotedRect.height),
                sectionCount,
                textLength: promotedText.length,
                zIndex: Number.parseInt(promotedStyle.zIndex || '0', 10) || 0,
                position: promotedStyle.position || '',
                textPreview: promotedText.slice(0, 240)
            };
        }
    """

    while time.time() < deadline:
        last_diagnostics = []
        for frame in list(page.frames):
            try:
                result = frame.evaluate(finder_js, {"dateTokens": date_tokens})
            except Exception as exc:
                last_diagnostics.append({"url": getattr(frame, "url", ""), "error": str(exc)[:200]})
                continue
            if isinstance(result, dict):
                last_diagnostics.append(result)
            if isinstance(result, dict) and result.get("found"):
                root = frame.locator("[data-pf-sync-print-document-root='true']").first
                root.wait_for(state="visible", timeout=5_000)
                return frame, root, result
        time.sleep(0.25)

    # Save evidence that is useful if PF changes the preview structure again.
    debug_dir = _PF_SYNC_DEBUG_DIR
    debug_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        page.screenshot(path=str(debug_dir / f"print_preview_not_found_{stamp}.png"), full_page=True)
    except Exception:
        pass
    try:
        (debug_dir / f"print_preview_not_found_{stamp}.html").write_text(
            page.content(), encoding="utf-8"
        )
    except Exception:
        pass
    try:
        import json

        (debug_dir / f"print_preview_not_found_{stamp}.json").write_text(
            json.dumps(last_diagnostics, indent=2), encoding="utf-8"
        )
    except Exception:
        pass

    raise RuntimeError(
        "PRINT_DOCUMENT_NOT_FOUND: Practice Fusion showed the print toolbar, but no "
        "visible document containing PATIENT/FACILITY/ENCOUNTER, SOAP headings, and "
        f"the appointment date was found. Diagnostics were saved in {debug_dir.resolve()}."
    )


def _install_print_document_isolation(page: Page, target_frame) -> None:
    """Make Chromium print only the marked SOAP document.

    The printable document can live in the main document or a same-origin iframe.  In
    either case we hide the underlying EHR with print-only CSS and expand the marked
    document so scroll containers do not clip later SOAP pages.
    """
    inner_style_js = r"""
        () => {
            document.getElementById('pf-sync-print-document-style')?.remove();
            const style = document.createElement('style');
            style.id = 'pf-sync-print-document-style';
            style.textContent = `
                @media print {
                    html, body {
                        margin: 0 !important;
                        padding: 0 !important;
                        width: 100% !important;
                        height: auto !important;
                        overflow: visible !important;
                        background: white !important;
                    }
                    body * { visibility: hidden !important; }
                    [data-pf-sync-print-document-root='true'],
                    [data-pf-sync-print-document-root='true'] * {
                        visibility: visible !important;
                    }
                    [data-pf-sync-print-document-root='true'] {
                        position: absolute !important;
                        inset: 0 auto auto 0 !important;
                        width: 100% !important;
                        min-width: 0 !important;
                        max-width: none !important;
                        height: auto !important;
                        max-height: none !important;
                        overflow: visible !important;
                        margin: 0 !important;
                        padding: 0 !important;
                        transform: none !important;
                        z-index: 2147483647 !important;
                        background: white !important;
                    }
                    [data-pf-sync-print-document-root='true'] .print-link,
                    [data-pf-sync-print-document-root='true'] [title='Print'],
                    [data-pf-sync-print-document-root='true'] .glyphicon-print,
                    [data-pf-sync-print-document-root='true'] .glyphicon-remove,
                    [data-pf-sync-print-document-root='true'] .icon-go-away {
                        display: none !important;
                    }
                }
            `;
            document.head.appendChild(style);
        }
    """
    target_frame.evaluate(inner_style_js)

    if target_frame == page.main_frame:
        return

    # If PF places the document in an iframe, expose only that iframe in the parent.
    frame_element = target_frame.frame_element()
    frame_element.evaluate(
        "el => el.setAttribute('data-pf-sync-print-document-frame', 'true')"
    )
    page.evaluate(
        r"""
        () => {
            document.getElementById('pf-sync-print-frame-style')?.remove();
            const style = document.createElement('style');
            style.id = 'pf-sync-print-frame-style';
            style.textContent = `
                @media print {
                    body * { visibility: hidden !important; }
                    iframe[data-pf-sync-print-document-frame='true'] {
                        visibility: visible !important;
                        position: absolute !important;
                        inset: 0 auto auto 0 !important;
                        width: 100% !important;
                        height: 100vh !important;
                        border: 0 !important;
                    }
                }
            `;
            document.head.appendChild(style);
        }
        """
    )


def _cleanup_print_preview_markers(page: Page) -> None:
    for frame in list(page.frames):
        try:
            frame.evaluate(
                """
                () => {
                    document.querySelectorAll('[data-pf-sync-preexisting-print-link]')
                        .forEach(el => el.removeAttribute('data-pf-sync-preexisting-print-link'));
                    document.querySelectorAll('[data-pf-sync-print-preview-root]')
                        .forEach(el => el.removeAttribute('data-pf-sync-print-preview-root'));
                    document.querySelectorAll('[data-pf-sync-print-document-root]')
                        .forEach(el => el.removeAttribute('data-pf-sync-print-document-root'));
                    document.getElementById('pf-sync-print-isolation-style')?.remove();
                    document.getElementById('pf-sync-print-document-style')?.remove();
                }
                """
            )
        except Exception:
            pass
    try:
        page.evaluate(
            """
            () => {
                document.querySelectorAll('[data-pf-sync-print-document-frame]')
                    .forEach(el => el.removeAttribute('data-pf-sync-print-document-frame'));
                document.getElementById('pf-sync-print-frame-style')?.remove();
            }
            """
        )
    except Exception:
        pass


def _expand_and_materialize_print_modal(target_frame) -> Dict[str, Any]:
    """Scroll the PF preview and remove nested scroll clamps before cloning.

    Practice Fusion keeps the printable chart in a viewport-sized modal.  Long output
    lives inside one or more nested scrollers, and some sections can render only after
    those scrollers move.  Chromium therefore sees only the visible modal height unless
    we first traverse the scrollers and tag them for expansion in the isolated clone.
    """
    result = target_frame.evaluate(
        r"""
        async () => {
            const root = document.querySelector('[data-pf-sync-print-document-root="true"]');
            if (!root) throw new Error('Marked print document root is missing');

            const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
            const candidates = [root, ...root.querySelectorAll('*')].filter(el => {
                if (!(el instanceof HTMLElement)) return false;
                const style = getComputedStyle(el);
                const overflow = `${style.overflow} ${style.overflowY}`;
                return el.scrollHeight > el.clientHeight + 8 || /auto|scroll|hidden/.test(overflow);
            });

            let traversed = 0;
            for (const el of candidates) {
                const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
                if (maxTop <= 0) continue;
                el.setAttribute('data-pf-sync-expand-scroll', 'true');
                const step = Math.max(250, Math.floor(Math.max(1, el.clientHeight) * 0.75));
                let top = 0;
                let guard = 0;
                while (top < maxTop && guard < 80) {
                    top = Math.min(maxTop, top + step);
                    el.scrollTop = top;
                    el.dispatchEvent(new Event('scroll', {bubbles: true}));
                    await sleep(35);
                    guard += 1;
                }
                await sleep(80);
                el.scrollTop = 0;
                el.dispatchEvent(new Event('scroll', {bubbles: true}));
                traversed += 1;
            }

            // Also exercise the document scroller in case the preview is not the direct
            // scrolling element.  Return to the top afterward so the source UI remains sane.
            const docScroller = document.scrollingElement;
            if (docScroller && docScroller.scrollHeight > docScroller.clientHeight + 8) {
                const maxTop = docScroller.scrollHeight - docScroller.clientHeight;
                const step = Math.max(400, Math.floor(docScroller.clientHeight * 0.8));
                for (let top = 0, guard = 0; top < maxTop && guard < 80; guard += 1) {
                    top = Math.min(maxTop, top + step);
                    docScroller.scrollTop = top;
                    window.dispatchEvent(new Event('scroll'));
                    await sleep(35);
                }
                await sleep(80);
                docScroller.scrollTop = 0;
            }

            await sleep(250);

            // Re-tag every currently clamped descendant.  These attributes survive
            // cloneNode and are expanded by the standalone PDF page CSS.
            let tagged = 0;
            for (const el of [root, ...root.querySelectorAll('*')]) {
                if (!(el instanceof HTMLElement)) continue;
                const style = getComputedStyle(el);
                const overflow = `${style.overflow} ${style.overflowY}`;
                if (el.scrollHeight > el.clientHeight + 8 || /auto|scroll|hidden/.test(overflow)) {
                    el.setAttribute('data-pf-sync-expand-scroll', 'true');
                    tagged += 1;
                }
            }

            const sections = root.querySelectorAll('.print-section').length ||
                (root.matches('.print-section') ? 1 : 0);
            return {
                traversed,
                tagged,
                sectionCount: sections,
                clientHeight: Math.round(root.clientHeight || 0),
                scrollHeight: Math.round(root.scrollHeight || 0),
                textLength: (root.innerText || '').trim().length
            };
        }
        """
    )
    return result if isinstance(result, dict) else {}


def _snapshot_print_document_for_clone(target_frame) -> Dict[str, str]:
    """Serialize the confirmed PF print section and the CSS needed to render it.

    Practice Fusion renders the printable chart inside the existing EHR document.  A
    print-only visibility rule can still yield a blank PDF because PF's own ancestor
    layout/print rules continue to participate.  Instead, clone the confirmed
    ``data-pf-sync-print-document-root`` into a new blank page in the same authenticated
    browser context.
    """
    result = target_frame.evaluate(
        r"""
        () => {
            const root = document.querySelector('[data-pf-sync-print-document-root="true"]');
            if (!root) throw new Error('Marked print document root is missing');

            const sourceSectionCount = root.querySelectorAll('.print-section').length ||
                (root.matches('.print-section') ? 1 : 0);
            const sourceScrollHeight = Math.round(root.scrollHeight || 0);
            const sourceClientHeight = Math.round(root.clientHeight || 0);

            const clone = root.cloneNode(true);
            clone.removeAttribute('data-pf-sync-print-document-root');
            clone.setAttribute('data-pf-sync-cloned-print-root', 'true');

            // Explicitly remove modal/viewport clipping from every source element that
            // was identified as a scroll clamp.  Inline !important is used because PF's
            // Ember styles can otherwise win against the standalone-page stylesheet.
            const expandable = [clone, ...clone.querySelectorAll('[data-pf-sync-expand-scroll="true"]')];
            for (const el of expandable) {
                if (!(el instanceof HTMLElement)) continue;
                el.style.setProperty('height', 'auto', 'important');
                el.style.setProperty('min-height', '0', 'important');
                el.style.setProperty('max-height', 'none', 'important');
                el.style.setProperty('overflow', 'visible', 'important');
                el.style.setProperty('overflow-x', 'visible', 'important');
                el.style.setProperty('overflow-y', 'visible', 'important');
                if (['fixed', 'sticky'].includes(getComputedStyle(el).position)) {
                    el.style.setProperty('position', 'relative', 'important');
                    el.style.setProperty('inset', 'auto', 'important');
                }
            }

            const absolute = (value) => {
                try { return new URL(value, document.baseURI).href; }
                catch (_) { return value; }
            };

            for (const el of clone.querySelectorAll('*')) {
                for (const attr of ['src', 'href', 'poster']) {
                    if (el.hasAttribute(attr)) {
                        const value = el.getAttribute(attr);
                        if (value && !value.startsWith('#') && !value.startsWith('data:') &&
                            !value.startsWith('javascript:')) {
                            el.setAttribute(attr, absolute(value));
                        }
                    }
                }
                if (el.hasAttribute('srcset')) {
                    const converted = el.getAttribute('srcset').split(',').map(part => {
                        const bits = part.trim().split(/\s+/);
                        if (bits[0]) bits[0] = absolute(bits[0]);
                        return bits.join(' ');
                    }).join(', ');
                    el.setAttribute('srcset', converted);
                }
            }

            // Preserve current form values in case PF uses inputs in the print view.
            const sourceInputs = root.querySelectorAll('input, textarea, select');
            const clonedInputs = clone.querySelectorAll('input, textarea, select');
            sourceInputs.forEach((source, index) => {
                const dest = clonedInputs[index];
                if (!dest) return;
                if (source instanceof HTMLInputElement) {
                    if (source.type === 'checkbox' || source.type === 'radio') {
                        source.checked ? dest.setAttribute('checked', '') : dest.removeAttribute('checked');
                    } else {
                        dest.setAttribute('value', source.value || '');
                    }
                } else if (source instanceof HTMLTextAreaElement) {
                    dest.textContent = source.value || '';
                } else if (source instanceof HTMLSelectElement) {
                    Array.from(dest.options || []).forEach((option, optionIndex) => {
                        source.options[optionIndex]?.selected
                            ? option.setAttribute('selected', '')
                            : option.removeAttribute('selected');
                    });
                }
            });

            // Canvas pixels are not retained by cloneNode. Convert them to images.
            const sourceCanvases = root.querySelectorAll('canvas');
            const clonedCanvases = clone.querySelectorAll('canvas');
            sourceCanvases.forEach((source, index) => {
                const dest = clonedCanvases[index];
                if (!dest) return;
                try {
                    const img = document.createElement('img');
                    img.src = source.toDataURL('image/png');
                    img.width = source.width;
                    img.height = source.height;
                    img.style.cssText = dest.style.cssText;
                    dest.replaceWith(img);
                } catch (_) {}
            });

            const cssNodes = Array.from(document.querySelectorAll('link[rel="stylesheet"], style'));
            const css = cssNodes.map(node => {
                if (node.tagName === 'LINK') {
                    const href = node.getAttribute('href');
                    return href ? `<link rel="stylesheet" href="${absolute(href)}">` : '';
                }
                return `<style>${node.textContent || ''}</style>`;
            }).join('\n');

            return {
                baseUrl: document.baseURI || location.href,
                title: document.title || 'Practice Fusion chart',
                css,
                rootHtml: clone.outerHTML,
                sourceSectionCount,
                sourceScrollHeight,
                sourceClientHeight,
                sourceTextLength: (root.innerText || '').trim().length
            };
        }
        """
    )
    if not isinstance(result, dict) or not result.get("rootHtml"):
        raise RuntimeError("Could not serialize the Practice Fusion print document.")
    return {str(k): str(v or "") for k, v in result.items()}


def _isolated_print_html(snapshot: Dict[str, str]) -> str:
    """Build a standalone document whose body contains only the PF print section."""
    import json as _json

    base_url = _json.dumps(snapshot.get("baseUrl", ""))[1:-1]
    title = _json.dumps(snapshot.get("title", "Practice Fusion chart"))[1:-1]
    return f"""<!doctype html>
<html>
<head>
<meta charset=\"utf-8\">
<base href=\"{base_url}\">
<title>{title}</title>
{snapshot.get('css', '')}
<style>
    html, body {{
        margin: 0 !important;
        padding: 0 !important;
        width: 100% !important;
        min-height: 100% !important;
        height: auto !important;
        overflow: visible !important;
        background: white !important;
    }}
    body {{ display: block !important; }}
    [data-pf-sync-cloned-print-root='true'] {{
        display: block !important;
        visibility: visible !important;
        position: static !important;
        inset: auto !important;
        float: none !important;
        width: 100% !important;
        min-width: 0 !important;
        max-width: none !important;
        height: auto !important;
        min-height: 0 !important;
        max-height: none !important;
        overflow: visible !important;
        margin: 0 !important;
        padding: 0 !important;
        transform: none !important;
        opacity: 1 !important;
        background: white !important;
    }}
    [data-pf-sync-cloned-print-root='true'] * {{
        visibility: visible !important;
        max-height: none !important;
    }}
    [data-pf-sync-expand-scroll='true'] {{
        height: auto !important;
        min-height: 0 !important;
        max-height: none !important;
        overflow: visible !important;
        overflow-x: visible !important;
        overflow-y: visible !important;
        position: relative !important;
        inset: auto !important;
    }}
    .print-section {{
        display: block !important;
        visibility: visible !important;
        height: auto !important;
        min-height: 0 !important;
        max-height: none !important;
        overflow: visible !important;
        break-inside: auto !important;
        page-break-inside: auto !important;
    }}
    .print-link, [title='Print'], .glyphicon-print,
    .glyphicon-remove, .icon-go-away {{ display: none !important; }}
    @media print {{
        html, body, [data-pf-sync-cloned-print-root='true'] {{
            display: block !important;
            visibility: visible !important;
            overflow: visible !important;
            height: auto !important;
            max-height: none !important;
            background: white !important;
        }}
    }}
</style>
</head>
<body>{snapshot.get('rootHtml', '')}</body>
</html>"""


def _save_pdf_debug_artifacts(
    debug_html: str,
    isolated_page,
    pdf_bytes: bytes,
    prefix: str,
) -> Path:
    debug_dir = _PF_SYNC_DEBUG_DIR
    debug_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = debug_dir / f"{prefix}_{stamp}"
    try:
        base.with_suffix(".html").write_text(debug_html, encoding="utf-8")
    except Exception:
        pass
    try:
        isolated_page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
    except Exception:
        pass
    try:
        if pdf_bytes:
            base.with_suffix(".pdf").write_bytes(pdf_bytes)
    except Exception:
        pass
    return base


def generate_pdf(
    page: Page,
    config: SyncConfig,
    record: QueueRecord,
    downloads_dir: str,
    dry_run: bool,
) -> str:
    """Clone the confirmed PF SOAP print section into a clean page and print it.

    Printing the original EHR page either captured the Summary screen or produced a
    929-byte blank PDF because Practice Fusion's surrounding layout and print rules
    remained active.  The new page preserves PF styles but contains no EHR navigation,
    hidden modal ancestors, advertisements, or overlay backdrop.
    """
    if dry_run:
        return "DRY_RUN_NO_PDF"

    output_dir = Path(downloads_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / format_pdf_name(record, config)

    marker = uuid.uuid4().hex
    existing_count = _mark_current_visible_print_links(
        page, config.printable_preview_ready_selector, marker
    )
    if existing_count:
        print(
            f"  preview guard: ignored {existing_count} pre-existing Summary-page print icon(s)",
            flush=True,
        )

    button = page.locator(config.generate_pdf_button_selector).first
    button.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
    button.click()

    isolated_page = None
    cdp = None
    pdf_bytes = b""
    isolated_html = ""
    try:
        _new_visible_print_preview_link(
            page, config.printable_preview_ready_selector, marker, DEFAULT_TIMEOUT
        )

        modal_deadline = time.time() + 15
        while time.time() < modal_deadline:
            if first_visible_locator(page, config.print_modal_ready_selectors, 200) is None:
                break
            time.sleep(0.2)
        if first_visible_locator(page, config.print_modal_ready_selectors, 200) is not None:
            raise RuntimeError(
                "PRINT_PREVIEW_NOT_READY: the Print Chart options modal is still visible "
                "after clicking Print; PDF was not saved."
            )

        page.wait_for_timeout(800)
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        try:
            page.evaluate(
                "() => document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve()"
            )
        except Exception:
            pass

        target_frame, print_root, root_info = _find_and_mark_print_document(
            page, record, config, DEFAULT_TIMEOUT
        )
        root_text = clean(print_root.inner_text(timeout=5_000))
        print(
            "  confirmed printable SOAP document "
            f"({root_info.get('tag')}.{root_info.get('className')}, "
            f"{root_info.get('width')}x{root_info.get('height')}, "
            f"text={len(root_text)}, frame={root_info.get('url')})",
            flush=True,
        )

        expansion = _expand_and_materialize_print_modal(target_frame)
        print(
            "  expanded printable modal "
            f"(sections={expansion.get('sectionCount')}, "
            f"scroll={expansion.get('clientHeight')}->{expansion.get('scrollHeight')}, "
            f"scrollers={expansion.get('tagged')}, traversed={expansion.get('traversed')}, "
            f"text={expansion.get('textLength')})",
            flush=True,
        )

        snapshot = _snapshot_print_document_for_clone(target_frame)
        isolated_html = _isolated_print_html(snapshot)
        isolated_page = page.context.new_page()
        isolated_page.set_default_timeout(DEFAULT_TIMEOUT)
        isolated_page.set_viewport_size({"width": 1200, "height": 1600})
        isolated_page.set_content(isolated_html, wait_until="domcontentloaded")
        try:
            isolated_page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        try:
            isolated_page.evaluate(
                "() => document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve()"
            )
        except Exception:
            pass
        try:
            isolated_page.wait_for_function(
                "() => Array.from(document.images || []).every(img => img.complete)",
                timeout=15_000,
            )
        except Exception:
            pass

        cloned_info = isolated_page.locator(
            "[data-pf-sync-cloned-print-root='true']"
        ).first.evaluate(
            """el => ({
                width: Math.round(el.getBoundingClientRect().width),
                height: Math.round(el.getBoundingClientRect().height),
                scrollHeight: Math.round(el.scrollHeight || 0),
                textLength: (el.innerText || '').trim().length,
                sectionCount: el.querySelectorAll('.print-section').length ||
                    (el.matches('.print-section') ? 1 : 0),
                display: getComputedStyle(el).display,
                visibility: getComputedStyle(el).visibility
            })"""
        )
        print(
            "  cloned SOAP document ready "
            f"({cloned_info.get('width')}x{cloned_info.get('height')}, "
            f"scrollHeight={cloned_info.get('scrollHeight')}, "
            f"sections={cloned_info.get('sectionCount')}, "
            f"text={cloned_info.get('textLength')}, "
            f"display={cloned_info.get('display')}, "
            f"visibility={cloned_info.get('visibility')})",
            flush=True,
        )
        source_section_count = int(snapshot.get("sourceSectionCount") or 0)
        cloned_section_count = int(cloned_info.get("sectionCount") or 0)
        if source_section_count and cloned_section_count < source_section_count:
            base = _save_pdf_debug_artifacts(
                isolated_html, isolated_page, b"", "cloned_print_sections_missing"
            )
            raise RuntimeError(
                "CLONED_PRINT_SECTIONS_MISSING: the isolated page retained only "
                f"{cloned_section_count} of {source_section_count} printable sections. "
                f"Diagnostics saved as {base}.*"
            )

        if int(cloned_info.get("textLength") or 0) < 300 or int(cloned_info.get("height") or 0) < 300:
            base = _save_pdf_debug_artifacts(
                isolated_html, isolated_page, b"", "cloned_print_document_invalid"
            )
            raise RuntimeError(
                "CLONED_PRINT_DOCUMENT_INVALID: the isolated page did not retain the "
                f"SOAP document. Diagnostics saved as {base}.*"
            )

        isolated_page.emulate_media(media="print")
        isolated_page.wait_for_timeout(300)
        try:
            pdf_bytes = isolated_page.pdf(
                format="Letter",
                print_background=True,
                prefer_css_page_size=True,
                display_header_footer=False,
                margin={
                    "top": "0.25in",
                    "bottom": "0.25in",
                    "left": "0.25in",
                    "right": "0.25in",
                },
            )
            print("  PDF engine: Playwright page.pdf() on cloned SOAP-only page", flush=True)
        except Exception as page_pdf_error:
            print(
                f"  page.pdf() unavailable ({type(page_pdf_error).__name__}); "
                "using Chrome Page.printToPDF fallback on cloned page",
                flush=True,
            )
            cdp = isolated_page.context.new_cdp_session(isolated_page)
            result = cdp.send(
                "Page.printToPDF",
                {
                    "landscape": False,
                    "displayHeaderFooter": False,
                    "printBackground": True,
                    "preferCSSPageSize": True,
                    "paperWidth": 8.5,
                    "paperHeight": 11,
                    "marginTop": 0.25,
                    "marginBottom": 0.25,
                    "marginLeft": 0.25,
                    "marginRight": 0.25,
                    "transferMode": "ReturnAsBase64",
                },
            )
            pdf_bytes = base64.b64decode(result.get("data", ""))

        if not isinstance(pdf_bytes, (bytes, bytearray)) or not pdf_bytes.startswith(b"%PDF"):
            base = _save_pdf_debug_artifacts(
                isolated_html, isolated_page, bytes(pdf_bytes or b""), "invalid_generated_pdf"
            )
            raise RuntimeError(
                f"The browser did not return a valid PDF. Diagnostics saved as {base}.*"
            )
        if len(pdf_bytes) < int(config.pdf_min_bytes or 1024):
            base = _save_pdf_debug_artifacts(
                isolated_html, isolated_page, bytes(pdf_bytes), "small_generated_pdf"
            )
            raise RuntimeError(
                f"Generated PDF is unexpectedly small: {len(pdf_bytes)} bytes. "
                f"Diagnostics saved as {base}.*"
            )

        destination.write_bytes(bytes(pdf_bytes))
        return str(destination.resolve())
    finally:
        if cdp is not None:
            try:
                cdp.detach()
            except Exception:
                pass
        if isolated_page is not None:
            try:
                isolated_page.close()
            except Exception:
                pass
        _cleanup_print_preview_markers(page)
        # PF's own native print-preview overlay (opened by the click on
        # generate_pdf_button_selector above) is closed here, proactively,
        # right where it was opened -- see dismiss_stray_print_preview_modal's
        # docstring for why leaving it open blocks the NEXT record's own
        # Print Chart click if this isn't done. open_print_chart calls the
        # same function defensively too, so a record that skips generate_pdf
        # entirely (an earlier failure) still gets it cleared before this one.
        dismiss_stray_print_preview_modal(page, config)
