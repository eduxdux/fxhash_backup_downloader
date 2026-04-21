"""
FxHash Explorer — Backend Python/Flask  v3
Proxy GraphQL + Download IPFS + Backup Completo com progresso
"""

import os
import io
import re
import json
import time
import uuid
import zipfile
import mimetypes
import threading
import webbrowser
import traceback
import tempfile
import concurrent.futures

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
FXHASH_GQL   = "https://api.fxhash.xyz/graphql"
IPFS_GW      = "https://gateway.fxhash.xyz/ipfs/"
TIMEOUT_REQ  = 20
TIMEOUT_IPFS = 45

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

HEADERS_GQL = {
    "Content-Type": "application/json",
    "Accept":       "application/json",
    "User-Agent":   "FxHashExplorer/1.0",
    "Origin":       "https://www.fxhash.xyz",
    "Referer":      "https://www.fxhash.xyz/",
}

# In-memory backup tasks registry
backup_tasks = {}

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def resolve_ipfs(uri: str) -> str:
    if not uri:
        return ""
    if uri.startswith("ipfs://"):
        return IPFS_GW + uri[len("ipfs://"):]
    return uri


def sanitize_name(name: str) -> str:
    return re.sub(r'[^\w\-. ]', '_', str(name or "unnamed")).strip()[:80]


