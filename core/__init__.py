"""iV Core: the portable agent runtime.

Nothing under core/ may import Supabase, GitHub, Vercel, Tailscale, a
specific LLM SDK, or anything OS-specific. Infrastructure lives in
adapters/ and interfaces/, both of which depend on core — never the
reverse. See docs/CORE_ARCHITECTURE.md.
"""
