"""
processador.py
- Lê planilha Excel do DP e retorna lista de funcionários
- Substitui variáveis nos modelos .docx
- Converte .docx → PDF via LibreOffice
"""

import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import openpyxl
from docx import Document

from config import MODELOS_DIR, OUTPUT_DIR, EMPRESA, RESP_SST, DOCUMENTOS


# ══════════════════════════════════════════════════════════
#  LEITURA DA PLANILHA
# ══════════════════════════════════════════════════════════

# Mapeamento: nome da coluna no Excel → chave interna
COLUNAS_MAP = {
    "nome":        ["nome", "funcionário", "funcionario", "colaborador"],
    "cpf":         ["cpf"],
    "matricula":   ["matrícula", "matricula", "mat"],
    "cargo":       ["cargo atual", "cargo", "função", "funcao", "função atual"],
    "lotacao":     ["contrato", "lotação", "lotacao", "obra", "frente"],
    "admissao":    ["admissão", "admissao", "data admissão", "data de admissão", "dt admissão"],
    "celular":     ["celular", "telefone", "whatsapp", "fone", "cel"],
    "email":       ["e-mail pessoal", "email", "e-mail", "email pessoal"],
}


def _mapear_cabecalho(headers: list[str]) -> dict:
    """Mapeia posição das colunas do Excel para chaves internas."""
    mapa = {}
    for i, h in enumerate(headers):
        if h is None:
            continue
        h_norm = str(h).strip().lower()
        for chave, aliases in COLUNAS_MAP.items():
            if h_norm in aliases and chave not in mapa:
                mapa[chave] = i
    return mapa


def ler_planilha(caminho: str) -> tuple[list[dict], list[str]]:
    """
    Lê planilha Excel do DP.
    Retorna (lista_funcionarios, avisos).
    """
    wb = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], ["Planilha vazia."]

    # Encontra linha de cabeçalho (primeira linha não vazia)
    header_row = None
    header_idx = 0
    for i, row in enumerate(rows):
        if any(cell is not None for cell in row):
            header_row = [str(c).strip() if c is not None else "" for c in row]
            header_idx = i
            break

    if header_row is None:
        return [], ["Não foi possível identificar o cabeçalho."]

    mapa = _mapear_cabecalho(header_row)
    avisos = []

    if "nome" not in mapa:
        avisos.append("⚠️ Coluna 'Nome' não encontrada.")
    if "cpf" not in mapa:
        avisos.append("⚠️ Coluna 'CPF' não encontrada.")

    funcionarios = []
    for row in rows[header_idx + 1:]:
        if not any(cell is not None for cell in row):
            continue

        def get(chave):
            idx = mapa.get(chave)
            if idx is None or idx >= len(row):
                return ""
            v = row[idx]
            return str(v).strip() if v is not None else ""

        nome = get("nome")
        cpf  = get("cpf")

        if not nome or not cpf:
            continue

        # Formata CPF
        cpf_num = re.sub(r"\D", "", cpf)
        if len(cpf_num) == 11:
            cpf = f"{cpf_num[:3]}.{cpf_num[3:6]}.{cpf_num[6:9]}-{cpf_num[9:]}"

        # Formata celular
        cel = re.sub(r"\D", "", get("celular"))

        funcionarios.append({
            "nome":      nome,
            "cpf":       cpf,
            "matricula": get("matricula"),
            "cargo":     get("cargo"),
            "lotacao":   get("lotacao"),
            "admissao":  get("admissao"),
            "celular":   cel,
            "email":     get("email"),
        })

    wb.close()
    return funcionarios, avisos


# ══════════════════════════════════════════════════════════
#  PREENCHIMENTO DO DOCX
# ══════════════════════════════════════════════════════════

def _substituir_texto(texto: str, variaveis: dict) -> str:
    """Substitui {{VARIAVEL}} pelo valor correspondente."""
    for chave, valor in variaveis.items():
        texto = texto.replace(f"{{{{{chave}}}}}", str(valor) if valor else "")
    return texto


def _processar_paragrafo(paragrafo, variaveis: dict):
    """Preserva formatação ao substituir variáveis no parágrafo."""
    # Primeiro tenta substituição simples em cada run
    for run in paragrafo.runs:
        if "{{" in run.text:
            run.text = _substituir_texto(run.text, variaveis)

    # Se ainda há variável espalhada entre runs, une e redistribui
    texto_completo = "".join(r.text for r in paragrafo.runs)
    if "{{" in texto_completo:
        texto_novo = _substituir_texto(texto_completo, variaveis)
        if paragrafo.runs:
            paragrafo.runs[0].text = texto_novo
            for run in paragrafo.runs[1:]:
                run.text = ""