def gql_post(query: str, variables: dict = None) -> dict:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = requests.post(
        FXHASH_GQL,
        json=payload,
        headers=HEADERS_GQL,
        timeout=TIMEOUT_REQ,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data and data["errors"]:
        raise ValueError(data["errors"][0].get("message", "GQL error"))
    return data.get("data", {})


def build_objkts_csv(objkts: list) -> str:
    lines = ["iteration,nome,owner_name,owner_addr,minter_name,minter_addr,"
             "preco_mint_tez,ultimo_venda_tez,raridade,criado_em,assinado,hash_geracao"]
    for o in objkts:
        owner  = o.get("owner")  or {}
        minter = o.get("minter") or {}
        mint_p = int(o.get("mintedPrice",  0) or 0) / 1_000_000
        last_p = int(o.get("lastSoldPrice", 0) or 0) / 1_000_000
        rarity = f'{float(o.get("rarity", 0) or 0)*100:.2f}%' if o.get("rarity") is not None else ""
        row = [
            str(o.get("iteration", "")),
            f'"{(o.get("name") or "").replace(chr(34), chr(39))}"',
            f'"{(owner.get("name") or "").replace(chr(34), chr(39))}"',
            owner.get("id", ""),
            f'"{(minter.get("name") or "").replace(chr(34), chr(39))}"',
            minter.get("id", ""),
            f"{mint_p:.6f}" if mint_p else "",
            f"{last_p:.6f}" if last_p else "",
            rarity,
            o.get("createdAt", ""),
            "sim" if o.get("assigned") else "nao",
            o.get("generationHash", ""),
        ]
        lines.append(",".join(row))
    return "\n".join(lines)


def get_image_ext(content_type: str) -> str:
    """Get file extension from content type, fixing common issues."""
    ct = (content_type or "image/png").split(";")[0].strip().lower()
    mapping = {
        "image/jpeg": ".jpg", "image/jpg": ".jpg",
        "image/png": ".png",  "image/gif": ".gif",
        "image/webp": ".webp","image/svg+xml": ".svg",
        "image/avif": ".avif","video/mp4": ".mp4",
        "image/tiff": ".tiff",
    }
    return mapping.get(ct, mimetypes.guess_extension(ct) or ".jpg")


# ─────────────────────────────────────────────
# GQL QUERIES
# ─────────────────────────────────────────────
Q_SEARCH_USER = """
query SearchUser($q: String!) {
  search(filters: { searchQuery_eq: $q }) {
    users {
      id name description flag avatarUri
    }
  }
}
"""

Q_USER_BY_ID = """
query UserById($id: String!) {
  user(id: $id) {
    id name description flag type avatarUri createdAt
    generativeTokens(take: 50, skip: 0) {
      id name slug createdAt supply originalSupply balance
      iterationsCount enabled royalties objktsCount
      thumbnailUri displayUri generativeUri metadataUri
      tags
      author { id name flag }
    }
  }
}
"""

Q_USER_TOKENS_PAGE = """
query UserTokensPage($id: String!, $skip: Int!) {
  user(id: $id) {
    generativeTokens(take: 50, skip: $skip) {
      id name slug createdAt supply originalSupply balance
      iterationsCount enabled royalties objktsCount
      thumbnailUri displayUri generativeUri metadataUri
      tags
      author { id name flag }
    }
  }
}
"""

Q_PROJECT_BY_ID = """
query ProjectById($id: Float!) {
  generativeToken(id: $id) {
    id name slug createdAt supply originalSupply balance
    iterationsCount enabled royalties objktsCount
    thumbnailUri displayUri generativeUri metadataUri
    tags metadata
    pricingFixed { price opensAt }
    pricingDutchAuction { levels restingPrice opensAt }
    author { id name flag }
  }
}
"""

Q_PROJECT_OBJKTS = """
query ProjectObjkts($id: Float!, $skip: Int!) {
  generativeToken(id: $id) {
    objkts(take: 50, skip: $skip) {
      id slug iteration name assigned
      generationHash displayUri thumbnailUri
      createdAt assignedAt mintedPrice lastSoldPrice
      rarity royalties version onChainId
      features tags metadata
      owner  { id name flag }
      minter { id name flag }
      activeListing { price version createdAt }
      captureMedia { width height placeholder mimeType }
      issuer { id name slug thumbnailUri }
    }
  }
}
"""

# ─────────────────────────────────────────────
# FETCH ALL OBJKTS (helper, used by backup)
# ─────────────────────────────────────────────
def fetch_all_objkts(token_id) -> list:
    """Fetch all objkts using parallel GraphQL requests after probing total count."""
    token_id_f = float(token_id)

    # First page — tells us how many items exist so we can fire all pages at once
    first_data = gql_post(Q_PROJECT_OBJKTS, {"id": token_id_f, "skip": 0})
    first_batch = first_data.get("generativeToken", {}).get("objkts", [])
    if not first_batch or len(first_batch) < 50:
        return first_batch  # Fits in one page, nothing else to do

    # Build skip offsets for remaining pages
    skips = list(range(50, 5000, 50))  # up to 5000 objkts max

    results = {0: first_batch}

    def fetch_page(skip):
        data = gql_post(Q_PROJECT_OBJKTS, {"id": token_id_f, "skip": skip})
        return skip, data.get("generativeToken", {}).get("objkts", [])

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(fetch_page, s): s for s in skips}
        for future in concurrent.futures.as_completed(futures):
            skip, batch = future.result()
            results[skip] = batch
            if len(batch) < 50:
                # Cancel remaining futures — we've hit the last page
                for f in futures:
                    f.cancel()
                break

    # Reassemble in order
    all_objkts = []
    for skip in sorted(results):
        batch = results[skip]
        all_objkts.extend(batch)
        if len(batch) < 50:
            break
    return all_objkts


