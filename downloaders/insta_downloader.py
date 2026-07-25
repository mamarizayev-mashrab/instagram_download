"""Login-required Instagram downloads via instaloader.

Handles: stories, highlights, HD profile pictures, and carousel/photo posts.

A single logged-in instaloader session is created once and reused across requests
(persisted to disk) so we don't re-authenticate on every message — repeated logins get
the account flagged. All blocking work runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import instaloader
from instaloader import Instaloader, Post, Profile, StoryItem

from utils.files import collect_media

# Directory where the persisted session file lives (mounted volume on hosting, ideally).
SESSION_DIR = Path(os.getenv("SESSION_DIR", ".sessions"))


class InstaAuthError(Exception):
    """Login/session problem."""


class InstaDownloadError(Exception):
    """Content could not be downloaded (private, missing, rate-limited, ...)."""


class InstaClient:
    """Thin wrapper that owns one logged-in instaloader instance."""

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._lock = asyncio.Lock()
        self._loader: Instaloader | None = None

    # ---- session lifecycle -------------------------------------------------
    def _build_loader(self, download_dir: Path) -> Instaloader:
        loader = Instaloader(
            dirname_pattern=str(download_dir),
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            post_metadata_txt_pattern="",
            quiet=True,
        )
        return loader

    def _login_blocking(self, download_dir: Path) -> Instaloader:
        loader = self._build_loader(download_dir)
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        session_file = SESSION_DIR / f"session-{self._username}"
        sessionid = os.getenv("IG_SESSIONID", "").strip()
        try:
            if session_file.exists():
                # Preferred: reuse the persisted session cookies (fast, no re-login).
                loader.load_session_from_file(self._username, str(session_file))
            elif sessionid:
                # Cloud/first-run bootstrap: build a session straight from the
                # sessionid cookie (works where password login is blocked), then cache it.
                loader.context._session.cookies.set(
                    "sessionid", sessionid, domain=".instagram.com"
                )
                who = loader.test_login()
                if not who:
                    raise InstaAuthError("IG_SESSIONID is invalid or expired.")
                loader.context.username = who
                loader.save_session_to_file(str(session_file))
            else:
                loader.login(self._username, self._password)
                loader.save_session_to_file(str(session_file))
        except InstaAuthError:
            raise
        except Exception as exc:
            raise InstaAuthError(f"Instagram login failed: {exc}") from exc
        return loader

    async def _ensure_login(self, download_dir: Path) -> Instaloader:
        # Rebuild the loader per request so its output dir points at the temp folder,
        # but reuse the persisted session cookies (fast, no re-login).
        return await asyncio.to_thread(self._login_blocking, download_dir)

    # ---- public download API ----------------------------------------------
    async def download_post(self, shortcode: str, workdir: Path) -> list[Path]:
        """Photo / video / carousel post by shortcode."""
        return await self._run(self._dl_post, shortcode, workdir)

    async def download_profile_pic(self, username: str, workdir: Path) -> list[Path]:
        return await self._run(self._dl_profile_pic, username, workdir)

    async def download_stories(self, username: str, workdir: Path) -> list[Path]:
        return await self._run(self._dl_stories, username, workdir)

    async def download_highlights(self, username: str, workdir: Path) -> list[Path]:
        return await self._run(self._dl_highlights, username, workdir)

    # ---- internal runner ---------------------------------------------------
    async def _run(self, fn, arg: str, workdir: Path) -> list[Path]:
        async with self._lock:  # serialize: one instaloader op at a time is safest
            loader = await self._ensure_login(workdir)
            try:
                await asyncio.to_thread(fn, loader, arg, workdir)
            except (InstaAuthError, InstaDownloadError):
                raise
            except instaloader.exceptions.LoginRequiredException as exc:
                raise InstaAuthError(str(exc)) from exc
            except instaloader.exceptions.PrivateProfileNotFollowedException as exc:
                raise InstaDownloadError("This profile is private and not followed.") from exc
            except instaloader.exceptions.ProfileNotExistsException as exc:
                raise InstaDownloadError("That Instagram profile does not exist.") from exc
            except Exception as exc:
                raise InstaDownloadError(f"Download failed: {exc}") from exc

        files = collect_media(workdir)
        if not files:
            raise InstaDownloadError("Nothing available to download (empty or expired).")
        return files

    # ---- blocking implementations (run in a thread) ------------------------
    @staticmethod
    def _dl_post(loader: Instaloader, shortcode: str, workdir: Path) -> None:
        post = Post.from_shortcode(loader.context, shortcode)
        loader.download_post(post, target=workdir.name)

    @staticmethod
    def _dl_profile_pic(loader: Instaloader, username: str, workdir: Path) -> None:
        profile = Profile.from_username(loader.context, username)
        loader.download_profilepic(profile)

    @staticmethod
    def _dl_stories(loader: Instaloader, username: str, workdir: Path) -> None:
        profile = Profile.from_username(loader.context, username)
        got = False
        for story in loader.get_stories(userids=[profile.userid]):
            for item in story.get_items():  # type: StoryItem
                loader.download_storyitem(item, target=workdir.name)
                got = True
        if not got:
            raise InstaDownloadError(f"@{username} has no active stories right now.")

    @staticmethod
    def _dl_highlights(loader: Instaloader, username: str, workdir: Path) -> None:
        profile = Profile.from_username(loader.context, username)
        got = False
        for highlight in loader.get_highlights(user=profile):
            for item in highlight.get_items():
                loader.download_storyitem(item, target=workdir.name)
                got = True
        if not got:
            raise InstaDownloadError(f"@{username} has no highlights available.")
