"""
Gallery crawler for the Game Sprite Importer.

Port of the original standalone downloader (archive/old-main
Old_Files/downloader.py) with the interactive CLI, Downloads-folder logic,
and stdout Tee replaced by progress callbacks, a cancel token, and logging
to raw/log.txt. The proven parts are kept verbatim: page selectors
(img#img, a#next / accesskey="n"), resume via completed.txt/resume.txt,
the failed.txt -> retry pass -> failed_final.txt protocol, and the
repeating-next-link guard.
"""

import json
import pathlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
DEFAULT_DOWNLOAD_DELAY = 0.25
RETRY_COUNT = 2
RETRY_DELAY = 0
HTTP_TIMEOUT = (5, 8)
IMG_TIMEOUT = (5, 10)


class CancelToken:
    """Thread-safe cancellation flag shared between UI and crawler thread."""

    def __init__(self):
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass
class CrawlProgress:
    """One progress event forwarded to the UI."""
    index: int
    message: str
    saved_path: Optional[pathlib.Path] = None
    failed: bool = False


@dataclass
class CrawlResult:
    downloaded: int = 0
    skipped: int = 0
    failed_pages: List[str] = field(default_factory=list)
    completed: bool = False   # False if cancelled or aborted mid-walk
    error: Optional[str] = None   # site-level failure (bad cookies, 509 limit)


def parse_cookie_header(cookie_str: str) -> Dict[str, str]:
    """Parse a 'k=v; k2=v2; ...' cookie header string into a dict."""
    jar: Dict[str, str] = {}
    for part in cookie_str.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and v:
                jar[k] = v
    return jar


def needs_cookies(url: str) -> bool:
    """True if the host is known to require login cookies (ExHentai)."""
    return "exhentai.org" in urlparse(url).netloc.lower()


def _sanitize_filename(s: str) -> str:
    s = re.sub(r"[^\w\-\.]+", "_", s)
    return s.strip("._") or "img"


def _session_with_headers(referer: str, cookies: Optional[Dict[str, str]]) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Referer": referer})
    if cookies:
        s.cookies.update(cookies)
    return s


