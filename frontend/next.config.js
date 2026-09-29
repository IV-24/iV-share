/** @type {import('next').NextConfig} */

// Next's dev server rejects cross-origin requests from hosts it wasn't
// told about, which is exactly what a phone hitting the laptop's
// Tailscale address is. The previous version hardcoded one person's
// Tailscale IP here; this reads it from the environment instead, so the
// checked-in file describes no particular network.
//
// IV_ALLOWED_DEV_ORIGINS is a comma-separated list of hosts/IPs. It only
// affects `next dev` — a production build (`next build && next start`)
// does not use it.
const allowedDevOrigins = (process.env.IV_ALLOWED_DEV_ORIGINS || "")
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean);

const nextConfig = {
  ...(allowedDevOrigins.length > 0 ? { allowedDevOrigins } : {}),
};

module.exports = nextConfig;
