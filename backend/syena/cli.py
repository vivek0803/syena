"""Admin CLI. There is intentionally no command that changes trading mode.

    syena create-admin            # one-time: create the single user + TOTP
    syena reset-totp              # rotate TOTP secret (requires current password)
    syena gen-key                 # print a new SYENA_ENCRYPTION_KEY
"""
import getpass

import typer
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from syena.db import SessionLocal
from syena.models import User
from syena.security import totp
from syena.security.passwords import MIN_PASSWORD_LENGTH, hash_password, verify_password

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _show_totp(secret: str, username: str) -> None:
    uri = totp.provisioning_uri(secret, username)
    typer.echo("\nAdd this to your authenticator app (Google Authenticator, Aegis, 1Password...):")
    typer.echo(f"  Secret key : {secret}")
    typer.echo(f"  otpauth URI: {uri}")
    try:
        import qrcode  # optional

        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.print_ascii(invert=True)
    except ImportError:
        typer.echo("  (pip install qrcode to show a scannable QR code here)")
    typer.echo("\nThis secret is shown ONCE. It is stored encrypted and never logged.")


def _confirm_code(secret: str) -> None:
    import pyotp

    for _ in range(3):
        code = typer.prompt("Enter the 6-digit code from your app to confirm")
        if pyotp.TOTP(secret).verify(code, valid_window=1):
            return
        typer.echo("Code did not match, try again.")
    raise typer.Exit("TOTP confirmation failed; nothing was saved.")


@app.command("create-admin")
def create_admin(username: str = typer.Option(..., prompt=True)):
    """Create the single Syena user. Refuses if a user already exists."""
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(User)):
            raise typer.Exit("A user already exists. Syena is single-user.")
        pw = getpass.getpass(f"Password (min {MIN_PASSWORD_LENGTH} chars): ")
        if pw != getpass.getpass("Repeat password: "):
            raise typer.Exit("Passwords do not match.")
        pw_hash = hash_password(pw)
        secret = totp.new_secret()
        _show_totp(secret, username)
        _confirm_code(secret)
        db.add(User(username=username, password_hash=pw_hash, totp_secret_enc=totp.encrypt_secret(secret)))
        db.commit()
        typer.echo(f"Created user '{username}'. 2FA is mandatory on every login.")


@app.command("reset-totp")
def reset_totp(username: str = typer.Option(..., prompt=True)):
    """Rotate the TOTP secret (e.g. new phone). Requires the current password and server shell access."""
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if user is None or not verify_password(user.password_hash, getpass.getpass("Current password: ")):
            raise typer.Exit("Invalid credentials.")
        secret = totp.new_secret()
        _show_totp(secret, username)
        _confirm_code(secret)
        user.totp_secret_enc = totp.encrypt_secret(secret)
        user.totp_last_step = 0
        db.commit()
        typer.echo("TOTP secret rotated.")


@app.command("seed-demo")
def seed_demo():
    """Fill a DEV database with clearly synthetic PAPER data to explore every page."""
    from syena.config import get_settings
    from syena.seed import seed

    if get_settings().env != "dev":
        raise typer.Exit("seed-demo only runs with SYENA_ENV=dev")
    with SessionLocal() as db:
        typer.echo(seed(db))


@app.command("telegram-webhook")
def telegram_webhook(url: str = typer.Argument(..., help="https://<domain>/api/telegram/webhook")):
    """Register the Telegram webhook (uses SYENA_TELEGRAM_BOT_TOKEN and SYENA_TELEGRAM_WEBHOOK_SECRET)."""
    from syena.notify import telegram

    typer.echo(telegram.set_webhook(url))


@app.command("eval-gate")
def eval_gate():
    """CI gate: run the deterministic parser evals and fail if below thresholds."""
    from syena import settings_store
    from syena.algos import parser
    from syena.evals import parser_eval

    metrics, rows = parser_eval.evaluate_cases(parser_eval.load_cases(), lambda t: parser.parse(t, use_llm=False))
    typer.echo(metrics)
    for r in rows:
        if not r["ok"]:
            typer.echo(f"FAIL {r['case']['id']}: {r['case']['text']!r} -> {r['info']}")
    if not parser_eval.thresholds_pass(metrics, settings_store.DEFAULTS["evals"]):
        raise typer.Exit(1)


@app.command("gen-key")
def gen_key():
    """Print a fresh Fernet key for SYENA_ENCRYPTION_KEY."""
    typer.echo(Fernet.generate_key().decode())


if __name__ == "__main__":
    app()
