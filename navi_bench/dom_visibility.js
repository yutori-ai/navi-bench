/**
 * Shared DOM-visibility check.
 *
 * Returns true iff at least 50% of `el`'s bounding rect area is currently within the
 * viewport. Extracted because `resy/resy_no_availability_check.js` and
 * `opentable/opentable_info_gathering.js` each defined byte-for-byte identical copies of
 * this function to decide whether an availability slot / "no availability" message is
 * actually visible on screen before treating it as evidence.
 *
 * Callers load this file as a script preamble (see `read_sidecar` call sites in
 * `resy_url_match.py`/`opentable_info_gathering.py`) so `isVisible` is in scope via
 * closure for the IIFE that follows in the same evaluated script.
 */
const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth;

    // Calculate the visible area of the element
    const visibleTop = Math.max(rect.top, 0);
    const visibleLeft = Math.max(rect.left, 0);
    const visibleBottom = Math.min(rect.bottom, viewportHeight);
    const visibleRight = Math.min(rect.right, viewportWidth);

    // If element is completely outside viewport
    if (visibleTop >= visibleBottom || visibleLeft >= visibleRight) {
        return false;
    }

    const visibleArea = (visibleBottom - visibleTop) * (visibleRight - visibleLeft);
    const totalArea = rect.height * rect.width;

    // Consider visible if at least 50% of the element is in viewport
    return totalArea > 0 && (visibleArea / totalArea) >= 0.5;
};
