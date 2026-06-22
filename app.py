"""
SST Digital - Sistema Web
Backend FastAPI para geração e envio de kits SST via Autentique
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends, Body
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


def _garantir_os_base():
    """Seed the 03_os_base template if not already in DB."""
    try:
        existing = banco.buscar_modelo("03_os_base")
        if not existing:
            docx_bytes = processador.criar_os_base_docx()
            banco.salvar_modelo("03_os_base", "Ordem de Serviço Base", docx_bytes)
    except Exception as e:
        print(f"[WARN] _garantir_os_base: {e}")


@app.on_event("startup")
async def startup_event():
    _garantir_os_base()
    # Seed PGR: insere cargos sem entrada E corrige riscos com duplicatas acumuladas
    try:
        import re as _re
        for item in PGR_SEED:
            existente = banco.buscar_pgr_cargo(item["cargo"])
            if not existente:
                banco.salvar_pgr_cargo(**item)
            else:
                # Corrige riscos duplicados (texto repetido com " — " de salvamentos anteriores)
                riscos_atual = existente.get("riscos", "")
                if " — " in riscos_atual:
                    partes = [p.strip() for p in riscos_atual.split(" — ")]
                    riscos_limpo = partes[0]  # primeira ocorrência = original do PGR
                    banco.salvar_pgr_cargo(
                        cargo=existente["cargo"],
                        cbo=existente.get("cbo",""),
                        ambiente=existente.get("ambiente",""),
                        atividades=existente.get("atividades",""),
                        riscos=riscos_limpo,
                        epis=existente.get("epis",""),
                        epcs=existente.get("epcs",""),
                    )
        print(f"[STARTUP] PGR seed verificado ({len(PGR_SEED)} entradas)")
    except Exception as e:
        print(f"[WARN] pgr seed: {e}")
    try:
        propagados = banco.propagar_cbo_variantes()
        if propagados:
            print(f"[STARTUP] CBO propagado para {len(propagados)} cargos: {propagados}")
    except Exception as e:
        print(f"[WARN] propagar_cbo_variantes: {e}")


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
async def listar_funcionarios(busca: str = "", q: str = "", _=Depends(verificar_acesso)):
    termo = q or busca
    return banco.buscar_funcionarios(termo)

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

        # Se a planilha tem coluna CBO, salva CBO por cargo automaticamente
        # Agrupa por cargo para buscar MTE só uma vez por código único
        cbos_salvos = 0
        cbo_por_cargo: dict[str, str] = {}
        for f in lista:
            cbo_num = (f.get("cbo") or "").strip()
            cargo   = (f.get("cargo") or "").strip()
            if cbo_num and cargo and cargo not in cbo_por_cargo:
                cbo_por_cargo[cargo] = cbo_num
        # Códigos únicos → busca MTE uma vez por código
        mte_cache: dict[str, tuple[str, str]] = {}
        for cargo, cbo_num in cbo_por_cargo.items():
            existente = banco.buscar_cargo_cbo(cargo)
            if not existente or not existente.get("cbo_codigo"):
                if cbo_num not in mte_cache:
                    mte_cache[cbo_num] = _cbo_enriquecer(cbo_num)
                titulo, descricao = mte_cache[cbo_num]
                banco.salvar_cargo_cbo(cargo, cbo_num, titulo, descricao)
                cbos_salvos += 1

        if cbos_salvos:
            avisos.append(f"ℹ️ {cbos_salvos} cargo(s) com CBO preenchido automaticamente da planilha.")

        return {"ok": True, "inseridos": ins, "atualizados": atu, "avisos": avisos}
    finally:
        os.unlink(tmp_path)

# ══════════════════════════════════════════════════════════
#  DOCUMENTOS / MATRIZ
# ══════════════════════════════════════════════════════════

@app.get("/api/documentos")
async def listar_documentos(_=Depends(verificar_acesso)):
    from config import MODELOS_DIR
    modelos_banco = {m["id"] for m in banco.listar_modelos() if m.get("tem_conteudo")}
    docs = []
    for d in DOCUMENTOS:
        existe_disco = os.path.exists(os.path.join(MODELOS_DIR, f"{d['id']}.docx"))
        existe_banco = d["id"] in modelos_banco
        docs.append({**d, "modelo_existe": existe_disco or existe_banco,
                     "modelo_no_banco": existe_banco, "modelo_no_disco": existe_disco, "extra": False})
    # Inclui documentos extras cadastrados pelo usuário
    for e in banco.listar_documentos_extras():
        existe_banco = e["id"] in modelos_banco
        docs.append({"id": e["id"], "nome": e["nome"], "obrig": False, "kit_padrao": False,
                     "modelo_existe": existe_banco, "modelo_no_banco": existe_banco,
                     "modelo_no_disco": False, "extra": True})
    return docs


@app.post("/api/documentos/novo")
async def adicionar_documento(
    nome: str = Form(...),
    file: UploadFile = File(...),
    _=Depends(verificar_acesso)
):
    """Cadastra um novo documento extra e salva o modelo."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "_", nome.lower()).strip("_")
    if not slug:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Nome inválido")
    doc_id = f"extra_{slug}"
    conteudo = await file.read()
    banco.salvar_documento_extra(doc_id, nome)
    banco.salvar_modelo(doc_id, nome, conteudo)
    return {"ok": True, "id": doc_id, "nome": nome}

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

@app.get("/api/cargos/configurados")
async def listar_cargos_configurados(_=Depends(verificar_acesso)):
    """Cargos que têm EPIs ou OS configurados — para dropdowns de Kit e seleção."""
    return banco.buscar_cargos_configurados()

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


@app.post("/api/modelos/{doc_id}/upload")
async def upload_modelo_base(doc_id: str, file: UploadFile = File(...), _=Depends(verificar_acesso)):
    """Substitui um modelo base (ex: 03_os_base) por upload de .docx."""
    conteudo = await file.read()
    banco.salvar_modelo(doc_id, file.filename or doc_id, conteudo)
    return {"ok": True}


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
#  PGR - INVENTÁRIO DE RISCOS
# ══════════════════════════════════════════════════════════

