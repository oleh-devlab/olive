from dataclasses import dataclass

import disnake


@dataclass
class UserMessageMetadata:
    timestamp_ms: int
    author_id: int
    author_name: str
    author_display_name: str
    message_id: int

    @classmethod
    def from_disnake_message(cls, message: disnake.Message) -> "UserMessageMetadata":
        return cls(
            timestamp_ms=int(message.created_at.timestamp() * 1000),
            author_id=message.author.id,
            author_name=message.author.name,
            author_display_name=getattr(message.author, "display_name", message.author.name),
            message_id=message.id,
        )
