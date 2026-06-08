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

@app.get("/api/cbo/buscar")
async def buscar_cbo(codigo: str = "", titulo: str = "", _=Depends(verificar_acesso)):
    """Busca no site do MTE CBO por código ou título de ocupação."""
    import re as _re
    try:
        import requests as _req
        headers = {"User-Agent": "Mozilla/5.0"}

        if codigo:
            # Busca por código CBO
            url = f"https://www.ocupacoes.com.br/cbosite/busca?texto={codigo}"
            r = _req.get(url, headers=headers, timeout=8)
            if r.status_code == 200 and "ocupacoes.com.br" in url:
                pass
        # Tenta via mtecbo.gov.br
        if titulo or codigo:
            termo = titulo or codigo
            url = f"http://www.mtecbo.gov.br/cbosite/pages/pesquisas/BuscaPorTituloResultado.jsf"
            data = {"javax.faces.partial.ajax": "true",
                    "javax.faces.partial.execute": "pesquisa_titulo",
                    "titulo": termo}
            r = _req.post(url, data=data, headers=headers, timeout=8)
            # tenta parsear JSON ou HTML
            try:
                resultado = r.json()
                return {"ok": True, "resultados": resultado}
            except Exception:
                pass

        # Fallback: pesquisa via API alternativa pública
        termo = titulo or codigo
        url = f"https://servicodados.ibge.gov.br/api/v2/cnae/subclasses?texto={termo}"
        # CBO não é CNAE - retornamos erro orientando entrada manual
        return {"ok": False, "erro": "Busca automática não disponível. Digite o código CBO e a descrição manualmente.",
                "sugestao": "Acesse www.mtecbo.gov.br para consultar o CBO"}
    except Exception as e:
        return {"ok": False, "erro": f"Não foi possível consultar o CBO: {str(e)}. "
                "Digite o código e descrição manualmente."}


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
        ret = autentique.enviar_documento(nome_documento=nome_doc, caminho_pdf=caminho_pdf,
                                          funcionario=f, sandbox=sandbox)
        if ret["sucesso"]:
            resultados.append({"id": f["id"], "nome": f["nome"],
                                "cargo": f["cargo"], "link": ret.get("link", "")})
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
    ret = autentique.enviar_documento(nome_documento=nome_doc, caminho_pdf=caminho_pdf,
                                      funcionario=f, sandbox=sandbox)
    if ret["sucesso"]:
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
        ret = autentique.enviar_documento(
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
        else:
            erros.append(f"{f['nome']}: {ret['erro']}")

    return {"ok": True, "enviados": len(resultados), "resultados": resultados, "erros": erros}

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
