(() => {
    /**
     * Simple check for "no online availability" message on Resy pages.
     * Returns true if the message is found and visible, false otherwise.
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
