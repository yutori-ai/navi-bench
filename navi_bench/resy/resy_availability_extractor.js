/* eslint-disable no-var */
/**
 * Extract reservation slot metadata from a Resy venue page.
 * Designed to be read and executed via Playwright: page.evaluate(<fileContents>)
 *
 * @returns {Array<{
 *   shift: (string|null),
 *   date: (string|null),                // "YYYY-MM-DD"
 *   time_label: (string|null),          // "11:00 AM"
 *   time_24: (string|null),             // "HH:mm:ss"
 *   datetime_local_iso: (string|null),  // "YYYY-MM-DDTHH:mm:ss" (no TZ)
 *   area: (string|null),                // e.g. "Dining Room"
 *   party_size: (number|null),
 *   venue_id: (number|string|null),
 *   config_id: (number|string|null),
 *   source_dtid: (string|null),
 *   is_visible: boolean                 // true if the slot is within the viewport
 * }>}
 */
(() => {
  'use strict';

  // ---------------------- helpers ----------------------
  const q = (sel, root = document) => root.querySelector(sel);
  const qa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const isISODate = (s) => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s);

  const to24h = (label) => {
    if (!label) return null;
    const m = label.trim().match(/^(\d{1,2}):(\d{2})\s*([AP]M)$/i);
    if (!m) return null;
    let h = parseInt(m[1], 10);
    const min = m[2];
    const ap = m[3].toUpperCase();
    if (ap === 'AM') h = (h === 12) ? 0 : h;
    else h = (h === 12) ? 12 : h + 12;
    return `${String(h).padStart(2, '0')}:${min}:00`;
  };

  const parseSelectedDate = () => {
    // Prefer the quick-picker selected button's aria-label.
    // e.g. "Saturday, November 1, 2025. Selected date."
    let btn = q('button[aria-label*="Selected date."]');
    if (!btn) btn = q('.ResyCalendar-day--selected__available');
    const label = btn?.getAttribute('aria-label') || '';
    const m = label.match(/[A-Za-z]+,\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})/);
    if (!m) return null;
    const monthName = m[1], day = m[2], year = m[3];
    const d = new Date(`${monthName} ${day}, ${year} 00:00:00`);
    if (Number.isNaN(+d)) return null;
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
  };

  const fromEnd = (arr, n) => arr[arr.length - n];

  const parseResyDataTestId = (dtidRaw) => {
    // Example:
    // "reservation-button-rgs://resy/86435/3315361/1/2025-11-01/2025-11-01/11:00:00/2/Dining Room"
    const out = { raw_dtid: dtidRaw || null };
    if (!dtidRaw) return out;

    const cleaned = decodeURIComponent(String(dtidRaw).replace(/^reservation-button-/, ''));
    const parts = cleaned.split('/');

    const area           = fromEnd(parts, 1) ?? null;
    const partySizeStr   = fromEnd(parts, 2) ?? null;
    const time24         = fromEnd(parts, 3) ?? null;
    const endDate        = fromEnd(parts, 4) ?? null;
    const startDate      = fromEnd(parts, 5) ?? null;
    // fromEnd(parts, 6) is often a shift flag like "1"
    const configId       = fromEnd(parts, 7) ?? null;
    const venueId        = fromEnd(parts, 8) ?? null;

    out.venue_id = /^\d+$/.test(venueId || '') ? Number(venueId) : (venueId || null);
    out.config_id = /^\d+$/.test(configId || '') ? Number(configId) : (configId || null);
    out.start_date = isISODate(startDate) ? startDate : null;
    out.end_date   = isISODate(endDate)   ? endDate   : null;
    out.time_24_from_dtid = /^\d{2}:\d{2}:\d{2}$/.test(time24 || '') ? time24 : null;
    out.party_size = /^\d+$/.test(partySizeStr || '') ? Number(partySizeStr) : null;
    out.area_from_dtid = area || null;

    return out;
  };

  const isElementVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    if (!rect) return false;

    const viewHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const viewWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const hasArea = rect.width > 0 && rect.height > 0;
    const withinVertical = rect.bottom > 0 && rect.top < viewHeight;
    const withinHorizontal = rect.right > 0 && rect.left < viewWidth;
    const style = window.getComputedStyle(el);
    const visibleStyle =
      style.visibility !== 'hidden' &&
      style.display !== 'none' &&
      parseFloat(style.opacity || '1') > 0;

    if (!(hasArea && withinVertical && withinHorizontal && visibleStyle)) return false;

    const pointsToCheck = [
      [rect.left + rect.width / 2, rect.top + rect.height / 2], // center
      [rect.left + Math.min(rect.width, 20), rect.top + Math.min(rect.height, 20)], // near top-left
      [rect.right - Math.min(rect.width, 20), rect.bottom - Math.min(rect.height, 20)], // near bottom-right
    ];

    const isTopMostAtPoint = ([x, y]) => {
      if (Number.isNaN(x) || Number.isNaN(y)) return false;
      if (x < 0 || y < 0 || x > viewWidth || y > viewHeight) return false;
      const elements = document.elementsFromPoint(x, y);
      if (!elements || elements.length === 0) return false;
      const top = elements[0];
      return top === el || el.contains(top);
    };

    return pointsToCheck.some(isTopMostAtPoint);
  };

  // ---------------------- extraction ----------------------
  const selectedISODate =
    parseSelectedDate() ||
    null;

  const selectedPartySize = (() => {
    const sel = q('#party_size');
    const v = sel?.value;
    return v ? Number(v) : null;
  })();

  const buttons = qa('.ShiftInventory__shift .ReservationButtonList button.ReservationButton[data-testid^="reservation-button-"]');

  const results = buttons.map((btn) => {
    const shift = btn.closest('.ShiftInventory__shift')?.querySelector('.ShiftInventory__shift__title')?.textContent?.trim() || null;
    const timeLabel = btn.querySelector('.ReservationButton__time')?.textContent?.trim() || null;
    const areaLabel = btn.querySelector('.ReservationButton__type')?.textContent?.trim() || null;

    const dtid = btn.getAttribute('data-testid') || '';
    const meta = parseResyDataTestId(dtid);

    const dateISO = meta.start_date || selectedISODate || null;
    const time24  = meta.time_24_from_dtid || to24h(timeLabel) || null;

    return {
      shift,
      date: dateISO,
      time_label: timeLabel,
      time_24: time24,
      datetime_local_iso: (dateISO && time24) ? `${dateISO}T${time24}` : null,
      area: areaLabel || meta.area_from_dtid || null,
      party_size: meta.party_size ?? selectedPartySize ?? null,
      venue_id: meta.venue_id,
      config_id: meta.config_id,
      source_dtid: meta.raw_dtid,
      is_visible: isElementVisible(btn),
    };
  }).filter(r => r.time_24 && r.date);

  return results;
})();