# ─────────────────────────────────────────────
# BACKUP WORKER (background thread)
# ─────────────────────────────────────────────
def run_backup(task_id: str, projects: list, options: dict):
    state = backup_tasks[task_id]

    def upd(msg, pct=None, sub_msg=None):
        state["current"]  = msg
        state["sub_msg"]  = sub_msg or ""
        if pct is not None:
            state["progress"] = pct

    tmp_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False, prefix="fxhash_backup_")
        tmp.close()
        tmp_path = tmp.name

        total_projects = len(projects)
        do_images  = options.get("include_images", False)
        do_source  = options.get("include_source", False)
        do_json    = options.get("include_json",   True)
        do_csv     = options.get("include_csv",    True)

        ALL_GATEWAYS = [
            "https://gateway.fxhash.xyz/ipfs/",
            "https://ipfs.io/ipfs/",
            "https://cloudflare-ipfs.com/ipfs/",
            "https://dweb.link/ipfs/",
        ]

        # ── Pre-fetch ALL projects' objkts in parallel ──────────────────
        # Done once before the main loop so the per-project wait is zero.
        if do_images or do_json or do_csv:
            upd("Buscando objkts de todos os projetos...", 0,
                f"0 / {total_projects} projetos")

            all_objkts_map = {}   # proj_id → list[objkt]
            completed_gql  = [0]
            gql_map_lock   = threading.Lock()

            def _fetch_project_objkts(proj):
                objkts = fetch_all_objkts(proj["id"])
                with gql_map_lock:
                    all_objkts_map[proj["id"]] = objkts
                    completed_gql[0] += 1
                    upd(
                        "Buscando objkts de todos os projetos...",
                        int(completed_gql[0] / total_projects * 10),  # 0–10% for GQL phase
                        f"{completed_gql[0]} / {total_projects} projetos"
                    )

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, total_projects)) as gql_ex:
                list(gql_ex.map(_fetch_project_objkts, projects))

            print(f"[GQL] Todos os {total_projects} projetos buscados.")
        else:
            all_objkts_map = {p["id"]: [] for p in projects}

        # ── Gateway probe (one-time, parallel HEAD on all gateways) ─────
        ordered_gateways = ALL_GATEWAYS[:]
        if do_images:
            probe_uri = None
            for proj in projects:
                for o in all_objkts_map.get(proj["id"], []):
                    probe_uri = o.get("displayUri") or o.get("thumbnailUri")
                    if probe_uri:
                        break
                if probe_uri:
                    break
            if probe_uri:
                probe_cid = probe_uri[7:] if probe_uri.startswith("ipfs://") else probe_uri.split("/ipfs/")[-1]
                def _head(gw):
                    try:
                        t0 = time.time()
                        r = requests.head(f"{gw}{probe_cid}", timeout=5, allow_redirects=True)
                        if r.status_code in (200, 206):
                            return gw, time.time() - t0
                    except Exception:
                        pass
                    return gw, float("inf")
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(ALL_GATEWAYS)) as gw_ex:
                    probe_results = dict(gw_ex.map(lambda g: _head(g), ALL_GATEWAYS))
                ordered_gateways = sorted(ALL_GATEWAYS, key=lambda g: probe_results.get(g, float("inf")))
                print(f"[PROBE] Gateway order: {[g.split('/')[2] for g in ordered_gateways]}")

        # ── Per-thread HTTP session ──────────────────────────────────────
        _thread_local = threading.local()
        def _session():
            if not hasattr(_thread_local, "sess"):
                s = requests.Session()
                s.mount("https://", requests.adapters.HTTPAdapter(
                    pool_connections=4, pool_maxsize=8, max_retries=0))
                s.mount("http://",  requests.adapters.HTTPAdapter(
                    pool_connections=4, pool_maxsize=8, max_retries=0))
                _thread_local.sess = s
            return _thread_local.sess

        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:

            for pi, proj in enumerate(projects):
                if state.get("cancelled"):
                    break

                proj_name    = sanitize_name(proj.get("name", f"projeto_{pi}"))
                base_pct     = 10 + int(pi / total_projects * 90)
                next_pct     = 10 + int((pi + 1) / total_projects * 90)
                objkts_final = all_objkts_map.get(proj["id"], [])

                upd(f"[{pi+1}/{total_projects}] {proj_name}", base_pct)

                # ── JSON ──
                if do_json:
                    zf.writestr(
                        f"{proj_name}/dados_completos.json",
                        json.dumps({"project": proj, "objkts": objkts_final},
                                   indent=2, ensure_ascii=False)
                    )

                # ── CSV ──
                if do_csv and objkts_final:
                    zf.writestr(f"{proj_name}/objkts.csv", build_objkts_csv(objkts_final))

                # ── IMAGES ──
                if do_images and objkts_final:
                    n_obj      = len(objkts_final)
                    ok_count   = 0
                    fail_count = 0
                    completed  = 0
                    zip_lock   = threading.Lock()

                    def download_image(oi, o):
                        if state.get("cancelled") or state.get("force_finish"):
                            return None
                        iter_n   = o.get("iteration", oi)
                        obj_name = sanitize_name(o.get("name") or f"objkt_{iter_n}")
                        uri      = o.get("displayUri") or o.get("thumbnailUri")
                        if not uri:
                            return None
                        cid  = uri[7:] if uri.startswith("ipfs://") else uri.split("/ipfs/")[-1]
                        sess = _session()
                        for gw in ordered_gateways:
                            try:
                                r = sess.get(f"{gw}{cid}", timeout=12)
                                if r.status_code == 200:
                                    ext   = get_image_ext(r.headers.get("content-type", ""))
                                    fname = f"{proj_name}/imagens/{iter_n:04d}_{obj_name}{ext}"
                                    return (fname, r.content, iter_n, obj_name, None)
                            except Exception:
                                continue
                        return (None, None, iter_n, obj_name, "Todas as tentativas falharam")

                    t_inicio = time.time()
                    N_WORKERS = min(60, n_obj)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=N_WORKERS) as executor:
                        futures = [executor.submit(download_image, oi, o)
                                   for oi, o in enumerate(objkts_final)]
                        for future in concurrent.futures.as_completed(futures):
                            if state.get("cancelled") or state.get("force_finish"):
                                executor.shutdown(wait=False, cancel_futures=True)
                                break
                            res = future.result()
                            completed += 1
                            pct = base_pct + int((completed / n_obj) * (next_pct - base_pct))
                            with zip_lock:
                                upd(f"[{pi+1}/{total_projects}] {proj_name}", pct,
                                    f"Imagens: {completed}/{n_obj}")
                                if res:
                                    fname, content, iter_n, obj_name, err = res
                                    if content:
                                        zf.writestr(fname, content, compress_type=zipfile.ZIP_STORED)
                                        ok_count += 1
                                        state["total_images_done"] = state.get("total_images_done", 0) + 1
                                    elif err:
                                        fail_count += 1
                                        state["log"].append(f"IMG ERRO {proj_name} #{iter_n}: {err}")

                    print(f"--- {proj_name}: {ok_count}/{n_obj} ok | {time.time()-t_inicio:.1f}s ---")
                    state["log"].append(f"{proj_name}: {ok_count}/{n_obj} imagens OK, {fail_count} falhas")

                # ── SOURCE CODE ──
                if do_source and proj.get("generativeUri"):
                    upd(f"[{pi+1}/{total_projects}] {proj_name}",
                        next_pct - 1, "Clonando codigo-fonte IPFS...")
                    base_url = resolve_ipfs(proj["generativeUri"])
                    if not base_url.endswith("/"):
                        base_url += "/"
                    try:
                        resp = requests.get(base_url, timeout=TIMEOUT_IPFS)
                        if resp.status_code == 200:
                            zf.writestr(f"{proj_name}/source/index.html", resp.text)
                            soup = BeautifulSoup(resp.text, "html.parser")
                            refs = set()
                            for tag in soup.find_all(["script", "link", "img"]):
                                src = tag.get("src") or tag.get("href")
                                if src and not src.startswith(("http", "data:", "//", "#")):
                                    refs.add(src)
                            IPFS_GATEWAYS_SRC = [
                                "https://gateway.fxhash.xyz/ipfs/",
                                "https://ipfs.io/ipfs/",
                                "https://cloudflare-ipfs.com/ipfs/"
                            ]

                            def download_ref(ref):
                                for gw in IPFS_GATEWAYS_SRC:
                                    try:
                                        test_url = urljoin(gw + base_url.split("/ipfs/")[-1] if "/ipfs/" in base_url else base_url, ref)
                                        r2 = requests.get(test_url, timeout=15)
                                        if r2.status_code == 200:
                                            return ref, r2.content
                                    except Exception:
                                        pass
                                return ref, None

                            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ref_executor:
                                ref_futures = [ref_executor.submit(download_ref, ref) for ref in refs]
                                for f in concurrent.futures.as_completed(ref_futures):
                                    if state.get("cancelled") or state.get("force_finish"):
                                        break
                                    ref, content = f.result()
                                    if content:
                                        with zip_lock:
                                            zf.writestr(f"{proj_name}/source/{ref}", content, compress_type=zipfile.ZIP_STORED)
                    except Exception as e:
                        state["log"].append(f"SOURCE ERRO {proj_name}: {e}")

                upd(f"[{pi+1}/{total_projects}] {proj_name} concluido", next_pct)

            # Write log file
            if state["log"]:
                zf.writestr("_log_backup.txt", "\n".join(state["log"]))

        # Done!
        state["zip_path"] = tmp_path
        state["status"]   = "done"
        state["progress"] = 100
        state["done"]     = True
        state["current"]  = "Backup concluido!"
        state["sub_msg"]  = f"Total de imagens: {state.get('total_images_done', 0)}"

    except Exception as e:
        traceback.print_exc()
        state["status"]  = "error"
        state["error"]   = str(e)
        state["done"]    = True
        state["current"] = f"Erro: {e}"
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# ─────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────

