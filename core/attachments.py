"""Durable attachment storage.

Discord attachment URLs on ordinary messages can expire/rotate, so any
feature that needs to remember an image long-term (a proxy's avatar, a
tag's embedded image, a leave-message banner, a quote snapshot's photos)
re-uploads it to a dedicated storage channel first and keeps *that*
message's attachment URL instead, which Discord treats as permanent.

Every one of those features used to independently reimplement this exact
five-line dance; centralized here since it's pure infrastructure with no
feature attached to it.
"""
from __future__ import annotations
from io import BytesIO

from discord import Attachment, Client, File

from config import STORAGE_CHANNEL_ID


async def persist_attachment(client: Client, attachment: Attachment) -> str:
    """Re-uploads `attachment` to the storage channel and returns a
    permanent CDN URL for it."""
    data = await attachment.read()
    channel = client.get_channel(STORAGE_CHANNEL_ID) or await client.fetch_channel(STORAGE_CHANNEL_ID)
    msg = await channel.send(file=File(BytesIO(data), filename=attachment.filename))
    return msg.attachments[0].url
