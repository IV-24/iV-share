"""Conversation/message history — what backend/app/db.py used to do
directly against Supabase. Single-user for now (no profile_id/user_id):
multi-user scoping is deferred until there's a working single-user model
to extend, per the migration decision log in docs/MIGRATION_AUDIT.md."""

from core.conversations.base import Conversation, ConversationStore, Message

__all__ = ["Conversation", "Message", "ConversationStore"]
