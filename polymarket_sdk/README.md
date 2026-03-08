# Polymarket SDK

This folder contains reusable plumbing that several Polymarket bots may share.
It bundles:

- configuration handling (`config.py`)
- Polymarket CLOB client plus helper functions (`api.py`)
- Telegram alert utilities (`telegram.py`)
- example Docker/NordVPN setup (see `docker/` subdirectory)

To consume the SDK from another bot project simply add the parent directory
to `PYTHONPATH` or install the package, then import::

    from polymarket_sdk import config, api, telegram

and use the helpers there instead of re-implementing.

The supplied Docker compose scripts rely on `NORDVPN_*` environment
variables and set up a `bot` service that shares a network namespace with
an OpenVPN sidecar container. You can copy the `docker/` directory into a
new project and edit the `bot:` service to point at your own application.