PGR_SEED = [
    {"cargo":"ADMINISTRADOR","cbo":"411005","ambiente":"ADMINISTRATIVO","atividades":"Documentos administrativos diversos, emissão de N.F, verificar recebimentos e lançamento no sistema, atendimento ao cliente via telefone.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"AJUDANTE","cbo":"717020","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Químicos: Particulados (insolúveis ou de baixa solubilidade) não especiﬁcados de outra maneira (PNOS) - Fração Respirável","epis":"BOTINA DE SEGURANÇA; RUÍDO; LUVA PARA PROTEÇÃO CONTRA AGENTES MECÂNICOS; AVENTAL DE RASPA; LUVA DE PROTEÇÃO CONTRA AGENTES QUÍMICOS","epcs":""},
    {"cargo":"AJUDANTE (OP ROÇADEIRA COSTAL)","cbo":"717020","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: PROJEÇÃO DE PARTÍCULAS. Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Químicos: Particulados (insolúveis ou de baixa solubilidade) não especiﬁcados de outra maneira (PNOS) - Fração Respirável","epis":"AVENTAL DE RASPA; PROJEÇÃO DE PARTÍCULAS; LUVA DE PROTEÇÃO CONTRA AGENTES QUÍMICOS; PROTETOR FACIAL; ÓCULOS; LUVA VAQUETA","epcs":""},
    {"cargo":"AJUDANTE A","cbo":"717020","ambiente":"OPERACIONAL/OBRA","atividades":"Auxiliar os profissionais nas montagens efetuando transportes e recolhimento de materiais, limpeza do local da obra, transporte e recolhimento de ferramentas e qualquer atividade de auxílio ao bom andamento da obra.","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Químicos: POEIRA MINERAL","epis":"BOTINA DE SEGURANÇA; RUÍDO; LUVA PARA PROTEÇÃO CONTRA AGENTES MECÂNICOS; AVENTAL DE RASPA; LUVA DE PROTEÇÃO CONTRA AGENTES QUÍMICOS","epcs":""},
    {"cargo":"ALMOXARIFADO","cbo":"414105","ambiente":"OPERACIONAL/OBRA","atividades":"Recepcionam, conferem e armazenam produtos e materiais. Fazem lançamentos de movimentação de entradas e saídas e controlam estoques. Distribuem produtos e materiais. Organizam o almoxarifado. Controle de entrada e saída de materiais, recebimento de materiais na obra.","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos","epis":"","epcs":""},
    {"cargo":"ANALISTA CONTÁBIL","cbo":"413110","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"ANALISTA DE CONTROLADORIA","cbo":"252405","ambiente":"ADMINISTRATIVO","atividades":"Responsável pela folha de pagamento, registro de ponto, recrutamento e seleção de funcionários.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"ANALISTA DE DEPARTAMENTO PESSOAL","cbo":"252405","ambiente":"ADMINISTRATIVO","atividades":"Responsável pela folha de pagamento, registro de ponto, recrutamento e seleção de funcionários.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"ANALISTA DE DEPARTAMENTO PESSOAL NV I","cbo":"252405","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"ANALISTA FINANCEIRA","cbo":"252405","ambiente":"ADMINISTRATIVO","atividades":"Responsável pela folha de pagamento, registro de ponto, recrutamento e seleção de funcionários.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"ANALISTA FISCAL","cbo":"251225","ambiente":"ADMINISTRATIVO","atividades":"Presta assistência na área administrativa; auxilia o administrador em suas atividades rotineiras e no controle de gestão financeira, administração, organização de arquivos, gerência de informações, contratações; revisão de documentos; emissão de notas.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"ANALISTA FISCAL II","cbo":"251225","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"APONTADOR","cbo":"414210","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Físicos: RADIAÇÃO NÃO IONIZANTE","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; RUÍDO","epcs":""},
    {"cargo":"ASSISTENTE ADMINISTRATIVO","cbo":"411005","ambiente":"ADMINISTRATIVO","atividades":"Documentos administrativos diversos, emissão de N.F, verificar recebimentos e lançamento no sistema, atendimento ao cliente via telefone.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"ASSISTENTE DEPARTAMENTO PESSOAL","cbo":"411005","ambiente":"ADMINISTRATIVO","atividades":"Documentos administrativos diversos, emissão de N.F, verificar recebimentos e lançamento no sistema, atendimento ao cliente via telefone.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"ASSISTENTE FINANCEIRO","cbo":"411005","ambiente":"ADMINISTRATIVO","atividades":"Responsável por auxiliar nas atividades financeiras da organização: controle de contas a pagar e a receber, conciliação bancária, emissão de relatórios financeiros.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"ASSISTENTE FINANCEIRO III","cbo":"411005","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"AUXILIAR ADMINISTRATIVO","cbo":"411005","ambiente":"ADMINISTRATIVO","atividades":"Documentos administrativos diversos, emissão de N.F, verificar recebimentos e lançamento no sistema, atendimento ao cliente via telefone.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"AUXILIAR ADMINISTRATIVO NIVEL I","cbo":"411005","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"AUXILIAR ADMINISTRATIVO NIVEL III","cbo":"411005","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"AUXILIAR CONTÁBIL","cbo":"413110","ambiente":"ADMINISTRATIVO","atividades":"Rotinas contábeis, garantindo que as operações financeiras sejam registradas corretamente dentro das normas contábeis e tributárias. Realiza diversas tarefas administrativas e auxilia no suporte à equipe contábil.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"AUXILIAR DE ALMOXARIFADO","cbo":"414105","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos","epis":"","epcs":""},
    {"cargo":"AUXILIAR DE DP","cbo":"411005","ambiente":"ADMINISTRATIVO","atividades":"Documentos administrativos diversos, emissão de N.F, verificar recebimentos e lançamento no sistema, atendimento ao cliente via telefone.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"AUXILIAR DE GESTÃO DE FROTA","cbo":"141605","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":""},
    {"cargo":"AUXILIAR DE LABORATÓRIO","cbo":"301105","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: FUMOS DE ASFALTO","epis":"BOTINA DE COURO; COLETE REFLETIVO; PROTETOR AURICULAR TIPO PLUG; RUÍDO; FUMOS DE ASFALTO","epcs":""},
    {"cargo":"AUXILIAR DE LIMPEZA","cbo":"514320","ambiente":"LIMPEZA","atividades":"Cuida da limpeza do escritório, mantendo sempre limpo e organizado.","riscos":"Acidentes / Mecânicos: Queda de mesmo nível, tropeços, escorregões, arranhões, cortes e ferimentos. Biológicos: Agentes biológicos infecciosos e infectocontagiosos ( bactérias, vírus, protozoários, fungos, príons, parasitas e outros). Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Químicos: PRODUTOS QUÍMICOS (DOMISSANITÁRIOS)","epis":"PRODUTOS QUÍMICOS (DOMISSANITÁRIOS); CALÇADO TIPO BOTA - BORRACHA; LUVA LATEX","epcs":""},
    {"cargo":"AUXILIAR DE LIMPEZA (OBRA)","cbo":"514320","ambiente":"LIMPEZA","atividades":"","riscos":"Acidentes / Mecânicos: Queda de mesmo nível, tropeços, escorregões, arranhões, cortes e ferimentos. Biológicos: Agentes biológicos infecciosos e infectocontagiosos ( bactérias, vírus, protozoários, fungos, príons, parasitas e outros). Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Químicos: PRODUTOS QUÍMICOS (DOMISSANITÁRIOS)","epis":"PRODUTOS QUÍMICOS (DOMISSANITÁRIOS); CALÇADO TIPO BOTA - BORRACHA; LUVA LATEX","epcs":""},
    {"cargo":"AUXILIAR DE MECÂNICO","cbo":"725205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos","epis":"BOTINA DE COURO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; LUVA DE PROTEÇÃO CONTRA AGENTES QUÍMICOS; CREME PROTETOR; BENZENO","epcs":""},
    {"cargo":"AUXILIAR DE MECÂNICO NIVEL I","cbo":"725205","ambiente":"OPERACIONAL/OBRA","atividades":"Realiza manutenção mecânica preventiva e corretiva de veículos, monta e troca peças, lubrifica motor, regula mecanismos e alinha equipamentos. Verifica quanto à substituição e aproveitamento de componentes.","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos","epis":"BOTINA DE COURO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; LUVA DE PROTEÇÃO CONTRA AGENTES QUÍMICOS; CREME PROTETOR; BENZENO","epcs":""},
    {"cargo":"AUXILIAR DE MECÂNICO NIVEL II","cbo":"725205","ambiente":"OPERACIONAL/OBRA","atividades":"Realiza manutenção mecânica preventiva e corretiva de veículos, monta e troca peças, lubrifica motor, regula mecanismos e alinha equipamentos. Verifica quanto à substituição e aproveitamento de componentes.","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos","epis":"BOTINA DE COURO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; LUVA DE PROTEÇÃO CONTRA AGENTES QUÍMICOS; CREME PROTETOR; BENZENO","epcs":""},
    {"cargo":"AUXILIAR DE RH","cbo":"411005","ambiente":"ADMINISTRATIVO","atividades":"Executam serviços de apoio nas áreas de recursos humanos e administração, atendem fornecedores e clientes, fornecendo e recebendo informações sobre produtos e serviços.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"AUXILIAR DE SERVIÇOS GERAIS","cbo":"514320","ambiente":"LIMPEZA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Químicos: PRODUTOS QUÍMICOS (DOMISSANITÁRIOS)","epis":"LUVA LATEX; PRODUTOS QUÍMICOS (DOMISSANITÁRIOS)","epcs":""},
    {"cargo":"AUXILIAR DE TOPOGRAFIA","cbo":"312320","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Físicos: Radiação Não Ionizante (Exposição ao sol)","epis":"BOTINA DE COURO; RUÍDO; CAPACETE CLASSE B; PROTETOR AURICULAR TIPO PLUG","epcs":"EXTINTORES DE INCENDIO"},
    {"cargo":"COMPRADOR","cbo":"354205","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"COMPRADORA JUNIOR","cbo":"354205","ambiente":"ADMINISTRATIVO","atividades":"Recebem requisições de compras, executam processo de cotação e concretizam a compra de serviços, produtos e equipamentos. Acompanham o fluxo de entregas, desenvolvem fornecedores, supervisionam equipe e processos de compra.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"CONTROLADOR DE MANUTENÇÃO","cbo":"391135","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO","epcs":""},
    {"cargo":"ENCARREGADO","cbo":"710205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"BOTINA DE SEGURANÇA","epcs":""},
    {"cargo":"ENCARREGADO ADMINISTRATIVO","cbo":"411005","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"ENCARREGADO DE EQUIPE","cbo":"710205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"BOTINA DE COURO","epcs":""},
    {"cargo":"ENCARREGADO DE LABORATORIO","cbo":"411005","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"PROTETOR AURICULAR TIPO PLUG; RUÍDO","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"ENCARREGADO DE OBRAS","cbo":"710205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"BOTINA DE COURO","epcs":""},
    {"cargo":"ENCARREGADO DE OBRAS NIVEL I","cbo":"710205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"BOTINA DE COURO","epcs":""},
    {"cargo":"ENCARREGADO DE OBRAS NIVEL II","cbo":"710205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"BOTINA DE COURO","epcs":""},
    {"cargo":"ENCARREGADO DE OBRAS NIVEL III","cbo":"710205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"BOTINA DE COURO","epcs":""},
    {"cargo":"ENCARREGADO DE OBRAS NIVEL IV","cbo":"710205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"BOTINA DE COURO","epcs":""},
    {"cargo":"ENCARREGADO DE RECURSOS HUMANOS","cbo":"252405","ambiente":"ADMINISTRATIVO","atividades":"Responsável pela folha de pagamento, registro de ponto, recrutamento e seleção de funcionários. Administram pessoal e plano de cargos e salários; promovem ações de treinamento e desenvolvimento de pessoal.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"ENCARREGADO DE TOPOGRAFIA","cbo":"312320","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Físicos: Radiação Não Ionizante (Exposição ao sol)","epis":"BOTINA DE COURO; RUÍDO; CAPACETE CLASSE B; PROTETOR AURICULAR TIPO PLUG","epcs":"EXTINTORES DE INCENDIO"},
    {"cargo":"ENCARREGADO DE USINA","cbo":"715420","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},
    {"cargo":"ENCARREGADO GERAL DE OBRAS","cbo":"710205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"BOTINA DE SEGURANÇA","epcs":""},
    {"cargo":"ENCARREGADO NIVEL 1","cbo":"710205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"BOTINA DE SEGURANÇA","epcs":""},
    {"cargo":"ENGENHEIRO CIVIL","cbo":"214205","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos. Físicos: RUÍDO CONTÍNUO OU INTERMITENTE","epis":"BOTINA DE COURO; CAPACETE COM JUGULAR; PROTETOR AURICULAR TIPO PLUG; RUÍDO CONTÍNUO OU INTERMITENTE","epcs":""},
    {"cargo":"GERENTE FINANCEIRO","cbo":"142115","ambiente":"ADMINISTRATIVO","atividades":"Exerce a gerência das operações financeiras da empresa, como previsão de receita, financiamentos, orçamento, créditos e outras, planejando, organizando e controlando os programas e sua execução.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},
    {"cargo":"GESTOR DE CONTRATO","cbo":"710205","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"BOTINA DE COURO; CAPACETE COM JUGULAR; COLETE REFLETIVO","epcs":"EXTINTOR DE INCÊNDIO"},
    {"cargo":"LABORATORISTA","cbo":"301105","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: FUMOS DE ASFALTO","epis":"BOTINA DE COURO; COLETE REFLETIVO; PROTETOR AURICULAR TIPO PLUG; RUÍDO; FUMOS DE ASFALTO","epcs":""},
    {"cargo":"MECÂNICO","cbo":"725205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos","epis":"BOTINA DE COURO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; LUVA DE PROTEÇÃO CONTRA AGENTES QUÍMICOS; CREME PROTETOR; BENZENO","epcs":""},
    {"cargo":"MECÂNICO NIVEL I","cbo":"725205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos","epis":"BOTINA DE COURO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; LUVA DE PROTEÇÃO CONTRA AGENTES QUÍMICOS; CREME PROTETOR; BENZENO","epcs":""},
    {"cargo":"MECÂNICO NIVEL II","cbo":"725205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos","epis":"BOTINA DE COURO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; LUVA DE PROTEÇÃO CONTRA AGENTES QUÍMICOS; CREME PROTETOR; BENZENO","epcs":""},
    {"cargo":"MESISTA","cbo":"715140","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},
    {"cargo":"MESITA DE VIBROACABADORA","cbo":"715140","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},
    {"cargo":"MOTOQUEIRO","cbo":"782310","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: CONDUÇÃO DE VEÍCULOS DE QUALQUER NATUREZA EM VIAS PÚBLICAS. Ergonômicos: Exposição a fatores psicossociais - Pressão temporal","epis":"","epcs":""},
    {"cargo":"MOTORISTA","cbo":"782510","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Exposição a fatores psicossociais - Pressão temporal","epis":"BOTINA DE SEGURANÇA; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; RUÍDO","epcs":""},
    {"cargo":"MOTORISTA CARRETEIRO","cbo":"782510","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Exposição a fatores psicossociais - Pressão temporal","epis":"BOTINA DE SEGURANÇA; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; RUÍDO","epcs":""},
    {"cargo":"MOTORISTA DE CAMINHÃO","cbo":"782510","ambiente":"OPERACIONAL/OBRA","atividades":"Serviço de transporte de materiais em caminhão caçamba.","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Exposição a fatores psicossociais - Pressão temporal","epis":"BOTINA DE SEGURANÇA; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; RUÍDO","epcs":""},
    {"cargo":"MOTORISTA DE CAMINHÃO PIPA","cbo":"782510","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Exposição a fatores psicossociais - Pressão temporal","epis":"BOTINA DE SEGURANÇA; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; RUÍDO","epcs":""},
    {"cargo":"MOTORISTA DE USINA","cbo":"782510","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Exposição a fatores psicossociais - Pressão temporal","epis":"BOTINA DE SEGURANÇA; LUVA VAQUETA; RUÍDO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG","epcs":""},
    {"cargo":"OPERADOR DE BOBCAT","cbo":"711210","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Químicos: Particulados (insolúveis ou de baixa solubilidade) não especiﬁcados de outra maneira (PNOS) - Fração Respirável","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},
    {"cargo":"OPERADOR DE ESCAVADEIRA","cbo":"715115","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},
    {"cargo":"OPERADOR DE ESCAVADEIRA HIDRAULICA","cbo":"715115","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},
    {"cargo":"OPERADOR DE ESPARGIDOR","cbo":"715140","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},
    {"cargo":"OPERADOR DE ESPARGIDOR NÍVEL II","cbo":"715140","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},
    {"cargo":"OPERADOR DE MESA VIBROACABADORA","cbo":"715140","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},
    {"cargo":"OPERADOR DE MINI CARREGADEIRA","cbo":"711210","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},
    {"cargo":"OPERADOR DE MOTONIVELADORA","cbo":"715125","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},
    {"cargo":"OPERADOR DE PÁ CARREGADEIRA","cbo":"711210","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},
    {"cargo":"OPERADOR DE RETROESCAVADEIRA","cbo":"711210","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},
    {"cargo":"OPERADOR DE RETROESCAVADEIRA NIVEL III","cbo":"711210","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},
    {"cargo":"OPERADOR DE ROLO COMPACTADOR","cbo":"715110","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},
    {"cargo":"OPERADOR DE ROLO COMPACTADOR NÍVEL I","cbo":"715110","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},
    {"cargo":"OPERADOR DE ROLO COMPACTADOR NÍVEL II","cbo":"715110","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},
    {"cargo":"OPERADOR DE ROLO COMPACTADOR NÍVEL III","cbo":"715110","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},
    {"cargo":"OPERADOR DE TRATOR","cbo":"715125","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA VAQUETA; LUVA PIGMENTADA (AGENTES MECÂNICOS)","epcs":""},
    {"cargo":"OPERADOR DE USINA","cbo":"715420","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},
    {"cargo":"OPERADOR DE VIBROACABADORA","cbo":"715140","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},
    {"cargo":"PEDREIRO","cbo":"715210","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: CONTATO COM PERFURO CORTANTES. Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Físicos: RUÍDO CONTÍNUO OU INTERMITENTE. Químicos: Particulados (insolúveis ou de baixa solubilidade) não especiﬁcados de outra maneira (PNOS) - Fração Respirável","epis":"LUVA PARA PROTEÇÃO CONTRA AGENTES MECÂNICOS; CONTATO COM PERFURO CORTANTES","epcs":""},
    {"cargo":"RASTELEIRO","cbo":"717020","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},
    {"cargo":"RASTELEIRO NIVEL II","cbo":"717020","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},
    {"cargo":"RASTELEIRO NIVEL III","cbo":"717020","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},
    {"cargo":"RASTELEIRO NÍVEL I","cbo":"717020","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},
    {"cargo":"RECEPCIONISTA","cbo":"422105","ambiente":"ADMINISTRATIVO","atividades":"Atendimento ao cliente presencial e por telefone, direcionamento de cliente ao departamento desejado.","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":""},
    {"cargo":"SERVENTE","cbo":"717020","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: CONTATO COM PERFURO CORTANTES. Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Físicos: RUÍDO CONTÍNUO OU INTERMITENTE","epis":"PROTETOR AURICULAR TIPO PLUG; RUÍDO CONTÍNUO OU INTERMITENTE","epcs":""},
    {"cargo":"SERVIÇOS GERAIS","cbo":"514320","ambiente":"LIMPEZA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Químicos: PRODUTOS QUÍMICOS (DOMISSANITÁRIOS)","epis":"LUVA LATEX; PRODUTOS QUÍMICOS (DOMISSANITÁRIOS)","epcs":""},
    {"cargo":"SOLDADOR","cbo":"724315","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Objetos cortantes e/ou perfurocortantes. Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Químicos: Manganês e seus compostos, fumos","epis":"BOTINA DE COURO; LUVA PARA PROTEÇÃO CONTRA AGENTES MECÂNICOS; ÓCULOS DE PROTEÇÃO INCOLOR; AVENTAL DE RASPA","epcs":"PROTEÇÃO DE PARTES MÓVEIS DE EQUIPAMENTOS"},
    {"cargo":"SOLDADOR NIVEL II","cbo":"724315","ambiente":"OPERACIONAL/OBRA","atividades":"Faz corte de peças metálicas, solda em aço carbono e aço inox, furos, chapas e equipamentos, utilizando processos de soldagem e corte: eletrodo revestido, TIG, MIG, MAG, oxigás, arco submerso, brasagem e plasma.","riscos":"Acidentes / Mecânicos: Objetos cortantes e/ou perfurocortantes. Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Químicos: Manganês e seus compostos, fumos","epis":"BOTINA DE COURO; LUVA PARA PROTEÇÃO CONTRA AGENTES MECÂNICOS; ÓCULOS DE PROTEÇÃO INCOLOR; AVENTAL DE RASPA","epcs":"PROTEÇÃO DE PARTES MÓVEIS DE EQUIPAMENTOS"},
    {"cargo":"SUPERVISOR DE MANUTENÇAO FROTA","cbo":"141605","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":""},
    {"cargo":"TECNICO EM CONSTRUÇÃO CIVIL","cbo":"710205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"PROTETOR AURICULAR TIPO PLUG; RUÍDO","epcs":""},
    {"cargo":"TECNICO EM SEGURANÇA DO TRABALHO","cbo":"351605","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para a ocorrência de acidentes (Quedas, tropeções, escorregões, incisões, perfurações, atropelamento). Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"BOTINA DE COURO; CAPACETE COM JUGULAR; PROTETOR AURICULAR TIPO PLUG","epcs":""},
    {"cargo":"TOPÓGRAFO","cbo":"312320","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Físicos: Radiação Não Ionizante (Exposição ao sol)","epis":"RUÍDO; BOTINA DE COURO; CAPACETE CLASSE B; PROTETOR AURICULAR TIPO PLUG","epcs":"EXTINTORES DE INCENDIO"},
    {"cargo":"VIGIA NOTURNO","cbo":"517420","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Trabalho com necessidade de variação de turnos","epis":"","epcs":""},
    {"cargo":"AJUDANTE B","cbo":"717020","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Químicos: POEIRA MINERAL","epis":"BOTINA DE SEGURANÇA; RUÍDO; LUVA PARA PROTEÇÃO CONTRA AGENTES MECÂNICOS; AVENTAL DE RASPA; LUVA DE PROTEÇÃO CONTRA AGENTES QUÍMICOS","epcs":""},  # mapeado de: AJUDANTE A
    {"cargo":"ANALISTA DE DEPARTAMENTO PESSOAL NIVEL I","cbo":"252405","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},  # mapeado de: ANALISTA DE DEPARTAMENTO PESSOAL
    {"cargo":"ANALISTA DE INTEGRIDADE","cbo":"252405","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},  # mapeado de: ANALISTA DE CONTROLADORIA
    {"cargo":"ANALISTA FINANCEIRO","cbo":"252405","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},  # mapeado de: ANALISTA FINANCEIRA
    {"cargo":"APONTADOR A","cbo":"414210","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Físicos: RADIAÇÃO NÃO IONIZANTE","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; RUÍDO","epcs":""},  # mapeado de: APONTADOR
    {"cargo":"ARMADOR","cbo":"715210","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: CONTATO COM PERFURO CORTANTES. Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Físicos: RUÍDO CONTÍNUO OU INTERMITENTE. Químicos: Particulados (insolúveis ou de baixa solubilidade) não especiﬁcados de outra maneira (PNOS) - Fração Respirável","epis":"LUVA PARA PROTEÇÃO CONTRA AGENTES MECÂNICOS; CONTATO COM PERFURO CORTANTES","epcs":""},  # mapeado de: PEDREIRO
    {"cargo":"ASSISTENTE DE DEPARTAMENTO PESSOAL","cbo":"411005","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},  # mapeado de: ASSISTENTE DEPARTAMENTO PESSOAL
    {"cargo":"ASSISTENTE DE ENGENHARIA","cbo":"411005","ambiente":"ADMINISTRATIVO","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},  # mapeado de: ASSISTENTE ADMINISTRATIVO
    {"cargo":"AUXILIAR DE MECANICO III","cbo":"725205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos","epis":"BOTINA DE COURO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; LUVA DE PROTEÇÃO CONTRA AGENTES QUÍMICOS; CREME PROTETOR; BENZENO","epcs":""},  # mapeado de: AUXILIAR DE MECÂNICO NIVEL II
    {"cargo":"ENCARREGADA DE RECURSOS HUMANOS","cbo":"252405","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"","epcs":"VENTILAÇÃO ARTIFICIAL, EXTINTOR DE INCÊNDIO"},  # mapeado de: ENCARREGADO DE RECURSOS HUMANOS
    {"cargo":"MECANICO NIVEL II","cbo":"725205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos","epis":"BOTINA DE COURO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; LUVA DE PROTEÇÃO CONTRA AGENTES QUÍMICOS; CREME PROTETOR; BENZENO","epcs":""},  # mapeado de: MECÂNICO NIVEL II
    {"cargo":"MECANICO NIVEL III","cbo":"725205","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Ergonômicos: Biomecânicos - Postura em pé por longos períodos","epis":"BOTINA DE COURO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; LUVA DE PROTEÇÃO CONTRA AGENTES QUÍMICOS; CREME PROTETOR; BENZENO","epcs":""},  # mapeado de: MECÂNICO NIVEL II
    {"cargo":"MESISTA  VIBROACABADORA","cbo":"715140","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},  # mapeado de: MESITA DE VIBROACABADORA
    {"cargo":"MOTORISTA DE ESPAGIDOR","cbo":"782510","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Exposição a fatores psicossociais - Pressão temporal","epis":"BOTINA DE SEGURANÇA; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; RUÍDO","epcs":""},  # mapeado de: MOTORISTA DE CAMINHÃO
    {"cargo":"OPERADOR DE CALDEIRA","cbo":"715420","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},  # mapeado de: OPERADOR DE USINA
    {"cargo":"OPERADOR DE ESPAGIDOR NIVEL II","cbo":"715140","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},  # mapeado de: OPERADOR DE ESPARGIDOR NÍVEL II
    {"cargo":"OPERADOR DE PA CARREGADEIRA NIVEL II","cbo":"711210","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},  # mapeado de: OPERADOR DE PÁ CARREGADEIRA
    {"cargo":"OPERADOR DE RETROESCAVADEIRA NIVEL II","cbo":"711210","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para ocorrer acidente. Ergonômicos: Biomecânicos - Postura sentada por longos períodos","epis":"BOTINA DE COURO; LUVA PIGMENTADA (AGENTES MECÂNICOS); PROTETOR AURICULAR TIPO PLUG; PROTETOR AURCULAR TIPO CONCHA; RUÍDO","epcs":""},  # mapeado de: OPERADOR DE RETROESCAVADEIRA
    {"cargo":"OPERADOR DE USINA NIVEL II","cbo":"715420","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: ACIDENTES DE TRÂNSITO. Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho. Químicos: CIMENTO ASFÁLTICO DE PETRÓLEO","epis":"BOTINA DE COURO; CIMENTO ASFÁLTICO DE PETRÓLEO; RUÍDO; PROTETOR AURICULAR TIPO PLUG; ÓCULOS DE PROTEÇÃO INCOLOR","epcs":"PLACAS DE TRÂNSITO, CONES REFLETIVOS"},  # mapeado de: OPERADOR DE USINA
    {"cargo":"PEDREIRO NIVEL II","cbo":"715210","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: CONTATO COM PERFURO CORTANTES. Ergonômicos: Biomecânicos - Postura em pé por longos períodos. Físicos: RUÍDO CONTÍNUO OU INTERMITENTE. Químicos: Particulados (insolúveis ou de baixa solubilidade) não especiﬁcados de outra maneira (PNOS) - Fração Respirável","epis":"LUVA PARA PROTEÇÃO CONTRA AGENTES MECÂNICOS; CONTATO COM PERFURO CORTANTES","epcs":""},  # mapeado de: PEDREIRO
    {"cargo":"TEC. SEGURANÇA DO TRABALHO","cbo":"351605","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para a ocorrência de acidentes (Quedas, tropeções, escorregões, incisões, perfurações, atropelamento). Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"BOTINA DE COURO; CAPACETE COM JUGULAR; PROTETOR AURICULAR TIPO PLUG","epcs":""},  # mapeado de: TECNICO EM SEGURANÇA DO TRABALHO
    {"cargo":"TECNICO SEGURANCA DO TRABALHO","cbo":"351605","ambiente":"OPERACIONAL/OBRA","atividades":"","riscos":"Acidentes / Mecânicos: Outras situações de risco que poderão contribuir para a ocorrência de acidentes (Quedas, tropeções, escorregões, incisões, perfurações, atropelamento). Ergonômicos: Organizacionais - Necessidade de manter ritmos intensos de trabalho","epis":"BOTINA DE COURO; CAPACETE COM JUGULAR; PROTETOR AURICULAR TIPO PLUG","epcs":""},  # mapeado de: TECNICO EM SEGURANÇA DO TRABALHO
]

@app.get("/api/pgr")
async def listar_pgr(_=Depends(verificar_acesso)):
    return banco.listar_pgr()

@app.get("/api/pgr/cargo/{cargo}")
async def buscar_pgr_cargo(cargo: str, _=Depends(verificar_acesso)):
    dados = banco.buscar_pgr_cargo(cargo)
    return dados if dados else {}

@app.post("/api/pgr/seed")
async def seed_pgr(_=Depends(verificar_acesso)):
    """Importa todos os dados do PGR para o banco."""
    for item in PGR_SEED:
        banco.salvar_pgr_cargo(**item)
    return {"ok": True, "total": len(PGR_SEED)}

@app.post("/api/pgr/cargo/{cargo}")
async def salvar_pgr_cargo(cargo: str, dados: dict, _=Depends(verificar_acesso)):
    banco.salvar_pgr_cargo(
        cargo=cargo,
        cbo=dados.get("cbo",""),
        ambiente=dados.get("ambiente",""),
        atividades=dados.get("atividades",""),
        riscos=dados.get("riscos",""),
        epis=dados.get("epis",""),
        epcs=dados.get("epcs",""),
    )
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


@app.get("/api/ficha-epi/{cargo}/download")
async def download_ficha_epi(cargo: str, _=Depends(verificar_acesso)):
    from fastapi.responses import Response
    conteudo = banco.buscar_modelo(EPI_DOC_ID, cargo=cargo)
    if not conteudo:
        raise HTTPException(status_code=404, detail="Ficha de EPI não gerada para este cargo. Salve a configuração da OS primeiro.")
    return Response(content=conteudo,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="FichaEPI_{cargo}.docx"'})


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

    # Extrai "Descrição Sumária" — apenas o primeiro parágrafo
    import re as _re
    m = _re.search(r"Descri[çc][ãa]o\s+Sum[aá]ria\s*(.*?)(?=Todos os direitos|$)", texto, _re.DOTALL)
    descricao_full = m.group(1).strip() if m else ""
    # Pega só o primeiro parágrafo (antes de "Esta família não compreende" ou linha em branco dupla)
    descricao = _re.split(r"\n{2,}|Esta fam[íi]lia n[ãa]o compreende", descricao_full)[0].strip()

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


def _cbo_enriquecer(cbo_codigo: str) -> tuple[str, str]:
    """Dado um código CBO (4-6 dígitos), retorna (titulo, descricao_sumaria) do MTE.
    Usa os 4 primeiros dígitos para buscar a família; tenta achar o título exato.
    Retorna ('', '') em caso de falha."""
    try:
        familia = str(cbo_codigo).replace("-", "").replace(".", "")[:4]
        data = _cbo_buscar_descricao(familia)
        if not data:
            return "", ""
        titulo = ""
        codigo_limpo = str(cbo_codigo).replace("-", "").replace(".", "")
        for t in data.get("titulos", []):
            if t.get("codigo", "").replace("-", "").replace(".", "") == codigo_limpo:
                titulo = t["titulo"]
                break
        if not titulo:
            titulo = data.get("nome_familia", "")
        return titulo, data.get("descricao_sumaria", "")
    except Exception:
        return "", ""


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


@app.get("/api/os/config/debug")
async def debug_os_config(_=Depends(verificar_acesso)):
    """Diagnóstico temporário."""
    try:
        cargos = banco.buscar_cargos()
        cbos   = banco.listar_cargos_cbo()
        return {"total_cargos": len(cargos), "sample": cargos[:3], "total_cbos": len(cbos)}
    except Exception as e:
        return {"erro": str(e)}


@app.get("/api/os/config")
async def listar_os_config(_=Depends(verificar_acesso)):
    """Lista cargos agrupados por CBO — um representante por CBO."""
    try:
        cargos  = banco.buscar_cargos()
        cbo_map = {c["cargo"].upper(): c for c in banco.listar_cargos_cbo()}

        import re as _re
        import unicodedata as _ud

        def _sem_acento(s):
            return ''.join(c for c in _ud.normalize('NFD', s) if _ud.category(c) != 'Mn')

        def _chave_grupo(cargo, codigo):
            # Agrupa por CBO se disponível, senão por nome normalizado
            if codigo:
                return f"cbo:{codigo}"
            # Remove acentos e sufixos de nível (NIVEL I, NÍVEL II, NV III, etc.)
            norm = _sem_acento(cargo.upper())
            norm = _re.sub(r'\s+(NIVEL|NV)\s*(I{1,3}V?|IV|\d+)\s*$', '', norm).strip()
            return f"nome:{norm}"

        grupos = {}   # chave -> item escolhido

        for cargo in cargos:
            cbo = cbo_map.get(cargo.upper(), {})
            codigo = (cbo.get("cbo_codigo") or "").strip()
            configurado = bool(cbo.get("cbo_codigo") or cbo.get("cbo_descricao"))
            item = {
                "cargo":         cargo,
                "cbo_codigo":    cbo.get("cbo_codigo", ""),
                "cbo_titulo":    cbo.get("cbo_titulo", ""),
                "cbo_descricao": cbo.get("cbo_descricao", ""),
                "configurado":   configurado,
                "variantes":     [],
            }
            chave = _chave_grupo(cargo, codigo)
            if chave not in grupos:
                grupos[chave] = item
            else:
                atual = grupos[chave]
                atual["variantes"].append(cargo)
                if configurado and not atual["configurado"]:
                    item["variantes"] = atual["variantes"]
                    grupos[chave] = item

        result = sorted(grupos.values(), key=lambda x: x["cargo"])
        return result
    except Exception as e:
        print(f"[ERRO /api/os/config] {e}")
        raise HTTPException(500, str(e))


@app.get("/api/os/config/{cargo}")
async def get_os_config_cargo(cargo: str, _=Depends(verificar_acesso)):
    cbo  = banco.buscar_cargo_cbo(cargo)
    epis = banco.listar_epis_do_cargo(cargo)
    pgr  = banco.buscar_pgr_cargo(cargo)  # já faz fallback por normalização
    return {
        "cargo": cargo,
        "cbo":   cbo or {"cbo_codigo": "", "cbo_titulo": "", "cbo_descricao": ""},
        "epis":  epis,
        "pgr": {
            "riscos":     pgr.get("riscos", ""),
            "epis":       pgr.get("epis", ""),
            "atividades": pgr.get("atividades", ""),
            "ambiente":   pgr.get("ambiente", ""),
            "cargo_ref":  pgr.get("cargo", ""),  # cargo que foi efetivamente encontrado no PGR
        },
    }


@app.post("/api/os/config/{cargo}")
async def salvar_os_config_cargo(cargo: str, dados: dict, _=Depends(verificar_acesso)):
    """Salva configuração de CBO para um cargo e gera a OS modelo.
    Propaga automaticamente para variantes de nível (NIVEL I, II, III) do mesmo cargo.
    """
    cbo_codigo    = dados.get("cbo_codigo", "")
    cbo_titulo    = dados.get("cbo_titulo", "")
    cbo_descricao = dados.get("cbo_descricao", "")
    riscos        = dados.get("riscos", "")

    banco.salvar_cargo_cbo(cargo, cbo_codigo, cbo_titulo, cbo_descricao)

    # Propaga para todos os cargos com o mesmo CBO (mesma função, nomes diferentes)
    todos_cargos = banco.buscar_cargos()
    propagados = []
    if cbo_codigo:
        for c in todos_cargos:
            if c == cargo:
                continue
            existente = banco.buscar_cargo_cbo(c)
            cbo_c = (existente or {}).get("cbo_codigo", "")
            # Mesmo CBO → propaga EPIs, riscos, OS e Ficha EPI
            if cbo_c and cbo_c.split("-")[0].strip() == cbo_codigo.split("-")[0].strip():
                # Propaga CBO/título/descrição
                banco.salvar_cargo_cbo(c, cbo_codigo, cbo_titulo, cbo_descricao)
                # Propaga EPIs
                if epis:
                    banco.salvar_cargo_epis(c, [{"epi_id": e["epi_id"], "quantidade": e.get("quantidade", 1)} for e in epis])
                # Propaga riscos no PGR
                if riscos_detalhe:
                    pgr_c = banco.buscar_pgr_cargo(c) or {}
                    banco.salvar_pgr_cargo(
                        cargo=c, cbo=cbo_codigo,
                        ambiente=pgr_c.get("ambiente", ""),
                        atividades=pgr_c.get("atividades", cbo_descricao),
                        riscos=riscos_detalhe,
                        epis=pgr_c.get("epis", ""),
                        epcs=pgr_c.get("epcs", ""),
                    )
                # Gera OS e Ficha EPI para o cargo variante
                func_c = {"nome": "{{NOME}}", "cpf": "{{CPF}}", "matricula": "{{MATRICULA}}",
                          "cargo": c, "lotacao": "{{LOTACAO}}", "admissao": "{{DATA_ADMISSAO}}", "rg": "{{RG}}"}
                if modelo_base := banco.buscar_modelo("03_os_base"):
                    epis_c = banco.listar_epis_do_cargo(c)
                    pgr_c2 = banco.buscar_pgr_cargo(c)
                    riscos_c = pgr_c2.get("riscos", "") if pgr_c2 else ""
                    os_bytes_c = processador.preencher_os_dinamica(func_c, cbo_descricao, _formatar_epis_texto(epis_c), modelo_base, riscos_texto=riscos_c)
                    banco.salvar_modelo(OS_DOC_ID, "Ordem de Serviço", os_bytes_c, cargo=c)
                    modelo_epi_b = banco.buscar_modelo("10_ficha_controle_epi")
                    if modelo_epi_b and epis_c:
                        ficha_c = processador.preencher_ficha_epi_dinamica(func_c, epis_c, modelo_epi_b)
                        banco.salvar_modelo(EPI_DOC_ID, "Ficha de Controle de EPI", ficha_c, cargo=c)
                propagados.append(c)

    # Salva riscos no PGR (substitui — não acumula)
    riscos_detalhe = dados.get("riscos_detalhe", "").strip()
    if riscos_detalhe:
        pgr_atual = banco.buscar_pgr_cargo(cargo) or {}
        banco.salvar_pgr_cargo(
            cargo=cargo,
            cbo=pgr_atual.get("cbo", cbo_codigo),
            ambiente=pgr_atual.get("ambiente", ""),
            atividades=pgr_atual.get("atividades", cbo_descricao),
            riscos=riscos_detalhe,
            epis=pgr_atual.get("epis", ""),
            epcs=pgr_atual.get("epcs", ""),
        )

    # Gera a OS modelo para o cargo (sem funcionário — com placeholders)
    modelo_base = banco.buscar_modelo("03_os_base")
    if not modelo_base:
        return {"ok": True, "aviso": "CBO salvo. Modelo base da OS não encontrado para gerar prévia."}

    # Monta texto de EPIs (já devem estar salvos antes de chegar aqui)
    epis = banco.listar_epis_do_cargo(cargo)
    epis_texto = _formatar_epis_texto(epis)

    # Pega riscos do PGR (texto completo, não o resumo do campo de detalhe)
    pgr_para_os = banco.buscar_pgr_cargo(cargo)
    riscos_para_os = pgr_para_os.get("riscos", "") if pgr_para_os else ""

    # Funcionário fictício para template de cargo
    func_template = {
        "nome": "{{NOME}}", "cpf": "{{CPF}}", "matricula": "{{MATRICULA}}",
        "cargo": cargo, "lotacao": "{{LOTACAO}}", "admissao": "{{DATA_ADMISSAO}}",
        "rg": "{{RG}}",
    }
    docx_bytes = processador.preencher_os_dinamica(
        func_template, cbo_descricao, epis_texto, modelo_base, riscos_texto=riscos_para_os
    )
    banco.salvar_modelo(OS_DOC_ID, "Ordem de Serviço", docx_bytes, cargo=cargo)

    # Gera Ficha de EPI automaticamente com os mesmos EPIs da OS
    modelo_epi_base = banco.buscar_modelo("10_ficha_controle_epi")
    if modelo_epi_base and epis:
        ficha_bytes = processador.preencher_ficha_epi_dinamica(func_template, epis, modelo_epi_base)
        banco.salvar_modelo(EPI_DOC_ID, "Ficha de Controle de EPI", ficha_bytes, cargo=cargo)

    # Auto-adiciona OS e Ficha EPI ao kit do cargo
    from config import KIT_PADRAO
    docs = banco.docs_do_cargo(cargo)
    extras = [d for d in docs if d not in KIT_PADRAO]
    if OS_DOC_ID not in extras:
        extras.append(OS_DOC_ID)
    if epis and EPI_DOC_ID not in extras:
        extras.append(EPI_DOC_ID)
    banco.salvar_docs_cargo(cargo, extras)

    return {"ok": True, "cargo": cargo, "epis_count": len(epis),
            "propagado_para": propagados}


def _formatar_epis_texto(epis: list) -> str:
    """Formata lista de EPIs para texto da OS — só nome do item, sem quantidade."""
    if not epis:
        return ""
    linhas = []
    for e in epis:
        desc = e.get("descricao", "")
        ca   = e.get("ca", "")
        if ca:
            linhas.append(f"- {desc} (C.A: {ca})")
        else:
            linhas.append(f"- {desc}")
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

        pgr = banco.buscar_pgr_cargo(f["cargo"])
        riscos_texto = pgr.get("riscos", "") if pgr else ""

        docx_bytes = processador.preencher_os_dinamica(f, cbo_descricao, epis_texto, modelo_bytes, riscos_texto=riscos_texto)

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

    nome_doc = f"Ficha de Entrega de EPI/EPC/UNIFORMES — {f['nome']}"
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

        nome_doc = f"Ficha de Entrega de EPI/EPC/UNIFORMES — {f['nome']}"
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
    """Atualiza o status de todos os envios não-assinados consultando o ZapSign."""
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


# ── ALOJAMENTOS ───────────────────────────────────────────────────────────────

@app.post("/api/alojamentos/vistorias")
async def criar_vistoria_alojamento(dados: dict, req: Request, _=Depends(verificar_acesso)):
    usuario = getattr(req.state, 'usuario', '')
    itens = dados.pop('itens', [])
    fotos = dados.pop('fotos', [])
    plano = dados.pop('plano_acao', [])
    vid = banco.salvar_vistoria_alojamento(dados, usuario)
    banco.salvar_itens_vistoria(vid, itens)
    banco.salvar_fotos_vistoria(vid, fotos)
    banco.salvar_plano_acao_vistoria(vid, plano)
    return {"ok": True, "id": vid}


@app.get("/api/alojamentos/vistorias")
async def listar_vistorias_alojamento(_=Depends(verificar_acesso)):
    return banco.listar_vistorias_alojamento()


@app.get("/api/alojamentos/vistorias/{vid}")
async def buscar_vistoria_alojamento(vid: int, _=Depends(verificar_acesso)):
    v = banco.buscar_vistoria_alojamento(vid)
    if not v:
        raise HTTPException(404, "Vistoria não encontrada")
    return v


@app.delete("/api/alojamentos/vistorias/{vid}")
async def deletar_vistoria_alojamento(vid: int, _=Depends(verificar_acesso)):
    banco.deletar_vistoria_alojamento(vid)
    return {"ok": True}


@app.get("/api/alojamentos/vistorias/{vid}/pdf")
async def gerar_pdf_vistoria(vid: int, _=Depends(verificar_acesso)):
    v = banco.buscar_vistoria_alojamento(vid)
    if not v:
        raise HTTPException(404, "Vistoria não encontrada")

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        import io, base64
        try:
            from PIL import Image as PILImage
            _pillow_ok = True
        except ImportError:
            _pillow_ok = False

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                                topMargin=1.5*cm, bottomMargin=1.5*cm)
        styles = getSampleStyleSheet()
        W = A4[0] - 3*cm

        title_style = ParagraphStyle('title', fontSize=13, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=4)
        sub_style   = ParagraphStyle('sub',   fontSize=10, fontName='Helvetica',      alignment=TA_CENTER, spaceAfter=8)
        sec_style   = ParagraphStyle('sec',   fontSize=10, fontName='Helvetica-Bold', spaceAfter=4, spaceBefore=8,
                                     backColor=colors.HexColor('#1F4E79'), textColor=colors.white, leftIndent=4)
        body_style  = ParagraphStyle('body',  fontSize=9,  fontName='Helvetica', spaceAfter=3)
        small_style = ParagraphStyle('small', fontSize=8,  fontName='Helvetica', textColor=colors.grey)

        story = []

        # Cabeçalho
        story.append(Paragraph("RELATÓRIO DE VISTORIA DE ALOJAMENTO", title_style))
        story.append(Paragraph("JS Construtora e Locadora Ltda — CNPJ: 16.910.656/0001-81 | CNAE: 4211-1/01 | GR: 4", sub_style))

        resultado_map = {'conforme': 'CONFORME', 'nao_conforme': 'NÃO CONFORME', 'conforme_ressalvas': 'CONFORME COM RESSALVAS'}
        res_label = resultado_map.get(v.get('resultado','conforme'), v.get('resultado',''))
        res_color = {'CONFORME': colors.HexColor('#16a34a'), 'NÃO CONFORME': colors.HexColor('#dc2626'),
                     'CONFORME COM RESSALVAS': colors.HexColor('#d97706')}.get(res_label, colors.black)

        # Identificação
        story.append(Paragraph("IDENTIFICAÇÃO", sec_style))
        id_data = [
            ["Frente / Contrato:", v.get('frente_servico','') + (' — ' + v.get('contrato','') if v.get('contrato') else ''),
             "Data:", v.get('data_vistoria','')],
            ["Localização:", v.get('localizacao',''), "Nº Trabalhadores:", str(v.get('num_trabalhadores',''))],
            ["Responsável:", v.get('responsavel',''), "Cargo:", v.get('cargo_responsavel','')],
            ["Encarregado:", v.get('encarregado',''), "", ""],
        ]
        id_table = Table(id_data, colWidths=[3.5*cm, 8*cm, 3*cm, 4*cm])
        id_table.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
            ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('GRID', (0,0), (-1,-1), 0.3, colors.lightgrey),
            ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#f1f5f9')),
            ('BACKGROUND', (2,0), (2,-1), colors.HexColor('#f1f5f9')),
            ('PADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(id_table)
        story.append(Spacer(1, 8))

        # Blocos
        BLOCOS = [
            (1, "DORMITÓRIOS (NR-24, item 24.7.2 e 24.7.3)", [
                "1.1","1.2","1.3","1.4","1.5","1.6","1.7","1.8","1.9","1.10","1.11","1.12","1.13","1.14"]),
            (2, "INSTALAÇÕES SANITÁRIAS (NR-18 / NR-24)", ["2.1","2.2","2.3","2.4","2.5","2.6"]),
            (3, "REFEITÓRIO / LOCAL DE REFEIÇÕES", ["3.1","3.2","3.3","3.4"]),
            (4, "LAVANDERIA (NR-18 / NR-24)", ["4.1","4.2"]),
            (5, "ÁREA DE LAZER (NR-18)", ["5.1"]),
            (6, "ABASTECIMENTO DE ÁGUA (NR-18)", ["6.1","6.2","6.3","6.4"]),
            (7, "HIGIENE E CONSERVAÇÃO GERAL (NR-24)", ["7.1","7.2","7.3","7.4","7.5","7.6"]),
            (8, "SAÚDE E SEGURANÇA NO ALOJAMENTO (NR-24)", ["8.1","8.2","8.3","8.4"]),
        ]

        itens_map = {i['item_num']: i for i in (v.get('itens') or [])}
        status_labels = {'c': 'C', 'nc': 'NC', 'na': 'N/A', '': 'N/A'}
        status_colors = {'c': colors.HexColor('#dcfce7'), 'nc': colors.HexColor('#fee2e2'),
                         'na': colors.HexColor('#f1f5f9'), '': colors.HexColor('#f1f5f9')}

        for bloco_num, bloco_nome, item_nums in BLOCOS:
            story.append(Paragraph(f"BLOCO {bloco_num} — {bloco_nome}", sec_style))
            tdata = [["Nº", "Item verificado", "Status", "Observação"]]
            for num in item_nums:
                item = itens_map.get(num, {})
                st = item.get('status', 'na')
                tdata.append([num, Paragraph(item.get('descricao', num), body_style),
                               status_labels.get(st, 'N/A'), Paragraph(item.get('observacao',''), small_style)])

            t = Table(tdata, colWidths=[1.2*cm, 10*cm, 1.5*cm, W-12.7*cm])
            ts = TableStyle([
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1F4E79')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('GRID', (0,0), (-1,-1), 0.3, colors.lightgrey),
                ('ALIGN', (2,0), (2,-1), 'CENTER'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('PADDING', (0,0), (-1,-1), 4),
            ])
            for row_i, num in enumerate(item_nums, 1):
                item = itens_map.get(num, {})
                st = item.get('status','na')
                ts.add('BACKGROUND', (2, row_i), (2, row_i), status_colors.get(st, colors.white))
            t.setStyle(ts)
            story.append(t)
            story.append(Spacer(1, 4))

        # Resultado
        story.append(Spacer(1, 6))
        story.append(Paragraph("RESULTADO DA VISTORIA", sec_style))
        res_data = [["Resultado:", Paragraph(f'<b>{res_label}</b>', body_style),
                     "Prazo regularização:", v.get('prazo_regularizacao','')]]
        rt = Table(res_data, colWidths=[4*cm, 6*cm, 4*cm, W-14*cm])
        rt.setStyle(TableStyle([
            ('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),('FONTNAME',(2,0),(2,-1),'Helvetica-Bold'),
            ('FONTSIZE',(0,0),(-1,-1),9),('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),
            ('BACKGROUND',(0,0),(0,-1),colors.HexColor('#f1f5f9')),
            ('BACKGROUND',(2,0),(2,-1),colors.HexColor('#f1f5f9')),
            ('PADDING',(0,0),(-1,-1),5),
        ]))
        story.append(rt)

        # Observação geral
        if v.get('observacao_geral'):
            story.append(Spacer(1, 6))
            story.append(Paragraph("OBSERVAÇÃO GERAL", sec_style))
            story.append(Paragraph(v['observacao_geral'], body_style))

        # Plano de ação
        plano = v.get('plano_acao') or []
        if plano:
            story.append(Spacer(1, 6))
            story.append(Paragraph("PLANO DE AÇÃO (não conformidades)", sec_style))
            pa_data = [["Nº NC","Descrição","Responsável","Prazo","Status"]]
            for a in plano:
                pa_data.append([a.get('num_nc',''), Paragraph(a.get('descricao',''), body_style),
                                 a.get('responsavel',''), a.get('prazo',''), a.get('status_acao','')])
            pat = Table(pa_data, colWidths=[1.5*cm, 7*cm, 3.5*cm, 2.5*cm, 2.5*cm])
            pat.setStyle(TableStyle([
                ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),
                ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#1F4E79')),
                ('TEXTCOLOR',(0,0),(-1,0),colors.white),
                ('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),
                ('PADDING',(0,0),(-1,-1),4),
            ]))
            story.append(pat)

        # Assinaturas
        story.append(Spacer(1, 16))
        story.append(Paragraph("ASSINATURAS", sec_style))
        sig_data = [
            ["Responsável pela vistoria", "", "Encarregado / Representante"],
            [f"Nome: {v.get('responsavel','')}", "", f"Nome: {v.get('encarregado','')}"],
            [f"Cargo: {v.get('cargo_responsavel','')}", "", "Cargo: ___________________________"],
            [f"Data: {v.get('data_vistoria','')}", "", f"Data: {v.get('data_vistoria','')}"],
            ["Assinatura: ______________________", "", "Assinatura: ______________________"],
        ]
        sigt = Table(sig_data, colWidths=[(W/2-0.5*cm), 1*cm, (W/2-0.5*cm)])
        sigt.setStyle(TableStyle([
            ('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTSIZE',(0,0),(-1,-1),9),
            ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
            ('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
            ('LINEBELOW',(0,0),(0,0),0.5,colors.black),('LINEBELOW',(2,0),(2,0),0.5,colors.black),
            ('PADDING',(0,0),(-1,-1),4),
        ]))
        story.append(sigt)

        # Fotos
        fotos = v.get('fotos') or []
        if fotos:
            story.append(Spacer(1, 10))
            story.append(Paragraph("REGISTRO FOTOGRÁFICO", sec_style))
            foto_row = []
            for foto in fotos:
                try:
                    b64 = foto.get('dados_base64','')
                    if not b64:
                        continue
                    if ',' in b64:
                        b64 = b64.split(',', 1)[1]
                    img_bytes = base64.b64decode(b64)
                    img_buf = io.BytesIO(img_bytes)
                    if _pillow_ok:
                        pil = PILImage.open(img_buf)
                        # Converter para RGB se necessário (PNG com alpha, WEBP etc.)
                        if pil.mode not in ('RGB', 'L'):
                            pil = pil.convert('RGB')
                        pil.thumbnail((600, 450), PILImage.LANCZOS)
                        out = io.BytesIO()
                        pil.save(out, format='JPEG', quality=85)
                        out.seek(0)
                        # Calcular dimensões mantendo proporção
                        w, h = pil.size
                        max_w, max_h = 8*cm, 6*cm
                        ratio = min(max_w/w, max_h/h) if w and h else 1
                        rl_img = RLImage(out, width=w*ratio, height=h*ratio)
                    else:
                        rl_img = RLImage(img_buf, width=8*cm, height=6*cm)
                    foto_row.append(rl_img)
                    if len(foto_row) == 2:
                        ft = Table([foto_row], colWidths=[9*cm, 9*cm])
                        ft.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('PADDING',(0,0),(-1,-1),6)]))
                        story.append(ft)
                        story.append(Spacer(1,6))
                        foto_row = []
                except Exception as e:
                    print(f"ERRO ao processar foto na vistoria {vid}: {e}")
            if foto_row:
                while len(foto_row) < 2:
                    foto_row.append(Paragraph('', body_style))
                ft = Table([foto_row], colWidths=[9*cm, 9*cm])
                ft.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('PADDING',(0,0),(-1,-1),6)]))
                story.append(ft)

        doc.build(story)
        buf.seek(0)
        fname = f"vistoria_alojamento_{vid}_{v.get('data_vistoria','').replace('/','')}.pdf"
        return StreamingResponse(buf, media_type="application/pdf",
                                 headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    except ImportError:
        raise HTTPException(500, "Biblioteca reportlab não instalada no servidor")
    except Exception as e:
        raise HTTPException(500, f"Erro ao gerar PDF: {e}")


@app.post("/api/alojamentos/vistorias/{vid}/enviar")
async def enviar_vistoria_assinatura(vid: int, dados: dict = {}, _=Depends(verificar_acesso)):
    v = banco.buscar_vistoria_alojamento(vid)
    if not v:
        raise HTTPException(404, "Vistoria não encontrada")

    encarregado_nome = v.get('encarregado', '').strip()
    if not encarregado_nome:
        raise HTTPException(400, "Encarregado não informado na vistoria")

    sandbox = dados.get("sandbox", False)

    # Gera o PDF em memória (reutiliza lógica do endpoint de PDF)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        import io as _io, base64 as _b64, tempfile, os as _os
        try:
            from PIL import Image as PILImage
            _pillow_ok = True
        except ImportError:
            _pillow_ok = False

        buf = _io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                                topMargin=1.5*cm, bottomMargin=1.5*cm)
        styles = getSampleStyleSheet()
        W = A4[0] - 3*cm

        title_style = ParagraphStyle('title2', fontSize=13, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=4)
        sub_style   = ParagraphStyle('sub2',   fontSize=10, fontName='Helvetica',      alignment=TA_CENTER, spaceAfter=8)
        sec_style   = ParagraphStyle('sec2',   fontSize=10, fontName='Helvetica-Bold', spaceAfter=4, spaceBefore=8,
                                     backColor=colors.HexColor('#1F4E79'), textColor=colors.white, leftIndent=4)
        body_style  = ParagraphStyle('body2',  fontSize=9,  fontName='Helvetica', spaceAfter=3)
        small_style = ParagraphStyle('small2', fontSize=8,  fontName='Helvetica', textColor=colors.grey)

        story = []
        story.append(Paragraph("RELATÓRIO DE VISTORIA DE ALOJAMENTO", title_style))
        story.append(Paragraph("JS Construtora e Locadora Ltda — CNPJ: 16.910.656/0001-81 | CNAE: 4211-1/01 | GR: 4", sub_style))

        resultado_map = {'conforme': 'CONFORME', 'nao_conforme': 'NÃO CONFORME', 'conforme_ressalvas': 'CONFORME COM RESSALVAS'}
        res_label = resultado_map.get(v.get('resultado','conforme'), v.get('resultado',''))

        story.append(Paragraph("IDENTIFICAÇÃO", sec_style))
        id_data = [
            ["Frente / Contrato:", v.get('frente_servico','') + (' — ' + v.get('contrato','') if v.get('contrato') else ''),
             "Data:", v.get('data_vistoria','')],
            ["Localização:", v.get('localizacao',''), "Nº Trabalhadores:", str(v.get('num_trabalhadores',''))],
            ["Responsável:", v.get('responsavel',''), "Cargo:", v.get('cargo_responsavel','')],
            ["Encarregado:", v.get('encarregado',''), "", ""],
        ]
        id_table = Table(id_data, colWidths=[3.5*cm, 8*cm, 3*cm, 4*cm])
        id_table.setStyle(TableStyle([
            ('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),
            ('FONTNAME',(2,0),(2,-1),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),
            ('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),
            ('BACKGROUND',(0,0),(0,-1),colors.HexColor('#f1f5f9')),
            ('BACKGROUND',(2,0),(2,-1),colors.HexColor('#f1f5f9')),
            ('PADDING',(0,0),(-1,-1),4),
        ]))
        story.append(id_table)
        story.append(Spacer(1, 8))

        BLOCOS = [
            (1,"DORMITÓRIOS (NR-24, item 24.7.2 e 24.7.3)",["1.1","1.2","1.3","1.4","1.5","1.6","1.7","1.8","1.9","1.10","1.11","1.12","1.13","1.14"]),
            (2,"INSTALAÇÕES SANITÁRIAS (NR-18 / NR-24)",["2.1","2.2","2.3","2.4","2.5","2.6"]),
            (3,"REFEITÓRIO / LOCAL DE REFEIÇÕES",["3.1","3.2","3.3","3.4"]),
            (4,"LAVANDERIA (NR-18 / NR-24)",["4.1","4.2"]),
            (5,"ÁREA DE LAZER (NR-18)",["5.1"]),
            (6,"ABASTECIMENTO DE ÁGUA (NR-18)",["6.1","6.2","6.3","6.4"]),
            (7,"HIGIENE E CONSERVAÇÃO GERAL (NR-24)",["7.1","7.2","7.3","7.4","7.5","7.6"]),
            (8,"SAÚDE E SEGURANÇA NO ALOJAMENTO (NR-24)",["8.1","8.2","8.3","8.4"]),
        ]
        itens_map = {i['item_num']: i for i in (v.get('itens') or [])}
        status_labels = {'c':'C','nc':'NC','na':'N/A','':'N/A'}
        status_colors = {'c':colors.HexColor('#dcfce7'),'nc':colors.HexColor('#fee2e2'),
                         'na':colors.HexColor('#f1f5f9'),'':colors.HexColor('#f1f5f9')}

        for bloco_num, bloco_nome, item_nums in BLOCOS:
            story.append(Paragraph(f"BLOCO {bloco_num} — {bloco_nome}", sec_style))
            tdata = [["Nº","Item verificado","Status","Observação"]]
            for num in item_nums:
                item = itens_map.get(num, {})
                st = item.get('status','na')
                tdata.append([num, Paragraph(item.get('descricao',num), body_style),
                               status_labels.get(st,'N/A'), Paragraph(item.get('observacao',''), small_style)])
            t = Table(tdata, colWidths=[1.2*cm,10*cm,1.5*cm,W-12.7*cm])
            ts = TableStyle([
                ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),
                ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#1F4E79')),
                ('TEXTCOLOR',(0,0),(-1,0),colors.white),
                ('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),
                ('ALIGN',(2,0),(2,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                ('PADDING',(0,0),(-1,-1),4),
            ])
            for row_i, num in enumerate(item_nums, 1):
                st = itens_map.get(num,{}).get('status','na')
                ts.add('BACKGROUND',(2,row_i),(2,row_i),status_colors.get(st,colors.white))
            t.setStyle(ts)
            story.append(t)
            story.append(Spacer(1,4))

        story.append(Spacer(1,6))
        story.append(Paragraph("RESULTADO DA VISTORIA", sec_style))
        res_data = [["Resultado:", Paragraph(f'<b>{res_label}</b>', body_style),
                     "Prazo regularização:", v.get('prazo_regularizacao','')]]
        rt = Table(res_data, colWidths=[4*cm,6*cm,4*cm,W-14*cm])
        rt.setStyle(TableStyle([
            ('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),('FONTNAME',(2,0),(2,-1),'Helvetica-Bold'),
            ('FONTSIZE',(0,0),(-1,-1),9),('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),
            ('BACKGROUND',(0,0),(0,-1),colors.HexColor('#f1f5f9')),
            ('BACKGROUND',(2,0),(2,-1),colors.HexColor('#f1f5f9')),
            ('PADDING',(0,0),(-1,-1),5),
        ]))
        story.append(rt)

        if v.get('observacao_geral'):
            story.append(Spacer(1,6))
            story.append(Paragraph("OBSERVAÇÃO GERAL", sec_style))
            story.append(Paragraph(v['observacao_geral'], body_style))

        plano = v.get('plano_acao') or []
        if plano:
            story.append(Spacer(1,6))
            story.append(Paragraph("PLANO DE AÇÃO (não conformidades)", sec_style))
            pa_data = [["Nº NC","Descrição","Responsável","Prazo","Status"]]
            for a in plano:
                pa_data.append([a.get('num_nc',''), Paragraph(a.get('descricao',''), body_style),
                                 a.get('responsavel',''), a.get('prazo',''), a.get('status_acao','')])
            pat = Table(pa_data, colWidths=[1.5*cm,7*cm,3.5*cm,2.5*cm,2.5*cm])
            pat.setStyle(TableStyle([
                ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),
                ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#1F4E79')),
                ('TEXTCOLOR',(0,0),(-1,0),colors.white),
                ('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),
                ('PADDING',(0,0),(-1,-1),4),
            ]))
            story.append(pat)

        story.append(Spacer(1,16))
        story.append(Paragraph("ASSINATURAS", sec_style))
        sig_data = [
            ["Responsável pela vistoria","","Encarregado / Representante"],
            [f"Nome: {v.get('responsavel','')}","",f"Nome: {v.get('encarregado','')}"],
            [f"Cargo: {v.get('cargo_responsavel','')}","","Cargo: ___________________________"],
            [f"Data: {v.get('data_vistoria','')}","",f"Data: {v.get('data_vistoria','')}"],
            ["Assinatura: ______________________","","Assinatura: ______________________"],
        ]
        sigt = Table(sig_data, colWidths=[(W/2-0.5*cm),1*cm,(W/2-0.5*cm)])
        sigt.setStyle(TableStyle([
            ('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTSIZE',(0,0),(-1,-1),9),
            ('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
            ('LINEBELOW',(0,0),(0,0),0.5,colors.black),('LINEBELOW',(2,0),(2,0),0.5,colors.black),
            ('PADDING',(0,0),(-1,-1),4),
        ]))
        story.append(sigt)

        fotos = v.get('fotos') or []
        if fotos:
            story.append(Spacer(1,10))
            story.append(Paragraph("REGISTRO FOTOGRÁFICO", sec_style))
            foto_row = []
            for foto in fotos:
                try:
                    b64 = foto.get('dados_base64','')
                    if not b64:
                        continue
                    if ',' in b64:
                        b64 = b64.split(',',1)[1]
                    img_bytes = _b64.b64decode(b64)
                    img_buf = _io.BytesIO(img_bytes)
                    if _pillow_ok:
                        pil = PILImage.open(img_buf)
                        if pil.mode not in ('RGB','L'):
                            pil = pil.convert('RGB')
                        pil.thumbnail((600,450), PILImage.LANCZOS)
                        out = _io.BytesIO()
                        pil.save(out, format='JPEG', quality=85)
                        out.seek(0)
                        w, h = pil.size
                        max_w, max_h = 8*cm, 6*cm
                        ratio = min(max_w/w, max_h/h) if w and h else 1
                        rl_img = RLImage(out, width=w*ratio, height=h*ratio)
                    else:
                        rl_img = RLImage(img_buf, width=8*cm, height=6*cm)
                    foto_row.append(rl_img)
                    if len(foto_row) == 2:
                        ft = Table([foto_row], colWidths=[9*cm,9*cm])
                        ft.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('PADDING',(0,0),(-1,-1),6)]))
                        story.append(ft)
                        story.append(Spacer(1,6))
                        foto_row = []
                except Exception as fe:
                    print(f"WARN foto no envio: {fe}")
            if foto_row:
                while len(foto_row) < 2:
                    foto_row.append(Paragraph('', body_style))
                ft = Table([foto_row], colWidths=[9*cm,9*cm])
                ft.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('PADDING',(0,0),(-1,-1),6)]))
                story.append(ft)

        doc.build(story)
        pdf_bytes = buf.getvalue()

    except ImportError:
        raise HTTPException(500, "Biblioteca reportlab não instalada")
    except Exception as e:
        raise HTTPException(500, f"Erro ao gerar PDF: {e}")

    # Salva PDF em arquivo temporário para o ZapSign
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    try:
        tmp.write(pdf_bytes)
        tmp.close()

        nome_doc = f"Vistoria de Alojamento — {v.get('frente_servico','')} — {v.get('data_vistoria','')}"
        funcionario_enc = {
            "nome":    encarregado_nome,
            "email":   dados.get("email_encarregado", ""),
            "celular": dados.get("celular_encarregado", ""),
            "cpf":     "",
        }
        ret = zapsign.enviar_documento(
            nome_documento=nome_doc,
            caminho_pdf=tmp.name,
            funcionario=funcionario_enc,
            sandbox=sandbox,
        )
    finally:
        try:
            _os.remove(tmp.name)
        except Exception:
            pass

    if not ret["sucesso"]:
        raise HTTPException(400, ret.get("erro", "Erro ao enviar para ZapSign"))

    link = ret.get("link", "")
    token = ret.get("autentique_id", "")
    banco.salvar_link_vistoria(vid, link, token)
    return {"ok": True, "link_assinatura": link, "zapsign_token": token}



# ── ACIDENTES ─────────────────────────────────────────────────────────────────

@app.post("/api/acidentes/relatorios")
async def criar_relatorio_acidente(dados: dict, req: Request, _=Depends(verificar_acesso)):
    usuario = getattr(req.state, 'usuario', '')
    plano = dados.pop('plano_acao', [])
    rid = banco.salvar_relatorio_acidente(dados, usuario)
    banco.salvar_plano_acao_acidente(rid, plano)
    return {"ok": True, "id": rid}


@app.get("/api/acidentes/relatorios")
async def listar_relatorios_acidente(_=Depends(verificar_acesso)):
    return {"relatorios": banco.listar_relatorios_acidente()}


@app.get("/api/acidentes/relatorios/branco/pdf")
async def pdf_relatorio_acidente_branco(_=Depends(verificar_acesso)):
    pdf_bytes = _gerar_pdf_acidente_bytes({})
    import io
    return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf",
                             headers={"Content-Disposition": 'attachment; filename="relatorio_acidente_BRANCO.pdf"'})


@app.get("/api/acidentes/relatorios/{rid}")
async def buscar_relatorio_acidente(rid: int, _=Depends(verificar_acesso)):
    r = banco.buscar_relatorio_acidente(rid)
    if not r:
        raise HTTPException(404, "Relatório não encontrado")
    return {"relatorio": r}


@app.put("/api/acidentes/relatorios/{rid}")
async def atualizar_relatorio_acidente(rid: int, dados: dict, _=Depends(verificar_acesso)):
    plano = dados.pop('plano_acao', [])
    dados['id'] = rid
    banco.salvar_relatorio_acidente(dados)
    banco.salvar_plano_acao_acidente(rid, plano)
    return {"ok": True, "id": rid}


@app.delete("/api/acidentes/relatorios/{rid}")
async def deletar_relatorio_acidente(rid: int, _=Depends(verificar_acesso)):
    banco.deletar_relatorio_acidente(rid)
    return {"ok": True}


@app.post("/api/acidentes/relatorios/{rid}/assinar")
async def assinar_relatorio_acidente(rid: int, dados: dict = None, _=Depends(verificar_acesso)):
    """
    Envia o relatório de acidente ao ZapSign com todos os signatários preenchidos.
    Recebe: {signatarios: [{tipo, nome, celular}], sandbox: bool}
    Retorna: {ok, signatarios: [{tipo, nome, link}], autentique_id}
    """
    if dados is None:
        dados = {}
    r = banco.buscar_relatorio_acidente(rid)
    if not r:
        raise HTTPException(404, "Relatório não encontrado")

    sandbox = dados.get("sandbox", False)

    # Monta lista de signatários a partir do payload OU dos dados salvos no relatório
    signatarios_input = dados.get("signatarios", [])

    if signatarios_input:
        signatarios = signatarios_input
    else:
        # Monta a partir dos dados do relatório (compatibilidade legada)
        signatarios = []
        func_nome = r.get("funcionario_nome") or "Funcionário"
        func_cel  = r.get("telefone") or ""
        if func_nome:
            signatarios.append({"tipo": "func", "nome": func_nome, "celular": func_cel})

        t1_nome = r.get("testemunha1_nome") or ""
        t1_cel  = r.get("cel_testemunha1") or ""
        if t1_nome:
            signatarios.append({"tipo": "test1", "nome": t1_nome, "celular": t1_cel})

        t2_nome = r.get("testemunha2_nome") or ""
        t2_cel  = r.get("cel_testemunha2") or ""
        if t2_nome:
            signatarios.append({"tipo": "test2", "nome": t2_nome, "celular": t2_cel})

        sup_nome = r.get("supervisor_turno_nome") or ""
        sup_cel  = r.get("cel_supervisor") or ""
        if sup_nome:
            signatarios.append({"tipo": "sup", "nome": sup_nome, "celular": sup_cel})

        tec_nome = r.get("tecnico_seguranca") or ""
        tec_cel  = r.get("cel_tecnico") or ""
        if tec_nome:
            signatarios.append({"tipo": "tec", "nome": tec_nome, "celular": tec_cel})

    if not signatarios:
        raise HTTPException(400, "Nenhum signatário informado.")

    pdf_bytes = _gerar_pdf_acidente_bytes(r)
    import tempfile as _tempfile, os as _os
    tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(pdf_bytes)
    tmp.close()

    nome_doc = f"Relatório de Acidente — {r.get('funcionario_nome','')} — {r.get('data_acidente','')}"
    try:
        ret = zapsign.enviar_documento_multi(nome_doc, tmp.name, signatarios, sandbox=sandbox)
    finally:
        try:
            _os.remove(tmp.name)
        except Exception:
            pass

    if not ret["sucesso"]:
        raise HTTPException(400, ret.get("erro", "Erro ao enviar para ZapSign"))

    # Salva o token ZapSign no relatório
    token = ret.get("autentique_id", "")
    if token:
        try:
            banco.executar(
                "UPDATE acidentes_relatorios SET zapsign_token=? WHERE id=?" if not banco.USE_POSTGRES
                else "UPDATE acidentes_relatorios SET zapsign_token=%s WHERE id=%s",
                (token, rid)
            )
        except Exception:
            pass

    return {"ok": True, "signatarios": ret.get("signatarios", []),
            "autentique_id": token}


def _gerar_pdf_acidente_bytes(r: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    import io, uuid as _uuid

    _uid = _uuid.uuid4().hex[:8]
    title_s = ParagraphStyle(f'act_{_uid}', fontSize=13, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=4)
    sub_s   = ParagraphStyle(f'acs_{_uid}', fontSize=9,  fontName='Helvetica', alignment=TA_CENTER, spaceAfter=6)
    sec_s   = ParagraphStyle(f'acsc_{_uid}',fontSize=10, fontName='Helvetica-Bold', spaceAfter=3, spaceBefore=8,
                             backColor=colors.HexColor('#1F4E79'), textColor=colors.white, leftIndent=4)
    body_s  = ParagraphStyle(f'acb_{_uid}', fontSize=9,  fontName='Helvetica', spaceAfter=2)
    bold_s  = ParagraphStyle(f'acbd_{_uid}',fontSize=9,  fontName='Helvetica-Bold', spaceAfter=2)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    W = A4[0] - 3*cm
    story = []

    story.append(Paragraph("RELATÓRIO DE ACIDENTE DE TRABALHO", title_s))
    story.append(Paragraph("JS Construtora e Locadora Ltda — CNPJ: 16.910.656/0001-81", sub_s))

    # Dados do Funcionário
    story.append(Paragraph("DADOS DO FUNCIONÁRIO", sec_s))
    fd = [
        ["Nome:", r.get('funcionario_nome',''), "Matrícula:", r.get('matricula','')],
        ["Função:", r.get('funcao',''), "Empresa:", r.get('empresa_terceiro','') or 'JS Construtora e Locadora Ltda'],
        ["Turno:", r.get('turno',''), "Sexo / Data Nasc.:", f"{r.get('sexo','')} / {r.get('data_nasc','')}"],
        ["Admissão:", r.get('data_admissao',''), "Telefone:", r.get('telefone','')],
        ["Posto de Trabalho:", r.get('posto_trabalho',''), "Chefia Imediata:", r.get('chefia_imediata','')],
    ]
    ft = Table(fd, colWidths=[3.5*cm, 7*cm, 3.5*cm, W-14*cm])
    ft.setStyle(TableStyle([
        ('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),
        ('FONTNAME',(2,0),(2,-1),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),
        ('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),
        ('BACKGROUND',(0,0),(0,-1),colors.HexColor('#f1f5f9')),
        ('BACKGROUND',(2,0),(2,-1),colors.HexColor('#f1f5f9')),
        ('PADDING',(0,0),(-1,-1),4),
    ]))
    story.append(ft)

    # Tipo do Acidente
    story.append(Paragraph("TIPO DO ACIDENTE", sec_s))
    story.append(Paragraph(f"<b>{r.get('tipo_acidente','')}</b>", body_s))

    # Dados do Acidente
    story.append(Paragraph("DADOS DO ACIDENTE / DOENÇA", sec_s))
    afas_txt = 'SIM' if r.get('afastamento') == 'sim' else 'NÃO'
    ad = [
        ["Data do Acidente:", r.get('data_acidente',''), "Hora:", r.get('hora_acidente','')],
        ["Data Preenchimento:", r.get('data_preenchimento',''), "Afastamento:", f"{afas_txt} — {r.get('num_dias_afastamento',0)} dias"],
        ["Natureza da Lesão:", Paragraph(r.get('natureza_lesao',''), body_s), "Agente Causador:", Paragraph(r.get('agente_causador',''), body_s)],
        ["Serviço que Executava:", Paragraph(r.get('servico_executava',''), body_s), "", ""],
    ]
    adt = Table(ad, colWidths=[3.5*cm, 7*cm, 3.5*cm, W-14*cm])
    adt.setStyle(TableStyle([
        ('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),
        ('FONTNAME',(2,0),(2,-1),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),
        ('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),
        ('BACKGROUND',(0,0),(0,-1),colors.HexColor('#f1f5f9')),
        ('BACKGROUND',(2,0),(2,-1),colors.HexColor('#f1f5f9')),
        ('PADDING',(0,0),(-1,-1),4),('VALIGN',(0,0),(-1,-1),'TOP'),
    ]))
    story.append(adt)

    # Descrição do Acidente
    if r.get('descricao_acidente'):
        story.append(Paragraph("DESCRIÇÃO DO ACIDENTE", sec_s))
        story.append(Paragraph(r.get('descricao_acidente',''), body_s))

    # Checklist
    def sn(val): return 'SIM' if val == 'sim' else ('NÃO' if val == 'nao' else '—')
    def sc(val): return colors.HexColor('#dcfce7') if val=='sim' else (colors.HexColor('#fee2e2') if val=='nao' else colors.white)

    story.append(Paragraph("CHECKLIST", sec_s))
    ck_data = [
        ["Pergunta", "Resp.", "Observação"],
        ["O funcionário já sofreu acidente anteriormente?", sn(r.get('checklist_acidentes_antes','')),
         f"Qtd: {r.get('checklist_qtd_acidentes','')}  Semelhante: {sn(r.get('checklist_acidente_semelhante',''))}"],
        ["O funcionário usava EPI?", sn(r.get('checklist_usava_epi','')), r.get('checklist_epi_justificativa','')],
        ["O funcionário recebeu treinamento?", sn(r.get('checklist_treinamento','')), ''],
        ["O funcionário tem experiência na função?", sn(r.get('checklist_experiencia','')), ''],
        ["O supervisor estava presente no momento?", sn(r.get('checklist_supervisor_presente','')), ''],
    ]
    ckt = Table(ck_data, colWidths=[9*cm, 1.5*cm, W-10.5*cm])
    cks = TableStyle([
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#1F4E79')),('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),('ALIGN',(1,0),(1,-1),'CENTER'),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),('PADDING',(0,0),(-1,-1),4),
    ])
    for i, key in enumerate(['checklist_acidentes_antes','checklist_usava_epi','checklist_treinamento',
                              'checklist_experiencia','checklist_supervisor_presente'], 1):
        cks.add('BACKGROUND',(1,i),(1,i), sc(r.get(key,'')))
    ckt.setStyle(cks)
    story.append(ckt)

    # Análise
    story.append(Paragraph("INVESTIGAÇÃO E ANÁLISE DO ACIDENTE", sec_s))
    if r.get('analise_acidente'):
        story.append(Paragraph(r['analise_acidente'], body_s))
        story.append(Spacer(1,4))

    # Assinaturas
    sign_data = [
        ["Funcionário:", r.get('funcionario_nome',''), "Assinatura:", ""],
        ["Testemunha 1:", r.get('testemunha1_nome',''), "Assinatura:", ""],
        ["Testemunha 2:", r.get('testemunha2_nome',''), "Assinatura:", ""],
        ["Supervisor (Turno):", r.get('supervisor_turno_nome',''), "Assinatura:", ""],
        ["Técnico de Seg.:", r.get('tecnico_seguranca',''), "Assinatura:", ""],
    ]
    signt = Table(sign_data, colWidths=[3.5*cm, 6*cm, 3*cm, W-12.5*cm])
    signt.setStyle(TableStyle([
        ('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),
        ('FONTNAME',(2,0),(2,-1),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),
        ('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),
        ('BACKGROUND',(0,0),(0,-1),colors.HexColor('#f1f5f9')),
        ('BACKGROUND',(2,0),(2,-1),colors.HexColor('#f1f5f9')),
        ('PADDING',(0,0),(-1,-1),5),('ROWBACKGROUNDS',(3,0),(3,-1),[colors.HexColor('#fffbe6')]),
        ('MINROWHEIGHT',(3,0),(3,-1),30),
    ]))
    story.append(signt)

    # Plano de Ação
    plano = r.get('plano_acao') or []
    if plano:
        story.append(Paragraph("PLANO DE AÇÃO / MEDIDAS CORRETIVAS", sec_s))
        pa = [["Ação","Responsável","Prazo","Visto"]]
        for a in plano:
            pa.append([Paragraph(a.get('acao',''), body_s), a.get('responsavel',''), a.get('prazo',''), a.get('visto','')])
        pat = Table(pa, colWidths=[8*cm, 3.5*cm, 2.5*cm, W-14*cm])
        pat.setStyle(TableStyle([
            ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),
            ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#1F4E79')),('TEXTCOLOR',(0,0),(-1,0),colors.white),
            ('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),('PADDING',(0,0),(-1,-1),4),
            ('VALIGN',(0,0),(-1,-1),'TOP'),
        ]))
        story.append(pat)

    # Responsável pelo preenchimento
    story.append(Spacer(1,12))
    resp_data = [
        ["Responsável pelo Preenchimento:", r.get('responsavel_nome',''), "CPF:", r.get('responsavel_cpf','')],
    ]
    respt = Table(resp_data, colWidths=[5*cm, 7*cm, 1.5*cm, W-13.5*cm])
    respt.setStyle(TableStyle([
        ('FONTNAME',(0,0),(-1,-1),'Helvetica'),('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),
        ('FONTNAME',(2,0),(2,-1),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),
        ('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),
        ('BACKGROUND',(0,0),(0,-1),colors.HexColor('#f1f5f9')),
        ('BACKGROUND',(2,0),(2,-1),colors.HexColor('#f1f5f9')),
        ('PADDING',(0,0),(-1,-1),4),
    ]))
    story.append(respt)

    doc.build(story)
    buf.seek(0)
    return buf.read()


@app.get("/api/acidentes/relatorios/{rid}/pdf")
async def pdf_relatorio_acidente(rid: int, _=Depends(verificar_acesso)):
    r = banco.buscar_relatorio_acidente(rid)
    if not r:
        raise HTTPException(404, "Relatório não encontrado")
    try:
        import io
        pdf_bytes = _gerar_pdf_acidente_bytes(r)
        fname = f"relatorio_acidente_{rid}_{r.get('data_acidente','').replace('/','')}.pdf"
        return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf",
                                 headers={"Content-Disposition": f'attachment; filename="{fname}"'})
    except ImportError:
        raise HTTPException(500, "reportlab não instalado")
    except Exception as e:
        raise HTTPException(500, f"Erro ao gerar PDF: {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