@app.route("/api/search", methods=["GET"])
def api_search():
    """Search user by wallet address OR username."""
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Missing query param 'q'"}), 400

    try:
        if query.startswith("tz") or query.startswith("KT"):
            user_id = query
        else:
            data = gql_post(Q_SEARCH_USER, {"q": query})
            users = data.get("search", {}).get("users", [])
            if not users:
                return jsonify({"error": "Nenhum usuario encontrado"}), 404
            match = next(
                (u for u in users if (u.get("name") or "").lower() == query.lower()),
                users[0]
            )
            user_id = match["id"]

        data2 = gql_post(Q_USER_BY_ID, {"id": user_id})
        user = data2.get("user")
        if not user:
            return jsonify({"error": "Usuario nao encontrado"}), 404

        all_tokens = list(user.get("generativeTokens", []))
        skip = 50
        while True:
            page_data = gql_post(Q_USER_TOKENS_PAGE, {"id": user_id, "skip": skip})
            batch = page_data.get("user", {}).get("generativeTokens", [])
            if not batch:
                break
            all_tokens.extend(batch)
            if len(batch) < 50:
                break
            skip += 50

        user["generativeTokens"] = all_tokens
        return jsonify({"user": user, "projects": all_tokens})

    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except requests.HTTPError as e:
        return jsonify({"error": f"HTTP {e.response.status_code}"}), 502
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/project", methods=["GET"])
def api_project():
    token_id = request.args.get("id")
    if not token_id:
        return jsonify({"error": "Missing 'id'"}), 400
    try:
        data = gql_post(Q_PROJECT_BY_ID, {"id": float(token_id)})
        proj = data.get("generativeToken")
        if not proj:
            return jsonify({"error": "Projeto nao encontrado"}), 404
        return jsonify({"project": proj})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/objkts", methods=["GET"])
