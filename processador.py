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

from config import MODELOS_DIR, OUTPUT_DIR, EMPRESA, RESP_SST, CNPJ, DOCUMENTOS


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
    try:
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

        return funcionarios, avisos
    finally:
        wb.close()


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
        "CNPJ":          CNPJ,
    }

    # Tenta banco primeiro, fallback para disco
    import banco as _banco
    import io
    conteudo_banco = _banco.buscar_modelo(modelo_id, cargo=funcionario.get("cargo"))
    if conteudo_banco:
        doc = Document(io.BytesIO(conteudo_banco))
    else:
        modelo_path = os.path.join(MODELOS_DIR, f"{modelo_id}.docx")
        if not os.path.exists(modelo_path):
            print(f"⚠️  Modelo não encontrado: {modelo_path}")
            return None
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


def preencher_ficha_epi_dinamica(funcionario: dict, epis: list, modelo_bytes: bytes) -> bytes:
    """
    Preenche a ficha de EPI base com os EPIs selecionados dinamicamente.
    epis = lista de dicts com {descricao, ca, quantidade}
    Retorna bytes do .docx preenchido.
    """
    import io
    from datetime import date

    doc = Document(io.BytesIO(modelo_bytes))

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
        "CNPJ":          CNPJ,
        "CTPS":          funcionario.get("ctps", ""),
        "RG":            funcionario.get("rg", ""),
        "TITULO_FICHA":  "FICHA DE ENTREGA DE EPI/EPC/UNIFORMES",
    }

    # Preenche variáveis nos parágrafos e cabeçalho/rodapé
    for para in doc.paragraphs:
        _processar_paragrafo(para, variaveis)
    for section in doc.sections:
        for para in section.header.paragraphs:
            _processar_paragrafo(para, variaveis)
        for para in section.footer.paragraphs:
            _processar_paragrafo(para, variaveis)

    # Encontra a tabela principal e preenche as linhas de EPI
    data_hoje = date.today().strftime("%d/%m/%Y")
    for table in doc.tables:
        # Identifica a linha de cabeçalho dos itens (ENTREGA / DESCRIÇÃO / C.A / QUANTIDADE)
        header_row = None
        for ri, row in enumerate(table.rows):
            celulas = [c.text.strip() for c in row.cells]
            if any("ENTREGA" in c or "DESCRI" in c for c in celulas):
                header_row = ri
                break

        if header_row is None:
            continue

        # Linhas de dados começam depois do cabeçalho
        epi_rows = []
        for ri in range(header_row + 1, len(table.rows)):
            row = table.rows[ri]
            celulas = [c.text.strip() for c in row.cells]
            # Para quando chega na seção de assinatura
            if any("Assinatura" in c or "Declaro" in c or "___" in c for c in celulas):
                break
            epi_rows.append(ri)

        # Preenche as linhas disponíveis com os EPIs
        for i, ri in enumerate(epi_rows):
            row = table.rows[ri]
            # Limpa todas as células primeiro
            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        run.text = ""
                    if para.runs:
                        para.runs[0].text = ""

            if i < len(epis):
                epi = epis[i]
                celulas_unicas = []
                seen_ids = set()
                for cell in row.cells:
                    if id(cell._tc) not in seen_ids:
                        seen_ids.add(id(cell._tc))
                        celulas_unicas.append(cell)

                if len(celulas_unicas) >= 4:
                    # ENTREGA
                    p = celulas_unicas[0].paragraphs[0]
                    if p.runs: p.runs[0].text = data_hoje
                    else: p.add_run(data_hoje)
                    # DESCRIÇÃO
                    p = celulas_unicas[1].paragraphs[0]
                    desc = epi.get("descricao", "")
                    if p.runs: p.runs[0].text = desc
                    else: p.add_run(desc)
                    # CA
                    p = celulas_unicas[2].paragraphs[0]
                    ca = str(epi.get("ca", ""))
                    if p.runs: p.runs[0].text = ca
                    else: p.add_run(ca)
                    # QUANTIDADE
                    p = celulas_unicas[3].paragraphs[0]
                    qtd = str(epi.get("quantidade", 1))
                    if p.runs: p.runs[0].text = qtd
                    else: p.add_run(qtd)

        # Preenche variáveis restantes na tabela (NOME, EMPRESA etc.)
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    _processar_paragrafo(para, variaveis)

        break  # Processa só a primeira tabela principal

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def preencher_os_dinamica(funcionario: dict, descricao_atividades: str,
                          epis_texto: str, modelo_bytes: bytes,
                          riscos_texto: str = "") -> bytes:
    """
    Preenche a OS modelo com dados do funcionário + CBO + EPIs.
    - descricao_atividades: texto da descrição sumária do CBO
    - epis_texto: lista de EPIs formatada (ex: "- BOTINA DE SEGURANÇA (CA: 48413)\n- ...")
    - modelo_bytes: bytes do modelo 03_os_base.docx
    Retorna bytes do .docx preenchido.
    """
    import io
    from datetime import date

    doc = Document(io.BytesIO(modelo_bytes))

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
        "CNPJ":          CNPJ,
        "CTPS":          funcionario.get("ctps", ""),
        "RG":            funcionario.get("rg", ""),
    }

    for table in doc.tables:
        # Primeira passagem: identifica índices das linhas-alvo (cabeçalhos)
        idx_cbo    = None  # linha após "DESCRIÇÃO DAS ATIVIDADES"
        idx_riscos = None  # linha após "RISCOS"
        idx_epis   = None  # linha após "EQUIPAMENTO DE PROTEÇÃO INDIVIDUAL"

        for ri, row in enumerate(table.rows):
            # Pega apenas a primeira célula única para não duplicar
            cells_unicas = []
            seen = set()
            for c in row.cells:
                if id(c._tc) not in seen:
                    seen.add(id(c._tc))
                    cells_unicas.append(c)
            texto_linha = " ".join(c.text.strip() for c in cells_unicas).upper()

            # Cabeçalho: "DESCRIÇÃO DAS ATIVIDADES"
            if ("DESCRI" in texto_linha and "ATIVIDADE" in texto_linha
                    and len(texto_linha) < 50 and idx_cbo is None):
                idx_cbo = ri + 1

            # Cabeçalho: "RISCOS" ou "RISCO A QUE ESTÁ EXPOSTO"
            if ("RISCO" in texto_linha and idx_riscos is None
                    and len(texto_linha) < 60):
                idx_riscos = ri + 1

            # Cabeçalho: "EQUIPAMENTO DE PROTEÇÃO INDIVIDUAL (EPI)"
            if ("EQUIPAMENTO" in texto_linha and "EPI" in texto_linha
                    and "UNIFORME" in texto_linha and idx_epis is None
                    and len(texto_linha) < 80):
                idx_epis = ri + 1

        def _preencher_celula(table, ri, conteudo):
            """Preenche a primeira célula da linha ri com o conteúdo."""
            if ri >= len(table.rows):
                return
            row = table.rows[ri]
            seen = set()
            for c in row.cells:
                if id(c._tc) not in seen:
                    seen.add(id(c._tc))
                    # Limpa runs de todos os parágrafos
                    for para in c.paragraphs:
                        for run in para.runs:
                            run.text = ""
                    # Coloca conteúdo no primeiro parágrafo
                    if c.paragraphs:
                        para = c.paragraphs[0]
                        if para.runs:
                            para.runs[0].text = conteudo
                        else:
                            para.add_run(conteudo)
                    break  # Apenas primeira célula

        if idx_cbo is not None:
            _preencher_celula(table, idx_cbo, descricao_atividades)
        if idx_riscos is not None and riscos_texto:
            _preencher_celula(table, idx_riscos, riscos_texto)
        if idx_epis is not None:
            _preencher_celula(table, idx_epis, epis_texto)

        # Segunda passagem: substitui variáveis {{...}} em TODAS as células
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    _processar_paragrafo(para, variaveis)

        break  # Processa só a tabela principal

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def criar_os_base_docx() -> bytes:
    """Gera o template base de Ordem de Serviço em DOCX."""
    from docx import Document as _Doc
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_ALIGN_VERTICAL
    import io as _io

    doc = _Doc()
    # Margens
    for sec in doc.sections:
        sec.top_margin = Cm(1.5)
        sec.bottom_margin = Cm(1.5)
        sec.left_margin = Cm(2)
        sec.right_margin = Cm(2)

    def add_bold(para, text, size=11):
        run = para.add_run(text)
        run.bold = True
        run.font.size = Pt(size)

    def add_normal(para, text, size=10):
        run = para.add_run(text)
        run.font.size = Pt(size)

    # Título
    titulo = doc.add_paragraph()
    titulo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_bold(titulo, "ORDEM DE SERVIÇO - SST", 14)

    doc.add_paragraph()

    # Dados do funcionário
    campos = [
        ("Empresa:", "{{EMPRESA}}"),
        ("CNPJ:", "{{CNPJ}}"),
        ("Nome:", "{{NOME}}"),
        ("CPF:", "{{CPF}}"),
        ("Matrícula:", "{{MATRICULA}}"),
        ("Cargo/Função:", "{{CARGO}}"),
        ("Lotação/Setor:", "{{LOTACAO}}"),
        ("Data de Admissão:", "{{DATA_ADMISSAO}}"),
        ("Celular:", "{{CELULAR}}"),
        ("Data de Emissão:", "{{DATA_HOJE}}"),
    ]
    for label, val in campos:
        p = doc.add_paragraph()
        add_bold(p, f"{label} ")
        add_normal(p, val)

    doc.add_paragraph()

    # Tabela principal com 3 colunas
    table = doc.add_table(rows=2, cols=3)
    table.style = 'Table Grid'

    headers = [
        "DESCRIÇÃO DAS ATIVIDADES",
        "RISCOS A QUE ESTÁ EXPOSTO",
        "EQUIPAMENTO DE PROTEÇÃO INDIVIDUAL (EPI) E UNIFORME",
    ]
    for i, hdr in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(hdr)
        run.bold = True
        run.font.size = Pt(9)

    placeholders = ["{{ATIVIDADES}}", "{{RISCOS}}", "{{EPIS}}"]
    for i, ph in enumerate(placeholders):
        cell = table.rows[1].cells[i]
        p = cell.paragraphs[0]
        p.add_run(ph).font.size = Pt(9)

    doc.add_paragraph()

    # Assinaturas
    p_ass = doc.add_paragraph()
    add_bold(p_ass, "Responsável SST: ")
    add_normal(p_ass, "{{RESP_SST}}")

    p_func = doc.add_paragraph()
    add_bold(p_func, "Assinatura do Funcionário: ")
    add_normal(p_func, "_" * 40)

    p_data = doc.add_paragraph()
    add_bold(p_data, "Data: ")
    add_normal(p_data, "{{DATA_HOJE}}")

    buf = _io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def extrair_texto_docx(conteudo_bytes: bytes) -> str:
    """Extrai todo o texto de um .docx em formato editável."""
    import io
    doc = Document(io.BytesIO(conteudo_bytes))
    linhas = []
    # Parágrafos do corpo
    for para in doc.paragraphs:
        linhas.append(para.text)
    # Tabelas
    for tabela in doc.tables:
        linhas.append("")
        for linha in tabela.rows:
            celulas = "\t".join(cel.text.strip() for cel in linha.cells)
            linhas.append(celulas)
    return "\n".join(linhas)


