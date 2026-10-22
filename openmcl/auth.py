"""Microsoft account authentication for Minecraft.

Flow (the same one every third-party launcher uses):

    Microsoft OAuth 2.0 device code
      -> Xbox Live user token
      -> XSTS token for rp://api.minecraftservices.com/
      -> Minecraft services bearer token
      -> entitlement + profile check

This requires a real Microsoft account that owns Minecraft: Java Edition.
There is deliberately no offline/cracked authentication path.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Optional

from . import config
from .net import session

log = logging.getLogger(__name__)

TENANT = "consumers"
DEVICE_CODE_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/devicecode"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
XBL_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_ENTITLEMENTS_URL = "https://api.minecraftservices.com/entitlements/mcstore"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"

SCOPE = "XboxLive.signin offline_access"

# Friendlier text for the XSTS failures people actually hit.
XSTS_ERRORS = {
    "2148916233": (
        "This Microsoft account has no Xbox profile. Sign in once at "
        "https://www.minecraft.net to create one, then try again."
    ),
    "2148916235": "Xbox Live is not available in this account's country or region.",
    "2148916236": "This account needs adult verification before it can be used.",
    "2148916237": "This account needs adult verification before it can be used.",
    "2148916238": (
        "This is a child account. An adult needs to add it to a Microsoft "
        "family group before it can sign in."
    ),
}


class AuthError(RuntimeError):
    pass


class PendingSignIn:
    """A device-code sign-in waiting for the user to approve it in a browser."""

    def __init__(self, data: dict, client_id: str):
        self.client_id = client_id
        self.device_code: str = data["device_code"]
        self.user_code: str = data["user_code"]
        self.verification_uri: str = data.get(
            "verification_uri", "https://www.microsoft.com/link"
        )
        self.message: str = data.get("message", "")
        self.interval: int = int(data.get("interval", 5))
        self.expires_at: float = time.time() + int(data.get("expires_in", 900))


@dataclass
class Account:
    uuid: str
    name: str
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    xuid: str = ""        # Xbox user id, passed to the game as --xuid
    user_hash: str = ""   # Xbox user hash, used only to build the login token
    skin_url: str = ""

    @property
    def expired(self) -> bool:
        # Refresh a minute early so a long install doesn't launch with a dead token.
        return time.time() >= self.expires_at - 60


def _post_json(url: str, payload: dict, headers: Optional[dict] = None) -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    h.update(headers or {})
    r = session().post(url, json=payload, headers=h, timeout=30)
    if r.status_code >= 400:
        raise AuthError(f"{url} returned {r.status_code}: {r.text[:400]}")
    return r.json()


def start_device_code(client_id: str) -> PendingSignIn:
    if not client_id:
        raise AuthError(
            "No Microsoft client ID configured. Register an Azure application "
            "(see README: Microsoft sign-in setup) and paste its Application "
            "(client) ID into Settings."
        )
    r = session().post(
        DEVICE_CODE_URL, data={"client_id": client_id, "scope": SCOPE}, timeout=30
    )
    if r.status_code >= 400:
        raise AuthError(f"Could not start sign-in: {r.status_code} {r.text[:300]}")
    return PendingSignIn(r.json(), client_id)


def poll_device_code(
    pending: PendingSignIn,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> dict:
    """Block until the user approves the sign-in. Returns the MS token payload."""
    interval = pending.interval
    while True:
        if should_cancel and should_cancel():
            raise AuthError("Sign-in cancelled.")
        if time.time() > pending.expires_at:
            raise AuthError("The sign-in code expired. Start again.")
        time.sleep(interval)
        r = session().post(
            TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": pending.client_id,
                "device_code": pending.device_code,
            },
            timeout=30,
        )
        body = r.json()
        if r.status_code == 200:
            return body
        err = body.get("error", "")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        if err == "authorization_declined":
            raise AuthError("Sign-in was declined.")
        if err in ("expired_token", "code_expired"):
            raise AuthError("The sign-in code expired. Start again.")
        raise AuthError(body.get("error_description") or f"Sign-in failed: {err}")


def refresh_ms_token(client_id: str, refresh_token: str) -> dict:
    r = session().post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "scope": SCOPE,
        },
        timeout=30,
    )
    if r.status_code >= 400:
        raise AuthError(
            "Your saved sign-in is no longer valid - sign in again. "
            f"({r.status_code})"
        )
    return r.json()


def _xbox_login(ms_access_token: str) -> tuple[str, str, str]:
    """Microsoft token -> (XSTS token, user hash, XUID)."""
    xbl = _post_json(
        XBL_URL,
        {
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={ms_access_token}",
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
        },
    )
    xbl_token = xbl["Token"]

    r = session().post(
        XSTS_URL,
        json={
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        },
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=30,
    )
    if r.status_code == 401:
        try:
            xerr = str(r.json().get("XErr", ""))
        except ValueError:
            xerr = ""
        raise AuthError(XSTS_ERRORS.get(xerr, f"Xbox Live rejected the sign-in ({xerr or 401})."))
    if r.status_code >= 400:
        raise AuthError(f"XSTS failed: {r.status_code} {r.text[:300]}")
    xsts = r.json()
    claims = xsts["DisplayClaims"]["xui"][0]
    # uhs authenticates the Minecraft login call; xid is the account's real
    # Xbox user id, which the game wants as --xuid. They are not the same.
    return xsts["Token"], claims["uhs"], claims.get("xid", "")


def _minecraft_login(xsts_token: str, user_hash: str) -> dict:
    r = session().post(
        MC_LOGIN_URL,
        json={"identityToken": f"XBL3.0 x={user_hash};{xsts_token}"},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=30,
    )
    # By far the most common first-run failure, and the raw response is
    # unhelpful: everything before this step succeeded, so it reads as if the
    # launcher is broken when the application simply has not been approved.
    if r.status_code == 403 and "Invalid app registration" in r.text:
        raise AuthError(
            "Microsoft has not approved this application for Minecraft yet.\n\n"
            "Your sign-in worked -- Microsoft, Xbox Live and the security check "
            "all accepted it. Only Minecraft itself refused, because new "
            "applications must be approved before they can sign players in.\n\n"
            "Apply at https://aka.ms/mce-reviewappid using the Application "
            "(client) ID from Settings, then try again once Microsoft replies. "
            "There is nothing to fix in the launcher."
        )
    if r.status_code >= 400:
        raise AuthError(f"Minecraft refused the sign-in ({r.status_code}): {r.text[:300]}")
    return r.json()


def _check_entitlement(mc_token: str) -> None:
    r = session().get(
        MC_ENTITLEMENTS_URL,
        headers={"Authorization": f"Bearer {mc_token}"},
        timeout=30,
    )
    if r.status_code >= 400:
        raise AuthError(f"Could not check game ownership ({r.status_code}).")
    if not r.json().get("items"):
        raise AuthError(
            "This account does not own Minecraft: Java Edition. "
            "Game Pass accounts must launch the game once from the official "
            "launcher before third-party launchers can use them."
        )


def _profile(mc_token: str) -> dict:
    r = session().get(
        MC_PROFILE_URL, headers={"Authorization": f"Bearer {mc_token}"}, timeout=30
    )
    if r.status_code == 404:
        raise AuthError(
            "This account owns the game but has no Minecraft profile yet. "
            "Create your username at https://www.minecraft.net/msaprofile first."
        )
    if r.status_code >= 400:
        raise AuthError(f"Could not read the Minecraft profile ({r.status_code}).")
    return r.json()


def _finish(ms_tokens: dict) -> Account:
    """Shared tail of both sign-in and refresh."""
    xsts_token, user_hash, xuid = _xbox_login(ms_tokens["access_token"])
    mc = _minecraft_login(xsts_token, user_hash)
    mc_token = mc["access_token"]
    _check_entitlement(mc_token)
    prof = _profile(mc_token)

    raw_uuid = prof["id"]
    pretty = (
        f"{raw_uuid[0:8]}-{raw_uuid[8:12]}-{raw_uuid[12:16]}-"
        f"{raw_uuid[16:20]}-{raw_uuid[20:32]}"
    )
    skin = ""
    for s in prof.get("skins", []):
        if s.get("state") == "ACTIVE":
            skin = s.get("url", "")
            break

    return Account(
        uuid=pretty,
        name=prof["name"],
        access_token=mc_token,
        refresh_token=ms_tokens.get("refresh_token", ""),
        expires_at=time.time() + int(mc.get("expires_in", 86400)),
        xuid=xuid,
        user_hash=user_hash,
        skin_url=skin,
    )


def complete_sign_in(pending: PendingSignIn, should_cancel=None) -> Account:
    return _finish(poll_device_code(pending, should_cancel))


def refresh(account: Account, client_id: str) -> Account:
    if not account.refresh_token:
        raise AuthError("This account has no saved refresh token - sign in again.")
    fresh = _finish(refresh_ms_token(client_id, account.refresh_token))
    # Microsoft does not always return a new refresh token; keep the old one.
    if not fresh.refresh_token:
        fresh.refresh_token = account.refresh_token
    return fresh


class AccountStore:
    """accounts.json, with 0600 permissions. Tokens are stored in plain text."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (config.DATA / "accounts.json")
        self.accounts: list[Account] = []
        self.selected: str = ""
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("accounts.json unreadable; starting empty")
            return
        fields = set(Account.__dataclass_fields__)
        self.accounts = [
            Account(**{k: v for k, v in a.items() if k in fields})
            for a in raw.get("accounts", [])
        ]
        self.selected = raw.get("selected", "")

    def save(self) -> None:
        config.ensure_dirs()
        payload = {
            "accounts": [asdict(a) for a in self.accounts],
            "selected": self.selected,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), "utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    def add(self, account: Account) -> None:
        self.accounts = [a for a in self.accounts if a.uuid != account.uuid]
        self.accounts.append(account)
        if not self.selected:
            self.selected = account.uuid
        self.save()

    def remove(self, uuid: str) -> None:
        self.accounts = [a for a in self.accounts if a.uuid != uuid]
        if self.selected == uuid:
            self.selected = self.accounts[0].uuid if self.accounts else ""
        self.save()

    def get(self, uuid: str) -> Optional[Account]:
        return next((a for a in self.accounts if a.uuid == uuid), None)

    def current(self) -> Optional[Account]:
        return self.get(self.selected) or (self.accounts[0] if self.accounts else None)

    def ensure_fresh(self, account: Account, client_id: str) -> Account:
        """Refresh the token if it is close to expiry, persisting the result."""
        if not account.expired:
            return account
        fresh = refresh(account, client_id)
        self.add(fresh)
        if self.selected == account.uuid:
            self.selected = fresh.uuid
            self.save()
        return fresh
