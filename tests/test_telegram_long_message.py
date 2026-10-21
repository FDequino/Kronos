"""Test de portfolio/telegram_bot.py::_send_long_message -- bug real en
produccion (VPS, 2026-10-20): telegram.error.BadRequest: Message is too long
tumbaba el resumen diario completo cuando el bloque de oportunidades crecia
mas alla de los 4096 caracteres que permite Telegram por mensaje. No pega a
la red real (FakeBot de test_telegram_bot.py)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio import telegram_bot


class FakeBot:
    def __init__(self):
        self.sent = []  # [(chat_id, text, kwargs), ...]

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))


def run(coro):
    return asyncio.run(coro)


def test_mensaje_corto_se_manda_entero_sin_partir():
    bot = FakeBot()
    run(telegram_bot._send_long_message(bot, "111", "Resumen corto", parse_mode="Markdown", reply_markup="kb"))

    assert len(bot.sent) == 1
    assert bot.sent[0] == ("111", "Resumen corto", {"parse_mode": "Markdown", "reply_markup": "kb"})


def test_mensaje_largo_se_parte_en_varios_sin_perder_contenido():
    lineas = [f"linea {i}: " + "x" * 80 for i in range(100)]  # bien por encima de 4096 chars
    texto = "\n".join(lineas)
    bot = FakeBot()

    run(telegram_bot._send_long_message(bot, "111", texto, parse_mode="Markdown", reply_markup="kb"))

    assert len(bot.sent) > 1
    for _, chunk, _ in bot.sent:
        assert len(chunk) <= telegram_bot.TELEGRAM_MAX_MESSAGE_CHARS
    # Ninguna linea se pierde ni se corta a la mitad: reconstruyendo todos los
    # chunks se recupera el texto original completo.
    reconstruido = "\n".join(chunk for _, chunk, _ in bot.sent)
    assert reconstruido == texto


def test_solo_el_ultimo_chunk_lleva_el_teclado():
    lineas = [f"linea {i}: " + "x" * 80 for i in range(100)]
    texto = "\n".join(lineas)
    bot = FakeBot()

    run(telegram_bot._send_long_message(bot, "111", texto, parse_mode="Markdown", reply_markup="kb"))

    assert len(bot.sent) > 1
    for _, _, kwargs in bot.sent[:-1]:
        assert "reply_markup" not in kwargs
    assert bot.sent[-1][2]["reply_markup"] == "kb"