def texto_para_docx(texto: str) -> bytes:
    """Cria um .docx simples a partir de texto (para salvar edições)."""
    import io
    doc = Document()
    for linha in texto.split("\n"):
        # Linha com tabs vira parágrafos separados por espaço (simplificado)
        doc.add_paragraph(linha.replace("\t", "   "))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def pasta_lote() -> str:
    """Cria e retorna pasta para o lote do dia."""
    from datetime import datetime
    nome = datetime.now().strftime("lote_%Y%m%d_%H%M%S")
    pasta = os.path.join(OUTPUT_DIR, nome)
    os.makedirs(pasta, exist_ok=True)
    return pasta


def juntar_pdfs(caminhos_pdf: list[str], pasta_saida: str, nome_funcionario: str) -> str | None:
    """
    Junta múltiplos PDFs em um único arquivo.
    Retorna o caminho do PDF final ou None em caso de erro.
    """
    try:
        import subprocess, re

        nome_seguro = re.sub(r"[^\w\s-]", "", nome_funcionario)
        nome_seguro = re.sub(r"\s+", "_", nome_seguro.strip())
        pdf_final   = os.path.join(pasta_saida, f"Kit_SST__{nome_seguro}.pdf")

        # Tenta usar pdfunite (ghostscript/poppler) — disponível no Docker
        pdfunite = shutil.which("pdfunite")
        if pdfunite:
            cmd = [pdfunite] + caminhos_pdf + [pdf_final]
            resultado = subprocess.run(cmd, capture_output=True, timeout=60)
            if resultado.returncode == 0 and os.path.exists(pdf_final):
                return pdf_final

        # Tenta ghostscript
        gs = shutil.which("gs")
        if gs:
            cmd = [gs, "-dBATCH", "-dNOPAUSE", "-q", "-sDEVICE=pdfwrite",
                   f"-sOutputFile={pdf_final}"] + caminhos_pdf
            resultado = subprocess.run(cmd, capture_output=True, timeout=60)
            if resultado.returncode == 0 and os.path.exists(pdf_final):
                return pdf_final

        # Fallback: usa pypdf
        try:
            from pypdf import PdfWriter
            writer = PdfWriter()
            for caminho in caminhos_pdf:
                writer.append(caminho)
            with open(pdf_final, "wb") as f:
                writer.write(f)
            if os.path.exists(pdf_final):
                return pdf_final
        except ImportError:
            pass

        # Último fallback: retorna o primeiro PDF se não conseguir juntar
        if caminhos_pdf:
            shutil.copy(caminhos_pdf[0], pdf_final)
            return pdf_final

        return None

    except Exception as e:
        print(f"❌ Erro ao juntar PDFs: {e}")
        if caminhos_pdf and os.path.exists(caminhos_pdf[0]):
            try:
                shutil.copy(caminhos_pdf[0], pdf_final)
                return pdf_final
            except Exception:
                return caminhos_pdf[0]
        return None
