"""Shared response models.

Every schema uses camelCase aliases: the Flutter models are @JsonSerializable
with camelCase Dart field names, so matching them here means the client model
is a mechanical conversion with no mapping layer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class MessageResponse(CamelModel):
    message: str
