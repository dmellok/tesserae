
    (function() {
      var preconnectOrigins = ["https://cdn.shopify.com","https://extensions.shopifycdn.com"];
      var scripts = ["/cdn/shopifycloud/checkout-web/assets/c1/polyfills-legacy.BSC7e5lr.js","/cdn/shopifycloud/checkout-web/assets/c1/app-legacy.DWQP95nk.js","/cdn/shopifycloud/checkout-web/assets/c1/esnext-vendor-legacy.BY-DEEj5.js","/cdn/shopifycloud/checkout-web/assets/c1/context-browser-legacy.CZZ-1E1L.js","/cdn/shopifycloud/checkout-web/assets/c1/UnauthenticatedErrorModalPayload-legacy.BD2F_2K6.js","/cdn/shopifycloud/checkout-web/assets/c1/receipt-mapper-load-recovery-legacy.D46CbOQN.js","/cdn/shopifycloud/checkout-web/assets/c1/receipt-eager-mappers-legacy.Bt1ERjNQ.js","/cdn/shopifycloud/checkout-web/assets/c1/helpers-installmentsNotSupportedForAddress-legacy.6kpnKmKr.js","/cdn/shopifycloud/checkout-web/assets/c1/shop-pay-normalizeBuyerDetails-legacy.0EeooDOo.js","/cdn/shopifycloud/checkout-web/assets/c1/helpers-paymentMethodFromPaymentLines-legacy.BjiHod33.js","/cdn/shopifycloud/checkout-web/assets/c1/graphql-UserPrivacySettingsSetMutation-legacy.BWD83Dt1.js","/cdn/shopifycloud/checkout-web/assets/c1/utils-getCommonShopPayExternalTelemetryAttributes-legacy.DUvOG9JS.js","/cdn/shopifycloud/checkout-web/assets/c1/extensions-rpc-legacy.LwrjdGPB.js","/cdn/shopifycloud/checkout-web/assets/c1/graphql-PaymentSessionMutation-legacy.D-h02J4X.js","/cdn/shopifycloud/checkout-web/assets/c1/hydrate-legacy.Oh3m_3Bv.js","/cdn/shopifycloud/checkout-web/assets/c1/utilities-browser-legacy.C9tfC2zR.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useShopPayExternalAppContext-legacy.AOvQcISp.js","/cdn/shopifycloud/checkout-web/assets/c1/locale-en-legacy.CL_yrr8-.js","/cdn/shopifycloud/checkout-web/assets/c1/OnePage-legacy.LCcCFGkU.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useWalletsTimeout-legacy.B170gAxj.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-usePostPurchase-legacy.wKHC9eKN.js","/cdn/shopifycloud/checkout-web/assets/c1/components-DeliveryTransition-legacy.Codjqbsr.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-usePickupPoints-legacy.XhSSlGFa.js","/cdn/shopifycloud/checkout-web/assets/c1/AddressPresenter-legacy.D-pd71Uj.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useShowShopPayOptin-legacy.Bpu1gP7p.js","/cdn/shopifycloud/checkout-web/assets/c1/NoAddressLocation-legacy.CjZ6U78M.js","/cdn/shopifycloud/checkout-web/assets/c1/OffsitePaymentFailed-legacy.iwwQZN0d.js","/cdn/shopifycloud/checkout-web/assets/c1/Page-legacy.DqFHq4ln.js","/cdn/shopifycloud/checkout-web/assets/c1/ChangeCompanyLocationLink-legacy.BJTqgtRI.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useStableHostMethodsReferences-legacy.gehQfmAr.js","/cdn/shopifycloud/checkout-web/assets/c1/helpers-getNormalizedPaymentMethodName-legacy.DLGzycA6.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useSuppressShopPayModalOnLoad-legacy.Dxs9e7f_.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useSandboxTelemetry-legacy.B86wrw4_.js","/cdn/shopifycloud/checkout-web/assets/c1/BillingAddressForm-legacy.Dfbnri9t.js","/cdn/shopifycloud/checkout-web/assets/c1/PhoneField-legacy.dmPQVCRB.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useCanChangeCompanyLocation-legacy.Bbk42D81.js","/cdn/shopifycloud/checkout-web/assets/c1/EmptyState-legacy.CWKfLEDf.js","/cdn/shopifycloud/checkout-web/assets/c1/Choice-legacy.DE2doGH3.js","/cdn/shopifycloud/checkout-web/assets/c1/Popover-legacy.Dts1x6Uo.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useForceShopPayUrl-legacy.DuJh_EXA.js","/cdn/shopifycloud/checkout-web/assets/c1/ShopPayLogo-legacy.DzgnlszY.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useWalletsMonorailTrack-legacy.Duitm2-m.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useShopPayCheckoutGqlVersion-legacy.DQbsIGuK.js","/cdn/shopifycloud/checkout-web/assets/c1/AutocompleteField-hooks-legacy.ZjXLhxnL.js","/cdn/shopifycloud/checkout-web/assets/c1/PendingShipping-legacy.BAjvIJia.js","/cdn/shopifycloud/checkout-web/assets/c1/ImpressionEventCapture-legacy.CddEyAYx.js","/cdn/shopifycloud/checkout-web/assets/c1/StoreCreditRedemption-StoreCreditRedemptionErrorBanner-legacy.BBZMAD5O.js","/cdn/shopifycloud/checkout-web/assets/c1/PaymentIcon-legacy.ZRtW5Wsw.js","/cdn/shopifycloud/checkout-web/assets/c1/shop-cash-context-legacy.DF0Dvn52.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useGeneralPaymentErrorMessage-legacy.CbreaLDV.js","/cdn/shopifycloud/checkout-web/assets/c1/PaymentLine-legacy.CfWGfqIX.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useShopPayProgressIntercepts-legacy.DHsWP_iZ.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useUpdateCheckoutAddress-legacy.BLg0V0gY.js","/cdn/shopifycloud/checkout-web/assets/c1/Section-legacy.BMBh1OZm.js","/cdn/shopifycloud/checkout-web/assets/c1/remember-me-hooks-legacy.C4PT5Y-g.js","/cdn/shopifycloud/checkout-web/assets/c1/useShopPaySessionTokenStorage-legacy.CwXt5cQ1.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useOnePageFormSubmit-legacy.D6Z36NV6.js","/cdn/shopifycloud/checkout-web/assets/c1/captcha-hooks-legacy.CLucIvNN.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-payment-button-legacy.D8wNeerN.js","/cdn/shopifycloud/checkout-web/assets/c1/shop-cash-monorail-legacy.-rAOHcvn.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-useAvailableShopPromotionDiscount-legacy.CbB2cnZp.js","/cdn/shopifycloud/checkout-web/assets/c1/BillingAddressSelector-legacy.AbLJJpxf.js","/cdn/shopifycloud/checkout-web/assets/c1/PaymentErrorBanner-legacy.y_9_L9Pt.js","/cdn/shopifycloud/checkout-web/assets/c1/Switch-legacy.CeMSPCOd.js","/cdn/shopifycloud/checkout-web/assets/c1/shipping-rates-progressiveShippingRatesLoading-legacy.CvKZliae.js","/cdn/shopifycloud/checkout-web/assets/c1/ShipmentBreakdown-legacy.S4BUf0VP.js","/cdn/shopifycloud/checkout-web/assets/c1/MerchandiseModal-legacy.DP-mxeIw.js","/cdn/shopifycloud/checkout-web/assets/c1/extension-targets-shipping-options-legacy.CSUgZn9E.js","/cdn/shopifycloud/checkout-web/assets/c1/EstimatedDeliveryContent-legacy.CPcLzn70.js","/cdn/shopifycloud/checkout-web/assets/c1/ShippingMethodRateLabel-legacy.DntLulHi.js","/cdn/shopifycloud/checkout-web/assets/c1/ShippingMethodSelector-legacy.CO_pVx5M.js","/cdn/shopifycloud/checkout-web/assets/c1/TextArea-legacy.BQtrzz7v.js","/cdn/shopifycloud/checkout-web/assets/c1/SubscriptionPriceBreakdown-legacy.BJSFZpMg.js","/cdn/shopifycloud/checkout-web/assets/c1/hooks-usePaypalRowEffects-legacy.B57WyMUY.js","/cdn/shopifycloud/checkout-web/assets/c1/Middot-legacy.CkuRHVot.js","/cdn/shopifycloud/checkout-web/assets/c1/StockProblems-StockProblemsLineItemList-legacy.skZjxWq5.js","/cdn/shopifycloud/checkout-web/assets/c1/utilities-publishMessage-legacy.DOsN1NwW.js","/cdn/shopifycloud/checkout-web/assets/c1/component-RuntimeExtension-legacy.D5uwj1hK.js","/cdn/shopifycloud/checkout-web/assets/c1/AnnouncementRuntimeExtensions-legacy.bI6XMuRG.js","/cdn/shopifycloud/checkout-web/assets/c1/QRCode-legacy.CZKUOk0Z.js","/cdn/shopifycloud/checkout-web/assets/c1/utilities-dates-legacy.qoyDsntr.js","/cdn/shopifycloud/checkout-web/assets/c1/NumberField-legacy.CPpUxaY-.js","/cdn/shopifycloud/checkout-web/assets/c1/extensions-remote-dom-legacy.Ddv5jENE.js","/cdn/shopifycloud/checkout-web/assets/c1/EmailField-legacy.B9WmkTCs.js","/cdn/shopifycloud/checkout-web/assets/c1/Sheet-legacy.CEiA5ZsX.js","/cdn/shopifycloud/checkout-web/assets/c1/extension-targets-rendering-extension-targets-legacy.O7girEnx.js","/cdn/shopifycloud/checkout-web/assets/c1/ExtensionsInner-legacy.DE2SJTky.js","/cdn/shopifycloud/checkout-web/assets/c1/adapter-host-legacy.DoH60VC8.js","/cdn/shopifycloud/checkout-web/assets/c1/sandbox.BcTa8xIv.worker.js","/cdn/shopifycloud/checkout-web/assets/c1/sandbox-2025-07.BU1xQJiP.worker.js","https://extensions.shopifycdn.com/shopifycloud/checkout-web/assets/c1/polyfills-entry-legacy.y34Tq78B.worker.js"];
      var styles = [];
      var fontPreconnectUrls = [];
      var fontPrefetchUrls = [];
      var imgPrefetchUrls = ["https://cdn.shopify.com/s/files/1/1374/4477/files/Jamie_Kay_Group_Checkout_Logos_Small_b83e459a-be82-46e4-be21-2711a2858518_x320.png?v=1768945404","https://cdn.shopify.com/s/files/1/1374/4477/files/JAMK-checkout-background_2000x.png?v=1740609347"];

      function preconnect(url, callback) {
        var link = document.createElement('link');
        link.rel = 'dns-prefetch preconnect';
        link.href = url;
        link.crossOrigin = '';
        link.onload = link.onerror = callback;
        document.head.appendChild(link);
      }

      function preconnectAssets() {
        var resources = preconnectOrigins.concat(fontPreconnectUrls);
        var index = 0;
        (function next() {
          var res = resources[index++];
          if (res) preconnect(res, next);
        })();
      }

      function prefetch(url, as, callback) {
        var link = document.createElement('link');
        if (link.relList.supports('prefetch')) {
          link.rel = 'prefetch';
          link.fetchPriority = 'low';
          link.as = as;
          if (as === 'font') link.type = 'font/woff2';
          link.href = url;
          link.crossOrigin = '';
          link.onload = link.onerror = callback;
          document.head.appendChild(link);
        } else {
          var xhr = new XMLHttpRequest();
          xhr.open('GET', url, true);
          xhr.onloadend = callback;
          xhr.send();
        }
      }

      function prefetchAssets() {
        var resources = [].concat(
          scripts.map(function(url) { return [url, 'script']; }),
          styles.map(function(url) { return [url, 'style']; }),
          fontPrefetchUrls.map(function(url) { return [url, 'font']; }),
          imgPrefetchUrls.map(function(url) { return [url, 'image']; })
        );
        var index = 0;
        function run() {
          var res = resources[index++];
          if (res) prefetch(res[0], res[1], next);
        }
        var next = (self.requestIdleCallback || setTimeout).bind(self, run);
        next();
      }

      function onLoaded() {
        try {
          if (parseFloat(navigator.connection.effectiveType) > 2 && !navigator.connection.saveData) {
            preconnectAssets();
            prefetchAssets();
          }
        } catch (e) {}
      }

      if (document.readyState === 'complete') {
        onLoaded();
      } else {
        addEventListener('load', onLoaded);
      }
    })();
  