"""
SST Digital - Sistema Web
Backend FastAPI para geração e envio de kits SST via Autentique
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
import json
import shutil
import tempfile
from datetime import datetime

import banco
import processador
import autentique
from config import DOCUMENTOS

app = FastAPI(title="SST Digital")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cria banco na inicialização
banco.criar_banco()

# ══════════════════════════════════════════════════════════
#  ROTA PRINCIPAL — serve o HTML
# ══════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

# ══════════════════════════════════════════════════════════
#  FUNCIONÁRIOS
# ══════════════════════════════════════════════════════════

@app.get("/api/funcionarios")
async def listar_funcionarios(busca: str = ""):
    return banco.buscar_funcionarios(busca)

@app.post("/api/funcionarios")
async def salvar_funcionario(dados: dict):
    fid = banco.salvar_funcionario(dados)
    return {"id": fid, "ok": True}

@app.post("/api/funcionarios/importar")
async def importar_planilha(file: UploadFile = File(...)):
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
async def listar_documentos():
    from config import MODELOS_DIR
    docs = []
    for d in DOCUMENTOS:
        existe = os.path.exists(os.path.join(MODELOS_DIR, f"{d['id']}.docx"))
        docs.append({**d, "modelo_existe": existe})
    return docs

@app.get("/api/matriz/{cargo}")
async def docs_do_cargo(cargo: str):
    return banco.docs_do_cargo(cargo)

@app.post("/api/matriz/{cargo}")
async def salvar_matriz(cargo: str, dados: dict):
    banco.salvar_docs_cargo(cargo, dados.get("doc_ids", []))
    return {"ok": True}

@app.get("/api/cargos")
async def listar_cargos():
    todos = banco.buscar_funcionarios("")
    cargos = sorted(set(f["cargo"] for f in todos if f["cargo"]))
    return cargos

# ══════════════════════════════════════════════════════════
#  UPLOAD DE MODELOS
# ══════════════════════════════════════════════════════════

@app.post("/api/modelos/upload/{doc_id}")
async def upload_modelo(doc_id: str, file: UploadFile = File(...)):
    from config import MODELOS_DIR
    os.makedirs(MODELOS_DIR, exist_ok=True)
    dest = os.path.join(MODELOS_DIR, f"{doc_id}.docx")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"ok": True, "arquivo": f"{doc_id}.docx"}

# ══════════════════════════════════════════════════════════
#  LOTES
# ══════════════════════════════════════════════════════════

@app.get("/api/lotes")
async def listar_lotes():
    return banco.listar_lotes()

@app.post("/api/lotes/preview")
async def preview_lote(dados: dict):
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
        from config import DOCUMENTOS as DOCS
        docs_nomes = [d["nome"] for d in DOCS if d["id"] in doc_ids]
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
async def enviar_lote(dados: dict):
    """Processa e envia o lote completo para o Autentique."""
    func_ids   = dados.get("func_ids", [])
    descricao  = dados.get("descricao", f"Lote {datetime.now().strftime('%d/%m/%Y')}")
    celulares  = dados.get("celulares", {})  # {str(func_id): celular_editado}
    sandbox    = dados.get("sandbox", False)

    todos  = banco.buscar_funcionarios("")
    lote_id = banco.criar_lote(descricao)
    pasta   = processador.pasta_lote()

    resultados = []
    erros      = []

    for fid in func_ids:
        f = next((x for x in todos if x["id"] == fid), None)
        if not f:
            continue

        # Celular editado pelo usuário
        cel_editado = celulares.get(str(fid))
        if cel_editado:
            f = dict(f)
            f["celular"] = cel_editado

        doc_ids = banco.docs_do_cargo(f["cargo"])
        if not doc_ids:
            erros.append(f"{f['nome']}: nenhum documento configurado para o cargo '{f['cargo']}'")
            continue

        banco.adicionar_ao_lote(lote_id, f["id"])
        pdfs = processador.gerar_kit_funcionario(f, doc_ids, pasta)

        links_func = []
        for res in pdfs:
            if res["erro"]:
                erros.append(f"{f['nome']} / {res['doc_nome']}: {res['erro']}")
                continue

            ret = autentique.enviar_documento(
                nome_documento=f"{res['doc_nome']} — {f['nome']}",
                caminho_pdf=res["pdf_path"],
                funcionario=f,
                sandbox=sandbox
            )

            banco.registrar_envio({
                "funcionario_id":  f["id"],
                "doc_id":          res["doc_id"],
                "doc_nome":        res["doc_nome"],
                "pdf_path":        res["pdf_path"],
                "autentique_id":   ret.get("autentique_id"),
                "link_assinatura": ret.get("link"),
                "status":          "enviado" if ret["sucesso"] else "erro",
            })

            if ret["sucesso"]:
                links_func.append({
                    "doc":  res["doc_nome"],
                    "link": ret.get("link",""),
                })
            else:
                erros.append(f"{f['nome']} / {res['doc_nome']}: {ret['erro']}")

        if links_func:
            resultados.append({
                "nome":    f["nome"],
                "celular": f.get("celular",""),
                "cargo":   f["cargo"],
                "links":   links_func,
            })

    return {
        "ok":        True,
        "lote_id":   lote_id,
        "enviados":  len(resultados),
        "erros":     erros,
        "resultados": resultados,
    }

# ══════════════════════════════════════════════════════════
#  AUTENTIQUE
# ══════════════════════════════════════════════════════════

@app.get("/api/autentique/verificar")
async def verificar_autentique():
    ok, msg = autentique.verificar_token()
    return {"ok": ok, "mensagem": msg}

# ══════════════════════════════════════════════════════════
#  HISTÓRICO
# ══════════════════════════════════════════════════════════

@app.get("/api/historico")
async def historico():
    return banco.listar_lotes()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
