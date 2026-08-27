"""The per-profile browser identity, as one injectable script.

Chromium leaks the HOST's hardware to every profile running on it: the same
WebGL renderer, the same canvas rendering, the same fonts, the same core count.
Twenty profiles on one machine are twenty accounts a fingerprinter can tie
together in a single query, and their separate exit IPs do not help at all.

This builds a script that gives one profile its own answers. It is injected via
CDP `Page.addScriptToEvaluateOnNewDocument` before any page script runs.

⚠️ THIS ONLY WORKS UNDER DrissionPage. patchright SILENTLY IGNORES init
scripts — measured 2026-08-20: the script never runs and
`navigator.hardwareConcurrency` reports the host's real 12. That is patchright
working as designed (injected scripts are themselves detectable), and it is
exactly why the two tools trade places here: patchright hides automation
better, DrissionPage is the only one that can make profiles DIFFERENT.

EVERYTHING IS DERIVED FROM THE PROFILE ID
-----------------------------------------
Not random. A profile whose fingerprint changes every launch looks like a brand
new device presenting the same login cookies — a stronger signal of automation
than never changing at all. A hash of the id gives a value that is stable
forever, differs per profile, and needs nothing stored to reproduce.

THE VALUES ARE ORDINARY ON PURPOSE
----------------------------------
Every string here is a real, common GPU and a real, common screen size. A
unique fingerprint is not the goal — blending into a large crowd is. A profile
reporting a 1337x911 viewport and a GPU nobody owns is more identifiable than
one reporting the same 1920x1080 as millions of others.

HONEST LIMITS
-------------
Patching JS is an arms race and this is not undetectable — it is not TRIVIALLY
detectable. The obvious `Function.prototype.toString` leak is closed (verified:
all patched functions report `[native code]`), but second-order checks exist
and this does not claim to survive a dedicated fingerprinting vendor. It is
proportionate to the threat here: Pinterest, on a session that logs in rarely
and loads one page per keepalive cycle.
"""
import hashlib

