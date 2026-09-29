"""Push the catalog file back to the GitHub repository.

Streamlit Community Cloud has an ephemeral filesystem, so saving locally would
not survive a reboot and could even break future `git pull`s on the managed
checkout. Committing through the Contents API keeps the repo as the single
source of truth: the push triggers a redeploy and the new catalog loads at
startup.
"""
from __future__ import annotations

import base64

import requests

API = "https://api.github.com"


def commit_catalog(
    content: bytes,
    repo: str,
    token: str,
    path: str = "static/catalogo.xlsx",
    branch: str = "main",
) -> str:
    """Create a commit updating `path` in `repo`; return the new commit sha."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    file_url = f"{API}/repos/{repo}/contents/{path}"
    try:
        current = requests.get(file_url, headers=headers, params={"ref": branch}, timeout=15)
    except requests.RequestException as error:
        raise ValueError("Não foi possível contactar o GitHub.") from error
    if current.status_code == 404:
        sha = None
    else:
        _raise_for_status(current)
        sha = current.json().get("sha")
    body = {
        "message": "📄 Update catalogo.xlsx from the app",
        "content": base64.b64encode(content).decode("ascii"),
        "branch": branch,
    }
    if sha is not None:
        body["sha"] = sha
    try:
        response = requests.put(file_url, headers=headers, json=body, timeout=15)
    except requests.RequestException as error:
        raise ValueError("Não foi possível contactar o GitHub.") from error
    _raise_for_status(response)
    return response.json()["commit"]["sha"]


def _raise_for_status(response: requests.Response) -> None:
    if response.status_code < 400:
        return
    if response.status_code in (401, 403):
        raise ValueError(
            "❌ O token do GitHub foi recusado — precisa da permissão "
            "'Contents: read and write' neste repositório."
        )
    if response.status_code == 404:
        raise ValueError("❌ Repositório ou ficheiro não encontrado — confirma GITHUB_REPO.")
    if response.status_code == 409:
        raise ValueError("❌ O ficheiro mudou entretanto no GitHub — tenta gravar outra vez.")
    raise ValueError(f"❌ Erro do GitHub ({response.status_code}).")
