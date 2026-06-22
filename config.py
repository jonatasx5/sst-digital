"""
Configurações do sistema SST Digital
Edite este arquivo com os dados da sua empresa.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── ZAPSIGN ──────────────────────────────────────────────
# Configure a variável de ambiente ZAPSIGN_TOKEN no Railway (ou .env local)
ZAPSIGN_TOKEN = os.environ.get("ZAPSIGN_TOKEN", "")
ZAPSIGN_URL   = "https://api.zapsign.com.br/api/v1"

# ─── AUTENTIQUE (legado — não utilizado) ──────────────────
AUTENTIQUE_TOKEN = os.environ.get("AUTENTIQUE_TOKEN", "")
AUTENTIQUE_URL   = "https://api.autentique.com.br/v2/graphql"

# ─── AUTENTICAÇÃO DO SISTEMA ──────────────────────────────
# Defina APP_PASSWORD no Railway para proteger o sistema
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# ─── EMPRESA ──────────────────────────────────────────────
EMPRESA   = os.environ.get("EMPRESA", "JS Construtora")  # Nome da empresa nos documentos
RESP_SST  = os.environ.get("RESP_SST", "")               # Nome do responsável SST
CNPJ      = os.environ.get("CNPJ", "")                   # CNPJ da empresa (opcional)

# ─── DOCUMENTOS ───────────────────────────────────────────
# Lista com os 14 documentos disponíveis
# "id"     → nome do arquivo .docx na pasta modelos/ (sem extensão)
# "nome"   → nome exibido na interface
# "obrig"  → se True, vai para todas as funções por padrão

KIT_PADRAO = [
    "01_treinamento_admissional",
    "02_politica_de_seguranca",
    "04_treinamento_nr06",
    "05_treinamento_nr18",
    "09_treinamento_produtos_quimicos",
    "11_pop",
    "12_treinamento_direcao_defensiva",
    "03_os",
    "10_ficha_controle_epi",
]

DOCUMENTOS = [
    {"id": "01_treinamento_admissional",          "nome": "01 - Treinamento Admissional",                                    "obrig": True,  "kit_padrao": True},
    {"id": "02_politica_de_seguranca",            "nome": "02 - Política de Segurança",                                      "obrig": True,  "kit_padrao": True},
    {"id": "03_os",                               "nome": "03 - Ordem de Serviço",                                            "obrig": False, "kit_padrao": False},
    {"id": "04_treinamento_nr06",                 "nome": "04 - Treinamento NR 06",                                          "obrig": True,  "kit_padrao": True},
    {"id": "05_treinamento_nr18",                 "nome": "05 - Treinamento NR 18",                                          "obrig": False, "kit_padrao": True},
    {"id": "06_treinamento_nr21",                 "nome": "06 - Treinamento NR 21",                                          "obrig": False, "kit_padrao": False},
    {"id": "07_treinamento_nr26",                 "nome": "07 - Treinamento NR 26",                                          "obrig": False, "kit_padrao": False},
    {"id": "08_treinamento_nr12",                 "nome": "08 - Treinamento NR 12",                                          "obrig": False, "kit_padrao": False},
    {"id": "09_treinamento_produtos_quimicos",    "nome": "09 - Treinamento Produtos Químicos",                              "obrig": False, "kit_padrao": True},
    {"id": "10_ficha_controle_epi",               "nome": "10 - Ficha de Controle de EPI",                                  "obrig": True,  "kit_padrao": False},
    {"id": "11_pop",                              "nome": "11 - POP",                                                        "obrig": False, "kit_padrao": True},
    {"id": "12_treinamento_direcao_defensiva",    "nome": "12 - Treinamento Direção Defensiva",                             "obrig": False, "kit_padrao": True},
    {"id": "13_treinamento_vias_urbanas",         "nome": "13 - Treinamento Trabalho em Vias Urbanas, Estaduais e Federais","obrig": False, "kit_padrao": False},
    {"id": "14_primeiros_socorros",               "nome": "14 - Primeiros Socorros",                                        "obrig": False, "kit_padrao": False},
]

# ─── PASTAS ───────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
MODELOS_DIR = os.path.join(BASE_DIR, "modelos")
OUTPUT_DIR  = os.path.join(BASE_DIR, "output")
DB_PATH     = os.path.join(BASE_DIR, "sst.db")