def api_objkts():
    token_id = request.args.get("id")
    skip     = int(request.args.get("skip", 0))
    if not token_id:
        return jsonify({"error": "Missing 'id'"}), 400
    try:
        data = gql_post(Q_PROJECT_OBJKTS, {"id": float(token_id), "skip": skip})
        objkts = data.get("generativeToken", {}).get("objkts", [])
        return jsonify({"objkts": objkts, "skip": skip, "count": len(objkts)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/ipfs-proxy", methods=["GET"])
def api_ipfs_proxy():
    uri = request.args.get("uri", "")
    url = resolve_ipfs(uri)
    if not url:
        return jsonify({"error": "Bad uri"}), 400
    try:
        r = requests.get(url, timeout=TIMEOUT_IPFS, stream=True)
        ct = r.headers.get("Content-Type", "application/octet-stream")
        return Response(
            stream_with_context(r.iter_content(chunk_size=8192)),
            content_type=ct, status=r.status_code,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# ─────────────────────────────────────────────
# DOWNLOAD ROUTES
# ─────────────────────────────────────────────

@app.route("/api/download/image", methods=["GET"])
def api_download_image():
    uri      = request.args.get("uri", "")
    filename = request.args.get("filename", "image")
    url      = resolve_ipfs(uri)
    if not url:
        return jsonify({"error": "Bad uri"}), 400
    try:
        r  = requests.get(url, timeout=TIMEOUT_IPFS)
        r.raise_for_status()
        ct  = r.headers.get("Content-Type", "image/png")
        ext = get_image_ext(ct)
        return Response(
            r.content, mimetype=ct,
            headers={"Content-Disposition": f'attachment; filename="{sanitize_name(filename)}{ext}"'},
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/download/source", methods=["POST"])
def api_download_source():
    body      = request.get_json(force=True)
    gen_uri   = body.get("generativeUri", "")
    proj_name = sanitize_name(body.get("name", "projeto"))
    if not gen_uri:
        return jsonify({"error": "generativeUri ausente"}), 400

    base_url = resolve_ipfs(gen_uri)
    if not base_url.endswith("/"):
        base_url += "/"

    zip_buffer = io.BytesIO()
    try:
        resp = requests.get(base_url, timeout=TIMEOUT_IPFS)
        resp.raise_for_status()
    except Exception as e:
        return jsonify({"error": f"IPFS error: {e}"}), 502

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{proj_name}/index.html", resp.text)
        soup = BeautifulSoup(resp.text, "html.parser")
        refs = set()
        for tag in soup.find_all(["script", "link", "img"]):
            src = tag.get("src") or tag.get("href")
            if src and not src.startswith(("http", "data:", "//", "#")):
                refs.add(src)
        for ref in refs:
            try:
                r2 = requests.get(urljoin(base_url, ref), timeout=30)
                if r2.status_code == 200:
                    zf.writestr(f"{proj_name}/{ref}", r2.content)
            except Exception:
                pass

    zip_buffer.seek(0)
    return Response(
        zip_buffer.getvalue(), mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{proj_name}_source.zip"'},
    )


@app.route("/api/download/objkts-csv", methods=["POST"])
def api_download_objkts_csv():
    body   = request.get_json(force=True)
    objkts = body.get("objkts", [])
    name   = sanitize_name(body.get("name", "projeto"))
    return Response(
        build_objkts_csv(objkts),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}_objkts.csv"'},
    )


@app.route("/api/download/projects-json", methods=["POST"])
def api_download_projects_json():
    body     = request.get_json(force=True)
    projects = body.get("projects", [])
    name_tag = sanitize_name(body.get("tag", "selecao"))
    return Response(
        json.dumps(projects, indent=2, ensure_ascii=False),
        mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="fxhash_{name_tag}.json"'},
    )


# ─────────────────────────────────────────────
# BACKUP ROUTES (background + SSE progress)
# ─────────────────────────────────────────────

@app.route("/api/backup/start", methods=["POST"])
def api_backup_start():
    """
    Start a backup task in background.
    Body JSON:
    {
      projects: [{id, name, generativeUri, ...}],
      options: {
        include_images: bool,
        include_source: bool,
        include_json:   bool,
        include_csv:    bool,
      }
    }
    Returns: { task_id: "..." }
    """
    body     = request.get_json(force=True)
    projects = body.get("projects", [])
    options  = body.get("options", {})

    if not projects:
        return jsonify({"error": "Nenhum projeto selecionado"}), 400

    task_id = str(uuid.uuid4())
    backup_tasks[task_id] = {
        "status":             "running",
        "progress":           0,
        "current":            "Iniciando...",
        "sub_msg":            "",
        "done":               False,
        "cancelled":          False,
        "force_finish":       False,
        "error":              None,
        "zip_path":           None,
        "log":                [],
        "total_images_done":  0,
        "total_objkts_done":  0,
        "n_projects":         len(projects),
    }

    t = threading.Thread(
        target=run_backup,
        args=(task_id, projects, options),
        daemon=True
    )
    t.start()
    return jsonify({"task_id": task_id})


@app.route("/api/backup/progress/<task_id>", methods=["GET"])
def api_backup_progress(task_id):
    """SSE stream with progress updates every 0.5s."""
    def generate():
        while True:
            state = backup_tasks.get(task_id)
            if not state:
                yield f"data: {json.dumps({'error': 'task not found', 'done': True})}\n\n"
                break
            payload = {
                "progress":   state.get("progress", 0),
                "current":    state.get("current", ""),
                "sub_msg":    state.get("sub_msg", ""),
                "done":       state.get("done", False),
                "error":      state.get("error"),
                "status":     state.get("status", "running"),
                "n_images":   state.get("total_images_done", 0),
                "n_projects": state.get("n_projects", 0),
            }
            yield f"data: {json.dumps(payload)}\n\n"
            if state.get("done"):
                break
            time.sleep(0.5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering":"no",
            "Connection":       "keep-alive",
        }
    )


@app.route("/api/backup/cancel/<task_id>", methods=["POST"])
def api_backup_cancel(task_id):
    if task_id in backup_tasks:
        state = backup_tasks[task_id]
        state["cancelled"] = True
        state["current"]   = "Cancelando..."
        return jsonify({"ok": True}), 200
    return jsonify({"error": "Task not found"}), 404

@app.route("/api/backup/force_finish/<task_id>", methods=["POST"])
def api_backup_force_finish(task_id):
    if task_id in backup_tasks:
        state = backup_tasks[task_id]
        state["force_finish"] = True
        state["current"] = "Gerando arquivo ZIP Incompleto..."
        return jsonify({"ok": True}), 200
    return jsonify({"error": "Task not found"}), 404


@app.route("/api/backup/download/<task_id>", methods=["GET"])
def api_backup_download(task_id):
    """Download the completed backup ZIP."""
    state = backup_tasks.get(task_id)
    if not state or not state.get("done") or state.get("error"):
        return jsonify({"error": "Backup nao disponivel ou com erro"}), 404

    zip_path = state.get("zip_path")
    if not zip_path or not os.path.exists(zip_path):
        return jsonify({"error": "Arquivo ZIP nao encontrado"}), 404

    def generate_file():
        try:
            with open(zip_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.unlink(zip_path)
            except Exception:
                pass
            backup_tasks.pop(task_id, None)

    return Response(
        stream_with_context(generate_file()),
        mimetype="application/zip",
        headers={"Content-Disposition": 'attachment; filename="fxhash_backup_completo.zip"'},
    )


@app.route("/api/backup/status/<task_id>", methods=["GET"])
def api_backup_status(task_id):
    state = backup_tasks.get(task_id)
    if not state:
        return jsonify({"error": "task not found"}), 404
    return jsonify({k: v for k, v in state.items() if k not in ("zip_path", "log")})


# ─────────────────────────────────────────────
# SERVE FRONTEND
# ─────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(".", filename)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
def open_browser():
    time.sleep(1.2)
    webbrowser.open("http://localhost:5050")


if __name__ == "__main__":
    print("\n" + "="*55)
    print("  [*] FxHash Explorer -- Backend Flask v3")
    print("  [>] http://localhost:5050")
    print("="*55 + "\n")
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)