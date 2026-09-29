from types import SimpleNamespace

import discord
import pytest

from jarvis.brain import Reply
from jarvis.config import Config
from jarvis.discord_bot import DiscordBot, is_audio, split_message


class FakeBrain:
    def __init__(self):
        self.asked = []

    async def ask(self, text, conv, **kw):
        self.asked.append((text, conv, kw.get("channel")))
        return Reply("Antwort " + "x" * 2500)

    def reset(self, conv):
        self.asked.append(("RESET", conv, None))


class FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeChannel:
    def __init__(self, cid, dm=False):
        self.id = cid
        self.sent = []
        self._dm = dm

    async def send(self, content=None, **kw):
        self.sent.append(content)

    def typing(self):
        return FakeTyping()


def msg(author_id, content, channel, mentions=(), attachments=()):
    return SimpleNamespace(author=SimpleNamespace(id=author_id, bot=False), content=content, channel=channel,
                           mentions=list(mentions), attachments=list(attachments))


@pytest.fixture
def bot(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path, discord_token="x", discord_allowed=["42"], discord_channels=["7"])
    b = DiscordBot(cfg, FakeBrain())
    me = SimpleNamespace(id=999)
    monkeypatch.setattr(type(b.client), "user", property(lambda self: me))
    # DMChannel-Erkennung für die Fakes
    monkeypatch.setattr(discord, "DMChannel", FakeChannelDM)
    return b, me


class FakeChannelDM(FakeChannel):
    pass


async def test_rules(bot):
    b, me = bot
    assert b.should_answer(msg(42, "hi", FakeChannelDM(1)))
    assert b.should_answer(msg(42, "hi", FakeChannel(7)))           # freigegebener Kanal
    assert not b.should_answer(msg(42, "hi", FakeChannel(8)))       # fremder Kanal ohne Erwähnung
    assert b.should_answer(msg(42, "<@999> hi", FakeChannel(8), mentions=[me]))
    assert not b.should_answer(msg(13, "hi", FakeChannelDM(1)))     # nicht erlaubt


async def test_handle_text_and_split(bot):
    b, me = bot
    ch = FakeChannel(8)
    await b.on_message(msg(42, "<@999> Was steht heute an?", ch, mentions=[me]))
    assert b.brain.asked == [("Was steht heute an?", "discord:8", "discord-text")]
    assert len(ch.sent) == 2 and all(len(p) <= 2000 for p in ch.sent)


async def test_reset_and_stranger(bot):
    b, _ = bot
    dm = FakeChannelDM(5)
    await b.on_message(msg(42, "!neu", dm))
    assert b.brain.asked == [("RESET", "discord:5", None)]
    await b.on_message(msg(13, "hallo", dm))
    assert "Deine Discord-ID ist 13" in dm.sent[-1]


def test_helpers():
    assert split_message("a" * 4000) == ["a" * 1990, "a" * 1990, "a" * 20]
    assert is_audio(SimpleNamespace(content_type="audio/ogg", filename="voice-message.ogg"))
    assert not is_audio(SimpleNamespace(content_type="image/png", filename="x.png"))
