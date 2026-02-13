(() => {

    const disablePrinting = () => {
        if (window.__printGuardInstalled__) return;
        window.__printGuardInstalled__ = true;

        const log = (...a) => { try { console.debug('[print-guard]', ...a); } catch { } };
        const noop = () => log('window.print() intercepted');

        // Disable window.print in this page
        try {
            Object.defineProperty(window, 'print', { configurable: true, writable: true, value: noop });
        } catch {
            try { window.print = noop; } catch { }
        }
    };

    const disableNewTabs = () => {
        // 1. Remove target from all elements that could open new windows
        const removeTargets = () => {
            document.querySelectorAll('[target], [formtarget]').forEach(el => {
                const target = el.getAttribute('target') || el.getAttribute('formtarget');
                if (target && target !== '_self' && target !== '_parent' && target !== '_top') {
                    el.removeAttribute('target');
                    el.removeAttribute('formtarget');
                }
            });
        };
        removeTargets();

        // 2. Override window.open - make it non-configurable to prevent sites from overwriting
        // Check if already redefined to avoid "Cannot redefine property" error on repeated calls
        const openDescriptor = Object.getOwnPropertyDescriptor(window, 'open');
        if (!openDescriptor || openDescriptor.configurable !== false) {
            Object.defineProperty(window, 'open', {
                value: function (url, name, specs) {
                    if (typeof url === 'string' && url && !url.startsWith('about:')) {
                        window.location.href = url;
                    }
                    return { closed: false, focus: () => { }, blur: () => { }, close: () => { }, postMessage: () => { } };
                },
                writable: false,
                configurable: false
            });
        }

        // 3. Intercept setAttribute to catch form.setAttribute('target', ...)
        // Use a marker to avoid wrapping multiple times
        if (!Element.prototype._setAttributePatched) {
            const originalSetAttribute = Element.prototype.setAttribute;
            Element.prototype.setAttribute = function (name, value) {
                if ((name.toLowerCase() === 'target' || name.toLowerCase() === 'formtarget') &&
                    value && value !== '_self' && value !== '_parent' && value !== '_top') {
                    return; // Block it
                }
                return originalSetAttribute.call(this, name, value);
            };
            Element.prototype._setAttributePatched = true;
        }

        // 4. Prevent form.target from being set to new-tab values (allow _self, _parent, _top)
        if (!HTMLFormElement.prototype._targetPatched) {
            Object.defineProperty(HTMLFormElement.prototype, 'target', {
                set: function (val) {
                    if (!val || val === '_self' || val === '_parent' || val === '_top') {
                        this.setAttribute('target', val || '');
                    }
                    // Otherwise silently block (e.g., _blank or named targets)
                },
                get: function () { return this.getAttribute('target') || ''; },
                configurable: true
            });
            HTMLFormElement.prototype._targetPatched = true;
        }

        // 5. Prevent anchor.target from being set to new-tab values (allow _self, _parent, _top)
        if (!HTMLAnchorElement.prototype._targetPatched) {
            Object.defineProperty(HTMLAnchorElement.prototype, 'target', {
                set: function (val) {
                    if (!val || val === '_self' || val === '_parent' || val === '_top') {
                        this.setAttribute('target', val || '');
                    }
                    // Otherwise silently block (e.g., _blank or named targets)
                },
                get: function () { return this.getAttribute('target') || ''; },
                configurable: true
            });
            HTMLAnchorElement.prototype._targetPatched = true;
        }

        // 6. Monitor form submissions to ensure bad targets are removed (preserving _self, _parent, _top)
        if (!window._submitListenerPatched) {
            document.addEventListener('submit', (e) => {
                const target = e.target.getAttribute('target');
                if (target && target !== '_self' && target !== '_parent' && target !== '_top') {
                    e.target.removeAttribute('target');
                }
            }, true);
            window._submitListenerPatched = true;
        }

        // 7. Watch for new elements with target attributes
        if (!window._mutationObserverPatched) {
            new MutationObserver(removeTargets).observe(document.documentElement, {
                childList: true, subtree: true, attributes: true, attributeFilter: ['target', 'formtarget']
            });
            window._mutationObserverPatched = true;
        }
    };

    const replaceNativeSelectDropdown = (input = null) => {
        const handledSelectElementsConvergence = new WeakSet();
        let activeSelectElement = null;
        const rootElement = input || document.documentElement;

        function createCustomSelectElement() {
            const customSelect = document.createElement('div');
            customSelect.id = 'yutori-custom-dropdown-element';
            customSelect.style.position = 'absolute';
            customSelect.style.zIndex = 2147483646;
            customSelect.style.display = 'none';
            document.body.appendChild(customSelect);

            const optionsList = document.createElement('div');
            optionsList.style.border = '1px solid #ccc';
            optionsList.style.backgroundColor = '#fff';
            optionsList.style.color = 'black';
            customSelect.appendChild(optionsList);

            return customSelect;
        }

        function hideCustomSelect(customSelect) {
            customSelect.style.display = 'none';
            activeSelectElement = null;
        }

        function showCustomSelect(select) {
            activeSelectElement = select;
            const customSelect = rootElement.querySelector('#yutori-custom-dropdown-element');
            const optionsList = customSelect.firstChild;
            optionsList.innerHTML = '';
            optionsList.style.overflowY = 'auto';
            optionsList.style.maxHeight = 'none';

            Array.from(select.options).forEach(option => {
                const customOption = document.createElement('div');
                customOption.className = 'custom-option';
                customOption.style.padding = '8px';
                customOption.style.cursor = 'pointer';
                customOption.textContent = option.text;
                customOption.dataset.value = option.value;
                optionsList.appendChild(customOption);

                customOption.addEventListener('mouseenter', () => {
                    customOption.style.backgroundColor = '#f0f0f0';
                });

                customOption.addEventListener('mouseleave', () => {
                    customOption.style.backgroundColor = '';
                });

                customOption.addEventListener('mousedown', (e) => {
                    e.stopPropagation();
                    select.value = customOption.dataset.value;
                    hideCustomSelect(customSelect);
                    if (!window.location.href.includes('resy.com')) {
                        select.dispatchEvent(new InputEvent('focus', { bubbles: true, cancelable: true }));
                    }
                    select.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true }));
                    select.dispatchEvent(new InputEvent('change', { bubbles: true, cancelable: true }));
                    select.dispatchEvent(new InputEvent('blur', { bubbles: true, cancelable: true }));
                });
            });

            const selectRect = select.getBoundingClientRect();
            customSelect.style.visibility = 'hidden';
            customSelect.style.display = 'block';

            const margin = 8;
            const viewportWidth = window.innerWidth;
            const minWidth = Math.max(selectRect.width, 120);
            const contentWidth = optionsList.scrollWidth + 8; // small buffer for padding/border
            const maxWidth = Math.max(0, viewportWidth - margin * 2);
            const targetWidth = Math.min(Math.max(minWidth, contentWidth), maxWidth);

            const preferredLeft = selectRect.left + window.scrollX;
            const viewportLeft = window.scrollX + margin;
            const viewportRight = window.scrollX + viewportWidth - margin;
            const clampedLeft = Math.min(Math.max(preferredLeft, viewportLeft), viewportRight - targetWidth);

            customSelect.style.width = `${targetWidth}px`;
            customSelect.style.left = `${clampedLeft}px`;

            const optionsHeight = optionsList.scrollHeight;
            const viewportTop = window.scrollY + margin;
            const viewportBottom = window.scrollY + window.innerHeight - margin;
            const maxHeight = Math.max(0, viewportBottom - viewportTop);
            const desiredHeight = Math.min(optionsHeight, maxHeight);

            const spaceBelow = window.innerHeight - selectRect.bottom - margin;
            const spaceAbove = selectRect.top - margin;

            optionsList.style.maxHeight = `${desiredHeight}px`;

            let dropdownTop;
            if (spaceBelow >= desiredHeight) {
                dropdownTop = selectRect.bottom + window.scrollY;
            } else if (spaceAbove >= desiredHeight) {
                dropdownTop = selectRect.top + window.scrollY - desiredHeight;
            } else {
                const centeredTop = selectRect.top + window.scrollY + (selectRect.height / 2) - (desiredHeight / 2);
                dropdownTop = Math.min(Math.max(centeredTop, viewportTop), viewportBottom - desiredHeight);
            }

            customSelect.style.top = `${dropdownTop}px`;
            customSelect.style.visibility = 'visible';
            select.focus();

            if (!optionsList.dataset.wheelHandlerAttached) {
                optionsList.addEventListener('wheel', (event) => {
                    event.preventDefault();
                    optionsList.scrollTop += event.deltaY;
                }, { passive: false });
                optionsList.dataset.wheelHandlerAttached = 'true';
            }

            select.addEventListener('blur', () => {
                hideCustomSelect(customSelect);
            });

            select.addEventListener('change', () => {
                hideCustomSelect(customSelect);
            });
        }

        let customSelect = rootElement.querySelector('#yutori-custom-dropdown-element');
        if (!customSelect) {
            customSelect = createCustomSelectElement();
        }

        function findSelectInShadowRoot(element) {
            return element.shadowRoot ? element.shadowRoot.querySelectorAll('select') : [];
        }

        let shadowSelects = [];
        rootElement.querySelectorAll('*').forEach(el => {
            shadowSelects.push(...findSelectInShadowRoot(el));
        });

        const lightSelects = Array.from(rootElement.querySelectorAll('select'));
        const allSelects = [...lightSelects, ...shadowSelects];

        allSelects.forEach(select => {
            if (select.hasAttribute('multiple')) return;
            if (!handledSelectElementsConvergence.has(select)) {
                select.addEventListener('mousedown', (e) => {
                    if (!e.defaultPrevented) {
                        if (customSelect.style.display === 'block' && activeSelectElement === select) {
                            hideCustomSelect(customSelect);
                        } else {
                            showCustomSelect(select);
                        }
                        e.preventDefault();
                    }
                });
                handledSelectElementsConvergence.add(select);
            }
        });
    };

    // Check document.readyState
    if (document.readyState !== 'complete') return false;

    // Check if there are any active network requests
    if (window.performance && window.performance.getEntriesByType) {
        {
            const resources = window.performance.getEntriesByType('resource');
            const pendingResources = resources.filter(r => !r.responseEnd);
            if (pendingResources.length > 0) return false;
        }
    }

    disablePrinting();
    disableNewTabs();
    replaceNativeSelectDropdown();

    return true;

})();