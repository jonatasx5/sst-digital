"""
SST Digital - Sistema Web
Backend FastAPI para geração e envio de kits SST via Autentique
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
import uvicorn
import os
import json
import shutil
import tempfile
from datetime import datetime

import banco
import processador
import autentique
from config import DOCUMENTOS, APP_PASSWORD, EMPRESA

app = FastAPI(title="SST Digital")

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBasic(auto_error=False)

def verificar_acesso(credentials: HTTPBasicCredentials = Depends(security)):
    """Verifica senha de acesso se APP_PASSWORD estiver configurada."""
    if not APP_PASSWORD:
        return  # Sem senha configurada, acesso liberado
    if credentials is None:
        raise HTTPException(status_code=401, detail="Autenticação necessária",
                            headers={"WWW-Authenticate": "Basic"})
    senha_correta = secrets.compare_digest(credentials.password.encode(), APP_PASSWORD.encode())
    if not senha_correta:
        raise HTTPException(status_code=401, detail="Senha incorreta",
                            headers={"WWW-Authenticate": "Basic"})

# Cria banco na inicialização
banco.criar_banco()

# ══════════════════════════════════════════════════════════
#  ROTA PRINCIPAL — serve o HTML
# ══════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index(_=Depends(verificar_acesso)):
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
        ret = autentique.enviar_documento(
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
#  AUTENTIQUE
# ══════════════════════════════════════════════════════════

@app.get("/api/config")
async def get_config(_=Depends(verificar_acesso)):
    return {"empresa": EMPRESA}

@app.get("/api/autentique/verificar")
async def verificar_autentique(_=Depends(verificar_acesso)):
    ok, msg = autentique.verificar_token()
    return {"ok": ok, "mensagem": msg}

# ══════════════════════════════════════════════════════════
#  HISTÓRICO
# ══════════════════════════════════════════════════════════

@app.get("/api/historico")
async def historico(_=Depends(verificar_acesso)):
    return banco.listar_lotes()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
