// Server-side proxy between the browser and the iV API.
//
// Why this exists rather than the browser calling the API directly:
//
//  1. The API secret stays on the server. It was previously
//     NEXT_PUBLIC_API_SECRET, which Next.js inlines into the client
//     bundle — anyone who could load the page could read it.
//  2. Requests become same-origin, so CORS stops being involved at all.
//     The old setup needed the backend's allowed-origin list to name
//     every address the phone might use.
//  3. The browser only ever needs a relative URL. That is what removes
//     the hardcoded Tailscale IP: the page works identically on
//     localhost, on the LAN address, and on the tailnet address, because
//     it never names a host.
//
// IV_API_URL is a server-only variable (no NEXT_PUBLIC_ prefix) pointing
// at the API from the machine running Next.js — normally loopback, since
// both processes live on the same laptop.

import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Default kept in sync with core/configuration/ports.py (DEFAULT_API_PORT).
const API_URL = process.env.IV_API_URL || "http://127.0.0.1:8024";

// The secret has one source of truth: backend/.env. Requiring it to be
// copied into frontend/.env.local as well meant two files had to agree,
// and when they drifted the only symptom was a 403 with no hint about
// which side was wrong. Since both processes normally run from the same
// checkout on the same machine, the frontend can just read the backend's
// value. IV_API_SECRET still wins when set — that is the case where the
// frontend runs somewhere the backend's .env does not exist.
function backendEnvCandidates() {
  // Two independent ways of locating the repo, because each fails in a
  // situation the other survives. process.cwd() is frontend/ under a
  // normal `npm run dev`, but not if the server was started from
  // somewhere else; walking up from this module's own location is
  // immune to that, but the module may be bundled into .next/ — which
  // is itself inside frontend/, so walking up still arrives.
  const roots = [process.cwd()];
  try {
    let dir = dirname(fileURLToPath(import.meta.url));
    for (let i = 0; i < 8; i += 1) {
      roots.push(dir);
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  } catch {
    // import.meta.url unavailable in this runtime — cwd alone will do.
  }

  const candidates = [];
  for (const root of roots) {
    for (const relative of [["..", "backend", ".env"], ["backend", ".env"]]) {
      const path = join(root, ...relative);
      if (!candidates.includes(path)) candidates.push(path);
    }
  }
  return candidates;
}

function readBackendSecret() {
  const tried = [];
  for (const candidate of backendEnvCandidates()) {
    if (!existsSync(candidate)) {
      tried.push(`${candidate} (no such file)`);
      continue;
    }
    try {
      const matches = readFileSync(candidate, "utf8")
        .split("\n")
        .filter((l) => l.trimStart().replace(/^export\s+/, "").startsWith("API_ACCESS_SECRET="));
      if (matches.length === 0) {
        tried.push(`${candidate} (no API_ACCESS_SECRET line)`);
        continue;
      }
      // The LAST occurrence wins, empty or not — exactly how
      // python-dotenv resolves a duplicated key, so this reader and the
      // backend can never disagree about the same file. Taking the first
      // is what broke: `cp .env.example .env` leaves an empty
      // API_ACCESS_SECRET= near the top, and appending the real value
      // below it gave a working backend and a frontend that reported no
      // secret at all. "Whatever dotenv would say" is the only rule here
      // that stays correct as the file grows.
      const lastMatch = matches[matches.length - 1];
      const value = lastMatch.slice(lastMatch.indexOf("=") + 1).trim().replace(/^["']|["']$/g, "");
      if (!value) {
        tried.push(`${candidate} (API_ACCESS_SECRET is empty)`);
        continue;
      }
      if (matches.length > 1) {
        tried.push(`${candidate} (note: API_ACCESS_SECRET appears ${matches.length}x; used the last)`);
      }
      return { value, source: candidate, tried };
    } catch (err) {
      tried.push(`${candidate} (unreadable: ${err.code || err.message})`);
    }
  }
  return { value: "", source: null, tried };
}

let cachedSecret;
function apiSecret() {
  if (cachedSecret === undefined) {
    cachedSecret = process.env.IV_API_SECRET
      ? { value: process.env.IV_API_SECRET, source: "IV_API_SECRET" }
      : readBackendSecret();
  }
  return cachedSecret;
}

// Only these API paths are reachable through the proxy. An allowlist,
// not a blocklist: the API also exposes the approval-decision endpoints
// and the internal sleep-cycle/self-audit triggers, and none of those
// should be callable by anything that merely loaded the chat page.
//
// allowedQuery names exactly which query-string keys, if any, are copied
// from the incoming request onto the upstream call — everything else the
// browser appends is dropped rather than forwarded. Direct reads of the
// owner's own project/task/conversation data (the sidebar) sit at the
// same trust level as chat and conversation history: gated on the same
// secret, never touching the approval-decision or internal endpoints.
const ALLOWED = {
  "chat": { method: "POST", path: "/api/chat" },
  "health": { method: "GET", path: "/health" },
  "conversations": { method: "GET", path: "/api/conversations" },
  "projects": { method: "GET", path: "/api/projects", allowedQuery: ["status"] },
  "tasks": { method: "GET", path: "/api/tasks", allowedQuery: ["project_id"] },
};

function resolve(segments, method) {
  if (segments[0] === "conversations" && segments.length === 2) {
    return { method: "GET", path: `/api/conversations/${encodeURIComponent(segments[1])}` };
  }
  if (segments[0] === "projects" && segments.length === 2) {
    return { method: "GET", path: `/api/projects/${encodeURIComponent(segments[1])}` };
  }
  if (segments[0] === "tasks" && segments.length === 3 && segments[2] === "status") {
    return { method: "POST", path: `/api/tasks/${encodeURIComponent(segments[1])}/status` };
  }
  // chess/games needs both GET (list) and POST (create) at the same
  // /chess/games shape -- the only allowlist entry so far where the
  // method, not just the path segments, decides where it goes. `method`
  // is passed in for exactly this case; every branch above stays
  // unambiguous from segments alone and ignores it.
  if (segments[0] === "chess" && segments[1] === "games") {
    if (segments.length === 2) {
      if (method === "GET") return { method: "GET", path: "/api/chess/games", allowedQuery: ["status"] };
      if (method === "POST") return { method: "POST", path: "/api/chess/games" };
      return null;
    }
    if (segments.length === 3) {
      return { method: "GET", path: `/api/chess/games/${encodeURIComponent(segments[2])}` };
    }
    if (segments.length === 4 && segments[3] === "move") {
      return { method: "POST", path: `/api/chess/games/${encodeURIComponent(segments[2])}/move` };
    }
    return null;
  }
  if (segments.length === 1 && Object.prototype.hasOwnProperty.call(ALLOWED, segments[0])) {
    return ALLOWED[segments[0]];
  }
  return null;
}

async function forward(request, segments) {
  const route = resolve(segments, request.method);
  if (!route || route.method !== request.method) {
    return Response.json({ detail: "not found" }, { status: 404 });
  }

  const secret = apiSecret();
  const init = {
    method: route.method,
    headers: { "X-API-Secret": secret.value },
    cache: "no-store",
  };
  if (route.method === "POST") {
    init.headers["Content-Type"] = "application/json";
    init.body = await request.text();
  }

  let upstreamUrl = `${API_URL}${route.path}`;
  if (route.allowedQuery) {
    const incoming = new URL(request.url);
    const forwarded = new URLSearchParams();
    for (const key of route.allowedQuery) {
      const value = incoming.searchParams.get(key);
      if (value !== null) forwarded.set(key, value);
    }
    const qs = forwarded.toString();
    if (qs) upstreamUrl += `?${qs}`;
  }

  let upstream;
  try {
    upstream = await fetch(upstreamUrl, init);
  } catch (err) {
    // The API being down is the single most likely failure here, and the
    // browser cannot tell that apart from the proxy being down. Say which.
    return Response.json(
      { detail: `iV API unreachable at ${API_URL}: ${err.message}` },
      { status: 502 },
    );
  }

  // The proxy is what attached the secret, and it is the only credential
  // these endpoints check — so a 403 here is a configuration mismatch,
  // never something the person using the page did wrong. Say that,
  // instead of passing through "Invalid or missing API secret" and
  // leaving them to guess which of two files is out of step.
  if (upstream.status === 403) {
    if (secret.value) {
      return Response.json(
        {
          detail:
            `The secret the frontend sent (from ${secret.source}) does not match ` +
            "API_ACCESS_SECRET in the backend's .env. Fix it and restart the frontend — " +
            "it reads the value once at startup.",
        },
        { status: 403 },
      );
    }
    // No secret found at all. Report exactly where it looked, so this is
    // diagnosable from the page instead of by guessing at cwd.
    return Response.json(
      {
        detail:
          "The frontend could not find an API secret. Set API_ACCESS_SECRET in " +
          "backend/.env, or IV_API_SECRET in frontend/.env.local, then restart the " +
          "frontend (env files are read once at startup).",
        looked_in: secret.tried || [],
      },
      { status: 403 },
    );
  }

  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("content-type") || "application/json" },
  });
}

export async function GET(request, { params }) {
  const { path } = await params;
  return forward(request, path || []);
}

export async function POST(request, { params }) {
  const { path } = await params;
  return forward(request, path || []);
}
