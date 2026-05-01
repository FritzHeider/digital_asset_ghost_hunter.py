from __future__ import annotations

import csv
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import requests

import phase1_smart_discovery
import phase2_dead_link_detection


class Phase1PaginationTest(unittest.TestCase):
    def test_search_legacy_video_channels_follows_pages(self) -> None:
        responses = [
            {
                "items": [{"snippet": {"channelId": "a"}}],
                "nextPageToken": "next",
            },
            {"items": [{"snippet": {"channelId": "b"}}]},
        ]

        with mock.patch("phase1_smart_discovery.youtube_get", side_effect=responses):
            channel_ids = phase1_smart_discovery._search_legacy_video_channels(
                session=mock.Mock(),
                api_key="key",
                query="tech",
                published_before="2016-01-01T00:00:00Z",
                published_after=None,
                max_channels=10,
            )

        self.assertEqual(channel_ids, ["a", "b"])

    def test_recent_upload_403_keeps_channel_eligible(self) -> None:
        """Non-fatal 403 returns None (no recent upload date), channel stays eligible."""
        response = requests.Response()
        response.status_code = 403
        response._content = b'{"error":{"message":"Forbidden","errors":[{"reason":"forbidden"}]}}'
        error = requests.HTTPError("403 Client Error: Forbidden")
        error.response = response

        with (
            mock.patch("phase1_smart_discovery.youtube_get", side_effect=error),
            self.assertLogs("phase1_smart_discovery", level="WARNING"),
        ):
            result = phase1_smart_discovery._has_recent_upload(
                mock.Mock(),
                api_key="key",
                channel_id="channel1",
                recent_days=180,
            )
        # None means "no recent upload detected" — channel stays eligible
        self.assertIsNone(result)

    def test_recent_upload_quota_403_remains_fatal(self) -> None:
        response = requests.Response()
        response.status_code = 403
        response._content = (
            b'{"error":{"message":"Quota exceeded","errors":[{"reason":"quotaExceeded"}]}}'
        )
        error = requests.HTTPError("403 Client Error: Forbidden")
        error.response = response

        with mock.patch("phase1_smart_discovery.youtube_get", side_effect=error):
            with self.assertRaises(requests.HTTPError):
                phase1_smart_discovery._has_recent_upload(
                    mock.Mock(),
                    api_key="key",
                    channel_id="channel1",
                    recent_days=180,
                )

    def test_recent_upload_returns_date_when_found(self) -> None:
        """A channel with a recent upload returns the publishedAt date."""
        response_data = {
            "items": [{"snippet": {"publishedAt": "2025-01-15T10:00:00Z"}}]
        }
        with mock.patch("phase1_smart_discovery.youtube_get", return_value=response_data):
            result = phase1_smart_discovery._has_recent_upload(
                mock.Mock(),
                api_key="key",
                channel_id="channel1",
                recent_days=180,
            )
        self.assertEqual(result, "2025-01-15T10:00:00Z")

    def test_discover_channels_raises_without_keywords(self) -> None:
        with self.assertRaises(ValueError, msg="Should require keywords"):
            phase1_smart_discovery.discover_channels(
                api_key="key",
                published_before="2016-01-01T00:00:00Z",
                min_video_count=1,
                recent_days=0,
                max_channels=1,
            )

    def test_search_quota_error_logs_warning_and_returns_partial(self) -> None:
        page1 = {"items": [{"snippet": {"channelId": "ch1"}}], "nextPageToken": "tok"}
        from cashtube_utils import YouTubeQuotaError
        responses = [page1, YouTubeQuotaError("quota")]

        def side_effect(*args, **kwargs):
            val = responses.pop(0)
            if isinstance(val, Exception):
                raise val
            return val

        with (
            mock.patch("phase1_smart_discovery.youtube_get", side_effect=side_effect),
            self.assertLogs("phase1_smart_discovery", level="WARNING") as cm,
        ):
            ids = phase1_smart_discovery._search_legacy_video_channels(
                session=mock.Mock(),
                api_key="key",
                query="tech",
                published_before="2016-01-01T00:00:00Z",
                published_after=None,
                max_channels=50,
            )

        self.assertEqual(ids, ["ch1"])
        self.assertTrue(any("Quota exhausted" in msg for msg in cm.output))


class Phase2DryRunTest(unittest.TestCase):
    def test_process_channel_dry_run_schema(self) -> None:
        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=False):
                if url.endswith("sort=p"):
                    return {"entries": [{"id": "video1"}]}
                return {"description": "Sponsor: https://www.example.com/deal"}

        fake_module = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        with mock.patch.dict(sys.modules, {"yt_dlp": fake_module}):
            rows = phase2_dead_link_detection.process_channel(
                "https://www.youtube.com/channel/test",
                top_n_videos=1,
                dry_run=True,
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].dead_domain, "example.com")
        self.assertEqual(rows[0].status, "unchecked")
        self.assertEqual(rows[0].error_category, "dry_run")
        self.assertEqual(rows[0].priority_score, 0)

    def test_empty_csv_still_writes_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "phase2.csv"
            phase2_dead_link_detection.write_dead_links_to_csv([], str(output))
            with output.open(newline="", encoding="utf-8") as f:
                header = next(csv.reader(f))

        self.assertIn("dead_domain", header)
        self.assertIn("error_category", header)
        self.assertIn("priority_score", header)


if __name__ == "__main__":
    unittest.main()
