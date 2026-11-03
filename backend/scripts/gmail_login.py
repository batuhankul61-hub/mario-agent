"""One-time Gmail sign-in for Mario.

    python scripts/gmail_login.py

Opens Google's consent page in the browser (read + send scopes only) and saves
the refresh token to backend/data/gmail-token.json. Needs an OAuth client of
type "Desktop app" saved as backend/gmail-client.json (see README).
Run it again to switch accounts; delete the token file to disconnect.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.tools.gmail import SCOPES  # noqa: E402


def main() -> None:
    settings = get_settings()
    if not settings.gmail_client_file.exists():
        sys.exit(f"OAuth istemci dosyası yok: {settings.gmail_client_file}\nREADME'deki 'Gmail bağlama' adımlarına bak.")
    flow = InstalledAppFlow.from_client_secrets_file(str(settings.gmail_client_file), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.gmail_token_file.write_text(creds.to_json(), encoding="utf-8")
    print(f"Gmail bağlandı. Token: {settings.gmail_token_file}")


if __name__ == "__main__":
    main()
