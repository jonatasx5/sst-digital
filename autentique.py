"""
autentique.py
Integração com a API GraphQL do Autentique v2.
"""

import json
import requests
from config import AUTENTIQUE_TOKEN, AUTENTIQUE_URL


MUTATION_CRIAR_DOCUMENTO = """
mutation CreateDocumentMutation(
    $document: DocumentInput!,
    $signers: [SignerInput!]!,
    $file: Upload!
) {
    createDocument(document: $document, signers: $signers, file: $file) {
        id
        name
        created_at
        signatures {
            public_id
            name
            email
            action { name }
            link { short_link }
        }
    }
}
"""


def _headers():
    return {"Authorization": f"Bearer {AUTENTIQUE_TOKEN}"}


def enviar_documento(
    nome_documento: str,
    caminho_pdf: str,
    funcionario: dict,
    sandbox: bool = False
) -> dict:
    """
    Envia um PDF para o Autentique e cria o documento para assinatura.

    Parâmetros:
        nome_documento : Nome do documento no Autentique
        caminho_pdf    : Caminho local do arquivo PDF
        funcionario    : Dict com dados do funcionário (nome, cpf, celular, email)
        sandbox        : True = documento de teste (não consome cota)

    Retorna dict com:
        sucesso        : bool
        autentique_id  : str  (ID do documento no Autentique)
        link           : str  (link curto de assinatura)
        erro           : str | None
    """

    # ── Monta o signatário ─────────────────────────────────
    # Usa celular via WhatsApp se disponível, senão e-mail
    celular = funcionario.get("celular", "").strip()
    email   = funcionario.get("email", "").strip()
    nome    = funcionario.get("nome", "Funcionário")
    cpf     = funcionario.get("cpf", "")

    if celular:
        # Garante formato internacional +55
        cel_num = "".join(filter(str.isdigit, celular))
        if not cel_num.startswith("55"):
            cel_num = "55" + cel_num
        signer = {
            "name":            nome,
            "phone":           f"+{cel_num}",
            "delivery_method": "DELIVERY_METHOD_LINK",  # Gera link — você envia manualmente
            "action":          "SIGN",
            "configs":         {"cpf": cpf.replace(".", "").replace("-", "")} if cpf else {}
        }
    elif email:
        signer = {
            "email":  email,
            "name":   nome,
            "action": "SIGN",
            "configs": {"cpf": cpf.replace(".", "").replace("-", "")} if cpf else {}
        }
    else:
        return {
            "sucesso": False,
            "autentique_id": None,
            "link": None,
            "erro": f"Funcionário '{nome}' não possui celular nem e-mail cadastrado — não é possível criar signatário."
        }

    # ── Monta o payload GraphQL multipart ─────────────────
    document_input = {
        "name":              nome_documento,
        "message":           f"Olá {nome.split()[0]}, seu kit SST está disponível para assinatura.",
        "reminder":          "WEEKLY",
        "new_signature_style": True,
    }

    operations = json.dumps({
        "query":     MUTATION_CRIAR_DOCUMENTO,
        "variables": {
            "document": document_input,
            "signers":  [signer],
            "file":     None
        }
    })

    map_field = json.dumps({"file": ["variables.file"]})

    # ── Envia requisição ───────────────────────────────────
    try:
        with open(caminho_pdf, "rb") as f:
            response = requests.post(
                AUTENTIQUE_URL,
                headers=_headers(),
                data={
                    "operations": operations,
                    "map":        map_field
                },
                files={"file": (nome_documento + ".pdf", f, "application/pdf")},
                timeout=60
            )

        if response.status_code != 200:
            return {
                "sucesso": False,
                "autentique_id": None,
                "link": None,
                "erro": f"HTTP {response.status_code}: {response.text[:300]}"
            }

        data = response.json()

        # Verifica erros GraphQL
        if "errors" in data:
            erros = "; ".join(e.get("message", "") for e in data["errors"])
            return {"sucesso": False, "autentique_id": None, "link": None, "erro": erros}

        doc = data.get("data", {}).get("createDocument", {})
        if not doc:
            return {"sucesso": False, "autentique_id": None, "link": None,
                    "erro": "Resposta inesperada da API"}

        # Log para debug
        import json as _json
        print("AUTENTIQUE RESPONSE:", _json.dumps(doc, indent=2, ensure_ascii=False))

        # Extrai link de assinatura (pega o primeiro sig com link não-nulo)
        link = None
        sigs = doc.get("signatures", [])
        for sig in sigs:
            link_obj = sig.get("link")
            if link_obj and link_obj.get("short_link"):
                link = link_obj["short_link"]
                break

        return {
            "sucesso":        True,
            "autentique_id":  doc.get("id"),
            "link":           link,
            "erro":           None
        }

    except requests.exceptions.Timeout:
        return {"sucesso": False, "autentique_id": None, "link": None,
                "erro": "Timeout — verifique a conexão."}
    except Exception as e:
        return {"sucesso": False, "autentique_id": None, "link": None, "erro": str(e)}


