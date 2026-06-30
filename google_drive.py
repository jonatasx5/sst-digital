"""
Integração com Google Drive para armazenar modelos de documentos.
Usa Service Account — configure a variável GOOGLE_CREDENTIALS_JSON no Railway.
"""

import os
import io
import json
import logging

log = logging.getLogger(__name__)

# Nome da pasta raiz no Drive onde os modelos serão salvos
DRIVE_FOLDER_NAME = "SST-Digital-Modelos"

_service = None  # cache da instância autenticada


def _get_service():
    global _service
    if _service is not None:
        return _service

    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        return None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        info = json.loads(creds_json)
        scopes = ["https://www.googleapis.com/auth/drive"]
        creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        _service = build("drive", "v3", credentials=creds, cache_discovery=False)
        log.info("[Drive] Serviço autenticado com sucesso")
        return _service
    except Exception as e:
        log.error(f"[Drive] Falha ao autenticar: {e}")
        return None


def _get_or_create_folder(service, folder_name: str, parent_id: str = None) -> str:
    """Retorna o ID de uma pasta, criando-a se não existir."""
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    folder = service.files().create(body=meta, fields="id").execute()
    log.info(f"[Drive] Pasta '{folder_name}' criada: {folder['id']}")
    return folder["id"]


def _file_name(doc_id: str, cargo: str = None) -> str:
    if cargo:
        safe = cargo.replace("/", "_").replace(" ", "_")
        return f"{doc_id}__{safe}.docx"
    return f"{doc_id}.docx"


def upload_modelo(doc_id: str, nome: str, conteudo: bytes, cargo: str = None) -> bool:
    """
    Faz upload de um modelo para o Google Drive.
    Substitui arquivo existente com o mesmo nome.
    Retorna True em caso de sucesso.
    """
    service = _get_service()
    if not service:
        return False

    try:
        from googleapiclient.http import MediaIoBaseUpload

        folder_id = _get_or_create_folder(service, DRIVE_FOLDER_NAME)
        file_name = _file_name(doc_id, cargo)

        # Verifica se já existe para fazer update em vez de create
        query = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
        existing = service.files().list(q=query, fields="files(id)").execute().get("files", [])

        media = MediaIoBaseUpload(io.BytesIO(conteudo),
                                  mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                  resumable=False)

        if existing:
            file_id = existing[0]["id"]
            service.files().update(fileId=file_id, media_body=media).execute()
            log.info(f"[Drive] Atualizado: {file_name} ({len(conteudo)} bytes)")
        else:
            meta = {"name": file_name, "parents": [folder_id]}
            service.files().create(body=meta, media_body=media, fields="id").execute()
            log.info(f"[Drive] Criado: {file_name} ({len(conteudo)} bytes)")

        return True
    except Exception as e:
        import traceback
        log.error(f"[Drive] Erro ao fazer upload de {doc_id}: {e}\n{traceback.format_exc()}")
        raise  # propaga para o chamador ver o erro real


def download_modelo(doc_id: str, cargo: str = None) -> bytes | None:
    """
    Baixa um modelo do Google Drive.
    Retorna bytes ou None se não encontrado.
    """
    service = _get_service()
    if not service:
        return None

    try:
        from googleapiclient.http import MediaIoBaseDownload

        folder_id = _get_or_create_folder(service, DRIVE_FOLDER_NAME)
        file_name = _file_name(doc_id, cargo)

        query = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, fields="files(id)").execute().get("files", [])

        if not results:
            return None

        file_id = results[0]["id"]
        request = service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        data = buf.getvalue()
        log.info(f"[Drive] Download OK: {file_name} ({len(data)} bytes)")
        return data
    except Exception as e:
        log.error(f"[Drive] Erro ao baixar {doc_id}: {e}")
        return None


def delete_modelo(doc_id: str, cargo: str = None) -> bool:
    """Remove um modelo do Google Drive. Retorna True se deletou."""
    service = _get_service()
    if not service:
        return False

    try:
        folder_id = _get_or_create_folder(service, DRIVE_FOLDER_NAME)
        file_name = _file_name(doc_id, cargo)

        query = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, fields="files(id)").execute().get("files", [])

        for f in results:
            service.files().delete(fileId=f["id"]).execute()
            log.info(f"[Drive] Deletado: {file_name}")
        return True
    except Exception as e:
        log.error(f"[Drive] Erro ao deletar {doc_id}: {e}")
        return False


def listar_modelos_drive() -> list:
    """Lista todos os modelos no Drive. Retorna lista de dicts com id, nome, cargo."""
    service = _get_service()
    if not service:
        return []

    try:
        folder_id = _get_or_create_folder(service, DRIVE_FOLDER_NAME)
        query = f"'{folder_id}' in parents and trashed=false and mimeType!='application/vnd.google-apps.folder'"
        results = service.files().list(q=query, fields="files(id, name, size)", pageSize=200).execute()
        items = []
        for f in results.get("files", []):
            name = f["name"]  # ex: 03_os__AJUDANTE.docx ou 01_treinamento_admissional.docx
            if name.endswith(".docx"):
                base = name[:-5]  # remove .docx
                if "__" in base:
                    doc_id, cargo = base.split("__", 1)
                    cargo = cargo.replace("_", " ")
                else:
                    doc_id = base
                    cargo = None
                items.append({"doc_id": doc_id, "cargo": cargo, "file_id": f["id"], "size": f.get("size", 0)})
        return items
    except Exception as e:
        log.error(f"[Drive] Erro ao listar: {e}")
        return []


def drive_disponivel() -> bool:
    """Retorna True se o Drive está configurado e autenticado."""
    return _get_service() is not None
