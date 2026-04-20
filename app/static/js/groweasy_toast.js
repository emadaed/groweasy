/**
 * GrowEasy Toast Notifications
 * Provides window.showToast() used by form_items.js and other scripts.
 
 */
(function () {
    'use strict';

    // Inject animation styles once
    if (!document.getElementById('ge-toast-styles')) {
        const style = document.createElement('style');
        style.id = 'ge-toast-styles';
        style.textContent = `
            .ge-toast {
                position: fixed;
                top: 80px;
                right: 20px;
                color: #fff;
                padding: 14px 22px;
                border-radius: 10px;
                box-shadow: 0 6px 20px rgba(0,0,0,0.18);
                z-index: 10000;
                font-size: 14px;
                font-weight: 500;
                min-width: 260px;
                max-width: 380px;
                opacity: 0;
                transform: translateX(420px);
                transition: opacity 0.3s ease, transform 0.3s ease;
                pointer-events: none;
            }
            .ge-toast.ge-toast-visible {
                opacity: 1;
                transform: translateX(0);
            }
        `;
        document.head.appendChild(style);
    }

    function showToast(message, bgColor, duration) {
        bgColor = bgColor || '#28a745';
        duration = duration || 3000;

        const toast = document.createElement('div');
        toast.className = 'ge-toast';
        toast.style.background = bgColor;
        toast.textContent = message;
        document.body.appendChild(toast);

        // Trigger animation
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                toast.classList.add('ge-toast-visible');
            });
        });

        setTimeout(function () {
            toast.classList.remove('ge-toast-visible');
            setTimeout(function () { toast.remove(); }, 350);
        }, duration);
    }

    // Expose globally
    window.showToast = showToast;

    window.showSuccessToast = function (message) { showToast(message, '#28a745', 3500); };
    window.showErrorToast   = function (message) { showToast('❌ ' + message, '#dc3545', 4000); };
    window.showWarningToast = function (message) { showToast('⚠️ ' + message, '#856404', 4000); };
    window.showInfoToast    = function (message) { showToast('ℹ️ ' + message, '#0c63e4', 3000); };

}());