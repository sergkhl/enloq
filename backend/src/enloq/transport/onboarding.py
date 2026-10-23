"""Identity-only startup and a bounded login send queue, without history."""

from telethon._updates import SessionState
from telethon.tl import functions


class OnboardingRefused(RuntimeError):
    pass


class IdentityOnlyStartup:
    async def _on_login(self, user):
        # Telethon 1.44 normally requests GetDifference here, even when updates
        # are disabled. Setup needs only self identity and the current watermark.
        self._mb_entity_cache.set_self_user(user.id, user.bot, user.access_hash)
        self._authorized = True
        state = await self(functions.updates.GetStateRequest())
        self._message_box.load(
            SessionState(
                0,
                0,
                False,
                state.pts,
                state.qts,
                int(state.date.timestamp()),
                state.seq,
                0,
            ),
            [],
        )
        return user

    async def _reconnect_owner(self):
        # One setup attempt never reconnects or retries an uncertain approval.
        await self.disconnected


class OnboardingQueue:
    """Guard actual enqueues/requeues; adapted from the existing source owner."""

    def __init__(self, queue, admission):
        self.queue, self.admission = queue, admission

    def __getattr__(self, name):
        return getattr(self.queue, name)

    def append(self, state):
        if state.future.done():
            return
        try:
            self.admission(state.request)
        except Exception as error:
            state.future.set_exception(error)
            return
        self.queue.append(state)

    def extend(self, states):
        for state in states:
            self.append(state)
