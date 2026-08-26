"""A local proxy that adds the password, so the browser never needs to know it.

    with ProxyRelay("http://user:pass@1.2.3.4:8080") as local:
        launch_browser(proxy=local)      # -> "http://127.0.0.1:53412"

THE PROBLEM THIS SOLVES
-----------------------
Chromium's `--proxy-server` flag **cannot carry credentials**. That is a
Chromium limitation, not a library one, and it is why every antidetect product
ships its own workaround — AdsPower injects a helper extension, Playwright
takes username/password as separate fields and answers the challenge itself.

DrissionPage does neither. Its `set_proxy` only *warns*:

    if search(r'.*?:.*?@.*?\\..*', proxy):
        print(UNSUPPORTED_USER_PROXY)
    return self.set_argument('--proxy-server', proxy)

…and then passes the string through anyway. Chromium drops the credentials, the
upstream answers 407, and the browser silently exits from the HOST IP instead.
Measured 2026-08-20: it reported success while doing exactly that.

THE FIX
-------
Put an unauthenticated proxy in front of the authenticated one. The browser
talks to `127.0.0.1` with no password; this relay adds
`Proxy-Authorization` on the way out.

    browser --(no auth)--> 127.0.0.1:PORT --(Basic auth)--> Webshare

WHY THIS AND NOT IP WHITELISTING
--------------------------------
Webshare does support IP authorisation (`/api/v2/proxy/ipauthorization/`,
currently empty), which would also work and needs no code. It was not chosen:

  * it binds the proxies to ONE public IP, so a laptop on a changing address
    breaks, and a cloud VM must keep a static IP forever;
  * whitelisting is account-wide — anything sharing that IP can spend the
    proxies;
  * and the failure is silent in the worst way. If the runner's IP changes, the
    proxy stops authenticating and Chromium falls back to a direct connection,
    which is the precise mismatch this whole layer exists to prevent.

The relay has none of that: it works from any machine, changes nothing about
the account, and if it dies the browser simply cannot reach the internet — a
loud failure instead of a quiet one.

⚠️ BINDS TO 127.0.0.1 ONLY. An open proxy on a public interface is found by
scanners within hours and becomes someone else's bandwidth.
"""
import base64
import select
import socket
import threading
import urllib.parse

BUFFER = 65536
# The browser is local, the upstream is not. Generous enough for a slow proxy,
# short enough that a dead upstream does not pin a thread forever.
UPSTREAM_TIMEOUT = 30


class ProxyRelay:
    """An unauthenticated local proxy that forwards to an authenticated one."""

    def __init__(self, upstream, host="127.0.0.1", port=0):
        parsed = urllib.parse.urlparse(upstream)
        if not parsed.hostname or not parsed.port:
            raise ValueError(f"upstream proxy needs host and port: {upstream!r}")
        self.upstream_host = parsed.hostname
        self.upstream_port = parsed.port
        self.auth = None
        if parsed.username:
            raw = (f"{urllib.parse.unquote(parsed.username)}:"
                   f"{urllib.parse.unquote(parsed.password or '')}")
            self.auth = base64.b64encode(raw.encode()).decode()

        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((host, port))
        self._server.listen(64)
        self.host, self.port = self._server.getsockname()
        self._stop = threading.Event()
        self._thread = None
        self.connections = 0
        self.failures = 0

    @property
    def url(self):
        return f"http://{self.host}:{self.port}"

    def start(self):
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        try:
            self._server.close()
        except OSError:
            pass

    def __enter__(self):
        return self.start().url

    def __exit__(self, *exc):
        self.stop()
        return False

    # ----------------------------------------------------------------- guts

    def _serve(self):
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except OSError:
                return                       # closed by stop()
            self.connections += 1
            threading.Thread(target=self._handle, args=(client,),
                             daemon=True).start()

    def _handle(self, client):
        upstream = None
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(BUFFER)
                if not chunk:
                    return
                head += chunk

            upstream = socket.create_connection(
                (self.upstream_host, self.upstream_port), UPSTREAM_TIMEOUT)

            first, _, rest = head.partition(b"\r\n")
            if first.upper().startswith(b"CONNECT"):
                # HTTPS. Re-issue the CONNECT ourselves with the credentials
                # attached, then get out of the way and move bytes — the TLS
                # session inside is end-to-end and never touches this process.
                request = first + b"\r\n"
                if self.auth:
                    request += b"Proxy-Authorization: Basic " + \
                               self.auth.encode() + b"\r\n"
                request += b"\r\n"
                upstream.sendall(request)

                reply = b""
                while b"\r\n\r\n" not in reply:
                    chunk = upstream.recv(BUFFER)
                    if not chunk:
                        break
                    reply += chunk
                # Pass the upstream's verdict straight through. A 407 must
                # reach the browser as a 407 — swallowing it and connecting
                # directly is the exact silent fallback this file prevents.
                client.sendall(reply)
                if b" 200 " not in reply.split(b"\r\n")[0]:
                    self.failures += 1
                    return
            else:
                # Plain HTTP: forward the request, credentials added.
                if self.auth:
                    head = (first + b"\r\nProxy-Authorization: Basic "
                            + self.auth.encode() + b"\r\n" + rest)
                upstream.sendall(head)

            self._pump(client, upstream)
        except Exception:
            self.failures += 1
        finally:
            for sock in (client, upstream):
                try:
                    if sock:
                        sock.close()
                except OSError:
                    pass

    @staticmethod
    def _pump(a, b):
        """Move bytes both ways until either side hangs up."""
        socks = [a, b]
        while True:
            readable, _, broken = select.select(socks, [], socks, 60)
            if broken or not readable:
                return
            for source in readable:
                target = b if source is a else a
                data = source.recv(BUFFER)
                if not data:
                    return
                target.sendall(data)
