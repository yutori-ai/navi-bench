(() => {
    /**
     * Simple check for "no online availability" message on Resy pages.
     * Returns true if the message is found and visible, false otherwise.
     *
     * Relies on `isVisible` being defined by the shared ../dom_visibility.js preamble
     * (see resy_url_match.py's js_script property), which loads it into scope ahead of
     * this IIFE.
     */

    // Look for the "no online availability" message
    const availabilityMessages = document.querySelectorAll('.ShiftInventory__availability-message');
    
    for (const message of availabilityMessages) {
        if (isVisible(message)) {
            const text = message.textContent.trim().toLowerCase();
            
            // Check if this is a "no availability" message
            if (text.includes('no online availability')) {
                return true;
            }
        }
    }

    // Also check for other possible unavailability indicators
    const unavailableElements = document.querySelectorAll('[id*="unavailable"]');
    for (const elem of unavailableElements) {
        if (isVisible(elem)) {
            const text = elem.textContent.trim().toLowerCase();
            if (text.includes('no online availability')) {
                return true;
            }
        }
    }

    return false;
})();
