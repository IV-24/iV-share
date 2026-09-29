// A phone reaching this app over Tailscale (cellular, a DERP relay, a
// laptop that's asleep) can hang a plain fetch() indefinitely -- no
// error, no timeout, just a spinner that never resolves. Desktop rarely
// hits this (usually the same LAN, a direct connection), so the failure
// mode is real but mobile-skewed. Every fetch in this app should go
// through here instead of calling fetch() directly, so a slow/dead
// connection surfaces as a clear, bounded error instead of an infinite wait.
export async function fetchWithTimeout(url, options = {}, timeoutMs = 20000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (err) {
    if (err.name === "AbortError") {
      const timeoutErr = new Error(
        "That request took too long and was cancelled -- the connection may be slow or the server unreachable.",
      );
      timeoutErr.name = "TimeoutError";
      throw timeoutErr;
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}
