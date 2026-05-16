"""
AlphaAbsolute — Central SSL/WARP Bypass Patch
==============================================
Cloudflare WARP intercepts TLS for some domains (fc.yahoo.com, stooq.com etc.)
and presents a self-signed cert. This module patches Python SSL + requests globally
so ALL HTTP calls in the process use verify=False.

Import at the TOP of any script that will run in this environment:
    from utils.ssl_patch import apply  # noqa
    apply()

Also patches yfinance to use the unverified session so .history() works.
Must be called BEFORE importing yfinance.
"""

import os
import ssl
import sys

_APPLIED = False


def apply():
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    # 1. Python ssl module — unverified default context
    ssl._create_default_https_context = ssl._create_unverified_context  # type: ignore

    # 2. Environment variables — disable cert verification for curl/httpx/aiohttp
    os.environ["CURL_CA_BUNDLE"]    = ""
    os.environ["REQUESTS_CA_BUNDLE"] = ""
    os.environ["SSL_CERT_FILE"]     = ""
    os.environ["PYTHONHTTPSVERIFY"] = "0"

    # 3. requests — patch Session to always pass verify=False
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        import requests
        from requests import Session as _Sess
        _orig = _Sess.request
        def _patched(self, *args, **kwargs):
            kwargs.setdefault("verify", False)
            return _orig(self, *args, **kwargs)
        _Sess.request = _patched
    except Exception:
        pass

    # 4. httpx (used by newer yfinance builds)
    try:
        import httpx
        _orig_init = httpx.Client.__init__
        def _patched_init(self, *args, **kwargs):
            kwargs.setdefault("verify", False)
            _orig_init(self, *args, **kwargs)
        httpx.Client.__init__ = _patched_init
    except Exception:
        pass

    # 5. yfinance — swap its download session to use verify=False
    try:
        import yfinance as yf
        import yfinance.data as _yfd
        # yfinance 0.2.x: uses YfData singleton with requests_cache.CachedSession
        if hasattr(_yfd, "YfData"):
            orig_call = _yfd.YfData.__init__
            def _yfd_init(self, *args, **kwargs):
                orig_call(self, *args, **kwargs)
                if hasattr(self, "_data") and hasattr(self._data, "verify"):
                    self._data.verify = False
                if hasattr(self, "session") and self.session and hasattr(self.session, "verify"):
                    self.session.verify = False
            _yfd.YfData.__init__ = _yfd_init
    except Exception:
        pass


# Auto-apply when imported
apply()
