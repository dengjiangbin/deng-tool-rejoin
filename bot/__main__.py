"""Entry-point shim — allows ``python -m bot`` as an alias for ``python -m bot.main``."""

from bot.main import main

if __name__ == "__main__":
    main()
