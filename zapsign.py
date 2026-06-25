"""
zapsign.py
Integração com a API REST do ZapSign v1.
Substitui o autentique.py — mesma interface de retorno.
"""

import os
import requests
import base64

ZAPSIGN_TOKEN = os.environ.get("ZAPSIGN_TOKEN", "")
ZAPSIGN_URL   = "https://api.zapsign.com.br/api/v1"


def _headers():
    return {
        "Authorization": f"Bearer {ZAPSIGN_TOKEN}",
        "Content-Type":  "application/json",
    }


def _montar_signer(signatario: dict) -> dict:
    """Converte dict interno {nome, celular, email} para formato ZapSign."""
    nome    = signatario.get("nome", "Signatário")
    email   = (signatario.get("email") or "").strip()
    celular = (signatario.get("celular") or "").strip()
    signer  = {"name": nome}
    if email:
        signer["email"] = email
        signer["send_automatic_email"] = False
    if celular:
        cel_num = "".join(filter(str.isdigit, celular))
        if not cel_num.startswith("55"):
            cel_num = "55" + cel_num
        signer["phone_country"] = "55"
        signer["phone_number"]  = cel_num[2:]
    return signer


def enviar_documento(
    nome_documento: str,
    caminho_pdf: str,
    funcionario: dict,
    sandbox: bool = False
) -> dict:
    """
    Envia um PDF para o ZapSign com um único signatário.
    Mantido para compatibilidade com outros módulos.
    """
    resultado = enviar_documento_multi(nome_documento, caminho_pdf, [funcionario], sandbox)
    if not resultado["sucesso"]:
        return {"sucesso": False, "autentique_id": None, "link": None,
                "erro": resultado.get("erro")}
    links = resultado.get("signatarios", [])
    link = links[0]["link"] if links else None
    return {"sucesso": True, "autentique_id": resultado["autentique_id"],
            "link": link, "erro": None}


def enviar_documento_multi(
    nome_documento: str,
    caminho_pdf: str,
    signatarios: list,   # lista de {tipo, nome, celular, email}
    sandbox: bool = False
) -> dict:
    """
    Envia um PDF para o ZapSign com múltiplos signatários.

    Retorna dict com:
        sucesso       : bool
        autentique_id : str
        signatarios   : list de {tipo, nome, link}
        erro          : str | None
    """
    if not signatarios:
        return {"sucesso": False, "autentique_id": None, "signatarios": [],
                "erro": "Nenhum signatário informado."}

    try:
        with open(caminho_pdf, "rb") as f:
            pdf_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return {"sucesso": False, "autentique_id": None, "signatarios": [],
                "erro": f"Erro ao ler PDF: {e}"}

    signers_payload = []
    for s in signatarios:
        signer = _montar_signer(s)
        if not signer.get("email") and not signer.get("phone_number"):
            continue  # pula signatários sem contato
        signers_payload.append(signer)

    if not signers_payload:
        return {"sucesso": False, "autentique_id": None, "signatarios": [],
                "erro": "Nenhum signatário com celular ou e-mail informado."}

    payload = {
        "name":       nome_documento,
        "base64_pdf": pdf_b64,
        "lang":       "pt-br",
        "signers":    signers_payload,
        "sandbox":    sandbox,
    }

    try:
        url_base = "https://sandbox.api.zapsign.com.br/api/v1" if sandbox else ZAPSIGN_URL
        r = requests.post(f"{url_base}/docs/", headers=_headers(), json=payload, timeout=60)

        if r.status_code not in (200, 201):
            return {"sucesso": False, "autentique_id": None, "signatarios": [],
                    "erro": f"HTTP {r.status_code}: {r.text[:300]}"}

        data = r.json()
        print("ZAPSIGN RESPONSE:", data)

        doc_token   = data.get("token")
        resp_signers = data.get("signers", [])

        result_signatarios = []
        for i, s in enumerate(resp_signers):
            token_signer = s.get("token")
            link = f"https://app.zapsign.co/verificar/{token_signer}" if token_signer else None
            tipo = signatarios[i].get("tipo", f"sign{i}") if i < len(signatarios) else f"sign{i}"
            nome = signatarios[i].get("nome", s.get("name", "")) if i < len(signatarios) else s.get("name", "")
            result_signatarios.append({"tipo": tipo, "nome": nome, "link": link})

        return {
            "sucesso":      True,
            "autentique_id": doc_token,
            "signatarios":  result_signatarios,
            "link":         result_signatarios[0]["link"] if result_signatarios else None,
            "erro":         None,
        }

    except requests.exceptions.Timeout:
        return {"sucesso": False, "autentique_id": None, "signatarios": [],
                "erro": "Timeout — verifique a conexão."}
    except Exception as e:
        return {"sucesso": False, "autentique_id": None, "signatarios": [], "erro": str(e)}


