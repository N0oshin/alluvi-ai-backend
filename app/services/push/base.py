"""Push contract: a message, and a sender that delivers it to device tokens.

push is best-effort, so a failed delivery is logged, not raised. What the caller *does* need back is
which tokens turned out to be dead (app uninstalled, token rotated), so it
can delete them — hence the return value on `send`.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass(slots=True)
class PushMessage:
    title: str
    body: str
    # optional
    data: dict[str, str] = field(default_factory=dict)


class PushSender(abc.ABC):
    name: str

    @abc.abstractmethod
    async def send(self, tokens: list[str], message: PushMessage) -> set[str]:
        """Deliver `message` to every token, best-effort.

        Returns the subset of `tokens` the provider reported as dead
        (unregistered / invalid) so the caller can prune them.
        """


__all__ = ["PushMessage", "PushSender"]
