"""Transport layer: fetch official IRD/GovHK pages despite the broken local TUN proxy.

Network diagnosis (2026-10-13, recorded for the 002 report):

  The workstation runs a Clash.Meta-style TUN proxy with fake-ip DNS.  Every DNS
  answer for *.gov.hk is a fake address (198.18.4.x / 2001:2::3e8) and every
  outbound TLS connection whose ClientHello carries SNI "*.gov.hk" stalls until
  timeout - both through the TUN device and through the local mixed proxy on
  127.0.0.1:17890 (CONNECT tunnel is accepted, then the upstream TLS handshake
  hangs).  google/github/baidu work fine through the same proxy, other DNS
  resolvers (8.8.8.8, 223.5.5.5 UDP:53) are hijacked to the same fake-ip, and
  flushdns does not help.  See docs/validation/002 report section 5 for the
  full list of failed attempts.

  Workaround that *does* work: the official IRD/GovHK sites are served from a
  Tencent EdgeOne edge (real IP discovered live at run time via the
  networkcalc.com DNS API through the working local proxy).  Connecting to that
  real IP over TLS *without SNI* (server_hostname=None) while keeping the
  original "Host:" header reaches the official server and returns the official
  bytes unchanged (verified 200 OK for every page used here).

  This module therefore provides official_get() which Playwright route handlers
  use to fulfill browser requests: the browser still runs the original page
  from its official origin; only the transport hop is substituted.  No page
  content is cached between runs - everything is fetched live.
"""
from __future__ import annotations

import http.client
import json
import ssl
import socket
import urllib.request
from urllib.parse import urljoin, urlparse

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Verified live on 2026-10-13 via networkcalc API (used only as a fallback if
# the resolver API is unreachable; every run re-resolves first).
EDGE_FALLBACK = {
    "www.ird.gov.hk": "43.159.109.162",
    "www.gov.hk": "43.159.109.162",
}


class NoSniHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection variant that connects to a fixed IP without sending SNI.

    仅用于显式开启的 insecure no-SNI 模式（模块 docstring 记录的本机 TUN
    代理故障绕行）。该模式必须禁用证书校验，因此抓取结果不得视为经认证
    的官方证据；默认模式不使用本类。
    """

    def __init__(self, real_ip: str, **kw) -> None:
        super().__init__(**kw)
        self._real_ip = real_ip

    def connect(self) -> None:  # noqa: D102 - see module docstring
        sock = socket.create_connection((self._real_ip, self.port), self.timeout)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # server_hostname=None -> no ServerNameIndication extension is sent.
        self.sock = ctx.wrap_socket(sock, server_hostname=None)


class OfficialFetcher:
    """Fetches official https pages.

    默认安全模式：正常 DNS + SNI + 完整 TLS 证书校验（ssl 默认上下文）。
    insecure_no_sni=True 时启用本机 TUN 代理故障环境的已记录绕行（真实
    边缘 IP 直连 + 无 SNI + 证书校验关闭）；该模式每次抓取都会在 log
    标注「未认证」，报告据此不得宣称官方认证证据。
    """

    def __init__(
        self,
        timeout: float = 25.0,
        local_proxy: str | None = None,
        insecure_no_sni: bool = False,
    ) -> None:
        self.timeout = timeout
        self.local_proxy = local_proxy
        self.insecure_no_sni = bool(insecure_no_sni)
        self._ip_cache: dict[str, str] = {}
        self.log: list[str] = []
        if self.insecure_no_sni:
            self.log.append(
                "INSECURE no-SNI transport enabled: TLS certificate "
                "verification DISABLED - fetched bytes are NOT authenticated "
                "official evidence"
            )

    # -- host resolution --------------------------------------------------
    def resolve(self, host: str) -> str:
        if host in self._ip_cache:
            return self._ip_cache[host]
        ip = self._resolve_via_networkcalc(host)
        if ip is None:
            ip = EDGE_FALLBACK.get(host)
            if ip is not None:
                self.log.append(f"resolve({host}): resolver API failed, used verified fallback {ip}")
        if ip is None:
            raise RuntimeError(f"cannot resolve official host {host!r}")
        self._ip_cache[host] = ip
        self.log.append(f"resolve({host}) -> {ip}")
        return ip

    def _resolve_via_networkcalc(self, host: str) -> str | None:
        url = f"https://networkcalc.com/api/dns/lookup/{host}?type=A"
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": self.local_proxy, "https": self.local_proxy})
            )
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=20) as resp:
                data = json.load(resp)
            records = (data.get("records") or {}).get("A") or []
            for rec in records:
                addr = rec.get("address")
                if addr:
                    return addr
        except Exception as exc:  # noqa: BLE001 - best-effort resolver
            self.log.append(f"resolve({host}): networkcalc error {exc!r}")
        return None

    # -- fetching ----------------------------------------------------------
    def get(self, url: str, _redirects: int = 5) -> tuple[int, dict[str, str], bytes]:
        """GET an official https URL via the no-SNI path; follows redirects."""
        u = urlparse(url)
        if u.scheme != "https":
            raise ValueError(f"only https official URLs are fetched, got {url!r}")
        host = u.hostname or ""
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        if self.insecure_no_sni:
            ip = self.resolve(host)
            conn = NoSniHTTPSConnection(ip, host=host, timeout=self.timeout)
        else:
            conn = http.client.HTTPSConnection(host, timeout=self.timeout)
        try:
            conn.request(
                "GET",
                path,
                headers={
                    "Host": host,
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,image/*,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,zh-HK;q=0.8",
                    "Connection": "close",
                },
            )
            resp = conn.getresponse()
            body = resp.read()
            status = resp.status
            headers = {k.lower(): v for k, v in resp.getheaders()}
        finally:
            conn.close()
        self.log.append(
            f"GET {url} -> {status} ({len(body)} bytes)"
            + (" [INSECURE no-SNI, certificate NOT verified]" if self.insecure_no_sni else "")
        )
        if status in (301, 302, 303, 307, 308) and _redirects > 0:
            loc = headers.get("location")
            if loc:
                return self.get(urljoin(url, loc), _redirects - 1)
        return status, headers, body
