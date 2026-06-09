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
