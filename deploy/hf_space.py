"""Deploy the website assistant to a Hugging Face Space.

    pip install huggingface_hub
    hf auth login                                  # once; paste a token with write access
    python deploy/hf_space.py <username>/<space-name>

The account needs a Hugging Face PRO subscription to host a Docker Space.
Creates the (public) Space if it doesn't exist yet, asks for its secrets, and uploads the code.
Hugging Face then builds the image, crawling the website as part of the build (about 5 minutes),
and starts the app at https://<username>-<space-name>.hf.space.

It also creates a private dataset, <space-name>-logs, that the app backs its logs up to, so the
staff dashboard (/admin) keeps its data when the Space restarts (see member_assistant/logsync.py).

Secrets are typed into this terminal and sent straight to Hugging Face. Keys and passwords are
hidden as you type. Press Enter to keep a value that is already set on the Space. Run again with
--code-only to upload new code without touching the secrets, or with --secrets NAME,NAME to set
only those.
"""

import argparse
import getpass
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

REPO = Path(__file__).resolve().parent.parent
# What the image needs (see the Dockerfile). Everything else stays on GitHub.
UPLOAD = ["Dockerfile", ".dockerignore", "requirements.txt", "member_assistant", "deploy/start.sh"]
SECRETS = [
    # name, prompt, hidden
    ("ANTHROPIC_API_KEY", "Anthropic API key (a key used only for this site)", True),
    ("CLIENT_SITE", "Client website root URL", False),
    ("COUNTY_WIKI", "Wikipedia article listing the region's municipalities", False),
    ("ACCESS_CODE", "Access code visitors must enter (strongly recommended until the client approves)", True),
    ("ADMIN_PASSWORD", "Password for the staff dashboard at /admin (empty turns the dashboard off)", True),
    ("HF_TOKEN", "Hugging Face token that can write to the logs dataset (a fine-grained token limited to it)", True),
]


def saved_crawl_args() -> dict:
    """Defaults from a local crawl, so the site URL doesn't have to be typed again."""
    try:
        return json.loads((REPO / "private" / "profile.json").read_text()).get("crawl_args") or {}
    except (OSError, ValueError):
        return {}


def ask_secrets(api: HfApi, repo_id: str, new_space: bool, only: set[str] | None = None) -> None:
    saved = saved_crawl_args()
    defaults = {"CLIENT_SITE": saved.get("site"), "COUNTY_WIKI": saved.get("county_wiki")}
    keep = "" if new_space else ", Enter keeps the current value"
    for name, prompt, hidden in SECRETS:
        if only and name not in only:
            continue
        default = defaults.get(name)
        hint = f" [{default}]" if default and not hidden else ""
        text = f"{name}: {prompt}{hint}{keep}: "
        value = (getpass.getpass(text) if hidden else input(text)).strip() or (default or "")
        if value:
            api.add_space_secret(repo_id, name, value)
            print(f"  set {name}")
        elif name == "ANTHROPIC_API_KEY" and new_space:
            sys.exit("An Anthropic API key is required.")
        else:
            print(f"  left {name} unchanged" if not new_space else f"  {name} not set")


def staged_files() -> Path:
    """A copy of only the files the image needs, plus the Space's own README card."""
    stage = Path(tempfile.mkdtemp(prefix="hf-space-"))
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    for rel in UPLOAD:
        src, dst = REPO / rel, stage / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, ignore=ignore)
        else:
            shutil.copy2(src, dst)
    shutil.copy2(REPO / "deploy" / "huggingface" / "README.md", stage / "README.md")
    return stage


def git_version() -> str:
    try:
        out = subprocess.run(["git", "-C", str(REPO), "describe", "--always", "--dirty"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("space", help="<space-name> or <username>/<space-name>, for example website-assistant")
    parser.add_argument("--code-only", action="store_true", help="Upload code; don't ask for secrets")
    parser.add_argument("--secrets", help="Comma-separated secret names to set; the others are left alone")
    args = parser.parse_args()

    api = HfApi()
    user = api.whoami()["name"]
    print(f"Logged in to Hugging Face as {user}.")
    if "/" not in args.space:
        args.space = f"{user}/{args.space}"
    new_space = not api.repo_exists(args.space, repo_type="space")
    api.create_repo(args.space, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
    print(("Created" if new_space else "Updating") + f" https://huggingface.co/spaces/{args.space}")

    logs = f"{args.space}-logs"
    api.create_repo(logs, repo_type="dataset", private=True, exist_ok=True)
    api.add_space_variable(args.space, "LOG_DATASET", logs)
    print(f"Logs are backed up to the private dataset https://huggingface.co/datasets/{logs}")

    if not args.code_only:
        only = {n.strip() for n in args.secrets.split(",")} if args.secrets else None
        unknown = (only or set()) - {name for name, _, _ in SECRETS}
        if unknown:
            sys.exit(f"Unknown secret names: {', '.join(sorted(unknown))}")
        if not only or "HF_TOKEN" in only:
            print(f"\nFor HF_TOKEN, create a fine-grained token at https://huggingface.co/settings/tokens/new?tokenType=fineGrained\n"
                  f"with write access to the dataset {logs} only.\n")
        ask_secrets(api, args.space, new_space, only)

    stage = staged_files()
    try:
        api.upload_folder(repo_id=args.space, repo_type="space", folder_path=stage,
                          delete_patterns="*",  # remove files that are no longer part of the app
                          commit_message=f"Deploy {git_version()}")
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    host = api.space_info(args.space).host or "https://<username>-<space-name>.hf.space"
    print(f"\nUploaded. Hugging Face is building the image now (about 5 minutes).\n"
          f"Build log: https://huggingface.co/spaces/{args.space}?logs=build\n"
          f"App:       {host}")


if __name__ == "__main__":
    main()