# Real GPUs, common in consumer laptops and desktops. Paired vendor+renderer,
# because a mismatched pair (Intel vendor, NVIDIA renderer) is itself a tell.
GPUS = [
    ("Google Inc. (Intel)",
     "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (0x000046A8) Direct3D11 "
     "vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)",
     "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)",
     "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)",
     "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)",
     "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
]
# Common desktop resolutions. Ordered by real-world share, roughly.
VIEWPORTS = [(1920, 1080), (1536, 864), (1366, 768), (1440, 900),
             (1600, 900), (1280, 720), (2560, 1440)]
TIMEZONES = ["America/New_York", "America/Chicago", "America/Denver",
             "America/Los_Angeles", "America/Phoenix"]
CORES = [4, 6, 8, 12, 16]
MEMORY = [4, 8, 8, 16, 16, 32]        # 8 and 16 weighted: they are the common ones


def _usable(value):
    """A measured signal we can trust. Null, empty and the probe's own "ERR:"
    marker all mean "not measured" — and a missing measurement must fall back
    to the synthetic shape, never write an empty value over a working one."""
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and not text.startswith("ERR:")


def profile_shape(profile_id, measured=None):
    """The machine this profile claims to be.

    Synthetic by default, derived from a hash of the profile id. When
    `measured` is supplied it is the fingerprint CAPTURED FROM ADSPOWER at
    login time, and it wins for every signal it actually carries.

    WHY THIS MATTERS. The account signs in through AdsPower wearing AdsPower's
    fingerprint. Without this, keepalive then re-presents those same cookies
    every five minutes wearing a machine invented from sha1(profile_id) — a
    different GPU, screen, core count and memory than the device that logged
    in. The UA and exit IP travelled with the session; the hardware did not.
    That is a device change on a live session, which is precisely the signal
    the session layer exists to avoid.

    CANVAS IS DELIBERATELY NOT CARRIED OVER. AdsPower perturbs it with a seeded
    noise function; the probe can read the resulting hash but cannot reproduce
    it without that function, so copying the value would be cargo-culting a
    number we cannot regenerate. The synthetic per-profile seed stays — it is
    stable per profile, which is what matters, even though it does not match
    what AdsPower produced.
    """
    digest = hashlib.sha1(f"fp::{profile_id}".encode()).digest()
    vendor, renderer = GPUS[digest[0] % len(GPUS)]
    width, height = VIEWPORTS[digest[1] % len(VIEWPORTS)]
    shape = {
        "webgl_vendor": vendor,
        "webgl_renderer": renderer,
        "width": width,
        "height": height,
        "timezone": TIMEZONES[digest[2] % len(TIMEZONES)],
        "cores": CORES[digest[3] % len(CORES)],
        "memory": MEMORY[digest[4] % len(MEMORY)],
        # Drives the canvas perturbation. Small and fixed per profile: enough
        # to change the hash, too small to be visible or to break rendering.
        "canvas_seed": digest[5],
    }
    if not measured:
        return shape

    if _usable(measured.get("webglVendor")):
        shape["webgl_vendor"] = str(measured["webglVendor"])
    if _usable(measured.get("webglRenderer")):
        shape["webgl_renderer"] = str(measured["webglRenderer"])
    if _usable(measured.get("timezone")):
        shape["timezone"] = str(measured["timezone"])
    for key, field in (("hardwareConcurrency", "cores"),
                       ("deviceMemory", "memory")):
        if _usable(measured.get(key)):
            try:
                shape[field] = int(measured[key])
            except (TypeError, ValueError):
                pass
    # `screen` arrives as "WxHxdepthxdpr" from the probe. Take only the first
    # two, and only if BOTH parse -- a half-applied viewport is worse than the
    # synthetic one, because it pairs a real width with an invented height.
    if _usable(measured.get("screen")):
        parts = str(measured["screen"]).split("x")
        if len(parts) >= 2:
            try:
                shape["width"], shape["height"] = int(parts[0]), int(parts[1])
            except (TypeError, ValueError):
                pass
    return shape


def platform_for(user_agent):
    """`navigator.platform` that AGREES with the user agent.

    Derived, never varied per profile. It is not a diversity knob — it is a
    consistency one, and the two are opposites here: the UA already declares an
    OS, so platform has exactly one correct value and any other is a
    contradiction a single line of JavaScript can catch.

    Measured on Linux 2026-08-25, before this existed: profiles served a
    Windows UA and Windows-only ANGLE/Direct3D11 WebGL strings while
    `navigator.platform` answered "Linux x86_64". Invisible on the Windows
    laptop that built them; guaranteed on the GCP VM they are headed for.
    """
    ua = user_agent or ""
    if "Windows" in ua:
        return "Win32"
    if "Macintosh" in ua or "Mac OS X" in ua:
        return "MacIntel"
    if "Android" in ua:
        return "Linux armv8l"
    if "iPhone" in ua or "iPad" in ua:
        return "iPhone"
    # Unknown UA: leave the real value alone rather than assert a wrong one.
    return None


def build_script(profile_id, user_agent=None, measured=None):
    """The init script for one profile.

    `measured` is the fingerprint captured from AdsPower at login, if the vault
    has one. Absent, the shape is synthetic -- see profile_shape().
    """
    shape = profile_shape(profile_id, measured)
    platform = platform_for(user_agent)
    platform_js = (
        f"define(navigator, 'platform', '{platform}');" if platform else "")
    return _TEMPLATE.format(
        vendor=shape["webgl_vendor"].replace("'", ""),
        renderer=shape["webgl_renderer"].replace("'", ""),
        cores=shape["cores"],
        memory=shape["memory"],
        width=shape["width"],
        height=shape["height"],
        seed=shape["canvas_seed"],
        platform_js=platform_js,
    )


# `swap()` records each original's source in a WeakMap and installs a
# replacement that keeps the original's NAME, then makes
# Function.prototype.toString report the original source for anything patched —
# and reports itself as native too, or the hider becomes the tell.
_TEMPLATE = """
(() => {{
  const nativeSrc = new WeakMap();
  const realToString = Function.prototype.toString;

  function toString() {{
    if (nativeSrc.has(this)) return nativeSrc.get(this);
    return realToString.call(this);
  }}
  nativeSrc.set(toString, realToString.call(realToString));

  function swap(obj, name, make) {{
    const original = obj[name];
    if (!original) return;
    const impl = make(original);
    nativeSrc.set(impl, realToString.call(original));
    try {{ Object.defineProperty(impl, 'name', {{value: name}}); }} catch (e) {{}}
    obj[name] = impl;
  }}

  function define(obj, name, value) {{
    try {{
      Object.defineProperty(obj, name, {{get: () => value, configurable: true}});
      const getter = Object.getOwnPropertyDescriptor(obj, name).get;
      nativeSrc.set(getter, 'function get ' + name + '() {{ [native code] }}');
    }} catch (e) {{}}
  }}

  define(navigator, 'hardwareConcurrency', {cores});
  define(navigator, 'deviceMemory', {memory});
  {platform_js}
  define(screen, 'width', {width});
  define(screen, 'height', {height});
  define(screen, 'availWidth', {width});
  define(screen, 'availHeight', {height} - 40);

  swap(WebGLRenderingContext.prototype, 'getParameter', (orig) =>
    function getParameter(p) {{
      if (p === 37445) return '{vendor}';
      if (p === 37446) return '{renderer}';
      return orig.apply(this, arguments);
    }});
  if (window.WebGL2RenderingContext) {{
    swap(WebGL2RenderingContext.prototype, 'getParameter', (orig) =>
      function getParameter(p) {{
        if (p === 37445) return '{vendor}';
        if (p === 37446) return '{renderer}';
        return orig.apply(this, arguments);
      }});
  }}

  swap(HTMLCanvasElement.prototype, 'toDataURL', (orig) =>
    function toDataURL(...a) {{
      const ctx = this.getContext('2d');
      if (ctx) {{
        ctx.fillStyle = 'rgba(' + ({seed} % 255) + ',0,0,0.01)';
        ctx.fillRect({seed} % 8, {seed} % 4, 1, 1);
      }}
      return orig.apply(this, a);
    }});

  Function.prototype.toString = toString;
}})();
"""
