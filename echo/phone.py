"""Run a password-protected ECHO session for a phone on the same Wi-Fi."""

import argparse
import ipaddress
import os
from pathlib import Path
import secrets

import uvicorn

from .model_setup import DATA_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", type=ipaddress.IPv4Address, help="PC's Wi-Fi IPv4 address")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--ssl-certfile", type=Path, help="PEM certificate trusted by the phone for the Wi-Fi IP")
    parser.add_argument("--ssl-keyfile", type=Path, help="PEM private key for the certificate")
    args = parser.parse_args()
    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        parser.error("Provide both --ssl-certfile and --ssl-keyfile for HTTPS.")
    if args.address.is_unspecified or args.address.is_multicast:
        parser.error("Enter the PC's Wi-Fi address.")
    password_file = DATA_DIR / "phone-password.txt"
    password = password_file.read_text(encoding="utf-8-sig").strip() if password_file.is_file() else secrets.token_hex(6)
    if not password or not password.isascii():
        parser.error("Use a non-empty ASCII password in phone-password.txt.")
    os.environ["ECHO_PHONE_ADDRESS"] = str(args.address)
    os.environ["ECHO_PHONE_PASSWORD"] = password
    scheme = "https" if args.ssl_certfile else "http"
    print(f"Phone URL: {scheme}://{args.address}:{args.port}\nUsername: echo\nPassword: {password}", flush=True)
    print("Use trusted Wi-Fi. Press Ctrl+C to end this session.", flush=True)
    uvicorn.run("echo.server:app", host=str(args.address), port=args.port,
                proxy_headers=False, access_log=False,
                ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile)


if __name__ == "__main__":
    main()
