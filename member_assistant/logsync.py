"""Keep the logs when the hosted server restarts.

A Hugging Face Space loses its files whenever it restarts or wakes up, which would empty the
staff dashboard and reset the daily budget. With ``LOG_DATASET`` (a private dataset such as
``<user>/website-assistant-logs``) and an ``HF_TOKEN`` that can write to it, the server:

- copies the dataset into the log folder when it starts, before anything reads the logs,
- uploads the log folder to the dataset every ``LOG_SYNC_MINUTES`` (default 2) while it runs,
- uploads once more when it shuts down.

The logs hold only redacted records (see privacy.py), and the dataset is created private.
Without ``LOG_DATASET`` nothing happens and the logs stay local, as in development.
"""

import logging
import os
from pathlib import Path

log = logging.getLogger("member_assistant.logsync")


class LogSync:
    def __init__(self, folder: Path, repo_id: str, token: str | None, every_minutes: float):
        from huggingface_hub import CommitScheduler, HfApi, snapshot_download

        self.folder, self.repo_id = folder, repo_id
        api = HfApi(token=token)
        # The deploy script creates the dataset; a token limited to it may not be allowed to create repos.
        if not api.repo_exists(repo_id, repo_type="dataset"):
            api.create_repo(repo_id, repo_type="dataset", private=True)
        folder.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id, repo_type="dataset", local_dir=folder, token=token,
                          allow_patterns=["*.jsonl"])
        self.scheduler = CommitScheduler(repo_id=repo_id, repo_type="dataset", folder_path=folder,
                                         every=every_minutes, private=True, token=token,
                                         allow_patterns=["*.jsonl"], hf_api=api)
        log.info("Logs restored from and backed up to the private dataset %s every %s min", repo_id, every_minutes)

    def flush(self) -> None:
        try:
            self.scheduler.trigger().result(timeout=60)
        except Exception:  # never block shutdown on the backup
            log.exception("Final log upload to %s failed", self.repo_id)

    def stop(self) -> None:
        self.flush()
        self.scheduler.stop()


def start_from_env(folder: Path) -> LogSync | None:
    repo_id = os.environ.get("LOG_DATASET", "").strip()
    if not repo_id:
        return None
    token = os.environ.get("HF_TOKEN") or None
    try:
        return LogSync(folder, repo_id, token, float(os.environ.get("LOG_SYNC_MINUTES", 2)))
    except Exception:
        # Serving visitors matters more than the backup: start anyway, with local logs only.
        log.exception("Couldn't set up the log backup to %s; logs will be lost on restart", repo_id)
        return None
