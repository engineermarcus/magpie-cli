"""
Client API interface for Magpie CLI.

Interacts with the Magpie Client Server to execute searches, retrieve TV seasons/episodes,
fetch resolution profiles, manage user accounts/coins, and resolve MP4 download URLs
with server-side billing rules.
"""

import os
import sys
import httpx
from typing import Dict, Any, List, Optional, Tuple
from .device import load_session, save_session, get_device_summary

DEFAULT_REMOTE_URL = "https://api.magpietv.duckdns.org"
DEFAULT_LOCAL_URL = "http://127.0.0.1:7860"


class MagpieAPIError(Exception):
    """Base error for Magpie API failures."""
    pass


class InsufficientCoinsError(MagpieAPIError):
    """Raised when an action requires coins but user wallet is empty."""
    def __init__(self, message: str, balance: Optional[Dict[str, int]] = None):
        super().__init__(message)
        self.balance = balance or {}


class MagpieClient:
    def __init__(self, base_url: Optional[str] = None):
        self.session = load_session()
        self.base_url = (
            base_url
            or os.environ.get("MAGPIE_SERVER_URL")
            or os.environ.get("MAGPIE_API_URL")
            or self._detect_best_server()
        ).rstrip("/")
        self.session["server_url"] = self.base_url
        save_session(self.session)

    def _detect_best_server(self) -> str:
        """Check if local client server is running; fallback to remote production server."""
        try:
            with httpx.Client(timeout=1.0) as client:
                res = client.get(f"{DEFAULT_LOCAL_URL}/api/bundles")
                if res.status_code in (200, 404, 400):
                    return DEFAULT_LOCAL_URL
        except Exception:
            pass
        return DEFAULT_REMOTE_URL

    @property
    def device_id(self) -> str:
        return self.session.get("device_id") or ""

    @property
    def user_id(self) -> Optional[str]:
        return self.session.get("user_id")

    @property
    def token(self) -> Optional[str]:
        return self.session.get("token")

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Magpie-CLI/1.0",
            "X-Device-Id": self.device_id,
            "X-Enforce-Billing": "true",
        }
        if self.user_id:
            headers["X-Account-User-Id"] = self.user_id
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def ensure_authenticated(self) -> Dict[str, Any]:
        """Ensure the terminal device is registered and authenticated with the billing system."""
        # 1. Try existing credentials
        if self.user_id and self.token:
            try:
                balance = self.get_balance()
                return {
                    "user_id": self.user_id,
                    "token": self.token,
                    "balance": balance,
                    "registered": False,
                }
            except Exception:
                # Token may have expired or is invalid, re-register
                pass

        # 2. Register device with client server
        url = f"{self.base_url}/api/account/register"
        payload = {"device_id": self.device_id}
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Magpie-CLI/1.0",
            "X-Device-Id": self.device_id,
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(url, json=payload, headers=headers)
                if resp.status_code != 200:
                    raise MagpieAPIError(f"Account registration failed ({resp.status_code}): {resp.text}")
                data = resp.json()
        except httpx.HTTPError as e:
            raise MagpieAPIError(f"Network error connecting to client server ({self.base_url}): {e}")

        self.session["user_id"] = data.get("user_id")
        self.session["token"] = data.get("token")
        self.session["welcome_bonus"] = data.get("welcome_bonus", 0)
        save_session(self.session)

        balance = self.get_balance()
        return {
            "user_id": self.session["user_id"],
            "token": self.session["token"],
            "balance": balance,
            "welcome_bonus": data.get("welcome_bonus", 0),
            "registered": True,
        }

    def get_balance(self) -> Dict[str, int]:
        """Get current coin balance from billing server."""
        url = f"{self.base_url}/api/account/balance"
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(url, headers=self._get_headers())
                if resp.status_code == 401:
                    raise MagpieAPIError("Authentication expired")
                if resp.status_code != 200:
                    raise MagpieAPIError(f"Failed to fetch balance ({resp.status_code}): {resp.text}")
                data = resp.json()
                return {
                    "total": int(data.get("total", 0)),
                    "bonus": int(data.get("bonus", 0)),
                    "paid": int(data.get("paid", 0)),
                    "pass": int(data.get("pass", 0)),
                }
        except httpx.HTTPError as e:
            raise MagpieAPIError(f"Failed to communicate with billing service: {e}")

    def search(self, query: str) -> List[Dict[str, Any]]:
        """Search movies and TV shows via TMDB through client server."""
        url = f"{self.base_url}/api/search"
        payload = {"query": query.strip()}
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(url, json=payload, headers=self._get_headers())
                if resp.status_code == 404:
                    return []
                if resp.status_code != 200:
                    raise MagpieAPIError(f"Search failed ({resp.status_code}): {resp.text}")
                data = resp.json()
                return data.get("results") or []
        except httpx.HTTPError as e:
            raise MagpieAPIError(f"Search request failed: {e}")

    def get_seasons(self, tmdb_id: str, title: str) -> List[Dict[str, Any]]:
        """Fetch TV show seasons."""
        url = f"{self.base_url}/api/seasons"
        payload = {
            "title": title,
            "tmdbId": str(tmdb_id),
            "subjectType": 2,
        }
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(url, json=payload, headers=self._get_headers())
                if resp.status_code != 200:
                    raise MagpieAPIError(f"Failed to fetch seasons ({resp.status_code}): {resp.text}")
                data = resp.json()
                return data.get("seasons") or []
        except httpx.HTTPError as e:
            raise MagpieAPIError(f"Seasons request failed: {e}")

    def get_episodes(self, tmdb_id: str, title: str, season: int) -> List[int]:
        """Fetch TV show episode numbers for a season."""
        url = f"{self.base_url}/api/episodes"
        payload = {
            "title": title,
            "tmdbId": str(tmdb_id),
            "season": int(season),
        }
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(url, json=payload, headers=self._get_headers())
                if resp.status_code != 200:
                    raise MagpieAPIError(f"Failed to fetch episodes ({resp.status_code}): {resp.text}")
                data = resp.json()
                return data.get("episodes") or []
        except httpx.HTTPError as e:
            raise MagpieAPIError(f"Episodes request failed: {e}")

    def get_resolutions(
        self,
        tmdb_id: str,
        subject_type: int,
        season: int = 1,
        episode: int = 1,
    ) -> List[Dict[str, Any]]:
        """Fetch available stream resolutions and qualities."""
        url = f"{self.base_url}/api/resolutions"
        payload = {
            "subjectId": str(tmdb_id),
            "subjectType": int(subject_type),
            "season": int(season),
            "episode": int(episode),
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, json=payload, headers=self._get_headers())
                if resp.status_code != 200:
                    raise MagpieAPIError(f"Failed to fetch resolutions ({resp.status_code}): {resp.text}")
                data = resp.json()
                return data.get("resolutions") or []
        except httpx.HTTPError as e:
            raise MagpieAPIError(f"Resolutions request failed: {e}")

    def resolve_download(
        self,
        tmdb_id: str,
        subject_type: int,
        season: int,
        episode: int,
        title: str,
        info_hash: str,
        file_idx: int = 0,
        resolution: int = 1080,
        resume: bool = False,
    ) -> Dict[str, Any]:
        """Resolve MP4 download URL. Billing rules are applied server-side.

        Deducts coins from the user's wallet if not already owned within
        the 72-hour window.
        """
        url = f"{self.base_url}/api/android/download-url"
        payload = {
            "subjectId": str(tmdb_id),
            "subjectType": int(subject_type),
            "season": int(season),
            "episode": int(episode),
            "title": title,
            "infoHash": info_hash,
            "fileIdx": file_idx,
            "resolution": resolution,
            "resume": bool(resume),
        }

        try:
            with httpx.Client(timeout=35.0) as client:
                resp = client.post(url, json=payload, headers=self._get_headers())
                if resp.status_code in (402, 403):
                    try:
                        err_data = resp.json()
                        err_msg = err_data.get("error") or err_data.get("detail") or "Insufficient coins"
                    except Exception:
                        err_msg = "Insufficient coins to download"
                    balance = {}
                    try:
                        balance = self.get_balance()
                    except Exception:
                        pass
                    raise InsufficientCoinsError(err_msg, balance=balance)

                if resp.status_code != 200:
                    try:
                        err_data = resp.json()
                        err_msg = err_data.get("error") or resp.text
                    except Exception:
                        err_msg = resp.text
                    raise MagpieAPIError(f"Download URL resolution failed ({resp.status_code}): {err_msg}")

                data = resp.json()
                download_url = data.get("download_url") or data.get("url")
                if not download_url:
                    raise MagpieAPIError("Server returned empty download URL")

                return {
                    "download_url": download_url,
                    "filename": data.get("filename") or "download.mp4",
                    "info_hash": data.get("infoHash") or info_hash,
                    "file_idx": data.get("fileIdx", file_idx),
                    "format": data.get("format", "DIRECT"),
                }
        except httpx.HTTPError as e:
            raise MagpieAPIError(f"Error communicating with download service: {e}")