def consultar_status(doc_token: str) -> dict:
    """
    Consulta o status atual de um documento no ZapSign.

    Retorna dict com:
        status        : str  ('pending' | 'signed' | 'refused' | 'error')
        status_pt     : str  ('Aguardando' | 'Assinado' | 'Recusado' | 'Erro')
        assinado_em   : str | None  (ISO datetime)
        signatarios   : list de {nome, status, assinado_em}
        erro          : str | None
    """
    try:
        r = requests.get(
            f"{ZAPSIGN_URL}/docs/{doc_token}/",
            headers=_headers(),
            timeout=20
        )
        if r.status_code == 404:
            return {"status": "error", "status_pt": "Não encontrado",
                    "assinado_em": None, "signatarios": [], "erro": "Documento não encontrado no ZapSign"}
        if r.status_code != 200:
            return {"status": "error", "status_pt": "Erro",
                    "assinado_em": None, "signatarios": [],
                    "erro": f"HTTP {r.status_code}: {r.text[:200]}"}

        data = r.json()

        # Status do documento
        doc_status = data.get("status", "pending")  # 'pending' | 'signed' | 'refused'
        status_map = {
            "pending":  "Aguardando",
            "signed":   "Assinado",
            "refused":  "Recusado",
            "canceled": "Cancelado",
        }
        status_pt = status_map.get(doc_status, doc_status.capitalize())

        # Signatários
        signers_raw = data.get("signers", [])
        signatarios = []
        assinado_em = None
        for s in signers_raw:
            s_status = s.get("status", "pending")
            s_assinado = s.get("signed_at") or s.get("last_remind_date")
            if s_status == "signed" and s_assinado:
                assinado_em = s_assinado
            signatarios.append({
                "nome":       s.get("name", ""),
                "status":     s_status,
                "status_pt":  status_map.get(s_status, s_status.capitalize()),
                "assinado_em": s_assinado,
            })

        # URL do PDF assinado (disponível após assinatura completa)
        signed_file = data.get("signed_file") or None

        return {
            "status":      doc_status,
            "status_pt":   status_pt,
            "assinado_em": assinado_em,
            "signed_file": signed_file,
            "signatarios": signatarios,
            "erro":        None,
        }

    except requests.exceptions.Timeout:
        return {"status": "error", "status_pt": "Timeout", "assinado_em": None,
                "signatarios": [], "erro": "Timeout ao consultar ZapSign"}
    except Exception as e:
        return {"status": "error", "status_pt": "Erro", "assinado_em": None,
                "signatarios": [], "erro": str(e)}


def baixar_pdf_assinado(doc_token: str) -> tuple[bytes | None, str | None]:
    """
    Baixa o PDF assinado de um documento ZapSign.
    Retorna (bytes_do_pdf, None) em caso de sucesso ou (None, mensagem_de_erro).
    """
    try:
        # Primeiro busca os detalhes para pegar a URL do arquivo assinado
        r = requests.get(
            f"{ZAPSIGN_URL}/docs/{doc_token}/",
            headers=_headers(),
            timeout=20
        )
        if r.status_code != 200:
            return None, f"Documento não encontrado (HTTP {r.status_code})"

        data = r.json()
        signed_file = data.get("signed_file")

        if not signed_file:
            doc_status = data.get("status", "pending")
            if doc_status != "signed":
                return None, "Documento ainda não foi assinado — aguardando assinatura"
            return None, "URL do arquivo assinado não disponível ainda"

        # Baixa o arquivo da URL retornada pelo ZapSign (sem proxy — URL pública S3)
        r2 = requests.get(signed_file, timeout=30, proxies={"http": None, "https": None})
        if r2.status_code != 200:
            # Tenta sem proxies=None caso o ambiente não suporte
            r2 = requests.get(signed_file, timeout=30)
        if r2.status_code != 200:
            return None, f"Erro ao baixar o arquivo (HTTP {r2.status_code})"

        return r2.content, None

    except requests.exceptions.Timeout:
        return None, "Timeout ao baixar o arquivo"
    except Exception as e:
        return None, str(e)


def verificar_token() -> tuple[bool, str]:
    """Verifica se o token do ZapSign está válido."""
    token = os.environ.get("ZAPSIGN_TOKEN", ZAPSIGN_TOKEN)
    if not token:
        return False, "ZAPSIGN_TOKEN não configurado"
    try:
        # Usa o endpoint de listagem com page_size=1 só para testar autenticação
        r = requests.get(
            f"{ZAPSIGN_URL}/docs/?page_size=1",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=15
        )
        if r.status_code == 200:
            return True, "ZapSign conectado com sucesso"
        elif r.status_code == 401:
            return False, "Token inválido ou expirado"
        else:
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)
