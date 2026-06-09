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


def enviar_documento(
    nome_documento: str,
    caminho_pdf: str,
    funcionario: dict,
    sandbox: bool = False
) -> dict:
    """
    Envia um PDF para o ZapSign e cria o documento para assinatura.

    Retorna dict com:
        sucesso       : bool
        autentique_id : str  (token do documento no ZapSign)
        link          : str  (link de assinatura do funcionário)
        erro          : str | None
    """

    nome   = funcionario.get("nome", "Funcionário")
    email  = funcionario.get("email", "").strip()
    celular = funcionario.get("celular", "").strip()

    if not email and not celular:
        return {
            "sucesso": False,
            "autentique_id": None,
            "link": None,
            "erro": f"Funcionário '{nome}' não possui e-mail nem celular cadastrado."
        }

    # Lê o PDF e converte para base64
    try:
        with open(caminho_pdf, "rb") as f:
            pdf_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return {"sucesso": False, "autentique_id": None, "link": None,
                "erro": f"Erro ao ler PDF: {e}"}

    # Monta signatário
    signer = {"name": nome}
    if email:
        signer["email"] = email
        signer["send_automatic_email"] = False   # não envia e-mail automático — usamos link manual
    if celular:
        cel_num = "".join(filter(str.isdigit, celular))
        if not cel_num.startswith("55"):
            cel_num = "55" + cel_num
        signer["phone_country"] = "55"
        signer["phone_number"]  = cel_num[2:]    # sem o 55

    # Payload principal
    payload = {
        "name":         nome_documento,
        "base64_pdf":   pdf_b64,
        "lang":         "pt-br",
        "signers":      [signer],
        "sandbox":      sandbox,
    }

    try:
        url_base = "https://sandbox.api.zapsign.com.br/api/v1" if sandbox else ZAPSIGN_URL
        r = requests.post(
            f"{url_base}/docs/",
            headers=_headers(),
            json=payload,
            timeout=60
        )

        if r.status_code not in (200, 201):
            return {
                "sucesso": False,
                "autentique_id": None,
                "link": None,
                "erro": f"HTTP {r.status_code}: {r.text[:300]}"
            }

        data = r.json()
        print("ZAPSIGN RESPONSE:", data)

        doc_token = data.get("token")
        signers   = data.get("signers", [])

        # Pega o link do primeiro signatário
        link = None
        for s in signers:
            token_signer = s.get("token")
            if token_signer:
                link = f"https://app.zapsign.co/verificar/{token_signer}"
                break

        return {
            "sucesso":        True,
            "autentique_id":  doc_token,   # mantém chave para compatibilidade com banco
            "link":           link,
            "erro":           None
        }

    except requests.exceptions.Timeout:
        return {"sucesso": False, "autentique_id": None, "link": None,
                "erro": "Timeout — verifique a conexão."}
    except Exception as e:
        return {"sucesso": False, "autentique_id": None, "link": None, "erro": str(e)}


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

        return {
            "status":      doc_status,
            "status_pt":   status_pt,
            "assinado_em": assinado_em,
            "signatarios": signatarios,
            "erro":        None,
        }

    except requests.exceptions.Timeout:
        return {"status": "error", "status_pt": "Timeout", "assinado_em": None,
                "signatarios": [], "erro": "Timeout ao consultar ZapSign"}
    except Exception as e:
        return {"status": "error", "status_pt": "Erro", "assinado_em": None,
                "signatarios": [], "erro": str(e)}


def verificar_token() -> tuple[bool, str]:
    """Verifica se o token do ZapSign está válido."""
    try:
        r = requests.get(
            f"{ZAPSIGN_URL}/docs/",
            headers=_headers(),
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
