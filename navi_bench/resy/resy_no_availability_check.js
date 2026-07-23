(() => {
    /**
     * Simple check for "no online availability" message on Resy pages.
     * Returns true if the message is found and visible, false otherwise.
     *
     * Relies on `isVisible` being defined by the shared ../dom_visibility.js preamble
     * (see resy_url_match.py's js_script property), which loads it into scope ahead of
     * this IIFE.
     */

    // Returns true if any element matching `selector` is visible and its text contains
    // the "no online availability" message.
    const hasVisibleNoAvailabilityMessage = (selector) => {
        for (const elem of document.querySelectorAll(selector)) {
            if (isVisible(elem) && elem.textContent.trim().toLowerCase().includes('no online availability')) {
                return true;
            }
        }
        return false;
    };

    // Look for the "no online availability" message, then fall back to other possible
    // unavailability indicators.
    return (
        hasVisibleNoAvailabilityMessage('.ShiftInventory__availability-message') ||
        hasVisibleNoAvailabilityMessage('[id*="unavailable"]')
    );
})();
