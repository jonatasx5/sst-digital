"""
SST Digital - Sistema Web
Backend FastAPI para geração e envio de kits SST via Autentique
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uvicorn
import os
import json
import shutil
import tempfile
from datetime import datetime, timedelta

import hashlib
import secrets as _secrets
import jwt as _jwt

import banco
import processador
import zapsign
from config import DOCUMENTOS, APP_PASSWORD, EMPRESA

app = FastAPI(title="SST Digital")

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
JWT_SECRET  = os.environ.get("JWT_SECRET", "sst-digital-secret-change-in-prod")
JWT_ALGO    = "HS256"
JWT_EXPIRY  = 8  # horas

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

bearer_sec = HTTPBearer(auto_error=False)


def _hash_senha(senha: str) -> str:
    salt = _secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 260000)
    return f"pbkdf2$sha256${salt}${h.hex()}"


def _verificar_senha(senha: str, hashed: str) -> bool:
    try:
        _, algo, salt, stored = hashed.split("$")
        h = hashlib.pbkdf2_hmac(algo, senha.encode(), salt.encode(), 260000)
        return _secrets.compare_digest(h.hex(), stored)
    except Exception:
        return False


def _criar_token(uid: int, perfil: str, permissoes: list) -> str:
    exp = datetime.utcnow() + timedelta(hours=JWT_EXPIRY)
    return _jwt.encode({"sub": str(uid), "perfil": perfil,
                        "permissoes": permissoes, "exp": exp}, JWT_SECRET, algorithm=JWT_ALGO)


def verificar_acesso(creds: HTTPAuthorizationCredentials = Depends(bearer_sec)):
    """Valida JWT. Retorna payload do token."""
    if creds is None:
        raise HTTPException(401, "Token não fornecido")
    try:
        payload = _jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGO])
        return payload
    except Exception:
        raise HTTPException(401, "Token inválido ou expirado")


def exigir_admin(payload=Depends(verificar_acesso)):
    if payload.get("perfil") != "admin":
        raise HTTPException(403, "Acesso restrito a administradores")
    return payload


def _garantir_admin_inicial():
    """Cria o usuário admin padrão se não existir nenhum admin."""
    try:
        total = banco.contar_admins()
        print(f"DEBUG admins no banco: {total}")
        if total == 0:
            login_padrao = os.environ.get("ADMIN_LOGIN", "admin")
            senha_padrao = os.environ.get("ADMIN_SENHA", "admin123")
            uid = banco.criar_usuario(
                nome="Administrador",
                login=login_padrao,
                senha_hash=_hash_senha(senha_padrao),
                perfil="admin",
                permissoes=["*"]
            )
            print(f"Admin inicial criado: login={login_padrao} id={uid}")
    except Exception as e:
        print(f"ERRO ao criar admin inicial: {e}")


# Cria banco na inicialização
banco.criar_banco()
_garantir_admin_inicial()

# ══════════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════════

@app.post("/api/auth/login")
async def login(dados: dict):
    login_str = dados.get("login", "").strip()
    senha     = dados.get("senha", "")
    usuario   = banco.buscar_usuario_por_login(login_str)
    if not usuario or not _verificar_senha(senha, usuario["senha_hash"]):
        raise HTTPException(401, "Usuário ou senha incorretos")
    import json as _json
    perms = usuario.get("permissoes", "[]")
    if isinstance(perms, str):
        perms = _json.loads(perms)
    token = _criar_token(usuario["id"], usuario["perfil"], perms)
    return {
        "token":      token,
        "id":         usuario["id"],
        "nome":       usuario["nome"],
        "perfil":     usuario["perfil"],
        "permissoes": perms,
    }


@app.get("/api/auth/me")
async def me(payload=Depends(verificar_acesso)):
    return payload


@app.post("/api/auth/reset-admin")
async def reset_admin(dados: dict):
    """Reseta a senha do admin. Só funciona se RESET_SECRET bater."""
    secret = os.environ.get("RESET_SECRET", "")
    if not secret or dados.get("secret") != secret:
        raise HTTPException(403, "Não autorizado")
    nova_senha = dados.get("senha", "admin123")
    usuarios = banco.listar_usuarios()
    admin = next((u for u in usuarios if u["perfil"] == "admin"), None)
    if not admin:
        uid = banco.criar_usuario("Administrador", "admin", _hash_senha(nova_senha), "admin", ["*"])
        return {"ok": True, "acao": "criado", "id": uid}
    banco.atualizar_usuario(admin["id"], {**admin, "senha_hash": _hash_senha(nova_senha),
                                          "permissoes": ["*"]})
    return {"ok": True, "acao": "senha_atualizada", "login": admin["login"]}




# ══════════════════════════════════════════════════════════
#  USUÁRIOS (admin)
# ══════════════════════════════════════════════════════════

@app.get("/api/usuarios")
async def listar_usuarios(_=Depends(exigir_admin)):
    import json as _json
    rows = banco.listar_usuarios()
    for r in rows:
        if isinstance(r.get("permissoes"), str):
            r["permissoes"] = _json.loads(r["permissoes"])
    return rows


@app.post("/api/usuarios")
async def criar_usuario(dados: dict, _=Depends(exigir_admin)):
    if not dados.get("login") or not dados.get("senha"):
        raise HTTPException(400, "login e senha são obrigatórios")
    if banco.buscar_usuario_por_login(dados["login"]):
        raise HTTPException(400, "Login já existe")
    uid = banco.criar_usuario(
        nome=dados.get("nome", dados["login"]),
        login=dados["login"],
        senha_hash=_hash_senha(dados["senha"]),
        perfil=dados.get("perfil", "usuario"),
        permissoes=dados.get("permissoes", [])
    )
    return {"ok": True, "id": uid}


@app.put("/api/usuarios/{uid}")
async def atualizar_usuario(uid: int, dados: dict, _=Depends(exigir_admin)):
    usuario = banco.buscar_usuario_por_id(uid)
    if not usuario:
        raise HTTPException(404, "Usuário não encontrado")
    update = {
        "nome":       dados.get("nome", usuario["nome"]),
        "login":      dados.get("login", usuario["login"]),
        "perfil":     dados.get("perfil", usuario["perfil"]),
        "permissoes": dados.get("permissoes", []),
        "ativo":      dados.get("ativo", usuario["ativo"]),
    }
    if dados.get("senha"):
        update["senha_hash"] = _hash_senha(dados["senha"])
    banco.atualizar_usuario(uid, update)
    return {"ok": True}


@app.delete("/api/usuarios/{uid}")
async def deletar_usuario(uid: int, payload=Depends(exigir_admin)):
    if uid == int(payload["sub"]):
        raise HTTPException(400, "Não é possível excluir seu próprio usuário")
    banco.deletar_usuario(uid)
    return {"ok": True}


# ══════════════════════════════════════════════════════════
#  ENGENHEIROS
# ══════════════════════════════════════════════════════════

@app.get("/api/engenheiros")
async def listar_engenheiros(_=Depends(verificar_acesso)):
    return banco.listar_engenheiros()

@app.post("/api/engenheiros")
async def criar_engenheiro(dados: dict, _=Depends(verificar_acesso)):
    eid = banco.salvar_engenheiro(None, dados.get("nome",""), dados.get("crea",""))
    return {"ok": True, "id": eid}

@app.put("/api/engenheiros/{eid}")
async def atualizar_engenheiro(eid: int, dados: dict, _=Depends(verificar_acesso)):
    banco.salvar_engenheiro(eid, dados.get("nome",""), dados.get("crea",""))
    return {"ok": True}

@app.delete("/api/engenheiros/{eid}")
async def deletar_engenheiro(eid: int, _=Depends(verificar_acesso)):
    banco.deletar_engenheiro(eid)
    return {"ok": True}


# ══════════════════════════════════════════════════════════
#  ROTA PRINCIPAL — serve o HTML
# ══════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(os.path.dirname(__file__), "index.html"), "r", encoding="utf-8") as f:
        return f.read()

# ══════════════════════════════════════════════════════════
#  FUNCIONÁRIOS
# ══════════════════════════════════════════════════════════

@app.get("/api/funcionarios")
async def listar_funcionarios(busca: str = "", _=Depends(verificar_acesso)):
    return banco.buscar_funcionarios(busca)

@app.post("/api/funcionarios")
async def salvar_funcionario(dados: dict, _=Depends(verificar_acesso)):
    fid = banco.salvar_funcionario(dados)
    return {"id": fid, "ok": True}

@app.post("/api/funcionarios/importar")
async def importar_planilha(file: UploadFile = File(...), _=Depends(verificar_acesso)):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        lista, avisos = processador.ler_planilha(tmp_path)
        if not lista:
            return {"ok": False, "erro": "Nenhum funcionário encontrado", "avisos": avisos}
        ins, atu = banco.importar_funcionarios(lista)
        return {"ok": True, "inseridos": ins, "atualizados": atu, "avisos": avisos}
    finally:
        os.unlink(tmp_path)

# ══════════════════════════════════════════════════════════
#  DOCUMENTOS / MATRIZ
# ══════════════════════════════════════════════════════════

@app.get("/api/documentos")
async def listar_documentos(_=Depends(verificar_acesso)):
    from config import MODELOS_DIR
    # Modelos salvos no banco
    modelos_banco = {m["id"] for m in banco.listar_modelos() if m.get("tem_conteudo")}
    docs = []
    for d in DOCUMENTOS:
        existe_disco = os.path.exists(os.path.join(MODELOS_DIR, f"{d['id']}.docx"))
        existe_banco = d["id"] in modelos_banco
        docs.append({**d, "modelo_existe": existe_disco or existe_banco,
                     "modelo_no_banco": existe_banco, "modelo_no_disco": existe_disco})
    return docs

@app.get("/api/matriz/{cargo}")
async def docs_do_cargo(cargo: str, _=Depends(verificar_acesso)):
    return banco.docs_do_cargo(cargo)

@app.post("/api/matriz/{cargo}")
async def salvar_matriz(cargo: str, dados: dict, _=Depends(verificar_acesso)):
    banco.salvar_docs_cargo(cargo, dados.get("doc_ids", []))
    return {"ok": True}

@app.get("/api/cargos")
async def listar_cargos(_=Depends(verificar_acesso)):
    return banco.buscar_cargos()

# ══════════════════════════════════════════════════════════
#  MODELOS .DOCX — CRUD
# ══════════════════════════════════════════════════════════

@app.post("/api/modelos/upload/{doc_id}")
async def upload_modelo(doc_id: str, file: UploadFile = File(...),
                        cargo: str = None, _=Depends(verificar_acesso)):
    from config import MODELOS_DIR
    conteudo = await file.read()

    # Salva no banco
    nome_doc = next((d["nome"] for d in DOCUMENTOS if d["id"] == doc_id), doc_id)
    banco.salvar_modelo(doc_id, nome_doc, conteudo, cargo=cargo)

    # Também salva no disco (compatibilidade), apenas quando não é cargo-específico
    if cargo is None:
        os.makedirs(MODELOS_DIR, exist_ok=True)
        dest = os.path.join(MODELOS_DIR, f"{doc_id}.docx")
        with open(dest, "wb") as f:
            f.write(conteudo)

    return {"ok": True, "arquivo": f"{doc_id}.docx", "salvo_banco": True, "cargo": cargo}


@app.get("/api/modelos")
async def listar_modelos_banco(_=Depends(verificar_acesso)):
    from config import MODELOS_DIR
    modelos_banco = banco.listar_modelos()
    banco_por_id = {m["id"]: m for m in modelos_banco if m.get("tem_conteudo") and m.get("cargo") is None}
    resultado = []
    for d in DOCUMENTOS:
        existe_banco = d["id"] in banco_por_id
        existe_disco = os.path.exists(os.path.join(MODELOS_DIR, f"{d['id']}.docx"))
        resultado.append({
            "id": d["id"],
            "nome": d["nome"],
            "tem_arquivo": existe_banco or existe_disco,
            "no_banco": existe_banco,
            "no_disco": existe_disco,
        })
    return resultado


@app.get("/api/modelos/{doc_id}/download")
async def download_modelo(doc_id: str, cargo: str = None, _=Depends(verificar_acesso)):
    from fastapi.responses import Response
    from config import MODELOS_DIR
    conteudo = banco.buscar_modelo(doc_id, cargo=cargo)
    if not conteudo:
        # Fallback para disco
        modelo_path = os.path.join(MODELOS_DIR, f"{doc_id}.docx")
        if os.path.exists(modelo_path):
            with open(modelo_path, "rb") as f:
                conteudo = f.read()
    if not conteudo:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Modelo não encontrado")
    return Response(
        content=conteudo,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{doc_id}.docx"'}
    )


@app.delete("/api/modelos/{doc_id}")
async def deletar_modelo(doc_id: str, cargo: str = None, _=Depends(verificar_acesso)):
    banco.deletar_modelo(doc_id, cargo=cargo)
    return {"ok": True}

# ══════════════════════════════════════════════════════════
#  CATÁLOGO DE EPIs
# ══════════════════════════════════════════════════════════

@app.get("/api/epis/catalogo")
async def get_catalogo_epis(_=Depends(verificar_acesso)):
    return banco.listar_catalogo_epis()

@app.post("/api/epis/catalogo")
async def salvar_epi(dados: dict, _=Depends(verificar_acesso)):
    eid = banco.salvar_epi_catalogo(dados["descricao"], dados.get("ca",""), dados.get("quantidade_padrao",1))
    return {"ok": True, "id": eid}

@app.post("/api/epis/catalogo/bulk")
async def bulk_epis(dados: dict, _=Depends(verificar_acesso)):
    lista = dados.get("lista", [])
    for e in lista:
        banco.salvar_epi_catalogo(e["descricao"], e.get("ca",""), e.get("quantidade_padrao",1))
    return {"ok": True, "total": len(lista)}

@app.delete("/api/epis/catalogo/{epi_id}")
async def deletar_epi(epi_id: int, _=Depends(verificar_acesso)):
    banco.deletar_epi_catalogo(epi_id)
    return {"ok": True}

@app.get("/api/epis/cargo/{cargo}")
async def get_epis_cargo(cargo: str, _=Depends(verificar_acesso)):
    return banco.listar_epis_do_cargo(cargo)

@app.post("/api/epis/cargo/{cargo}")
async def salvar_epis_cargo(cargo: str, dados: dict, _=Depends(verificar_acesso)):
    banco.salvar_cargo_epis(cargo, dados.get("epis", []))
    return {"ok": True}

# ══════════════════════════════════════════════════════════
#  EPI POR CARGO
# ══════════════════════════════════════════════════════════
EPI_DOC_ID = "10_ficha_controle_epi"
OS_DOC_ID  = "03_os"

@app.get("/api/epi")
async def listar_epi(_=Depends(verificar_acesso)):
    """Lista cargos com status do EPI e OS (apenas modelos cargo-específicos)."""
    cargos = banco.buscar_cargos()
    # Busca apenas modelos com cargo definido (ignora o modelo geral)
    todos_modelos = banco.listar_modelos()
    modelos_cargo = {(m["id"], m["cargo"]) for m in todos_modelos if m.get("cargo") and m.get("tem_conteudo")}
    result = []
    for cargo in cargos:
        tem_epi = (EPI_DOC_ID, cargo) in modelos_cargo
        tem_os  = (OS_DOC_ID,  cargo) in modelos_cargo
        result.append({"cargo": cargo, "tem_epi": tem_epi, "tem_os": tem_os})
    return result


@app.post("/api/epi/upload/{cargo}")
async def upload_epi(cargo: str, file: UploadFile = File(...), _=Depends(verificar_acesso)):
    from config import KIT_PADRAO
    conteudo = await file.read()
    banco.salvar_modelo(EPI_DOC_ID, "Ficha de Controle de EPI", conteudo, cargo=cargo)
    # Auto-adiciona ao kit do cargo
    docs = banco.docs_do_cargo(cargo)
    extras = [d for d in docs if d not in KIT_PADRAO]
    if EPI_DOC_ID not in extras:
        extras.append(EPI_DOC_ID)
    banco.salvar_docs_cargo(cargo, extras)
    return {"ok": True, "cargo": cargo}


@app.get("/api/epi/{cargo}/download")
async def download_epi(cargo: str, _=Depends(verificar_acesso)):
    from fastapi.responses import Response
    conteudo = banco.buscar_modelo(EPI_DOC_ID, cargo=cargo)
    if not conteudo:
        raise HTTPException(status_code=404, detail="EPI não configurado para este cargo")
    return Response(content=conteudo,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="EPI_{cargo}.docx"'})


@app.delete("/api/epi/{cargo}")
async def deletar_epi(cargo: str, _=Depends(verificar_acesso)):
    from config import KIT_PADRAO
    banco.deletar_modelo(EPI_DOC_ID, cargo=cargo)
    docs = banco.docs_do_cargo(cargo)
    extras = [d for d in docs if d not in KIT_PADRAO and d != EPI_DOC_ID]
    banco.salvar_docs_cargo(cargo, extras)
    return {"ok": True}


@app.get("/api/epi/{cargo}/texto")
async def get_epi_texto(cargo: str, _=Depends(verificar_acesso)):
    conteudo = banco.buscar_modelo(EPI_DOC_ID, cargo=cargo)
    if not conteudo:
        raise HTTPException(status_code=404, detail="EPI não configurado")
    texto = processador.extrair_texto_docx(conteudo)
    return {"texto": texto}


@app.post("/api/epi/{cargo}/texto")
async def save_epi_texto(cargo: str, dados: dict, _=Depends(verificar_acesso)):
    texto = dados.get("texto", "")
    conteudo = processador.texto_para_docx(texto)
    banco.salvar_modelo(EPI_DOC_ID, "Ficha de Controle de EPI", conteudo, cargo=cargo)
    return {"ok": True}


# ── OS POR CARGO ──────────────────────────────────────────

@app.post("/api/os/upload/{cargo}")
async def upload_os(cargo: str, file: UploadFile = File(...), _=Depends(verificar_acesso)):
    from config import KIT_PADRAO
    conteudo = await file.read()
    banco.salvar_modelo(OS_DOC_ID, "Ordem de Serviço", conteudo, cargo=cargo)
    docs = banco.docs_do_cargo(cargo)
    extras = [d for d in docs if d not in KIT_PADRAO]
    if OS_DOC_ID not in extras:
        extras.append(OS_DOC_ID)
    banco.salvar_docs_cargo(cargo, extras)
    return {"ok": True, "cargo": cargo}


@app.get("/api/os/{cargo}/download")
async def download_os(cargo: str, _=Depends(verificar_acesso)):
    from fastapi.responses import Response
    conteudo = banco.buscar_modelo(OS_DOC_ID, cargo=cargo)
    if not conteudo:
        raise HTTPException(status_code=404, detail="OS não configurada para este cargo")
    return Response(content=conteudo,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="OS_{cargo}.docx"'})


@app.delete("/api/os/{cargo}")
async def deletar_os(cargo: str, _=Depends(verificar_acesso)):
    from config import KIT_PADRAO
    banco.deletar_modelo(OS_DOC_ID, cargo=cargo)
    docs = banco.docs_do_cargo(cargo)
    extras = [d for d in docs if d not in KIT_PADRAO and d != OS_DOC_ID]
    banco.salvar_docs_cargo(cargo, extras)
    return {"ok": True}


@app.get("/api/os/{cargo}/texto")
async def get_os_texto(cargo: str, _=Depends(verificar_acesso)):
    conteudo = banco.buscar_modelo(OS_DOC_ID, cargo=cargo)
    if not conteudo:
        raise HTTPException(status_code=404, detail="OS não configurada")
    texto = processador.extrair_texto_docx(conteudo)
    return {"texto": texto}


@app.post("/api/os/{cargo}/texto")
async def save_os_texto(cargo: str, dados: dict, _=Depends(verificar_acesso)):
    texto = dados.get("texto", "")
    conteudo = processador.texto_para_docx(texto)
    banco.salvar_modelo(OS_DOC_ID, "Ordem de Serviço", conteudo, cargo=cargo)
    return {"ok": True}


# ══════════════════════════════════════════════════════════
#  CBO — BUSCA E CONFIGURAÇÃO POR CARGO
# ══════════════════════════════════════════════════════════

def _cbo_buscar_por_titulo(titulo: str) -> list:
    """
    Busca ocupações no site mtecbo.gov.br pelo título.
    Retorna lista de dicts {codigo, titulo, tipo}
    """
    import requests as _req
    from bs4 import BeautifulSoup
    import urllib3
    urllib3.disable_warnings()

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    sess = _req.Session()

    # GET para pegar campos do form
    url_busca = "http://www.mtecbo.gov.br/cbosite/pages/pesquisas/BuscaPorTitulo.jsf"
    r = sess.get(url_busca, headers=headers, verify=False, timeout=10)
    soup = BeautifulSoup(r.content, "html.parser")

    campos = {}
    for inp in soup.find_all("input"):
        n = inp.get("name", "")
        if n:
            campos[n] = inp.get("value", "")

    campos["formBuscaPorTitulo:j_idt80"] = titulo
    campos["formBuscaPorTitulo:btConsultar"] = "Consultar"
    campos["formBuscaPorTitulo:radio"] = "3"
    campos["formBuscaPorTitulo:checkboxFamilias"] = "on"
    campos["formBuscaPorTitulo:checkboxOcupacoes"] = "on"
    campos["formBuscaPorTitulo:checkboxSinonimos"] = "on"

    form = soup.find("form", id="formBuscaPorTitulo")
    if not form:
        return []
    url_action = "http://www.mtecbo.gov.br" + form.get("action", "")

    # POST busca — resultado vem como PDF com lista de títulos e códigos
    r2 = sess.post(url_action, data=campos, headers=headers, verify=False, timeout=10)

    ct = r2.headers.get("Content-Type", "")
    resultados = []

    if "html" in ct.lower():
        r2.encoding = "iso-8859-1"
        soup2 = BeautifulSoup(r2.text, "html.parser")
        tabela = soup2.find("table")
        if tabela:
            for tr in tabela.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) >= 3:
                    titulo_td = tds[1].get_text(strip=True)
                    codigo_td = tds[2].get_text(strip=True)
                    tipo_td = tds[3].get_text(strip=True) if len(tds) > 3 else ""
                    import re as _re
                    if titulo_td and _re.match(r"\d{4}", codigo_td):
                        resultados.append({"titulo": titulo_td, "codigo": codigo_td, "tipo": tipo_td})

    elif "pdf" in ct.lower():
        # Fallback: extrai do PDF quando há muitos resultados
        try:
            from pypdf import PdfReader
            import io as _io, re as _re
            reader = PdfReader(_io.BytesIO(r2.content))
            for page in reader.pages:
                txt = page.extract_text() or ""
                for m in _re.finditer(r"(.+?)\s+(\d{4}-\d{2}|\d{4})\s+(Sin[oô]nimo|Ocupa[cç][aã]o|Fam[ií]lia)", txt):
                    resultados.append({
                        "titulo": m.group(1).strip(),
                        "codigo": m.group(2).strip(),
                        "tipo": m.group(3).strip(),
                    })
        except Exception:
            pass

    return resultados


def _cbo_buscar_descricao(codigo_familia: str) -> dict:
    """
    Dada a família CBO (ex: '7170'), retorna:
    {titulos: [...], descricao_sumaria: "..."}
    """
    import requests as _req
    from bs4 import BeautifulSoup
    import urllib3
    urllib3.disable_warnings()

    headers = {"User-Agent": "Mozilla/5.0"}
    sess = _req.Session()

    url_cod = "http://www.mtecbo.gov.br/cbosite/pages/pesquisas/BuscaPorCodigo.jsf"
    r = sess.get(url_cod, headers=headers, verify=False, timeout=10)
    soup = BeautifulSoup(r.content, "html.parser")

    campos = {}
    for inp in soup.find_all("input"):
        n = inp.get("name", "")
        if n:
            campos[n] = inp.get("value", "")

    # Usa apenas os 4 primeiros dígitos (família)
    familia = codigo_familia.split("-")[0].strip()
    campos["formBuscaPorCodigo:j_idt79"] = familia
    campos["formBuscaPorCodigo:btConsultar"] = "Consultar"

    form = soup.find("form", id="formBuscaPorCodigo")
    if not form:
        return {}
    url_action = "http://www.mtecbo.gov.br" + form.get("action", "")

    # POST busca
    r2 = sess.post(url_action, data=campos, headers=headers, verify=False, timeout=10)
    r2.encoding = "iso-8859-1"
    soup2 = BeautifulSoup(r2.text, "html.parser")

    campos2 = {}
    for inp in soup2.find_all("input"):
        n = inp.get("name", "")
        if n:
            campos2[n] = inp.get("value", "")

    # Clica na família (índice 0) para obter a descrição sumária
    campos2[f"formBuscaPorCodigo:objetos2:0:j_idt110"] = f"formBuscaPorCodigo:objetos2:0:j_idt110"
    form2 = soup2.find("form", id="formBuscaPorCodigo")
    if not form2:
        return {}
    url_action2 = "http://www.mtecbo.gov.br" + form2.get("action", "")

    r3 = sess.post(url_action2, data=campos2, headers=headers, verify=False, timeout=10)
    r3.encoding = "iso-8859-1"
    soup3 = BeautifulSoup(r3.text, "html.parser")

    texto = soup3.get_text(separator="\n")

    # Extrai "Descrição Sumária"
    import re as _re
    m = _re.search(r"Descri[çc][ãa]o\s+Sum[aá]ria\s*(.*?)(?=Todos os direitos|$)", texto, _re.DOTALL)
    descricao = m.group(1).strip() if m else ""

    # Remove linhas de rodapé
    descricao = _re.sub(r"\n{3,}", "\n\n", descricao).strip()

    # Extrai títulos (ocupações da família)
    titulos = []
    for m2 in _re.finditer(r"(\d{4}-\d{2})\s*-\s*([^\n]+)", texto):
        titulos.append({"codigo": m2.group(1).strip(), "titulo": m2.group(2).strip()})

    # Extrai nome da família
    m_familia = _re.search(r"\d{4}\s+([A-ZÁÉÍÓÚÃÂÊÎÔÛÀÇ][^\n]{5,80})\n", texto)
    nome_familia = m_familia.group(1).strip() if m_familia else ""

    return {
        "familia": familia,
        "nome_familia": nome_familia,
        "titulos": titulos,
        "descricao_sumaria": descricao,
    }


@app.get("/api/cbo/buscar")
async def buscar_cbo(titulo: str = "", codigo: str = "", _=Depends(verificar_acesso)):
    """
    Busca automática no site mtecbo.gov.br.
    - ?titulo=AJUDANTE → busca por nome, retorna lista de resultados
    - ?codigo=7170     → busca descrição da família, retorna descricao_sumaria
    """
    try:
        if codigo:
            data = _cbo_buscar_descricao(codigo)
            if data:
                return {"ok": True, **data}
            return {"ok": False, "erro": "Código não encontrado no CBO"}

        if titulo:
            resultados = _cbo_buscar_por_titulo(titulo)
            if resultados:
                return {"ok": True, "resultados": resultados}
            return {"ok": False, "erro": "Nenhum resultado encontrado para o termo informado"}

        return {"ok": False, "erro": "Informe titulo ou codigo"}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"ok": False, "erro": f"Erro ao consultar CBO: {str(e)}"}


@app.get("/api/os/config")
async def listar_os_config(_=Depends(verificar_acesso)):
    """Lista configuração de OS (CBO) por cargo."""
    cargos = banco.buscar_cargos()
    cbo_configs = {c["cargo"]: c for c in banco.listar_cargos_cbo()}
    todos_modelos = banco.listar_modelos()
    modelos_cargo = {(m["id"], m["cargo"]) for m in todos_modelos if m.get("cargo") and m.get("tem_conteudo")}
    result = []
    for cargo in cargos:
        cbo = cbo_configs.get(cargo, {})
        tem_os = (OS_DOC_ID, cargo) in modelos_cargo
        result.append({
            "cargo": cargo,
            "cbo_codigo": cbo.get("cbo_codigo", ""),
            "cbo_titulo": cbo.get("cbo_titulo", ""),
            "cbo_descricao": cbo.get("cbo_descricao", ""),
            "tem_os_gerada": tem_os,
        })
    return result


@app.get("/api/os/config/{cargo}")
async def get_os_config_cargo(cargo: str, _=Depends(verificar_acesso)):
    cbo = banco.buscar_cargo_cbo(cargo)
    epis = banco.listar_epis_do_cargo(cargo)
    return {
        "cargo": cargo,
        "cbo": cbo or {"cbo_codigo": "", "cbo_titulo": "", "cbo_descricao": ""},
        "epis": epis,
    }


@app.post("/api/os/config/{cargo}")
async def salvar_os_config_cargo(cargo: str, dados: dict, _=Depends(verificar_acesso)):
    """Salva configuração de CBO para um cargo e gera a OS modelo."""
    cbo_codigo   = dados.get("cbo_codigo", "")
    cbo_titulo   = dados.get("cbo_titulo", "")
    cbo_descricao = dados.get("cbo_descricao", "")

    banco.salvar_cargo_cbo(cargo, cbo_codigo, cbo_titulo, cbo_descricao)

    # Gera a OS modelo para o cargo (sem funcionário — com placeholders)
    modelo_base = banco.buscar_modelo("03_os_base")
    if not modelo_base:
        return {"ok": True, "aviso": "CBO salvo. Modelo base da OS não encontrado para gerar prévia."}

    # Monta texto de EPIs
    epis = banco.listar_epis_do_cargo(cargo)
    epis_texto = _formatar_epis_texto(epis)

    # Funcionário fictício para template de cargo
    func_template = {
        "nome": "{{NOME}}", "cpf": "{{CPF}}", "matricula": "{{MATRICULA}}",
        "cargo": cargo, "lotacao": "{{LOTACAO}}", "admissao": "{{DATA_ADMISSAO}}",
        "rg": "{{RG}}",
    }
    docx_bytes = processador.preencher_os_dinamica(
        func_template, cbo_descricao, epis_texto, modelo_base
    )
    banco.salvar_modelo(OS_DOC_ID, "Ordem de Serviço", docx_bytes, cargo=cargo)

    # Auto-adiciona ao kit do cargo
    from config import KIT_PADRAO
    docs = banco.docs_do_cargo(cargo)
    extras = [d for d in docs if d not in KIT_PADRAO]
    if OS_DOC_ID not in extras:
        extras.append(OS_DOC_ID)
    banco.salvar_docs_cargo(cargo, extras)

    return {"ok": True, "cargo": cargo, "epis_count": len(epis)}


def _formatar_epis_texto(epis: list) -> str:
    """Formata lista de EPIs para texto da OS."""
    if not epis:
        return ""
    linhas = []
    for e in epis:
        ca = e.get("ca", "")
        qtd = e.get("quantidade", 1)
        desc = e.get("descricao", "")
        if ca:
            linhas.append(f"- {desc} (C.A: {ca}) — Qtd: {qtd}")
        else:
            linhas.append(f"- {desc} — Qtd: {qtd}")
    return "\n".join(linhas)


@app.post("/api/os/enviar")
async def enviar_os(dados: dict, _=Depends(verificar_acesso)):
    """Envia OS para um ou mais funcionários."""
    func_ids = dados.get("func_ids", [])
    sandbox  = dados.get("sandbox", False)

    todos   = banco.buscar_funcionarios("")
    pasta   = processador.pasta_lote()
    resultados = []
    erros      = []

    for fid in func_ids:
        f = next((x for x in todos if x["id"] == fid), None)
        if not f:
            continue

        # Busca modelo OS gerado para o cargo
        modelo_bytes = banco.buscar_modelo(OS_DOC_ID, cargo=f["cargo"])
        if not modelo_bytes:
            erros.append(f"{f['nome']}: OS não configurada para o cargo '{f['cargo']}'")
            continue

        # Preenche OS com dados reais do funcionário
        cbo = banco.buscar_cargo_cbo(f["cargo"])
        cbo_descricao = cbo.get("cbo_descricao", "") if cbo else ""
        epis = banco.listar_epis_do_cargo(f["cargo"])
        epis_texto = _formatar_epis_texto(epis)

        docx_bytes = processador.preencher_os_dinamica(f, cbo_descricao, epis_texto, modelo_bytes)

        import re as _re
        nome_seguro = _re.sub(r"[^\w\s-]", "", f["nome"])
        nome_seguro = _re.sub(r"\s+", "_", nome_seguro.strip())
        caminho_docx = os.path.join(pasta, f"OS_{nome_seguro}.docx")
        with open(caminho_docx, "wb") as fw:
            fw.write(docx_bytes)

        caminho_pdf = processador.converter_para_pdf(caminho_docx)
        if not caminho_pdf:
            erros.append(f"{f['nome']}: falha ao converter OS para PDF")
            continue

        try: os.remove(caminho_docx)
        except: pass

        nome_doc = f"Ordem de Serviço — {f['nome']}"
        ret = zapsign.enviar_documento(nome_documento=nome_doc, caminho_pdf=caminho_pdf,
                                          funcionario=f, sandbox=sandbox)
        if ret["sucesso"]:
            resultados.append({"id": f["id"], "nome": f["nome"],
                                "cargo": f["cargo"], "link": ret.get("link", "")})
            try:
                banco.registrar_envio({
                    "funcionario_id": f["id"],
                    "doc_id":         "03_os",
                    "doc_nome":       nome_doc,
                    "pdf_path":       caminho_pdf,
                    "autentique_id":  ret.get("autentique_id", ""),
                    "link_assinatura": ret.get("link", ""),
                    "status":         "enviado",
                })
            except Exception as e:
                print(f"WARN registrar_envio OS: {e}")
        else:
            erros.append(f"{f['nome']}: {ret['erro']}")

    return {"ok": True, "enviados": len(resultados), "resultados": resultados, "erros": erros}


# ══════════════════════════════════════════════════════════
#  LOTES
# ══════════════════════════════════════════════════════════

@app.get("/api/lotes")
async def listar_lotes(_=Depends(verificar_acesso)):
    return banco.listar_lotes()

@app.post("/api/lotes/preview")
async def preview_lote(dados: dict, _=Depends(verificar_acesso)):
    """Retorna preview do lote sem enviar."""
    func_ids = dados.get("func_ids", [])
    todos = banco.buscar_funcionarios("")
    preview = []
    total_docs = 0
    for fid in func_ids:
        f = next((x for x in todos if x["id"] == fid), None)
        if not f:
            continue
        doc_ids = banco.docs_do_cargo(f["cargo"])
        docs_nomes = [d["nome"] for d in DOCUMENTOS if d["id"] in doc_ids]
        total_docs += len(doc_ids)
        preview.append({
            "id":       f["id"],
            "nome":     f["nome"],
            "cargo":    f["cargo"],
            "lotacao":  f.get("lotacao",""),
            "celular":  f.get("celular",""),
            "doc_ids":  doc_ids,
            "docs_nomes": docs_nomes,
        })
    return {"funcionarios": preview, "total_docs": total_docs}

@app.post("/api/lotes/enviar")
async def enviar_lote(dados: dict, _=Depends(verificar_acesso)):
    """Processa e envia o lote completo para o Autentique — PDF único por funcionário."""
    func_ids  = dados.get("func_ids", [])
    descricao = dados.get("descricao", f"Lote {datetime.now().strftime('%d/%m/%Y')}")
    celulares = dados.get("celulares", {})
    sandbox   = dados.get("sandbox", False)

    todos   = banco.buscar_funcionarios("")
    lote_id = banco.criar_lote(descricao)
    pasta   = processador.pasta_lote()

    resultados = []
    erros      = []

    for fid in func_ids:
        f = next((x for x in todos if x["id"] == fid), None)
        if not f:
            continue

        cel_editado = celulares.get(str(fid))
        if cel_editado:
            f = dict(f)
            f["celular"] = cel_editado

        doc_ids = banco.docs_do_cargo(f["cargo"])
        if not doc_ids:
            erros.append(f"{f['nome']}: nenhum documento configurado para o cargo '{f['cargo']}'")
            continue

        banco.adicionar_ao_lote(lote_id, f["id"])

        # Gera PDFs individuais
        pdfs = processador.gerar_kit_funcionario(f, doc_ids, pasta)

        # Filtra PDFs gerados com sucesso
        pdfs_ok = [r for r in pdfs if not r["erro"] and r["pdf_path"]]
        pdfs_erro = [r for r in pdfs if r["erro"]]

        for r in pdfs_erro:
            erros.append(f"{f['nome']} / {r['doc_nome']}: {r['erro']}")

        if not pdfs_ok:
            erros.append(f"{f['nome']}: nenhum PDF gerado com sucesso")
            continue

        # Junta todos os PDFs em um único
        pdf_final = processador.juntar_pdfs(
            [r["pdf_path"] for r in pdfs_ok],
            pasta,
            f["nome"]
        )

        if not pdf_final:
            erros.append(f"{f['nome']}: falha ao juntar PDFs")
            continue

        # Envia PDF único para o Autentique
        nome_kit = f"Kit SST — {f['nome']}"
        ret = zapsign.enviar_documento(
            nome_documento=nome_kit,
            caminho_pdf=pdf_final,
            funcionario=f,
            sandbox=sandbox
        )

        # Registra no banco
        try:
            banco.registrar_envio({
                "funcionario_id":  f["id"],
                "doc_id":          "kit_completo",
                "doc_nome":        nome_kit,
                "pdf_path":        pdf_final,
                "autentique_id":   ret.get("autentique_id"),
                "link_assinatura": ret.get("link"),
                "status":          "enviado" if ret.get("sucesso") else "erro",
            })
        except Exception as db_err:
            print(f"⚠️  Falha ao registrar envio no banco para {f['nome']}: {db_err}")

        if ret["sucesso"]:
            resultados.append({
                "nome":    f["nome"],
                "celular": f.get("celular", ""),
                "cargo":   f["cargo"],
                "links":   [{"doc": nome_kit, "link": ret.get("link", "")}],
            })
        else:
            erros.append(f"{f['nome']}: {ret['erro']}")

    return {
        "ok":         True,
        "lote_id":    lote_id,
        "enviados":   len(resultados),
        "erros":      erros,
        "resultados": resultados,
    }

# ══════════════════════════════════════════════════════════
#  ENVIO FICHA DE EPI
# ══════════════════════════════════════════════════════════

@app.post("/api/epi/enviar-custom")
async def enviar_ficha_epi_custom(dados: dict, _=Depends(verificar_acesso)):
    """Envia ficha de EPI com EPIs selecionados dinamicamente."""
    func_id  = dados.get("func_id")
    epis     = dados.get("epis", [])   # [{descricao, ca, quantidade}]
    sandbox  = dados.get("sandbox", False)

    todos = banco.buscar_funcionarios("")
    f = next((x for x in todos if x["id"] == func_id), None)
    if not f:
        return {"ok": False, "erro": "Funcionário não encontrado"}

    # Busca modelo base zerado
    modelo_bytes = banco.buscar_modelo("10_ficha_epi_base")
    if not modelo_bytes:
        from config import MODELOS_DIR
        path = os.path.join(MODELOS_DIR, "10_ficha_epi_base.docx")
        if os.path.exists(path):
            with open(path, "rb") as f2:
                modelo_bytes = f2.read()
    if not modelo_bytes:
        return {"ok": False, "erro": "Modelo base da ficha de EPI não encontrado. Faça o upload do modelo zerado."}

    pasta = processador.pasta_lote()
    docx_bytes = processador.preencher_ficha_epi_dinamica(f, epis, modelo_bytes)

    import re, io
    nome_seguro = re.sub(r"[^\w\s-]", "", f["nome"])
    nome_seguro = re.sub(r"\s+", "_", nome_seguro.strip())
    caminho_docx = os.path.join(pasta, f"EPI_{nome_seguro}.docx")
    with open(caminho_docx, "wb") as fw:
        fw.write(docx_bytes)

    caminho_pdf = processador.converter_para_pdf(caminho_docx)
    if not caminho_pdf:
        return {"ok": False, "erro": "Falha ao converter para PDF"}

    try: os.remove(caminho_docx)
    except: pass

    nome_doc = f"Ficha de EPI — {f['nome']}"
    ret = zapsign.enviar_documento(nome_documento=nome_doc, caminho_pdf=caminho_pdf,
                                      funcionario=f, sandbox=sandbox)
    if ret["sucesso"]:
        try:
            banco.registrar_envio({
                "funcionario_id":  f["id"],
                "doc_id":          "10_ficha_epi",
                "doc_nome":        nome_doc,
                "pdf_path":        caminho_pdf,
                "autentique_id":   ret.get("autentique_id", ""),
                "link_assinatura": ret.get("link", ""),
                "status":          "enviado",
            })
        except Exception as e:
            print(f"WARN registrar_envio EPI individual: {e}")
        return {"ok": True, "nome": f["nome"], "cargo": f["cargo"], "link": ret.get("link","")}
    return {"ok": False, "erro": ret["erro"]}


@app.post("/api/epi/enviar")
async def enviar_ficha_epi(dados: dict, _=Depends(verificar_acesso)):
    """Envia a ficha de EPI para um ou mais funcionários."""
    func_ids = dados.get("func_ids", [])
    sandbox  = dados.get("sandbox", False)

    todos   = banco.buscar_funcionarios("")
    pasta   = processador.pasta_lote()
    resultados = []
    erros      = []

    for fid in func_ids:
        f = next((x for x in todos if x["id"] == fid), None)
        if not f:
            continue

        # Gera PDF da ficha de EPI
        caminho_docx = processador.preencher_docx(EPI_DOC_ID, f, pasta)
        if not caminho_docx:
            erros.append(f"{f['nome']}: modelo de EPI não encontrado para o cargo '{f['cargo']}'")
            continue

        caminho_pdf = processador.converter_para_pdf(caminho_docx)
        if not caminho_pdf:
            erros.append(f"{f['nome']}: falha ao converter PDF")
            continue

        try:
            os.remove(caminho_docx)
        except Exception:
            pass

        nome_doc = f"Ficha de EPI — {f['nome']}"
        ret = zapsign.enviar_documento(
            nome_documento=nome_doc,
            caminho_pdf=caminho_pdf,
            funcionario=f,
            sandbox=sandbox
        )

        if ret["sucesso"]:
            resultados.append({
                "id":    f["id"],
                "nome":  f["nome"],
                "cargo": f["cargo"],
                "link":  ret.get("link", ""),
            })
            try:
                banco.registrar_envio({
                    "funcionario_id":  f["id"],
                    "doc_id":          "10_ficha_epi",
                    "doc_nome":        nome_doc,
                    "pdf_path":        caminho_pdf,
                    "autentique_id":   ret.get("autentique_id", ""),
                    "link_assinatura": ret.get("link", ""),
                    "status":          "enviado",
                })
            except Exception as e:
                print(f"WARN registrar_envio EPI: {e}")
        else:
            erros.append(f"{f['nome']}: {ret['erro']}")

    return {"ok": True, "enviados": len(resultados), "resultados": resultados, "erros": erros}

# ══════════════════════════════════════════════════════════
#  AUTENTIQUE
# ══════════════════════════════════════════════════════════

@app.get("/api/config")
async def get_config(_=Depends(verificar_acesso)):
    return {"empresa": EMPRESA}

@app.get("/api/zapsign/verificar")
async def verificar_zapsign(_=Depends(verificar_acesso)):
    ok, msg = zapsign.verificar_token()
    return {"ok": ok, "mensagem": msg}

@app.get("/api/zapsign/conta")
async def info_conta_zapsign(_=Depends(verificar_acesso)):
    """Retorna informações da conta ZapSign: plano, limites, uso."""
    import requests as _req
    token = os.environ.get("ZAPSIGN_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resultados = {}
    for path in ["/api/v1/account/", "/api/v1/user/", "/api/v1/organization/", "/api/v1/plan/"]:
        try:
            r = _req.get(f"https://api.zapsign.com.br{path}", headers=headers, timeout=10)
            resultados[path] = {"status": r.status_code, "body": r.json() if r.headers.get("content-type","").startswith("application/json") else r.text[:300]}
        except Exception as e:
            resultados[path] = {"status": "erro", "body": str(e)}
    return resultados

# alias legado para não quebrar chamadas antigas
@app.get("/api/autentique/verificar")
async def verificar_autentique_legado(_=Depends(verificar_acesso)):
    ok, msg = zapsign.verificar_token()
    return {"ok": ok, "mensagem": msg}

# ══════════════════════════════════════════════════════════
#  HISTÓRICO
# ══════════════════════════════════════════════════════════

@app.get("/api/historico")
async def historico(_=Depends(verificar_acesso)):
    return banco.listar_lotes()


# ── Histórico de Envios (ZapSign) ─────────────────────────
@app.get("/api/envios")
async def listar_envios(
    funcionario_id: int = None,
    status: str = None,
    limite: int = 100,
    _=Depends(verificar_acesso)
):
    """Lista todos os documentos enviados para assinatura."""
    return banco.listar_envios(funcionario_id=funcionario_id, status=status, limite=limite)


@app.post("/api/envios/{envio_id}/atualizar-status")
async def atualizar_status_envio(envio_id: int, _=Depends(verificar_acesso)):
    """Consulta o ZapSign e atualiza o status do envio no banco."""
    envio = banco.buscar_envio_por_id(envio_id)
    if not envio:
        raise HTTPException(404, "Envio não encontrado")

    doc_token = envio.get("autentique_id")  # campo guarda o token ZapSign
    if not doc_token:
        raise HTTPException(400, "Envio não possui token ZapSign")

    resultado = zapsign.consultar_status(doc_token)

    if resultado["erro"]:
        raise HTTPException(500, resultado["erro"])

    # Atualiza no banco
    banco.atualizar_status_envio(
        envio_id=envio_id,
        status=resultado["status"],
        assinado_em=resultado.get("assinado_em")
    )

    return {
        "envio_id":    envio_id,
        "status":      resultado["status"],
        "status_pt":   resultado["status_pt"],
        "assinado_em": resultado.get("assinado_em"),
        "signatarios": resultado.get("signatarios", []),
    }


@app.post("/api/envios/atualizar-todos")
async def atualizar_todos_pendentes(_=Depends(verificar_acesso)):
    """Atualiza o status de todos os envios pendentes consultando o ZapSign."""
    # Busca todos os envios que ainda não foram assinados (independente do status exato)
    todos = banco.listar_envios(status=None, limite=500)
    pendentes = [e for e in todos if e.get("status") != "signed"]
    atualizados = 0
    erros = 0
    for envio in pendentes:
        doc_token = envio.get("autentique_id") or envio.get("zapsign_token")
        if not doc_token:
            continue
        resultado = zapsign.consultar_status(doc_token)
        if resultado["erro"]:
            erros += 1
            continue
        banco.atualizar_status_envio(
            envio_id=envio["id"],
            status=resultado["status"],
            assinado_em=resultado.get("assinado_em")
        )
        atualizados += 1

    return {"atualizados": atualizados, "erros": erros, "total_pendentes": len(pendentes)}


@app.get("/api/envios/{envio_id}/download")
async def download_pdf_assinado(envio_id: int):
    """Baixa o PDF assinado direto do ZapSign (sem auth — link direto no browser)."""
    envio = banco.buscar_envio_por_id(envio_id)
    if not envio:
        raise HTTPException(404, "Envio não encontrado")

    doc_token = envio.get("autentique_id") or envio.get("zapsign_token")
    if not doc_token:
        raise HTTPException(400, "Envio não possui token ZapSign")

    try:
        pdf_bytes, erro = zapsign.baixar_pdf_assinado(doc_token)
    except Exception as exc:
        print(f"ERRO download envio {envio_id}: {exc}")
        raise HTTPException(500, f"Erro interno ao baixar PDF: {exc}")

    if erro:
        raise HTTPException(400, erro)

    nome_arquivo = (envio.get("doc_nome") or "documento").replace("/", "-").replace(" ", "_")
    nome_arquivo += "_assinado.pdf"

    import io
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'}
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
