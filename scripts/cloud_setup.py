"""Provision and verify narration without displaying credentials.

Run from the checkout after installing the project. Default behavior only
validates provider access; cloud changes and paid samples require explicit flags.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from booktr.audio.providers import ElevenLabsProvider, GoogleProvider
from booktr.audio.service import _make_m4b, _wav_info, _write_wav, find_ffmpeg
from booktr.config import config_path, load_config
from booktr.errors import BookTrError


def _json_response(response):
    if not response.ok:
        raise BookTrError("config", f"Google setup returned HTTP {response.status_code}; check project billing and permissions")
    return response.json()


def _wait_operation(session, base_url: str, operation: dict) -> dict:
    deadline = time.monotonic() + 180
    while not operation.get("done"):
        if time.monotonic() >= deadline:
            raise BookTrError("config", "Google setup operation is still pending; retry the command")
        time.sleep(1)
        operation = _json_response(session.get(f"{base_url}/{operation['name']}", timeout=30))
    if operation.get("error"):
        # Response bodies may contain account data, so show only the error code.
        raise BookTrError("config", f"Google setup operation failed (code {operation['error'].get('code', 'unknown')})")
    return operation.get("response", {})


def _save_google_key(path: Path, key: str, project: str):
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.update(google_api_key=key, google_cloud_project=project,
                google_voice="cs-CZ-Chirp3-HD-Gacrux")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".booktr-config-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        if sys.platform != "win32":
            temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def provision_google(config, *, enable: bool, create_key: bool):
    """Use an administrator's ADC; never grant broad roles to the desktop app."""
    if not config.google_cloud_project:
        raise BookTrError("config", "Set google_cloud_project or pass --project")
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    if config.google_credentials_file:
        from google.oauth2.service_account import Credentials
        credentials = Credentials.from_service_account_file(config.google_credentials_file, scopes=scopes)
    else:
        try:
            credentials, _ = google.auth.default(scopes=scopes)
        except google.auth.exceptions.DefaultCredentialsError:
            raise BookTrError("config", "Administrator ADC missing; run gcloud auth application-default login first") from None
    with AuthorizedSession(credentials) as session:
        project = _json_response(session.get(
            f"https://cloudresourcemanager.googleapis.com/v3/projects/{config.google_cloud_project}", timeout=30))
        project_name = project["name"]  # APIs require the resolved project number.
        services = ["texttospeech.googleapis.com"]
        if create_key:
            services.append("apikeys.googleapis.com")
        if enable or create_key:
            for service in services:
                endpoint = f"https://serviceusage.googleapis.com/v1/{project_name}/services/{service}"
                current = _json_response(session.get(endpoint, timeout=30))
                if current.get("state") != "ENABLED":
                    operation = _json_response(session.post(endpoint + ":enable", json={}, timeout=30))
                    _wait_operation(session, "https://serviceusage.googleapis.com/v1", operation)
                print(f"Google API enabled: {service}")
        if create_key:
            collection = f"https://apikeys.googleapis.com/v2/{project_name}/locations/global/keys"
            # Fixed resource ID makes interrupted setup retryable without creating
            # a new paid-project key every time the script is run.
            existing = session.get(collection + "/booktr-narrator", timeout=30)
            if existing.status_code == 404:
                operation = _json_response(session.post(collection, params={"keyId": "booktr-narrator"}, json={
                    "displayName": "BookTr Czech narration",
                    "restrictions": {"apiTargets": [{"service": "texttospeech.googleapis.com"}]},
                }, timeout=30))
                resource = _wait_operation(session, "https://apikeys.googleapis.com/v2", operation)
            else:
                resource = _json_response(existing)
            targets = resource.get("restrictions", {}).get("apiTargets", [])
            if targets != [{"service": "texttospeech.googleapis.com"}]:
                raise BookTrError("config", "Existing BookTr key is not restricted to Text-to-Speech; inspect it in Cloud Console")
            key = _json_response(session.get(
                f"https://apikeys.googleapis.com/v2/{resource['name']}/keyString", timeout=30))["keyString"]
            _save_google_key(config_path(), key, config.google_cloud_project)
            config.google_api_key = key
            print(f"Restricted Google key saved privately to {config_path()}")


