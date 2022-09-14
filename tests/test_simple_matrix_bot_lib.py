from matrix_bot_lib import __version__, MatrixBot

def test_version():
    assert __version__ == '0.1.0'

async def test_bot_auth():
    USER = "@dtuhax-bot:xn--sb-lka.org"
    bot = MatrixBot(USER)
    PASS = "..."
    await bot.login(PASS)

#test_bot_auth()
#await test_bot_auth_token()