def preencher_docx(modelo_id: str, funcionario: dict, pasta_saida: str) -> str | None:
    """
    Preenche um modelo .docx com os dados do funcionário.
    Retorna o caminho do arquivo gerado ou None se modelo não existe.
    """
    modelo_path = os.path.join(MODELOS_DIR, f"{modelo_id}.docx")
    if not os.path.exists(modelo_path):
        print(f"⚠️  Modelo não encontrado: {modelo_path}")
        return None

    os.makedirs(pasta_saida, exist_ok=True)

    # Monta dicionário de variáveis
    variaveis = {
        "NOME":          funcionario.get("nome", ""),
        "CPF":           funcionario.get("cpf", ""),
        "MATRICULA":     funcionario.get("matricula", funcionario.get("cpf", "")),
        "CARGO":         funcionario.get("cargo", ""),
        "LOTACAO":       funcionario.get("lotacao", ""),
        "DATA_ADMISSAO": funcionario.get("admissao", ""),
        "DATA_HOJE":     date.today().strftime("%d/%m/%Y"),
        "CELULAR":       funcionario.get("celular", ""),
        "EMAIL":         funcionario.get("email", ""),
        "EMPRESA":       EMPRESA,
        "RESP_SST":      RESP_SST,
    }

    doc = Document(modelo_path)

    # Processa parágrafos do corpo
    for para in doc.paragraphs:
        _processar_paragrafo(para, variaveis)

    # Processa tabelas
    for tabela in doc.tables:
        for linha in tabela.rows:
            for celula in linha.cells:
                for para in celula.paragraphs:
                    _processar_paragrafo(para, variaveis)

    # Processa cabeçalho e rodapé
    for section in doc.sections:
        for para in section.header.paragraphs:
            _processar_paragrafo(para, variaveis)
        for para in section.footer.paragraphs:
            _processar_paragrafo(para, variaveis)

    # Nome seguro para o arquivo
    nome_seguro = re.sub(r"[^\w\s-]", "", funcionario.get("nome", "funcionario"))
    nome_seguro = re.sub(r"\s+", "_", nome_seguro.strip())
    nome_arquivo = f"{modelo_id}__{nome_seguro}.docx"
    caminho_docx = os.path.join(pasta_saida, nome_arquivo)

    doc.save(caminho_docx)
    return caminho_docx


# ══════════════════════════════════════════════════════════
#  CONVERSÃO DOCX → PDF
# ══════════════════════════════════════════════════════════

def _libreoffice_path() -> str | None:
    """Localiza o executável do LibreOffice no Windows ou Linux."""
    candidatos = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "soffice",
        "libreoffice",
    ]
    for c in candidatos:
        if os.path.exists(c):
            return c
        if shutil.which(c):
            return c
    return None


def converter_para_pdf(caminho_docx: str) -> str | None:
    """
    Converte .docx para PDF usando LibreOffice headless.
    Retorna caminho do PDF gerado ou None em caso de erro.
    """
    soffice = _libreoffice_path()
    if not soffice:
        print("❌ LibreOffice não encontrado. Instale em https://www.libreoffice.org")
        return None

    pasta = os.path.dirname(caminho_docx)
    try:
        resultado = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", pasta, caminho_docx],
            capture_output=True, text=True, timeout=60
        )
        if resultado.returncode != 0:
            print(f"❌ Erro ao converter: {resultado.stderr}")
            return None

        caminho_pdf = caminho_docx.replace(".docx", ".pdf")
        if os.path.exists(caminho_pdf):
            return caminho_pdf

        # LibreOffice às vezes gera nome diferente
        base = Path(caminho_docx).stem
        for f in os.listdir(pasta):
            if f.endswith(".pdf") and base in f:
                return os.path.join(pasta, f)

        return None
    except subprocess.TimeoutExpired:
        print("❌ Timeout ao converter para PDF.")
        return None
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        return None


# ══════════════════════════════════════════════════════════
#  FLUXO COMPLETO: gerar kit de um funcionário
# ══════════════════════════════════════════════════════════

def gerar_kit_funcionario(funcionario: dict, doc_ids: list[str], lote_pasta: str) -> list[dict]:
    """
    Gera todos os PDFs do kit de um funcionário.
    Retorna lista de dicts com {doc_id, doc_nome, pdf_path, erro}.
    """
    nome_seguro = re.sub(r"[^\w\s-]", "", funcionario.get("nome", "func"))
    nome_seguro = re.sub(r"\s+", "_", nome_seguro.strip())
    pasta_func  = os.path.join(lote_pasta, nome_seguro)
    os.makedirs(pasta_func, exist_ok=True)

    resultados = []
    docs_map   = {d["id"]: d["nome"] for d in DOCUMENTOS}

    for doc_id in doc_ids:
        doc_nome = docs_map.get(doc_id, doc_id)
        print(f"  📄 {doc_nome} → {funcionario['nome']}")

        caminho_docx = preencher_docx(doc_id, funcionario, pasta_func)
        if not caminho_docx:
            resultados.append({"doc_id": doc_id, "doc_nome": doc_nome,
                                "pdf_path": None, "erro": "Modelo não encontrado"})
            continue

        caminho_pdf = converter_para_pdf(caminho_docx)
        if not caminho_pdf:
            resultados.append({"doc_id": doc_id, "doc_nome": doc_nome,
                                "pdf_path": None, "erro": "Falha na conversão PDF"})
            continue

        # Remove o .docx intermediário (mantém só o PDF)
        try:
            os.remove(caminho_docx)
        except Exception:
            pass

        resultados.append({"doc_id": doc_id, "doc_nome": doc_nome,
                            "pdf_path": caminho_pdf, "erro": None})

    return resultados


def pasta_lote() -> str:
    """Cria e retorna pasta para o lote do dia."""
    from datetime import datetime
    nome = datetime.now().strftime("lote_%Y%m%d_%H%M%S")
    pasta = os.path.join(OUTPUT_DIR, nome)
    os.makedirs(pasta, exist_ok=True)
    return pasta