def _export_sample(provider, name: str, directory: Path, *, verify_encoding: bool = False) -> dict:
    """One short paid request; all encoding checks run locally afterward."""
    destination = directory / f"{name}-czech-sample.wav"
    audio = provider.synthesize(
        "Dobrý den. Vaše audiokniha je připravena. Přejeme vám příjemný poslech.")
    _write_wav(destination, audio)
    pcm_format, frames = _wav_info(destination)
    if pcm_format != (1, 2, 24000):
        raise BookTrError("unknown", "Sample has an unexpected PCM format")
    result = {"wav": str(destination), "duration_seconds": round(frames / 24000, 3),
              "pcm": "24 kHz, 16-bit mono"}
    if verify_encoding:
        ffmpeg = find_ffmpeg()
        if ffmpeg is None:
            raise BookTrError("config", "FFmpeg is required to verify M4B encoding")
        manifest = {"title": "Ukázka českého hlasu", "chapters": [{
            "title": "Český hlas", "start_ms": 0, "end_ms": round(frames * 1000 / 24000),
        }]}
        output = _make_m4b(destination, manifest, ffmpeg)
        if output is None:
            raise BookTrError("unknown", "Sample M4B encoding failed; the WAV sample remains available")
        decoded = subprocess.run([str(ffmpeg), "-v", "error", "-i", str(output), "-c:a", "pcm_s16le",
                                  "-f", "s16le", "-"], capture_output=True, timeout=60,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if decoded.returncode or not decoded.stdout or len(decoded.stdout) % 2:
            raise BookTrError("unknown", "Sample M4B failed the decode check")
        result.update(m4b=str(output), decoded_frames=len(decoded.stdout) // 2)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Set up and verify BookTr Czech audiobook providers")
    parser.add_argument("--config", type=Path, help="Private config.json to read/update")
    parser.add_argument("--project", help="Existing Google Cloud project ID with billing enabled")
    parser.add_argument("--provider", choices=("google", "elevenlabs", "both"), default="both")
    parser.add_argument("--enable-google-api", action="store_true", help="Enable Text-to-Speech using administrator ADC")
    parser.add_argument("--create-google-key", action="store_true", help="Create/reuse restricted BookTr key and save it privately")
    parser.add_argument("--sample-dir", type=Path, help="Synthesize a short paid Czech sample for each selected provider")
    parser.add_argument("--verify-encoding", action="store_true", help="Verify sample PCM and encode/decode M4B locally")
    args = parser.parse_args(argv)
    if args.verify_encoding and args.sample_dir is None:
        parser.error("--verify-encoding requires --sample-dir")
    if args.config:
        os.environ["BOOKTR_CONFIG_FILE"] = str(args.config.expanduser().resolve())
    try:
        config = load_config(validate=False)
        if args.project:
            config.google_cloud_project = args.project
        if args.enable_google_api or args.create_google_key:
            provision_google(config, enable=args.enable_google_api, create_key=args.create_google_key)
        failed = False
        for name, factory in (("google", GoogleProvider), ("elevenlabs", ElevenLabsProvider)):
            if args.provider not in (name, "both"):
                continue
            provider = factory(config)
            try:
                provider.validate()
                print(f"{name}: Czech narrator and model available")
                if args.sample_dir:
                    sample = _export_sample(provider, name, args.sample_dir, verify_encoding=args.verify_encoding)
                    print(f"{name}: {sample['pcm']}, {sample['duration_seconds']} s; sample saved to {sample['wav']}")
                    if "m4b" in sample:
                        print(f"{name}: M4B decode verified; {sample['m4b']}")
            except BookTrError as error:
                print(f"{name}: {error.detail}", file=sys.stderr)
                failed = True
            finally:
                provider.close()
        return 1 if failed else 0
    except BookTrError as error:
        print(error.detail, file=sys.stderr)
        return 1
    except Exception as error:
        # Avoid exception strings from HTTP libraries: they can contain tokens.
        print(f"Setup failed ({type(error).__name__}); check credentials and project permissions", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