QUERY_STATUS_DOCUMENTO = """
query GetDocument($id: UUID!) {
    document(id: $id) {
        id
        name
        signatures {
            public_id
            name
            email
            signed { name }
            rejected { name }
        }
    }
}
"""


def consultar_status(doc_id: str) -> dict:
    """
    Consulta o status de um documento no Autentique pelo ID.
    Retorna dict com:
        sucesso       : bool
        status        : str  ('signed', 'pending', 'rejected')
        status_pt     : str
        assinado_em   : str | None
        signatarios   : list
        erro          : str | None
    """
    try:
        response = requests.post(
            AUTENTIQUE_URL,
            headers={**_headers(), "Content-Type": "application/json"},
            json={"query": QUERY_STATUS_DOCUMENTO, "variables": {"id": doc_id}},
            timeout=20
        )
        if response.status_code != 200:
            return {"sucesso": False, "status": "erro", "status_pt": "Erro",
                    "assinado_em": None, "signatarios": [],
                    "erro": f"HTTP {response.status_code}"}

        data = response.json()
        if "errors" in data:
            erros = "; ".join(e.get("message", "") for e in data["errors"])
            return {"sucesso": False, "status": "erro", "status_pt": "Erro",
                    "assinado_em": None, "signatarios": [], "erro": erros}

        doc = data.get("data", {}).get("document", {})
        if not doc:
            return {"sucesso": False, "status": "erro", "status_pt": "Não encontrado",
                    "assinado_em": None, "signatarios": [], "erro": "Documento não encontrado"}

        sigs = doc.get("signatures", [])
        signatarios = []
        todos_assinados = bool(sigs)
        algum_rejeitado = False
        assinado_em = None

        for sig in sigs:
            signed = sig.get("signed")
            rejected = sig.get("rejected")
            if rejected and rejected.get("name"):
                algum_rejeitado = True
            if not signed or not signed.get("name"):
                todos_assinados = False
            else:
                assinado_em = signed.get("name")  # campo "name" guarda o timestamp no Autentique
            signatarios.append({
                "nome":   sig.get("name", ""),
                "email":  sig.get("email", ""),
                "status": "signed" if (signed and signed.get("name")) else
                          ("rejected" if (rejected and rejected.get("name")) else "pending"),
            })

        if algum_rejeitado:
            status = "rejected"
            status_pt = "Rejeitado"
        elif todos_assinados:
            status = "signed"
            status_pt = "Assinado"
        else:
            status = "pending"
            status_pt = "Aguardando assinatura"

        return {
            "sucesso":    True,
            "status":     status,
            "status_pt":  status_pt,
            "assinado_em": assinado_em,
            "signatarios": signatarios,
            "erro":       None,
        }

    except requests.exceptions.Timeout:
        return {"sucesso": False, "status": "erro", "status_pt": "Timeout",
                "assinado_em": None, "signatarios": [], "erro": "Timeout"}
    except Exception as e:
        return {"sucesso": False, "status": "erro", "status_pt": "Erro",
                "assinado_em": None, "signatarios": [], "erro": str(e)}


def verificar_token() -> tuple[bool, str]:
    """
    Verifica se o token da API está válido.
    Retorna (ok, mensagem).
    """
    query = '{ me { id name email } }'
    try:
        r = requests.post(
            AUTENTIQUE_URL,
            headers={**_headers(), "Content-Type": "application/json"},
            json={"query": query},
            timeout=15
        )
        data = r.json()
        if "errors" in data:
            return False, data["errors"][0].get("message", "Token inválido")
        me = data.get("data", {}).get("me", {})
        return True, f"Conectado como: {me.get('name')} ({me.get('email')})"
    except Exception as e:
        return False, str(e)