class _Crawl:
    """Single crawl run: bundles session, logging, and resume bookkeeping."""

    def __init__(
        self,
        dest_dir: pathlib.Path,
        start_url: str,
        cookies: Optional[Dict[str, str]],
        delay: float,
        progress_cb: Optional[Callable[[CrawlProgress], None]],
        cancel: Optional[CancelToken],
        file_prefix: str = "",
    ):
        self.dest = dest_dir
        self.start_url = start_url
        self.delay = max(0.0, delay)
        self.progress_cb = progress_cb
        self.cancel = cancel or CancelToken()
        self.file_prefix = file_prefix

        # Bookkeeping files are per-source (prefixed) so a follow-up crawl
        # into the same workspace keeps its own resume protocol.
        self.completed_file = dest_dir / f"{file_prefix}completed.txt"
        self.resume_file = dest_dir / f"{file_prefix}resume.txt"
        self.failed_file = dest_dir / f"{file_prefix}failed.txt"
        self.failed_final_file = dest_dir / f"{file_prefix}failed_final.txt"
        self.log_path = dest_dir / "log.txt"

        self.session = _session_with_headers(referer=start_url, cookies=cookies)
        self.result = CrawlResult()

    # ------------------------------------------------------------------
    # Logging / progress
    # ------------------------------------------------------------------
    def _log(self, message: str, index: int = 0, saved: Optional[pathlib.Path] = None,
             failed: bool = False) -> None:
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {message}\n")
        except OSError:
            pass
        if self.progress_cb:
            self.progress_cb(CrawlProgress(
                index=index, message=message, saved_path=saved, failed=failed
            ))

    # ------------------------------------------------------------------
    # Network helpers (kept from the original)
    # ------------------------------------------------------------------
    def _get_with_retries(self, url: str, description: str,
                          timeout: Tuple[int, int]) -> Optional[requests.Response]:
        for attempt in range(1, RETRY_COUNT + 1):
            if self.cancel.cancelled():
                return None
            try:
                resp = self.session.get(url, timeout=timeout)
                resp.raise_for_status()
                return resp
            except Exception as e:
                self._log(f"Attempt {attempt}/{RETRY_COUNT} failed for {description}: {e}")
                if attempt < RETRY_COUNT and RETRY_DELAY > 0:
                    time.sleep(RETRY_DELAY)
        self._log(f"All retries failed for {description}.")
        return None

    def _fetch_image_and_next(self, page_url: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract the full-size image URL and next-page URL from a gallery page."""
        resp = self._get_with_retries(page_url, "image page", HTTP_TIMEOUT)
        if not resp:
            return None, None

        # ExHentai without valid cookies serves an image (the "sad panda")
        # instead of a gallery page. Abort with a clear message rather than
        # walking a dead gallery.
        content_type = resp.headers.get("Content-Type", "")
        if content_type.startswith("image/"):
            self.result.error = (
                "The site returned an image instead of a gallery page, "
                "your ExHentai cookies are invalid or expired. Update them "
                "on the Source screen and resume this import."
            )
            self._log(self.result.error, failed=True)
            return None, None

        soup = BeautifulSoup(resp.text, "html.parser")

        img_tag = soup.find("img", id="img")
        img_url = img_tag["src"] if img_tag and img_tag.has_attr("src") else None

        next_a = soup.find("a", id="next")
        if not next_a:
            next_a = soup.find("a", attrs={"accesskey": "n"})
        next_url = (
            urljoin(page_url, next_a["href"])
            if next_a and next_a.has_attr("href") else None
        )

        if img_url:
            img_url = urljoin(page_url, img_url)
        return img_url, next_url

    def _fetch_next_only(self, page_url: str) -> Optional[str]:
        resp = self._get_with_retries(page_url, "image page", HTTP_TIMEOUT)
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        next_a = soup.find("a", id="next") or soup.find("a", attrs={"accesskey": "n"})
        if next_a and next_a.has_attr("href"):
            return urljoin(page_url, next_a["href"])
        return None

    # ------------------------------------------------------------------
    # Resume bookkeeping (protocol kept from the original)
    # ------------------------------------------------------------------
    def _load_completed(self) -> set:
        completed = set()
        if self.completed_file.exists():
            with self.completed_file.open() as f:
                for line in f:
                    try:
                        completed.add(int(line.strip()))
                    except ValueError:
                        pass
        return completed

    def _save_completed(self, index: int) -> None:
        with self.completed_file.open("a") as f:
            f.write(f"{index}\n")

    def _save_resume(self, index: int, url: str) -> None:
        with self.resume_file.open("w") as f:
            f.write(f"{index}\n{url}\n")

    def _log_failed(self, index: int, url: str, path: pathlib.Path) -> None:
        with path.open("a") as f:
            f.write(f"{index}\t{url}\n")
        self._log(f"Logged failed download for image {index}", index=index, failed=True)

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------
    def _process_image(self, index: int, page_url: str,
                       fail_log: pathlib.Path) -> Optional[str]:
        """Download one gallery image; returns the next page URL (or None)."""
        self._log(f"Image {index}: fetching page", index=index)
        img_url, next_url = self._fetch_image_and_next(page_url)
        if self.result.error:
            return None     # site-level failure, stop the walk
        if not img_url:
            self._log(f"Image {index}: could not find image on page", index=index, failed=True)
            self._log_failed(index, page_url, fail_log)
            return next_url

        # ExHentai bandwidth limit: full-size links degrade to a 509
        # placeholder. Every further download would be the placeholder,
        # so stop and let the user resume later.
        if img_url.split("?")[0].endswith("509.gif"):
            self.result.error = (
                "The site is serving its bandwidth-limit placeholder "
                "(509). Wait for your quota to reset, then resume this "
                "import, completed images are kept."
            )
            self._log(self.result.error, failed=True)
            return None

        img_resp = self._get_with_retries(img_url, "image file", IMG_TIMEOUT)
        if not img_resp:
            self._log_failed(index, page_url, fail_log)
            return next_url

        ext = img_url.split(".")[-1].split("?")[0]
        filename = _sanitize_filename(f"{self.file_prefix}{index:05d}.{ext}")
        filepath = self.dest / filename
        with filepath.open("wb") as f:
            f.write(img_resp.content)

        self._save_completed(index)
        self.result.downloaded += 1
        self._log(f"Image {index}: saved {filename}", index=index, saved=filepath)
        return next_url

    def run(self) -> CrawlResult:
        self.dest.mkdir(parents=True, exist_ok=True)
        self._log("=" * 40)
        self._log(f"Crawl started: {self.start_url}")

        completed_indices = self._load_completed()

        # Resume from resume.txt if present and further along than the start
        current_url = self.start_url
        image_count = 1
        if self.resume_file.exists():
            try:
                lines = self.resume_file.read_text().splitlines()
                saved_index = int(lines[0])
                saved_url = lines[1].strip()
                if saved_url:
                    image_count = saved_index
                    current_url = saved_url
                    self._log(f"Resuming from image {image_count}")
            except (ValueError, IndexError, OSError):
                pass

        while current_url:
            if self.cancel.cancelled():
                self._log("Crawl cancelled by user.")
                return self.result

            if image_count in completed_indices:
                self._log(f"Image {image_count}: already downloaded, skipping",
                          index=image_count)
                self.result.skipped += 1
                next_url = self._fetch_next_only(current_url)
                if not next_url or next_url == current_url:
                    break
                image_count += 1
                current_url = next_url
                self._save_resume(image_count, current_url)
                continue

            next_url = self._process_image(image_count, current_url, self.failed_file)
            if next_url:
                if next_url == current_url:
                    self._log("Detected repeating next link. Stopping.")
                    break
                image_count += 1
                current_url = next_url
                self._save_resume(image_count, current_url)
            else:
                self._log("No more images or next link missing. Ending.")
                break

            time.sleep(self.delay)

        # Retry pass for failed items
        if (self.failed_file.exists() and not self.cancel.cancelled()
                and not self.result.error):
            self._log("Starting retry pass for failed items.")
            with self.failed_file.open() as f:
                failed_items = [line.strip().split("\t") for line in f if "\t" in line]
            self.failed_file.unlink()

            for index_str, url in failed_items:
                if self.cancel.cancelled():
                    return self.result
                try:
                    idx = int(index_str)
                except ValueError:
                    continue
                self._process_image(idx, url, self.failed_final_file)

        if self.failed_final_file.exists():
            with self.failed_final_file.open() as f:
                self.result.failed_pages = [
                    line.split("\t", 1)[1].strip() for line in f if "\t" in line
                ]

        self.result.completed = (not self.cancel.cancelled()
                                 and self.result.error is None)
        self._log(
            f"Crawl finished: {self.result.downloaded} downloaded, "
            f"{self.result.skipped} skipped, {len(self.result.failed_pages)} failed."
        )
        return self.result


def crawl_gallery(
    dest_dir: pathlib.Path,
    start_url: str,
    cookies: Optional[Dict[str, str]] = None,
    delay: float = DEFAULT_DOWNLOAD_DELAY,
    progress_cb: Optional[Callable[[CrawlProgress], None]] = None,
    cancel: Optional[CancelToken] = None,
    file_prefix: str = "",
) -> CrawlResult:
    """Crawl a gallery starting at start_url, saving images into dest_dir.

    Resumable: re-running with the same dest_dir skips completed images and
    picks up from resume.txt. Cancellation via the token is lossless.
    file_prefix namespaces filenames and resume bookkeeping so additional
    sources can crawl into the same folder without collisions.
    """
    return _Crawl(dest_dir, start_url, cookies, delay, progress_cb, cancel,
                  file_prefix=file_prefix).run()


def write_download_meta(dest_dir: pathlib.Path, game_name: str, start_url: str) -> None:
    """Write download_meta.json (game name consumed later by finalize)."""
    meta = {
        "source_game": game_name,
        "start_url": start_url,
        "created_at_unix": int(time.time()),
    }
    dest_dir.mkdir(parents=True, exist_ok=True)
    with (dest_dir / "download_meta.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------
# CLI entry point for testing (Phase A harness)
# ----------------------------------------------------------------------
def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Gallery crawler (test harness)")
    parser.add_argument("start_url")
    parser.add_argument("dest_dir")
    parser.add_argument("--delay", type=float, default=DEFAULT_DOWNLOAD_DELAY)
    parser.add_argument("--cookies", default="", help="cookie header string k=v; k2=v2")
    args = parser.parse_args()

    cookies = parse_cookie_header(args.cookies) if args.cookies else None
    result = crawl_gallery(
        pathlib.Path(args.dest_dir),
        args.start_url,
        cookies=cookies,
        delay=args.delay,
        progress_cb=lambda p: print(p.message),
    )
    print(f"downloaded={result.downloaded} skipped={result.skipped} "
          f"failed={len(result.failed_pages)} completed={result.completed}")


if __name__ == "__main__":
    _cli()
