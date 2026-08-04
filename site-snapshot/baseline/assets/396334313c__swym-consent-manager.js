(function() {
  "use strict";

  // ============================================
  // SwymConsentManager - Handles consent state
  // ============================================
  window.SwymConsentManager = window.SwymConsentManager || {
    hasConsent: null, // null = unknown, true = granted, false = denied

    // Check if user has given consent
    checkConsent() {
      const shopify = window.Shopify;
      const privacy = shopify?.customerPrivacy;
      
      // In design mode, always treat as consented
      if (shopify?.designMode) {
        this.hasConsent = true;
        return true;
      }
      
      const consent = privacy?.preferencesProcessingAllowed?.();
      
      if (consent === true) {
        this.hasConsent = true;
        return true;
      } else if (consent === false) {
        this.hasConsent = false;
        return false;
      }
      
      // Consent not yet determined - treat as no consent for safety
      this.hasConsent = false;
      return false;
    },

    // Set consent after user accepts
    async grantConsent() {
      const privacy = window.Shopify?.customerPrivacy;
      
      if (privacy?.setTrackingConsent) {
        try {
          await new Promise((resolve, reject) => {
            privacy.setTrackingConsent(
              {
                preferences: true,
                analytics: true,
                marketing: false
              },
              resolve,
              reject
            );
          });
          this.hasConsent = true;
          
          // Dispatch event for other components
          document.dispatchEvent(new CustomEvent('visitorConsentCollected'));
          
          return true;
        } catch (error) {
          console.warn('[SWYM] Failed to set consent via Shopify API:', error);
          return false;
        }
      }
      
      console.warn('[SWYM] Shopify Customer Privacy API not available');
      return false;
    }
  };

  // ============================================
  // SwymConsentPopupManager - Handles popup UI
  // ============================================
  window.SwymConsentPopupManager = window.SwymConsentPopupManager || {
    popupElement: null,
    isInitialized: false,
    pendingAction: null,
    source: null, // For instrumentation (e.g., 'advanced-pdp-button', 'advanced-header-icon')
    _isActive: false, // Whether this manager is currently controlling the popup

    init() {
      if (this.isInitialized) return;
      
      this.popupElement = document.getElementById('swym-consent-popup');
      if (!this.popupElement) {
        return;
      }

      this.bindEvents();
      this.isInitialized = true;
    },

    bindEvents() {
      // Handle close button and overlay clicks
      this.popupElement.querySelectorAll('[data-swym-consent-close]').forEach(el => {
        el.addEventListener('click', (e) => {
          e.preventDefault();
          this.hide();
        });
      });

      // Handle Accept button
      const acceptBtn = this.popupElement.querySelector('[data-swym-consent-accept]');
      if (acceptBtn) {
        acceptBtn.addEventListener('click', async (e) => {
          e.preventDefault();
          await this.handleAccept();
        });
      }

      // Handle Decline button
      const declineBtn = this.popupElement.querySelector('[data-swym-consent-decline]');
      if (declineBtn) {
        declineBtn.addEventListener('click', (e) => {
          e.preventDefault();
          this.handleDecline();
        });
      }

      // Handle Escape key
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.isVisible()) {
          this.hide();
        }
      });
    },

    // Show popup with options
    // options: { source: string, pendingAction: function, localOnly: boolean, nudge: boolean, itemCount: number, isFullCapacity: boolean }
    show(options = {}) {
      if (!this.popupElement) {
        this.init();
      }
      if (!this.popupElement) return false;

      this.pendingAction = options.pendingAction || null;
      this.source = options.source || 'unknown';
      this._ghostIntentLocalOnly = options.localOnly || false;
      this._isNudge = options.nudge || false;
      this._isFullCapacity = options.isFullCapacity || false;
      this._lastItemCount = options.itemCount || 0;
      this._isActive = true;
      
      // Swap popup text for Mode 2 "device only" vs full consent
      this._updatePopupText(this._ghostIntentLocalOnly);

      // Replace {{itemCount}} placeholder with dynamic count
      if (options.itemCount != null) {
        var messageEl = this.popupElement.querySelector('.swym-consent-popup-message');
        if (messageEl) {
          messageEl.textContent = messageEl.textContent.replace('{{itemCount}}', String(options.itemCount));
        }
      }
      
      this.popupElement.setAttribute('aria-hidden', 'false');
      
      // Instrument: GDPR Consent Popup Shown (804)
      window._swat?.instrumentV3?.(804, {
        src: this.source,
        action: 'gdpr-consent-popup-shown'
      });
      
      // Focus the accept button for accessibility
      const acceptBtn = this.popupElement.querySelector('[data-swym-consent-accept]');
      if (acceptBtn) {
        setTimeout(() => acceptBtn.focus(), 100);
      }
      
      return true;
    },

    hide() {
      if (!this.popupElement) return;
      
      // Clear variant flags BEFORE restoring text so _updatePopupText
      // hits the restore branch (not the nudge/localOnly branches)
      this._ghostIntentLocalOnly = false;
      this._isNudge = false;
      this._isFullCapacity = false;
      
      // Restore original popup text
      this._updatePopupText(false);
      
      this.popupElement.setAttribute('aria-hidden', 'true');
      this.pendingAction = null;
      this.source = null;
      this._isActive = false;
    },

    isVisible() {
      return this.popupElement?.getAttribute('aria-hidden') === 'false';
    },

    /**
     * Swap popup text between full consent, Mode 2 "device only", and nudge variants.
     * Reads variant text from data attributes set by Liquid (supports i18n).
     */
    _updatePopupText(isLocalOnly) {
      if (!this.popupElement) return;

      const titleEl = this.popupElement.querySelector('.swym-consent-popup-title');
      const messageEl = this.popupElement.querySelector('.swym-consent-popup-message');
      const acceptBtn = this.popupElement.querySelector('[data-swym-consent-accept] .swym-consent-btn-text');
      const declineBtn = this.popupElement.querySelector('[data-swym-consent-decline]');
      const noteEl = this.popupElement.querySelector('.swym-consent-popup-note');

      // Save original content on first swap
      if (!this._originalText && (isLocalOnly || this._isNudge)) {
        this._originalText = {
          title: titleEl?.textContent || '',
          messageHTML: messageEl?.innerHTML || '',
          accept: acceptBtn?.textContent || '',
          decline: declineBtn?.textContent || ''
        };
      }

      if (isLocalOnly) {
        // Use Mode 2 "device only" text from data attributes
        const localTitle = this.popupElement.getAttribute('data-local-only-title');
        const localMessage = this.popupElement.getAttribute('data-local-only-message');
        const localNote = this.popupElement.getAttribute('data-local-only-note');
        const localAccept = this.popupElement.getAttribute('data-local-only-accept');
        const localDecline = this.popupElement.getAttribute('data-local-only-decline');

        if (titleEl && localTitle) titleEl.textContent = localTitle;
        if (messageEl && localMessage) {
          // Build two-paragraph layout with bold note
          var html = '<p style="margin:0 0 12px">' + this._escapeHTML(localMessage) + '</p>';
          if (localNote) {
            html += '<p style="margin:0"><strong>Note:</strong> <strong>' + this._escapeHTML(localNote) + '</strong></p>';
          }
          messageEl.innerHTML = html;
        }
        if (acceptBtn && localAccept) acceptBtn.textContent = localAccept;
        if (declineBtn && localDecline) declineBtn.textContent = localDecline;
        // Hide note for Mode 2 popup
        if (noteEl) noteEl.style.display = 'none';
      } else if (this._isNudge) {
        // Ghost Intent nudge popup — swap to ghost-specific text from data attributes
        var ghostTitle = this.popupElement.getAttribute('data-ghost-title');
        var ghostMessage = this.popupElement.getAttribute('data-ghost-message');
        var ghostDisclaimer = this.popupElement.getAttribute('data-ghost-disclaimer');
        var ghostAccept = this.popupElement.getAttribute('data-ghost-accept');
        var ghostDecline = this.popupElement.getAttribute('data-ghost-decline');

        if (titleEl && ghostTitle) titleEl.textContent = ghostTitle;
        if (messageEl && ghostMessage) messageEl.textContent = ghostMessage;
        if (acceptBtn && ghostAccept) acceptBtn.textContent = ghostAccept;
        if (declineBtn && ghostDecline) declineBtn.textContent = ghostDecline;

        // Use full-capacity message variant if at 5th click or blocked
        if (this._isFullCapacity) {
          var fullMessage = this.popupElement.getAttribute('data-ghost-message-full');
          if (messageEl && fullMessage) messageEl.textContent = fullMessage;
        }

        // Show disclaimer for ghost nudge popup
        if (noteEl && ghostDisclaimer) {
          noteEl.textContent = ghostDisclaimer;
          noteEl.style.display = 'block';
        }
      } else {
        // Regular consent popup — restore original content, hide disclaimer
        if (noteEl) noteEl.style.display = 'none';
        if (this._originalText) {
          if (titleEl) titleEl.textContent = this._originalText.title;
          if (messageEl) messageEl.innerHTML = this._originalText.messageHTML;
          if (acceptBtn) acceptBtn.textContent = this._originalText.accept;
          if (declineBtn) declineBtn.textContent = this._originalText.decline;
        }
      }
    },

    /**
     * Escape HTML special characters to prevent XSS when building innerHTML.
     */
    _escapeHTML(str) {
      var div = document.createElement('div');
      div.appendChild(document.createTextNode(str));
      return div.innerHTML;
    },

    async handleAccept() {
      // Guard: skip if popup was not shown by this manager
      if (!this._isActive) return;

      const acceptBtn = this.popupElement?.querySelector('[data-swym-consent-accept]');
      
      // Show spinner on accept button
      if (acceptBtn) {
        acceptBtn.classList.add('swym-loading');
        acceptBtn.disabled = true;
      }
      
      // Instrument: GDPR Consent Accepted (805)
      window._swat?.instrumentV3?.(805, {
        src: this.source,
        action: 'gdpr-consent-accepted'
      });
      
      let success = true;
      if (this._ghostIntentLocalOnly) {
        // Mode 2 local-only accept — persist the acknowledgment so popup doesn't show again
        window.SwymLocalWishlist?.setLocalConsentGiven?.();
      } else {
        // Full consent (sync nudge accept) — mark Ghost-Intent consent BEFORE grantConsent()
        // because grantConsent() dispatches 'visitorConsentCollected' which triggers
        // checkConsentAndLoad() in wishlist-app-embed.liquid. The localStorage flag must
        // be set BEFORE that event fires so the SDK loading check sees consent is given.
        window.SwymLocalWishlist?.setConsentGiven?.();
        success = await SwymConsentManager.grantConsent();
        if (!success) {
          // Rollback consent flag if Shopify consent failed
          try {
            var consentKey = 'swym-ghost-consent-' + (window.Shopify?.shop || '');
            localStorage.removeItem(consentKey);
          } catch(e) {}
        }
      }
      
      // Remove spinner
      if (acceptBtn) {
        acceptBtn.classList.remove('swym-loading');
        acceptBtn.disabled = false;
      }
      
      // Save state before hide() clears it
      const actionToExecute = this.pendingAction;
      const wasNudge = this._isNudge;
      const wasLocalOnly = this._ghostIntentLocalOnly;
      
      this.hide();
      
      // Execute pending action (component-specific logic)
      if (success && actionToExecute && typeof actionToExecute === 'function') {
        try {
          await actionToExecute();
        } catch (error) {
          console.warn('[SWYM] Pending action failed:', error);
        }
      }

      // After nudge accept + consent granted, redirect to login page (not for localOnly)
      if (success && wasNudge && !wasLocalOnly) {
        window.location.href = this._getLoginUrl();
        return;
      }
    },

    handleDecline() {
      // Guard: skip if popup was not shown by this manager
      if (!this._isActive) return;

      // Instrument: GDPR Consent Declined (806)
      window._swat?.instrumentV3?.(806, {
        src: this.source,
        action: 'gdpr-consent-declined'
      });
      
      if (this._ghostIntentLocalOnly) {
        // Mode 2 "device only" popup decline — just close, no blocking.
        // Popup will re-show on the next heart click until user accepts.
        this.hide();
        console.log('[SWYM] User declined local-only consent — popup will re-show on next click');
        return;
      }

      // Progressive consent decline logic for sync nudge
      if (this._isNudge && window.SwymLocalWishlist) {
        var currentStage = window.SwymLocalWishlist.getDeclineStage();
        if (currentStage === 0) {
          // First decline: advance to stage 1, reset counter so next popup triggers at 5 total clicks
          window.SwymLocalWishlist.advanceDeclineStage();
          this.hide();
          console.log('[SWYM] First consent decline — will show again after 2 more add clicks');
          return;
        }
        if (currentStage === 1) {
          // Second decline: show confirmation alert immediately
          this.hide();
          this._showConsentConfirmationAlert();
          return;
        }
      }

      this.hide();
      console.log('[SWYM] User declined consent');
    },

    /**
     * Show confirmation alert after second consent decline.
     * Gives the user a last chance before blocking wishlist for 30 days.
     */
    _showConsentConfirmationAlert() {
      var alertEl = document.getElementById('swym-consent-confirm-alert');
      if (!alertEl) return;

      // Replace {{itemCount}} in confirmation message
      var confirmMsgEl = alertEl.querySelector('.swym-consent-confirm-message');
      if (confirmMsgEl) {
        var template = confirmMsgEl.getAttribute('data-confirm-message-template') || confirmMsgEl.textContent;
        confirmMsgEl.textContent = template.replace('{{itemCount}}', String(this._lastItemCount || 0));
      }

      alertEl.setAttribute('aria-hidden', 'false');

      // Instrument: Confirmation alert shown
      window._swat?.instrumentV3?.(804, {
        src: 'ghost-intent-confirm-alert',
        action: 'gdpr-consent-confirm-shown'
      });

      // Bind event handlers (idempotent — uses data attribute to avoid double-binding)
      if (!alertEl._swymBound) {
        alertEl._swymBound = true;
        var self = this;

        // "Give consent" button — accept
        var acceptBtn = alertEl.querySelector('[data-swym-confirm-accept]');
        if (acceptBtn) {
          acceptBtn.addEventListener('click', async function(e) {
            e.preventDefault();
            alertEl.setAttribute('aria-hidden', 'true');
            // Reset consent state and grant full consent
            window.SwymLocalWishlist?.resetConsentState?.();
            window.SwymLocalWishlist?.setConsentGiven?.();
            var success = await SwymConsentManager.grantConsent();
            if (!success) {
              // Rollback
              try {
                var consentKey = 'swym-ghost-consent-' + (window.Shopify?.shop || '');
                localStorage.removeItem(consentKey);
              } catch(err) {}
            }

            // Instrument: Consent accepted from confirmation
            window._swat?.instrumentV3?.(805, {
              src: 'ghost-intent-confirm-alert',
              action: 'gdpr-consent-accepted-from-confirm'
            });

            // Redirect to login page
            if (success) {
              window.location.href = self._getLoginUrl();
            }
          });
        }

        // "Yes, decline" button — block permanently until consent
        var declineBtn = alertEl.querySelector('[data-swym-confirm-decline]');
        if (declineBtn) {
          declineBtn.addEventListener('click', function(e) {
            e.preventDefault();
            alertEl.setAttribute('aria-hidden', 'true');
            // Block wishlist permanently until user accepts consent
            window.SwymLocalWishlist?.setConsentBlocked?.();
            console.log('[SWYM] User confirmed final decline — wishlist blocked until consent given');

            // Instrument: Consent final decline
            window._swat?.instrumentV3?.(806, {
              src: 'ghost-intent-confirm-alert',
              action: 'gdpr-consent-final-decline'
            });
          });
        }

        // Overlay close
        var overlay = alertEl.querySelector('[data-swym-confirm-close]');
        if (overlay) {
          overlay.addEventListener('click', function(e) {
            e.preventDefault();
            // Closing overlay without choosing = just close (don't auto-block)
            alertEl.setAttribute('aria-hidden', 'true');
          });
        }

        // Escape key
        document.addEventListener('keydown', function(e) {
          if (e.key === 'Escape' && alertEl.getAttribute('aria-hidden') === 'false') {
            alertEl.setAttribute('aria-hidden', 'true');
          }
        });
      }
    },

    /**
     * Show a non-blocking toast: "Items saved! Log in to access on other devices"
     * Auto-dismisses after 8 seconds. CTA links to /account/login.
     */
    _showLoginToast() {
      // Avoid duplicate toasts
      const existing = document.querySelector('.swym-ghost-toast');
      if (existing) existing.remove();

      const message = this._getToastText('message');
      const cta = this._getToastText('cta');

      const toast = document.createElement('div');
      toast.className = 'swym-ghost-toast';
      toast.setAttribute('role', 'status');
      toast.setAttribute('aria-live', 'polite');
      toast.innerHTML =
        '<span class="swym-ghost-toast-message">' + message + '</span>' +
        '<a href="' + this._escapeHTML(this._getLoginUrl()) + '" class="swym-ghost-toast-cta">' + cta + '</a>' +
        '<button class="swym-ghost-toast-close" aria-label="Close">&times;</button>';
      document.body.appendChild(toast);

      // Animate in
      requestAnimationFrame(function() {
        toast.classList.add('swym-ghost-toast-visible');
      });

      // Close button
      toast.querySelector('.swym-ghost-toast-close').addEventListener('click', function() {
        toast.classList.remove('swym-ghost-toast-visible');
        setTimeout(function() { toast.remove(); }, 300);
      });

      // Auto-dismiss after 8 seconds
      setTimeout(function() {
        if (toast.parentElement) {
          toast.classList.remove('swym-ghost-toast-visible');
          setTimeout(function() { toast.remove(); }, 300);
        }
      }, 8000);
    },

    /**
     * Return the login URL from the popup element's data-login-url attribute.
     * This is rendered by Liquid as routes.storefront_login_url, which is a
     * Shopify-native URL that returns the customer to the originating storefront
     * page after sign-in — including through the New Customer Accounts OAuth flow.
     * @returns {string}
     */
    _getLoginUrl() {
      return (this.popupElement && this.popupElement.getAttribute('data-login-url')) || '/customer_authentication/login';
    },

    /**
     * Get translated toast text from data attributes on the popup element.
     * Falls back to English defaults.
     */
    _getToastText(key) {
      if (this.popupElement) {
        var value = this.popupElement.getAttribute('data-toast-' + key);
        if (value) return value;
      }
      // Fallback defaults
      if (key === 'message') return 'Item saved! Log in to sync across all your devices.';
      if (key === 'cta') return 'Log in to sync';
      return '';
    }
  };

  // Auto-initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      SwymConsentPopupManager.init();
    });
  } else {
    SwymConsentPopupManager.init();
  }
})();
